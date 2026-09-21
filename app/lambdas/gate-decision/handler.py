"""POST /api/runs/{sessionId}/gate-decision — operator responds to an ask-mode gate.

Services: AWS Step Functions, Amazon S3.

Receives the operator's decision (continue/retry/stop) and the task token
that was broadcast via WebSocket when the gate fired. Calls SendTaskSuccess
or SendTaskFailure to resume the paused Step Functions run.

Request body:
  {
    "task_token": "...",
    "decision": "continue" | "retry" | "stop",
    "agent_id": "...",
    "gate_type": "citation" | "format" | "output_kind" | "grounding"
  }
"""
import json
import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
# AWS Step Functions client
sfn = boto3.client("stepfunctions", region_name=REGION)
# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
ATTENTION_TABLE = os.environ.get("ATTENTION_TABLE", "")
_attention = boto3.resource("dynamodb", region_name=REGION).Table(ATTENTION_TABLE) if ATTENTION_TABLE else None
GATE_CHAT_MODEL = os.environ.get("GATE_CHAT_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def _token_gone(e) -> bool:
    """True if an SFN callback failed because the task token is no longer
    valid — i.e. the gate was ALREADY resolved (double-click / re-displayed
    dialog) or the task timed out. In that case the operator's intent is already
    satisfied, so we treat the call as an idempotent success instead of erroring."""
    s = str(e)
    return any(x in s for x in ("TaskTimedOut", "Task Timed Out", "TaskDoesNotExist", "InvalidToken"))


def _clear_attention_item(owner_id, session_id, agent_id):
    """Remove the inbox item for a resolved gate. Best-effort."""
    if _attention is None or not session_id or not agent_id:
        return
    try:
        _attention.delete_item(Key={"ownerId": owner_id or "unassigned", "itemId": f"{session_id}#{agent_id}"})
    except Exception:
        pass


# ── Authenticated-browser sign-in (folded into gate-decision) ───────────────
_BROWSER_IDENTIFIER = os.environ.get("BROWSER_IDENTIFIER", "aws.browser.v1")


def _cdp_navigate(client, url):
    """Navigate the browser session's page to `url` via raw Chrome DevTools
    Protocol over the automation websocket (the AgentCore browser is chromeless,
    so there's no address bar to type into). Best-effort — returns the resulting
    page URL, or "" on any failure (the modal shows the URL as a fallback)."""
    import json as _json, time as _t
    conn = None
    try:
        import websocket  # websocket-client
        ws_url, headers = client.generate_ws_headers()
        conn = websocket.create_connection(
            ws_url, header=[f"{k}: {v}" for k, v in headers.items()], timeout=15)
        _id = {"n": 0}

        def _send(method, params=None, session=None):
            _id["n"] += 1
            m = {"id": _id["n"], "method": method, "params": params or {}}
            if session:
                m["sessionId"] = session
            conn.send(_json.dumps(m))
            return _id["n"]

        def _await(pred, budget=8.0):
            end = _t.time() + budget
            while _t.time() < end:
                try:
                    m = _json.loads(conn.recv())
                except Exception:
                    break
                if pred(m):
                    return m
            return None

        gid = _send("Target.getTargets")
        r = _await(lambda m: m.get("id") == gid)
        targets = ((r or {}).get("result") or {}).get("targetInfos") or []
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            return ""
        aid = _send("Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})
        at = _await(lambda m: m.get("id") == aid)
        sess = ((at or {}).get("result") or {}).get("sessionId")
        if not sess:  # sometimes only the event carries it
            ev = _await(lambda m: m.get("method") == "Target.attachedToTarget")
            sess = ((ev or {}).get("params") or {}).get("sessionId")
        if not sess:
            return ""
        _send("Page.enable", session=sess)
        _send("Page.navigate", {"url": url}, session=sess)
        _await(lambda m: m.get("method") == "Page.frameNavigated", budget=6.0)
        return url
    except Exception as e:
        print(f"[browser-login] CDP navigate failed (human can navigate in the view): {type(e).__name__}: {str(e)[:200]}")
        return ""
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _bl_key(session_id, agent_id):
    safe = lambda s, n=128: __import__("re").sub(r"[^A-Za-z0-9_-]", "-", s or "")[:n]  # noqa: E731
    return f"runs/{safe(session_id)}/{safe(agent_id, 64)}/browser_login.json"


def _browser_login(body):
    """action=open → ensure a Browser profile + start a session on it + take
    control + return a 5-min live-view URL. action=done → SaveBrowserSessionProfile
    (commit the signed-in state) + stop + clear the Attention item."""
    action = body.get("action")
    session_id = body.get("session_id", "")
    agent_id = body.get("agent_id", "")
    if not session_id or not agent_id or not RUNS_BUCKET:
        return _response(400, {"error": "session_id and agent_id are required"})
    key = _bl_key(session_id, agent_id)
    try:
        rec = json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read())
    except Exception:
        return _response(404, {"error": "no pending browser_login for this session/agent"})
    if not rec.get("domain"):
        return _response(404, {"error": "browser_login record is missing the site domain"})

    from bedrock_agentcore.tools.browser_client import BrowserClient
    cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
    dp = boto3.client("bedrock-agentcore", region_name=REGION)

    if action == "browser_login_open":
        try:
            profile_id = ""
            for s in (cp.list_browser_profiles(name=rec["profile_name"]).get("profileSummaries") or []):
                if s.get("profileId"):
                    profile_id = s["profileId"]; break
            if not profile_id:
                profile_id = cp.create_browser_profile(
                    name=rec["profile_name"], description="Tessera authenticated-browsing profile")["profileId"]
            client = BrowserClient(REGION)
            client.identifier = _BROWSER_IDENTIFIER
            client.start(identifier=_BROWSER_IDENTIFIER, session_timeout_seconds=1800,
                         viewport={"width": 1440, "height": 900},
                         profile_configuration={"profileIdentifier": profile_id})
            # The AgentCore browser is CHROMELESS (no address bar), so navigate the
            # PAGE directly over CDP (Page.navigate) before handing control to the
            # human — otherwise it sits on Chrome's default page. Raw CDP via
            # websocket-client (Playwright won't fit a zip Lambda). Best-effort.
            nav_url = _cdp_navigate(client, f"https://{rec['domain']}/")
            client.take_control()   # human drives (types creds / SSO / MFA)
            live_view_url = client.generate_live_view_url(expires=300)
            rec.update({"profile_id": profile_id, "browser_identifier": client.identifier,
                        "browser_session_id": client.session_id})
            s3.put_object(Bucket=RUNS_BUCKET, Key=key, Body=json.dumps(rec).encode())
            return _response(200, {"status": "open", "domain": rec["domain"],
                                   "live_view_url": live_view_url, "expires_in_s": 300,
                                   "remote_width": 1440, "remote_height": 900,
                                   "navigated_url": nav_url,
                                   "message": "Sign in in the live view (SSO/MFA fine), then click Done."})
        except Exception as e:
            print(f"[browser-login] open failed: {type(e).__name__}: {e}")
            return _response(500, {"error": f"could not start sign-in session: {str(e)[:300]}"})

    if action == "browser_login_skip":
        # Proceed WITHOUT signing in — resume the paused run with the agent's
        # partial (pre-login) output so downstream still gets the public capture.
        tok = rec.get("task_token", "")
        try:
            if tok:
                sfn.send_task_success(taskToken=tok, output=json.dumps(rec.get("base_payload") or {"agent_id": agent_id}))
        except Exception as e:
            if not _token_gone(e):
                print(f"[browser-login] skip send failed: {e}")
        _clear_attention_item(rec.get("user_id"), session_id, agent_id)
        try:
            s3.delete_object(Bucket=RUNS_BUCKET, Key=key)
        except Exception:
            pass
        return _response(200, {"status": "skipped", "domain": rec["domain"],
                               "message": f"Proceeding without an in-app sign-in for {rec['domain']}."})

    # browser_login_done: commit the signed-in state into the profile, stop, then
    # RESUME the paused run — SendTaskFailure(GateRetry) re-runs the agent, which
    # now starts with the seeded profile and reaches the authenticated surfaces.
    try:
        profile_id, bid, sid = rec.get("profile_id", ""), rec.get("browser_identifier", ""), rec.get("browser_session_id", "")
        if not (profile_id and bid and sid):
            return _response(409, {"error": "no active sign-in session — click 'Open sign-in' first"})
        dp.save_browser_session_profile(profileIdentifier=profile_id, browserIdentifier=bid, sessionId=sid)
        try:
            client = BrowserClient(REGION)
            client.identifier = bid; client.session_id = sid
            client.release_control(); client.stop()
        except Exception as e:
            print(f"[browser-login] release/stop warning: {e}")
        tok = rec.get("task_token", "")
        try:
            if tok:
                sfn.send_task_failure(taskToken=tok, error="GateRetry",
                                      cause=f"browser login complete for {rec['domain']} — re-running authenticated")
        except Exception as e:
            if not _token_gone(e):
                print(f"[browser-login] done GateRetry send failed: {e}")
        _clear_attention_item(rec.get("user_id"), session_id, agent_id)
        try:
            s3.delete_object(Bucket=RUNS_BUCKET, Key=key)
        except Exception:
            pass
        return _response(200, {"status": "done", "domain": rec["domain"], "profile_saved": True,
                               "message": f"Signed in to {rec['domain']}. Re-running that step authenticated…"})
    except Exception as e:
        print(f"[browser-login] done failed: {type(e).__name__}: {e}")
        return _response(500, {"error": f"could not save the sign-in profile: {str(e)[:300]}"})


def _gate_chat(body):
    """One turn of a multi-turn discussion about a gate, before the operator
    decides. Concise, conversational; uses the verdict/weaknesses as context."""
    msg = (body.get("message") or "").strip()
    history = body.get("history") or []
    ctx = {
        "verdict": body.get("verdict"),
        "weaknesses": (body.get("weaknesses") or "")[:4000],
        "gate_type": body.get("gate_type"),
        "agent_id": body.get("agent_id"),
        "checkpoint_layer": body.get("checkpoint_layer"),
    }
    system = (
        "You are helping a human operator decide on a Tessera quality gate. Be CONCISE "
        "(at most 4 sentences), conversational and concrete. Use the verdict and weaknesses "
        "as context to answer the operator's questions. When useful, recommend whether to "
        "Accept (proceed), Loop (re-run the layer with corrective guidance), or Abort — and "
        "what specific guidance to give on Loop. Do NOT dump the full weakness list back.\n\n"
        "CRITICAL: You CANNOT take any action. You do NOT Accept, Loop, Abort, continue, or "
        "resolve this gate — only the operator can, by clicking the Accept / Loop / Abort "
        "buttons below the chat. NEVER say 'I'll accept', \"I'll continue\", 'let the workflow "
        "continue', 'moving forward', or anything implying you performed or will perform an "
        "action. When the operator signals a choice, simply confirm your recommendation and "
        "tell them to click the matching button to confirm (e.g. \"Sounds reasonable — click "
        "Accept below to proceed\")."
    )
    user = json.dumps({"gate": ctx, "history": history[-8:], "operator_message": msg})
    try:
        resp = bedrock.invoke_model(
            modelId=GATE_CHAT_MODEL, contentType="application/json", accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31", "max_tokens": 600,
                "system": system, "messages": [{"role": "user", "content": user}],
            }),
        )
        text = json.loads(resp["body"].read())["content"][0]["text"].strip()
        return _response(200, {"reply": text})
    except Exception as e:
        return _response(500, {"error": str(e)[:300]})


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON"})

    # Multi-turn discussion about the gate (no token resolution).
    if body.get("action") == "chat":
        return _gate_chat(body)

    # Authenticated-browser sign-in (folded in — no separate Lambda/route, to
    # stay under CloudFormation's 500-resource stack limit). Seeds an AgentCore
    # Browser profile via a human live-view login. See docs/features/browser-tool.md.
    if body.get("action") in ("browser_login_open", "browser_login_done", "browser_login_skip"):
        return _browser_login(body)

    task_token = body.get("task_token", "").strip()
    decision = body.get("decision", "").strip()
    agent_id = body.get("agent_id", "")
    gate_type = body.get("gate_type", "")
    payload_key = body.get("payload_key", "")

    if not task_token:
        return _response(400, {"error": "task_token is required"})
    if decision not in ("continue", "retry", "stop"):
        return _response(400, {"error": "decision must be continue, retry, or stop"})

    session_id = body.get("session_id", "")

    # Operator corrective guidance entered at the gate. Persist it to a
    # run-global, accumulating file so the agent-invoker injects it into every
    # subsequent agent invocation (the looped layer on retry, downstream agents
    # on continue) IN ADDITION to the original user request. Applies to all gate
    # types, not just the challenger.
    operator_input = (body.get("operator_input") or "").strip()
    if operator_input and RUNS_BUCKET and session_id:
        guidance_key = f"runs/{session_id}/_operator_guidance.txt"
        existing = ""
        try:
            existing = s3.get_object(Bucket=RUNS_BUCKET, Key=guidance_key)["Body"].read().decode("utf-8")
        except Exception:
            existing = ""
        entry = f"[at {agent_id or gate_type or 'gate'} — operator chose {decision}]\n{operator_input}"
        combined = (existing.rstrip() + "\n\n" + entry) if existing.strip() else entry
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=guidance_key, Body=combined.encode("utf-8"), ContentType="text/plain; charset=utf-8")
        except Exception:
            pass

    # Per-level checkpoint gate: the operator's choice maps to a routing
    # decision the CheckpointChoice state reads from the task output —
    #   continue → Accept (proceed to next layer)
    #   retry    → Loop   (re-run this layer with the challenger's feedback)
    #   stop     → Abort  (fail the run)
    # IMPORTANT: a checkpoint can trip EITHER the challenger-verdict gate OR a
    # generic output_kind/format gate (e.g. when it fails to emit a valid
    # ## VERDICT). BOTH must resolve through this path — the downstream
    # `CheckpointChoice{N}` state reads `$.checkpoint{N}.decision`, so the
    # generic resolution (which omits `decision`) would crash the run with
    # "Invalid path '$.checkpointN.decision'". Detect checkpoints by id too.
    is_checkpoint = gate_type == "challenger" or agent_id.startswith("checkpoint_")
    print(f"[gate-decision] session={session_id} agent={agent_id} gate_type={gate_type} decision={decision} is_checkpoint={is_checkpoint}")
    if is_checkpoint:
        gate_meta = {}
        if RUNS_BUCKET and session_id and agent_id:
            try:
                obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent_id}/gate_decision.json")
                gate_meta = json.loads(obj["Body"].read())
            except Exception:
                gate_meta = {}
        checkpoint_layer = gate_meta.get("checkpoint_layer")
        # Generic gates (output_kind/format) don't store checkpoint_layer — derive
        # it from the agent id (checkpoint_0 → 0) so the output is well-formed.
        if checkpoint_layer is None and agent_id.startswith("checkpoint_"):
            try:
                checkpoint_layer = int(agent_id.rsplit("_", 1)[-1])
            except (ValueError, IndexError):
                checkpoint_layer = None
        base_payload = gate_meta.get("base_payload") or {}
        try:
            if decision == "stop":
                sfn.send_task_failure(
                    taskToken=task_token, error="GateStop",
                    cause=f"Operator aborted run at checkpoint (layer {checkpoint_layer})",
                )
            else:
                out = dict(base_payload)
                out["checkpoint_layer"] = checkpoint_layer
                if decision == "continue":  # Accept
                    out["decision"] = "proceed"
                    # Operator accepted despite the verdict — clear the layer's
                    # feedback so a later clean re-run doesn't re-inject it.
                    if RUNS_BUCKET and checkpoint_layer is not None:
                        try:
                            s3.delete_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_checkpoint/{checkpoint_layer}.txt")
                        except Exception:
                            pass
                else:  # retry → Loop (feedback file left in place for the re-run)
                    out["decision"] = "loop"
                sfn.send_task_success(taskToken=task_token, output=json.dumps(out))
        except Exception as e:
            if _token_gone(e):
                # Gate already resolved (re-displayed dialog / double-click) — treat
                # as success and fall through to cleanup so the dialog closes cleanly.
                print(f"[gate-decision] session={session_id} agent={agent_id} checkpoint token already resolved/expired — idempotent success (no-op)")
            else:
                print(f"[gate-decision] session={session_id} agent={agent_id} checkpoint SFN callback FAILED: {e}")
                return _response(500, {"error": f"SFN callback failed: {e}"})

        print(f"[gate-decision] session={session_id} agent={agent_id} checkpoint resolved -> {'abort' if decision=='stop' else ('proceed' if decision=='continue' else 'loop')}")
        if RUNS_BUCKET and session_id and agent_id:
            try:
                s3.delete_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent_id}/gate_decision.json")
            except Exception:
                pass
        # Clear the async inbox item (resolved by the operator).
        _clear_attention_item(gate_meta.get("ownerId"), session_id, agent_id)
        return _response(200, {"decision": decision, "agent_id": agent_id, "gate_type": gate_type})

    try:
        if decision == "continue":
            # Read the saved partial payload so downstream gets real agent output
            output_payload = {"gate_override": True, "agent_id": agent_id, "gate_type": gate_type}
            if payload_key and RUNS_BUCKET:
                try:
                    obj = s3.get_object(Bucket=RUNS_BUCKET, Key=payload_key)
                    output_payload = json.loads(obj["Body"].read())
                    output_payload["gate_override"] = True
                except Exception:
                    pass  # fall back to stub
            sfn.send_task_success(taskToken=task_token, output=json.dumps(output_payload))
        elif decision == "retry":
            sfn.send_task_failure(
                taskToken=task_token,
                error="GateRetry",
                cause=f"Operator requested retry for {agent_id} ({gate_type})",
            )
        elif decision == "stop":
            sfn.send_task_failure(
                taskToken=task_token,
                error="GateStop",
                cause=f"Operator stopped run at {agent_id} ({gate_type})",
            )
    except Exception as e:
        if _token_gone(e):
            # Gate already resolved (re-displayed dialog / double-click) — idempotent
            # success; fall through to cleanup so the dialog closes cleanly.
            print(f"[gate-decision] session={session_id} agent={agent_id} gate_type={gate_type} token already resolved/expired — idempotent success (no-op)")
        else:
            print(f"[gate-decision] session={session_id} agent={agent_id} gate_type={gate_type} SFN callback FAILED: {e}")
            return _response(500, {"error": f"SFN callback failed: {e}"})
    print(f"[gate-decision] session={session_id} agent={agent_id} gate_type={gate_type} resolved -> {decision}")

    # Clean up the gate_decision.json so polling stops showing it
    session_id = body.get("session_id", "")
    if RUNS_BUCKET and session_id and agent_id:
        # Read ownerId from the gate file (to clear the matching inbox item) before deleting.
        owner = None
        try:
            gm = json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent_id}/gate_decision.json")["Body"].read())
            owner = gm.get("ownerId")
        except Exception:
            owner = None
        try:
            s3.delete_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent_id}/gate_decision.json")
        except Exception:
            pass
        _clear_attention_item(owner, session_id, agent_id)

    return _response(200, {"decision": decision, "agent_id": agent_id, "gate_type": gate_type})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }
