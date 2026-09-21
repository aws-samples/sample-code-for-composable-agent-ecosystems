"""Mission Console — conversational run-planner for a SINGLE exported ecosystem.

This is the mission-shaping half of the platform's builder (suggest-ecosystem `mode=mission`),
extracted for export so a deployed eco has the SAME conversational mission capability WITHOUT
shipping the proprietary ecosystem-DESIGN builder (create/converse/beautify modes + design
prompt stay in Tessera). It turns the operator's intent + uploaded documents into a runnable
mission + declared inputs for THIS eco.

Contract (identical to the platform, so the reused frontend works unchanged):
  POST /api/ecosystems/suggest {mode:"mission", message, history, mission_state, eco_context,
       attachments:[{name,ref}], clientId}
  -> 202 {jobId, status:"pending"}  (async), then over the client's WebSocket:
     CONVERSE_DELTA {jobId, text}  (streamed reply tokens)
     CONVERSE_RESULT {jobId, reply, next_question, mission_state, converged, mission, inputs}
  If no clientId (testing), runs synchronously and returns the turn as JSON.
"""
import json
import os
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "")
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
MISSION_MODEL_ID = os.environ.get("MISSION_MODEL_ID") or os.environ.get(
    "BUILDER_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"
)
_MISSION_MARKER = "===MISSION==="


def _resp(status, body):
    return {"statusCode": status,
            "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
                        "Content-Type": "application/json"},
            "body": json.dumps(body, default=str)}


def _resolve_connections(client_id):
    """Return (apigw_client, [connectionId]) for a clientId, or (None, [])."""
    if not CONNECTIONS_TABLE or not WS_ENDPOINT or not client_id:
        return None, []
    table = boto3.resource("dynamodb", region_name=REGION).Table(CONNECTIONS_TABLE)
    items = table.scan(
        FilterExpression="clientId = :cid",
        ExpressionAttributeValues={":cid": client_id},
    ).get("Items", [])
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    return apigw, [i["connectionId"] for i in items]


def _post(apigw, conns, payload):
    if not apigw:
        return
    data = json.dumps(payload).encode()
    for c in conns:
        try:
            apigw.post_to_connection(ConnectionId=c, Data=data)
        except Exception:
            pass


MISSION_SYSTEM = """You are the Tessera Run Planner — a conversational partner that helps a user
define the MISSION for a single run of an ALREADY-BUILT ecosystem. You do NOT design or modify the
ecosystem; you turn the user's intent into a clear, runnable mission for THIS ecosystem.

You are given `ecosystem` (name, description, declared inputs, outcome) and any `attachments` the
user uploaded for this run.

Rules:
- BUILD A BRIEFING CHECKLIST. On your first turn, derive from the ecosystem's declared inputs (and its
  purpose) the things you must know to brief THIS ecosystem — every REQUIRED declared input plus any
  essential scope/constraint. Put them in `mission_state.checklist` as a list of
  { "id", "label", "resolved": false, "value": null, "source": null }. Acknowledge the user's goal in
  one line on the first turn.
- SOURCE FROM THE ATTACHMENTS FIRST. The user uploaded documents precisely so the assessment reads
  them — do NOT ask the user to hand-type information that is present in the files. For each checklist
  item, FIRST try to resolve it from the attachment excerpts; when a document states it, set
  resolved=true, record the value, and set `source` to the filename (e.g. "solution-overview.pdf").
  Extracting a value a document states is SOURCING, not inventing.
- CONFIRM, DON'T INTERROGATE. When attachments resolve items, present the extracted values back to the
  user in ONE consolidated confirmation — a short list of item -> value -> source — and ask them to
  CONFIRM or CORRECT. Do NOT ask for file-covered items one at a time. Treat file-sourced items as
  provisionally resolved pending that single confirmation.
- ASK ONLY FOR GENUINE GAPS, one at a time: items NO attachment covers, that the attachments leave
  ambiguous, or where attachments conflict. If the user says "take it from the files" (or names a
  file), resolve the item from the files and cite the source rather than pushing back for a typed answer.
- NEVER INVENT. Never record a value that is neither stated in an attachment nor given by the user. If
  it is in neither, it is a gap — ask, or if the user defers, record "(left to the ecosystem's
  discretion)".
- USE OPTIONS for closed choices (e.g. a level -> low / medium / high) via next_question.options; the
  user can still type their own.
- CONVERGENCE IS GATED ON THE CHECKLIST. converged=true ONLY when EVERY checklist item is resolved —
  each either confirmed/corrected by the user or explicitly deferred by the user. File-sourced values
  must have been shown to the user for confirmation before you converge. When converged, write
  `mission` as a clear imperative and fill `inputs` with the declared-input values (file-sourced values
  included), noting each value's source in `mission_state.notes`. Convergence is verified by the
  platform, so a premature converged=true will be rejected.

Respond in TWO parts:
PART 1 — a short prose reply to the user (acknowledgement / the one question / confirmation).
PART 2 — on its own line the marker ===MISSION=== then a JSON object:
{
  "next_question": { "id": "...", "text": "...", "options": ["...","..."] } | null,
  "mission_state": {
    "goal": "...",
    "checklist": [ { "id": "...", "label": "...", "resolved": false, "value": null, "source": null } ],
    "notes": "...",
    "inputs": { "<declared_input_id>": "<value>" }
  },
  "converged": false,
  "mission": "<final imperative when converged, else null>",
  "inputs": { "<declared_input_id>": "<value>" }
}
Emit ONLY these two parts."""


