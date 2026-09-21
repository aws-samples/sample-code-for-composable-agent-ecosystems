"""Chat Lambda — post-run follow-up Q&A against a Tessera ecosystem run.

Services: Amazon Bedrock, AWS Step Functions, Amazon S3.

Route: POST /api/runs/{sessionId}/chat
Body:  {"question": "..."}

Pipeline:
  1. Resolve ecosystem from sessionId (format: <ecosystem-id>-<8hex>).
  2. Load ecosystem YAML from ECOSYSTEMS_BUCKET to get the agent list.
  3. Walk SFN execution history to identify which agents ran successfully and
     fetch each one's output from RUNS_BUCKET (same flow as runs-api).
  4. Use Bedrock (Sonnet) to route the question to 1-3 agent IDs based on
     their declared persona prompts. Top-N picked from the LLM.
  5. For each routed agent, invoke AgentCore Runtime with:
       - agent_config = the agent's YAML config (so persona + tools survive)
       - mission       = the user's question
       - dependency_context = the agent's PRIOR run output, scoped to ~3KB
       - input_context = brief framing
     Stream the SSE response, accumulate TEXT_MESSAGE_CONTENT deltas into
     a single text answer.
  6. Return JSON: {"responses": [{"agent_id", "agent_name", "response"}, ...]}

This mirrors the local orchestrator's /api/runs/{id}/chat endpoint
(tessera_platform/orchestrator/server.py) — the deployed CDK stack never
had this, so chat in production was silently 404-ing.
"""
import json
import os
import re
import uuid
from datetime import datetime

import boto3
import yaml
from botocore.config import Config as _BotoConfig

