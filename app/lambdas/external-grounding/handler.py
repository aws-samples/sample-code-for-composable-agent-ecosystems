"""External Grounding Lambda — verifies time-sensitive claims against fresh web sources.

Amazon Bedrock Guardrails contextual grounding only checks claims against artifacts the
system already produced (upstream agents + tool results). It cannot catch
hallucinations of dates, prices, current events, or any claim where neither
upstream agents nor tool calls contain the truth.

This Lambda fills that gap. For each STRUCTURED FINDING flagged as time-sensitive,
it issues a web search, then asks Sonnet 4 to evaluate corroboration.

Result per claim: verified | unverified | contradicted, with the supporting
source URL when verified or contradicted. Pushed to the WebSocket as STATE_DELTA
so the UI can render per-claim badges in the report.

No silent fallbacks: if WEBSEARCH_API_KEY is missing or Bedrock fails, the Lambda
raises and the SFN execution fails.
"""
import os
import re
import time
import json
import boto3
import httpx

REGION = os.environ.get("AWS_REGION", "us-east-1")
WEBSEARCH_CREDENTIAL_PROVIDER = os.environ["WEBSEARCH_CREDENTIAL_PROVIDER"]
WORKLOAD_IDENTITY_NAME = os.environ["WORKLOAD_IDENTITY_NAME"]
# Tavily endpoint (env-overridable; real default so a sanitized deploy can't break it).
WEBSEARCH_API_URL = os.environ.get("WEBSEARCH_API_URL", "https://api.tavily.com/search")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")
EVAL_MODEL_ID = os.environ.get(
    "EXTERNAL_EVAL_MODEL_ID",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
)

# Amazon Bedrock client
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
agentcore_dp = boto3.client("bedrock-agentcore", region_name=REGION)

# web search API key cache — fetched once per warm container from the credential provider.
# Two-step AgentCore Identity flow:
#   1. GetWorkloadAccessToken (IAM-authenticated by Lambda's role) → opaque token
#   2. GetResourceApiKey (uses the token) → the actual web search API key
_websearch_cache: dict = {"key": None, "fetched_at": 0}
_TAVILY_CACHE_TTL = 600  # 10 minutes


def get_websearch_api_key() -> str:
    """Read the web search API key from AgentCore Identity Token Vault.

    Uses boto3 directly (not the bedrock-agentcore Python SDK) because the SDK
    ships compiled deps that don't fit Lambda's packaging path. Same AgentCore
    service is called either way.

    Raises if the credential provider is missing/empty or the Lambda lacks the
    workload identity it needs — no silent fallback to env.
    """
    now = time.time()
    if _websearch_cache["key"] and (now - _websearch_cache["fetched_at"] < _TAVILY_CACHE_TTL):
        return _websearch_cache["key"]

    # Step 1: get a workload access token using the Lambda's IAM role
    try:
        wat_resp = agentcore_dp.get_workload_access_token(
            workloadName=WORKLOAD_IDENTITY_NAME,
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to get workload access token for {WORKLOAD_IDENTITY_NAME!r}: {e}. "
            f"Check that the IAM role for the Lambda function has bedrock-agentcore:GetWorkloadAccessToken "
            f"and the workload identity exists."
        )
    token = wat_resp.get("workloadAccessToken")
    if not token:
        raise RuntimeError(f"GetWorkloadAccessToken returned no token for {WORKLOAD_IDENTITY_NAME!r}")

    # Step 2: exchange token for the API key
    try:
        api_resp = agentcore_dp.get_resource_api_key(
            resourceCredentialProviderName=WEBSEARCH_CREDENTIAL_PROVIDER,
            workloadIdentityToken=token,
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to read web search provider credential from provider {WEBSEARCH_CREDENTIAL_PROVIDER!r}: {e}"
        )
    api_key = api_resp.get("apiKey")
    if not api_key:
        raise RuntimeError(
            f"Credential provider {WEBSEARCH_CREDENTIAL_PROVIDER!r} returned an empty key. "
            f"Update via: aws bedrock-agentcore-control update-api-key-credential-provider"
        )

    _websearch_cache["key"] = api_key
    _websearch_cache["fetched_at"] = now
    return api_key


# Patterns that mark a claim as time-sensitive. Conservative — false positives are OK
# (extra verification work) but false negatives are not (lets stale claims through).
_TIME_PATTERNS = [
    r"\b\d{4}\b",                           # year
    r"\b(?:Q[1-4]|FY)\s*\d{2,4}",           # quarter / fiscal-year
    r"\$\d[\d,]*(?:\.\d+)?\s*(?:[BMK]|billion|million|thousand|trillion)?\b",
    r"\b\d+(?:\.\d+)?\s*%",                 # percentages
    r"\b(?:today|yesterday|recent|recently|current|currently|latest|now|this\s+(?:week|month|quarter|year))\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b",
    r"\bversion\s+\d",                      # version numbers
    r"\b(?:CVE-\d{4}-\d{4,7})\b",           # CVE IDs (publication-dated)
]
_TIME_RE = re.compile("|".join(_TIME_PATTERNS), re.IGNORECASE)


