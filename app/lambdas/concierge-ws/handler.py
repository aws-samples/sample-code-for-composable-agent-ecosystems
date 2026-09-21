"""Concierge over WebSocket — a fast, streaming, tool-using front-door agent.

One streaming agent (Bedrock converse_stream) grounded in a compact capability index
(the self-model digest) + the ratified two-direction intent. It answers directly for
discovery/Q&A, and uses TOOLS for deeper knowledge (past runs, cost, time, artifacts).
When it finds what the user asked for, it emits a NAVIGATION action the frontend
executes (open the past-run view) — "bring up John Doe's advice → it opens in the app".

SECURITY (hard requirements):
- The WS message is authenticated per-message: a signature-verified Cognito ID token;
  identity = the verified `sub` claim ONLY (never client-supplied). 401 otherwise.
- Every run tool is AUTHORIZED: it only ever returns runs whose ecosystem is owned by
  the caller's sub. No cross-user data (runs hold PII, e.g. a client's finances).

Route: WS `concierge`  ·  body: {token, message, history?}
Streams back: CONCIERGE_DELTA {text} · CONCIERGE_TOOL {name} · CONCIERGE_ACTION {action,...}
              · CONCIERGE_DONE · CONCIERGE_ERROR {error}
Env: TESSERA_MEMORY_ID, CATALOG_REFRESH_TABLE, ECOSYSTEMS_BUCKET, TESSERA_RUNS_BUCKET,
     COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, CONCIERGE_MODEL_ID
"""
import os, re, json, time
import boto3, yaml
from botocore.config import Config as BotoConfig

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL = os.environ.get("CONCIERGE_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MEM = os.environ.get("TESSERA_MEMORY_ID", "")
ECOS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
os.environ.setdefault("CATALOG_REFRESH_TABLE", os.environ.get("CATALOG_REFRESH_TABLE", "tessera-dev-catalog-refresh"))

_bed = boto3.client(
    "bedrock-runtime", region_name=REGION,
    # Long presentations (10 min) mean a big pre-fill after preflight tool results and a
    # large streamed plan; botocore's default 60s read timeout trips mid-generation.
    config=BotoConfig(read_timeout=280, connect_timeout=10, retries={"max_attempts": 1}),
)
_ac = boto3.client("bedrock-agentcore", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)
_sfn = boto3.client("stepfunctions", region_name=REGION)
_sts = boto3.client("sts", region_name=REGION)
_ACCT = None
# Verified Permissions (Cedar) — the SAME authorization model list-ecosystems/runs-api use.
POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID", "")
_avp = boto3.client("verifiedpermissions", region_name=REGION) if POLICY_STORE_ID else None
# Per-invocation caller context (Lambda serves one event per container at a time, so this is
# safe): lets _authorized see the verified sub + groups without threading through every tool.
_CUR_USER = {"sub": "", "groups": []}

# ─────────────────────────── auth ───────────────────────────
_JWK = None
def _verify_jwt(token):
    """Verify a Cognito ID token SIGNATURE (JWKS) + issuer/aud/expiry. Returns claims or None.
    Signature verification is REQUIRED — decoding claims unverified lets a caller forge identity."""
    pool = os.environ.get("COGNITO_USER_POOL_ID", ""); client = os.environ.get("COGNITO_CLIENT_ID", "")
    if not token or not pool:
        return None
    global _JWK
    try:
        import jwt
        from jwt import PyJWKClient
        if _JWK is None:
            _JWK = PyJWKClient(f"https://cognito-idp.{REGION}.amazonaws.com/{pool}/.well-known/jwks.json")
        key = _JWK.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=client if client else None,
                          issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{pool}",
                          options={"verify_aud": bool(client)})
    except Exception as e:
        print("jwt verify failed:", type(e).__name__, e)
        return None

# ─────────────────────────── streaming back to client ───────────────────────────
def _mgmt(event):
    rc = event.get("requestContext", {})
    dom = rc.get("domainName"); stg = rc.get("stage")
    ep = os.environ.get("WS_ENDPOINT") or (f"https://{dom}/{stg}" if dom and stg else None)
    return boto3.client("apigatewaymanagementapi", region_name=REGION, endpoint_url=ep)

def _pusher(mgmt, connection_id):
    def push(obj):
        try:
            mgmt.post_to_connection(ConnectionId=connection_id, Data=json.dumps(obj).encode())
        except Exception as e:
            print("push failed:", type(e).__name__, e)
    return push

# ─────────────────────────── capability index (self-model digest) ───────────────────────────
_INDEX = None
def _active_version():
    from tessera_platform.catalog import store as refresh
    return refresh.get_active_version("tessera-self")

def _list_type(version, etype, limit=300):
    out, tok = [], None
    while len(out) < limit:
        kw = {"memoryId": MEM, "namespacePath": f"/tessera/catalog/{version}/{etype}", "maxResults": 100}
        if tok: kw["nextToken"] = tok
        r = _ac.list_memory_records(**kw)
        for x in r.get("memoryRecordSummaries") or []:
            try: out.append(json.loads((x.get("content") or {}).get("text", "{}") or "{}"))
            except Exception: pass
        tok = r.get("nextToken")
        if not tok: break
    return out

