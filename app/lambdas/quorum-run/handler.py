"""quorum-run — runtime entrypoint for Quorum-archetype ecosystems.

A Quorum eco does not compile to Step Functions (see save-ecosystem / spec §5); it runs
HERE: convene → deliberate → verdict, streaming AG-UI-style events to the client WebSocket
and persisting the replayable artifacts (plan + transcript + verdict) to the runs bucket.

Invoke event (async, from the launch seam or a direct test invoke):
  {"session_id","ecosystem_id","decision","context","user_id"}

Packaging: deploy-lambdas vendors `tessera_platform/quorum/` (and `tessera_platform/catalog/`
for the live persona pool) into this zip, the same way agent-invoker vendors shared code.
Everything is best-effort: streaming/persist failures never abort the deliberation.
"""
import json
import os
import time

import boto3
import yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS_BUCKET = os.environ.get("RUNS_BUCKET", "")
ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "")

s3 = boto3.client("s3", region_name=REGION)


# --- streaming to the client WebSocket (mirrors agent-invoker) -------------------------
def _connections(session_id):
    if not CONNECTIONS_TABLE:
        return []
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        tbl = ddb.Table(CONNECTIONS_TABLE)
        out = []
        for item in tbl.scan().get("Items", []):
            if session_id in (item.get("sessionIds") or set()):
                out.append(item["connectionId"])
        return out
    except Exception as e:
        print(f"[quorum-run] connection lookup failed: {type(e).__name__}: {e}")
        return []


def _make_emitter(session_id):
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT) if WS_ENDPOINT else None
    conns = _connections(session_id) if WS_ENDPOINT else []

    def emit(event: dict):
        event = {**event, "session_id": session_id}
        print(f"[quorum-run] event {event.get('type')}")
        if not apigw:
            return
        for cid in list(conns):
            try:
                apigw.post_to_connection(ConnectionId=cid, Data=json.dumps(event).encode("utf-8"))
            except Exception:
                pass  # stale connection; never break the run
    return emit


def _persist(session_id, name, obj):
    if not RUNS_BUCKET:
        return
    try:
        s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{name}",
                      Body=json.dumps(obj, indent=2).encode("utf-8"), ContentType="application/json")
    except Exception as e:
        print(f"[quorum-run] persist {name} failed: {type(e).__name__}: {e}")


def _load_config(ecosystem_id):
    if not (ECOSYSTEMS_BUCKET and ecosystem_id):
        return {}
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{ecosystem_id}.yaml")
        return yaml.safe_load(obj["Body"].read()) or {}
    except Exception as e:
        print(f"[quorum-run] config load failed: {type(e).__name__}: {e}")
        return {}


def _load_personas():
    """Prefer the live scrutineer catalog; fall back to the bundled seed pool."""
    from tessera_platform.quorum import personas as pp
    try:
        pool = pp.from_catalog()
        if pool:
            return pool
    except Exception as e:
        print(f"[quorum-run] catalog personas unavailable ({type(e).__name__}); using seed pool")
    # Bundled seed shipped alongside this handler (works before the catalog is ingested).
    bundled = os.path.join(os.path.dirname(__file__), "scrutineers.json")
    if os.path.exists(bundled):
        return pp.from_seed_file(path=bundled)
    return pp.from_seed_file()


def _load_attachments_text(session_id, max_chars=120_000):
    """Fold the FULL text of any uploaded run attachments into the panel's context. Reuses the
    existing extraction pipeline: file-classify already wrote <key>.extracted.txt for PDFs/docs.
    Reads runs/<sid>/_attachments.json (name+ref) and pulls each extracted text. Best-effort."""
    if not RUNS_BUCKET:
        return ""
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_attachments.json")
        atts = json.loads(obj["Body"].read()) or []
    except Exception:
        return ""
    parts, used = [], 0
    for a in atts:
        ref = str(a.get("ref", ""))
        name = a.get("name", "attachment")
        if not ref.startswith("s3://"):
            continue
        bkt, _, key = ref[5:].partition("/")
        text = ""
        for candidate in (key + ".extracted.txt", key):  # prefer extracted text; fall back to raw
            try:
                o = s3.get_object(Bucket=bkt, Key=candidate)
                text = o["Body"].read().decode("utf-8", "ignore")
                if candidate.endswith(".extracted.txt"):
                    break
            except Exception:
                continue
        text = (text or "").strip()
        if not text:
            continue
        room = max_chars - used
        if room <= 0:
            break
        snippet = text[:room]
        used += len(snippet)
        parts.append(f"=== UPLOADED DOCUMENT: {name} ===\n{snippet}")
    return ("\n\n".join(parts)).strip()


