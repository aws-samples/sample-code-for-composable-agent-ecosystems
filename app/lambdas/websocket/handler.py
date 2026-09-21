"""WebSocket AWS Lambda — handles connect, disconnect, launch, chat, and SFN callback routes.

Services: Amazon DynamoDB, AWS Step Functions, Amazon S3, Amazon Bedrock."""

import os
import json
import time
import uuid
import boto3

# Non-sensitive resource configuration (no secrets in env vars)
CONNECTIONS_TABLE = os.environ["CONNECTIONS_TABLE"]
STATE_MACHINE_PREFIX = os.environ["STATE_MACHINE_PREFIX"]
AGENT_RUNTIME_ARN = os.environ["TESSERA_AGENT_RUNTIME_ARN"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
connections_table = dynamodb.Table(CONNECTIONS_TABLE)
sfn = boto3.client("stepfunctions", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

# spec-quality-feedback-loop §5: persistent per-ecosystem guidance store lives in
# the runs bucket at guidance/<eco>.json; injected into input_context at launch.
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")


def _read_standing_guidance(ecosystem_id):
    """Compose the STANDING QUALITY GUIDANCE block from the ecosystem's enabled
    guidance entries. Returns "" when there are none (launch stays byte-identical
    to before). Capped to <=20 entries / <=4KB to bound prompt bloat. Best-effort:
    never breaks a launch."""
    if not RUNS_BUCKET or not ecosystem_id:
        return ""
    try:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(ecosystem_id))[:128]
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"guidance/{safe}.json")
        data = json.loads(obj["Body"].read() or b"{}")
    except Exception:
        return ""
    entries = [e for e in (data.get("entries") or [])
               if isinstance(e, dict) and e.get("enabled") and str(e.get("text") or "").strip()]
    if not entries:
        return ""
    lines, total = [], 0
    for e in entries[:20]:
        t = str(e["text"]).strip().replace("\n", " ")
        if total + len(t) > 4000:
            lines.append("- … (additional guidance truncated)")
            break
        lines.append(f"- {t}")
        total += len(t)
    return (
        "=== STANDING QUALITY GUIDANCE (operator-curated, applies to every run) ===\n"
        "These are lessons from prior runs of this ecosystem. Treat them as authoritative\n"
        "preferences for HOW to do the work. Apply every applicable point:\n"
        + "\n".join(lines) + "\n"
        "========================================================================="
    )

# Text/data formats we can decode and inline directly; others fall back to the
# file-classify summary (.meta.json) since we can't extract binary text here.
_ATTACH_TEXT_EXTS = {"md", "txt", "csv", "json", "yaml", "yml", "xml", "html", "htm", "log", "tsv", "ini", "toml", "rtf"}
_ATTACH_PER_FILE_BYTES = 60000
_ATTACH_TOTAL_BYTES = 300000


def _load_attachment_block(attachments):
    """Build a compact MANIFEST of the uploaded run attachments to fold into the
    mission — name, type, size, and a short summary/preview per file — and direct
    agents to pull the FULL contents on demand via the `read_attachment` tool.

    We deliberately DO NOT inline each file's full text anymore: doing so bloated
    the Step Functions state (every agent's Task payload carries `mission`) and blew
    the 256KB StartExecution cap on resume for doc-heavy runs. The full extracted
    text still lives in S3 (<key>.extracted.txt from file-classify) and is fetched
    on demand by the read_attachment tool (see agentcore main.py). Best-effort —
    never breaks a launch."""
    if not attachments:
        return ""
    _PREVIEW_BYTES = 500
    parts = []
    for a in attachments:
        name = (a.get("name") or "").strip()
        ref = a.get("ref") or a.get("content") or ""
        if not name or not isinstance(ref, str) or not ref.startswith("s3://"):
            continue
        bucket, _, key = ref[5:].partition("/")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        descr = ""
        try:
            if ext in _ATTACH_TEXT_EXTS:
                raw = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{_PREVIEW_BYTES - 1}")["Body"].read()
                head = raw.decode("utf-8", errors="replace").strip().replace("\n", " ")
                descr = f"text/{ext or 'txt'} — preview: {head[:400]}…"
            else:
                summ = ""
                try:
                    meta = json.loads(s3.get_object(Bucket=bucket, Key=key + ".meta.json")["Body"].read())
                    summ = (meta.get("summary") or "").strip()
                except Exception:
                    summ = ""
                descr = f"{ext or 'file'} document — {summ}" if summ else f"{ext or 'file'} document"
        except Exception as e:
            print(f"[launch] attachment manifest failed for {key}: {e}")
            descr = f"{ext or 'file'}"
        parts.append(f"- **{name}** — {descr}")
    if not parts:
        return ""
    return (
        "\n\n## ATTACHED FILES (uploaded by the operator for this run)\n"
        "These files are available to you. To read or quote the EXACT contents of any of them, "
        "call the `read_attachment` tool with the file's name (use offset/max_chars to page "
        "through large documents). Do NOT claim you cannot access an uploaded file — read it "
        "with read_attachment.\n" + "\n".join(parts))