def capability_index():
    """Compact digest the agent reasons over inline — one line per eco + intent."""
    global _INDEX
    if _INDEX:
        return _INDEX
    v = _active_version()
    ecos = _list_type(v, "ecosystem")
    goals = _list_type(v, "goal"); nongoals = _list_type(v, "non-goal"); vp = _list_type(v, "value-prop")
    lines = ["# TESSERA CAPABILITY INDEX (self-model)"]
    if vp: lines.append("VALUE: " + (vp[0].get("promise", "")[:400]))
    lines.append("GOALS:")
    for g in goals: lines.append(f"  - {g.get('statement','')[:200]}")
    lines.append("NON-GOALS (refuse only these):")
    for n in nongoals: lines.append(f"  - {n.get('statement','')[:200]}")
    lines.append("ECOSYSTEMS (id | alignment | purpose | inputs | deliverable | maturity):")
    for e in sorted(ecos, key=lambda x: x.get("eco_id", "")):
        al = e.get("alignment"); al = al.get("classification") if isinstance(al, dict) else al
        purpose = (e.get("purpose") or e.get("problem_solved") or "")
        purpose = " ".join(str(purpose).split())[:160]
        inp = e.get("inputs_accepted") or e.get("inputs") or ""
        deliv = e.get("deliverable") or ""
        lines.append(f"  - {e.get('eco_id')} | {al} | {purpose} | in:{str(inp)[:60]} | out:{str(deliv)[:50]} | {e.get('maturity')}")
    _INDEX = "\n".join(lines)
    return _INDEX

# ─────────────────────────── authz ───────────────────────────
def _retry(fn, tries=3, base=0.2):
    """Small exponential-backoff retry for transient AWS errors (throttling, blips).
    Re-raises the last error if all attempts fail — callers convert that into a TYPED
    failure, never a silent empty result."""
    import time
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(base * (2 ** i))
    raise last