def _build_surface(session_id, result, pool):
    """Compose the deliberation into a single A2UI surface using the Quorum widget
    extensions (MemberBench/ArgumentThread/DissentRail/ExclusionTray/CoverageMeter/
    VerdictRuling), so the existing generic A2UI renderer draws it. No per-eco UI code."""
    by_id = {p.persona_id: p for p in pool}
    plan, t, v = result.plan, result.transcript, result.verdict
    members = [{"persona_id": pid,
                "name": getattr(by_id.get(pid), "name", pid),
                "stance": getattr(by_id.get(pid), "stance", ""),
                "summoned": pid in plan.summoned, "state": "silent"} for pid in plan.seated]
    entries = [e.to_dict() if hasattr(e, "to_dict") else e for e in t.entries]
    return {
        "surfaceId": f"quorum-{session_id}",
        "components": [
            {"id": "root", "component": "Column",
             "children": ["bench", "tray", "cov", "thread", "dissent", "verdict"]},
            {"id": "bench", "component": "MemberBench", "members": members},
            {"id": "tray", "component": "ExclusionTray",
             "excluded": plan.excluded, "justification": plan.justification},
            {"id": "cov", "component": "CoverageMeter",
             "covered": plan.covered_dimensions, "missing": plan.missing_dimensions},
            {"id": "thread", "component": "ArgumentThread", "entries": entries},
            {"id": "dissent", "component": "DissentRail", "dissents": v.dissent_preserved},
            {"id": "verdict", "component": "VerdictRuling", "grade": v.grade, "summary": v.summary,
             "against_whom": v.against_whom, "indefensible_elements": v.indefensible_elements,
             "dissent_preserved": v.dissent_preserved, "composition_summary": v.composition_summary},
        ],
    }


def _save_session(session_id, st, ecosystem_id=""):
    if not RUNS_BUCKET:
        return
    try:
        body = {"ecosystem_id": ecosystem_id, "state": st.to_dict()}
        s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_session.json",
                      Body=json.dumps(body).encode("utf-8"), ContentType="application/json")
    except Exception as e:
        print(f"[quorum-run] save session failed: {type(e).__name__}: {e}")


def _load_session(session_id):
    from tessera_platform.quorum.session import SessionState
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_session.json")
        body = json.loads(obj["Body"].read())
        return SessionState.from_dict(body["state"]), body.get("ecosystem_id", "")
    except Exception as e:
        print(f"[quorum-run] load session failed: {type(e).__name__}: {e}")
        return None, ""


def _config_hash(session_id):
    """Read back the hash recorded at launch, so config and outcome are joinable."""
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_run_config.json")
        return json.loads(obj["Body"].read()).get("config_hash", "")
    except Exception:
        return ""