_ATTACH_TEXT_EXTS = {"md", "txt", "csv", "json", "yaml", "yml", "xml", "html", "htm", "log", "tsv", "ini", "toml", "rtf"}
_ATTACH_DOC_EXTS = {"pdf", "doc", "docx"}
_ATTACH_EXCERPT_BYTES = 8000
_ATTACH_EXCERPT_TOTAL_BYTES = 40000


def _enrich_attachments(attachments):
    """Let the run-planner *see* uploaded files (summary from file-classify meta + a bounded
    text excerpt) so it can SOURCE declared inputs from documents. Best-effort."""
    s3 = boto3.client("s3", region_name=REGION)
    out = []
    atts = attachments or []
    text_atts = sum(1 for a in atts if isinstance(a, dict)
                    and (a.get("name", "").rsplit(".", 1)[-1].lower() if "." in a.get("name", "") else "") in (_ATTACH_TEXT_EXTS | _ATTACH_DOC_EXTS))
    per_file_budget = min(_ATTACH_EXCERPT_BYTES, max(1500, _ATTACH_EXCERPT_TOTAL_BYTES // max(1, text_atts)))
    remaining = _ATTACH_EXCERPT_TOTAL_BYTES
    for a in atts:
        name = a.get("name", "")
        ref = a.get("ref") or a.get("content") or ""
        item = {"name": name, "ref": ref}
        if not isinstance(ref, str) or not ref.startswith("s3://"):
            out.append(item); continue
        bucket, _, key = ref[5:].partition("/")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            meta = json.loads(s3.get_object(Bucket=bucket, Key=key + ".meta.json")["Body"].read())
            if meta.get("summary"): item["summary"] = meta["summary"]
            if meta.get("tag"): item["tag"] = meta["tag"]
        except Exception:
            pass
        budget = min(per_file_budget, remaining)
        read_key = None
        if ext in _ATTACH_TEXT_EXTS:
            read_key = key
        elif ext in _ATTACH_DOC_EXTS:
            read_key = key + ".extracted.txt"
        if read_key and budget > 0:
            try:
                raw = s3.get_object(Bucket=bucket, Key=read_key, Range=f"bytes=0-{budget - 1}")["Body"].read()
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    item["excerpt"] = text
                    remaining -= len(raw)
                    if len(raw) >= budget:
                        item["excerpt_truncated"] = True
            except Exception as e:
                print(f"attachment excerpt read failed for {read_key}: {e}")
        if "excerpt" not in item and "summary" not in item:
            item["note"] = "binary file — full contents are analyzed by the ecosystem at run time"
        out.append(item)
    return out


def _mission_turn_messages(history, message, mission_state, eco_context, attachments):
    return [{"role": "user", "content": json.dumps({
        "ecosystem": eco_context or {},
        "attachments": _enrich_attachments(attachments),
        "mission_state": mission_state or {},
        "conversation_so_far": history or [],
        "user_message": message or "",
    })}]


def _parse_mission_turn(text):
    """Split a two-part run-planner response into {reply, next_question, mission_state,
    converged, mission, inputs}. PART 2 follows ===MISSION===."""
    if _MISSION_MARKER in text:
        reply, _, rest = text.partition(_MISSION_MARKER)
    else:
        reply, rest = text, ""
    reply = reply.strip()
    rest = rest.strip()
    if rest.startswith("```"):
        rest = "\n".join(rest.split("\n")[1:])
        if rest.endswith("```"):
            rest = "\n".join(rest.split("\n")[:-1])
    structured = {}
    parse_error = False
    if rest:
        try:
            structured = json.loads(rest)
        except json.JSONDecodeError:
            structured = {}
            parse_error = True
    turn = {
        "reply": reply or "(no reply)",
        "next_question": structured.get("next_question"),
        "mission_state": structured.get("mission_state") or {},
        "converged": bool(structured.get("converged")),
        "mission": structured.get("mission"),
        "inputs": structured.get("inputs") or {},
        "_parse_error": parse_error,
    }
    # Deterministic sufficiency gate: override eager convergence until every checklist item is resolved.
    checklist = (turn["mission_state"] or {}).get("checklist") or []
    if isinstance(checklist, list):
        unresolved = [c for c in checklist if isinstance(c, dict) and not c.get("resolved")]
        if turn["converged"] and unresolved:
            turn["converged"] = False
            turn["mission"] = None
            if not turn.get("next_question"):
                nxt = unresolved[0]
                turn["next_question"] = {"id": nxt.get("id", "q"),
                                         "text": f"Before we launch — {nxt.get('label', 'one more detail')}?"}
    return turn


def converse_mission(history, message, mission_state, eco_context, attachments):
    """Non-streaming mission turn (sync/testing). Returns the parsed turn."""
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.invoke_model(
        modelId=MISSION_MODEL_ID, contentType="application/json", accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31", "max_tokens": 16384,
            "system": MISSION_SYSTEM,
            "messages": _mission_turn_messages(history, message, mission_state, eco_context, attachments),
        }),
    )
    result = json.loads(resp["body"].read())
    if result.get("stop_reason") == "max_tokens":
        raise ValueError("Mission turn was truncated (max_tokens).")
    return _parse_mission_turn(result["content"][0]["text"])