def _eco_meta(eco_id):
    """Return {'owner':str, 'platform':bool} for an eco, or {} on read failure (logged —
    a transient miss must not masquerade as 'no such eco' / false 'not authorized')."""
    try:
        body = _retry(lambda: _s3.get_object(Bucket=ECOS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read())
    except Exception as e:
        print(f"[eco_meta] read failed eco={eco_id}: {type(e).__name__}: {str(e)[:160]}")
        return {}
    try:
        d = yaml.safe_load(body) or {}
        return {
            "owner": d.get("owner") or (d.get("ecosystem") or {}).get("owner") or "",
            "platform": bool(d.get("platform") or (d.get("ecosystem") or {}).get("platform")),
        }
    except Exception:
        return {}


def _avp_view_allowed(eco_id, user_sub, groups, platform):
    """Verified Permissions 'view' check — the SAME model list-ecosystems uses, so an eco
    SHARED with the caller (directly or via a group policy) is usable by the concierge too."""
    if not _avp or not POLICY_STORE_ID or not user_sub:
        return False
    try:
        resp = _avp.is_authorized(
            policyStoreId=POLICY_STORE_ID,
            principal={"entityType": "Tessera::User", "entityId": user_sub},
            action={"actionType": "Tessera::Action", "actionId": "view"},
            resource={"entityType": "Tessera::Ecosystem", "entityId": eco_id},
            entities={"entityList": [
                {"identifier": {"entityType": "Tessera::User", "entityId": user_sub},
                 "parents": [{"entityType": "Tessera::Group", "entityId": g} for g in (groups or [])]},
                {"identifier": {"entityType": "Tessera::Ecosystem", "entityId": eco_id},
                 "attributes": {"platform": {"boolean": bool(platform)}}},
            ]})
        return resp.get("decision") == "ALLOW"
    except Exception as e:
        print(f"[authz] AVP is_authorized failed eco={eco_id}: {type(e).__name__}: {str(e)[:160]}")
        return False


def _authorized(eco_id, user_sub):
    """The caller may touch a run's ecosystem if they OWN it, it's a PLATFORM eco, they are an
    ADMIN, or it is SHARED with them (AVP) — matching list-ecosystems / runs-api visibility.
    Deny-by-default otherwise (runs may hold PII)."""
    if not user_sub:
        return False
    meta = _eco_meta(eco_id)
    if not meta:
        return False
    if meta.get("owner") == user_sub:
        return True
    if meta.get("platform"):
        return True
    groups = _CUR_USER.get("groups", []) if _CUR_USER.get("sub") == user_sub else []
    if "admin" in groups:
        return True
    return _avp_view_allowed(eco_id, user_sub, groups, meta.get("platform", False))

# ─────────────────────────── tools (read; all authz-scoped) ───────────────────────────
def _sm_arn(eco_id):
    global _ACCT
    if not _ACCT:
        _ACCT = _sts.get_caller_identity()["Account"]
    return f"arn:aws:states:{REGION}:{_ACCT}:stateMachine:tessera-dev-{eco_id}"

def tool_search_runs(args, user_sub):
    """Find past runs of an ecosystem the caller owns, optionally filtered by text in the
    mission/inputs (e.g. a client name). Returns run summaries with status/time."""
    eco_id = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
    q = str(args.get("query", "")).lower().strip()
    if not eco_id:
        return {"error": "eco_id required (resolve it from the capability index first)"}
    if not _authorized(eco_id, user_sub):
        print(f"[search_runs] not authorized eco={eco_id} sub={str(user_sub)[:12]}")
        return {"error": f"not authorized: you do not own '{eco_id}' (or it does not exist)", "runs": []}
    # Retry ListExecutions. Transient Step Functions throttling used to be swallowed into an
    # empty list, which the model then read as "no runs exist" — the intermittent, invisible bug.
    try:
        execs = _retry(lambda: _sfn.list_executions(
            stateMachineArn=_sm_arn(eco_id), maxResults=25).get("executions", []))
    except Exception as e:
        print(f"[search_runs] list_executions FAILED eco={eco_id}: {type(e).__name__}: {str(e)[:160]}")
        # TYPED failure — distinct from an empty result so the caller flags/retries instead of
        # wrongly concluding there are no runs.
        return {"eco_id": eco_id, "lookup_failed": True,
                "error": (f"run lookup failed transiently ({type(e).__name__}) — retry; "
                          "do NOT conclude 'no runs exist'"), "runs": []}
    qtokens = [t for t in re.split(r"\s+", q) if t]
    out = []
    for ex in execs:
        run = {"run_id": ex["name"], "status": ex["status"],
               "started": ex.get("startDate").isoformat() if ex.get("startDate") else None,
               "duration_s": (int((ex["stopDate"] - ex["startDate"]).total_seconds())
                              if ex.get("stopDate") and ex.get("startDate") else None)}
        # Only fetch mission text when there is a query to match against. The old code called
        # DescribeExecution for EVERY run (up to 25 per call) — a burst that, multiplied across
        # a multi-ecosystem pitch preflight, tripped Step Functions API throttling. With no
        # query (the presentation path) we don't need it at all.
        if qtokens:
            mission = ""
            try:
                d = _sfn.describe_execution(executionArn=ex["executionArn"])
                inp = json.loads(d.get("input", "{}") or "{}")
                mission = (inp.get("mission_display") or inp.get("mission") or "")
            except Exception as ie:
                print(f"[search_runs] describe skipped {ex['name']}: {type(ie).__name__}")
            run["mission"] = " ".join(str(mission).split())[:240]
            if not (all(t in run["mission"].lower() for t in qtokens) or q in eco_id.lower()):
                continue
        out.append(run)
        if len(out) >= 10:
            break
    print(f"[search_runs] eco={eco_id} q='{q[:40]}' execs={len(execs)} returned={len(out)}")
    return {"eco_id": eco_id, "runs": out}

def _read_runs_json(key):
    if not RUNS_BUCKET or not key:
        return {}
    try:
        return json.loads(_s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read() or b"{}")
    except Exception:
        return {}

def tool_get_run(args, user_sub):
    """Detail of one run the user owns — status, timing, and COST (from the run manifest:
    cost_estimate_usd, wall-clock, tokens)."""
    eco_id = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
    run_id = re.sub(r"[^A-Za-z0-9._-]", "", str(args.get("run_id", "")))
    if not eco_id or not run_id:
        return {"error": "eco_id and run_id required"}
    if not _authorized(eco_id, user_sub):
        return {"error": "not authorized"}
    out = {"eco_id": eco_id, "run_id": run_id}
    try:
        d = _sfn.describe_execution(executionArn=f"{_sm_arn(eco_id)}:{run_id}".replace(":stateMachine:", ":execution:"))
        out["status"] = d.get("status")
        out["started"] = d.get("startDate").isoformat() if d.get("startDate") else None
    except Exception as e:
        return {"error": f"{type(e).__name__}"}
    # Cost/telemetry from the cached run manifest (spec-run-tuning §5): runs/<run_id>/_run_manifest.json
    mt = (_read_runs_json(f"runs/{run_id}/_run_manifest.json").get("metrics") or {})
    if mt:
        tot = mt.get("totals") or {}
        out.update({"cost_usd": mt.get("cost_estimate_usd"), "cost_is_partial": mt.get("cost_is_partial"),
                    "duration_s": mt.get("wall_clock_s"),
                    "tokens_in": tot.get("tokens_in"), "tokens_out": tot.get("tokens_out")})
    else:
        out["cost_note"] = "cost manifest not yet computed for this run"
    return out

def tool_self_model(args, user_sub):
    """Deeper self-model lookup (evolution history, gaps, components, invariants)."""
    topic = re.sub(r"[^a-z0-9-]", "", str(args.get("type", "")).lower())
    if topic not in ("evolution-event", "gap", "component", "invariant", "roadmap-item", "feature-spec"):
        return {"error": "type must be one of: evolution-event, gap, component, invariant, roadmap-item, feature-spec"}
    recs = _list_type(_active_version(), topic, limit=60)
    return {"type": topic, "count": len(recs), "items": [{k: r.get(k) for k in list(r)[:6]} for r in recs[:25]]}

def _fetch_s3_uri(uri):
    if not uri or not str(uri).startswith("s3://"):
        return ""
    b, _, k = uri[5:].partition("/")
    try:
        return _s3.get_object(Bucket=b, Key=k)["Body"].read().decode("utf-8", "replace")
    except Exception:
        return ""

def tool_read_run_output(args, user_sub):
    """Read a run's ACTUAL outcome — reconstructs the final/report agent's output text from
    the execution history + S3 (authz-scoped). Use this to summarize or answer questions
    about what a run produced."""
    eco_id = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
    run_id = re.sub(r"[^A-Za-z0-9._-]", "", str(args.get("run_id", "")))
    if not eco_id or not run_id:
        return {"error": "eco_id and run_id required"}
    if not _authorized(eco_id, user_sub):
        return {"error": "not authorized"}
    arn = f"{_sm_arn(eco_id)}:{run_id}".replace(":stateMachine:", ":execution:")
    outputs, tok = {}, None
    try:
        while True:
            kw = {"executionArn": arn, "maxResults": 500}
            if tok:
                kw["nextToken"] = tok
            h = _sfn.get_execution_history(**kw)
            for ev in h.get("events", []):
                if ev.get("type") != "TaskSucceeded":
                    continue
                try:
                    d = json.loads(ev["taskSucceededEventDetails"]["output"])
                except Exception:
                    continue
                p = d.get("Payload", d) if isinstance(d, dict) else d
                if isinstance(p, dict) and p.get("output_s3_uri") and p.get("agent_id"):
                    outputs[p["agent_id"]] = p["output_s3_uri"]
            tok = h.get("nextToken")
            if not tok:
                break
    except Exception as e:
        return {"error": f"{type(e).__name__}"}
    if not outputs:
        return {"error": "no agent outputs found for this run"}
    # Prefer the final deliverable agent (report/composer/responder/…); else any.
    def rank(aid):
        a = aid.lower()
        return 0 if any(k in a for k in ("report", "composer", "responder", "drafter", "synth", "final", "brief", "pack", "advice")) else 1
    final = sorted(outputs, key=lambda a: (rank(a), a))[0]
    # Also surface the run's HTML DELIVERABLE (the drafter's polished artifact) so the
    # presenter can open the REAL outcome and spotlight sections copied from IT (not markdown).
    deliverable_key, deliverable_text = _resolve_deliverable_key(run_id), None
    if deliverable_key:
        try:
            raw = _s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{run_id}/{deliverable_key}")["Body"].read().decode("utf-8", "replace")
            txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)  # drop script/style
            txt = re.sub(r"<[^>]+>", " ", txt)                              # strip tags
            deliverable_text = re.sub(r"\s+", " ", txt).strip()[:12000]
        except Exception:
            pass
    return {"eco_id": eco_id, "run_id": run_id, "agents": list(outputs),
            "final_agent": final, "output": _fetch_s3_uri(outputs[final])[:20000],
            "deliverable_artifact_key": deliverable_key, "deliverable_text": deliverable_text}