REGION = os.environ.get("AWS_REGION", "us-east-1")
PROJECT = os.environ.get("TESSERA_PROJECT", "tessera")
ENVIRONMENT = os.environ.get("TESSERA_ENVIRONMENT", "dev")
ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
AGENT_RUNTIME_ARN = os.environ.get("TESSERA_AGENT_RUNTIME_ARN", "")
ROUTING_MODEL_ID = os.environ.get(
    "TESSERA_CHAT_ROUTING_MODEL",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# Amazon Bedrock AgentCore Runtime streams SSE; need a longer read timeout. 90s is enough for
# a follow-up question with a small mission and small dependency_context — much
# faster than a full agent invocation in a run since there are no tool loops.
_AGENTCORE_CFG = _BotoConfig(
    connect_timeout=10,
    read_timeout=90,
    retries={"max_attempts": 1},
)

# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
# AWS Step Functions client
sfn = boto3.client("stepfunctions", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

# AgentCore Memory — durable chat history, keyed per (user, RUN) so each report's
# Ask thread is isolated: no bleed between different reports, and the agent context
# stays bounded to the one run. actorId = "{user}-chat" (user isolation), sessionId =
# the run session id (run isolation). Cross-run / solution-level conversation is the
# concierge's job (a separate endpoint), not this per-report thread. The transcript is
# stored as raw conversational events (verbatim recall, not semantic extraction).
MEMORY_ID = os.environ.get("TESSERA_MEMORY_ID", "")
_agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def _user_id(event) -> str:
    """Cognito sub from the API Gateway authorizer — the user-isolation key."""
    try:
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        return claims.get("sub") or claims.get("cognito:username") or "anon"
    except Exception:
        return "anon"


def _safe_key(s: str, n: int = 64) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", s or "")[:n] or "default"


def _chat_actor(user_id: str) -> str:
    return f"{_safe_key(user_id)}-chat"


def _load_chat_history(user_id: str, scope_id: str, limit: int = 40) -> list:
    """Return the (user, run) chat transcript oldest-first: [{role, content, agent_id, ts}].
    `scope_id` is the RUN session id — each report/run has its own isolated thread. The
    concierge (separate endpoint) is the cross-run/solution-level assistant."""
    if not MEMORY_ID:
        return []
    try:
        resp = _agentcore.list_events(
            memoryId=MEMORY_ID, actorId=_chat_actor(user_id), sessionId=_safe_key(scope_id),
            maxResults=min(limit, 100), includePayloads=True,
        )
    except Exception as e:
        print(f"[chat-memory] list_events error: {e}")
        return []
    turns = []
    for ev in resp.get("events", []) or []:
        md = ev.get("metadata", {}) or {}
        aid = md.get("agent_id", {}).get("stringValue", "") if isinstance(md.get("agent_id"), dict) else ""
        for block in ev.get("payload", []) or []:
            conv = block.get("conversational") or {}
            text = (conv.get("content") or {}).get("text", "")
            if not text:
                continue
            role = "user" if (conv.get("role") or "").upper() == "USER" else "assistant"
            turns.append({"role": role, "content": text, "agent_id": aid, "ts": str(ev.get("eventTimestamp", ""))})
    turns.sort(key=lambda t: t["ts"])  # oldest-first
    return turns


def _append_chat_event(user_id: str, scope_id: str, role: str, text: str, agent_id: str = "") -> None:
    if not MEMORY_ID or not text:
        return
    from datetime import timezone as _tz
    try:
        _agentcore.create_event(
            memoryId=MEMORY_ID, actorId=_chat_actor(user_id), sessionId=_safe_key(scope_id),
            eventTimestamp=datetime.now(_tz.utc),
            payload=[{"conversational": {"role": "USER" if role == "user" else "ASSISTANT", "content": {"text": text[:8000]}}}],
            metadata={"agent_id": {"stringValue": agent_id or ""}},
        )
    except Exception as e:
        print(f"[chat-memory] create_event error: {e}")


def _clear_chat_history(user_id: str, scope_id: str) -> int:
    """Delete all chat events for this (user, run). Returns count cleared."""
    if not MEMORY_ID:
        return 0
    actor, session, cleared = _chat_actor(user_id), _safe_key(scope_id), 0
    try:
        resp = _agentcore.list_events(memoryId=MEMORY_ID, actorId=actor, sessionId=session, maxResults=100, includePayloads=False)
        for ev in resp.get("events", []) or []:
            eid = ev.get("eventId")
            if not eid:
                continue
            try:
                _agentcore.delete_event(memoryId=MEMORY_ID, actorId=actor, sessionId=session, eventId=eid)
                cleared += 1
            except Exception as e:
                print(f"[chat-memory] delete_event error: {e}")
    except Exception as e:
        print(f"[chat-memory] clear list error: {e}")
    return cleared
agentcore = boto3.client("bedrock-agentcore", region_name=REGION, config=_AGENTCORE_CFG)


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(body, default=str),
    }


def _answer_question(session_id, ecosystem_id, question, target_agent, user_id):
    """Core Q&A: route the question to a run agent, invoke it, persist the turn, and
    return the `responses` list. Raises on hard errors (missing ecosystem/agents).
    Shared by the REST handler and the async-WebSocket path so both behave identically."""
    if not ecosystem_id:
        raise ValueError(f"could not infer ecosystem from session_id {session_id!r}")

    # 1. Load ecosystem config (agent personas)
    agents_cfg = _load_ecosystem_agents(ecosystem_id)
    if not agents_cfg:
        raise ValueError(f"ecosystem {ecosystem_id!r} not found or has no agents")

    # 2. Per-agent prior output from this run (grounding) + the (user, RUN)
    #    conversation history (memory, scoped to THIS report).
    run_context = _load_run_outputs(ecosystem_id, session_id)
    history = _load_chat_history(user_id, session_id)

    # 3. Route: sticky to the agent handling the thread; hand off when the
    #    question is clearly another agent's domain; explicit target overrides.
    aid, handoff_from = _route_chat(question, agents_cfg, history, target_agent)
    agent_config = next((a for a in agents_cfg if a["id"] == aid), None)
    if not agent_config:
        raise ValueError(f"router picked unknown agent {aid!r}")
    agent_output = run_context.get(aid, "")
    print(f"chat: session={session_id} eco={ecosystem_id} user={user_id[:8]} agent={aid} handoff_from={handoff_from!r} hist_turns={len(history)} target={target_agent!r}")

    # 4. Invoke the chosen agent with the conversation history (memory) + run output
    try:
        text = _invoke_agent(
            agent_config=agent_config, question=question, prior_output=agent_output,
            session_id=session_id, history=history, handoff_from=handoff_from,
            memory_acl=_agent_memory_acl(ecosystem_id, aid), user_id=user_id,
        )
    except Exception as e:
        text = f"(agent {aid!r} failed to respond: {type(e).__name__}: {str(e)[:200]})"

    # 5. Persist the turn to durable AgentCore Memory (user Q + agent A), per RUN
    _append_chat_event(user_id, session_id, "user", question)
    _append_chat_event(user_id, session_id, "assistant", text, agent_id=aid)

    return [{
        "agent_id": aid,
        "agent_name": agent_config.get("name", aid),
        "response": text,
        "handoff_from": handoff_from,
    }]


def _push_ws(ws_endpoint, connection_id, data):
    """Push a JSON event to one WebSocket connection. Best-effort."""
    try:
        apigw = boto3.client("apigatewaymanagementapi", endpoint_url=ws_endpoint, region_name=REGION)
        apigw.post_to_connection(ConnectionId=connection_id, Data=json.dumps(data, default=str).encode())
    except Exception as e:
        print(f"[chat] ws push failed: {type(e).__name__}: {str(e)[:200]}")


def _handle_async_ws(event):
    """ASYNC mode: invoked (InvocationType=Event) by the WebSocket handler's `chat` route.
    Runs the same Q&A as the REST path but pushes the result back over the WebSocket, so
    the answer is NOT bound by API Gateway's 29s REST integration timeout (report-generation
    questions can take a minute+). Pushes ASK_RESPONSE on success, ASK_ERROR on failure."""
    connection_id = event.get("connection_id", "")
    ws_endpoint = event.get("ws_endpoint", "")
    session_id = event.get("session_id", "")
    question = (event.get("question") or "").strip()
    target_agent = (event.get("target_agent") or "").strip()
    user_id = event.get("user_id", "")
    ecosystem_id = _ecosystem_from_session(session_id)
    try:
        responses = _answer_question(session_id, ecosystem_id, question, target_agent, user_id)
        _push_ws(ws_endpoint, connection_id, {"type": "ASK_RESPONSE", "session_id": session_id, "responses": responses})
    except Exception as e:
        import traceback
        traceback.print_exc()
        _push_ws(ws_endpoint, connection_id, {"type": "ASK_ERROR", "session_id": session_id, "error": f"{type(e).__name__}: {str(e)[:300]}"})
    return {"statusCode": 200}


def handler(event, context):
    # Async WebSocket dispatch: the WS `chat` route Event-invokes this Lambda with a
    # connection_id + ws_endpoint (no API Gateway pathParameters). Detect + branch first.
    if event.get("async_ws") and event.get("connection_id"):
        return _handle_async_ws(event)

    path_params = event.get("pathParameters") or {}
    session_id = path_params.get("sessionId") or path_params.get("execName") or ""
    if not session_id:
        return _resp(400, {"error": "missing sessionId in path"})

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _resp(400, {"error": "invalid JSON body"})
    action = (body.get("action") or "chat").strip()
    question = (body.get("question") or "").strip()
    target_agent = (body.get("target_agent") or "").strip()
    user_id = _user_id(event)
    ecosystem_id = _ecosystem_from_session(session_id)

    # Durable chat history is keyed per (user, RUN) — each report has its own thread.
    # Cross-run/solution-level questions are handled by the concierge (separate endpoint).
    if action == "load":
        agents = [{"id": a.get("id"), "name": a.get("name", a.get("id"))}
                  for a in _load_ecosystem_agents(ecosystem_id) if a.get("id")]
        return _resp(200, {"history": _load_chat_history(user_id, session_id), "agents": agents})
    if action == "clear":
        return _resp(200, {"cleared": _clear_chat_history(user_id, session_id)})

    if not question:
        return _resp(400, {"error": "question required"})

    # NOTE: this synchronous REST path is bounded by API Gateway's 29s integration timeout.
    # Long questions (e.g. "generate the HTML report") exceed it and the browser sees a
    # failed fetch. The frontend therefore prefers the async WebSocket path (WS `chat`
    # route → this Lambda's _handle_async_ws) for the actual question; this REST path is
    # kept for `load`/`clear` and as a fallback.
    try:
        responses = _answer_question(session_id, ecosystem_id, question, target_agent, user_id)
        return _resp(200, {"responses": responses})
    except ValueError as e:
        return _resp(404, {"error": str(e)})
    except Exception as e:
        # Loud failure — surface the error so we can debug. We do NOT want a
        # silent 500 here.
        import traceback
        traceback.print_exc()
        return _resp(500, {"error": f"chat failed: {type(e).__name__}: {str(e)[:300]}"})


# ─── Helpers ────────────────────────────────────────────────────────────────

def _ecosystem_from_session(session_id: str) -> str:
    """SFN execution names are <ecosystem-id>-<8hex>. Strip the trailing hex
    suffix to recover the ecosystem id. Falls back to the full string if the
    pattern doesn't match."""
    m = re.match(r"^(.+)-([0-9a-f]{8})$", session_id)
    if m:
        return m.group(1)
    return session_id


def _load_ecosystem_agents(eco_id: str) -> list:
    """Pull agents[] (id, name, prompt, depends_on, tools, swarm) from
    ecosystems/{id}.yaml in the ecosystems bucket. Returns the list with
    compiler-injected challenger_agent + report_agent appended IF they were
    enabled (best-effort — we look at quality.challenger / report flags)."""
    if not ECOSYSTEMS_BUCKET:
        return []
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        config = yaml.safe_load(obj["Body"].read())
    except Exception:
        return []
    agents = list(config.get("agents") or [])
    quality = config.get("quality") or {}
    if config.get("report", True):
        if quality.get("challenger", True):
            agents.append({
                "id": "challenger_agent",
                "name": "Challenger",
                "prompt": "Stress-test upstream reasoning; identify weak claims, gaps, contradictions.",
                "swarm": "platform",
            })
        agents.append({
            "id": "report_agent",
            "name": "Report",
            "prompt": "Faithfully synthesize all upstream findings + the Challenger's critique into a final report.",
            "swarm": "platform",
        })
    return agents


def _load_run_outputs(eco_id: str, session_id: str) -> dict:
    """Walk SFN execution history for {session_id} and pull each succeeded
    agent's full output from S3. Returns {agent_id: output_text}. Mirrors
    the runs-api detail logic (kept inline here to avoid a Lambda→Lambda
    call)."""
    sm_arn = _state_machine_arn(eco_id)
    account = sm_arn.split(":")[4]
    exec_arn = f"arn:aws:states:{REGION}:{account}:execution:{PROJECT}-{ENVIRONMENT}-{eco_id}:{session_id}"
    out = {}
    try:
        history_pages = sfn.get_paginator("get_execution_history").paginate(
            executionArn=exec_arn,
        )
    except Exception:
        return out
    events = []
    for page in history_pages:
        events.extend(page["events"])
    by_id = {e["id"]: e for e in events}

    def find_state_for_succeeded(succ):
        cur = succ
        for _ in range(20):
            pid = cur.get("previousEventId")
            if not pid:
                return None
            prev = by_id.get(pid)
            if not prev:
                return None
            if prev["type"] == "TaskStateEntered":
                return prev.get("stateEnteredEventDetails", {}).get("name")
            cur = prev
        return None

    for ev in events:
        if ev["type"] != "TaskSucceeded":
            continue
        sn = find_state_for_succeeded(ev)
        if not sn:
            continue
        try:
            d = json.loads(ev["taskSucceededEventDetails"]["output"])
        except Exception:
            continue
        p = d.get("Payload", d) if isinstance(d, dict) else d
        if not isinstance(p, dict) or "output_s3_uri" not in p:
            continue
        aid = p.get("agent_id") or sn
        if aid in out:
            continue
        full = _fetch_s3(p.get("output_s3_uri"))
        if full:
            out[aid] = full
    return out


def _fetch_s3(uri: str) -> str:
    if not uri or not uri.startswith("s3://"):
        return ""
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        return ""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _state_machine_arn(eco_id: str) -> str:
    sts = boto3.client("sts", region_name=REGION)
    account = sts.get_caller_identity()["Account"]
    return f"arn:aws:states:{REGION}:{account}:stateMachine:{PROJECT}-{ENVIRONMENT}-{eco_id}"


def _route_question(question: str, agents_cfg: list) -> list:
    """Ask Bedrock to pick 1-3 agent IDs best positioned to answer.
    Returns a list of agent_ids. On failure returns [] and the caller
    falls back."""
    if not agents_cfg:
        return []
    descriptions = "\n".join(
        f"- {a['id']}: {a.get('name', a['id'])} — {(a.get('prompt') or '')[:120]}"
        for a in agents_cfg
    )
    routing_prompt = (
        "You are routing a follow-up question to the most relevant agent(s) "
        "from a multi-agent ecosystem run. Pick 1 to 3 agent IDs whose role "
        "and persona best match the question. Return ONLY a JSON array of "
        "agent_id strings, no prose, no markdown.\n\n"
        f"Agents:\n{descriptions}\n\n"
        f"Question: {question}\n\n"
        "JSON array:"
    )
    try:
        resp = bedrock.invoke_model(
            modelId=ROUTING_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": routing_prompt}],
            }),
        )
        result = json.loads(resp["body"].read())
        text = (result.get("content") or [{}])[0].get("text", "").strip()
        # Tolerate stray prose: extract first [...] JSON array
        m = re.search(r"\[[^\]]*\]", text, re.DOTALL)
        if m:
            text = m.group(0)
        ids = json.loads(text)
        if isinstance(ids, list):
            valid = {a["id"] for a in agents_cfg}
            return [str(x) for x in ids if str(x) in valid][:1]
    except Exception as e:
        print(f"routing failed: {e}")
    return []


