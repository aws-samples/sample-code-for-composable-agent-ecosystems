# Services: Amazon Bedrock, Amazon Bedrock AgentCore (Gateway, Identity, Memory), Amazon S3
"""Tessera Agent — Native AG-UI server on AgentCore Runtime.

Uses ag-ui-strands to wrap a Strands agent and emit native AG-UI events.
Events flow:
  RUN_STARTED → TEXT_MESSAGE_START → TEXT_MESSAGE_CONTENT (delta) → TEXT_MESSAGE_END
  → TOOL_CALL_START → TOOL_CALL_ARGS → TOOL_CALL_END → TOOL_CALL_RESULT
  → STATE_DELTA (for A2UI components) → RUN_FINISHED

Tools come from two sources:
  1. Platform tools (web_search, generateUI) — always available, defined inline.
  2. Discovered tools (MCP) — fetched from AgentCore Gateway at request time,
     filtered by ecosystem YAML's agent_config.tools list.

Secrets are read from AgentCore Identity credential providers (Token Vault),
NOT from environment variables. Honors P9 (zero static secrets).
"""

import os
import re
import json
import time
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

import boto3
import httpx

from ag_ui_strands import StrandsAgent
from ag_ui.core import RunAgentInput, StateDeltaEvent, EventType
from ag_ui.encoder import EventEncoder

from strands import Agent, tool
from strands.tools import PythonAgentTool
from strands.models.bedrock import BedrockModel

from strands_tools.calculator import calculator as _strands_calculator
from strands_tools.current_time import current_time as _strands_current_time
from strands_tools.http_request import http_request as _strands_http_request, TOOL_SPEC as _HTTP_REQUEST_TOOL_SPEC

REGION = os.environ.get("AWS_REGION", "us-east-1")

# === Per-run token usage capture (spec-run-tuning §5.1) ======================
# Strands exposes token usage via the model's `stream()` `metadata` event
# ({"metadata": {"usage": {"inputTokens", "outputTokens", "totalTokens"}, ...}}) —
# the canonical source the event loop itself accumulates from. We capture it at
# the MODEL layer because the ag-ui-strands wrapper runs a per-thread agent copy
# (so our agent instance's event_loop_metrics stays 0) and never surfaces usage
# in the AG-UI event stream. Capturing in the model's stream is independent of
# all of that — it's where the Bedrock response's usage actually arrives.
#
# Accumulator is keyed by a per-invocation token (uuid) stored on the model as a
# plain string attribute, so it survives even if the wrapper shallow/deep-copies
# the model object (the copy keeps the same key string and writes to the same
# module-level dict). Thread-locked for safety under concurrent invocations.
import threading as _threading
_USAGE_ACC: dict = {}
_USAGE_LOCK = _threading.Lock()


def _usage_record(key: str, usage: dict) -> None:
    if not key or not isinstance(usage, dict):
        return
    with _USAGE_LOCK:
        acc = _USAGE_ACC.setdefault(key, {"in": 0, "out": 0})
        acc["in"] += int(usage.get("inputTokens") or 0)
        acc["out"] += int(usage.get("outputTokens") or 0)


def _usage_pop(key: str) -> dict:
    with _USAGE_LOCK:
        return _USAGE_ACC.pop(key, {"in": 0, "out": 0})


class _UsageRecordingBedrockModel(BedrockModel):
    """BedrockModel that tees per-call token usage from its stream() metadata
    events into the module accumulator (summed across all event-loop cycles).
    Defensive: capture failures never affect streaming."""
    _usage_key: str = ""

    async def stream(self, *args, **kwargs):  # type: ignore[override]
        async for ev in super().stream(*args, **kwargs):
            try:
                if isinstance(ev, dict):
                    u = None
                    meta = ev.get("metadata")
                    if isinstance(meta, dict) and isinstance(meta.get("usage"), dict):
                        u = meta["usage"]
                    elif isinstance(ev.get("usage"), dict):
                        u = ev["usage"]
                    if u:
                        _usage_record(self._usage_key, u)
            except Exception:
                pass
            yield ev


# === Amazon Bedrock AgentCore Identity / Gateway / Memory configuration ===
# Gateway M2M client secret retrieved from AWS Secrets Manager (not env vars).
WEBSEARCH_CREDENTIAL_PROVIDER = os.environ.get("WEBSEARCH_CREDENTIAL_PROVIDER", "")
# Tavily web-search API endpoint. Env-overridable but defaults to the real host so
# a sanitized/misconfigured deploy can't silently break web search (it previously
# shipped as a non-resolving placeholder → DNS errors on every search).
WEBSEARCH_API_URL = os.environ.get("WEBSEARCH_API_URL", "https://api.tavily.com/search")
GATEWAY_URL = os.environ.get("TESSERA_GATEWAY_URL", "")
GATEWAY_CLIENT_ID = os.environ.get("TESSERA_GATEWAY_CLIENT_ID", "")
GATEWAY_TOKEN_URL = os.environ.get("TESSERA_GATEWAY_TOKEN_URL", "")
GATEWAY_SCOPE = os.environ.get("TESSERA_GATEWAY_SCOPE", "")
MEMORY_ID = os.environ.get("TESSERA_MEMORY_ID", "")
# Runs bucket — the runtime reads uploaded run attachments on demand (read_attachment
# tool) from runs/<session_id>/_attachments.json instead of the launch inlining every
# file's full text into the mission (which blew the 256KB Step Functions state cap).
TESSERA_RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")


_RUNS_S3_CLIENT = {"c": None}


def _runs_s3():
    """Lazy, cached S3 client for reading run attachments (read_attachment tool)."""
    if _RUNS_S3_CLIENT["c"] is None:
        _RUNS_S3_CLIENT["c"] = boto3.client("s3", region_name=REGION)
    return _RUNS_S3_CLIENT["c"]


# Deterministic authority citations (Option 1): a deliverable-authoring agent (composer/
# report) is several hops downstream of the lenses that actually cited the catalog, and the
# intermediate synthesizer/challenger/mapper agents compress the `[catalog: type/id — uri]`
# markers down to bare names. So at invoke time we harvest those markers from the run's OWN
# upstream outputs and hand the author the citation IDS — it cites by `tessera-cite://type/id`
# (never a URL), and agent-invoker resolves the id to the verified uri at save. General across
# ecosystems; no per-authority code.
_RUN_CIT_RE = re.compile(
    r"\[catalog:\s*([A-Za-z0-9][A-Za-z0-9._/\-]*?)\s*[\u2014\u2013-]+\s*(https?://[^\]\s]+?)\s*\]")


def _harvest_run_citations(session_id: str, limit: int = 200) -> dict:
    """Scan the run's agent outputs for `[catalog: type/id — uri]` → ordered unique {id: uri}."""
    out: dict = {}
    if not (TESSERA_RUNS_BUCKET and session_id):
        return out
    try:
        s3c = _runs_s3()
        tok = None
        while True:
            kw = {"Bucket": TESSERA_RUNS_BUCKET, "Prefix": f"runs/{session_id}/", "MaxKeys": 1000}
            if tok:
                kw["ContinuationToken"] = tok
            r = s3c.list_objects_v2(**kw)
            for o in r.get("Contents", []):
                if not o["Key"].endswith("/output.txt"):
                    continue
                try:
                    txt = s3c.get_object(Bucket=TESSERA_RUNS_BUCKET, Key=o["Key"])["Body"].read().decode("utf-8", "replace")
                except Exception:
                    continue
                for cid, uri in _RUN_CIT_RE.findall(txt):
                    cid = cid.strip().rstrip(".,;")
                    if cid and cid not in out:
                        out[cid] = uri
                        if len(out) >= limit:
                            return out
            tok = r.get("NextContinuationToken")
            if not tok:
                break
    except Exception as e:
        print(f"citation harvest failed for {session_id}: {type(e).__name__}: {e}")
    return out
# Blue/green catalog versioning: the active-version pointer lives ONLY in the
# refresh-state table (pk=DOMAIN#<d>, sk=ACTIVE -> {version}). Catalog reads must
# resolve it so they hit /tessera/catalog/<version>/<type> and not the legacy
# unversioned namespace. Same table lambdas/catalog-api resolves against.
CATALOG_REFRESH_TABLE = os.environ.get("CATALOG_REFRESH_TABLE", "")


def _get_gateway_secret():
    """Retrieve Gateway M2M client secret from AWS Secrets Manager."""
    secret_arn = os.environ.get("TESSERA_GATEWAY_SECRET_ARN", "")
    if not secret_arn:
        raise RuntimeError("TESSERA_GATEWAY_SECRET_ARN not set — cannot retrieve gateway secret")
    import boto3
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return client.get_secret_value(SecretId=secret_arn)["SecretString"]