def _resolve_deliverable_key(run_id):
    """Server-authoritative resolution of a run's polished HTML deliverable key,
    RELATIVE to runs/<run_id>/ (i.e. '<agent>/<file>.html') — the biggest .html
    under the run prefix. Returns None if none found. Used so spotlight_outcome
    never trusts a model-supplied (often hallucinated) artifact_key."""
    if not run_id:
        return None
    try:
        listing = _s3.list_objects_v2(Bucket=RUNS_BUCKET, Prefix=f"runs/{run_id}/").get("Contents", [])
        htmls = [o for o in listing if str(o.get("Key", "")).lower().endswith(".html")]
        if not htmls:
            return None
        htmls.sort(key=lambda o: o.get("Size", 0), reverse=True)  # biggest = the deliverable
        return htmls[0]["Key"][len(f"runs/{run_id}/"):]
    except Exception:
        return None

# open_run / navigate are CLIENT-ACTION tools: their effect is a UI navigation event.
def _latest_successful_run(eco_id):
    """Most-recent SUCCEEDED run name for an ecosystem, or '' — used to make
    presentation beats deterministic when the model omits or invents a run_id."""
    try:
        execs = _retry(lambda: _sfn.list_executions(stateMachineArn=_sm_arn(eco_id),
                       statusFilter="SUCCEEDED", maxResults=5).get("executions", []))
        return execs[0]["name"] if execs else ""
    except Exception as e:
        print(f"[latest_run] failed eco={eco_id}: {type(e).__name__}")
        return ""

TOOLS = {"search_runs": tool_search_runs, "get_run": tool_get_run,
         "self_model_lookup": tool_self_model, "read_run_output": tool_read_run_output}
