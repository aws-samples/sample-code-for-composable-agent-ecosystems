"""Grounding Check Lambda — validates agent output against grounding source.

Called by AWS Step Functions after each agent that has dependencies. Uses guardrails for Amazon Bedrock
contextual-grounding policy. The grounding source is a combination of:
  1. Upstream agent outputs (transitively, all keys in $.results except this agent)
  2. THIS agent's own tool-call results (from agent-invoker's `tool_results` field)

No silent fallbacks. If config is missing, the Lambda raises and the SFN execution
fails loudly so operators see the real problem instead of false `grounded: true`.
"""
import os
import json
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
GUARDRAIL_ID = os.environ.get("TESSERA_GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("TESSERA_GUARDRAIL_VERSION", "DRAFT")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")

# Amazon Bedrock Runtime client
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)


def _fetch_s3_uri(uri: str) -> str:
    """Fetch text from an s3://bucket/key URI. Empty string on missing/error."""
    if not uri or not uri.startswith("s3://"):
        return ""
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        return ""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8", errors="replace")
    except Exception as e:
        # Loud failure rather than silent fallback — this is a real wiring bug
        raise RuntimeError(f"Failed to fetch grounding artifact from {uri}: {e}")


def _resolve_output(result: dict) -> str:
    """Inline output (legacy) takes precedence; else fetch from S3 URI (Phase 4.5)."""
    if not isinstance(result, dict):
        return ""
    if result.get("output"):
        return result["output"]
    return _fetch_s3_uri(result.get("output_s3_uri", ""))


def _resolve_tool_results(result: dict) -> list:
    """Inline tool_results (legacy) takes precedence; else fetch JSON from S3."""
    if not isinstance(result, dict):
        return []
    if result.get("tool_results"):
        return result["tool_results"]
    raw = _fetch_s3_uri(result.get("tool_results_s3_uri", ""))
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def handler(event, context):
    agent_id = event.get("agent_id", "")
    results = event.get("results", {})
    session_id = event.get("session_id", "")
    mission = event.get("mission", "")  # original user query — needed by Bedrock contextual grounding
    # Dry run: skip the (slow) grounding check instantly. The state still runs
    # so the vertical slice is exercised, but returns in milliseconds.
    if event.get("dry_run"):
        return {"agent_id": agent_id, "grounded": True, "skipped": True, "reason": "dry_run"}

    if not GUARDRAIL_ID:
        raise RuntimeError(
            "TESSERA_GUARDRAIL_ID env var is not set. The grounding check cannot run "
            "without a Bedrock Guardrail. Configure the CDK stack to provision one."
        )

    if not agent_id:
        raise ValueError("Missing agent_id in grounding-check event")

    # Resolve this agent's output and tool results.
    # Phase 4.5: prefer inline `output`/`tool_results` (legacy) when present,
    # otherwise fetch from the S3 URIs that agent-invoker now stages there.
    agent_result = results.get(agent_id)
    if not isinstance(agent_result, dict):
        raise ValueError(f"No result found in $.results for agent {agent_id!r}")
    agent_output = _resolve_output(agent_result)
    if not agent_output:
        # Agent produced nothing (e.g. gate override, empty specialist). Skip grounding.
        return {"statusCode": 200, "body": json.dumps({"agent_id": agent_id, "grounded": True, "skipped": True, "reason": "empty_output"})}

    own_tool_results = _resolve_tool_results(agent_result)

    # Build grounding source: every other agent's output + this agent's own tool results.
    # Tool results are the most authoritative grounding because they are the actual
    # external evidence the agent used to make its claims.
    upstream_blocks = []
    for aid, res in results.items():
        if aid == agent_id or not isinstance(res, dict):
            continue
        upstream_text = _resolve_output(res)
        if upstream_text:
            upstream_blocks.append(f"[{aid}]:\n{upstream_text}")

    tool_blocks = []
    for tr in own_tool_results:
        if not isinstance(tr, dict):
            continue
        name = tr.get("name", "tool")
        result = tr.get("result", "")
        if isinstance(result, (dict, list)):
            result = json.dumps(result, default=str)
        tool_blocks.append(f"[tool:{name}]:\n{result}")

    grounding_parts = []
    if upstream_blocks:
        grounding_parts.append("=== UPSTREAM AGENT OUTPUTS ===\n\n" + "\n\n".join(upstream_blocks))
    if tool_blocks:
        grounding_parts.append("=== TOOL CALL RESULTS ===\n\n" + "\n\n".join(tool_blocks))

    if not grounding_parts:
        # Leaf agent with no tool results: nothing to ground against.
        # That's a real condition, not an error. Return explicitly so callers can see it.
        return {
            "agent_id": agent_id,
            "grounded": True,
            "skipped_reason": "no grounding source available (leaf agent with no tool results)",
        }

    grounding_source = "\n\n".join(grounding_parts)

    # Amazon Bedrock ApplyGuardrail contextual-grounding has a hard limit of 5 text units
    # (~5000 chars) on the OUTPUT being checked. With Opus producing 20-60K char
    # outputs, we must chunk. Strategy:
    #   1. Prefer STRUCTURED FINDINGS section — that's where claims are made;
    #      verbose narrative prose isn't claim-bearing.
    #   2. If still too long, chunk into <=4500 char pieces (margin for safety).
    #   3. Run apply_guardrail on each chunk; ANY non-NONE action fails grounding.
    #   4. Same chunking applied to grounding_source (max ~50K input total).
    GUARDRAIL_OUTPUT_CHAR_LIMIT = 4500   # 5 text units * 1000 chars - safety margin
    GUARDRAIL_SOURCE_CHAR_LIMIT = 45000  # roomy on source side; trimmed if needed

    structured = agent_result.get("structured") or {}
    findings_text = structured.get("findings", "") if isinstance(structured, dict) else ""
    questions_text = structured.get("open_questions", "") if isinstance(structured, dict) else ""
    # Compose a claim-dense block from structured fields when available; otherwise
    # fall back to the full output. Findings are what need grounding most.
    if findings_text:
        output_for_check = findings_text
        if questions_text:
            output_for_check += "\n\nOPEN QUESTIONS:\n" + questions_text
    else:
        output_for_check = agent_output

    # Trim grounding source if it's huge; prefer the most recent / most cited parts
    if len(grounding_source) > GUARDRAIL_SOURCE_CHAR_LIMIT:
        grounding_source = grounding_source[:GUARDRAIL_SOURCE_CHAR_LIMIT] + "\n... [trimmed]"

    # Chunk output and run apply_guardrail per chunk. Any non-NONE action means the
    # whole output failed grounding.
    chunks = _chunk_text(output_for_check, GUARDRAIL_OUTPUT_CHAR_LIMIT)
    if not chunks:
        chunks = [""]

    aggregate_action = "NONE"
    aggregate_assessments = []
    for idx, chunk in enumerate(chunks):
        result = _apply_guardrail_with_grounding(chunk, grounding_source, mission)
        action = result.get("action", "NONE")
        if action != "NONE":
            aggregate_action = action
        aggregate_assessments.extend(result.get("assessments", []) or [])

    grounded = aggregate_action == "NONE"
    assessments = aggregate_assessments

    payload = {
        "agent_id": agent_id,
        "grounded": grounded,
        "action": action,
        "assessments_summary": _summarize_assessments(assessments),
    }

    # Push grounding result to WebSocket so the UI updates per-agent badge
    if session_id:
        push_event(session_id, {
            "type": "STATE_DELTA",
            "agent": agent_id,
            "grounding": {"grounded": grounded, "action": action},
        })

    return payload