def handler(event, context):
    import traceback
    try:
        route = event.get("requestContext", {}).get("routeKey")
        connection_id = event.get("requestContext", {}).get("connectionId")
        print(f"Route: {route}, Connection: {connection_id}")

        if route == "$connect":
            return on_connect(connection_id)
        elif route == "$disconnect":
            return on_disconnect(connection_id)
        elif route == "launch":
            return on_launch(connection_id, event)
        elif route == "chat":
            return on_chat(connection_id, event)
        elif route == "register":
            return on_register(connection_id, event)
        elif route == "ping":
            return on_ping(connection_id, event)
        elif route == "sfn_complete":
            return on_sfn_complete(event)
        else:
            body = json.loads(event.get("body", "{}"))
            action = body.get("action", "")
            print(f"Default route, action: {action}")
            if action == "launch":
                return on_launch(connection_id, event)
            elif action == "chat":
                return on_chat(connection_id, event)
            elif action == "register":
                return on_register(connection_id, event)
            elif action == "unregister":
                return on_unregister(connection_id, event)
            elif action == "ping":
                return on_ping(connection_id, event)
            elif action == "quorum":
                return on_quorum(connection_id, event)
            elif action == "sfn_complete":
                return on_sfn_complete(event)

        return {"statusCode": 200}
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        return {"statusCode": 500, "body": str(e)}


def on_connect(connection_id):
    connections_table.put_item(Item={"connectionId": connection_id, "sessionIds": set(["__empty__"])})
    return {"statusCode": 200}


def on_disconnect(connection_id):
    connections_table.delete_item(Key={"connectionId": connection_id})
    return {"statusCode": 200}


def on_ping(connection_id, event):
    """Keepalive — frontend sends a `ping` action every ~5 minutes so the
    API Gateway WebSocket idle timeout (10 minutes, reset only by client→server
    messages) doesn't fire mid-run. Returns immediately; no payload back to
    the client (the act of sending the ping is itself the keepalive — server
    push events do NOT reset API Gateway's idle timer, only client messages do).

    See docs/features/long-run-keepalive.md for the design.
    """
    return {"statusCode": 200}


def on_register(connection_id, event):
    """Bind a clientId and optionally add a sessionId to this connection's session set.
    Multiple sessions can be registered per connection (multi-tab support)."""
    body = json.loads(event.get("body", "{}"))
    client_id = body.get("clientId", "")
    session_id = body.get("sessionId", "")
    if not client_id:
        return {"statusCode": 400}
    update_expr = "SET clientId = :cid"
    expr_values = {":cid": client_id}
    if session_id:
        # Add session_id to the set
        update_expr += " ADD sessionIds :sids"
        expr_values[":sids"] = set([session_id])
    connections_table.update_item(
        Key={"connectionId": connection_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )
    push_event(connection_id, event, {"type": "REGISTERED", "clientId": client_id, "connectionId": connection_id})
    return {"statusCode": 200}


def on_unregister(connection_id, event):
    """Remove a sessionId from this connection's session set (tab closed)."""
    body = json.loads(event.get("body", "{}"))
    session_id = body.get("sessionId", "")
    if not session_id:
        return {"statusCode": 400}
    try:
        connections_table.update_item(
            Key={"connectionId": connection_id},
            UpdateExpression="DELETE sessionIds :sids",
            ExpressionAttributeValues={":sids": set([session_id])},
        )
    except Exception:
        pass
    return {"statusCode": 200}


_JWK_CLIENT = None


def _verify_jwt(token):
    """Verify a Cognito ID token's SIGNATURE (JWKS) + issuer/audience/expiry.
    Returns the claims dict on success, else None. Signature verification is REQUIRED:
    decoding claims without verifying the signature lets a caller forge any identity."""
    pool = os.environ.get("COGNITO_USER_POOL_ID", "")
    client = os.environ.get("COGNITO_CLIENT_ID", "")
    if not token or not pool:
        return None
    global _JWK_CLIENT
    try:
        import jwt
        from jwt import PyJWKClient
        if _JWK_CLIENT is None:
            _JWK_CLIENT = PyJWKClient(
                f"https://cognito-idp.{REGION}.amazonaws.com/{pool}/.well-known/jwks.json")
        key = _JWK_CLIENT.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256"],
            audience=client if client else None,
            issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{pool}",
            options={"verify_aud": bool(client)},
        )
    except Exception as e:
        print(f"[auth] WS token verification failed: {type(e).__name__}: {str(e)[:200]}")
        return None