TOOLSPEC = [
    {"toolSpec": {"name": "search_runs", "description": "Find past runs of an ecosystem the user owns, filtered by text in the mission/inputs (e.g. a client name like 'John Doe'). Resolve the eco_id from the capability index first.",
      "inputSchema": {"json": {"type": "object", "properties": {"eco_id": {"type": "string"}, "query": {"type": "string"}}, "required": ["eco_id"]}}}},
    {"toolSpec": {"name": "get_run", "description": "Detail of one run the user owns — status, timing, and COST (cost_usd, duration_s, tokens). Use for 'how much did it cost / how long did it take'.",
      "inputSchema": {"json": {"type": "object", "properties": {"eco_id": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["eco_id", "run_id"]}}}},
    {"toolSpec": {"name": "read_run_output", "description": "Read a run's ACTUAL outcome — the final/report agent's output text, PLUS the run's polished HTML deliverable (deliverable_artifact_key + deliverable_text). Use this to summarize a run AND to pick spotlight targets copied verbatim from the real deliverable.",
      "inputSchema": {"json": {"type": "object", "properties": {"eco_id": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["eco_id", "run_id"]}}}},
    {"toolSpec": {"name": "self_model_lookup", "description": "Deeper self-model: evolution-event, gap, component, invariant, roadmap-item, feature-spec.",
      "inputSchema": {"json": {"type": "object", "properties": {"type": {"type": "string"}}, "required": ["type"]}}}},
    {"toolSpec": {"name": "open_run", "description": "OPEN a past run's result view in the app for the user (navigation). Call this once you've found the run they asked for.",
      "inputSchema": {"json": {"type": "object", "properties": {"eco_id": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["eco_id", "run_id"]}}}},
    {"toolSpec": {"name": "navigate", "description": "Navigate the app to a view: ecosystems | catalog | runs. Optionally an id.",
      "inputSchema": {"json": {"type": "object", "properties": {"view": {"type": "string"}, "id": {"type": "string"}}, "required": ["view"]}}}},
    {"toolSpec": {"name": "replay_run", "description": (
        "Open the CINEMATIC REPLAY of a past run — a full-screen player that re-enacts, in order: how the "
        "ecosystem was built (builder conversation), how the mission was shaped (mission conversation), the DAG "
        "executing agent-by-agent with checkpoints, and the final deliverable. Use whenever the user asks to "
        "'replay' / 're-run as a story' / 'show how this run happened'. Provide eco_id; run_id is OPTIONAL — omit "
        "it to replay the most recent successful run of that ecosystem."),
      "inputSchema": {"json": {"type": "object", "properties": {
        "eco_id": {"type": "string"}, "run_id": {"type": "string", "description": "optional — omit for the latest successful run"}},
        "required": ["eco_id"]}}}},
    {"toolSpec": {"name": "spotlight_outcome", "description": (
        "Open a run's REAL HTML deliverable in-app and SPOTLIGHT one or more sections in it — the same spotlight the "
        "presenter uses, but for direct Q&A. Use whenever the user asks to open/show/spotlight sections, findings, or "
        "'things' in a run's OUTCOME/deliverable. Call read_run_output FIRST, then call this with its "
        "deliverable_artifact_key and 1-4 sections whose 'target' is a SHORT VERBATIM snippet copied EXACTLY from "
        "deliverable_text (a unique 4-10 word phrase or a figure/code)."),
      "inputSchema": {"json": {"type": "object", "properties": {
        "eco_id": {"type": "string"}, "run_id": {"type": "string"},
        "artifact_key": {"type": "string", "description": "deliverable_artifact_key from read_run_output."},
        "sections": {"type": "array", "description": "1-4 sections to spotlight, in order.", "items": {"type": "object", "properties": {
            "target": {"type": "string", "description": "SHORT verbatim distinctive snippet copied from deliverable_text."},
            "note": {"type": "string", "description": "the callout annotation for this section."}},
          "required": ["target"]}}},
        "required": ["eco_id", "run_id", "sections"]}}}},
    {"toolSpec": {"name": "deliver_presentation", "description": (
        "Deliver a LIVE presentation to the frontend player. Call this ONCE, after gathering real assets, with the "
        "full beat plan. The player narrates each beat and executes its actions live (driving the real app + overlay)."),
      "inputSchema": {"json": {"type": "object", "properties": {
        "title": {"type": "string"}, "audience": {"type": "string"}, "minutes": {"type": "number"}, "focus": {"type": "string"},
        "beats": {"type": "array", "description": "Ordered beats.", "items": {"type": "object", "properties": {
            "narration": {"type": "string", "description": "What the presenter SAYS this beat (spoken script — engaging, conversational)."},
            "headline": {"type": "string", "description": "Short punchy on-screen slide TITLE (<= 7 words). Shown big; do NOT just repeat the narration."},
            "bullets": {"type": "array", "items": {"type": "string"}, "description": "0-3 very short on-screen key points (<= 7 words each)."},
            "stats": {"type": "array", "description": "0-3 headline figures shown as big chips (REAL numbers from the runs, e.g. cost/time/score/counts).", "items": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}}, "required": ["value"]}},
            "duration_s": {"type": "number", "description": "approx narration_words / 2.3 (~140 wpm)."},
            "actions": {"type": "array", "description": "Live actions during this beat.", "items": {"type": "object", "properties": {
                "action": {"type": "string", "description": "navigate|open_catalog|filter_grid|open_run|highlight|clear_highlight|start_run|self_model|open_builder|open_mission|show_artifact|replay_run"},
                "view": {"type": "string"}, "domain": {"type": "string"}, "query": {"type": "string"},
                "eco_id": {"type": "string"}, "run_id": {"type": "string"},
                "artifact_key": {"type": "string", "description": "show_artifact: the deliverable's artifact key (deliverable_artifact_key from read_run_output). Opens the REAL HTML deliverable in-app."},
                "target": {"type": "string", "description": "highlight: a SHORT, VERBATIM, highly DISTINCTIVE snippet copied EXACTLY from the opened content — a unique 4-10 word phrase or a specific figure/code (e.g. 'CPS 234 \u00b637' or '15-day-vs-72-hour'). NEVER a long sentence."},
                "note": {"type": "string", "description": "highlight: the callout annotation."},
                "mission": {"type": "string", "description": "start_run: the mission to launch."}},
              "required": ["action"]}}},
          "required": ["narration", "duration_s"]}}},
        "required": ["title", "beats"]}}}},
]