def _finalize(session_id, st, speaker, emit, ecosystem_id):
    """Render the verdict, persist the replayable artifacts, and emit the A2UI surface."""
    from tessera_platform.quorum import session as S
    from tessera_platform.quorum.models import QuorumResult
    v = S.render_verdict(st, speaker, emit)
    result = QuorumResult(decision_ref=(ecosystem_id or "quorum") + "-run",
                          plan=st.plan, transcript=st.transcript, verdict=v)
    _persist(session_id, "_quorum_plan.json", st.plan.to_dict())
    _persist(session_id, "_deliberation.json", st.transcript.to_dict())
    _persist(session_id, "_verdict.json", v.to_dict())
    _persist(session_id, "_quorum_result.json", result.to_dict())
    # The outcome half of a comparable run (quorum/index.html §12.3): the same measures the regression
    # harness uses, so a live run and a test run can be read on the same axes. Paired with
    # _run_config.json by config_hash.
    try:
        from tessera_platform.quorum import metrics as QM
        m = QM.collect(st, config_hash=_config_hash(session_id))
        _persist(session_id, "_run_metrics.json", m)
        emit({"type": "QUORUM_METRICS", **{k: m[k] for k in ("participation", "agenda",
                                                             "human_reach", "board", "cost")}})
        print(f"[quorum-run] metrics: {json.dumps(m['cost'])} {json.dumps(m['participation'])}")
    except Exception as e:                     # measurement must never break the meeting
        print(f"[quorum-run] metrics failed: {type(e).__name__}: {e}")
    emit({"type": "STATE_DELTA", "agent": "quorum",
          "a2ui": _build_surface(session_id, result, list(st.personas.values()))})
    emit({"type": "RUN_FINISHED", "ecosystem": ecosystem_id, "verdict": v.grade})


def _acquire_lock(session_id, ttl=120):
    """Per-session mutex via S3 create-if-absent (IfNoneMatch). Ensures only ONE turn runs at a
    time for a session, so concurrent invocations can't interleave streaming or clobber the
    transcript. Stale locks (older than ttl) are stolen."""
    if not RUNS_BUCKET:
        return True
    key = f"runs/{session_id}/_lock.json"
    now = time.time()
    try:
        s3.put_object(Bucket=RUNS_BUCKET, Key=key, Body=json.dumps({"ts": now}).encode("utf-8"), IfNoneMatch="*")
        return True
    except Exception:
        pass  # lock exists (or IfNoneMatch unsupported) — check staleness
    try:
        o = s3.get_object(Bucket=RUNS_BUCKET, Key=key)
        if now - float(json.loads(o["Body"].read()).get("ts", 0)) > ttl:
            s3.delete_object(Bucket=RUNS_BUCKET, Key=key)
            try:
                s3.put_object(Bucket=RUNS_BUCKET, Key=key, Body=json.dumps({"ts": now}).encode("utf-8"), IfNoneMatch="*")
                return True
            except Exception:
                return False
    except Exception:
        pass
    return False


def _release_lock(session_id):
    if not RUNS_BUCKET:
        return
    try:
        s3.delete_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/_lock.json")
    except Exception:
        pass