def on_launch(connection_id, event):
    body = json.loads(event.get("body", "{}"))
    ecosystem_id = body.get("ecosystem_id", "")
    mission = body.get("mission", "")
    inputs = body.get("inputs", {})
    attachments = body.get("attachments", []) or []  # [{name, ref(s3Uri)}] uploaded during mission chat
    # Mission conversation transcript ([{role, content}]) — persisted for run replay.
    # Missions are short, so it rides in the SFN input (well under the 256KB limit).
    mission_transcript = body.get("mission_transcript", []) or []
    session_id = f"{ecosystem_id}-{uuid.uuid4().hex[:8]}"

    # Authenticate: REQUIRE a signature-verified Cognito ID token. Identity comes ONLY
    # from the verified `sub` claim — never a client-supplied user_id (which would let
    # a caller impersonate any user and run/read under their identity).
    claims = _verify_jwt(body.get("token", ""))
    if not claims or not claims.get("sub"):
        return {"statusCode": 401, "body": "Invalid or missing token"}
    user_id = claims["sub"]

    # Store session mapping — ADD to the set (multi-tab support)
    # Also record userId for this session
    update_expr = "ADD sessionIds :sids"
    expr_values = {":sids": set([session_id])}
    if user_id:
        update_expr += " SET userId = :uid"
        expr_values[":uid"] = user_id
    connections_table.update_item(
        Key={"connectionId": connection_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    # Build input_context from ecosystem inputs
    input_context = ""
    if inputs:
        input_context = "\n".join(f"[{k}]: {v}" for k, v in inputs.items() if v)

    # spec-quality-feedback-loop §5: prepend operator-curated STANDING GUIDANCE
    # (enabled entries from prior-run feedback promoted for this ecosystem). It
    # rides in input_context, which agent-invoker already forwards to every agent
    # (below run-specific OPERATOR DIRECTIVES, above the raw mission). No-op when
    # the ecosystem has no enabled guidance.
    _standing = _read_standing_guidance(ecosystem_id)
    if _standing:
        input_context = _standing + ("\n\n" + input_context if input_context else "")

    # Fold a compact MANIFEST of the uploaded attachments into the mission (names +
    # summaries), and direct agents to pull full contents on demand via the
    # `read_attachment` tool. We do NOT inline full file text: the agent Task payload
    # carries `mission`, so dumping every file's text bloated the Step Functions state
    # and blew the 256KB StartExecution cap on resume for doc-heavy runs. Full text
    # stays in S3 (<key>.extracted.txt) and is read on demand (see agentcore main.py).
    #
    # `mission_display` (clean mission, no manifest) is kept for run HISTORY so the UI
    # shows the mission text + the uploaded files as files.
    mission_display = mission or ""
    _att_block = _load_attachment_block(attachments)
    if _att_block:
        mission = mission_display + "\n\n" + _att_block

    # Persist the attachment refs so agent-invoker can presign doc CITATIONS at save time.
    # The uploaded files live under the upload DRAFT prefix (draft-XXX/inputs/), not the run
    # prefix, and the Task payload doesn't forward attachments — so we drop a small manifest
    # at runs/<session_id>/_attachments.json (same runs bucket the refs point at).
    if attachments:
        try:
            _first = next((a.get("ref") for a in attachments if str(a.get("ref", "")).startswith("s3://")), "")
            if _first:
                _bkt = _first[5:].split("/", 1)[0]
                s3.put_object(
                    Bucket=_bkt, Key=f"runs/{session_id}/_attachments.json",
                    Body=json.dumps([{"name": a.get("name"), "ref": a.get("ref")} for a in attachments]).encode("utf-8"),
                    ContentType="application/json",
                )
        except Exception as e:
            print(f"[launch] failed to write _attachments.json: {type(e).__name__}: {e}")

    # Start Step Functions execution
    state_machine_arn = f"arn:aws:states:{REGION}:{os.environ.get('AWS_ACCOUNT_ID', '')}:stateMachine:{STATE_MACHINE_PREFIX}-{ecosystem_id}"

    try:
        sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=session_id,
            input=json.dumps({
                "mission": mission,
                "mission_display": mission_display,
                "session_id": session_id,
                "user_id": user_id,
                "dry_run": bool(body.get("dry_run", False)),
                # spec-run-tuning §4: per-run model config (model_profile / model_overrides).
                # Empty {} for legacy launches; agentcore falls back to platform defaults.
                "run_config": body.get("run_config", {}) or {},
                "dependency_context": "",
                "input_context": input_context,
                "attachments": attachments,
                "mission_transcript": mission_transcript,
                "results": {},
            }),
        )
    except sfn.exceptions.StateMachineDoesNotExist:
        # No compiled state machine. Quorum-archetype ecos never compile one (spec §5). If a
        # Quorum runtime is wired via QUORUM_RUN_FUNCTION, route the launch there; otherwise
        # fail closed — identical to Stage 0 when the env is unset (zero impact by default).
        quorum_fn = os.environ.get("QUORUM_RUN_FUNCTION", "")
        if quorum_fn:
            try:
                # Interactive Deliberation Room: `start` convenes the panel and stops at the
                # empanelment gate, streaming turn-by-turn. Subsequent human commands
                # (empanel/advance/human_say/summon/rule_now) arrive via the `quorum` action.
                boto3.client("lambda", region_name=REGION).invoke(
                    FunctionName=quorum_fn, InvocationType="Event",
                    Payload=json.dumps({"command": "start", "session_id": session_id,
                                        "ecosystem_id": ecosystem_id, "decision": mission,
                                        "context": input_context, "user_id": user_id,
                                        # Advanced-launch run settings (quorum/index.html §12).
                                        # Validated fail-closed by the quorum runtime, then recorded
                                        # with the run so two runs can be compared.
                                        "settings": ((body.get("run_config") or {})
                                                     .get("quorum_settings") or {})}).encode("utf-8"))
                push_event(connection_id, event, {"type": "RUN_STARTED", "ecosystem": ecosystem_id,
                                                  "session_id": session_id, "archetype": "quorum",
                                                  "interactive": True})
                return {"statusCode": 200, "body": "quorum session started"}
            except Exception as e:
                print(f"[launch] quorum route failed: {type(e).__name__}: {e}")
        print(f"[launch] no state machine for {ecosystem_id} — refusing (quorum archetype or undeployed)")
        return {"statusCode": 409, "body": "ecosystem is not runnable on the standard engine"}

    # Push RUN_STARTED event
    push_event(connection_id, event, {"type": "RUN_STARTED", "ecosystem": ecosystem_id, "session_id": session_id})

    return {"statusCode": 200}


def on_quorum(connection_id, event):
    """Dispatch an interactive Deliberation Room command to the quorum runtime (flag-guarded
    by QUORUM_RUN_FUNCTION). The command resumes the persisted session and streams events back
    to this connection's session. Fully separate from the DAG launch/start_execution path."""
    body = json.loads(event.get("body", "{}"))
    session_id = body.get("session_id", "")
    command = body.get("command", "")
    if not session_id or not command:
        return {"statusCode": 400, "body": "session_id and command required"}
    quorum_fn = os.environ.get("QUORUM_RUN_FUNCTION", "")
    if not quorum_fn:
        return {"statusCode": 409, "body": "quorum runtime not configured"}
    # Authz: the session must belong to THIS connection (prevents driving another's session).
    try:
        item = connections_table.get_item(Key={"connectionId": connection_id}).get("Item", {}) or {}
        if session_id not in (item.get("sessionIds") or set()):
            return {"statusCode": 403, "body": "session not owned by this connection"}
    except Exception:
        pass
    payload = {"command": command, "session_id": session_id}
    # Stamp the human's message with the RECEIPT time now — before it can queue behind an in-flight
    # panelist turn — so the transcript preserves where they actually spoke.
    if command == "human_say":
        payload["received_at"] = time.time()
    for k in ("empanel", "text", "target", "ids", "id", "entry_type"):
        if k in body:
            payload[k] = body[k]
    try:
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=quorum_fn, InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"))
    except Exception as e:
        print(f"[quorum] dispatch failed: {type(e).__name__}: {e}")
        return {"statusCode": 500, "body": "quorum dispatch failed"}
    return {"statusCode": 200, "body": "quorum command dispatched"}


def on_sfn_complete(event):
    """Called by EventBridge or SFN callback when execution finishes."""
    body = json.loads(event.get("body", "{}")) if event.get("body") else event
    session_id = body.get("session_id", "")
    status = body.get("status", "SUCCEEDED")
    ecosystem_id = body.get("ecosystem_id", "")

    if not session_id:
        return {"statusCode": 400}

    event_type = "RUN_FINISHED" if status == "SUCCEEDED" else "RUN_ERROR"
    push_to_session(session_id, {"type": event_type, "ecosystem": ecosystem_id, "session_id": session_id})

    return {"statusCode": 200}


def on_chat(connection_id, event):
    """Async dispatcher for the run "Ask" chat. The rich Q&A (agent routing, run grounding,
    durable history) lives in the `chat` Lambda. Report-generation questions can take a
    minute+, which blows API Gateway's 29s REST timeout — so we run it ASYNC: Event-invoke
    the chat Lambda with this connection + the WS callback endpoint, and it pushes an
    ASK_RESPONSE (or ASK_ERROR) back over the socket when done. We return 200 immediately."""
    body = json.loads(event.get("body", "{}"))
    question = (body.get("question") or "").strip()
    session_id = body.get("session_id", "")
    target_agent = body.get("target_agent", "")

    if not question or not session_id:
        return {"statusCode": 400, "body": "question and session_id required"}

    # Authenticate: derive identity from the verified Cognito ID token (never a
    # client-supplied user_id). Mirrors on_launch.
    claims = _verify_jwt(body.get("token", ""))
    if not claims or not claims.get("sub"):
        push_event(connection_id, event, {"type": "ASK_ERROR", "session_id": session_id, "error": "Unauthorized"})
        return {"statusCode": 401, "body": "Invalid or missing token"}
    user_id = claims["sub"]

    chat_fn = os.environ.get("CHAT_FUNCTION", "")
    if not chat_fn:
        push_event(connection_id, event, {"type": "ASK_ERROR", "session_id": session_id, "error": "chat runtime not configured"})
        return {"statusCode": 409, "body": "chat function not configured"}

    # The WS callback endpoint the chat Lambda uses to push its result back to THIS
    # connection (derived from the request context, same shape push_event uses).
    domain = event.get("requestContext", {}).get("domainName")
    stage = event.get("requestContext", {}).get("stage")
    ws_endpoint = f"https://{domain}/{stage}"

    try:
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=chat_fn,
            InvocationType="Event",  # fire-and-forget; no 29s ceiling
            Payload=json.dumps({
                "async_ws": True,
                "connection_id": connection_id,
                "ws_endpoint": ws_endpoint,
                "session_id": session_id,
                "question": question,
                "target_agent": target_agent,
                "user_id": user_id,
            }).encode("utf-8"),
        )
    except Exception as e:
        print(f"[chat] async dispatch failed: {type(e).__name__}: {e}")
        push_event(connection_id, event, {"type": "ASK_ERROR", "session_id": session_id, "error": "chat dispatch failed"})
        return {"statusCode": 500, "body": "chat dispatch failed"}

    # Optional immediate ack so the UI can show a "thinking" state keyed to the session.
    push_event(connection_id, event, {"type": "ASK_STARTED", "session_id": session_id})
    return {"statusCode": 200}


def push_event(connection_id, event, data):
    """Push event back to a specific WebSocket connection."""
    domain = event.get("requestContext", {}).get("domainName")
    stage = event.get("requestContext", {}).get("stage")
    endpoint = f"https://{domain}/{stage}"

    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)
    apigw.post_to_connection(
        ConnectionId=connection_id,
        Data=json.dumps(data).encode(),
    )


def push_to_session(session_id, data):
    """Push event to all WebSocket connections for a session."""
    resp = connections_table.scan(
        FilterExpression="sessionId = :sid",
        ExpressionAttributeValues={":sid": session_id},
    )
    ws_endpoint = os.environ.get("WS_ENDPOINT", "")
    if not ws_endpoint:
        return
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=ws_endpoint)
    for item in resp.get("Items", []):
        try:
            apigw.post_to_connection(ConnectionId=item["connectionId"], Data=json.dumps(data).encode())
        except Exception:
            pass