SYS = (
    "You are the Tessera Concierge — the fast front door. You help users discover what Tessera can do and "
    "navigate to their work. Ground EVERYTHING in the CAPABILITY INDEX below; never invent an ecosystem, "
    "capability, or run. Be concise and direct.\n"
    "Behaviour:\n"
    "- Discovery/Q&A ('what can you do about X', 'do you have Y'): answer directly from the index — match ecosystems "
    "(with alignment + maturity), be honest about gaps ('no eco for that; closest is Y'), and REFUSE only true non-goals.\n"
    "- 'Bring up / open / show me <the result of> X for <someone>': resolve the ecosystem from the index, call "
    "search_runs(eco_id, query=<name/topic>), then call open_run(eco_id, run_id) to OPEN it in the app. Tell the user "
    "what you found (status, when) as you open it.\n"
    "  Disambiguation: several ecosystems may fit a phrase (e.g. 'financial advice' → iress-adviser-copilot, which "
    "prepares a Statement-of-Advice pack for a NAMED CLIENT from their documents, vs personal-finance-advisor, the "
    "user's OWN finances). Pick the MOST likely by the index purpose; if search_runs returns no match, TRY THE NEXT "
    "best-matching ecosystem before concluding nothing exists. Use only the query's distinctive tokens (a surname), not full titles.\n"
    "- Deeper questions (history/gaps): use self_model_lookup. COST or DURATION of a run ('how much did it "
    "cost', 'how long') -> call get_run(eco_id, run_id) which returns cost_usd/duration_s/tokens; use the run from "
    "CURRENT CONTEXT or recent conversation, else search_runs first. Do NOT claim cost isn't available — it is, via get_run.\n"
    "- Summarize / 'what did it produce' / questions ABOUT a run's outcome: call read_run_output(eco_id, run_id) "
    "to get the actual deliverable text, then answer/summarize from it. read_run_output NEEDS the exact run_id — use "
    "the one from CURRENT CONTEXT if present, else the one from the recent conversation, else call search_runs FIRST "
    "to resolve it. Do NOT claim you can't read run content, and do NOT say it's unavailable unless read_run_output "
    "actually returned an error — you CAN read it via read_run_output.\n"
    "- OPEN / SHOW / SPOTLIGHT sections or things IN a run's OUTCOME/deliverable ('open the advice pack and spotlight "
    "the concentration risk', 'show me the CPS 234 breach in that dossier'): call read_run_output(eco_id, run_id) "
    "FIRST, then call spotlight_outcome(eco_id, run_id, artifact_key=<deliverable_artifact_key>, sections=[{target, "
    "note}, …]) with each target copied VERBATIM from deliverable_text. This opens the REAL HTML deliverable in-app and "
    "spotlights each section — the same spotlight the presenter uses. Do this instead of just summarizing when the user "
    "wants to SEE it.\n"
    "You can ONLY see runs the user owns; if search_runs returns not-authorized or empty, say so honestly — never guess.\n"
    "If a tool result has lookup_failed:true, that is a TRANSIENT error (e.g. throttling), NOT an empty history — "
    "retry the same search once or twice; never conclude 'no runs exist' or silently pivot away from a lookup_failed result.\n"
    "\n"
    "PRESENTATION MODE — when asked to 'present Tessera' (in X minutes, to an audience, with a focus):\n"
    "You are a world-class live presenter. This must be ENGAGING, nuanced, and exciting — SHOW, don't tell — never "
    "just open things and move on. VARY EVERY PRESENTATION: the arc, the headlines, the wording, and WHICH capabilities "
    "you spotlight must be freshly tailored to THIS audience, focus, and time budget — never reproduce a fixed template, "
    "a canned running order, or the same headlines/phrases across talks. Process:\n"
    "1) GATHER REAL ASSETS FIRST (before planning): call search_runs on the most relevant STRATEGIC ecosystems from the "
    "index (e.g. ai-governance-conformity, iress-adviser-copilot, model-risk-continuous-review, risk-assessment) to find "
    "IMPRESSIVE real runs to showcase, and read_run_output on the best one to find a STRIKING, specific finding to spotlight. "
    "Only ever reference ecosystems/runs that actually exist (preflight — never promise a demo you can't deliver).\n"
    "2) Then call deliver_presentation ONCE with the full beat plan. Craft it like a great talk:\n"
    "   - A hook opening; a clear narrative arc tailored to the AUDIENCE and FOCUS (a CISO hears governance/provenance/"
    "non-goals; an exec hears value + real cost/time; an engineer hears architecture + re-grounding).\n"
    "   - SHOW-DON'T-TELL via live actions: navigate the ecosystems grid (filter_grid to the focus), open_catalog + "
    "self_model to flaunt the versioned catalogs and self-awareness, and — the money shot — open the REAL HTML DELIVERABLE "
    "the pipeline produced via show_artifact (use deliverable_artifact_key from read_run_output), then highlight a striking "
    "section INSIDE that polished deliverable with a sharp callout. Spotlight the ACTUAL outcome document, never markdown. "
    "(You may also open_run for the completed run's telemetry/cost.) For a pitch, ALWAYS show COMPLETED runs — open the "
    "finished run and its real HTML deliverable; do NOT use start_run to launch a new run live (an empty run mid-pitch "
    "reads as broken). Every run-bound action must point at a real, SUCCEEDED run.\n"
    "   - Show Tessera's CONVERSATIONAL nature (a signature differentiator) when it fits the audience/focus: a beat using "
    "open_builder (the IDEA: you create a whole new multi-agent capability just by describing it — no code) and one using "
    "open_mission on a real eco_id (the IDEA: you don't fill forms, you converse — it interviews you, reads your files, then "
    "launches). Convey these IDEAS in your OWN fresh words EACH TIME — never reuse canned lines verbatim.\n"
    "   - At least one 'only Tessera can do this' beat: the live self-model, ground-or-flag honesty, the challenger, or "
    "re-grounding. Make it land.\n"
    "   - Stay ON MESSAGE and never confuse: ONE idea per beat, smooth transitions, no jargon dumps.\n"
    "   - EVERY beat IS A SLIDE the audience reads (narration is NOT shown on screen — it is only the spoken script). So "
    "every beat MUST have a punchy 'headline' (<=7 words) AND 2-3 SHORT 'bullets' (<=6 words each) that carry the message "
    "on their OWN, plus 'stats' (real figures — cost, minutes, agent counts, scores, gap/finding counts) where relevant. "
    "Keep the slide skimmable: NO full sentences, NO paragraphs — headline + tight bullets + big numbers.\n"
    "   - TIME-BOX: total narration words \u2248 minutes\u00d7140; set each beat's duration_s \u2248 its narration words \u00f7 2.3.\n"
    "   - highlight.target must be a SHORT, VERBATIM, highly DISTINCTIVE snippet you copied EXACTLY from deliverable_text "
    "(the real HTML deliverable you open with show_artifact) — a unique 4-10 word phrase or a specific figure/code — NOT a "
    "long sentence — so the player reliably finds and spotlights it INSIDE the opened deliverable. Put the punchy explanation in 'note'.\n"
    "Do NOT narrate the plan in chat — put everything inside deliver_presentation; the player performs it.\n"
)