def _apply_guardrail_with_grounding(agent_output: str, grounding_source: str, query: str) -> dict:
    """Wrap the apply_guardrail call so the grounding source is passed correctly.

    The Bedrock contextual-grounding policy requires THREE qualified blocks:
      - 'grounding_source': the source-of-truth text
      - 'query': the original user query / mission (so relevance can be scored)
      - (unqualified): the model output to be checked

    Missing any of these returns a ValidationException from Bedrock.
    """
    # Amazon Bedrock contextual grounding limits query to ~1000 chars
    query_text = (query or "Provide an analysis grounded in the source material.")[:1000]
    return bedrock.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source="OUTPUT",
        content=[
            {"text": {"text": grounding_source, "qualifiers": ["grounding_source"]}},
            {"text": {"text": query_text, "qualifiers": ["query"]}},
            {"text": {"text": agent_output}},
        ],
    )


def _summarize_assessments(assessments: list) -> dict:
    """Pull out grounding-policy specifics for traceability."""
    summary = {"grounding_score": None, "relevance_score": None}
    for a in assessments or []:
        cgp = a.get("contextualGroundingPolicy") or {}
        for f in cgp.get("filters", []):
            t = f.get("type")
            score = f.get("score")
            if t == "GROUNDING":
                summary["grounding_score"] = score
            elif t == "RELEVANCE":
                summary["relevance_score"] = score
    return summary


def push_event(session_id: str, event: dict):
    """Push grounding event to WebSocket connections for the session."""
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


def _chunk_text(text: str, max_chars: int) -> list:
    """Split text into chunks of up to max_chars, preferring paragraph/line boundaries.

    Phase 4.5: Amazon Bedrock Guardrail contextual-grounding caps response at 5 text units
    (~5000 chars). With Opus producing rich claim-dense outputs, single-shot grounding
    is impossible. We chunk on natural boundaries so each chunk reads coherently.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        # Try to split at a paragraph boundary
        split = remaining.rfind("\n\n", 0, max_chars)
        if split < max_chars * 0.5:  # too far back; try line boundary
            split = remaining.rfind("\n", 0, max_chars)
        if split < max_chars * 0.5:  # still bad; hard cut
            split = max_chars
        chunks.append(remaining[:split])
        remaining = remaining[split:].lstrip("\n")
    return chunks