# === Model tier configuration ===
# Synthesizer agents (with depends_on) must reason over many upstream inputs and
# require the strongest model. Leaf research agents are mostly tool-driven and
# work fine on Sonnet. Both are env-driven so versions bump without code changes.
SYNTHESIZER_MODEL_ID = os.environ.get(
    "SYNTHESIZER_MODEL_ID", "us.anthropic.claude-opus-4-7"
)
LEAF_MODEL_ID = os.environ.get(
    "LEAF_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)


def _sanitize_http_response(text: str, max_bytes: int = 8192) -> str:
    """Sanitize raw http_request output before it lands in the agent's
    conversation history.

    Why: the strands_tools http_request returns the raw response body. For
    HTML pages (real-estate listings, news, dashboards) this is overwhelmingly
    noise: <script>, <style>, <head>, <noscript>, <iframe>, modal forms, GTM/
    analytics blobs, inline JS runtime, embedded SVG. A single fetch can
    add 30-60K tokens of non-content elements to every subsequent Amazon Bedrock
    turn's input context, consuming the agent's output budget for STRUCTURED FINDINGS.

    Strategy:
      1. Detect HTML-flavoured responses (preserve JSON / plain-text as-is).
      2. Strip noise blocks via stdlib html.parser — no extra deps.
      3. Collapse runs of whitespace.
      4. Cap final body at `max_bytes` with explicit truncation marker so the
         agent knows it's seeing only part of the page.

    NEVER silently drops content without leaving a marker — the agent must
    know when it's looking at a partial response so it can fetch a deeper
    URL or use code_interpreter for full processing.
    """
    if not text:
        return text
    # The strands http_request tool returns "Status Code: NNNHeaders: {...}Body: <body>"
    # in a single string. Split off the body so we only sanitize HTML-shaped bodies.
    body_marker = "Body: "
    body_idx = text.find(body_marker)
    if body_idx < 0:
        # No recognisable body separator — just cap the whole thing.
        return _cap_with_marker(text, max_bytes)
    head = text[: body_idx + len(body_marker)]  # status + headers + "Body: "
    body = text[body_idx + len(body_marker):]

    # HTML detection — case-insensitive substring match on the first 1KB.
    sniff = body[:1024].lower()
    is_html = ("<!doctype html" in sniff) or ("<html" in sniff) or ("<head" in sniff and "<body" in sniff)

    if is_html:
        body = _html_to_text(body)

    # Collapse whitespace — multi-line runs of blanks add no signal.
    import re as _re
    body = _re.sub(r"\n{3,}", "\n\n", body)
    body = _re.sub(r"[ \t]{2,}", " ", body)
    body = body.strip()

    full = head + body
    return _cap_with_marker(full, max_bytes)


def _cap_with_marker(text: str, max_bytes: int) -> str:
    """Cap a string to `max_bytes` (UTF-8 byte length) with an explicit
    marker line so the agent knows it's seeing a truncated response."""
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= max_bytes:
        return text
    keep = encoded[: max(0, max_bytes - 200)].decode("utf-8", errors="ignore")
    marker = (
        f"\n\n[... TRUNCATED — full response was {len(encoded)} bytes; "
        f"capped at {max_bytes} bytes for context safety. "
        f"Use code_interpreter or a more specific URL if you need the full payload.]"
    )
    return keep + marker


def _html_to_text(html: str) -> str:
    """Strip <script>, <style>, <head>, <noscript>, <iframe>, <svg>, modal
    forms, and other noise from an HTML payload; return visible text with
    block-level boundaries preserved as newlines.

    Pure stdlib (html.parser) — no extra deps.
    """
    from html.parser import HTMLParser

    SKIP_TAGS = {"script", "style", "head", "noscript", "iframe", "svg",
                 "link", "meta", "form", "input", "button", "select",
                 "option", "label", "textarea", "footer", "header", "nav"}
    BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br",
                  "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.skip_depth = 0
        def handle_starttag(self, tag, attrs):
            if tag in SKIP_TAGS:
                self.skip_depth += 1
            elif tag in BLOCK_TAGS and self.skip_depth == 0:
                self.parts.append("\n")
        def handle_endtag(self, tag):
            if tag in SKIP_TAGS and self.skip_depth > 0:
                self.skip_depth -= 1
            elif tag in BLOCK_TAGS and self.skip_depth == 0:
                self.parts.append("\n")
        def handle_data(self, data):
            if self.skip_depth == 0:
                self.parts.append(data)

    try:
        s = _Stripper()
        s.feed(html)
        return "".join(s.parts)
    except Exception:
        # Parser failure on malformed HTML — fall back to passing through;
        # the cap still applies.
        return html


def _namespace_allowed(requested: str, allowed: list) -> bool:
    """True if `requested` namespace is one of `allowed` (exact) or a sub-path of one.

    Allows hierarchical namespaces — granting `/tessera/catalog` allows access to
    `/tessera/catalog/risks` but not `/tessera/other`.
    """
    if not requested or not allowed:
        return False
    requested = requested.rstrip("/")
    for a in allowed:
        a = (a or "").rstrip("/")
        if not a:
            continue
        if requested == a or requested.startswith(a + "/"):
            return True
    return False


def _namespace_to_actor_session(namespace: str) -> tuple:
    """Map a logical namespace path like /tessera/catalog/risks to AgentCore Memory's
    (actorId, sessionId) tuple so the strategy template /tessera/{actorId}/{sessionId}
    produces records at exactly the path the agent specified.

    The first segment after /tessera becomes actorId; remaining segments join with '-'
    to form sessionId. Trailing slashes mean prefix mode — caller checks separately.

    Examples:
      /tessera/catalog/risks                -> ("catalog", "risks")
      /tessera/catalog/attack-techniques    -> ("catalog", "attack-techniques")
      /tessera/sessions/abc/agents/foo      -> ("sessions", "abc-agents-foo")
      /tessera/org/profile                  -> ("org", "profile")
      /tessera/catalog/                     -> ("catalog", "")  (prefix mode)
    """
    if not namespace:
        return ("default", "default")
    parts = [p for p in namespace.strip("/").split("/") if p]
    # Strip leading "tessera" if present
    if parts and parts[0] == "tessera":
        parts = parts[1:]
    if not parts:
        return ("default", "default")
    if len(parts) == 1:
        return (parts[0], "")
    actor = parts[0]
    session = "-".join(parts[1:])
    # AgentCore Memory has limits on actorId/sessionId length and characters.
    # Sanitize: keep alphanumeric + hyphen + underscore, truncate.
    import re as _re
    actor = _re.sub(r"[^A-Za-z0-9_-]", "-", actor)[:64] or "default"
    session = _re.sub(r"[^A-Za-z0-9_-]", "-", session)[:64]
    return (actor, session)


def _is_single_segment(namespace: str) -> bool:
    """True for paths like '/tessera/catalog' (one segment after /tessera).

    Single-segment writes fall back to session='default' (AgentCore needs a
    non-empty sessionId). Reads must walk all sessions for the actor to find
    those events — otherwise writers and readers disagree on what 'this
    namespace' contains.
    """
    if not namespace or namespace.endswith("/"):
        return False
    parts = [p for p in namespace.strip("/").split("/") if p]
    if parts and parts[0] == "tessera":
        parts = parts[1:]
    return len(parts) == 1


def _is_prefix_only(namespace: str, allowed: list) -> bool:
    """True if the requested namespace is a strict ancestor of any granted path.

    Allows operators to declare granular paths in YAML (e.g. /catalog/risks,
    /catalog/attack-techniques) and have agents query the parent (/catalog/) to
    fetch from all of them.
    """
    if not namespace:
        return False
    requested = namespace.rstrip("/")
    for a in allowed or []:
        a = (a or "").rstrip("/")
        if a and a != requested and a.startswith(requested + "/"):
            return True
    return False


def _list_session_events(ac, memory_id: str, actor: str, session: str,
                         display_namespace: str, limit: int) -> list:
    """Helper: list events for one (actor, session) and shape them into entries."""
    if not session:
        return []
    try:
        resp = ac.list_events(
            memoryId=memory_id,
            actorId=actor,
            sessionId=session,
            maxResults=min(limit, 100),
            includePayloads=True,
        )
    except Exception as e:
        print(f"[memory_read] list_events error: actor={actor}, session={session}: {e}")
        return []
    raw_events = resp.get("events", []) or []
    entries = []
    for ev in resp.get("events", []) or []:
        for block in ev.get("payload", []) or []:
            conv = block.get("conversational") or {}
            text = (conv.get("content") or {}).get("text", "")
            if not text:
                continue
            md = ev.get("metadata", {}) or {}
            writer = ""
            wam = md.get("writer_agent_id")
            if isinstance(wam, dict):
                writer = wam.get("stringValue", "")
            src_ns = ""
            sn = md.get("source_namespace")
            if isinstance(sn, dict):
                src_ns = sn.get("stringValue", "")
            entries.append({
                "content": text[:1000],
                "writer_agent_id": writer,
                "namespace": src_ns or display_namespace,
                "timestamp": str(ev.get("eventTimestamp", "")),
            })
    return entries


# --- Native catalog recall (Phase M) ------------------------------------------
# retrieve_memory_records matches an EXACT namespace only (no prefix), and agents
# query at the TYPE level while each catalog entity is its own session
# (/tessera/catalog/<type>-<entity>). So we fan out: list the entity sessions under
# the requested prefix and run the NATIVE semantic retrieve on each, merging by
# score. AgentCore's semantic strategy does the ranking — no custom embeddings.

def _record_text(rec) -> str:
    c = rec.get("content") or {}
    if isinstance(c, dict):
        return c.get("text", "") or ""
    if isinstance(c, list):
        return " ".join((b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)
    return str(c)


# --- Native catalog records (self-managed "tessera_catalog" strategy) ----------
# The durable entity CATALOG writes FULL dossiers DIRECTLY as memory records
# (BatchCreateMemoryRecords) at their literal /tessera/catalog/<entity> namespace,
# and retrieves them via native retrieve_memory_records (ranked, full-fidelity).
# No chat extraction, no USER-role hack, no custom embeddings. The self-managed
# strategy id is resolved once by name and cached for the process lifetime.
_CATALOG_STRATEGY = {"id": None}
CATALOG_STRATEGY_NAME = "tessera_catalog"


def _is_catalog_ns(ns: str) -> bool:
    return ns.startswith("/tessera/catalog/") or ns == "/tessera/catalog"


def _catalog_strategy_id() -> str:
    """Resolve (and cache) the self-managed catalog strategy id by name."""
    if _CATALOG_STRATEGY["id"]:
        return _CATALOG_STRATEGY["id"]
    if not MEMORY_ID:
        return ""
    try:
        import boto3
        cpc = boto3.client("bedrock-agentcore-control", region_name=REGION)
        mem = cpc.get_memory(memoryId=MEMORY_ID).get("memory", {})
        for s in mem.get("strategies", []) or []:
            if s.get("name") == CATALOG_STRATEGY_NAME or s.get("type") == "CUSTOM":
                _CATALOG_STRATEGY["id"] = s.get("strategyId") or ""
                break
    except Exception as e:
        print(f"[catalog] strategy id resolve failed: {type(e).__name__}: {str(e)[:160]}")
    return _CATALOG_STRATEGY["id"] or ""


def _catalog_write_record(ac, namespace: str, content: str, agent_id: str) -> dict:
    """Upsert one full-fidelity dossier record at the exact catalog namespace
    (delete existing records there, then create) via the self-managed strategy."""
    import uuid
    from datetime import datetime, timezone as _tz
    sid = _catalog_strategy_id()
    if not sid:
        return {"error": "catalog strategy unavailable — record not written"}
    ns = namespace.rstrip("/")
    deleted = 0
    try:
        existing = ac.list_memory_records(memoryId=MEMORY_ID, namespace=ns, maxResults=100)
        ids = [r.get("memoryRecordId") for r in (existing.get("memoryRecordSummaries") or existing.get("memoryRecords") or []) if r.get("memoryRecordId")]
        if ids:
            ac.batch_delete_memory_records(memoryId=MEMORY_ID, records=[{"memoryRecordId": i} for i in ids])
            deleted = len(ids)
    except Exception as e:
        print(f"[catalog] upsert delete skipped ns={ns}: {type(e).__name__}: {str(e)[:120]}")
    rec = {
        "requestIdentifier": str(uuid.uuid4()),
        "namespaces": [ns],
        "content": {"text": _catalog_pack_text(ns, content)},
        "timestamp": datetime.now(_tz.utc),
        "memoryStrategyId": sid,
        "metadata": {"writer_agent_id": {"stringValue": (agent_id or "")[:200]}},
    }
    resp = ac.batch_create_memory_records(memoryId=MEMORY_ID, records=[rec])
    failed = resp.get("failedRecords") or []
    if failed:
        return {"error": f"record write failed: {str(failed[0])[:200]}"}
    rid = (resp.get("successfulRecords") or [{}])[0].get("memoryRecordId", "")
    return {"status": "written", "namespace": ns, "memoryRecordId": rid,
            "replaced": deleted, "store": "catalog_record"}


def _catalog_blob_bucket():
    return (os.environ.get("CATALOG_CONTENT_BUCKET") or os.environ.get("TESSERA_ECOSYSTEMS_BUCKET")
            or os.environ.get("ECOSYSTEMS_BUCKET") or "")


def _catalog_pack_text(ns: str, content: str, limit: int = 15000) -> str:
    """Full fidelity under the 16k Memory cap: if the dossier fits, store inline; else PUT
    the FULL record to S3 and keep a lean record (scalars/short strings/excerpts + a
    content_ref) in Memory. Mirrors tessera_platform.catalog.blob (the container can't
    import it). Never truncates the stored dossier."""
    try:
        if len(content) <= limit:
            return content
        rec = json.loads(content)
        if not isinstance(rec, dict):
            return content[:limit]
        bucket = _catalog_blob_bucket()
        if not bucket:
            return content[:limit]
        key = "catalog-content/" + ns.strip("/") + ".json"
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="application/json")
        lean = {}
        for k, v in rec.items():
            if isinstance(v, (int, float, bool)) or v is None:
                lean[k] = v
            elif isinstance(v, str):
                lean[k] = v if len(v) <= 400 else (v[:399] + "\u2026")
        lean["content_ref"] = f"s3://{bucket}/{key}"
        return json.dumps(lean, separators=(",", ":"), ensure_ascii=False)
    except Exception as e:
        print(f"[catalog] pack fallback ns={ns}: {type(e).__name__}: {str(e)[:120]}")
        return content[:limit]


def _catalog_resolve_text(text: str) -> str:
    """Reconstitute a FULL dossier for agent reads: if the record carries a content_ref,
    fetch the complete JSON from S3; else return the text unchanged. Best-effort."""
    try:
        rec = json.loads(text)
        ref = rec.get("content_ref") if isinstance(rec, dict) else None
        if not (isinstance(ref, str) and ref.startswith("s3://")):
            return text
        b, _, k = ref[5:].partition("/")
        return boto3.client("s3", region_name=REGION).get_object(Bucket=b, Key=k)["Body"].read().decode("utf-8")
    except Exception:
        return text


# ── Blue/green version resolution ─────────────────────────────────────────────
# The active-version pointer lives ONLY in the refresh table (pk=DOMAIN#<d>,
# sk=ACTIVE -> {version}). A shared runtime may serve several catalog domains,
# each with its own active version, so we read ALL active versions (tiny table,
# cached ~5 min) and try each version-scoped namespace before the legacy
# unversioned one. Mirrors lambdas/catalog-api's per-request resolution.
_ACTIVE_VERSIONS = {"vals": None, "ts": 0.0}
_ACTIVE_VERSIONS_TTL = 300  # seconds


def _catalog_active_versions() -> list:
    """All active catalog versions across domains, cached. Empty => no versioned
    catalog live (reads fall back to the legacy unversioned namespace)."""
    now = time.time()
    if _ACTIVE_VERSIONS["vals"] is not None and (now - _ACTIVE_VERSIONS["ts"] < _ACTIVE_VERSIONS_TTL):
        return _ACTIVE_VERSIONS["vals"]
    vals: list = []
    if CATALOG_REFRESH_TABLE:
        try:
            import boto3
            ddb = boto3.client("dynamodb", region_name=REGION)
            for page in ddb.get_paginator("scan").paginate(
                TableName=CATALOG_REFRESH_TABLE,
                FilterExpression="sk = :a",
                ExpressionAttributeValues={":a": {"S": "ACTIVE"}},
                ProjectionExpression="version",
            ):
                for it in page.get("Items", []) or []:
                    v = (it.get("version") or {}).get("S")
                    if v:
                        vals.append(v)
        except Exception as e:
            print(f"[catalog] active-version resolve failed: {type(e).__name__}: {str(e)[:160]}")
    _ACTIVE_VERSIONS["vals"] = vals
    _ACTIVE_VERSIONS["ts"] = now
    return vals


def _catalog_candidate_namespaces(namespace: str) -> list:
    """Priority-ordered namespaces for a logical catalog path: active-version-scoped
    first (blue/green), then the legacy unversioned path as fallback. e.g.
      /tessera/catalog/regulatory-obligation
        -> /tessera/catalog/<v>/regulatory-obligation   (per active version)
        -> /tessera/catalog/regulatory-obligation         (legacy fallback)
    """
    ns = namespace.rstrip("/")
    base = "/tessera/catalog"
    rest = ns[len(base):] if (ns == base or ns.startswith(base + "/")) else ""
    out = [f"{base}/{v}{rest}" for v in _catalog_active_versions()]
    out.append(ns)  # legacy fallback (also covers version-less deployments)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _catalog_read_records(ac, namespace: str, limit: int) -> list:
    """Full-fidelity dump of catalog records under a namespace (hierarchy),
    resolving the active blue/green version (versioned-first, legacy fallback)."""
    last_err = None
    for cns in _catalog_candidate_namespaces(namespace):
        try:
            resp = ac.list_memory_records(memoryId=MEMORY_ID, namespacePath=cns,
                                          maxResults=min(int(limit or 100), 100))
        except Exception as e:
            last_err = f"catalog read failed: {type(e).__name__}: {str(e)[:160]}"
            continue
        out = []
        for r in (resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or []):
            text = _record_text(r)
            if not text:
                continue
            nsf = r.get("namespaces")
            out.append({"content": _catalog_resolve_text(text),
                        "namespace": (nsf[0] if isinstance(nsf, list) and nsf else cns)})
        if out:
            return out
    return [{"error": last_err}] if last_err else []


def _catalog_search_records(ac, namespace: str, query: str, top_k: int) -> list:
    """Native ranked search over catalog records under a namespace (hierarchy),
    scoped to the self-managed strategy, resolving the active blue/green version
    (versioned-first, legacy fallback). Full-fidelity content, native ranking."""
    crit = {"searchQuery": query}
    sid = _catalog_strategy_id()
    if sid:
        crit["memoryStrategyId"] = sid
    last_err = None
    for cns in _catalog_candidate_namespaces(namespace):
        try:
            resp = ac.retrieve_memory_records(memoryId=MEMORY_ID, namespacePath=cns,
                                              searchCriteria=crit, maxResults=max(1, top_k))
        except Exception as e:
            last_err = f"catalog search failed: {type(e).__name__}: {str(e)[:160]}"
            continue
        out = []
        for r in (resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or []):
            text = _record_text(r)
            if not text:
                continue
            nsf = r.get("namespaces")
            out.append({"content": _catalog_resolve_text(text), "score": r.get("score") or r.get("relevanceScore"),
                        "namespace": (nsf[0] if isinstance(nsf, list) and nsf else cns)})
        if out:
            return out
    return [{"error": last_err}] if last_err else []


# Header name AgentCore Runtime uses to deliver the agent's workload access token
# alongside each /invocations request. Setting it on the BedrockAgentCoreContext makes
# `@requires_api_key` and other Identity SDK flows work transparently.
WORKLOAD_ACCESS_TOKEN_HEADER = "WorkloadAccessToken"  # nosec B105 — API header name, not a secret


# web search provider search wrapped with @requires_api_key. The decorator:
#   1. Reads the workload access token from BedrockAgentCoreContext (set per-request)
#   2. Calls IdentityClient.get_api_key(provider_name=..., agent_identity_token=...)
#   3. Injects api_key kwarg into our function
# No env var, no .env, no fallback. If the credential provider is empty or the
# workload token is missing, the decorator raises and the tool reports the failure.
try:
    from bedrock_agentcore.identity.auth import requires_api_key

    @requires_api_key(provider_name=WEBSEARCH_CREDENTIAL_PROVIDER)
    def _web_search_with_key(query: str, *, api_key: str) -> str:
        # Defensive: strip whitespace/newlines that may have been stored alongside the key
        api_key = (api_key or "").strip()
        if not api_key:
            raise RuntimeError("web search API key from credential provider is empty")
        # Try Bearer auth first (web search provider's current preferred form). If that fails
        # with 401/432 (auth-related), fall back to the legacy body-based form
        # so we surface the real failure mode if neither works.
        try:
            resp = httpx.post(
                WEBSEARCH_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": query, "max_results": 5},
                timeout=10,
            )
            if resp.status_code in (401, 432):
                # Fall back to body-form (some keys / accounts only work with this)
                resp = httpx.post(
                    WEBSEARCH_API_URL,
                    json={"api_key": api_key, "query": query, "max_results": 5},
                    timeout=10,
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Surface body so the operator sees web search provider's actual error message
            body = e.response.text[:200] if e.response is not None else ""
            raise RuntimeError(
                f"web search provider {e.response.status_code if e.response else '?'}: {body}"
            )
        results = resp.json().get("results", [])
        return json.dumps([
            {"title": r.get("title", ""), "snippet": r.get("content", "")[:300], "url": r.get("url", "")}
            for r in results
        ])
except ImportError as e:
    def _web_search_with_key(query: str, *, api_key: str = "") -> str:  # type: ignore[misc]
        raise RuntimeError(f"bedrock-agentcore SDK not available: {e}")


# JWT cache for the Gateway M2M client. Cognito client_credentials tokens last
# 1 hour; we cache for slightly less.
_jwt_cache: dict = {"token": None, "fetched_at": 0}
_JWT_CACHE_TTL = 3000  # 50 minutes


def get_gateway_jwt() -> str:
    """Fetch a JWT from Cognito for the agent → gateway M2M auth.

    Uses the OAuth2 client_credentials grant. The token is signed by Cognito and
    validated by the Gateway's CUSTOM_JWT authorizer.
    """
    if not GATEWAY_TOKEN_URL or not GATEWAY_CLIENT_ID:
        raise RuntimeError(
            "Gateway M2M auth not configured. Set TESSERA_GATEWAY_TOKEN_URL, "
            "TESSERA_GATEWAY_CLIENT_ID, TESSERA_GATEWAY_SECRET_ARN via the agent runtime env."
        )

    now = time.time()
    if _jwt_cache["token"] and (now - _jwt_cache["fetched_at"] < _JWT_CACHE_TTL):
        return _jwt_cache["token"]

    # Retrieve secret on-demand (not cached in module global)
    client_secret = _get_gateway_secret()
    resp = httpx.post(
        GATEWAY_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": GATEWAY_SCOPE,
        },
        auth=(GATEWAY_CLIENT_ID, client_secret),
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"Cognito returned no access_token: {resp.text[:200]}")
    _jwt_cache["token"] = token
    _jwt_cache["fetched_at"] = now
    return token


def discover_gateway_tools(allowed_tool_names: set) -> list:
    """Discover tools from the AgentCore Gateway via MCP and filter by allow-list.

    Returns the Strands-compatible tool wrappers for the discovered targets.
    Empty catalog → empty list (the agent runs with platform tools only).
    Connection failure → raise (no silent fallback to stubs).
    """
    if not GATEWAY_URL:
        # Gateway not configured — fine for non-tool ecosystems; return empty.
        return []

    try:
        from strands.tools.mcp import MCPClient
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as e:
        raise RuntimeError(f"Strands MCP client unavailable: {e}")

    jwt_token = get_gateway_jwt()

    def transport_factory():
        return streamablehttp_client(
            url=GATEWAY_URL,
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

    mcp = MCPClient(transport_factory)
    discovered = []
    with mcp:
        all_tools = mcp.list_tools_sync()
        for t in all_tools:
            name = getattr(t, "tool_name", None) or getattr(t, "name", None)
            if name and (not allowed_tool_names or name in allowed_tool_names):
                discovered.append(t)
    return discovered


A2UI_TOOL_DESCRIPTION = """Render a UI surface using the A2UI v0.9 Basic Catalog. Use this to display rich, structured UI to the user — forms, dashboards, comparison tables, decision summaries, etc.

Pass a JSON object with this shape:
{
  "surfaceId": "<unique_id>",
  "components": [ ...flat list of components linked by id... ],
  "dataModel": { ...optional initial data... }
}

The components form an adjacency list. There MUST be exactly one component with id "root" — it is the entry point of the tree. Other components are referenced by id from the root's children.

Every component has: { "id": "<unique>", "component": "<TypeName>", ...props }

Available component types (the Basic Catalog):

DISPLAY:
- Text         { text: "...", variant: "h1"|"h2"|"h3"|"body"|"caption" }   (text supports markdown)
- Image        { url: "...", alt: "..." }
- Icon         { name: "mail"|"check"|"warning"|"info"|"error"|"success"|"search"|"user"|"calendar"|"chart" }
- Video        { url: "..." }
- AudioPlayer  { url: "..." }

LAYOUT (containers — children: array of component IDs):
- Row          { children: [...], align?: "start"|"center"|"end"|"stretch", justify?: "start"|"center"|"end"|"spaceBetween" }
- Column       { children: [...], align?: ..., justify?: ... }
- List         { children: [...] }                              (scrollable)
- Card         { child: "<id>"  OR  children: [...] }
- Tabs         { tabs: [{ title: "...", child: "<id>" }, ...] }
- Divider      { axis?: "horizontal"|"vertical" }
- Modal        { open: true|false, child: "<id>" }

INTERACTIVE:
- Button       { text: "...", variant?: "primary"|"borderless", action: { event: { name: "...", context: {...} } } }
- CheckBox     { label: "...", value: { path: "/data/path" } }
- TextField    { label: "...", value: { path: "/data/path" }, variant?: "shortText"|"longText" }
- DateTimeInput{ value: { path: "/data/path" }, variant?: "datetime"|"date"|"time" }
- ChoicePicker { options: [{label, value}, ...], value: { path: "/data/path" }, variant?: "mutuallyExclusive"|"multipleSelect" }
- Slider       { min: 0, max: 100, step: 1, value: { path: "/data/path" } }

DATA VIZ (Tessera extensions — use these for tables, charts, KPI tiles):
- Metric       { label: "Revenue", value: "$45.2B", trend?: "+8% YoY", trendDirection?: "up"|"down"|"flat" }
- Table        { title?: "...", columns: ["Col1","Col2",...] OR [{key,label},...], rows: [["v1","v2",...],...] OR [{col1:v1, col2:v2},...] }
- Chart        { title?: "...", chartType: "bar"|"line"|"pie", data: { labels: ["A","B",...], values: [1,2,...] } OR { labels: [...], series: [{name:"X", values:[...]}, {name:"Y", values:[...]}] } }

STORYTELLING (Tessera extensions — use these for risk scores, alerts, sources, status):
- Gauge        { label?: "Risk score", value: 7.2, min?: 0, max?: 10, target?: 5, unit?: "/10",
                 zones?: [{from:0,to:3,color:"green"},{from:3,to:7,color:"amber"},{from:7,to:10,color:"red"}] }
                 — use for any 0-N score with target zones (risk score, control coverage %, confidence).
- Heatmap      { title?: "Risk matrix", xLabels: ["Low","Med","High"], yLabels: ["Cat","Sev","Crit"],
                 values: [[1,2,3],[2,4,6],[3,6,9]], colorScale?: "risk"|"heat" }
                 — values is a 2D array values[y][x]. Colors auto-scale. Use for likelihood×impact matrices,
                 control coverage by domain, vendor posture grids.
- Badge        { text: "PASS", severity: "success"|"warning"|"danger"|"info"|"neutral" }
                 — small inline pill for status. Pair with Row to lay out multiple side-by-side.
- AlertBanner  { severity: "success"|"warning"|"danger"|"info", title: "...", body?: "markdown allowed" }
                 — full-width callout. Use for critical findings, deployment blockers, policy violations,
                 important caveats. Don't overuse — reserve for high-attention items.
- CitationCard { title: "...", snippet?: "...", url?: "https://...", source?: "tool_name", retrievedAt?: "2024-10-31" }
                 — structured source citation. PREFER this over inline `[source: ...]` markdown when surfacing
                 evidence in a generated UI. List several inside a Column for a "Sources" section.

DATA BINDING:
- Any text or value can be a literal OR { "path": "/json/pointer" } to read from dataModel.
- Use formatString for templating: { "call": "formatString", "args": { "value": "Hello ${/user/name}!" } }

EXAMPLE — Investment analysis dashboard with KPIs, table, and chart:
{
  "surfaceId": "amzn_analysis",
  "components": [
    { "id": "root", "component": "Column", "children": ["title", "kpi_row", "history_chart", "peer_table", "verdict"] },
    { "id": "title", "component": "Text", "text": "# Example Corp (EXMP) — Q3 2024", "variant": "h2" },
    { "id": "kpi_row", "component": "Row", "children": ["m_rev", "m_op", "m_aws", "m_eps"], "justify": "spaceBetween" },
    { "id": "m_rev", "component": "Metric", "label": "Net Sales", "value": "$45.2B", "trend": "+8% YoY", "trendDirection": "up" },
    { "id": "m_op", "component": "Metric", "label": "Operating Income", "value": "$6.8B", "trend": "+12% YoY", "trendDirection": "up" },
    { "id": "m_aws", "component": "Metric", "label": "AWS Revenue", "value": "$27.5B", "trend": "+19% YoY", "trendDirection": "up" },
    { "id": "m_eps", "component": "Metric", "label": "EPS Surprise", "value": "+70.5%", "trendDirection": "up" },
    { "id": "history_chart", "component": "Chart", "title": "Revenue trend (last 4 quarters, $B)", "chartType": "bar",
      "data": { "labels": ["Q4'23","Q1'24","Q2'24","Q3'24"], "values": [170,143,148,158.9] } },
    { "id": "peer_table", "component": "Table", "title": "Cloud market share growth",
      "columns": ["Provider", "YoY growth", "Op margin"],
      "rows": [["AWS","19%","38%"],["Azure","33%","45%"],["GCP","35%","11%"]] },
    { "id": "verdict", "component": "Card", "child": "verdict_text" },
    { "id": "verdict_text", "component": "Text", "text": "**BUY** — Strong fundamentals, AWS deceleration is structural but margins expanding. **CONFIDENCE: HIGH**" }
  ]
}

Use this whenever the user benefits from structured visual output instead of plain prose. ALWAYS prefer Metric tiles over restating numbers in markdown, Table over markdown lists of rows, and Chart over describing trends in words."""


_CONCLUSION_RE = re.compile(
    r"(?im)^\s*(VERDICT|RECOMMENDATION|FINAL\s+DECISION|DECISION|GO\s*/\s*NO-?GO|WALK-?AWAY)\b"
    r"|\b(we\s+recommend\b|final\s+verdict\b|should\s+(?:buy|sell|hold)\b|NOT\s+VETOED\b)"
)


def _conclusion_signal(content: str) -> str:
    """Return the matched marker if `content` looks like a run CONCLUSION (verdict /
    recommendation / decision) rather than a durable, mission-invariant fact. Used to
    keep conclusions out of the shared catalog (M.2). Conservative — only fires on
    heading-style verdicts or unambiguous decision phrasing to avoid false positives."""
    m = _CONCLUSION_RE.search(content or "")
    return (m.group(0).strip()[:40]) if m else ""


# ─── Browser platform tool (Amazon Bedrock AgentCore managed Browser sandbox) ─
#
# The `browser` tool drives a MANAGED, sandboxed headless Chromium (the AgentCore
# Browser primitive — sibling of Code Interpreter) via Playwright over CDP. The
# browser itself runs in the AWS-managed sandbox; we only connect to it with
# Playwright's `connect_over_cdp` (so the container ships the pip package, NOT a
# Chromium binary). See docs/features/browser-tool.md.
#
# Because the FastAPI request handler runs inside an asyncio event loop and the
# Playwright *sync* API refuses to run inside a running loop, all browser work is
# delegated to a dedicated worker THREAD that owns the Playwright objects and the
# session lifecycle, and communicates via command/result queues. A fresh thread
# has no running event loop, so the sync API is happy and the session persists
# across ReAct turns (mirrors how code_interpreter reuses one session).

# Hostname/scheme SSRF policy. Blocks non-http(s) schemes and any URL resolving to
# a private / loopback / link-local / reserved address (defence against tricking
# the sandbox into reaching internal metadata endpoints like 169.254.169.254).
_BLOCKED_HOST_SUFFIXES = (".internal", ".local", ".localhost")


def _is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return (ok, reason). Only http/https to a public host is allowed."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    if not url or not isinstance(url, str):
        return False, "empty url"
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"unparseable url: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme {parsed.scheme!r} not allowed (only http/https)"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "missing host"
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, f"host {host!r} is not a public address"
    # Resolve to IP(s) and reject any private/reserved/loopback/link-local target.
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return False, f"DNS resolution failed for {host!r}: {e}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, f"host {host!r} resolves to non-public address {ip_str}"
    return True, "ok"


def _derive_browser_profile_name(user_id: str, domain: str) -> str:
    """Deterministic AgentCore Browser profile name for (operator, site). Must be
    stable so the runtime (lookup) and the browser-login Lambda (create/save) agree,
    and must satisfy the profile name charset ([A-Za-z][A-Za-z0-9_]{0,47})."""
    import re as _re
    u = _re.sub(r"[^a-zA-Z0-9]", "", (user_id or "anon"))[:8] or "anon"
    d = _re.sub(r"[^a-zA-Z0-9]+", "_", (domain or "site").lower()).strip("_") or "site"
    name = _re.sub(r"[^a-zA-Z0-9_]", "_", f"t_{u}_{d}")
    if not name[:1].isalpha():
        name = "t" + name
    return name[:48]


def _lookup_browser_profile_id(region: str, profile_name: str) -> str:
    """Return the profileId of a SEEDED profile with this name (auth state saved),
    else '' — meaning the site needs a human login first. Best-effort/never raises."""
    if not profile_name:
        return ""
    try:
        import boto3
        cp = boto3.client("bedrock-agentcore-control", region_name=region)
        for s in (cp.list_browser_profiles(name=profile_name).get("profileSummaries") or []):
            pid = s.get("profileId")
            if not pid:
                continue
            try:
                prof = cp.get_browser_profile(profileId=pid)
                if prof.get("lastSavedAt"):   # profile has been seeded with auth state
                    return pid
            except Exception:
                pass
        return ""
    except Exception as e:
        print(f"[browser] profile lookup failed for {profile_name!r}: {e}")
        return ""


class _BrowserWorker(_threading.Thread):
    """Owns a single AgentCore Browser sandbox session + Playwright page on a
    dedicated thread. Commands are dispatched via a queue; results come back on
    another queue. One worker per agent invocation (lazy-started on first call)."""

    def __init__(self, region: str, viewport: dict | None = None, profile_id: str = ""):
        super().__init__(daemon=True, name="tessera-browser-worker")
        self.region = region
        self.viewport = viewport or {"width": 1440, "height": 900}
        self.profile_id = profile_id or ""   # AgentCore Browser profile (persisted auth state)
        self._cmd_q: "object" = __import__("queue").Queue()
        self._res_q: "object" = __import__("queue").Queue()
        self._ready = _threading.Event()
        self._start_error: str = ""

    # --- public API (called from the tool thread) ---------------------------
    def ensure_started(self, timeout: float = 60.0) -> None:
        if not self.is_alive():
            self.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError("browser sandbox did not become ready in time")
        if self._start_error:
            raise RuntimeError(self._start_error)

    def call(self, op: str, args: dict, timeout: float = 90.0) -> dict:
        self.ensure_started()
        self._cmd_q.put((op, args))
        status, value = self._res_q.get(timeout=timeout)
        if status == "err":
            raise RuntimeError(value)
        return value

    def shutdown(self) -> None:
        if self.is_alive():
            try:
                self._cmd_q.put((None, None))
                self.join(timeout=20)
            except Exception:
                pass

    # --- worker thread body -------------------------------------------------
    def run(self) -> None:
        page = None
        client = None
        pw = None
        browser = None
        try:
            from playwright.sync_api import sync_playwright
            from bedrock_agentcore.tools.browser_client import BrowserClient

            client = BrowserClient(self.region)
            # Bind the session to a persisted auth PROFILE when we have one — the
            # session then starts already logged in (profile seeded via the
            # browser_login Attention flow). Without a profile it's a clean,
            # anonymous session (auth-gated apps will hit a login wall).
            start_kwargs = {"session_timeout_seconds": 1800, "viewport": self.viewport}
            if self.profile_id:
                start_kwargs["profile_configuration"] = {"profileIdentifier": self.profile_id}
            client.start(**start_kwargs)
            ws_url, headers = client.generate_ws_headers()
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(ws_url, headers=headers)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(30000)
            self._ready.set()
        except Exception as e:
            self._start_error = f"browser sandbox start failed: {type(e).__name__}: {str(e)[:300]}"
            self._ready.set()
            return

        while True:
            op, args = self._cmd_q.get()
            if op is None:  # shutdown sentinel
                break
            try:
                self._res_q.put(("ok", self._dispatch(page, op, args or {})))
            except Exception as e:
                self._res_q.put(("err", f"{type(e).__name__}: {str(e)[:300]}"))

        # cleanup
        for closer in (
            lambda: browser.close() if browser else None,
            lambda: pw.stop() if pw else None,
            lambda: client.stop() if client else None,
        ):
            try:
                closer()
            except Exception:
                pass

    def _dispatch(self, page, op: str, args: dict):
        if op == "navigate":
            page.goto(args["url"], wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # SPAs may never go fully idle — best effort
            return {"url": page.url, "title": page.title()}
        if op == "read":
            html = page.content()
            interactive = page.evaluate(
                """() => {
                    const out = [];
                    const seen = new Set();
                    const sel = 'a,button,[role=button],input,select,textarea,[role=link],[role=tab],[role=menuitem]';
                    for (const el of document.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        let name = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || el.value || '').trim();
                        name = name.replace(/\\s+/g, ' ').slice(0, 80);
                        if (!name) continue;
                        const kind = el.tagName.toLowerCase();
                        const key = kind + '|' + name;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push({kind, name});
                        if (out.length >= 60) break;
                    }
                    return out;
                }"""
            )
            return {"url": page.url, "title": page.title(), "text": html, "interactive": interactive}
        if op == "click":
            target = args["target"]
            loc = self._resolve_locator(page, target)
            loc.click(timeout=15000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            return {"clicked": target, "url": page.url, "title": page.title()}
        if op == "type":
            target = args["target"]
            loc = self._resolve_locator(page, target)
            loc.fill(args.get("text", ""), timeout=15000)
            if args.get("submit"):
                loc.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
            return {"typed_into": target, "submitted": bool(args.get("submit")), "url": page.url}
        if op == "back":
            page.go_back(wait_until="domcontentloaded")
            return {"url": page.url, "title": page.title()}
        if op == "screenshot":
            png = page.screenshot(full_page=bool(args.get("full_page")), type="jpeg", quality=int(args.get("quality", 55)))
            return {"bytes": png, "url": page.url, "title": page.title()}
        raise ValueError(f"unknown browser op {op!r}")

    @staticmethod
    def _resolve_locator(page, target: str):
        """Resolve a human target ('Login button', 'email', a CSS selector) to a
        Playwright locator. Tries role/text/label/placeholder before CSS."""
        t = (target or "").strip()
        # Explicit CSS/selector escape hatch
        if t.startswith(("#", ".", "//", "css=", "xpath=", "text=")):
            return page.locator(t).first
        # Prefer an accessible-name match across common roles, then text, then
        # label/placeholder for inputs. get_by_text is a broad fallback.
        candidates = [
            page.get_by_role("button", name=t, exact=False),
            page.get_by_role("link", name=t, exact=False),
            page.get_by_label(t, exact=False),
            page.get_by_placeholder(t, exact=False),
            page.get_by_text(t, exact=False),
        ]
        for c in candidates:
            try:
                if c.count() > 0:
                    return c.first
            except Exception:
                continue
        return page.get_by_text(t, exact=False).first


def build_tools(tool_names: list, a2ui_sink: list, agent_id: str, memory_config: dict | None = None, tool_budget: dict | None = None, run_session_id: str = "default", user_id: str = "", ecosystem_id: str = "", mission: str = "") -> list:
    """Compose the tool list for an agent invocation.

    Tools come from these sources:
      1. Platform tools (web_search, generateUI, memory_read, memory_write,
         calculator, current_time, http_request, code_interpreter) — always included.
      2. Discovered Gateway/MCP tools — filtered by ecosystem YAML.
      3. (Removed) Dynamic stubs — these are no longer created. If a tool
         appears in the YAML but isn't registered in the Gateway, the agent
         simply doesn't get it. The model will work without it or fail loudly.

    A2UI surfaces emitted via generateUI are appended to a2ui_sink so the server
    can stream them as STATE_DELTA events after the agent finishes.

    memory_config carries the agent's allowed namespaces from its ecosystem YAML:
        {"writable": ["/tessera/catalog/risks"], "readable": ["/tessera/catalog/risks", "/tessera/org/profile"]}
    Tools enforce these ACLs at call time. Cross-ecosystem reads are visible in the
    UI via STATE_DELTA events emitted alongside each memory operation.

    tool_budget (Phase 5.1): per-tool hard cap on calls per invocation, e.g.
        {"web_search": 6, "memory_read": 5}
    Compiler injects this from the YAML role-default + per-agent override. When
    cap is hit, the tool returns a structured `tool_budget_exhausted` error that
    tells the model to stop and synthesize. Tools also append `_budget` metadata
    to their results so the model can self-pace.
    """
    memory_config = memory_config or {"writable": [], "readable": []}
    writable_namespaces = memory_config.get("writable", []) or []
    readable_namespaces = memory_config.get("readable", []) or []
    tool_budget = tool_budget or {}
    # Closure-scoped per-invocation counter (each Lambda invocation = each agent run)
    used = {tn: 0 for tn in tool_budget}

    def _budget_check(tool_name: str) -> str | None:
        """Pre-flight: returns JSON error string if budget exhausted, else None to proceed.

        Caps not present in tool_budget are treated as unlimited (e.g. operator
        added a Gateway tool without specifying a cap — falls through to caller).
        """
        cap = tool_budget.get(tool_name)
        if cap is None:
            return None
        if used.get(tool_name, 0) >= cap:
            return json.dumps({
                "error": "tool_budget_exhausted",
                "tool": tool_name,
                "cap": cap,
                "used": used.get(tool_name, 0),
                "message": (
                    f"You have used all {cap} of your {tool_name} calls. Stop calling "
                    f"{tool_name} and synthesize from the evidence you've already gathered. "
                    f"If you cite a finding that needed another {tool_name} call, mark it "
                    f"`[novel — budget exhausted]`."
                ),
            })
        used[tool_name] = used.get(tool_name, 0) + 1
        return None

    def _budget_decorate(tool_name: str, raw_result: str) -> str:
        """Post-flight: append _budget metadata to JSON-object results so the
        model can self-pace. JSON arrays / non-JSON returns pass through unchanged.
        """
        cap = tool_budget.get(tool_name)
        if cap is None:
            return raw_result
        try:
            parsed = json.loads(raw_result)
            if isinstance(parsed, dict):
                parsed["_budget"] = {
                    "used": used.get(tool_name, 0),
                    "cap": cap,
                    "remaining": max(0, cap - used.get(tool_name, 0)),
                }
                return json.dumps(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
        return raw_result

    @tool(name="web_search", description="Search the web for current information. Use this to find real-time data, news, facts, or evidence. Returns relevant results with titles, snippets, and URLs.")
    def web_search(query: str) -> str:
        err = _budget_check("web_search")
        if err: return err
        try:
            result = _web_search_with_key(query)
        except Exception as e:
            result = json.dumps({"error": f"web_search failed: {e}"})
        # Cap result size — search results re-enter the agent's context on EVERY
        # subsequent turn (ReAct loop), so a few large result sets dominate input
        # tokens. 12KB keeps titles/snippets/URLs while bounding the bloat.
        result = _cap_with_marker(result, 12000)
        return _budget_decorate("web_search", result)

    @tool(name="generateUI", description=A2UI_TOOL_DESCRIPTION)
    def generate_ui(surface_json: str) -> str:
        err = _budget_check("generateUI")
        if err: return err
        try:
            surface = json.loads(surface_json)
            if not isinstance(surface, dict) or "components" not in surface:
                return _budget_decorate("generateUI", json.dumps({"status": "error", "message": "Surface must be an object with a 'components' array"}))
            components = surface.get("components", [])
            if not any(c.get("id") == "root" for c in components):
                return _budget_decorate("generateUI", json.dumps({"status": "error", "message": "Surface must contain a component with id='root'"}))
            a2ui_sink.append(surface)
            return _budget_decorate("generateUI", json.dumps({
                "status": "rendered",
                "surfaceId": surface.get("surfaceId", "unknown"),
                "component_count": len(components),
            }))
        except Exception as e:
            return _budget_decorate("generateUI", json.dumps({"status": "error", "message": str(e)}))

    @tool(name="memory_write",
          description=(
              "Publish a DURABLE, mission-INVARIANT fact or dossier to the shared catalog so "
              "FUTURE runs (this ecosystem or others) can reuse it. Use an ENTITY-KEYED catalog "
              "path: /tessera/catalog/<domain>/<entity> (e.g. /tessera/catalog/agents/jane-doe, "
              "/tessera/catalog/controls/CKV_AWS_173). "
              "WRITE: stable facts/dossiers about an entity (a company, person, control, audience, "
              "product). DO NOT WRITE: this run's conclusions, verdicts, recommendations, or "
              "mission-specific analysis — those belong in your normal output, NOT in memory. "
              "ALWAYS embed provenance in the content: who wrote it, today's date, and the source. "
              "When updating an entity you already know, RESTATE it with a newer date and note what "
              "it supersedes (e.g. 'refresh 2026-06-23 — supersedes 2026-05-01 on pricing'). "
              "You may only write to namespaces your ecosystem grants. Returns {status, eventId, namespace}."
          ))
    def memory_write(namespace: str, content: str) -> str:
        err = _budget_check("memory_write")
        if err: return err
        if not MEMORY_ID:
            return _budget_decorate("memory_write", json.dumps({"error": "MEMORY_ID not configured — memory disabled"}))
        # Normalize: agents often pass short names like "songbook" instead of
        # "/tessera/songbook". Auto-prefix if missing.
        if not namespace.startswith("/"):
            namespace = f"/tessera/{namespace}"
        # ACL: prefix-match against writable namespaces
        if not _namespace_allowed(namespace, writable_namespaces):
            return _budget_decorate("memory_write", json.dumps({
                "error": f"agent {agent_id!r} is not authorized to write to {namespace!r}",
                "allowed": writable_namespaces,
            }))
        # M.2: conclusions belong in your OUTPUT + run history, never the shared catalog.
        # Soft-reject content that is clearly a run conclusion (verdict/recommendation/
        # decision) so catalogs stay mission-INVARIANT facts. Catalog namespaces only.
        if namespace.startswith("/tessera/catalog/"):
            _sig = _conclusion_signal(content)
            if _sig:
                return _budget_decorate("memory_write", json.dumps({
                    "skipped": True,
                    "error": (f"NOT written — this looks like a run CONCLUSION (matched {_sig!r}), not a "
                              "durable fact. Verdicts/recommendations/decisions belong in your OUTPUT "
                              "(captured in run history), not the shared catalog. Write only "
                              "mission-invariant entity facts/dossiers (with provenance + refresh date)."),
                }))
        # CATALOG (durable entity dossiers) -> self-managed strategy: write the FULL
        # dossier DIRECTLY as a memory record (upsert by entity), full-fidelity +
        # natively retrievable. Chat/handoff and other namespaces stay create_event below.
        if _is_catalog_ns(namespace):
            try:
                import boto3
                ac = boto3.client("bedrock-agentcore", region_name=REGION)
                res = _catalog_write_record(ac, namespace, content, agent_id)
                return _budget_decorate("memory_write", json.dumps(res))
            except Exception as e:
                return _budget_decorate("memory_write", json.dumps({"error": f"memory_write failed: {str(e)[:300]}"}))
        # Map the user-supplied namespace to AgentCore Memory's actorId+sessionId so
        # the strategy template /tessera/{actorId}/{sessionId} produces records at the
        # exact path the agent specified. The first segment after /tessera becomes the
        # actorId; the rest joins as the sessionId. Matches what readers expect.
        actor, session = _namespace_to_actor_session(namespace)
        # User-scoped memory: prefix actor with user_id unless namespace is org-scoped
        # (org-scoped namespaces start with /tessera/org/ or /tessera/catalog/)
        is_org_scoped = namespace.startswith("/tessera/org/") or namespace.startswith("/tessera/catalog/")
        if user_id and not is_org_scoped:
            actor = f"{user_id}-{actor}"
        # AgentCore CreateEvent requires a non-empty sessionId. Single-segment
        # paths like /tessera/catalog map to session="" — use the run's session_id
        # so each run gets isolated memory (no cross-run pollution).
        if not session:
            session = run_session_id or "default"
        try:
            import boto3
            ac = boto3.client("bedrock-agentcore", region_name=REGION)
            from datetime import datetime, timezone as _tz
            resp = ac.create_event(
                memoryId=MEMORY_ID,
                actorId=actor,
                sessionId=session,
                eventTimestamp=datetime.now(_tz.utc),
                payload=[{"conversational": {"role": "USER", "content": {"text": content[:8000]}}}],
                metadata={
                    "source_namespace": {"stringValue": namespace},
                    "writer_agent_id": {"stringValue": agent_id},
                },
            )
            return _budget_decorate("memory_write", json.dumps({
                "status": "written",
                "eventId": resp.get("event", {}).get("eventId", ""),
                "namespace": namespace,
                "extraction_namespace": f"/tessera/{actor}/{session}",
            }))
        except Exception as e:
            return _budget_decorate("memory_write", json.dumps({"error": f"memory_write failed: {str(e)[:300]}"}))

    @tool(name="memory_read",
          description=(
              "Read entries from a shared memory namespace. Two modes:\n"
              "  - Exact path: '/tessera/catalog/risks' returns events at that path.\n"
              "  - Prefix path: '/tessera/catalog/' (trailing slash) returns events from\n"
              "    ALL sub-namespaces under the prefix, e.g. /catalog/risks AND\n"
              "    /catalog/attack-techniques.\n"
              "You can only read from namespaces your ecosystem YAML grants you access to. "
              "Optional `query` filter does case-insensitive substring match on content. "
              "Returns a JSON list of {content, writer_agent_id, namespace, timestamp}."
          ))
    def memory_read(namespace: str, query: str = "", limit: int = 100) -> str:
        err = _budget_check("memory_read")
        if err: return err
        if not MEMORY_ID:
            return _budget_decorate("memory_read", json.dumps({"error": "MEMORY_ID not configured — memory disabled"}))
        # Normalize: auto-prefix if agent passes short name like "songbook"
        if not namespace.startswith("/"):
            namespace = f"/tessera/{namespace}"
        if not _namespace_allowed(namespace, readable_namespaces):
            return _budget_decorate("memory_read", json.dumps({
                "error": f"agent {agent_id!r} is not authorized to read from {namespace!r}",
                "allowed": readable_namespaces,
            }))
        try:
            import boto3
            ac = boto3.client("bedrock-agentcore", region_name=REGION)

            # CATALOG -> read full-fidelity records from the self-managed strategy
            # (list_memory_records over the namespace hierarchy). Other namespaces use
            # the raw event walk below.
            if namespace.startswith("/tessera/catalog"):
                return _budget_decorate("memory_read", json.dumps(_catalog_read_records(ac, namespace, limit)))

            # Detect prefix mode: namespace ends in '/' OR is a parent of any writable
            # path the operator declared. Prefix mode walks all sub-sessions; exact
            # mode reads one session.
            #
            # ALSO trigger prefix mode for single-segment paths (e.g. /tessera/catalog),
            # since writes to those paths fall back to session="default" (AgentCore
            # requires non-empty sessionId), and we want reads to find them. Walking
            # all sessions for the actor surfaces the "default" session AND any other
            # sub-sessions written by other agents under the same actor — symmetric
            # with the write semantics.
            is_prefix = (
                namespace.endswith("/")
                or _is_prefix_only(namespace, readable_namespaces)
                or _is_single_segment(namespace)
            )
            entries = []

            if is_prefix:
                # Walk: list_sessions for the actor, filter sessions by path prefix
                actor, session_prefix = _namespace_to_actor_session(namespace)
                # User-scoped memory: prefix actor with user_id unless org-scoped
                is_org_scoped = namespace.startswith("/tessera/org/") or namespace.startswith("/tessera/catalog/")
                if user_id and not is_org_scoped:
                    actor = f"{user_id}-{actor}"
                # ALWAYS try the run's session directly first — writes to
                # single-segment paths land in run_session_id, and list_sessions
                # has eventual consistency that can miss recently-created sessions.
                entries = _list_session_events(ac, MEMORY_ID, actor, run_session_id or "default", namespace, limit)
                # For single-segment paths (e.g. /tessera/assessment), ONLY read
                # the current run's session. Walking all sessions would leak data
                # from previous runs. The walk-all behavior is reserved for
                # explicitly shared namespaces like /tessera/catalog/ (trailing slash).
                if not _is_single_segment(namespace):
                    try:
                        sess_resp = ac.list_sessions(memoryId=MEMORY_ID, actorId=actor, maxResults=50)
                    except Exception:
                        sess_resp = {}
                    for s in sess_resp.get("sessionSummaries", []) or []:
                        sid = s.get("sessionId", "")
                        if sid == (run_session_id or "default"):
                            continue  # already read above
                        if session_prefix and not sid.startswith(session_prefix):
                            continue
                        sub_entries = _list_session_events(ac, MEMORY_ID, actor, sid, namespace, limit)
                        entries.extend(sub_entries)
                        if len(entries) >= limit:
                            break
            else:
                # Exact path
                actor, session = _namespace_to_actor_session(namespace)
                # User-scoped memory: prefix actor with user_id unless org-scoped
                is_org_scoped_exact = namespace.startswith("/tessera/org/") or namespace.startswith("/tessera/catalog/")
                if user_id and not is_org_scoped_exact:
                    actor = f"{user_id}-{actor}"
                entries = _list_session_events(ac, MEMORY_ID, actor, session, namespace, limit)

            # Optional substring filter
            if query:
                q = query.lower()
                filtered = [e for e in entries if q in e["content"].lower()]
                if filtered:
                    entries = filtered

            return json.dumps(entries[:limit])
        except Exception as e:
            return _budget_decorate("memory_read", json.dumps({"error": f"memory_read failed: {str(e)[:300]}"}))

    @tool(name="memory_search",
          description=(
              "Relevance-RANKED semantic search over the shared catalog. Use this INSTEAD of "
              "memory_read when you want the most relevant facts/dossiers for a specific question "
              "rather than a raw chronological dump — it returns far less, more on-point context. "
              "Give a catalog namespace you're granted (prefer the most specific entity path, e.g. "
              "/tessera/catalog/agents) and a natural-language query. Returns a JSON list of "
              "{content, score, namespace}, ranked by relevance."
          ))
    def memory_search(namespace: str, query: str, limit: int = 5) -> str:
        err = _budget_check("memory_search")
        if err: return err
        if not MEMORY_ID:
            return _budget_decorate("memory_search", json.dumps({"error": "MEMORY_ID not configured — memory disabled"}))
        if not namespace.startswith("/"):
            namespace = f"/tessera/{namespace}"
        if not _namespace_allowed(namespace, readable_namespaces):
            return _budget_decorate("memory_search", json.dumps({
                "error": f"agent {agent_id!r} is not authorized to read from {namespace!r}",
                "allowed": readable_namespaces,
            }))
        if not (query or "").strip():
            return _budget_decorate("memory_search", json.dumps({
                "error": "memory_search requires a non-empty query — use memory_read for an unranked dump",
            }))
        try:
            import boto3
            ac = boto3.client("bedrock-agentcore", region_name=REGION)
            top_k = min(int(limit or 5), 20)
            if namespace.startswith("/tessera/catalog"):
                # CATALOG -> native ranked search over self-managed records
                # (full-fidelity, namespace-hierarchy scoped).
                out = _catalog_search_records(ac, namespace, query, top_k)
            else:
                # Non-catalog -> semantic-strategy records under the (user-scoped) path.
                actor, session = _namespace_to_actor_session(namespace)
                is_org = namespace.startswith("/tessera/org/")
                if user_id and not is_org:
                    actor = f"{user_id}-{actor}"
                search_ns = "/tessera/" + actor + (("/" + session) if session else "")
                resp = ac.retrieve_memory_records(memoryId=MEMORY_ID, namespacePath=search_ns,
                                                  searchCriteria={"searchQuery": query}, maxResults=top_k)
                out = []
                for r in (resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or []):
                    t = _record_text(r)
                    if not t:
                        continue
                    nsf = r.get("namespaces")
                    out.append({"content": t[:1500], "score": r.get("score") or r.get("relevanceScore"),
                                "namespace": (nsf[0] if isinstance(nsf, list) and nsf else search_ns)})
            print(f"[memory_search] eco={ecosystem_id} ns={namespace} returned={len(out)}")
            return _budget_decorate("memory_search", json.dumps(out))
        except Exception as e:
            return _budget_decorate("memory_search", json.dumps({"error": f"memory_search failed: {str(e)[:300]}"}))

    # --- Platform tool: calculator (strands_tools) ---
    @tool(name="calculator", description="Evaluate mathematical expressions. Supports arithmetic, algebra, calculus (derivatives, integrals, limits), matrix operations, and symbolic simplification. Input is a math expression string.")
    def calculator_tool(expression: str) -> str:
        err = _budget_check("calculator")
        if err: return err
        try:
            result = _strands_calculator(expression=expression)
            if isinstance(result, dict):
                text = ""
                for block in (result.get("content") or []):
                    if isinstance(block, dict) and "text" in block:
                        text += block["text"]
                return _budget_decorate("calculator", text or json.dumps(result))
            return _budget_decorate("calculator", str(result))
        except Exception as e:
            return _budget_decorate("calculator", json.dumps({"error": f"calculator failed: {e}"}))

    # --- Platform tool: current_time (strands_tools) ---
    @tool(name="current_time", description="Get the current date and time. Optionally specify a timezone (e.g. 'America/New_York', 'UTC', 'Asia/Tokyo'). Returns formatted datetime string.")
    def current_time_tool(timezone: str = "UTC") -> str:
        err = _budget_check("current_time")
        if err: return err
        try:
            result = _strands_current_time(timezone=timezone)
            return _budget_decorate("current_time", str(result))
        except Exception as e:
            return _budget_decorate("current_time", json.dumps({"error": f"current_time failed: {e}"}))

    # --- Platform tool: http_request (strands_tools) ---
    @tool(name="http_request", description="Make HTTP requests to URLs. Supports GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS. Use for fetching APIs, web pages, or HTTP endpoints. Input: method, url, and optionally headers, body, params. HTML responses are auto-sanitized (scripts/styles/head/forms stripped; visible text extracted) and capped at ~8KB to keep agent context tractable. Use code_interpreter if you need to programmatically process a larger raw response.")
    def http_request_tool(url: str, method: str = "GET", headers: str = "", body: str = "") -> str:
        err = _budget_check("http_request")
        if err: return err
        try:
            tool_input = {"url": url, "method": method}
            if headers:
                tool_input["headers"] = headers
            if body:
                tool_input["body"] = body
            result = _strands_http_request(tool={"toolUseId": "http", "input": tool_input})
            text = ""
            for block in (result.get("content") or []):
                if isinstance(block, dict) and "text" in block:
                    text += block["text"]
            text = text or json.dumps(result)
            # Sanitize HTML responses + cap size — see docs/features/
            # http-request-sanitizer.md (TODO). Without this, a single fetch of
            # a real-estate or news page injects 30-60K tokens of <script>/
            # <style>/inline-JS/modal-form noise into the agent's context,
            # which then poisons every subsequent Bedrock turn and starves
            # the agent's output budget for STRUCTURED FINDINGS. Observed
            # failure: building_forensic_analyst on 2/124A Galston Road
            # (2026-05-19) — 200s Lambda runtime, agent finished mid-sentence
            # after fetching the listing HTML. Sanitizer is per-tool-result,
            # so a stuck pattern of "fetch → drown in markup" becomes
            # "fetch → small clean text → make progress".
            text = _sanitize_http_response(text, max_bytes=8192)
            return _budget_decorate("http_request", text)
        except Exception as e:
            return _budget_decorate("http_request", json.dumps({"error": f"http_request failed: {e}"}))

    # --- Platform tool: code_interpreter (Amazon Bedrock AgentCore Code Interpreter) ---
    # Session is opened lazily on first call and reused for the invocation.
    _code_session_holder = {"session": None, "session_id": None}
    _artifact_holder = []  # [{filename, content}] — picked up by invoker after run

    @tool(name="code_interpreter", description="Run Python code in a sandboxed environment. Use for data analysis, calculations, file processing, or computations that benefit from running real code. Variables persist across calls within the same agent invocation. Returns stdout, stderr, and any generated files.")
    def code_interpreter_tool(code: str, language: str = "python") -> str:
        err = _budget_check("code_interpreter")
        if err: return err
        try:
            if _code_session_holder["session"] is None:
                from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
                ci = CodeInterpreter(REGION)
                ci.start()
                _code_session_holder["session"] = ci
                _code_session_holder["session_id"] = ci.session_id
            ci = _code_session_holder["session"]
            # Note: execute_code is the AgentCore CodeInterpreter SDK method name (cannot be renamed)
            result = ci.execute_code(code=code, language=language)
            # Defensive drain — observed (2026-05-19) that ci.execute_code may
            # return EventStream / generator-like values inside `result` (e.g.
            # result["stream"] or result["output"]) instead of plain strings.
            # When json.dumps() with default=str hits those, it produces
            # <object at 0x...> repr text that the agent literally complains
            # about: "code_interpreter is returning a stream wrapper rather
            # than stdout". Drain iterables to concrete values before serialising.
            if isinstance(result, dict):
                for key, value in list(result.items()):
                    if (hasattr(value, "__iter__")
                            and not isinstance(value, (str, bytes, bytearray, list, tuple, dict, set))):
                        try:
                            drained = []
                            for item in value:
                                if isinstance(item, dict):
                                    text = (item.get("text") or item.get("output")
                                            or item.get("content") or item.get("delta"))
                                    if text is None:
                                        drained.append(json.dumps(item, default=str))
                                    else:
                                        drained.append(str(text))
                                else:
                                    drained.append(str(item))
                            result[key] = "".join(drained) if all(isinstance(d, str) for d in drained) else drained
                        except Exception as drain_err:
                            result[key] = f"[stream drain failed: {type(drain_err).__name__}: {drain_err}]"
            result["_session_id"] = _code_session_holder["session_id"]
            # Cap what the model sees — full code output re-enters context every
            # turn. Generated FILES are captured separately (_artifact_holder) and
            # are unaffected by this cap; only the model-facing stdout/repr is bounded.
            return _budget_decorate("code_interpreter", _cap_with_marker(json.dumps(result, default=str), 16000))
        except Exception as e:
            err_msg = str(e)
            if "not available" in err_msg.lower() or "not supported" in err_msg.lower() or "AccessDenied" in err_msg:
                raise RuntimeError(
                    f"Amazon Bedrock AgentCore Code Interpreter is not available in {REGION}: {err_msg}"
                )
            return _budget_decorate("code_interpreter", json.dumps({"error": f"code_interpreter failed: {err_msg}"}))

    # --- Platform tool: browser (AgentCore managed Browser sandbox) ---------
    # State held across ReAct turns of THIS invocation. The worker thread is
    # stashed on _code_session_holder so the run-end cleanup (which already stops
    # the code_interpreter session) also shuts the browser sandbox down.
    _browser_holder = {"worker": None, "domain": "", "profile_name": ""}
    _code_session_holder["browser_worker"] = None
    _login_sink = []  # [{domain, profile_name}] — emitted as STATE_DELTA /browser_login/-
    _code_session_holder["login_sink"] = _login_sink
    _screenshot_seq = {"n": 0}

    def _browser_worker(profile_id: str = ""):
        w = _browser_holder["worker"]
        if w is None:
            w = _BrowserWorker(REGION, profile_id=profile_id)
            _browser_holder["worker"] = w
            _code_session_holder["browser_worker"] = w
        return w

    @tool(name="browser", description=(
        "Drive a real, sandboxed web browser to explore and understand a live web "
        "application: navigate to pages, read rendered content + the interactive "
        "elements on the page, click links/buttons, type into fields, and capture "
        "SCREENSHOTS for a presentation. Use this (not http_request) when a site is "
        "a JavaScript single-page app, requires clicking through a flow, or when you "
        "need visual screenshots. Call once per step.\n"
        "Parameters:\n"
        "  action  — one of: navigate | read | click | type | screenshot | back | request_login\n"
        "  url     — (navigate) absolute http(s) URL\n"
        "  target  — (click/type) the visible text / accessible name of the element "
        "(e.g. 'Sign in', 'Email'), or a CSS selector\n"
        "  text    — (type) text to enter\n"
        "  submit  — (type) true to press Enter after typing\n"
        "  caption — (screenshot) a short human caption of what the screenshot shows\n"
        "  full_page — (screenshot) true to capture the whole scrollable page\n"
        "If a site requires SIGN-IN and you are not already authenticated (navigate "
        "reports authenticated_profile=false and you see a login wall), do NOT try to "
        "log in yourself — call action='request_login' to raise a human sign-in request "
        "(handled out-of-band via an Attention item); a saved login profile then makes "
        "FUTURE runs authenticated. Capture what's visible, note it, and move on.\n"
        "'read' returns the page's visible text plus a list of clickable/typeable "
        "elements so you can decide the next step. 'screenshot' saves the image as a "
        "downloadable run artifact, shows it live in the run view, and returns a short "
        "reference token like `tessera-screenshot://<agent>/<file>` — cite that token "
        "in your SCREENSHOT MANIFEST so the presentation can embed the image."
    ))
    def browser_tool(action: str, url: str = "", target: str = "", text: str = "",
                     submit: bool = False, caption: str = "", full_page: bool = False) -> str:
        err = _budget_check("browser")
        if err:
            return err
        action = (action or "").strip().lower()
        try:
            if action == "navigate":
                ok, reason = _is_safe_public_url(url)
                if not ok:
                    return _budget_decorate("browser", json.dumps({
                        "error": "url_blocked",
                        "message": f"Refusing to navigate to {url!r}: {reason}. Only public http/https URLs are allowed.",
                    }))
                # Resolve a persisted auth profile for (operator, site). If one is
                # already seeded, the session starts logged in; otherwise it's a
                # clean session and auth-gated sites will need request_login.
                from urllib.parse import urlparse as _urlparse
                domain = (_urlparse(url).hostname or "").lower()
                profile_name = _derive_browser_profile_name(user_id, domain)
                profile_id = _lookup_browser_profile_id(REGION, profile_name) if _browser_holder["worker"] is None else ""
                _browser_holder["domain"] = domain
                _browser_holder["profile_name"] = profile_name
                res = _browser_worker(profile_id=profile_id).call("navigate", {"url": url})
                out = {"status": "navigated", **res, "authenticated_profile": bool(_browser_holder["worker"] and getattr(_browser_holder["worker"], "profile_id", ""))}
                if not out["authenticated_profile"]:
                    out["auth_hint"] = ("No saved login profile for this site. If it requires sign-in, "
                                        "call browser(action='request_login') — do not attempt to log in yourself.")
                return _budget_decorate("browser", json.dumps(out))

            if action == "request_login":
                domain = _browser_holder.get("domain") or ""
                profile_name = _browser_holder.get("profile_name") or ""
                if not domain and url:
                    from urllib.parse import urlparse as _urlparse
                    domain = (_urlparse(url).hostname or "").lower()
                    profile_name = _derive_browser_profile_name(user_id, domain)
                if not domain:
                    return _budget_decorate("browser", json.dumps({
                        "error": "request_login needs a navigated page (or a url) to identify the site"}))
                _login_sink.append({"domain": domain, "profile_name": profile_name, "user_id": user_id})
                return _budget_decorate("browser", json.dumps({
                    "status": "login_requested",
                    "domain": domain,
                    "note": ("A human sign-in request has been raised as an Attention item. You CANNOT "
                             "complete the login yourself. Capture what is visible now (e.g. the sign-in "
                             "page), add a '## NEEDS LOGIN' note to your findings naming the site, and "
                             "finish — once an operator signs in, a future run will be authenticated."),
                }))

            if action == "read":
                res = _browser_worker().call("read", {})
                visible = _html_to_text(res.get("text", ""))
                visible = _cap_with_marker(visible, 9000)
                return _budget_decorate("browser", json.dumps({
                    "status": "read",
                    "url": res.get("url", ""),
                    "title": res.get("title", ""),
                    "interactive_elements": res.get("interactive", []),
                    "visible_text": visible,
                }))

            if action == "click":
                if not target:
                    return _budget_decorate("browser", json.dumps({"error": "click requires 'target'"}))
                res = _browser_worker().call("click", {"target": target})
                return _budget_decorate("browser", json.dumps({"status": "clicked", **res}))

            if action == "type":
                if not target:
                    return _budget_decorate("browser", json.dumps({"error": "type requires 'target'"}))
                res = _browser_worker().call("type", {"target": target, "text": text, "submit": submit})
                return _budget_decorate("browser", json.dumps({"status": "typed", **res}))

            if action == "back":
                res = _browser_worker().call("back", {})
                return _budget_decorate("browser", json.dumps({"status": "back", **res}))

            if action == "screenshot":
                res = _browser_worker().call("screenshot", {"full_page": full_page})
                png = res.get("bytes", b"")
                if not png:
                    return _budget_decorate("browser", json.dumps({"error": "screenshot produced no image"}))
                import base64 as _b64
                b64 = _b64.b64encode(png).decode()
                _screenshot_seq["n"] += 1
                n = _screenshot_seq["n"]
                slug = re.sub(r"[^a-z0-9]+", "-", (caption or f"screen-{n}").lower()).strip("-")[:40] or f"screen-{n}"
                filename = f"screenshot-{n:02d}-{slug}.jpg"
                # 1) Downloadable run artifact (agent-invoker writes it to S3).
                _artifact_holder.append({"filename": filename, "content": b64, "binary": True})
                # 2) Live render in the run view via an A2UI Image surface (server-
                #    side data URI — the base64 NEVER enters the model's context).
                data_uri = f"data:image/jpeg;base64,{b64}"
                a2ui_sink.append({
                    "surfaceId": f"screenshot-{n}",
                    "components": [
                        {"id": "root", "component": "Column", "children": ["cap", "img"]},
                        {"id": "cap", "component": "Text",
                         "text": f"📸 {caption or res.get('title') or res.get('url','')}", "variant": "caption"},
                        {"id": "img", "component": "Image", "url": data_uri,
                         "alt": caption or res.get("title", "screenshot")},
                    ],
                })
                # 3) Compact, model-safe receipt: the ref token (NOT the base64).
                ref = f"tessera-screenshot://{agent_id}/{filename}"
                return _budget_decorate("browser", json.dumps({
                    "status": "captured",
                    "index": n,
                    "filename": filename,
                    "caption": caption,
                    "ref": ref,
                    "page_url": res.get("url", ""),
                    "note": "Saved as a run artifact and shown live. Reference this exact `ref` token in your SCREENSHOT MANIFEST; the presentation embeds it.",
                }))

            return _budget_decorate("browser", json.dumps({
                "error": f"unknown action {action!r}",
                "allowed": ["navigate", "read", "click", "type", "screenshot", "back", "request_login"],
            }))
        except Exception as e:
            return _budget_decorate("browser", json.dumps({"error": f"browser {action} failed: {str(e)[:300]}"}))

    # --- Platform tool: read_attachment (on-demand uploaded-input reader) ------
    # Uploaded run files are NO LONGER dumped whole into the mission. The launch
    # writes runs/<sid>/_attachments.json ([{name, ref}]) + a manifest (names +
    # summaries) into the mission; agents pull the FULL text on demand here. This
    # keeps the Step Functions state small (256KB cap) and lets each agent read
    # only the files it needs. Text formats are read directly; PDFs/docs use the
    # extracted text (<key>.extracted.txt from file-classify).
    _attach_manifest_cache = {"loaded": False, "items": []}

    def _load_attach_manifest():
        if _attach_manifest_cache["loaded"]:
            return _attach_manifest_cache["items"]
        items = []
        if TESSERA_RUNS_BUCKET and run_session_id:
            try:
                raw = _runs_s3().get_object(
                    Bucket=TESSERA_RUNS_BUCKET,
                    Key=f"runs/{run_session_id}/_attachments.json")["Body"].read()
                items = json.loads(raw) or []
            except Exception as e:
                print(f"read_attachment: manifest load failed for {run_session_id}: {type(e).__name__}: {e}")
        _attach_manifest_cache["items"] = items
        _attach_manifest_cache["loaded"] = True
        return items

    _TEXT_EXTS = {"md", "txt", "csv", "json", "yaml", "yml", "xml", "html", "htm", "log", "tsv", "ini", "toml", "rtf"}

    @tool(name="read_attachment", description=(
        "Read the full text of a file the operator uploaded for this run. The uploaded "
        "files are listed in your mission under 'ATTACHED FILES' (with a short summary of "
        "each). Call this to read or quote the EXACT contents/evidence of one of those "
        "files. Parameters: filename (the file's name as shown in the manifest — a close "
        "match is fine); offset (character offset to start from, default 0) and max_chars "
        "(default 40000) to page through large documents. Call with no filename to list "
        "the available files."))
    def read_attachment(filename: str = "", offset: int = 0, max_chars: int = 40000) -> str:
        err = _budget_check("read_attachment")
        if err:
            return err
        items = _load_attach_manifest()
        if not items:
            return _budget_decorate("read_attachment", json.dumps({
                "error": "no_attachments",
                "message": "No uploaded files are available for this run."}))
        names = [i.get("name", "") for i in items if i.get("name")]
        if not (filename or "").strip():
            return _budget_decorate("read_attachment", json.dumps({"available_files": names}))
        # Resolve filename: exact → case-insensitive → basename → substring.
        q = filename.strip()
        match = None
        for it in items:
            if it.get("name") == q:
                match = it; break
        if match is None:
            ql = q.lower()
            cands = [it for it in items if it.get("name", "").lower() == ql] \
                or [it for it in items if it.get("name", "").lower().split("/")[-1] == ql.split("/")[-1]] \
                or [it for it in items if ql in it.get("name", "").lower() or it.get("name", "").lower() in ql]
            if cands:
                match = cands[0]
        if match is None:
            return _budget_decorate("read_attachment", json.dumps({
                "error": "file_not_found", "requested": filename, "available_files": names}))
        ref = match.get("ref") or ""
        name = match.get("name") or filename
        if not ref.startswith("s3://"):
            return _budget_decorate("read_attachment", json.dumps({
                "error": "unreadable_ref", "file": name}))
        bucket, _, key = ref[5:].partition("/")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        text = ""
        try:
            if ext in _TEXT_EXTS:
                text = _runs_s3().get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", "replace")
            else:
                try:
                    text = _runs_s3().get_object(Bucket=bucket, Key=key + ".extracted.txt")["Body"].read().decode("utf-8", "replace")
                except Exception:
                    try:
                        meta = json.loads(_runs_s3().get_object(Bucket=bucket, Key=key + ".meta.json")["Body"].read())
                        summ = meta.get("summary", "")
                        return _budget_decorate("read_attachment", json.dumps({
                            "file": name, "note": "No extracted text available; summary only.",
                            "summary": summ}))
                    except Exception:
                        return _budget_decorate("read_attachment", json.dumps({
                            "error": "no_readable_content", "file": name}))
        except Exception as e:
            return _budget_decorate("read_attachment", json.dumps({
                "error": f"read failed: {type(e).__name__}: {e}", "file": name}))
        total = len(text)
        try:
            offset = max(0, int(offset)); max_chars = max(1, min(int(max_chars), 200000))
        except Exception:
            offset, max_chars = 0, 40000
        chunk = text[offset:offset + max_chars]
        out = {"file": name, "offset": offset, "returned_chars": len(chunk), "total_chars": total, "content": chunk}
        if offset + max_chars < total:
            out["truncated"] = True
            out["next_offset"] = offset + max_chars
            out["note"] = f"Showing chars {offset}-{offset + len(chunk)} of {total}. Call again with offset={offset + max_chars} for more."
        return _budget_decorate("read_attachment", json.dumps(out))

    # Always-on AMBIENT platform tools — every agent gets these.
    tools = [web_search, generate_ui, memory_write, memory_read, memory_search,
             calculator_tool, current_time_tool, http_request_tool, code_interpreter_tool,
             read_attachment]

    # `browser` is an OPT-IN platform tool. Unlike the ambient tools above it is a
    # heavier, more sensitive capability (it drives a live web browser and can
    # reach arbitrary public sites), so it is handed ONLY to an agent that
    # EXPLICITLY lists `browser` in its ecosystem YAML `tools:`. This makes browser
    # access a deliberate per-agent grant rather than something every agent — in
    # every ecosystem — can silently call. See docs/features/browser-tool.md.
    if "browser" in (tool_names or []):
        tools.append(browser_tool)

    # Capture platform tool names from the actual registered objects so the
    # set stays correct automatically when someone adds or removes a platform
    # tool above. No separate hardcoded list to drift out of sync.
    platform_tool_names = {
        getattr(t, "tool_name", getattr(t, "name", None)) for t in tools
    }
    platform_tool_names.discard(None)
    # `browser` is a RECOGNIZED platform-tool name even when it wasn't granted to
    # this agent, so an agent that declares `browser` is never flagged as
    # "tool requested but not registered".
    platform_tool_names.add("browser")

    # Discover tools from the Gateway, filter by what the ecosystem YAML requested.
    discovered_names = set()
    missing_names = set()
    if tool_names:
        try:
            allowed = set(tool_names)
            discovered = discover_gateway_tools(allowed)
            tools.extend(discovered)
            discovered_names = {getattr(t, "tool_name", getattr(t, "name", None)) for t in discovered}
            # `missing` = declared in YAML but neither Gateway-discovered nor a
            # platform tool. Without subtracting platform_tool_names here, the
            # platform's own built-in tools (memory_read, generateUI, etc.) would
            # be falsely flagged as "not registered" in the agent's system prompt
            # — which the agent then dutifully reports as "I cannot proceed,
            # memory_read is not registered." Verified bug from da-navigator
            # 2026-05-19; submission_strategist refused to run because both its
            # declared tools (memory_read, generateUI) were falsely flagged.
            missing_names = allowed - discovered_names - platform_tool_names
            if missing_names:
                print(f"WARN: tools requested but not registered: {sorted(missing_names)}")
        except Exception as e:
            # Hard failure — propagate. We don't fall back to stubs.
            print(f"ERROR: Gateway tool discovery failed: {e}")
            raise

    return tools, _code_session_holder, _artifact_holder, {"discovered": sorted(discovered_names), "missing": sorted(missing_names)}


# Agent system prompts use second person ("You are...") as required by LLM prompt engineering conventions.
ROLE_FRAMING = {
    "leaf": (
        "You are a LEAF ANALYST. There are no upstream agents — you are providing the first independent "
        "perspective in this domain. Focus narrowly on your specialty (do not try to cover what other "
        "analysts will do). Cite tool evidence with [source: tool_name]. Stay disciplined and concise."
    ),
    "synthesizer": (
        "You are a SYNTHESIZER. The CONVERSATION SO FAR shows what other analysts have already "
        "established. Your value is what you ADD — EXTEND, RECONCILE, or CHALLENGE upstream findings. "
        "NEVER restate what an agent already said. When building on or contradicting an upstream "
        "finding, cite the agent by name as [per agent_id: their finding]. If you agree with "
        "upstream, your job is to identify the IMPLICATIONS or extend the analysis — not to repeat."
    ),
    "researcher": (
        "You are a CONTEXT-AWARE RESEARCHER. The CONVERSATION SO FAR gives you upstream context — "
        "USE IT to target your research (e.g. the specific entity/asset/topic to investigate). Then do "
        "FRESH web research to gather current, verifiable facts. Prefix every tool-derived claim with "
        "[source: tool_name] and, when you build on an upstream finding, cite it as [per agent_id: ...]. "
        "Your findings are grounding-checked, so ground every time-sensitive claim in a dated tool result "
        "— never rely on training-data recall. Stay in your lane; research, don't just re-synthesize."
    ),
    "challenger": (
        "You are the CHALLENGER. The CONVERSATION SO FAR section contains the work you "
        "must review — it is ALWAYS present when you run; never claim otherwise. Critically "
        "review those findings AND the premises they accepted from the user's request. "
        "Identify: claims with no supporting evidence; logical gaps (conclusions that don't "
        "follow from the data); contradictions between agents; overconfident conclusions; and "
        "unsound or unverified premises carried over from the request (e.g. a misidentified "
        "tool, standard, definition, or scope). Quote the agent you are critiquing with "
        "[per agent_id: ...]; when the issue is a premise from the request itself, attribute "
        "it to (user request). NEVER state that there is no upstream/analyst work, that only "
        "the request is present, or that you are the 'first responder' — that is false, and "
        "the absence of an earlier stage is NEVER a finding. Be rigorous but fair: if the "
        "work is solid, say so."
    ),
    "reporter": (
        "You are the REPORTER. Faithfully synthesize all upstream findings AND the Challenger's critique. "
        "You are NOT a judge. Quote agents directly with [per agent_id: ...]. Do not silently drop or "
        "rewrite findings. When the Challenger flagged a claim as weak, surface BOTH the claim and the "
        "critique. Present disagreements intact. Cite by name. Use generateUI to visualize key findings."
    ),
}


MANDATORY_OUTPUT_FORMAT = """

--- MANDATORY OUTPUT FORMAT ---
Your response MUST BEGIN with the two sections below, BEFORE any prose,
tool calls, generateUI rendering, or supporting analysis. EMIT THESE
SECTIONS FIRST. They are not optional. Without a properly formatted
`## STRUCTURED FINDINGS` section as the first content of your response,
your output will be rejected.

WHY FIRST AND NOT LAST: when something goes wrong later in your output —
a tool returning a stream wrapper, max_tokens hit during a generateUI
render, Amazon Bedrock latency on a final turn — the contract section is the
most fragile thing to lose if it's at the END. Putting it FIRST makes
your finding bulletproof regardless of what happens after.

After emitting STRUCTURED FINDINGS and OPEN QUESTIONS, continue with
your prose, tool calls, generateUI rendering, and any supporting
analysis. Those are valuable but optional. The contract section above
them is the non-negotiable.

## STRUCTURED FINDINGS
- claim: <one-sentence finding>
  confidence: HIGH | MEDIUM | LOW
  evidence: [source: tool_name] short citation OR [per agent_id] when extending an upstream finding
- claim: <next finding>
  confidence: ...
  evidence: ...

## OPEN QUESTIONS
- question: <what is still uncertain>
  why: <why this matters for the mission>

(continue with prose / tool calls / UI rendering / analysis below)
"""


# Entity-keying (Phase M.1/M.6): injected ONLY for analysts that write a durable
# /catalog/ namespace. Makes the agent emit a stable per-entity slug so the catalog
# keeps one dossier PER real-world entity (per address / suburb / scheme) instead of
# overwriting a single shared record. agent-invoker appends this slug to the catalog
# namespace at auto-persist time (/tessera/catalog/<type>/<entity-key>).
ENTITY_KEY_CONTRACT_TEMPLATE = """

--- ENTITY KEY (durable catalog) ---
Your findings are auto-saved to a SHARED, durable catalog that future runs reuse, so
it can hold one dossier PER real-world entity (not overwrite a single record).
{spec}

Add this section immediately AFTER `## STRUCTURED FINDINGS`:

## ENTITY KEY
<one short, stable, lowercase slug, hyphens only, no spaces/punctuation,
 e.g. 12-slattery-road-north-rothbury-2335>

Use the SAME slug whenever this entity recurs so future runs UPDATE the same dossier
rather than create a duplicate. If this run concerns no single durable entity, omit
the section.
"""

_GENERIC_KEY_SPEC = ("Emit a stable identifier for THE primary entity this run is about, "
                     "derived from its most canonical attribute (street address, scheme number, ticker, ...).")
ENTITY_KEY_CONTRACT = ENTITY_KEY_CONTRACT_TEMPLATE.format(spec=_GENERIC_KEY_SPEC)


def _entity_key_contract(patterns: dict | None) -> str:
    """Build the ENTITY KEY instruction using the domain's declared key PATTERN(S) for
    the catalog type(s) this agent writes — so it keys by the right attribute (e.g. a
    selling-agent by {name-agency}, not by the property address). Falls back to the
    generic spec when no domain pattern is known (e.g. an eco-local catalog)."""
    if not patterns:
        return ENTITY_KEY_CONTRACT
    if len(patterns) == 1:
        t, k = next(iter(patterns.items()))
        spec = (f"This run's durable entity is a **{t}** — build its key from the pattern "
                f"`{k}` (fill the braces from the entity's canonical attributes).")
    else:
        lines = "; ".join(f"{t} -> `{k}`" for t, k in patterns.items())
        spec = (f"Key the PRIMARY durable entity this run is about using its type's pattern "
                f"({lines}).")
    return ENTITY_KEY_CONTRACT_TEMPLATE.format(spec=spec)


QUALITY_REQUIREMENTS = """

--- QUALITY REQUIREMENTS ---
- Prefix tool-derived claims with [source: tool_name].
- When extending or contradicting an upstream agent, cite them by name as [per agent_id: their finding].
- Your value is what you ADD: EXTEND, RECONCILE, or CHALLENGE — never restate.
- If you disagree with an upstream finding, say so explicitly with your reasoning.
- For ANY time-sensitive claim (prices, news, events, "current", "recent", "latest"), you MUST
  ground it in a tool result with an explicit date. Never rely on your training data's notion of
  "now" or "recent". If you cannot find a current source, say so explicitly — do not guess a year.
- Include a CONFIDENCE: HIGH/MEDIUM/LOW marker with one-line justification AFTER the structured
  sections, alongside or near your prose. (Confidence is no longer the last thing you emit;
  the structured sections are first.)
"""


# ─── Phase 5e: Per-kind output contracts ────────────────────────────────────
#
# Today every agent receives MANDATORY_OUTPUT_FORMAT + QUALITY_REQUIREMENTS
# regardless of its actual job. Reporters are forced to emit bullets; drafters
# satisfy STRUCTURED FINDINGS and stop without producing their artifact;
# challengers can emit AMBER alongside an open BLOCKER (logically inconsistent).
#
# The fix: declare output_kind in the agent YAML; runtime injects the matching
# contract; agent-invoker validates per kind.
#
# Default output_kind = "analyst" preserves all existing ecosystem behaviour.
# The chat_mode: true flag is now a deprecated alias for output_kind: chat.

CHALLENGER_OUTPUT_FORMAT = """

--- MANDATORY OUTPUT FORMAT (CHALLENGER) ---
You are critically reviewing the work shown to you in the conversation and the
premises it accepted from the request. Your response MUST contain the three
sections below, IN THIS ORDER, before any prose. Without all three sections
present and correctly formatted, your output will be REJECTED.

ORDER MATTERS — DO THE ANALYSIS FIRST, CONCLUDE LAST: write WEAKNESSES, then
UNRESOLVED CONTRADICTIONS, then derive the VERDICT LAST from what you logged.
Do NOT decide the verdict up front. DERIVATION RULE (mechanical, not a fresh
impression): any finding you log with severity BLOCKER forces VERDICT: WEAK —
no exceptions; otherwise material WARN-level gaps → ADEQUATE; only NITs/None →
STRONG if the work is genuinely solid, else ADEQUATE. A verdict that
contradicts your own logged severities is a contract violation and is rejected.

NOT A WEAKNESS — DO NOT LOG, MENTION, OR LET IT AFFECT YOUR VERDICT: the
work you review is provided to you in the "CONVERSATION SO FAR" section. It is
ALWAYS present when you run. NEVER write that "no upstream/analyst output
exists", that "the conversation contains only the user request", or that you
are "the first responder" — that is false. The absence of an earlier stage,
prior reviewer, or additional "upstream" beyond the work shown is NEVER a
finding (not BLOCKER, not WARN, not NIT) and must NOT appear in your VERDICT
justification. Being an early stage of the pipeline is normal. Judge ONLY the
substance of the work shown and the premises it took from the request.

## WEAKNESSES
- finding: <one-sentence concern>
  severity: BLOCKER | WARN | NIT
  agent: <the agent_id this concerns, or (user request) when the issue is a flawed premise from the request>
  evidence: [per <agent_id>: <quote>]  (or a quote of the request when it's a premise issue)

## UNRESOLVED CONTRADICTIONS
- <contradiction between agents that wasn't resolved> [per agent_a: ...] vs [per agent_b: ...]
  (or "None" if no contradictions found)

## VERDICT
<one of: STRONG | ADEQUATE | WEAK — DERIVED per the rule above: any BLOCKER you logged forces WEAK>
<one-line justification consistent with the severities you logged>

(continue with prose analysis below the three sections)
"""

REPORTER_OUTPUT_FORMAT = """

--- OUTPUT FORMAT (REPORTER) ---
You are composing the final human-facing narrative. DO NOT emit STRUCTURED
FINDINGS or OPEN QUESTIONS sections — those are for analyst agents. Your job
is to write a coherent prose report that SYNTHESIZES the upstream findings
and the challenger's verdict.

Required structure (markdown, prose, no bullet-list contracts):

## Executive Summary
2-4 sentences capturing the headline outcome and confidence.

## Key Findings
Prose narrative covering the most important upstream findings, organised by
theme not by agent. Cite agents inline with [per agent_id: ...].

## Disagreements & Tensions
If the challenger flagged weaknesses or unresolved contradictions, surface
them here. Quote the challenger directly.

## Risks & Concerns
Real-world risks the operator should know about before acting.

## Next Steps
Concrete, prioritised actions.

Length: ≥ 200 chars and ≤ ~3000 chars. Output below 200 chars will be
rejected as insufficient. Use generateUI to visualize key findings if it
adds clarity.
"""

CHAT_OUTPUT_FORMAT = """

--- OUTPUT FORMAT (CHAT) ---
You are answering the operator's follow-up question about a completed run.
Reply in plain prose — concise, accurate, citation-bearing. DO NOT emit
STRUCTURED FINDINGS or OPEN QUESTIONS sections.

When the answer derives from a specific agent's prior output, cite it with
[per agent_id: ...]. When the prior_output doesn't contain the answer, say
so plainly — never fabricate figures or claims.
"""

DRAFTER_OUTPUT_FORMAT = """

--- OUTPUT FORMAT (DRAFTER) ---
Your primary deliverable is the document/artifact itself, NOT analysis of it.

If an "OUTPUT FORMAT REQUIREMENT" section appears below (a typed artifact such as
html/json/yaml/pdf/csv): follow it exactly. BUILD the document into a file via
code_interpreter, write NO prose while building (no plan, no narration), and ONLY
when the file is COMPLETE end with a short ## SUMMARY (2-3 sentences). Never paste
the document inline.

Otherwise (plain markdown): begin with a short ## SUMMARY (2-3 sentences), then
the document body inline.
"""

CREATIVE_OUTPUT_FORMAT = """

--- OUTPUT FORMAT (CREATIVE) ---
Produce the requested creative artifact directly. No required structure
beyond what your specific prompt asks for. Be substantive — empty output
is rejected.
"""

OPERATOR_OUTPUT_FORMAT = """

--- OUTPUT FORMAT (OPERATOR) ---
You are executing a side-effect (API call, file write, system action). Your
response MUST BEGIN with this section, BEFORE any prose:

## RESULT
<one of: SUCCESS | FAILURE | DRY_RUN | AWAITING_APPROVAL>
<one-line summary of what was done or attempted>

## DIFF
<structured before/after, or status payload, or error message>
"""

DELIVERABLE_PROVENANCE_CONTRACT = """

--- AUDIENCE & PROVENANCE (deliverable) ---
This deliverable is written for: {audience}. Write in that audience's language and register.

PROVENANCE — TWO REGISTERS. The deliverable BODY and the machine/audit trail serve
DIFFERENT audiences; keep them separate.

1) IN THE BODY, cite provenance the way {audience} needs in order to trust and verify a
   claim — never in internal system terms:
   - AUTHORITY: name the authoritative source the way {audience} can verify it — the
     standard / regulation / legislation / framework + the specific section or paragraph. To make
     it clickable, DO NOT write a URL yourself — models get long URLs wrong, so the platform owns
     them. Every catalog citation reaches you from the upstream lens as `[catalog: <type>/<id> —
     <url>]`. When the deliverable is HTML, render the authority as a citation-id anchor:
     <a href="tessera-cite://<type>/<id>" target="_blank">Authority name (section)</a> — copy that
     EXACT `<type>/<id>` from the marker; the platform resolves it to the real, verified source URL
     at save time. NEVER put an http(s) URL in the href yourself, and NEVER invent, complete, or
     guess one. If the upstream gave NO catalog citation for an authority, name it in PLAIN TEXT
     with no link. Do not paraphrase an authority into a vague label. DEEP LINK: when you have a
     SHORT VERBATIM phrase (a handful of words copied EXACTLY from the cited source), add it to the
     anchor as data-quote="<those exact words>" — e.g.
     <a href="tessera-cite://<type>/<id>" data-quote="maintain its critical operations">…</a> — the
     platform turns it into a scroll-to-and-highlight deep link to that passage. Use ONLY genuinely
     verbatim source words (never a paraphrase); omit data-quote if you don't have an exact quote.
   - EVIDENCE: the ACTUAL source document relied on — cite it by the name/filename the upstream
     used (e.g. the attached "04-dpia.pdf") and, where it sharpens the point, the short quoted
     line the upstream extracted. PRESERVE the exact reference the upstream gave you; never soften
     a precise citation into a generic name or drop the document it came from. When the deliverable
     format is HTML, render each uploaded evidence document as a clickable link using a
     tessera-doc token with its EXACT filename — <a href="tessera-doc://04-dpia.pdf"
     target="_blank" rel="noopener">04-dpia.pdf</a> — the platform swaps that token at save time
     for a link that opens the actual uploaded document. Use the filename verbatim as it appears
     under "## ATTACHED FILES"; never invent a filename.
   - CONFIDENCE: state how the claim is supported, using these EXACT bases (define them ONCE in a
     short legend, then tag each finding with the matching basis AND its source):
       * grounded — quoted from a NAMED authoritative source: an attached evidence document, or a
         regulator standard / piece of legislation held in the reference catalog (name which one).
       * independently-verified — corroborated by an INDEPENDENT external check (a web-search
         verification of that specific claim); name what corroborated it.
       * management-assertion — asserted in the SUBJECT'S OWN submitted documents; taken at face
         value, NOT independently verified.
       * reasoning-only — the assessment's OWN analytical inference; NO external source. Use this
         ONLY when no catalog record or document supports the claim, and say why in one phrase
         (e.g. "no catalog coverage for accessibility"). Do not use reasoning-only for anything a
         cited standard or document actually supports.
   DO NOT put INTERNAL machine identifiers in the body: no agent ids like `[per <agent_id>]`, no
   `/tessera/catalog/...` memory paths, no catalog record-id slugs. (A document filename and an
   authority's PUBLIC source URL are NOT machine identifiers — cite those; they are exactly what
   {audience} needs.) This SUPERSEDES any earlier direction to cite agents inline — agent
   attribution belongs in the assurance section, not the body.
   Be honest about confidence: if a claim is reasoning-only or a management assertion, label it
   as such rather than presenting it as grounded. Never invent a section/paragraph pinpoint for
   an authority you cannot actually cite.

2) ASSURANCE & PROVENANCE: end the deliverable with a short section titled exactly
   "Assurance & Provenance", in {audience}-appropriate language, stating how the assessment
   was produced (multi-specialist analysis with independent adversarial challenge,
   authoritative-source grounding, and human-review checkpoints) and that the full
   evidentiary trail is retained with the run record. Then place, on its own line, the token:
   [[ASSURANCE_PROVENANCE]]
   The platform replaces that token with the verifiable run details (run id, timestamp,
   contributing specialists, methodology). Do NOT fabricate run ids, timestamps, or grounding
   statistics yourself — emit the token and let the platform fill it.
"""


CATALOG_CITATION_CONTRACT = """

--- CATALOG GROUNDING (authoritative reference sources) ---
You have one or more authoritative REFERENCE CATALOGS mounted for read (your /catalog/...
namespaces). For EVERY standard, regulation, legislation, obligation, control, typology, or
precedent you rely on, you MUST look it up in the catalog (use memory_search for the specific
question, or memory_read) and CITE THE SPECIFIC RECORD together with its source reference/URL —
e.g. `[catalog: regulatory-requirement/cps-230-critical-operations — https://www.legislation.gov.au/...]`.
Do NOT cite a standard or obligation from your own memory when the catalog holds it; ground it
in the record so the citation is verifiable and links to the original source. The record's
canonical link is its **`uri`** field — when present, cite that EXACT value verbatim as the
source URL (the composer renders it as a clickable link). NEVER construct, complete, shorten,
or guess a URL, and never link a bare domain: if a record has no `uri`, cite the authority in
PLAIN TEXT with no link. If the catalog has NO record for something you must rely on, say so
explicitly and mark that claim reasoning-only — never invent a citation, a record id, a URL, or
a section/paragraph number.
"""


# Mapping from output_kind -> (contract_text, append_quality_requirements)
# QUALITY_REQUIREMENTS gets appended for kinds that emit citation-bearing
# claims (analyst, synthesizer). For other kinds, citation rules are
# baked into their kind-specific contract.
OUTPUT_CONTRACTS = {
    "analyst":      (MANDATORY_OUTPUT_FORMAT, True),
    "synthesizer":  (MANDATORY_OUTPUT_FORMAT, True),
    "researcher":   (MANDATORY_OUTPUT_FORMAT, True),
    "challenger":   (CHALLENGER_OUTPUT_FORMAT, False),
    "reporter":     (REPORTER_OUTPUT_FORMAT, False),
    "drafter":      (DRAFTER_OUTPUT_FORMAT, False),
    "creative":     (CREATIVE_OUTPUT_FORMAT, False),
    "chat":         (CHAT_OUTPUT_FORMAT, False),
    "operator":     (OPERATOR_OUTPUT_FORMAT, False),
}


def _resolve_output_kind(agent_config: dict) -> str:
    """Determine the agent's output_kind from the YAML.

    Resolution order:
      1. Explicit `output_kind` field — authoritative
      2. Legacy `chat_mode: true` flag — deprecated alias for "chat"
      3. agent_id-based heuristics for auto-injected platform agents
         (challenger_agent → challenger; report_agent → reporter)
      4. Default: "analyst" (preserves pre-Phase-5e behaviour)
    """
    explicit = agent_config.get("output_kind")
    if explicit and explicit in OUTPUT_CONTRACTS:
        return explicit
    if bool(agent_config.get("chat_mode")):
        return "chat"
    aid = agent_config.get("id", "")
    if aid == "challenger_agent":
        return "challenger"
    if aid == "report_agent":
        return "reporter"
    return "analyst"


# ─── Phase 5e: output_format support ────────────────────────────────────────
#
# Agents can declare output_format: { type: json | yaml | markdown | html,
# file_extension, schema_id?, validate } in YAML. The runtime appends a
# format-specific instruction to the agent prompt (after the kind contract);
# the agent-invoker Lambda's validator then parses + schema-validates the
# output for non-markdown formats.

_FORMAT_EXTENSIONS = {
    "markdown": ".txt",   # current behaviour (output.txt)
    "json":     ".json",
    "yaml":     ".yaml",
    "html":     ".html",
    "csv":      ".csv",
    "pdf":      ".pdf",
    "docx":     ".docx",
    "png":      ".png",
    "bundle":   ".zip",
}


def _resolve_output_format(agent_config: dict) -> dict:
    """Return the agent's output_format with sensible defaults.

    Default: { type: markdown, file_extension: .txt, validate: false }
    matches today's behaviour exactly.
    """
    raw = agent_config.get("output_format") or {}
    if not isinstance(raw, dict):
        raw = {}
    ftype = raw.get("type", "markdown")
    file_ext = raw.get("file_extension") or _FORMAT_EXTENSIONS.get(ftype, ".txt")
    validate = bool(raw.get("validate", ftype in ("json", "yaml", "html", "csv")))
    return {
        "type": ftype,
        "file_extension": file_ext,
        "schema_id": raw.get("schema_id"),
        "validate": validate,
        "save_as": raw.get("save_as"),
    }


def _runtime_format_contract(format_type: str, save_as: str = "") -> str:
    """Returns the prompt suffix telling the agent how to produce its typed artifact.
    For non-markdown types: use code_interpreter to build the file, then save_artifact.
    Empty string for markdown (no extra contract).
    """
    if format_type == "markdown":
        return ""
    filename = save_as or f"output.{format_type}"
    # Binary/structured formats are built as ONE object and written ONCE (repeating
    # output()/appending stacks complete documents → blank/cover-only files). Text
    # formats can be appended section-by-section.
    _BINARY_FORMATS = {"pdf", "docx", "xlsx", "png"}
    _common_top = (
        f"\n\n--- OUTPUT FORMAT REQUIREMENT ({format_type.upper()}) ---\n"
        f"Produce '{filename}' by WRITING IT TO A FILE with code_interpreter — never inline,\n"
        f"no fenced blocks.\n"
        f"\n"
        f"CRITICAL — DO NOT NARRATE, DO NOT STOP EARLY. Narration is the #1 cause of a\n"
        f"half-written file: prose burns the output-token budget you need to write the\n"
        f"artifact, and ending your turn with a write-up makes the run stop before the file\n"
        f"is done.\n"
        f"  • Do NOT explain your plan, restate the brief, or describe the sections/slides —\n"
        f"    not before building, not while building.\n"
        f"  • Do NOT end your turn by writing out a description of the document. Keep issuing\n"
        f"    code_interpreter calls until the WHOLE artifact is written and the file is\n"
        f"    closed/well-formed. A partial file is NOT done.\n"
        f"  • The ONLY prose you may emit is a short ## SUMMARY (2-3 sentences), and ONLY\n"
        f"    AFTER the file is complete.\n"
        f"\n"
    )
    if format_type in _BINARY_FORMATS:
        _how = (
            f"HOW TO BUILD IT ({format_type.upper()} is a BINARY format — build ONE document, write it ONCE):\n"
            f"  1. In your FIRST code_interpreter call, create a SINGLE document object\n"
            f"     (e.g. `pdf = FPDF()` for PDF; one Document for docx). It PERSISTS across\n"
            f"     your code_interpreter calls within this run.\n"
            f"  2. Across as many calls as you need, add ALL pages/sections to that SAME\n"
            f"     object (e.g. `pdf.add_page(); ...`). Do NOT save or call output() yet.\n"
            f"  3. In your FINAL call, write the file EXACTLY ONCE (e.g. `pdf.output('{filename}')`),\n"
            f"     then read it back and confirm it is a SINGLE well-formed document.\n"
            f"  WARNING: NEVER call output()/save more than once, and NEVER open the file in\n"
            f"  append ('a'/'ab') mode. Each {format_type.upper()} write is a COMPLETE document, so\n"
            f"  repeating it CONCATENATES several documents into one file — which viewers render\n"
            f"  as blank/cover-only. A multi-document file is REJECTED.\n"
        )
    else:
        _how = (
            f"HOW TO BUILD IT (incrementally, so no single call hits the output-token limit):\n"
            f"  1. open('{filename}','w') — write only the opening/header.\n"
            f"  2. one open('{filename}','a') call PER section/slide — append it.\n"
            f"  3. a final append with the closing markup so the document is well-formed.\n"
            f"  4. read '{filename}' back and confirm it is complete (ends with the proper\n"
            f"     closing tag) BEFORE finishing.\n"
        )
    base = _common_top + _how + (
        f"The platform retrieves '{filename}' from the sandbox after you finish. A missing\n"
        f"or truncated file is REJECTED.\n"
    )
    if format_type == "html":
        base += (
            "The HTML must be a complete document (doctype, html, head, body). "
            "Use only inline CSS (no external stylesheets / scripts) so the "
            "rendered file is self-contained.\n"
            "VISUALS: where a diagram, chart, icon, timeline, or graphic would strengthen "
            "the content, DRAW IT as rich, bespoke inline <svg> right in that section — do "
            "not merely describe it, link it, or leave a placeholder. Inline SVG (plus CSS) "
            "is how you make the document genuinely compelling rather than plain text. These "
            "graphics are FILE CONTENT written via code_interpreter, NOT narration — so the "
            "no-prose rule does not limit them; invest your effort here. Put a section that "
            "carries a large SVG in its OWN append call so no single write gets too large.\n"
        )
    elif format_type == "json":
        base += "The JSON will be parsed and schema-validated by the platform.\n"
    elif format_type == "yaml":
        base += "The YAML will be parsed and schema-validated by the platform.\n"
    elif format_type == "csv":
        base += (
            "The CSV must have a header row followed by data rows. Comma-separated; "
            "double-quote fields containing commas or quotes.\n"
        )
    elif format_type == "pdf":
        base += (
            "You MUST use the `fpdf` library (pre-installed) to generate a real PDF binary. "
            "Do NOT just write text to a .pdf file — that produces a corrupt file. "
            "Example: `from fpdf import FPDF; pdf = FPDF(); pdf.add_page(); "
            "pdf.set_font('Helvetica', size=12); pdf.cell(text='...'); "
            "pdf.output('{}')`. The output must be a valid PDF binary.\n".format(filename)
        )
    elif format_type == "bundle":
        base += (
            "This is a MULTI-FILE bundle. Produce SEVERAL files (e.g. one per topic/"
            "section) and package them into a single .zip written to the filename above:\n"
            "  import os, zipfile\n"
            "  os.makedirs('bundle', exist_ok=True)\n"
            "  # write each file, e.g. open('bundle/<name>.md','w').write(content)\n"
            "  with zipfile.ZipFile('{}', 'w', zipfile.ZIP_DEFLATED) as z:\n"
            "      for root,_,files in os.walk('bundle'):\n"
            "          for fn in files: z.write(os.path.join(root,fn), arcname=fn)\n"
            "Use sensible per-file names and a logical split (the user asked for a SET of "
            "files, not one big file). The .zip is the deliverable.\n".format(filename)
        )
    return base


def build_agent_from_payload(payload: dict):
    """Build a Strands agent from the AG-UI forwardedProps (our agent_config)."""
    fp = payload.get("forwardedProps", {})
    agent_config = fp.get("agent_config") or payload.get("agent_config", {})
    mission = fp.get("mission") or payload.get("mission", "")
    conversation_context = fp.get("conversation_context") or payload.get("conversation_context", "")
    input_context = fp.get("input_context") or payload.get("input_context", "")
    run_session_id = fp.get("session_id") or payload.get("session_id") or agent_config.get("_ecosystem_id") or "default"
    user_id = fp.get("user_id") or ""
    # spec-run-tuning §4: per-run config (model_profile / model_overrides).
    run_config = fp.get("run_config") or payload.get("run_config") or {}
    # Dry-run: a fast plumbing test — keep the full vertical slice (every agent,
    # checkpoint, gate, memory write, report, artifact) but make each agent do
    # MINIMAL work (tiny valid output, no research). Applied below: a scope
    # directive in the prompt, Haiku for non-reviewers, low max_tokens, and
    # near-zero tool budgets.
    dry_run = bool(fp.get("dry_run", False))

    agent_id = agent_config.get("id", "agent")
    agent_role = agent_config.get("agent_role", "leaf")
    system_prompt = agent_config.get("prompt", "You are a Tessera agent.")
    tool_names = agent_config.get("tools", [])
    memory_config = agent_config.get("memory") or {"writable": [], "readable": []}
    role_contract = agent_config.get("role_contract") or {}
    # Resolved early: a challenger is a REVIEWER even though it carries depends_on
    # (it receives the layer's work to review). Gate role framing on this so the
    # compiler's generic synthesizer role_contract doesn't mis-frame it.
    output_kind = _resolve_output_kind(agent_config)

    # Phase 4.5: prefer compiler-injected role_contract over hardcoded ROLE_FRAMING.
    # role_contract carries: must_cite_upstream (= depends_on), other_agents_in_ecosystem,
    # lane_discipline, citation_format, novel_marker, role (leaf|synthesizer).
    # Falls back to ROLE_FRAMING if role_contract is absent (backward compat with v1
    # SFNs that were compiled before Phase 4.5).
    # Fix: the compiler stamps role on role_contract.role (NOT agent_config.agent_role,
    # which is never set), and first-class kinds (researcher/challenger/reporter) have
    # their own framing. Resolve the effective framing role from both so the ROLE
    # section actually matches what the agent is.
    _framing_role = role_contract.get("role") or agent_role
    if output_kind in ROLE_FRAMING:  # researcher / challenger / reporter (drafter has no framing)
        _framing_role = output_kind
    role_framing = ROLE_FRAMING.get(_framing_role, ROLE_FRAMING["leaf"])

    # Build tools first — we need tool_resolution for the prompt.
    a2ui_sink = []
    tool_budget = agent_config.get("tool_budget") or {}
    if dry_run:
        # Never cap memory (inter-agent handoff). Drafters also need code_interpreter
        # UNCAPPED — it's how they build and zip their deliverable; capping it (was 2)
        # left bundle drafters exhausting the budget mid-build (6/7 files, no .zip),
        # which tripped the format gate. Everything else is zeroed for speed.
        UNCAPPED = {"memory_read", "memory_write"}
        if agent_config.get("output_kind") == "drafter":
            UNCAPPED.add("code_interpreter")
        tool_budget = {t: 0 for t in (tool_names or []) if t not in UNCAPPED}
    tools, _code_session_holder, _artifact_holder, tool_resolution = build_tools(tool_names, a2ui_sink, agent_id=agent_id, memory_config=memory_config, tool_budget=tool_budget, run_session_id=run_session_id, user_id=user_id, ecosystem_id=agent_config.get("_ecosystem_id", ""), mission=mission)

    # Compose the full system prompt: agent-specific persona, role contract, platform tools,
    # quality requirements, mandatory output format.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    full_prompt = system_prompt
    full_prompt += f"\n\n--- CONTEXT ---\nToday is {today} (UTC). Any references to 'now', 'current', 'recent', or specific dates must be consistent with this. Your training data is older — do not rely on it for time-sensitive facts."

    # If compiler-injected role_contract present, render structural sections from it.
    # Otherwise fall back to the legacy ROLE_FRAMING block.
    if output_kind == "challenger":
        # A challenger REVIEWS the layer's work — it carries depends_on only so it
        # RECEIVES that work, not because it should synthesize/cite it. Never inject
        # the synthesizer "must cite upstream / outputs REJECTED" mandate (it frames
        # the model as a producer of cited findings instead of a reviewer). List the
        # agents it reviews for context, then apply the challenger role framing.
        others = (role_contract.get("other_agents_in_ecosystem") if role_contract else None) or []
        if others:
            full_prompt += "\n\n--- AGENTS WHOSE WORK YOU REVIEW ---"
            for o in others:
                full_prompt += f"\n- {o.get('id')}: {o.get('name', o.get('id'))}"
        full_prompt += f"\n\n--- ROLE ---\n{ROLE_FRAMING['challenger']}"
    elif role_contract:
        others = role_contract.get("other_agents_in_ecosystem") or []
        if others:
            full_prompt += "\n\n--- OTHER AGENTS IN THIS ECOSYSTEM ---"
            for o in others:
                full_prompt += f"\n- {o.get('id')}: {o.get('name', o.get('id'))}"
        # ENFORCED ROLE ISOLATION (structural — the fix). The role_contract branch used
        # to drop ROLE_FRAMING entirely, so leaf/researcher agents lost their "stay
        # narrow / you are first" framing. Re-inject it, and for every agent that does
        # NOT author the deliverable, add a hard SCOPE guard naming who does and what
        # this agent must not redo. Deliverable authors (drafter/reporter/artifact
        # owner) are exempt — producing the whole thing IS their job.
        # Backward-compat: only apply the SCOPE guard when the compiler stamped the
        # structural field (new SFNs). Old SFNs lack it → preserve prior behavior.
        _has_scope = "is_deliverable_author" in role_contract
        is_author = (
            role_contract.get("is_deliverable_author", False)
            or output_kind in ("drafter", "reporter")
        )
        if _has_scope and not is_author:
            full_prompt += f"\n\n--- ROLE ---\n{role_framing}"
            _author = role_contract.get("deliverable_author")
            _does_not = role_contract.get("does_not")
            full_prompt += (
                "\n\n--- SCOPE (enforced) ---\n"
                "Produce ONLY your own slice as defined by your prompt above. You are NOT "
                "the deliverable author"
                + (f" — {_author} assembles the final deliverable" if _author else "")
                + ". Do NOT produce the final report/bundle/kit/deck, and do NOT redo other "
                "agents' work"
                + (f" ({_does_not})" if _does_not else "")
                + ". Publish your slice; downstream agents and the deliverable author consume it."
            )
        lane = role_contract.get("lane_discipline")
        if lane:
            full_prompt += f"\n\n--- LANE DISCIPLINE ---\n{lane}"
        deps = role_contract.get("must_cite_upstream") or []
        if deps:
            cite_fmt = role_contract.get("citation_format", "[per <agent_id>: ...]")
            novel = role_contract.get("novel_marker", "[novel — <reason>]")
            threshold = role_contract.get("cite_threshold", 1)
            full_prompt += (
                f"\n\n--- MANDATORY UPSTREAM CITATIONS ---"
                f"\nYou depend on: {', '.join(deps)}."
                f"\nFor EACH upstream agent, your STRUCTURED FINDINGS must include "
                f"at least {threshold} claim(s) that cite it using the format `{cite_fmt}` "
                f"(e.g. `[per {deps[0]}: <their finding>]`). "
                f"Claims with no upstream basis MUST be tagged `{novel}`. "
                f"Outputs missing required citations will be REJECTED by the platform."
            )
    else:
        full_prompt += f"\n\n--- ROLE ---\n{role_framing}"

    full_prompt += "\n\n--- PLATFORM TOOLS ---"
    full_prompt += "\n- generateUI: render rich, structured UI surfaces (Metric, Table, Chart, Gauge, Heatmap, Badge, AlertBanner, CitationCard, plus the A2UI Basic Catalog)."
    full_prompt += "\n- web_search: find current information from the web."
    full_prompt += "\n- memory_write(namespace, content): publish a DURABLE, mission-INVARIANT fact or dossier to the shared catalog so FUTURE runs (this or other ecosystems) can reuse it. Use an entity-keyed path /tessera/catalog/<domain>/<entity> (e.g. /tessera/catalog/properties/<addr>, /tessera/catalog/controls/<id>). Write facts/dossiers about a STABLE ENTITY (company, person, control, property, suburb) — NOT this run's conclusions/verdicts (those belong in your output, not memory). Embed provenance: who wrote it, today's date, and the source; when updating an entity, restate it with a newer date and note what it supersedes."
    full_prompt += "\n- memory_search(namespace, query): RELEVANCE-ranked semantic search of the catalog. PREFER this over memory_read to pull the most on-point facts for a specific question (returns less, more relevant context). Use the most specific entity namespace you can."
    full_prompt += "\n- memory_read(namespace, query): raw recent entries from a namespace (chronological, not ranked). Use memory_search instead when you have a specific question."
    full_prompt += "\n- calculator(expression): evaluate math expressions — arithmetic, algebra, calculus, matrices. Use for any numerical computation."
    full_prompt += "\n- current_time(timezone): get the current date and time. Default timezone is UTC."
    full_prompt += "\n- http_request(url, method, headers, body): make HTTP requests to APIs or URLs. Use for fetching data from REST APIs, public endpoints, etc."
    full_prompt += "\n- code_interpreter(code, language): run Python/JS/TS code in a sandboxed environment. Variables persist across calls. Use for data analysis, complex calculations, or computations that benefit from running real code."
    if memory_config.get("writable") or memory_config.get("readable"):
        full_prompt += "\n  Your authorized namespaces:"
        if memory_config.get("writable"):
            full_prompt += f"\n    write: {memory_config['writable']}"
        if memory_config.get("readable"):
            full_prompt += f"\n    read:  {memory_config['readable']}"
    # Phase 5.6: Custom tools (Gateway-discovered) and unavailable tools
    if tool_resolution.get("discovered"):
        full_prompt += "\n\n--- CUSTOM TOOLS (registered in Gateway) ---"
        for tn in tool_resolution["discovered"]:
            full_prompt += f"\n- {tn}"
    if tool_resolution.get("missing"):
        full_prompt += "\n\n--- REQUESTED BUT UNAVAILABLE ---"
        full_prompt += "\nThese tools were declared in the ecosystem config but are not registered. Use platform tools instead:"
        for tn in tool_resolution["missing"]:
            full_prompt += f"\n- {tn}: not registered — use calculator, code_interpreter, http_request, or web_search to achieve the same goal"
    # Phase 5.1: TOOL BUDGET section — model sees its caps and can self-pace.
    tool_budget = agent_config.get("tool_budget") or {}
    if tool_budget:
        full_prompt += "\n\n--- TOOL BUDGET ---\n"
        full_prompt += "You have these caps per tool for this invocation:"
        for tn, cap in sorted(tool_budget.items()):
            full_prompt += f"\n  - {tn}: {cap} call(s)"
        full_prompt += (
            "\nAfter exhausting a budget, the tool returns a `tool_budget_exhausted` "
            "error. Plan ahead: pick high-value queries first; do not issue redundant "
            "searches with minor query variations. Each tool result also includes a "
            "`_budget` field with `used`, `cap`, and `remaining` so you can self-pace. "
            "When a budget is exhausted, synthesize from the evidence you have rather "
            "than failing — mark any unverified claims as `[novel — budget exhausted]`."
        )
    # Phase 5e: per-kind output contract dispatch. Replaces the universal
    # MANDATORY_OUTPUT_FORMAT + QUALITY_REQUIREMENTS injection. The agent's
    # YAML declares output_kind (default "analyst" preserves pre-5e behaviour);
    # the runtime injects the matching contract. Reporters get prose framing,
    # drafters get artifact framing, challengers get verdict framing, etc.
    # The legacy chat_mode: true flag is a deprecated alias for "chat".
    output_kind = _resolve_output_kind(agent_config)
    contract_text, include_quality_requirements = OUTPUT_CONTRACTS[output_kind]
    if include_quality_requirements:
        full_prompt += QUALITY_REQUIREMENTS
    full_prompt += contract_text

    # Deterministic decision engine (spec-decision-engine.md).
    # (a) SCORER — this agent's config carries a `decision` block (threaded by the compiler):
    #     instruct it to emit STRUCTURED SCORED ITEMS (dimension VALUES + justification +
    #     evidence) and NOT the final label/verdict — the platform computes those from the rubric.
    _decision_cfg = agent_config.get("decision") or {}
    if _decision_cfg.get("dimensions"):
        noun = _decision_cfg.get("item_noun", "item")
        full_prompt += (
            "\n\n--- DECISION SCORING (structured — the platform computes the labels & verdict) ---"
            f"\nAfter your STRUCTURED FINDINGS, add a section titled `## DECISION ITEMS` containing a "
            f"single fenced ```json``` array. Each object is ONE distinct {noun} being CLASSIFIED — the "
            "unit of assessment. ITEMS ARE THE THINGS YOU SCORE, AND NOTHING ELSE. Do NOT create an item "
            "for: (1) the overall verdict / recommendation; (2) a remediation action or CONDITION — the "
            "platform DERIVES conditions automatically from the labelled items, so listing them here "
            "double-counts; (3) an accountability owner / unassigned-owner entry; (4) a caveat, meta, or "
            f"'catalog-gap' note. Emit ONE item per genuine distinct {noun} (usually a handful — not one "
            "per section of your write-up). Each object MUST have: `id` (short stable id), `title` "
            "(one line), `justification` (one line), `evidence` (list of citation ids/filenames), and "
            "`dimensions` (an object). Do NOT emit a severity/label or the overall verdict — score only "
            "the dimensions; the platform assigns labels and computes the outcome deterministically.\n"
            "`dimensions` MUST contain EXACTLY these keys with these allowed values:")
        for name, meta in _decision_cfg["dimensions"].items():
            if meta.get("use") == "verifiability" or (meta.get("use") and "verif" in (meta.get("use") or "")):
                spec = "one of reasoning|assertion|verified|grounded (weakest→strongest)"
            elif meta.get("type") == "ordinal":
                spec = f"one of {'|'.join(meta.get('levels', []))} (ordered low→high)"
            elif meta.get("type") == "boolean":
                spec = "true or false"
            elif meta.get("type") == "category":
                spec = f"one of {'|'.join(meta.get('values', []))}"
            elif meta.get("type") == "number":
                spec = "a number"
            else:
                spec = "a value"
            # Eco-provided semantics for this dimension (config-over-code; the platform stays
            # domain-blind — it renders whatever description the ecosystem declares).
            desc = (meta.get("desc") or "").strip()
            full_prompt += f"\n  - {name}: {spec}" + (f" — {desc}" if desc else "")
        full_prompt += ("\nScore each dimension INDEPENDENTLY and CONSERVATIVELY against its definition; "
                        "do not inflate. Every item must be evidence-grounded; set the confidence/"
                        "verifiability dimension honestly (reasoning = your own inference only).")
    # (b) DELIVERABLE — a reporter/drafter renders the COMPUTED decision if present in its context.
    if output_kind in ("reporter", "drafter"):
        full_prompt += (
            "\n\n--- COMPUTED DECISION (render verbatim) ---"
            "\nIf your context contains a `COMPUTED DECISION` block, render its OUTCOME and per-item "
            "labels EXACTLY as given — they are the authoritative, platform-computed classification. "
            "Do NOT re-derive, relabel, override, or soften them; you may explain and organise them, "
            "and MUST surface any items marked [BORDERLINE] as lower-confidence.")

    # Audience-appropriate provenance + assurance (opt-in via ecosystem.outcome.audience,
    # threaded onto the deliverable agent by the compiler). When set, deliverable kinds
    # (reporter/drafter) render provenance in the audience's language and keep machine
    # attribution out of the body; the [[ASSURANCE_PROVENANCE]] token they emit is stamped
    # deterministically by agent-invoker from the run record. Unset = no change (creative /
    # data ecosystems are unaffected).
    _audience = (agent_config.get("audience") or "").strip()
    _ofmt = ((agent_config.get("output_format") or {}).get("type") or "").lower()
    if _audience and output_kind in ("reporter", "drafter") and _ofmt not in ("png", "jpg", "jpeg", "gif", "svg"):
        full_prompt += DELIVERABLE_PROVENANCE_CONTRACT.format(audience=_audience)

    # Option 1 — deterministic authority citations. Hand the deliverable author the catalog
    # citation IDS harvested from the run's own upstream outputs (the lenses cite the catalog,
    # but intermediate synthesizer/challenger agents strip the markers, so the author never
    # receives an id to link). The author cites by `tessera-cite://<type/id>` — NEVER a URL —
    # and agent-invoker resolves each id to the verified source uri at save time.
    if output_kind in ("reporter", "drafter"):
        _run_cits = _harvest_run_citations(run_session_id)
        if _run_cits:
            full_prompt += (
                "\n\n--- AUTHORITY CITATIONS AVAILABLE (cite by id — NEVER write a URL) ---"
                "\nUpstream analysis grounded these authorities in the catalog. Whenever your "
                "deliverable names one of them, MAKE IT A CLICKABLE LINK by wrapping the authority "
                "name in <a href=\"tessera-cite://<id>\">Authority name (section)</a> using the EXACT "
                "id from the list below. NEVER put an http(s) URL in the href yourself and NEVER "
                "invent one — the platform resolves the id to the real, verified source URL at save "
                "time. If you cite an authority that is NOT in this list, name it in PLAIN TEXT (no "
                "link). You may add data-quote=\"<verbatim words copied exactly from the source>\" to "
                "an anchor to produce a highlighted deep link.\nAvailable citation ids:"
            )
            for _cid in _run_cits:
                full_prompt += f"\n- {_cid}"

    # Entity-keying (M.1/M.6): an analyst that writes a durable /catalog/ namespace
    # must emit a stable entity key so the catalog keeps one dossier PER entity
    # (per address/suburb/scheme) instead of overwriting a single shared record.
    # Only inject for catalog-writing analysts — synthesizers/conclusions never catalog.
    _writable_ns = (memory_config.get("writable") or [])
    _writes_catalog = any("/catalog/" in p for p in _writable_ns if isinstance(p, str))
    if output_kind == "analyst" and _writes_catalog:
        full_prompt += _entity_key_contract(memory_config.get("entity_key_patterns") or {})

    # Catalog grounding: analysts/synthesizers/researchers that READ a reference catalog must
    # cite the specific record + its source URL — so authorities link to the original, and any
    # claim the catalog can't support is honestly marked reasoning-only rather than cited from
    # the model's own memory. Drives verifiable grounding through to the deliverable composer.
    _readable_ns = (memory_config.get("readable") or [])
    _reads_catalog = any("/catalog/" in p for p in _readable_ns if isinstance(p, str))
    if output_kind in ("analyst", "synthesizer", "researcher") and _reads_catalog:
        full_prompt += CATALOG_CITATION_CONTRACT

    # Phase 5e: output_format support. When the agent declares a non-markdown
    # output_format (json/yaml/html/...), append the format-specific prompt
    # suffix telling the model to emit a fenced code block of the right type.
    # Agent-invoker validates after.
    output_format = _resolve_output_format(agent_config)
    full_prompt += _runtime_format_contract(output_format["type"], output_format.get("save_as", ""))

    # Build the user message — conversation FIRST, mission SECOND.
    parts = []
    if conversation_context:
        # When role_contract present, re-emphasise citation in the conversation framing
        # since that's where the agent first encounters upstream content.
        cite_fmt = (role_contract.get("citation_format") if role_contract else "") or "[per agent_id: ...]"
        parts.append(
            "=== CONVERSATION SO FAR ===\n"
            "Other agents have already established the findings below. They are your "
            "PRIMARY EVIDENCE. EXTEND, RECONCILE, or CHALLENGE them — never restate. "
            f"Cite each one by id with `{cite_fmt}` for every claim that derives from them.\n\n"
            + conversation_context
        )
    # Chat-mode agents (post-run follow-up Q&A via the chat Lambda) and
    # creative/drafter agents are invoked without upstream peers and should
    # NOT be told to "synthesize upstream" or to end with STRUCTURED FINDINGS.
    # The per-kind output_kind drives this branching (output_kind: chat is
    # the modern path; chat_mode: true remains as a deprecated alias).
    is_chat = output_kind == "chat"
    if is_chat:
        parts.append(f"=== USER QUESTION ===\n{mission or '(no question provided)'}")
    elif output_kind in ("creative", "drafter", "operator"):
        # These kinds produce artifacts/actions, not analysis. Reframe the
        # mission so the model focuses on producing the deliverable rather
        # than synthesizing upstream findings.
        parts.append(
            f"=== YOUR MISSION ===\n{mission or 'Run your mission.'}\n\n"
            "Use upstream context where it shapes the deliverable. Your primary "
            "output is the artifact/action defined by your prompt and output_format, "
            "not analysis of upstream work."
        )
    elif output_kind == "challenger":
        # A challenger REVIEWS the work in CONVERSATION SO FAR — it does NOT perform
        # the run's goal. Present the mission as the GOAL TO REVIEW AGAINST (context),
        # never as "YOUR MISSION", and explicitly forbid producing the deliverable —
        # otherwise the model just executes the task and never emits a verdict.
        parts.append(
            "=== YOUR JOB: REVIEW (you are NOT performing this run's task) ===\n"
            "The CONVERSATION SO FAR section above IS the work you must review — it is "
            "always present when you run, and it is your primary material.\n\n"
            f"=== THE RUN'S GOAL (context only — do NOT carry it out) ===\n{mission or '(none provided)'}\n\n"
            "=== OPERATOR GUIDANCE IS AUTHORITATIVE — IT OVERRIDES THE GOAL ===\n"
            "If a USER-PROVIDED INPUT / OPERATOR GUIDANCE section appears below, it is the "
            "operator's AUTHORITATIVE adjudication and SUPERSEDES the run goal and the original "
            "request. Any premise, target, trade-off, or concern the operator has explicitly "
            "resolved, relaxed, overridden, or told you to accept there is SETTLED — you MUST "
            "NOT raise it as a weakness or BLOCKER, and you MUST NOT keep re-flagging a "
            "contradiction the operator has already adjudicated (e.g. if the operator has "
            "directed a specific reconciliation of two goals, treat it as reconciled). The "
            "operator has seen your prior critique and decided; respect that decision. Only "
            "flag issues that remain genuinely unaddressed AFTER applying the operator's "
            "guidance. Re-raising an already-resolved premise is itself an error.\n\n"
            "MANDATORY: Emit your sections in THIS ORDER — `## WEAKNESSES` first, then "
            "`## UNRESOLVED CONTRADICTIONS`, then `## VERDICT` LAST. Do the analysis first and "
            "DERIVE the verdict from the severities you logged: any BLOCKER-severity finding "
            "REQUIRES `## VERDICT` = WEAK (no exceptions). The line immediately after the "
            "`## VERDICT` header MUST be exactly one of STRONG / ADEQUATE / WEAK. No preamble, "
            "no greeting, no restatement of the goal. Output missing a valid `## VERDICT` is "
            "REJECTED.\n"
            "You are the reviewer, not a worker: do NOT research the goal or produce files, "
            "specs, dashboards, or any deliverable. Review the upstream findings above AND the "
            "premises they accepted from the goal (e.g. a misidentified tool, standard, "
            "definition, or scope) — EXCEPT premises the operator has already settled per the "
            "guidance above. It is IMPOSSIBLE that there is 'no work to review' — the "
            "work is above; NEVER claim it is missing, NEVER ask for it, NEVER say you are the "
            "'first responder'. If the work is genuinely sound, log 'None' under "
            "`## WEAKNESSES` and set the verdict accordingly."
        )
    else:
        # analyst, synthesizer, researcher, reporter. The deliverable author keeps the
        # full mission as YOUR MISSION; every other agent gets the mission as CONTEXT
        # only, with an explicit slice-only task — so the run goal stops reading as
        # "you personally produce the whole deliverable" (the root cause of role runaway).
        if (role_contract or {}).get("is_deliverable_author", True):
            parts.append(
                f"=== YOUR MISSION ===\n{mission or 'Run your mission.'}\n\n"
                "Use your tools where they add evidence. Synthesize upstream first, research "
                "only to verify or extend. Follow your output contract precisely."
            )
        else:
            parts.append(
                f"=== THE RUN GOAL (context only — you deliver just your slice) ===\n"
                f"{mission or 'Run your mission.'}\n\n"
                "=== YOUR TASK ===\n"
                "Do exactly what your own prompt defines — nothing more. Produce your slice "
                "and publish it for downstream agents; do NOT attempt to produce the full "
                "deliverable. Use your tools where they add evidence; synthesize upstream "
                "first, research only to verify or extend. Follow your output contract precisely."
            )
    if input_context:
        parts.append(f"=== USER-PROVIDED INPUT ===\n{input_context}")
    user_message = "\n\n".join(parts)

    # build_tools already called above (before prompt construction)

    # Model tier: agents with declared upstream (depends_on non-empty) are
    # synthesizers — they must reason over many inputs and get the strongest
    # model. Leaf agents do tool-driven research and run on Sonnet.
    if dry_run:
        full_prompt += (
            "\n\n--- DRY RUN (FAST TEST MODE) ---\n"
            "This is a DRY RUN. Produce the SMALLEST valid output that fully satisfies your "
            "output contract — at most 1-2 findings, a 1-2 sentence rationale, or a 2-3 line "
            "file. Do NOT research, do NOT call tools unless strictly required, do NOT write "
            "long prose. Your output MUST still be correctly FORMATTED (e.g. ## STRUCTURED "
            "FINDINGS, ## VERDICT with STRONG/ADEQUATE/WEAK, or the drafter's file) so "
            "downstream stages and gates work — only the DEPTH is minimized, never the structure."
        )

    is_synthesizer = bool(agent_config.get("depends_on"))
    MODEL_ALIASES = {
        "opus": "us.anthropic.claude-opus-4-7",
        "sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        # non-Claude (benchmarked 2026-07-06 for contract-compliance + latency)
        "nova-lite": "us.amazon.nova-lite-v1:0",
        "nova-pro": "us.amazon.nova-pro-v1:0",
        "llama-70b": "us.meta.llama3-3-70b-instruct-v1:0",
        "llama4-maverick": "us.meta.llama4-maverick-17b-instruct-v1:0",
        "deepseek-r1": "us.deepseek.r1-v1:0",
        "deepseek-v3": "deepseek.v3.2",
    }
    # spec-run-tuning §3/§4 — model TIER + per-run resolution. Tier is stamped by
    # the compiler (leaf/synthesizer/terminal/reviewer/report/drafter). Precedence,
    # lowest applied first → highest wins:
    #   platform default  <  model_profile(tier)  <  YAML agent.model
    #                     <  run_config.by_type[tier]  <  run_config.by_agent[id]
    PROFILE_TIERS = {
        "fast":     {"leaf": "sonnet", "synthesizer": "sonnet", "terminal": "sonnet", "reviewer": "sonnet", "report": "sonnet", "drafter": "sonnet", "researcher": "sonnet"},
        "balanced": {"leaf": "sonnet", "synthesizer": "sonnet", "terminal": "opus",   "reviewer": "sonnet", "report": "opus",   "drafter": "sonnet", "researcher": "sonnet"},
        "thorough": {"leaf": "sonnet", "synthesizer": "opus",   "terminal": "opus",   "reviewer": "opus",   "report": "opus",   "drafter": "sonnet", "researcher": "sonnet"},
        # Non-Claude combo (benchmarked 2026-07-06): fast + contract-compliant.
        # Nova Lite for tool-driven research (~1s), Llama 3.3 70B for synthesis
        # and drafting (cleanest non-Claude HTML), DeepSeek R1 as the challenger
        # (sharpest critique; runs only at gates so its latency barely counts).
        # NOTE: the drafter's code_interpreter loop is the one path to validate
        # live before trusting — swap drafter->sonnet if Llama can't drive it.
        "nonclaude": {"leaf": "nova-lite", "synthesizer": "llama-70b", "terminal": "llama-70b", "reviewer": "deepseek-r1", "report": "llama-70b", "drafter": "llama-70b", "researcher": "nova-lite"},
        # All-DeepSeek-V3.2 (deepseek.v3.2 on-demand): V3.2 executes Bedrock tool
        # calls correctly in both streaming modes (R1 does NOT support tool use at
        # all, so it is deliberately not used here). Experimental — validate the
        # drafter/code_interpreter path end-to-end before trusting.
        "deepseek": {"leaf": "deepseek-v3", "synthesizer": "deepseek-v3", "terminal": "deepseek-v3", "reviewer": "deepseek-v3", "report": "deepseek-v3", "drafter": "deepseek-v3", "researcher": "deepseek-v3"},
    }
    def _alias(m):
        return MODEL_ALIASES.get(m, m) if m else None
    tier = agent_config.get("tier") or ("synthesizer" if is_synthesizer else "leaf")
    # 5. platform default (env-driven). A `researcher` carries depends_on (so
    # is_synthesizer is True) but is leaf-CLASS for model selection — it does
    # tool-driven research, not heavy synthesis — so key the default off tier.
    model_id = LEAF_MODEL_ID if tier in ("leaf", "researcher") else SYNTHESIZER_MODEL_ID
    # 4. model_profile → per-tier default
    _profile = str((run_config or {}).get("model_profile") or "").lower()
    if _profile in PROFILE_TIERS:
        model_id = MODEL_ALIASES.get(PROFILE_TIERS[_profile].get(tier, "sonnet"), model_id)
    # 3. YAML per-agent model override
    if agent_config.get("model"):
        model_id = _alias(agent_config["model"])
    # 2. run_config per-type override
    _overrides = (run_config or {}).get("model_overrides") or {}
    _bt = (_overrides.get("by_type") or {}).get(tier)
    if _bt:
        model_id = _alias(_bt)
    # 1. run_config per-agent override (highest precedence)
    _ba = (_overrides.get("by_agent") or {}).get(agent_config.get("id"))
    if _ba:
        model_id = _alias(_ba)
    # Dry-run clamp: non-reviewers → Sonnet (speed is from no-tools/small output;
    # Sonnet honors the STRUCTURED FINDINGS / VERDICT contracts reliably).
    if dry_run and output_kind != "challenger":
        model_id = MODEL_ALIASES["sonnet"]
    print(f"[model] agent={agent_config.get('id')} tier={tier} profile={_profile or '-'} -> {model_id}")

    # max_tokens budget: both tiers carry rich context (upstream findings for
    # synthesizers, accumulated tool results for tool-heavy leaves) plus produce
    # structured findings + generateUI calls. Strands' BedrockModel default
    # (~4K) is too low for either tier with realistic workloads — running out
    # mid-stream raises MaxTokensReachedException and the run fails before
    # STRUCTURED FINDINGS lands.
    #
    # History: Phase 4.5 set this to 16384. Empirically observed (2026-05-19)
    # that 16384 was insufficient for research-heavy agents that emit a long
    # generateUI table + then need to write STRUCTURED FINDINGS — final turn
    # hits the cap mid-thought ("Let me organize the analysis." → silent
    # truncation → STRUCTURED FINDINGS never written → loud contract failure).
    # Hit this 3x in one day across Stage subject_researcher, political-debate
    # progressive_advocate, BuyerSide building_forensic_analyst. Bumped to
    # 32768 to give 2x headroom while staying within both Sonnet 4.5 (64K
    # max output) and Opus 4.7 (32K max output) limits.
    max_tokens = 32768
    # Drafter agents produce large typed artifacts (full HTML documents, etc.)
    # and routinely exceed 32K. Bump to model max for drafters.
    # max_tokens: drafters produce artifacts via code_interpreter so they don't
    # need huge output budgets, but give them headroom for tool call arguments.
    if agent_config.get("output_kind") == "drafter":
        max_tokens = 64000
    # Dry-run: short outputs generate fast, but 1500 was too low — agents hit
    # max_tokens mid-output (truncating ## STRUCTURED FINDINGS / generateUI),
    # which produced structureless output that tripped output_kind gates. 3000
    # fits the contract sections while staying small/fast. Drafters need more to
    # write a (small) file via code_interpreter.
    if dry_run:
        max_tokens = 6000 if agent_config.get("output_kind") == "drafter" else 3000

    from botocore.config import Config as BotocoreConfig
    import uuid as _uuid
    _usage_key = _uuid.uuid4().hex
    model = _UsageRecordingBedrockModel(
        model_id=model_id,
        region_name=REGION,
        max_tokens=max_tokens,
        boto_client_config=BotocoreConfig(read_timeout=300, connect_timeout=10),
    )
    model._usage_key = _usage_key
    strands_agent = Agent(model=model, system_prompt=full_prompt, tools=tools)

    return strands_agent, user_message, a2ui_sink, agent_id, _code_session_holder, _artifact_holder, model_id, _usage_key


# FastAPI server
app = FastAPI()


@app.post("/invocations")
async def invocations(request: Request):
    """AG-UI endpoint — streams typed events as SSE."""
    # Capture the workload access token from the request header and set it on the
    # AgentCore Identity context so @requires_api_key (used by web_search) can fetch
    # the web search API key from the Token Vault for this request.
    workload_token = request.headers.get(WORKLOAD_ACCESS_TOKEN_HEADER)
    if workload_token:
        try:
            from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
            BedrockAgentCoreContext.set_workload_access_token(workload_token)
        except Exception as e:
            print(f"WARN: could not set workload access token on context: {e}")

    payload = await request.json()
    accept_header = request.headers.get("accept")
    encoder = EventEncoder(accept=accept_header)

    # Build agent from forwardedProps
    strands_agent, user_message, a2ui_sink, agent_id, code_session_holder, artifact_holder, model_id, usage_key = build_agent_from_payload(payload)

    # Phase 5e: pre-load template into code_interpreter sandbox if template_id is declared
    agent_config = (payload.get("forwardedProps") or payload).get("agent_config") or {}
    output_fmt = agent_config.get("output_format") or {}
    template_id = output_fmt.get("template_id", "")
    if template_id:
        try:
            import boto3 as _boto3
            _s3 = _boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            _bucket = os.environ.get("TESSERA_ECOSYSTEMS_BUCKET", "")
            if _bucket:
                _tpl_key = f"templates/{template_id}/template.html"
                _tpl_obj = _s3.get_object(Bucket=_bucket, Key=_tpl_key)
                _tpl_content = _tpl_obj["Body"].read().decode("utf-8")
                # Start code_interpreter session eagerly and write template
                from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
                ci = CodeInterpreter(os.environ.get("AWS_REGION", "us-east-1"))
                ci.start()
                code_session_holder["session"] = ci
                code_session_holder["session_id"] = ci.session_id
                ci.execute_code(code=f"with open('template.html', 'w') as f: f.write({repr(_tpl_content)})", language="python")
        except Exception as e:
            print(f"[template] Failed to pre-load template '{template_id}': {e}")

    # If a RAW AG-UI client supplied a messages array, use its last user message
    # as input. BUT when the Tessera pipeline (agent-invoker) or the chat Lambda
    # drives us, forwardedProps.agent_config is present and the user_message we
    # built above already embeds mission + conversation_context (upstream findings)
    # + input_context (operator guidance, files) + per-kind framing. Overwriting it
    # with the bare placeholder message would strip ALL of that — which blinds
    # tool-less reviewers/checkpoints (they have no memory_read fallback) and drops
    # operator guidance/file context for every agent. So only honor an external
    # messages array when there is NO agent_config (a genuine raw AG-UI client).
    _fp_msgs = payload.get("forwardedProps") or {}
    messages = payload.get("messages", [])
    if messages and not _fp_msgs.get("agent_config"):
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        if last_user and last_user.get("content"):
            user_message = last_user["content"]

    # Wrap with AG-UI protocol support — emits standard AG-UI events
    agui_agent = StrandsAgent(
        agent=strands_agent,
        name=agent_id,
        description=f"Tessera agent: {agent_id}",
    )

    async def event_generator():
        run_input = RunAgentInput(
            thread_id=payload.get("threadId", "default-thread"),
            run_id=payload.get("runId", "default-run"),
            messages=[{"id": "input-msg", "role": "user", "content": user_message}],
            tools=payload.get("tools", []),
            context=payload.get("context", []),
            state=payload.get("state", {}),
            forwarded_props=payload.get("forwardedProps", {}),
        )

        # Stream the underlying agent's AG-UI events
        async for event in agui_agent.run(run_input):
            yield encoder.encode(event)

        # spec-run-tuning §5.1: emit per-agent token usage so agent-invoker can
        # persist usage.json (the run manifest reads it for cost/telemetry).
        # Best-effort + fully defensive — must NEVER break the stream. Strands
        # accumulates usage on the agent's event_loop_metrics; shapes vary across
        # versions, so probe both dict- and attr-style.
        # spec-run-tuning §5.1: emit per-agent token usage so agent-invoker can
        # persist usage.json (the run manifest reads it for cost/telemetry).
        # Defensive in two layers: (1) reading Strands metrics may fail/vary by
        # version — default to zeros; (2) ALWAYS emit the /usage event (with the
        # resolved model + tool_calls) even if metrics parsing failed, so the
        # manifest at least gets the model. Never breaks the stream.
        # Tokens are captured at the MODEL layer (see _UsageRecordingBedrockModel):
        # the ag-ui-strands wrapper leaves agent.event_loop_metrics at 0, so we read
        # the per-invocation accumulator instead. tool_calls falls back to
        # len(tool_results) in agent-invoker when the metrics path yields 0.
        _u = _usage_pop(usage_key)
        usage = {"tokens_in": _u.get("in", 0), "tokens_out": _u.get("out", 0), "tool_calls": 0, "model": model_id}
        try:
            m = getattr(strands_agent, "event_loop_metrics", None)
            tm = getattr(m, "tool_metrics", None) if m is not None else None
            if tm is not None:
                vals = tm.values() if hasattr(tm, "values") else tm
                usage["tool_calls"] = sum(int((t.get("call_count") if isinstance(t, dict) else getattr(t, "call_count", 0)) or 0) for t in vals)
        except Exception as _ue:
            print(f"[usage] tool metrics read failed (non-fatal): {_ue}")
        try:
            yield encoder.encode(StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[{"op": "add", "path": "/usage", "value": usage}],
            ))
        except Exception as _ue2:
            print(f"[usage] emit failed (non-fatal): {_ue2}")

        # After the agent finishes, emit any pending A2UI surfaces as STATE_DELTA events
        for surface in a2ui_sink:
            delta_event = StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[{"op": "add", "path": "/a2ui/surfaces/-", "value": surface}],
            )
            yield encoder.encode(delta_event)

        # Emit saved artifacts so agent-invoker can write them to S3
        for art in artifact_holder:
            delta_event = StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[{"op": "add", "path": "/artifacts/-", "value": art}],
            )
            yield encoder.encode(delta_event)

        # Emit any human-sign-in requests raised by the browser tool. The
        # agent-invoker turns each into a `browser_login` Attention item so an
        # operator can seed the site's auth profile (see docs/features/browser-tool.md).
        for _lr in (code_session_holder.get("login_sink") or []):
            yield encoder.encode(StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[{"op": "add", "path": "/browser_login/-", "value": _lr}],
            ))

        # Auto-retrieve declared artifact from code_interpreter sandbox
        # If the agent has output_format.save_as and a code_interpreter session
        # was used, try to read that file from the sandbox and stage it.
        agent_config = (payload.get("forwardedProps") or payload).get("agent_config") or {}
        output_fmt = agent_config.get("output_format") or {}
        save_as = output_fmt.get("save_as", "")
        if save_as and code_session_holder.get("session") and not artifact_holder:
            try:
                ci = code_session_holder["session"]
                fmt_type = output_fmt.get("type", "")
                is_binary = fmt_type in ("pdf", "docx", "png", "jpg", "jpeg", "zip", "bundle")
                if is_binary:
                    read_code = f"import base64; print(base64.b64encode(open('{save_as}', 'rb').read()).decode(), end='')"
                else:
                    read_code = f"with open('{save_as}', 'r') as _f: print(_f.read(), end='')"
                result = ci.execute_code(code=read_code, language="python")
                content = ""
                if isinstance(result, dict):
                    stream_val = result.get("stream", "")
                    # Drain if it's an iterable (generator/EventStream)
                    if hasattr(stream_val, "__iter__") and not isinstance(stream_val, (str, bytes, dict, list)):
                        drained = []
                        for item in stream_val:
                            if isinstance(item, dict):
                                # This is the actual result dict from the stream
                                stdout = (item.get("result", {})
                                          .get("structuredContent", {})
                                          .get("stdout", ""))
                                if stdout:
                                    content = stdout
                                    break
                                drained.append(json.dumps(item))
                            else:
                                drained.append(str(item))
                        if not content:
                            stream_val = "".join(drained)
                    if not content:
                        if isinstance(stream_val, dict):
                            content = (stream_val.get("result", {})
                                       .get("structuredContent", {})
                                       .get("stdout", ""))
                        elif isinstance(stream_val, str) and stream_val.strip():
                            try:
                                parsed = json.loads(stream_val)
                                content = (parsed.get("result", {})
                                           .get("structuredContent", {})
                                           .get("stdout", ""))
                            except json.JSONDecodeError:
                                content = stream_val
                if content:
                    artifact_holder.append({"filename": save_as, "content": content, "binary": is_binary})
                    delta_event = StateDeltaEvent(
                        type=EventType.STATE_DELTA,
                        delta=[{"op": "add", "path": "/artifacts/-", "value": {"filename": save_as, "content": content, "binary": is_binary}}],
                    )
                    yield encoder.encode(delta_event)
            except Exception as e:
                print(f"[artifact-retrieve] Failed to read '{save_as}' from sandbox: {e}")
                pass  # Best-effort; if file doesn't exist, no artifact

        # Cleanup: stop any code_interpreter session opened during this invocation
        if code_session_holder.get("session") is not None:
            try:
                code_session_holder["session"].stop()
            except Exception as e:
                print(f"WARN: code_interpreter session cleanup failed: {e}")

        # Cleanup: stop the browser sandbox session (worker thread) if one was opened.
        _bw = code_session_holder.get("browser_worker")
        if _bw is not None:
            try:
                _bw.shutdown()
            except Exception as e:
                print(f"WARN: browser sandbox cleanup failed: {e}")

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "Healthy"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container service, must bind all interfaces