def _route_chat(question: str, agents_cfg: list, history: list, target_agent: str = "") -> tuple:
    """Pick the agent to answer + whether it's a handoff. Sticky: keep the agent
    currently handling the thread unless another agent's domain clearly fits.
    An explicit target_agent overrides the router (no handoff). Returns
    (agent_id, handoff_from) where handoff_from is the previous agent id if the
    thread is changing hands, else ""."""
    valid = {a["id"] for a in agents_cfg}
    last_agent = next((t.get("agent_id") for t in reversed(history) if t["role"] == "assistant" and t.get("agent_id")), "")
    # Explicit choice wins.
    if target_agent and target_agent in valid:
        return target_agent, ""
    descriptions = "\n".join(
        f"- {a['id']}: {a.get('name', a['id'])} — {(a.get('prompt') or '')[:120]}" for a in agents_cfg
    )
    convo = "\n".join(f"{t['role']}: {t['content'][:300]}" for t in history[-6:])
    routing_prompt = (
        "You route a follow-up question in an ONGOING chat with a multi-agent run. "
        "Maintain CONTINUITY: keep the SAME agent that is currently handling the thread if it "
        "can reasonably answer. Switch to a different agent ONLY when the new question clearly "
        "falls in another agent's domain.\n\n"
        f"Agents:\n{descriptions}\n\n"
        f"Conversation so far (most recent last):\n{convo or '(none)'}\n\n"
        f"Agent currently handling the thread: {last_agent or '(none)'}\n\n"
        f"New question: {question}\n\n"
        'Return ONLY JSON: {"agent_id":"<id>","handoff":<true|false>}. '
        "handoff=true means a DIFFERENT agent is taking over."
    )
    try:
        resp = bedrock.invoke_model(
            modelId=ROUTING_MODEL_ID, contentType="application/json", accept="application/json",
            body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 120,
                             "messages": [{"role": "user", "content": routing_prompt}]}),
        )
        text = (json.loads(resp["body"].read()).get("content") or [{}])[0].get("text", "").strip()
        m = re.search(r"\{[^}]*\}", text, re.DOTALL)
        if m:
            d = json.loads(m.group(0))
            aid = str(d.get("agent_id", ""))
            if aid in valid:
                handoff_from = last_agent if (bool(d.get("handoff")) and last_agent and aid != last_agent) else ""
                return aid, handoff_from
    except Exception as e:
        print(f"routing failed: {e}")
    # Fallback: stay sticky to the last agent; else report; else first.
    if last_agent and last_agent in valid:
        return last_agent, ""
    fb = next((a["id"] for a in agents_cfg if a["id"] in ("report_agent", "report-agent")), None) or (agents_cfg[0]["id"] if agents_cfg else "")
    return fb, ""