# ─────────────────────────── agent loop ───────────────────────────
def _run_agent(user_sub, message, history, push, ctx=None):
    idx = capability_index()
    msgs = []
    for h in (history or [])[-6:]:
        role = "user" if h.get("role") == "user" else "assistant"
        msgs.append({"role": role, "content": [{"text": str(h.get("content", ""))[:4000]}]})
    msgs.append({"role": "user", "content": [{"text": message}]})
    system = [{"text": SYS}, {"text": idx[:120000]}]
    # Current UI context — what the user is looking at right now (e.g. an open run).
    # Lets "summarize that run / the outcome" resolve deterministically.
    op = (ctx or {}).get("open_run") if isinstance(ctx, dict) else None
    if isinstance(op, dict) and op.get("run_id"):
        system.append({"text": f"CURRENT CONTEXT: the user is viewing run '{op.get('run_id')}' of ecosystem "
                               f"'{op.get('eco_id')}'. If they say 'this run', 'that run', or 'the outcome', it refers "
                               f"to THIS run — call read_run_output with exactly this eco_id and run_id."})

    for _turn in range(10):  # bounded tool-use loop (room for heavy preflight on long pitches)
        resp = _bed.converse_stream(modelId=MODEL, system=system, messages=msgs,
                                    toolConfig={"tools": TOOLSPEC},
                                    inferenceConfig={"maxTokens": 8000, "temperature": 0.4})
        assistant, tool_uses = [], []
        cur_text, cur_tool = "", None
        for ev in resp["stream"]:
            if "contentBlockStart" in ev:
                start = ev["contentBlockStart"]["start"]
                if "toolUse" in start:
                    cur_tool = {"toolUseId": start["toolUse"]["toolUseId"], "name": start["toolUse"]["name"], "input": ""}
            elif "contentBlockDelta" in ev:
                d = ev["contentBlockDelta"]["delta"]
                if "text" in d:
                    cur_text += d["text"]; push({"type": "CONCIERGE_DELTA", "text": d["text"]})
                elif "toolUse" in d and cur_tool is not None:
                    cur_tool["input"] += d["toolUse"].get("input", "")
            elif "contentBlockStop" in ev:
                if cur_tool is not None:
                    try: cur_tool["args"] = json.loads(cur_tool["input"] or "{}")
                    except Exception: cur_tool["args"] = {}
                    tool_uses.append(cur_tool); cur_tool = None
                elif cur_text:
                    assistant.append({"text": cur_text}); cur_text = ""
            elif "messageStop" in ev:
                stop = ev["messageStop"].get("stopReason")
        # assemble assistant message
        content = list(assistant)
        for t in tool_uses:
            content.append({"toolUse": {"toolUseId": t["toolUseId"], "name": t["name"], "input": t["args"]}})
        if not tool_uses:
            push({"type": "CONCIERGE_DONE"})
            return
        msgs.append({"role": "assistant", "content": content})
        # execute tools
        results = []
        for t in tool_uses:
            name, args = t["name"], t["args"]
            push({"type": "CONCIERGE_TOOL", "name": name})
            if name in ("open_run", "navigate", "deliver_presentation", "spotlight_outcome", "replay_run"):
                # client-action: authorize open_run to the caller's own eco, then emit nav
                if name == "open_run":
                    eco = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
                    if not _authorized(eco, user_sub):
                        res = {"error": "not authorized to open this run"}
                    else:
                        push({"type": "CONCIERGE_ACTION", "action": "open_run",
                              "eco_id": eco, "run_id": str(args.get("run_id", ""))})
                        res = {"opened": True}
                elif name == "replay_run":
                    eco = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
                    if not _authorized(eco, user_sub):
                        res = {"error": "not authorized"}
                    else:
                        push({"type": "CONCIERGE_ACTION", "action": "replay_run",
                              "eco_id": eco, "run_id": str(args.get("run_id", ""))})
                        res = {"replaying": True}
                elif name == "navigate":
                    push({"type": "CONCIERGE_ACTION", "action": "navigate",
                          "view": str(args.get("view", "")), "id": str(args.get("id", ""))})
                    res = {"navigated": True}
                elif name == "spotlight_outcome":
                    eco = re.sub(r"[^a-z0-9._-]", "", str(args.get("eco_id", "")).lower())
                    if not _authorized(eco, user_sub):
                        res = {"error": "not authorized"}
                    else:
                        secs = [{"target": str(s.get("target", "")), "note": str(s.get("note", ""))}
                                for s in (args.get("sections") or [])
                                if isinstance(s, dict) and s.get("target")][:6]
                        run_id = str(args.get("run_id", ""))
                        # AUTHORITATIVE: re-resolve the deliverable key server-side from the
                        # run's S3 prefix — the model routinely hallucinates artifact_key (e.g.
                        # a name+timestamp slug), which 404s in the viewer. Fall back to the
                        # model's value only if nothing resolves.
                        art_key = _resolve_deliverable_key(run_id) or str(args.get("artifact_key", ""))
                        push({"type": "CONCIERGE_ACTION", "action": "spotlight_outcome",
                              "eco_id": eco, "run_id": run_id,
                              "artifact_key": art_key, "sections": secs})
                        res = {"spotlighting": len(secs), "artifact_key": art_key}
                else:  # deliver_presentation — hand the whole beat plan to the frontend player
                    beats = args.get("beats") or []
                    # Make the LIVE actions deterministic. The model routinely (a) supplies a
                    # blank/hallucinated artifact_key so the deliverable 404s ("doesn't open the
                    # outcome"), (b) points at a run_id that doesn't exist, or (c) fires start_run —
                    # a fresh EMPTY run — instead of opening the completed run ("empty run instead
                    # of history"). Resolve/validate every run-bound action here against real,
                    # SUCCEEDED runs + the real deliverable key, and NEVER launch a run mid-pitch.
                    _latest = {}
                    def _pick_run(eco, rid):
                        rid = str(rid or "").strip()
                        if rid:
                            return rid
                        if eco not in _latest:
                            _latest[eco] = _latest_successful_run(eco)
                        return _latest[eco]
                    for b in (beats if isinstance(beats, list) else []):
                        cleaned = []
                        for a in (b.get("actions") or []):
                            if not isinstance(a, dict):
                                continue
                            act = a.get("action")
                            eco = re.sub(r"[^a-z0-9._-]", "", str(a.get("eco_id", "")).lower())
                            if act == "start_run":
                                # Never launch a new (empty) run during a pitch — show the
                                # COMPLETED run's history/outcome instead.
                                rid = _pick_run(eco, a.get("run_id")) if (eco and _authorized(eco, user_sub)) else ""
                                if not rid:
                                    continue
                                a = {"action": "open_run", "eco_id": eco, "run_id": rid}
                            elif act in ("open_run", "show_artifact", "replay_run"):
                                if not (eco and _authorized(eco, user_sub)):
                                    continue
                                rid = _pick_run(eco, a.get("run_id"))
                                if not rid:
                                    continue
                                a["eco_id"], a["run_id"] = eco, rid
                                if act == "show_artifact":
                                    # authoritative deliverable key (biggest .html), never the model's guess
                                    a["artifact_key"] = _resolve_deliverable_key(rid) or str(a.get("artifact_key", ""))
                            cleaned.append(a)
                        b["actions"] = cleaned
                    push({"type": "CONCIERGE_PLAN", "title": args.get("title", ""),
                          "audience": args.get("audience", ""), "minutes": args.get("minutes"),
                          "focus": args.get("focus", ""), "beats": beats})
                    res = {"presenting": True, "beats": len(beats)}
            else:
                res = TOOLS[name](args, user_sub)
            results.append({"toolResult": {"toolUseId": t["toolUseId"],
                                           "content": [{"text": json.dumps(res)[:8000]}]}})
        msgs.append({"role": "user", "content": results})
    push({"type": "CONCIERGE_DONE"})