def converse_mission_stream(history, message, mission_state, eco_context, attachments, client_id, job_id):
    """Streaming mission turn — CONVERSE_DELTA reply tokens then a CONVERSE_RESULT turn."""
    apigw, conns = _resolve_connections(client_id)
    client = boto3.client("bedrock-runtime", region_name=REGION)
    try:
        resp = client.invoke_model_with_response_stream(
            modelId=MISSION_MODEL_ID, contentType="application/json", accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31", "max_tokens": 16384,
                "system": MISSION_SYSTEM,
                "messages": _mission_turn_messages(history, message, mission_state, eco_context, attachments),
            }),
        )
        full = ""; streamed = 0; marker_hit = False; stop_reason = None
        hold = len(_MISSION_MARKER) - 1
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            d = json.loads(chunk["bytes"])
            if d.get("type") == "message_delta":
                stop_reason = ((d.get("delta") or {}).get("stop_reason")) or stop_reason
            if d.get("type") != "content_block_delta":
                continue
            piece = (d.get("delta") or {}).get("text", "")
            if not piece:
                continue
            full += piece
            if marker_hit:
                continue
            idx = full.find(_MISSION_MARKER)
            if idx == -1:
                safe = len(full) - hold
                if safe > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:safe]})
                    streamed = safe
            else:
                if idx > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:idx]})
                    streamed = idx
                marker_hit = True
        turn = _parse_mission_turn(full)
        if stop_reason == "max_tokens" or turn.get("_parse_error"):
            _post(apigw, conns, {
                "type": "CONVERSE_RESULT", "jobId": job_id,
                "error": ("The planner's reply was too long and got cut off before it finished, "
                          "so the mission couldn't be finalized. Your answers are preserved — "
                          "send once more to retry the final step."),
                "truncated": True, "reply": turn.get("reply") or "…",
                "mission_state": mission_state or {}, "next_question": None,
                "converged": False, "mission": None,
                "inputs": (mission_state or {}).get("inputs") or {},
            })
            return
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, **turn})
    except Exception as e:
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, "error": str(e)[:300]})


def run_async(payload):
    """Async self-invocation worker: stream the mission turn over the WebSocket."""
    converse_mission_stream(
        payload.get("history", []), payload.get("message", ""), payload.get("mission_state", {}),
        payload.get("eco_context", {}), payload.get("attachments", []),
        payload.get("clientId", ""), payload.get("jobId", ""),
    )
    return {"ok": True}


def handler(event, context):
    # Async self-invocation path
    if event.get("_async"):
        return run_async(event["_async"])

    if event.get("httpMethod") == "OPTIONS":
        return _resp(200, {})

    body = json.loads(event.get("body", "{}"))
    # This Lambda ONLY does mission planning (the proprietary builder modes are not shipped).
    if body.get("mode") and body.get("mode") != "mission":
        return _resp(400, {"error": "this endpoint only supports mode=mission"})

    message = body.get("message", "")
    history = body.get("history", []) or []
    mission_state = body.get("mission_state", {}) or {}
    eco_context = body.get("eco_context", {}) or {}
    attachments = body.get("attachments", []) or []
    client_id = body.get("clientId", "")

    if not client_id:
        try:
            return _resp(200, converse_mission(history, message, mission_state, eco_context, attachments))
        except Exception as e:
            return _resp(500, {"error": str(e)})

    job_id = str(uuid.uuid4())
    boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=LAMBDA_FUNCTION_NAME, InvocationType="Event",
        Payload=json.dumps({"_async": {
            "mode": "mission", "message": message, "history": history,
            "mission_state": mission_state, "eco_context": eco_context,
            "attachments": attachments, "clientId": client_id, "jobId": job_id,
        }}).encode(),
    )
    return _resp(202, {"jobId": job_id, "status": "pending"})