def _agent_memory_acl(eco_id: str, agent_id: str) -> dict:
    """Compute an agent's memory ACL (writable/readable namespaces) from the
    ecosystem's `memory.shared_namespaces`, mirroring the compiler
    (save-ecosystem.compile_to_asl). The ACL is injected by the compiler into
    the run's state machine but is NOT in the stored YAML, so chat — which
    rebuilds the agent_config from the YAML — must re-derive it. Without this the
    chat agent gets an empty ACL and every memory_write is denied, even though
    the same agent could write during the run. Lets the operator ask the agent
    to persist a conclusion to its own namespace mid-conversation."""
    if not ECOSYSTEMS_BUCKET:
        return {"writable": [], "readable": []}
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        config = yaml.safe_load(obj["Body"].read())
    except Exception:
        return {"writable": [], "readable": []}
    shared = ((config.get("memory") or {}).get("shared_namespaces")) or []

    def _norm_ns(p: str) -> str:
        p = (p or "").strip().rstrip("/")
        if not p:
            return ""
        if not p.startswith("/"):
            p = "/" + p
        if not p.startswith("/tessera"):
            p = "/tessera" + p
        return p

    def _acl_list(v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, (list, tuple)):
            return list(v)
        return []

    writable, readable = [], []
    for ns in shared:
        path = _norm_ns(ns.get("path") or ns.get("name", ""))
        if not path:
            continue
        wl = _acl_list(ns.get("write"))
        rl = _acl_list(ns.get("read"))
        if agent_id in wl or "all" in wl:
            writable.append(path)
        if agent_id in rl or "all" in rl:
            readable.append(path)
    return {"writable": writable, "readable": readable}