# ─────────────────────────── entrypoint ───────────────────────────
def handler(event, context=None):
    connection_id = event.get("requestContext", {}).get("connectionId")
    mgmt = _mgmt(event)
    push = _pusher(mgmt, connection_id)
    try:
        body = json.loads(event.get("body", "{}") or "{}")
    except Exception:
        body = {}
    claims = _verify_jwt(body.get("token", ""))
    if not claims or not claims.get("sub"):
        push({"type": "CONCIERGE_ERROR", "error": "unauthorized"})
        return {"statusCode": 401}
    user_sub = claims["sub"]
    # Capture verified identity for authorization (owner/admin/AVP). Groups drive the admin
    # bypass + group-shared AVP policies; identity is the verified sub only (never client-supplied).
    _grp = claims.get("cognito:groups") or []
    _CUR_USER["sub"] = user_sub
    _CUR_USER["groups"] = ([x.strip() for x in _grp.split(",") if x.strip()] if isinstance(_grp, str)
                           else [str(x) for x in _grp] if isinstance(_grp, (list, tuple)) else [])
    message = str(body.get("message", ""))[:8000]
    if not message.strip():
        push({"type": "CONCIERGE_ERROR", "error": "empty message"}); return {"statusCode": 400}
    try:
        _run_agent(user_sub, message, body.get("history") or [], push, body.get("context"))
    except Exception as e:
        import traceback; traceback.print_exc()
        push({"type": "CONCIERGE_ERROR", "error": type(e).__name__})
    return {"statusCode": 200}