def _handle_session(event):
    """Interactive deliberation-room dispatch (spec-quorum-deliberation-room.md). Each command
    is a fresh invocation resuming from runs/<sid>/_session.json — the human is in the room."""
    from tessera_platform.quorum import session as S
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker
    session_id = event.get("session_id") or "quorum-adhoc"
    cmd = event.get("command")
    emit = _make_emitter(session_id)
    speaker = BedrockSpeaker()
    try:
        if cmd == "start":
            ecosystem_id = event.get("ecosystem_id", "")
            decision = event.get("decision") or event.get("mission") or ""
            ctx = event.get("context", "")
            config = _load_config(ecosystem_id) or {"name": ecosystem_id or "quorum"}
            # Fold the full text of any uploaded documents into the context so the panel can
            # actually review them (reuses file-classify's <key>.extracted.txt). 
            doc_text = _load_attachments_text(session_id)
            if doc_text:
                ctx = (ctx + "\n\n" if ctx else "") + doc_text
            # Advanced-launch settings (quorum/index.html §12). Resolved from DEFAULTS + the eco's
            # quorum block + these overrides, validated FAIL-CLOSED, and recorded with the run so two
            # runs can be compared and an outcome attributed to a setting.
            overrides = event.get("settings") or {}
            from tessera_platform.quorum import settings as QS
            resolved = QS.resolve(config, overrides)
            problems = QS.validate(resolved)
            # Every configured model must be invokable BEFORE a panel is convened: a balanced room
            # otherwise seats six members and dies on the first turn that lands on a dead model.
            if not problems:
                problems = QS.models_usable(QS.configured_models(resolved), speaker.probe)
            if problems:
                print(f"[quorum-run] REFUSED launch — invalid run settings: {problems}")
                emit({"type": "RUN_ERROR", "error": "invalid run settings: " + "; ".join(problems[:4])})
                return {"ok": False, "error": "invalid settings", "problems": problems}
            # The model actually used is the most consequential variable in the run; record it
            # RESOLVED (the env default when the setting is blank), or two runs on different models
            # read as identical.
            if not (resolved.get("models") or {}).get("default"):
                resolved.setdefault("models", {})["default"] = getattr(speaker, "model_id", "")
            cfg_record = QS.record(resolved, overrides, os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""))
            emit({"type": "RUN_STARTED", "ecosystem": ecosystem_id, "archetype": "quorum",
                  "interactive": True, "config_hash": cfg_record["config_hash"],
                  "settings_changed": cfg_record["changed_from_defaults"]})
            st = S.convene(session_id, config, decision, ctx, _load_personas(), speaker, emit,
                           settings_overrides=overrides)
            # Written AFTER convene so the record includes which model ended up in which seat — a
            # balanced room is only reproducible if the resolved assignment is kept, not just the rule.
            cfg_record["seat_models"] = dict(getattr(st, "seat_models", {}) or {})
            # Capabilities learned while probing must travel with the session, not just this process.
            st.model_caps = {"no_temperature": sorted(getattr(speaker, "_no_temperature", set()) or set())}
            cfg_record["model_caps"] = st.model_caps
            _persist(session_id, "_run_config.json", cfg_record)
            _save_session(session_id, st, ecosystem_id)
            return {"ok": True, "phase": st.phase}

        # Serialize all mutating commands per session so two turns can NEVER run concurrently
        # (that caused interleaved/broken streaming and duplicated messages). advance drops when
        # busy; human commands wait briefly then proceed so input is never lost.
        acquired = _acquire_lock(session_id)
        if not acquired:
            if cmd == "advance":
                # ALWAYS emit a terminal event: a silent no-op leaves the client's single-flight
                # lock stuck until its watchdog fires (that was a stall source).
                emit({"type": "QUORUM_STATE", "outcome": "busy", "command": cmd})
                return {"ok": True, "skipped": "busy"}
            for _ in range(20):
                time.sleep(0.5)
                if _acquire_lock(session_id):
                    acquired = True
                    break
        try:
            st, ecosystem_id = _load_session(session_id)
            if st is None:
                if cmd == "sync":
                    # A client can ask to resync before `start` has created the session (mount race).
                    # That is NOT an error — tell it we're still coming up.
                    emit({"type": "QUORUM_STATE", "outcome": "pending", "command": "sync"})
                    return {"ok": True, "phase": "pending"}
                emit({"type": "RUN_ERROR", "error": "no active deliberation session"})
                return {"ok": False, "error": "no session"}
            if st.phase in ("done", "verdict") and cmd != "sync":
                # Session already ended — stop any client still polling (prevents runaway invokes).
                emit({"type": "RUN_FINISHED", "ecosystem": ecosystem_id, "verdict": getattr(st.verdict, "grade", None)})
                return {"ok": True, "phase": st.phase}

            if cmd == "sync":
                # Resync a (re)connected client from authoritative server state. No model calls.
                emit({"type": "QUORUM_SYNC", "phase": st.phase, "agenda": st.agenda,
                      "owed_reply": st.owed_reply, "owed_question": st.owed_question,
                      "plan": st.plan.to_dict(),
                      "members": [{"persona_id": p, **({"name": st.personas[p].name,
                                                        "stance": st.personas[p].stance}
                                                       if p in st.personas else {})}
                                  for p in st.plan.seated],
                      "entries": [e.to_dict() for e in st.transcript.entries],
                      "verdict": st.verdict.to_dict() if st.verdict else None})
                S.whiteboard_state_event(st, emit)   # rebuild the board on (re)connect
                S.floor_state_event(st, emit)        # and who holds the floor (never inferred)
                return {"ok": True, "phase": st.phase}
            elif cmd == "empanel":
                S.apply_empanel(st, event.get("empanel") or {}, speaker, emit)
            elif cmd == "advance":
                S.step(st, speaker, emit)
            elif cmd == "request_floor":
                S.request_floor(st, emit)            # the human raised a hand
            elif cmd == "release_floor":
                S.release_floor(st, emit)            # hand lowered — the panel may resume
            elif cmd == "wb_draw":
                S.submitter_draw(st, event.get("ops") or [], emit)
            elif cmd == "wb_erase":
                S.resolve_erase(st, event.get("op_id", ""), True, emit)
            elif cmd == "wb_erase_consent":
                S.resolve_erase(st, event.get("op_id", ""), bool(event.get("approve")), emit)
            elif cmd == "human_say":
                S.inject_human(st, event.get("text", ""), event.get("target"),
                               event.get("entry_type", "question"), emit,
                               received_at=event.get("received_at"))
            elif cmd == "summon":
                ids = event.get("ids") or ([event.get("id")] if event.get("id") else [])
                S.apply_empanel(st, {"summon": ids}, speaker, emit)
            elif cmd in ("rule_now", "verdict"):
                _finalize(session_id, st, speaker, emit, ecosystem_id)
            elif cmd in ("stop", "end", "kill"):
                st.phase = "done"                     # end the session WITHOUT rendering a verdict
                emit({"type": "RUN_FINISHED", "ecosystem": ecosystem_id, "stopped": True})
            else:
                emit({"type": "RUN_ERROR", "error": f"unknown command {cmd}"})

            _save_session(session_id, st, ecosystem_id)
            # Terminal marker for EVERY command, so the client always has something to react to
            # (a yielded turn or a converge-with-no-entry used to emit nothing at all).
            emit({"type": "QUORUM_STATE", "outcome": "ok", "command": cmd, "phase": st.phase,
                  "turns": st.turns, "owed_reply": st.owed_reply})
            return {"ok": True, "phase": st.phase}
        finally:
            if acquired:
                _release_lock(session_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        emit({"type": "RUN_ERROR", "error": f"{type(e).__name__}: {e}"})
        return {"ok": False, "error": str(e)}


def handler(event, context=None):
    # Interactive Deliberation Room: commands resume a persisted, streaming session.
    if event.get("command"):
        return _handle_session(event)

    # Legacy/headless BATCH path (direct test invoke without a command) — kept intact.
    session_id = event.get("session_id") or "quorum-adhoc"
    ecosystem_id = event.get("ecosystem_id", "")
    decision = event.get("decision") or event.get("mission") or ""
    ctx = event.get("context", "")

    from tessera_platform.quorum.engine import run_quorum
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker

    config = _load_config(ecosystem_id) or {"name": ecosystem_id or "quorum"}
    emit = _make_emitter(session_id)
    emit({"type": "RUN_STARTED", "ecosystem": ecosystem_id, "archetype": "quorum"})

    try:
        pool = _load_personas()
        result = run_quorum(config, decision, ctx, pool, BedrockSpeaker(), emit)
        _persist(session_id, "_quorum_plan.json", result.plan.to_dict())
        _persist(session_id, "_deliberation.json", result.transcript.to_dict())
        _persist(session_id, "_verdict.json", result.verdict.to_dict())
        _persist(session_id, "_quorum_result.json", result.to_dict())
        # Render the deliberation into the UI via a generative A2UI surface.
        emit({"type": "STATE_DELTA", "agent": "quorum", "a2ui": _build_surface(session_id, result, pool)})
        emit({"type": "RUN_FINISHED", "ecosystem": ecosystem_id, "verdict": result.verdict.grade})
        return {"ok": True, "session_id": session_id, "verdict": result.verdict.grade}
    except Exception as e:
        import traceback
        traceback.print_exc()
        emit({"type": "RUN_ERROR", "ecosystem": ecosystem_id, "error": f"{type(e).__name__}: {e}"})
        return {"ok": False, "error": str(e)}