def _invoke_agent(agent_config: dict, question: str, prior_output: str, session_id: str, history: list = None, handoff_from: str = "", memory_acl: dict = None, user_id: str = "") -> str:
    """Invoke AgentCore Runtime with the agent's persona, the user's
    question as the mission, and the agent's prior run output as
    dependency_context so it can ground its answer.

    Builds a CHAT-MODE agent_config: keeps the persona prompt and tools but
    strips depends_on + role_contract so the runtime doesn't enforce the
    synthesizer citation contract. Synthesizers in chat mode would otherwise
    refuse to answer ("I have no upstream findings to compose from") because
    the agent's own prior output is one finding, not the upstream layer's
    structured findings sections that build_conversation expects.
    """
    if not AGENT_RUNTIME_ARN:
        raise RuntimeError("TESSERA_AGENT_RUNTIME_ARN not configured")

    # Clone + reshape — keeps id/name/swarm but replaces prompt with a chat-
    # mode wrapper that:
    #   (a) reframes the task as Q&A grounded in prior output
    #   (b) explicitly tells the agent NOT to read memory/upstream (sources of
    #       confusion that made early versions refuse to answer)
    #   (c) preserves the agent's specialist persona for tone/expertise
    # Also drops tools (no need for web_search etc. in a follow-up Q&A) and
    # clears depends_on/role_contract so runtime doesn't enforce upstream-
    # citation contract.
    persona = agent_config.get("prompt", "")
    name = agent_config.get("name", agent_config.get("id"))
    chat_prompt = (
        f"You are the {name} agent answering a follow-up question from the "
        f"operator about a completed multi-agent run. The operator already "
        f"saw your full prior output and now wants targeted clarification.\n\n"
        f"YOUR TASK FOR THIS TURN:\n"
        f"- Answer the user's question concisely and directly.\n"
        f"- Ground your answer in YOUR PRIOR OUTPUT (provided in the input "
        f"  context below the question).\n"
        f"- You MAY use memory_write to persist a conclusion to your OWN "
        f"  namespace when the operator asks you to save/record it, or when the "
        f"  conversation clearly reaches something worth keeping for future runs "
        f"  (your writable namespace(s) are listed below if you have any). Use "
        f"  memory_read only to recall something previously written. Otherwise "
        f"  don't call tools, don't re-do the analysis, and don't cite upstream "
        f"  agents — they are not needed for this turn.\n"
        f"- Do NOT include `## STRUCTURED FINDINGS` or `## OPEN QUESTIONS` "
        f"  sections. This is a chat reply, not a synthesis pass.\n"
        f"- Be conversational. Quote your prior findings when useful.\n\n"
        f"YOUR SPECIALIST PERSONA (for tone and domain framing):\n{persona}"
    )
    chat_agent_config = {
        "id": agent_config.get("id"),
        "name": name,
        "swarm": agent_config.get("swarm", "chat"),
        "prompt": chat_prompt,
        "tools": [],            # ← chat mode: no Gateway tools (platform tools incl. memory_* are always available)
        "depends_on": [],       # ← no upstream peers
        # Same memory ACL the agent had during the run, so it can write to / read
        # from its own namespace mid-conversation. Derived from the ecosystem's
        # shared_namespaces (the compiler injects this for runs; chat re-derives).
        "memory": memory_acl or {"writable": [], "readable": []},
        # Phase 5e: output_kind: chat is the modern path. chat_mode: True
        # remains as a deprecated alias for backwards-compat with any older
        # callers that still set it directly.
        "output_kind": "chat",
        "chat_mode": True,
        # Intentionally omit role_contract — runtime treats as leaf, no
        # citation enforcement.
    }

    runtime_session_id = f"{session_id}-chat-{agent_config['id']}-{uuid.uuid4().hex[:8]}"[:64]
    if len(runtime_session_id) < 33:
        runtime_session_id = f"{runtime_session_id}-{uuid.uuid4().hex}"[:64]

    # Compose the mission field to include BOTH the question and the prior
    # output. The runtime hardcodes a "Synthesize upstream first... End with
    # STRUCTURED FINDINGS" instruction immediately after the mission text,
    # which would override any chat-mode framing we put in input_context.
    # Stuffing the prior output into the mission ensures the question +
    # context are the dominant signal the model sees.
    if prior_output:
        snippet = prior_output[:6000]
        convo = "\n".join(f"{t['role'].upper()}: {t['content'][:400]}" for t in (history or [])[-8:])
        handoff_line = (
            f"\n\n--- HANDOFF ---\nThe operator was talking to '{handoff_from}', but this question is in "
            f"YOUR domain, so you are taking over the thread. Briefly acknowledge that you're stepping in "
            f"(one short sentence, e.g. 'I'll take this — it's my area'), then answer.\n"
            if handoff_from else ""
        )
        mission_text = (
            f"FOLLOW-UP QUESTION FROM OPERATOR:\n\n{question}\n\n"
            + (f"--- CONVERSATION SO FAR (for continuity — you may reference it) ---\n{convo}\n\n" if convo else "")
            + handoff_line
            + f"--- YOUR PRIOR OUTPUT FROM THIS RUN (session {session_id}) ---\n"
            f"{snippet}\n\n"
            f"--- INSTRUCTIONS ---\n"
            f"Answer the operator's question concisely, grounded in your prior "
            f"output above and the conversation so far. This is a chat-style follow-up, NOT a synthesis "
            f"pass. Do not call tools. Do not produce '## STRUCTURED FINDINGS' "
            f"or '## OPEN QUESTIONS' sections — they are not needed for chat. "
            f"Quote relevant figures and findings from your prior output where "
            f"useful. If the answer isn't in your prior output, say so plainly "
            f"rather than fabricating numbers."
        )
    else:
        mission_text = (
            f"FOLLOW-UP QUESTION FROM OPERATOR:\n\n{question}\n\n"
            f"You have no prior output from this run for context. Answer "
            f"based on your specialist persona, but be explicit if the "
            f"question requires data you don't have."
        )

    payload = json.dumps({
        "threadId": session_id,
        "runId": f"chat-{agent_config['id']}-{uuid.uuid4().hex[:8]}",
        "messages": [{"id": str(uuid.uuid4()), "role": "user", "content": mission_text}],
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {
            "agent_config": chat_agent_config,
            "mission": mission_text,
            "dependency_context": "",
            "input_context": "",
            # Pass user_id so memory_write is scoped to the SAME actor the run
            # used (memory is user-scoped for non-org namespaces) — otherwise a
            # chat write would land under a different actor than the run's writes.
            "user_id": user_id,
        },
    }).encode()

    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        runtimeSessionId=runtime_session_id,
        payload=payload,
        qualifier="DEFAULT",
    )

    sse_stream = response.get("response")
    line_iter = sse_stream.iter_lines() if hasattr(sse_stream, "iter_lines") else _fallback_lines(sse_stream)

    text_parts = []
    for line in line_iter:
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            event_data = json.loads(line[6:])
        except Exception:
            continue
        if event_data.get("type") == "TEXT_MESSAGE_CONTENT":
            delta = event_data.get("delta") or event_data.get("content") or ""
            text_parts.append(str(delta))
    return "".join(text_parts).strip()


def _fallback_lines(stream):
    """Line iterator for streams without iter_lines()."""
    buf = b""
    for chunk in stream:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line
    if buf:
        yield buf