def handler(event, context):
    """Run external grounding for one agent's findings.

    Expected event shape (passed by the ASL compiler — see save-ecosystem):
        {
            "agent_id": "fundamental",
            "results": { ...all-agent-results... },
            "session_id": "..."
        }
    """
    agent_id = event.get("agent_id", "")
    results = event.get("results", {})
    session_id = event.get("session_id", "")

    # Dry run: skip the (slowest) external web-search grounding instantly. The
    # state still runs so the slice is exercised; returns in milliseconds and
    # doesn't even need the web-search credential provider.
    if event.get("dry_run"):
        return {"agent_id": event.get("agent_id", ""), "verifications": [], "skipped_reason": "dry_run"}

    if not WEBSEARCH_CREDENTIAL_PROVIDER:
        raise RuntimeError(
            "WEBSEARCH_CREDENTIAL_PROVIDER env var is not set. External grounding cannot run "
            "without a web search credential provider. Configure it in CDK."
        )
    if not agent_id:
        raise ValueError("Missing agent_id in external-grounding event")

    agent_result = results.get(agent_id)
    if not isinstance(agent_result, dict):
        raise ValueError(f"No result found in $.results for agent {agent_id!r}")

    structured = agent_result.get("structured") or {}
    findings_text = structured.get("findings", "") if isinstance(structured, dict) else ""
    if not findings_text:
        # Agent produced no structured findings — earlier validation should have failed,
        # but defend here too.
        return {
            "agent_id": agent_id,
            "verifications": [],
            "skipped_reason": "no structured findings to verify",
        }

    claims = extract_time_sensitive_claims(findings_text)
    if not claims:
        return {
            "agent_id": agent_id,
            "verifications": [],
            "skipped_reason": "no time-sensitive claims detected",
        }

    verifications = []
    for claim in claims[:6]:  # cap per-agent verifications to bound latency/cost
        verdict = verify_claim(claim)
        verifications.append({"claim": claim, **verdict})
        if session_id:
            push_event(session_id, {
                "type": "STATE_DELTA",
                "agent": agent_id,
                "external_grounding": {
                    "claim": claim[:200],
                    "verdict": verdict["verdict"],
                    "source_url": verdict.get("source_url"),
                },
            })

    summary = _summarize(verifications)
    return {
        "agent_id": agent_id,
        "verifications": verifications,
        "summary": summary,
    }


def extract_time_sensitive_claims(findings_markdown: str) -> list[str]:
    """Pull `- claim: ...` bullets that match a time-sensitive pattern."""
    claims = []
    for line in findings_markdown.splitlines():
        m = re.match(r"\s*-\s*claim:\s*(.+)", line, re.IGNORECASE)
        if not m:
            continue
        claim = m.group(1).strip()
        if _TIME_RE.search(claim):
            claims.append(claim)
    return claims


def verify_claim(claim: str) -> dict:
    """Issue a web search and ask Sonnet 4 whether the result corroborates the claim."""
    try:
        search_results = web_search(claim)
    except Exception as e:
        # Failure to search is still informative — record it instead of silently passing.
        return {
            "verdict": "unverified",
            "reason": f"search failed: {str(e)[:600]}",
            "source_url": None,
        }

    if not search_results:
        return {
            "verdict": "unverified",
            "reason": "no search results",
            "source_url": None,
        }

    # Build a compact evidence block for the evaluator.
    evidence = "\n\n".join(
        f"[{i+1}] {r.get('title', '')}\n{r.get('content', '')[:600]}\n(url: {r.get('url', '')})"
        for i, r in enumerate(search_results[:3])
    )

    prompt = (
        "You are a fact-check evaluator. Read the CLAIM and the SEARCH RESULTS, "
        "then answer in this exact JSON form:\n"
        '{"verdict": "verified" | "unverified" | "contradicted", '
        '"reason": "<one sentence>", '
        '"source_index": <1-based index of the most relevant search result, or null>}\n\n'
        "verified = the search results clearly corroborate the claim.\n"
        "contradicted = the search results contradict the claim or show different numbers/dates.\n"
        "unverified = the search results don't address the claim either way.\n\n"
        f"CLAIM: {claim}\n\n"
        f"SEARCH RESULTS:\n{evidence}\n\n"
        "Respond with the JSON object only — no markdown fences, no commentary."
    )

    resp = bedrock.invoke_model(
        modelId=EVAL_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    body = json.loads(resp["body"].read())
    raw_text = (body.get("content") or [{}])[0].get("text", "").strip()
    if raw_text.startswith("```"):
        raw_text = "\n".join(raw_text.split("\n")[1:-1])

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "verdict": "unverified",
            "reason": "evaluator returned non-JSON",
            "source_url": None,
        }

    verdict = parsed.get("verdict") or "unverified"
    reason = parsed.get("reason") or ""
    src_idx = parsed.get("source_index")
    source_url = None
    if isinstance(src_idx, int) and 1 <= src_idx <= len(search_results):
        source_url = search_results[src_idx - 1].get("url")

    return {"verdict": verdict, "reason": reason, "source_url": source_url}


def web_search(query: str) -> list[dict]:
    """Issue a web search provider search. Returns the raw `results` list."""
    api_key = get_websearch_api_key()
    # web search provider deprecated body-based api_key (returns HTTP 432). Use Bearer auth.
    resp = httpx.post(
        WEBSEARCH_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "query": query[:400],
            "max_results": 5,
            "search_depth": "basic",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _summarize(verifications: list[dict]) -> dict:
    counts = {"verified": 0, "unverified": 0, "contradicted": 0}
    for v in verifications:
        verdict = v.get("verdict", "unverified")
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def push_event(session_id: str, event: dict):
    if not CONNECTIONS_TABLE or not WS_ENDPOINT:
        return
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(CONNECTIONS_TABLE)
    resp = table.scan(
        FilterExpression="sessionId = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    data = json.dumps(event).encode()
    for item in resp.get("Items", []):
        try:
            apigw.post_to_connection(ConnectionId=item["connectionId"], Data=data)
        except apigw.exceptions.GoneException:
            try:
                table.delete_item(Key={"connectionId": item["connectionId"]})
            except Exception:
                pass
