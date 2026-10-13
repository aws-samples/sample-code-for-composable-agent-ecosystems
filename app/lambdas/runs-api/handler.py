"""Runs API Lambda — list past AWS Step Functions executions per ecosystem and

Services: AWS Step Functions, Amazon S3.
reconstruct full per-agent run data from SFN history + S3 outputs.

Routes:
  GET  /api/ecosystems/{id}/runs                      → list recent executions
  GET  /api/ecosystems/{id}/runs/{execName}           → reconstructed snapshot
  POST /api/ecosystems/{id}/runs/{execName}/redrive   → resume failed exec from
                                                        the failed task

Phase 5.2 closed the past-runs viewer gap. Phase 5.4 (this revision) adds:
  - error / cause / redriveStatus / redriveCount on the detail response so
    the UI can render an actionable failure banner instead of pretending
    every run "completed".
  - failed_agent identification by walking history for TaskFailed /
    TaskTimedOut events; lets the UI mark the failed node distinctly in the
    DAG instead of showing it as still-pending.
  - skipped_agents — agents present in the ecosystem YAML that never ran
    because a sibling parallel branch failed first; these should render as
    "skipped" in the DAG, not "complete".
  - POST /redrive route that calls stepfunctions:RedriveExecution. Eligible
    only when status in (FAILED, TIMED_OUT, ABORTED) and redriveStatus is  # Note: ABORTED is an AWS Step Functions API status value (style guide exception — official API terminology)
    REDRIVABLE. Otherwise returns 409 with the reason from SFN.

No silent fallbacks. If config is missing, the Lambda raises so operators see
the real problem.
"""
import json
import os
import re
import sys
import uuid
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import require_auth, is_admin
import pricing  # shared model price table / cost estimate (spec-run-tuning §5.1)

import boto3
import yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
ENVIRONMENT = os.environ.get("TESSERA_ENVIRONMENT", "dev")
PROJECT = os.environ.get("TESSERA_PROJECT", "tessera")
ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")

# AWS Step Functions client
sfn = boto3.client("stepfunctions", region_name=REGION)
# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
ATTENTION_TABLE = os.environ.get("ATTENTION_TABLE", "")
_attention = boto3.resource("dynamodb", region_name=REGION).Table(ATTENTION_TABLE) if ATTENTION_TABLE else None


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(body, default=str),
    }


def _exec_name_from_path(path: str):
    """Derive the run execName from the raw request path when the request is routed
    via the runs/{proxy+} greedy integration (execName is then not a named path
    param). Returns None for the collection (/runs) and the /runs/recommendation
    sibling. Explicit-route requests still populate execName as a named param, so
    this is a superset — behavior is unchanged for both routings."""
    if not path or "/runs/" not in path:
        return None
    seg = path.split("/runs/", 1)[1].split("/", 1)[0].strip()
    if not seg or seg == "recommendation":
        return None
    from urllib.parse import unquote
    return unquote(seg)


def _get_ecosystem_owner(eco_id: str) -> str:
    """Read owner field from ecosystem YAML. Returns empty string if not found."""
    if not ECOSYSTEMS_BUCKET:
        return ""
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        body = obj["Body"].read().decode("utf-8", errors="replace")
        cfg = yaml.safe_load(body) or {}
        return cfg.get("owner", "")
    except Exception:
        return ""


def _presign_attachment(exec_name: str, name):
    """Presigned INLINE GET URL for an uploaded run INPUT file, so both the technical
    and business run views can open the original document. Resolves name→ref from
    runs/<sid>/_attachments.json ([{name, ref(s3Uri)}]) — the same manifest agent-invoker
    uses to presign evidence-doc citations."""
    if not name:
        return _resp(400, {"error": "name query param required"})
    try:
        atts = json.loads(s3.get_object(
            Bucket=RUNS_BUCKET, Key=f"runs/{_safe_seg(exec_name)}/_attachments.json")["Body"].read()) or []
    except Exception:
        atts = []
    ref = ""
    for a in atts:
        if isinstance(a, dict) and a.get("name") == name:
            ref = a.get("ref") or ""
            break
    if not ref:
        return _resp(404, {"error": "attachment not found"})
    # ref is an s3://bucket/key URI (falls back to the runs bucket + bare key).
    if ref.startswith("s3://"):
        bucket, _, key = ref[len("s3://"):].partition("/")
    else:
        bucket, key = RUNS_BUCKET, ref
    if not (bucket and key):
        return _resp(404, {"error": "attachment ref malformed"})
    import mimetypes
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket, "Key": key,
                "ResponseContentDisposition": f'inline; filename="{name}"',
                "ResponseContentType": ctype,
            },
            ExpiresIn=3600,
        )
    except Exception as e:
        return _resp(500, {"error": f"presign failed: {e}"})
    return _resp(200, {"url": url, "name": name})


def handler(event, context):
    try:
        user = require_auth(event)
    except PermissionError:
        return _resp(401, {"error": "Unauthorized"})

    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = event.get("path", "") or event.get("rawPath", "")
    path_params = event.get("pathParameters") or {}
    eco_id = path_params.get("id", "")
    exec_name = path_params.get("execName") or _exec_name_from_path(path)
    # 'recommendation' is a static run-collection route, not a run. It resolves via
    # the {execName} slot (no run is ever named 'recommendation'), so neutralize it
    # here → the recommendation dispatch below (which keys off `not exec_name`) fires.
    if exec_name == "recommendation":
        exec_name = None

    # User-scoped Attention inbox (not eco-scoped): GET /api/attention
    if path.rstrip("/").endswith("/attention"):
        return _list_attention(user["user_id"])

    if not eco_id:
        return _resp(400, {"error": "missing ecosystem id in path"})

    # Ownership check: user must be owner or admin
    if not is_admin(user):
        owner = _get_ecosystem_owner(eco_id)
        if owner and owner != user["user_id"]:
            return _resp(403, {"error": "Access denied"})

    try:
        # Ecosystem Evaluation Harness (spec §5, Phase 5 UI) — launch/poll the
        # per-eco Eval State Machine. Rides the existing greedy runs proxy under
        # an `_eval` sentinel execName (no new API resources — the platform stack
        # is at CloudFormation's 500-resource ceiling):
        #   POST /api/ecosystems/{id}/runs/_eval/launch → StartExecution of the Eval SM
        #   GET  /api/ecosystems/{id}/runs/_eval/status → latest eval-run status + scorecard
        if exec_name == "_eval":
            if method == "POST" or path.rstrip("/").endswith("/launch"):
                try:
                    _lbody = json.loads(event.get("body") or "{}")
                except Exception:
                    _lbody = {}
                return _eval_launch(eco_id, _lbody if isinstance(_lbody, dict) else {})
            return _eval_status(eco_id)
        # POST /runs/{execName}/redrive — initiate redrive
        if method == "POST" and exec_name and "redrive" in path:
            return _redrive(eco_id, exec_name)
        # POST /runs/{execName}/resume — resume DAG from a selected state (failed OR
        # succeeded), reusing this run's completed layers. spec: resume-from-state.
        if method == "POST" and exec_name and path.rstrip("/").endswith("/resume"):
            try:
                rbody = json.loads(event.get("body") or "{}")
            except Exception:
                rbody = {}
            return _resume(eco_id, exec_name, rbody, user)
        # GET /runs/{execName}/manifest — run config + telemetry ledger (spec-run-tuning §5)
        if exec_name and path.rstrip("/").endswith("/manifest"):
            return _manifest(eco_id, exec_name)
        # GET /runs/{execName}/attachment?name=<name> — presigned inline URL to an uploaded
        # INPUT file, so the technical + business run views can open the source document.
        if method == "GET" and exec_name and path.rstrip("/").endswith("/attachment"):
            return _presign_attachment(exec_name, (event.get("queryStringParameters") or {}).get("name"))
        # POST /runs/{execName}/rating — human quality rating (spec-run-tuning §6.3)
        if method == "POST" and exec_name and path.rstrip("/").endswith("/rating"):
            try:
                rbody = json.loads(event.get("body") or "{}")
            except Exception:
                rbody = {}
            return _rating(eco_id, exec_name, rbody)
        # ── spec-quality-feedback-loop: per-run feedback + section marks ──────
        def _body():
            try:
                return json.loads(event.get("body") or "{}")
            except Exception:
                return {}
        if exec_name and path.rstrip("/").endswith("/feedback"):
            if method == "GET":
                return _get_feedback(eco_id, exec_name)
            if method == "PUT":
                return _put_feedback(eco_id, exec_name, _body())
        if method == "PUT" and exec_name and path.rstrip("/").endswith("/sections"):
            return _put_sections(eco_id, exec_name, _body())
        # Run name + tags (business "Your results" — editable name, searchable tags).
        if method == "POST" and exec_name and path.rstrip("/").endswith("/meta"):
            return _meta_write(eco_id, exec_name, _body())
        if method == "POST" and exec_name and path.rstrip("/").endswith("/promote"):
            return _promote(eco_id, exec_name, _body(), user)
        # ── spec-quality-feedback-loop: persistent per-ecosystem guidance ─────
        gid = path_params.get("gid")
        if not exec_name and "/guidance" in path:
            if gid:
                if method == "PATCH":
                    return _patch_guidance(eco_id, gid, _body())
                if method == "DELETE":
                    return _delete_guidance(eco_id, gid)
            else:
                if method == "GET":
                    return _list_guidance(eco_id)
                if method == "POST":
                    return _add_guidance(eco_id, _body(), user)
        # GET /runs/recommendation — cross-run config recommendation (spec-run-tuning §6)
        if not exec_name and path.rstrip("/").endswith("/recommendation"):
            return _recommendation(eco_id)
        if exec_name:
            return _detail(eco_id, exec_name)
        return _list(eco_id)
    except sfn.exceptions.ExecutionDoesNotExist:
        return _resp(404, {"error": "execution not found"})
    except Exception as e:
        return _resp(500, {"error": str(e)[:300]})


_ACCOUNT_ID = None


def _account_id() -> str:
    """Cached account id (GetCallerIdentity is always permitted). Used to build
    the deterministic Eval State Machine ARN without ListStateMachines (which
    cannot be resource-scoped, so the runs-api role would deny it)."""
    global _ACCOUNT_ID
    if _ACCOUNT_ID is None:
        _ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    return _ACCOUNT_ID


def _eval_state_machine_arn(eco_id: str):
    """Deterministic per-eco Eval State Machine ARN (name = <project>-<env>-<eco>-eval),
    verified via the resource-scoped DescribeStateMachine. Returns None if absent."""
    arn = f"arn:aws:states:{REGION}:{_account_id()}:stateMachine:{PROJECT}-{ENVIRONMENT}-{eco_id}-eval"
    try:
        sfn.describe_state_machine(stateMachineArn=arn)
        return arn
    except sfn.exceptions.StateMachineDoesNotExist:
        return None
    except Exception:
        return None


def _clamp_int(v, lo, hi):
    """Coerce v to an int within [lo, hi]; return None if absent/unusable.
    Out-of-range values are CLIPPED (not rejected) so a per-run override is
    forgiving — the harness applies the clamped effective value."""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _eval_launch(eco_id: str, body: dict | None = None) -> dict:
    """Start an evaluation run of the eco's Eval State Machine (spec §5). Returns
    404 if the eco has no Eval SM (no approved `evaluation:` block yet).

    Optional per-run OVERRIDES (body): {replicates?: int, max_cases?: int}. These
    are PER-RUN only — they never mutate the stored `evaluation:` block. Bounds:
    replicates 1..10, max_cases 1..50 (out-of-range values are clipped). The
    StartExecution input ALWAYS carries an `overrides` object (default {}) so the
    Eval SM's `$.overrides` JSONPath always resolves; an empty {} means the full
    stored config is used (backward-compatible)."""
    body = body or {}
    arn = _eval_state_machine_arn(eco_id)
    if not arn:
        return _resp(404, {"error": "No evaluation is set up for this ecosystem yet. "
                                     "Propose + approve an evaluation suite first."})
    overrides: dict = {}
    rep = _clamp_int(body.get("replicates"), 1, 10)
    if rep is not None:
        overrides["replicates"] = rep
    mc = _clamp_int(body.get("max_cases"), 1, 50)
    if mc is not None:
        overrides["max_cases"] = mc
    exec_name = f"eval-{eco_id[:40]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    try:
        res = sfn.start_execution(stateMachineArn=arn, name=exec_name,
                                  input=json.dumps({"overrides": overrides}))
    except Exception as e:
        return _resp(500, {"error": f"failed to start eval run: {str(e)[:200]}"})
    return _resp(200, {"started": True, "eco_id": eco_id, "overrides": overrides,
                       "execution_arn": res["executionArn"], "exec_name": exec_name})


def _eval_status(eco_id: str) -> dict:
    """Latest evaluation-run status + whether a scorecard exists (spec §9.1). The
    scorecard itself is served by get-ecosystem ?artifact=scorecard."""
    arn = _eval_state_machine_arn(eco_id)
    out = {"has_eval_sm": bool(arn), "executions": [], "latest": None, "scorecard_present": False}
    if arn:
        try:
            execs = sfn.list_executions(stateMachineArn=arn, maxResults=5).get("executions", [])
            out["executions"] = [{"name": e["name"], "status": e["status"],
                                  "startDate": e.get("startDate"),
                                  "stopDate": e.get("stopDate")} for e in execs]
            if out["executions"]:
                out["latest"] = out["executions"][0]
        except Exception as e:
            out["error"] = str(e)[:200]
    if RUNS_BUCKET:
        try:
            s3.head_object(Bucket=RUNS_BUCKET,
                           Key=f"evaluations/{eco_id}/scorecards/_latest.json")
            out["scorecard_present"] = True
        except Exception:
            pass
    return _resp(200, out)


def _list_attention(owner_id: str) -> dict:
    """Return the awaiting AttentionItems for this user (the async inbox).
    Queried by ownerId; awaiting items only, newest first."""
    if _attention is None or not owner_id:
        return _resp(200, {"items": []})
    from boto3.dynamodb.conditions import Key, Attr
    try:
        resp = _attention.query(
            KeyConditionExpression=Key("ownerId").eq(owner_id),
            FilterExpression=Attr("status").eq("awaiting"),
        )
        items = resp.get("Items", [])
    except Exception as e:
        return _resp(500, {"error": str(e)[:300], "items": []})
    # Lazy expiry: an awaiting item past its deadline is no longer actionable
    # (the run ends with a clean GateExpired). Flip it to expired and drop it
    # from the inbox so the badge clears — no sweeper needed.
    import time as _t
    now = int(_t.time())
    live = []
    for it in items:
        dl = int(it.get("deadline") or 0)
        if dl and dl < now:
            try:
                _attention.update_item(
                    Key={"ownerId": it["ownerId"], "itemId": it["itemId"]},
                    UpdateExpression="SET #s = :e",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":e": "expired"},
                )
            except Exception:
                pass
            continue
        live.append(it)
    live.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return _resp(200, {"items": live, "count": len(live)})


def _list(eco_id: str) -> dict:
    """List recent executions for tessera-{env}-{eco_id} state machine."""
    sm_arn = _state_machine_arn(eco_id)
    try:
        resp = sfn.list_executions(
            stateMachineArn=sm_arn,
            maxResults=20,
        )
    except sfn.exceptions.StateMachineDoesNotExist:
        # Quorum-archetype ecosystems never compile a Step Functions state machine (spec §5) —
        # they run on the quorum runtime and persist their artifacts to the runs bucket instead.
        # So there is no SM to ListExecutions against; History must come from S3. Without this,
        # the History tab surfaced a raw 'StateMachineDoesNotExist' error for every quorum eco.
        return _list_quorum_runs_from_s3(eco_id)
    executions = []
    for ex in resp.get("executions", []) or []:
        start = ex.get("startDate")
        stop = ex.get("stopDate")
        duration_s = None
        if start and stop:
            duration_s = (stop - start).total_seconds()
        meta = _run_meta(ex.get("name"))
        executions.append({
            "name": ex.get("name"),
            "status": ex.get("status"),
            "startDate": start.isoformat() if isinstance(start, datetime) else start,
            "stopDate": stop.isoformat() if isinstance(stop, datetime) else stop,
            "duration_s": duration_s,
            "executionArn": ex.get("executionArn"),
            "displayName": meta.get("name") or "",
            "tags": meta.get("tags") or [],
        })
    return _resp(200, {"executions": executions, "ecosystemId": eco_id})


def _list_quorum_runs_from_s3(eco_id: str) -> dict:
    """History for a quorum ecosystem, which has no Step Functions state machine.

    Quorum runs persist to runs/<eco_id>-<id>/ in the runs bucket. We enumerate those folders,
    derive each run's timing from its persisted artifacts (_run_config.json = start, _verdict.json
    = finish), and mark status SUCCEEDED when a verdict exists, else RUNNING. Shape matches _list()
    so the History UI renders quorum runs identically to DAG runs."""
    if not RUNS_BUCKET:
        return _resp(200, {"executions": [], "ecosystemId": eco_id})
    prefix = f"runs/{eco_id}-"
    try:
        pages = s3.get_paginator("list_objects_v2").paginate(
            Bucket=RUNS_BUCKET, Prefix=prefix, Delimiter="/")
        folders = []
        for pg in pages:
            folders.extend(cp["Prefix"] for cp in (pg.get("CommonPrefixes") or []))
    except Exception as e:
        print(f"[runs-api] quorum S3 history list failed for {eco_id}: {type(e).__name__}: {e}")
        return _resp(200, {"executions": [], "ecosystemId": eco_id})

    runs = []
    for folder in folders:
        exec_name = folder[len("runs/"):].rstrip("/")
        if not exec_name:
            continue
        start = _s3_obj_time(f"{folder}_run_config.json") or _s3_obj_time(f"{folder}_attachments.json")
        verdict_t = _s3_obj_time(f"{folder}_verdict.json")
        done = verdict_t is not None
        duration_s = (verdict_t - start).total_seconds() if (start and verdict_t) else None
        meta = _run_meta(exec_name)
        runs.append({
            "name": exec_name,
            "status": "SUCCEEDED" if done else "RUNNING",
            "startDate": start.isoformat() if isinstance(start, datetime) else None,
            "stopDate": verdict_t.isoformat() if isinstance(verdict_t, datetime) else None,
            "duration_s": duration_s,
            "executionArn": "",   # no Step Functions execution for a quorum run
            "displayName": meta.get("name") or "",
            "tags": meta.get("tags") or [],
        })
    # Most recent first (unstarted/undated runs sort last).
    runs.sort(key=lambda r: r.get("startDate") or "", reverse=True)
    return _resp(200, {"executions": runs[:20], "ecosystemId": eco_id})


def _s3_obj_time(key: str):
    """LastModified of a runs-bucket object, or None if absent/unreadable."""
    try:
        return s3.head_object(Bucket=RUNS_BUCKET, Key=key).get("LastModified")
    except Exception:
        return None


def _quorum_detail_from_s3(eco_id: str, exec_name: str) -> dict:
    """Run detail for a quorum run (no Step Functions execution). Sourced from the runs bucket.

    Returns the quorum plan/transcript/verdict for the Deliberation Room + replay, plus
    empty DAG fields (agents/failed_agents/skipped_agents) so DAG-oriented UI never errors.
    Clears the 'execution not found' banner that describe_execution produced for quorum runs."""
    seg = _safe_seg(exec_name)
    result = _read_json_key(f"runs/{seg}/_quorum_result.json") or {}
    verdict = result.get("verdict") or _read_json_key(f"runs/{seg}/_verdict.json") or {}
    plan = result.get("plan") or _read_json_key(f"runs/{seg}/_quorum_plan.json") or {}
    transcript = result.get("transcript") or _read_json_key(f"runs/{seg}/_deliberation.json") or {}
    session = _read_json_key(f"runs/{seg}/_session.json") or {}

    start = _s3_obj_time(f"runs/{seg}/_run_config.json") or _s3_obj_time(f"runs/{seg}/_attachments.json")
    stop = _s3_obj_time(f"runs/{seg}/_verdict.json")
    status = "SUCCEEDED" if verdict else "RUNNING"

    # Mission transcript sidecar (same convention as the DAG path).
    mission_transcript = None
    _side = _read_json_key(f"runs/{seg}/_mission_transcript.json")
    if _side and _side.get("messages"):
        mission_transcript = {"messages": _side["messages"],
                              "reconstructed": bool(_side.get("reconstructed", True))}

    meta = _run_meta(exec_name)
    start_iso = start.isoformat() if isinstance(start, datetime) else None
    stop_iso = stop.isoformat() if isinstance(stop, datetime) else None
    return _resp(200, {
        "ecosystemId": eco_id,
        "name": exec_name,
        "archetype": "quorum",
        "status": status,
        "startDate": start_iso,
        "stopDate": stop_iso,
        # `execution` mirrors the DAG-path shape so the History UI's detail.execution.* reads
        # (status, dates, input) never throw for a quorum run.
        "execution": {"name": exec_name, "status": status,
                      "startDate": start_iso, "stopDate": stop_iso, "input": {}},
        # Quorum payload for the Deliberation Room / replay / verdict panel.
        "quorum": {"plan": plan, "transcript": transcript, "verdict": verdict,
                   "state": session.get("state") if isinstance(session, dict) else None},
        "verdict": verdict,
        "mission_transcript": mission_transcript,
        # DAG-shaped fields kept empty so DAG UI components degrade gracefully, never throw.
        "agents": [],
        "failed_agents": [],
        "skipped_agents": [],
        "running_agents": [],
        "displayName": meta.get("name") or "",
        "tags": meta.get("tags") or [],
    })


def _detail(eco_id: str, exec_name: str) -> dict:
    """Reconstruct per-agent run data from SFN history + S3 outputs."""
    sm_arn = _state_machine_arn(eco_id)
    account = sm_arn.split(":")[4]
    exec_arn = f"arn:aws:states:{REGION}:{account}:execution:{PROJECT}-{ENVIRONMENT}-{eco_id}:{exec_name}"

    try:
        desc = sfn.describe_execution(executionArn=exec_arn)
    except (sfn.exceptions.ExecutionDoesNotExist, sfn.exceptions.StateMachineDoesNotExist,
            sfn.exceptions.InvalidArn):
        # Quorum-archetype runs have no Step Functions execution — their state lives in the runs
        # bucket (_quorum_result/_deliberation/_verdict/_session). describe_execution 404s, which
        # surfaced as the "execution not found" banner. Reconstruct the detail from S3 instead.
        return _quorum_detail_from_s3(eco_id, exec_name)

    history_pages = sfn.get_paginator("get_execution_history").paginate(
        executionArn=exec_arn,
    )
    events = []
    for page in history_pages:
        events.extend(page["events"])

    # Walk events: collect TaskSucceeded outputs that look like agent payloads
    # AND collect TaskFailed/TaskTimedOut to identify the failed agent(s).
    by_id = {e["id"]: e for e in events}
    def find_state_for_event(ev):
        cur = ev
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

    agents = {}
    failed_agents = []
    # running_agents: agents currently in flight — TaskStateEntered without a
    # corresponding TaskStateExited yet. Critical for the live UI: when the
    # frontend polls runs-api during a refresh or after the WS drops, it
    # needs to know which agents are running RIGHT NOW so it can render
    # them with the live "pulsing" state instead of falsely as "pending".
    # Tracked by state name (which equals agent_id for agent tasks).
    entered_states = {}  # state_name -> entered_event_id
    for ev in events:
        if ev["type"] == "TaskStateEntered":
            name = ev.get("stateEnteredEventDetails", {}).get("name")
            if name:
                entered_states[name] = ev.get("id")
        elif ev["type"] == "TaskStateExited":
            name = ev.get("stateExitedEventDetails", {}).get("name")
            if name:
                entered_states.pop(name, None)
    # Filter to plausible agent names (skip internal grounding states like
    # Grounding_<id>, External_<id>, MergeLayer1, Pass states, etc.)
    running_agents = []
    grounding_in_flight = []   # agent_ids that internal grounding is verifying
    external_in_flight = []    # agent_ids that external grounding is verifying
    for state_name in entered_states:
        if not state_name:
            continue
        if state_name.startswith("Grounding_"):
            grounding_in_flight.append(state_name[len("Grounding_"):])
            continue
        if state_name.startswith("External_"):
            external_in_flight.append(state_name[len("External_"):])
            continue
        if (state_name.startswith("MergeLayer") or
                state_name.startswith("Layer") or
                state_name.startswith("Pass") or
                "_grounding_" in state_name.lower()):
            continue
        running_agents.append(state_name)

    for ev in events:
        if ev["type"] == "TaskSucceeded":
            try:
                d = json.loads(ev["taskSucceededEventDetails"]["output"])
            except Exception:
                continue
            p = d.get("Payload", d) if isinstance(d, dict) else d
            if not isinstance(p, dict) or "output_s3_uri" not in p:
                continue
            # agent_id comes from the agent-invoker payload itself (authoritative).
            # Only fall back to tracing the enclosing TaskStateEntered name if the
            # payload somehow lacks it. We must NOT *require* the trace: inside a
            # Parallel (layer) state the previousEventId walk can fail to reach the
            # agent's TaskStateEntered (branch interleaving / 20-hop limit), which
            # previously dropped a completed parallel agent entirely — it then
            # shows grey in the DAG forever even though it succeeded.
            aid = p.get("agent_id") or find_state_for_event(ev)
            if not aid or aid in agents:
                continue
            full_output = _fetch_s3(p.get("output_s3_uri"))
            tool_results_raw = _fetch_s3(p.get("tool_results_s3_uri"))
            try:
                tool_results = json.loads(tool_results_raw) if tool_results_raw else []
            except Exception:
                tool_results = []
            agents[aid] = {
                "agent_id": aid,
                "structured": p.get("structured") or {},
                "output_preview": p.get("output_preview", ""),
                "output_size": p.get("output_size", 0),
                "output": full_output,
                "tool_results": tool_results,
                "output_s3_uri": p.get("output_s3_uri"),
                "tool_results_s3_uri": p.get("tool_results_s3_uri"),
            }
        elif ev["type"] in ("TaskFailed", "TaskTimedOut"):
            sn = find_state_for_event(ev)
            details_key = "taskFailedEventDetails" if ev["type"] == "TaskFailed" else "taskTimedOutEventDetails"
            details = ev.get(details_key, {}) or {}
            err = details.get("error", "")
            cause = details.get("cause", "")
            # Extract agent_id from the state name pattern (Task_<agent_id>) or
            # by parsing the cause's structured error message which usually says
            # "Agent <agent_id> did not produce..." etc.
            agent_id = None
            if sn and sn.startswith("Task_"):
                agent_id = sn[len("Task_"):]
            elif sn:
                agent_id = sn
            # Heuristic: parse cause for "Agent 'X'" pattern as fallback
            if not agent_id or agent_id == sn:
                import re as _re
                m = _re.search(r"Agent\s+'?([a-z_][a-z0-9_-]*)'?", cause)
                if m:
                    agent_id = m.group(1)
            failed_agents.append({
                "agent_id": agent_id,
                "state_name": sn,
                "event_type": ev["type"],
                "error": err,
                "cause": cause[:2000],
            })

    # Mission / session_id from execution input
    try:
        inp = json.loads(desc.get("input") or "{}")
    except Exception:
        inp = {}

    # Resume backfill (see _resume): a resumed execution starts mid-DAG via the
    # ResumeRouter, so its REUSED upstream agents have no TaskSucceeded event in
    # this execution — but their outputs were copied into this run's S3 prefix.
    # Surface them as completed ("carried_over") so the live DAG shows them done
    # instead of stuck-pending (and so they aren't mis-counted as skipped).
    if inp.get("resumed_from_session"):
        try:
            prefix = f"runs/{_safe_seg(exec_name)}/"
            folders = set()
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=RUNS_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    rel = obj["Key"][len(prefix):]
                    if "/" in rel:
                        folders.add(rel.split("/", 1)[0])
            for folder in folders:
                if not folder or folder in agents:
                    continue
                try:
                    out = s3.get_object(Bucket=RUNS_BUCKET, Key=f"{prefix}{folder}/output.txt")["Body"].read().decode("utf-8", "replace")
                except Exception:
                    continue  # no output.txt → not a completed agent folder
                agents[folder] = {
                    "agent_id": folder,
                    "structured": {},
                    "output_preview": out[:500],
                    "output_size": len(out),
                    "output": out,
                    "tool_results": [],
                    "output_s3_uri": "",
                    "tool_results_s3_uri": "",
                    "carried_over": True,
                }
        except Exception:
            pass

    # Ecosystem config — needed to compute "skipped" agents (declared in YAML
    # but never ran). Best-effort fetch from S3; if unavailable, omit.
    declared_agent_ids = _fetch_declared_agents(eco_id)
    succeeded_ids = set(agents.keys())
    failed_ids = set(fa["agent_id"] for fa in failed_agents if fa["agent_id"])
    skipped_ids = []
    if declared_agent_ids:
        skipped_ids = [aid for aid in declared_agent_ids
                       if aid not in succeeded_ids and aid not in failed_ids]

    desc_status = desc.get("status")
    # When the execution ultimately SUCCEEDED (e.g. after a redrive), TaskFailed
    # events from the prior attempt are still in history but do not represent
    # the run's outcome. Suppress them so the UI shows accurate state — the
    # historical failures are visible in the events log if anyone needs them.
    final_failed_agents = failed_agents if desc_status != "SUCCEEDED" else []
    # Skipped only makes sense for terminal-failure executions (agents that
    # didn't run because a parallel sibling failed first). For RUNNING runs,
    # agents that haven't started yet are PENDING (waiting their turn between
    # layers / grounding / parallel branches), not skipped — leaving them out
    # so the DAG renders them as default-pending grey dots, and inter-layer
    # transition arrows correctly blink (which requires the target layer to
    # have anyPending || anyRunning).
    final_skipped_agents = skipped_ids if desc_status not in ("SUCCEEDED", "RUNNING") else []
    # Only meaningful for RUNNING executions; for terminal states there are
    # no still-running agents (anything that was running when the run ended
    # was either stopped, failed, or completed by the time history was written).
    final_running_agents = running_agents if desc_status == "RUNNING" else []
    final_grounding_in_flight = grounding_in_flight if desc_status == "RUNNING" else []
    final_external_in_flight = external_in_flight if desc_status == "RUNNING" else []

    # Phase 5f: resolve typed outcome record (declared vs produced artifacts).
    # Cheap S3 head-checks; gracefully degrades to {status: "none"} if the eco
    # has no outcome declaration or YAML can't be loaded.
    outcome_record = _resolve_outcome(eco_id, exec_name)

    # Degraded-success signal: a run can reach SUCCEEDED while its declared
    # deliverable never materialized (e.g. a drafter truncated and its artifact
    # was never produced, or a gate was Accepted past a missing artifact). Surface
    # that so the UI doesn't present a clean success with nothing to download.
    degraded = False
    degraded_reason = ""
    if desc_status == "SUCCEEDED" and outcome_record.get("kind") == "file_artifact" \
            and outcome_record.get("status") in ("empty", "partial"):
        degraded = True
        miss = outcome_record.get("missing_artifacts") or []
        degraded_reason = (
            f"Run completed but the declared deliverable was not produced (missing: {', '.join(miss)})."
            if miss else "Run completed but the declared deliverable is incomplete."
        )

    # Mission transcript: captured runs carry it in the SFN input; reconstructed/older
    # runs have an S3 sidecar (a completed execution's input is immutable). The sidecar
    # is {"messages": [...], "reconstructed": true}. Normalize to {messages, reconstructed}.
    mission_transcript = None
    _mt = inp.get("mission_transcript")
    if _mt:
        mission_transcript = {"messages": _mt, "reconstructed": False}
    else:
        _side = _read_json_key(f"runs/{_safe_seg(exec_name)}/_mission_transcript.json")
        if _side and _side.get("messages"):
            mission_transcript = {"messages": _side["messages"], "reconstructed": bool(_side.get("reconstructed", True))}

    return _resp(200, {
        "execution": {
            "name": exec_name,
            "executionArn": exec_arn,
            "status": desc_status,
            "degraded": degraded,
            "degraded_reason": degraded_reason,
            "error": desc.get("error"),
            "cause": (desc.get("cause") or "")[:4000],
            "redriveStatus": desc.get("redriveStatus"),
            "redriveCount": desc.get("redriveCount"),
            "redriveStatusReason": desc.get("redriveStatusReason"),
            "startDate": _isoformat(desc.get("startDate")),
            "stopDate": _isoformat(desc.get("stopDate")),
            "input": inp,
        },
        "mission_transcript": mission_transcript,
        "agents": list(agents.values()),
        "failed_agents": final_failed_agents,
        "skipped_agents": final_skipped_agents,
        "running_agents": final_running_agents,
        "grounding_in_flight": final_grounding_in_flight,
        "external_in_flight": final_external_in_flight,
        "outcome": outcome_record,
        "pending_gates": _resolve_pending_gates(eco_id, exec_name, set(entered_states.keys())),
    })


# ─── spec-run-tuning §5: run manifest (config + telemetry ledger) ────────────

def _safe_seg(s, n=128):
    """Sanitize an S3 path segment (mirror of agent-invoker._safe)."""
    s = str(s or "")
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)[:n]


def _read_json_key(key):
    """Read+parse a JSON object from RUNS_BUCKET key, or {} on any failure."""
    if not RUNS_BUCKET or not key:
        return {}
    try:
        return json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read() or b"{}")
    except Exception:
        return {}


def _is_agent_state(name: str) -> bool:
    """Deprecated: per-agent attribution now maps state→agent_id from task
    payloads (see _manifest), which correctly handles single-agent layers named
    "Layer{i}". Kept as a no-op-free helper in case other call sites want it."""
    if not name:
        return False
    for pre in ("Layer", "MergeLayer", "Pass", "Grounding_", "External_",
                "Checkpoint", "CheckpointChoice", "CheckpointExpired", "Start"):
        if name.startswith(pre):
            return False
    return True


def _manifest(eco_id: str, exec_name: str) -> dict:
    """GET /runs/{execName}/manifest — HTTP wrapper around _manifest_obj."""
    return _resp(200, _manifest_obj(eco_id, exec_name))


def _manifest_obj(eco_id: str, exec_name: str) -> dict:
    """Reconstruct the run manifest (spec-run-tuning §5) as a plain dict (also used
    by the recommender). Built from the SFN execution input (launch choices) +
    history (durations/gates) + S3 artifacts (token usage) + the eco YAML (config
    hash, quality). Cached to runs/{sid}/_run_manifest.json once the run is terminal."""
    sm_arn = _state_machine_arn(eco_id)
    account = sm_arn.split(":")[4]
    exec_arn = f"arn:aws:states:{REGION}:{account}:execution:{PROJECT}-{ENVIRONMENT}-{eco_id}:{exec_name}"
    desc = sfn.describe_execution(executionArn=exec_arn)
    status = desc.get("status")
    session_id = exec_name  # session_id == execution name (see websocket on_launch)
    cache_key = f"runs/{_safe_seg(session_id)}/_run_manifest.json"

    # Serve cached manifest for terminal runs (stable + avoids re-walking history).
    if status != "RUNNING":
        cached = _read_json_key(cache_key)
        if cached.get("_cached"):
            return cached

    try:
        inp = json.loads(desc.get("input") or "{}")
    except Exception:
        inp = {}

    # Eco config: hash the stored YAML at read time (pins the cohort — spec §5.1)
    # and pull the quality posture.
    eco_hash = ""
    if ECOSYSTEMS_BUCKET:
        try:
            raw = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()
            eco_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
        except Exception:
            eco_hash = ""
    quality = (_load_eco_yaml(eco_id) or {}).get("quality") or {}

    # Walk history. Per-agent durations/attempts must be keyed by AGENT ID, not
    # state name: single-agent layers are named "Layer{i}" (the task is the layer
    # state), while parallel-branch tasks are named by agent id. So first map
    # state_name → agent_id from agent TaskSucceeded payloads (which carry
    # agent_id / output_s3_uri — this also naturally excludes grounding/merge/pass
    # wrapper states), then attribute durations through that map. Checkpoints are
    # real model calls (reviewer tier) and ARE included — their cost/latency matter.
    events = []
    for page in sfn.get_paginator("get_execution_history").paginate(executionArn=exec_arn):
        events.extend(page["events"])
    by_id = {e["id"]: e for e in events}

    def _find_state(ev):
        cur = ev
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

    state_agent: dict = {}
    agent_ids: set = set()   # every agent_id seen in an agent-invoker payload
    for ev in events:
        if ev["type"] != "TaskSucceeded":
            continue
        try:
            d = json.loads(ev["taskSucceededEventDetails"]["output"])
        except Exception:
            continue
        p = d.get("Payload", d) if isinstance(d, dict) else d
        # Require output_s3_uri — the agent-invoker payload signature. (Grounding_/
        # External_ Lambda tasks ECHO agent_id in their output, so keying on
        # agent_id alone would map those wrapper states onto the agent and triple-
        # count its duration/attempts.)
        if not (isinstance(p, dict) and p.get("output_s3_uri")):
            continue
        aid = p.get("agent_id")
        if aid:
            agent_ids.add(aid)
        # Map enclosing state name → agent_id when we can trace it. The trace can
        # fail for a Parallel branch (interleaving / 20-hop limit); that's fine —
        # the duration loop falls back to matching the agent's own state name
        # (which equals the agent_id for branch tasks) against agent_ids, so the
        # agent's tokens/duration are no longer silently dropped from telemetry.
        sn = _find_state(ev)
        if sn:
            state_agent[sn] = aid or sn

    per_agent: dict = {}
    open_ts: dict = {}
    gate_events = []
    for ev in events:
        et = ev["type"]
        if et == "TaskStateEntered":
            nm = ev.get("stateEnteredEventDetails", {}).get("name")
            if nm:
                open_ts.setdefault(nm, []).append(ev["timestamp"])
        elif et == "TaskStateExited":
            nm = ev.get("stateExitedEventDetails", {}).get("name")
            if nm and open_ts.get(nm):
                start_ts = open_ts[nm].pop(0)
                dur = (ev["timestamp"] - start_ts).total_seconds()
                aid = state_agent.get(nm) or (nm if nm in agent_ids else None)
                if not aid:
                    continue  # wrapper / non-agent state
                a = per_agent.setdefault(aid, {"agent_id": aid, "duration_s": 0.0, "attempts": 0})
                a["duration_s"] = round(a["duration_s"] + dur, 1)
                a["attempts"] += 1
        elif et == "ChoiceStateEntered":
            nm = ev.get("stateEnteredEventDetails", {}).get("name") or ""
            if nm.startswith("CheckpointChoice"):
                i = nm[len("CheckpointChoice"):]
                try:
                    cinp = json.loads(ev["stateEnteredEventDetails"].get("input") or "{}")
                    dec = (cinp.get(f"checkpoint{i}") or {}).get("decision")
                except Exception:
                    dec = None
                gate_events.append({"layer": i, "decision": dec, "at": _isoformat(ev["timestamp"])})

    # Per-agent token usage + cost (token capture; graceful if usage.json absent).
    resolved_models: dict = {}
    totals_in = totals_out = 0
    cost_total = 0.0
    cost_partial = False
    for aid, a in per_agent.items():
        usage = _read_json_key(f"runs/{_safe_seg(session_id)}/{_safe_seg(aid, 64)}/usage.json")
        model = (usage.get("model") or "")
        ti = int(usage.get("tokens_in") or 0)
        to = int(usage.get("tokens_out") or 0)
        a["model"] = model
        a["tokens_in"] = ti
        a["tokens_out"] = to
        a["tool_calls"] = int(usage.get("tool_calls") or 0)
        a["cost_usd"] = pricing.estimate_cost(model, ti, to)
        if model:
            resolved_models[aid] = model
        totals_in += ti
        totals_out += to
        cost_total += a["cost_usd"]
        if (ti or to) and not pricing.is_priced(model):
            cost_partial = True

    start = desc.get("startDate")
    stop = desc.get("stopDate")
    wall = round((stop - start).total_seconds(), 1) if (start and stop) else None

    manifest = {
        "schema_version": 1,
        "session_id": session_id,
        "ecosystem_id": eco_id,
        "ecosystem_config_hash": eco_hash,
        "launched_by": inp.get("user_id", ""),
        "launched_at": _isoformat(start),
        "finished_at": _isoformat(stop),
        "status": status,
        "dry_run": bool(inp.get("dry_run", False)),
        # Prefer the CLEAN mission (mission_display) for the UI — the agent-facing
        # `mission` may have uploaded-file CONTENT folded in; the display shouldn't
        # dump that. Files are surfaced separately as `attachments`.
        "mission": inp.get("mission_display") or inp.get("mission", ""),
        "attachments": [
            {"name": a.get("name", "")}
            for a in (inp.get("attachments") or []) if isinstance(a, dict) and a.get("name")
        ],
        # Phase 2 fills run_config (model_profile/overrides); empty until then.
        "run_config": inp.get("run_config") or {},
        "quality": {
            "challenger": quality.get("challenger"),
            "block_below": (quality.get("challenger_gate") or {}).get("block_below"),
            "gates": quality.get("gates") or {},
            "grounding": quality.get("grounding"),
        },
        "operator_guidance_ref": f"runs/{session_id}/_operator_guidance.txt",
        "gate_events": gate_events,
        "metrics": {
            "wall_clock_s": wall,
            "per_agent": sorted(per_agent.values(), key=lambda x: x["agent_id"]),
            "totals": {"tokens_in": totals_in, "tokens_out": totals_out},
            "cost_estimate_usd": round(cost_total, 6),
            # True when some agent reported tokens but we had no price/model for it
            # (so cost is a lower bound). Surfaces "estimate only" honestly.
            "cost_is_partial": cost_partial,
        },
        "quality_signals": {
            "gate_interventions": sum(1 for g in gate_events if g.get("decision") in ("loop", "stop")),
        },
        "resolved_models": resolved_models,
        "human_rating": None,
    }

    # Cache once terminal (manifest is then stable).
    if status != "RUNNING" and RUNS_BUCKET:
        try:
            cached = dict(manifest)
            cached["_cached"] = True
            s3.put_object(Bucket=RUNS_BUCKET, Key=cache_key,
                          Body=json.dumps(cached).encode(), ContentType="application/json")
        except Exception:
            pass
    return manifest


def _rating(eco_id: str, exec_name: str, body: dict) -> dict:
    """POST /runs/{execName}/rating — attach a human quality rating to the run
    manifest (spec-run-tuning §6.3). Body: {score: 1..5, note?}. Persisted into the
    cached manifest so the recommender can use it as the quality anchor."""
    try:
        score = int(body.get("score"))
    except (TypeError, ValueError):
        return _resp(400, {"error": "score must be an integer 1..5"})
    if not (1 <= score <= 5):
        return _resp(400, {"error": "score must be between 1 and 5"})
    note = str(body.get("note") or "")[:1000]
    m = _manifest_obj(eco_id, exec_name)  # builds + caches if needed
    m["human_rating"] = {"score": score, "note": note}
    m["_cached"] = True
    cache_key = f"runs/{_safe_seg(exec_name)}/_run_manifest.json"
    if RUNS_BUCKET:
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=cache_key,
                          Body=json.dumps(m).encode(), ContentType="application/json")
        except Exception as e:
            return _resp(500, {"error": f"failed to persist rating: {str(e)[:200]}"})
    return _resp(200, {"ok": True, "human_rating": m["human_rating"]})


# ─── Run metadata: business name (auto + editable) + tags (for search) ───────
def _run_meta_key(exec_name: str) -> str:
    return f"runs/{_safe_seg(exec_name)}/_run_meta.json"


def _run_meta(exec_name: str) -> dict:
    """Best-effort read of the per-run {name, tags} metadata. {} if absent."""
    try:
        return _read_json_key(_run_meta_key(exec_name)) or {}
    except Exception:
        return {}


def _meta_write(eco_id, exec_name, body):
    """POST /runs/{execName}/meta — set the run's business name and/or tags.
    Body: {name?, tags?[]}. Name is user-editable (auto-suggested elsewhere); tags
    are lowercased for search. Merges into runs/{sid}/_run_meta.json."""
    m = _run_meta(exec_name)
    if "name" in body:
        m["name"] = str(body.get("name") or "")[:200]
    if "tags" in body:
        raw = body.get("tags") or []
        if isinstance(raw, list):
            seen, tags = set(), []
            for t in raw:
                s = str(t).strip().lower()[:40]
                if s and s not in seen:
                    seen.add(s); tags.append(s)
            m["tags"] = tags[:20]
    m["updated_at"] = _now_iso()
    try:
        _put_json_key(_run_meta_key(exec_name), m)
    except Exception as e:
        return _resp(500, {"error": f"failed to save run metadata: {str(e)[:200]}"})
    return _resp(200, {"ok": True, "name": m.get("name", ""), "tags": m.get("tags", [])})


# ─── spec-quality-feedback-loop: run feedback + per-ecosystem guidance ───────
#
# Two S3-backed stores in RUNS_BUCKET:
#   runs/<session>/_feedback.json   — per-run freeform feedback + section marks
#                                      (kept separate from the machine telemetry
#                                       manifest, which humans don't edit).
#   guidance/<eco_id>.json          — persistent per-ecosystem guidance entries,
#                                      injected into every future run at launch
#                                      (websocket on_launch). Editable/toggleable.
# Ownership: gated by the owner/admin check already run at the top of handler().

_MAX_GUIDANCE_ENTRIES = 100
_MAX_SECTIONS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _put_json_key(key: str, obj: dict) -> None:
    """Write a JSON object to a RUNS_BUCKET key. Raises on failure (no silent
    fallback — the caller surfaces a 500)."""
    if not RUNS_BUCKET:
        raise RuntimeError("RUNS_BUCKET not configured")
    s3.put_object(Bucket=RUNS_BUCKET, Key=key,
                  Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")


def _feedback_key(exec_name: str) -> str:
    return f"runs/{_safe_seg(exec_name)}/_feedback.json"


def _guidance_key(eco_id: str) -> str:
    return f"guidance/{_safe_seg(eco_id)}.json"


def _clamp_score(v):
    """Coerce a rating score to int 1..5, or None if absent/invalid."""
    try:
        s = int(v)
    except (TypeError, ValueError):
        return None
    return s if 1 <= s <= 5 else None


def _empty_feedback(eco_id: str, exec_name: str) -> dict:
    return {"schema_version": 1, "session_id": exec_name, "ecosystem_id": eco_id,
            "run_rating": None, "run_feedback": "", "sections": [], "updated_at": None}


def _get_feedback(eco_id: str, exec_name: str) -> dict:
    fb = _read_json_key(_feedback_key(exec_name))
    if not fb:
        fb = _empty_feedback(eco_id, exec_name)
    fb.setdefault("sections", [])
    return _resp(200, fb)


def _put_feedback(eco_id: str, exec_name: str, body: dict) -> dict:
    """Upsert the run-level freeform feedback (and optionally mirror a star
    rating). Section marks are managed via _put_sections and preserved here."""
    fb = _read_json_key(_feedback_key(exec_name)) or _empty_feedback(eco_id, exec_name)
    fb["schema_version"] = 1
    fb["session_id"] = exec_name
    fb["ecosystem_id"] = eco_id
    fb.setdefault("sections", [])
    if "run_feedback" in body:
        fb["run_feedback"] = str(body.get("run_feedback") or "")[:8000]
    if isinstance(body.get("run_rating"), dict):
        score = _clamp_score(body["run_rating"].get("score"))
        note = str(body["run_rating"].get("note") or "")[:1000]
        fb["run_rating"] = {"score": score, "note": note} if score else fb.get("run_rating")
        # Mirror into the run manifest so the cross-run recommender's quality
        # anchor stays consistent with the standalone rating endpoint.
        if score:
            try:
                _rating(eco_id, exec_name, {"score": score, "note": note})
            except Exception:
                pass
    fb["updated_at"] = _now_iso()
    try:
        _put_json_key(_feedback_key(exec_name), fb)
    except Exception as e:
        return _resp(500, {"error": f"failed to persist feedback: {str(e)[:200]}"})
    return _resp(200, fb)


def _put_sections(eco_id: str, exec_name: str, body: dict) -> dict:
    """Replace the run's section marks. Body: {sections: [{id?, artifact_key,
    anchor, excerpt, score, feedback}]}."""
    sections = body.get("sections")
    if not isinstance(sections, list):
        return _resp(400, {"error": "sections must be a list"})
    cleaned = []
    for s in sections[:_MAX_SECTIONS]:
        if not isinstance(s, dict):
            continue
        cleaned.append({
            "id": s.get("id") or _gen_id("sec"),
            "artifact_key": str(s.get("artifact_key") or "")[:256],
            "anchor": s.get("anchor") if isinstance(s.get("anchor"), dict) else {},
            "excerpt": str(s.get("excerpt") or "")[:500],
            "score": _clamp_score(s.get("score")),
            "feedback": str(s.get("feedback") or "")[:4000],
            "created_at": s.get("created_at") or _now_iso(),
            "updated_at": _now_iso(),
        })
    fb = _read_json_key(_feedback_key(exec_name)) or _empty_feedback(eco_id, exec_name)
    fb["schema_version"] = 1
    fb["session_id"] = exec_name
    fb["ecosystem_id"] = eco_id
    fb["sections"] = cleaned
    fb["updated_at"] = _now_iso()
    try:
        _put_json_key(_feedback_key(exec_name), fb)
    except Exception as e:
        return _resp(500, {"error": f"failed to persist sections: {str(e)[:200]}"})
    return _resp(200, fb)


def _read_guidance(eco_id: str) -> dict:
    g = _read_json_key(_guidance_key(eco_id))
    if not g:
        g = {"schema_version": 1, "ecosystem_id": eco_id, "entries": [], "updated_at": None}
    g.setdefault("entries", [])
    return g


def _write_guidance(eco_id: str, g: dict) -> dict:
    g["schema_version"] = 1
    g["ecosystem_id"] = eco_id
    g["updated_at"] = _now_iso()
    _put_json_key(_guidance_key(eco_id), g)
    return g


def _list_guidance(eco_id: str) -> dict:
    return _resp(200, _read_guidance(eco_id))


def _add_guidance_entry(eco_id: str, text: str, source: dict, user_id: str) -> dict:
    g = _read_guidance(eco_id)
    if len(g["entries"]) >= _MAX_GUIDANCE_ENTRIES:
        raise RuntimeError(f"guidance limit reached ({_MAX_GUIDANCE_ENTRIES})")
    entry = {
        "id": _gen_id("g"), "text": text, "enabled": True, "source": source,
        "created_by": user_id, "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    g["entries"].append(entry)
    _write_guidance(eco_id, g)
    return entry


def _add_guidance(eco_id: str, body: dict, user: dict) -> dict:
    text = str(body.get("text") or "").strip()
    if not text:
        return _resp(400, {"error": "text is required"})
    try:
        entry = _add_guidance_entry(eco_id, text[:2000], {"type": "manual"}, user.get("user_id", ""))
    except RuntimeError as e:
        return _resp(409, {"error": str(e)})
    except Exception as e:
        return _resp(500, {"error": f"failed to add guidance: {str(e)[:200]}"})
    return _resp(201, {"ok": True, "guidance": entry})


def _patch_guidance(eco_id: str, gid: str, body: dict) -> dict:
    g = _read_guidance(eco_id)
    entry = next((x for x in g["entries"] if x.get("id") == gid), None)
    if not entry:
        return _resp(404, {"error": "guidance entry not found"})
    if "text" in body:
        entry["text"] = str(body.get("text") or "")[:2000]
    if "enabled" in body:
        entry["enabled"] = bool(body.get("enabled"))
    entry["updated_at"] = _now_iso()
    try:
        _write_guidance(eco_id, g)
    except Exception as e:
        return _resp(500, {"error": f"failed to update guidance: {str(e)[:200]}"})
    return _resp(200, {"ok": True, "guidance": entry})


def _delete_guidance(eco_id: str, gid: str) -> dict:
    g = _read_guidance(eco_id)
    before = len(g["entries"])
    g["entries"] = [x for x in g["entries"] if x.get("id") != gid]
    if len(g["entries"]) == before:
        return _resp(404, {"error": "guidance entry not found"})
    try:
        _write_guidance(eco_id, g)
    except Exception as e:
        return _resp(500, {"error": f"failed to delete guidance: {str(e)[:200]}"})
    return _resp(200, {"ok": True})


def _promote(eco_id: str, exec_name: str, body: dict, user: dict) -> dict:
    """Promote run-level or a specific section's feedback into a persistent
    guidance entry. Body: {scope: "run"|"section", section_id?, text?}."""
    scope = str(body.get("scope") or "run")
    text = str(body.get("text") or "").strip()
    fb = _read_json_key(_feedback_key(exec_name)) or {}
    if scope == "section":
        sid = body.get("section_id")
        sec = next((s for s in fb.get("sections", []) if s.get("id") == sid), None)
        if not sec:
            return _resp(404, {"error": "section not found"})
        if not text:
            text = sec.get("feedback", "")
        source = {"type": "section", "session_id": exec_name, "section_id": sid}
    else:
        if not text:
            text = fb.get("run_feedback", "")
        source = {"type": "run", "session_id": exec_name}
    text = text.strip()
    if not text:
        return _resp(400, {"error": "nothing to promote — feedback is empty"})
    try:
        entry = _add_guidance_entry(eco_id, text[:2000], source, user.get("user_id", ""))
    except RuntimeError as e:
        return _resp(409, {"error": str(e)})
    except Exception as e:
        return _resp(500, {"error": f"failed to promote: {str(e)[:200]}"})
    return _resp(200, {"ok": True, "guidance": entry})


def _config_signature(m: dict) -> str:
    """Stable label for a run's MODEL configuration (the knob the recommender
    compares): model_profile + sorted per-type overrides. 'default' when none."""
    rc = m.get("run_config") or {}
    prof = rc.get("model_profile") or "default"
    bt = (rc.get("model_overrides") or {}).get("by_type") or {}
    suffix = ",".join(f"{k}={v}" for k, v in sorted(bt.items()) if v)
    return f"{prof}" + (f" [{suffix}]" if suffix else "")


def _quality_score(m: dict) -> float:
    """0..1 quality proxy. Human rating (1..5) anchors it when present; otherwise a
    gate-intervention proxy (more operator Loops/Aborts → worse)."""
    hr = (m.get("human_rating") or {}).get("score")
    if hr:
        return max(0.0, min(1.0, (int(hr) - 1) / 4.0))
    gi = int((m.get("quality_signals") or {}).get("gate_interventions") or 0)
    return max(0.0, 1.0 - 0.2 * gi)


def _recommendation(eco_id: str) -> dict:
    """GET /runs/recommendation — cross-run config recommendation (spec-run-tuning §6).
    Cohorts terminal runs by ecosystem_config_hash, groups by model-config signature,
    returns per-config cost/latency/quality aggregates + Pareto frontier + a
    recommended config. Advisory only."""
    sm_arn = _state_machine_arn(eco_id)
    manifests = []
    try:
        execs = sfn.list_executions(stateMachineArn=sm_arn, statusFilter="SUCCEEDED", maxResults=60).get("executions", [])
    except Exception as e:
        return _resp(200, {"ecosystem_id": eco_id, "status": "no_data", "detail": str(e)[:200], "configs": []})
    for e in execs[:40]:
        try:
            manifests.append(_manifest_obj(eco_id, e["name"]))
        except Exception:
            continue
    if not manifests:
        return _resp(200, {"ecosystem_id": eco_id, "status": "no_data",
                           "detail": "No completed runs with a manifest yet.", "configs": []})

    from collections import Counter
    hashes = Counter(m.get("ecosystem_config_hash") or "" for m in manifests)
    dominant_hash = hashes.most_common(1)[0][0]
    cohort = [m for m in manifests if (m.get("ecosystem_config_hash") or "") == dominant_hash]

    groups: dict = {}
    for m in cohort:
        sig = _config_signature(m)
        g = groups.setdefault(sig, {"signature": sig, "runs": 0, "cost": [], "latency": [],
                                    "quality": [], "rated": 0, "run_config": m.get("run_config") or {}})
        g["runs"] += 1
        cost = (m.get("metrics") or {}).get("cost_estimate_usd")
        wall = (m.get("metrics") or {}).get("wall_clock_s")
        if cost is not None:
            g["cost"].append(cost)
        if wall is not None:
            g["latency"].append(wall)
        g["quality"].append(_quality_score(m))
        if (m.get("human_rating") or {}).get("score"):
            g["rated"] += 1

    def _avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    configs = []
    for sig, g in groups.items():
        configs.append({
            "signature": sig, "run_config": g["run_config"], "runs": g["runs"],
            "rated_runs": g["rated"], "avg_cost_usd": _avg(g["cost"]),
            "avg_wall_clock_s": _avg(g["latency"]), "avg_quality": _avg(g["quality"]),
        })

    def _dominates(a, b):
        ac, al, aq = a["avg_cost_usd"] or 0, a["avg_wall_clock_s"] or 0, a["avg_quality"] or 0
        bc, bl, bq = b["avg_cost_usd"] or 0, b["avg_wall_clock_s"] or 0, b["avg_quality"] or 0
        return (ac <= bc and al <= bl and aq >= bq) and (ac < bc or al < bl or aq > bq)
    frontier = [c for c in configs if not any(_dominates(o, c) for o in configs if o is not c)]

    rec = None
    if frontier:
        best_q = max((c["avg_quality"] or 0) for c in configs)
        eligible = [c for c in frontier if (c["avg_quality"] or 0) >= best_q - 0.05] or frontier
        rec = sorted(eligible, key=lambda c: (c["avg_cost_usd"] or 0, c["avg_wall_clock_s"] or 0))[0]

    baseline = max(configs, key=lambda c: c["runs"]) if configs else None
    delta = None
    if rec and baseline and rec["signature"] != baseline["signature"]:
        def _pct(new, old):
            return round(100.0 * (new - old) / old, 1) if old else None
        delta = {
            "vs": baseline["signature"],
            "cost_pct": _pct(rec["avg_cost_usd"] or 0, baseline["avg_cost_usd"] or 0),
            "latency_pct": _pct(rec["avg_wall_clock_s"] or 0, baseline["avg_wall_clock_s"] or 0),
            "quality_delta": round((rec["avg_quality"] or 0) - (baseline["avg_quality"] or 0), 3),
        }

    return _resp(200, {
        "ecosystem_id": eco_id, "status": "ok",
        "cohort_config_hash": dominant_hash, "cohort_runs": len(cohort),
        "total_runs_considered": len(manifests),
        "configs": sorted(configs, key=lambda c: (c["avg_cost_usd"] or 0)),
        "pareto_frontier": [c["signature"] for c in frontier],
        "recommended": rec, "recommended_delta": delta,
        "quality_basis": "human_rating where available, else gate-intervention proxy",
        "advisory": True,
    })


def _resume(eco_id: str, exec_name: str, body: dict, user: dict) -> dict:
    """POST /runs/{execName}/resume — start a NEW run that reuses this run's
    completed layers and re-executes from a selected node/layer onward.

    body: { resume_from: "<agent_id>" | resume_from_index: <int>, guidance: "<text>" }

    Unlike redrive (native SFN, FAILED-only, resumes failed states), this works
    for FAILED *and* SUCCEEDED prior runs and from ANY layer. It pre-seeds
    $.results from the prior run's S3 outputs for every agent in layers BEFORE the
    target, copies those agents' artifacts into the new session, writes operator
    guidance, then StartExecution with `resume_from_index` so the compiled
    ResumeRouter jumps straight to Layer{N}. Upstream is neither re-run nor re-paid.
    """
    import uuid as _uuid

    # Mirror agent-invoker.extract_structured so resumed results carry the SAME compact
    # `structured` the normal pipeline puts in $.results (full output stays in S3).
    _FIND_RE = re.compile(r"##\s*STRUCTURED\s+FINDINGS\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE)
    _QUES_RE = re.compile(r"##\s*OPEN\s+QUESTIONS\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE)

    def _structured_lite(txt: str):
        fm = _FIND_RE.search(txt or "")
        if not fm or not fm.group(1).strip():
            return None
        qm = _QUES_RE.search(txt or "")
        return {"findings": fm.group(1).strip(),
                "open_questions": qm.group(1).strip() if qm else "",
                "entity_key": ""}

    sm_arn = _state_machine_arn(eco_id)
    account = sm_arn.split(":")[4]
    prior_arn = f"arn:aws:states:{REGION}:{account}:execution:{PROJECT}-{ENVIRONMENT}-{eco_id}:{exec_name}"

    # 1. Parse the DEPLOYED ASL → agent_id ⇄ layer index (stays in sync with the
    #    actual compiled DAG, including injected platform agents).
    try:
        defn = json.loads(sfn.describe_state_machine(stateMachineArn=sm_arn)["definition"])
    except Exception as e:
        return _resp(404, {"error": f"state machine not found for {eco_id}: {e}"})
    layer_agents: dict = {}
    agent_layer: dict = {}
    kind_by_id: dict = {}
    for name, st in (defn.get("States") or {}).items():
        m = re.fullmatch(r"Layer(\d+)", name)
        if not m:
            continue
        li = int(m.group(1))
        ids = []
        if st.get("Type") == "Task":
            _cfg = (((st.get("Parameters") or {}).get("Payload") or {}).get("agent_config") or {})
            aid = _cfg.get("id")
            if aid:
                ids.append(aid)
                if _cfg.get("output_kind"):
                    kind_by_id[aid] = _cfg.get("output_kind")
        elif st.get("Type") == "Parallel":
            for br in st.get("Branches") or []:
                for _bs in (br.get("States") or {}).values():
                    _cfg = (((_bs.get("Parameters") or {}).get("Payload") or {}).get("agent_config") or {})
                    aid = _cfg.get("id")
                    if aid:
                        ids.append(aid)
                        if _cfg.get("output_kind"):
                            kind_by_id[aid] = _cfg.get("output_kind")
        layer_agents[li] = ids
        for aid in ids:
            agent_layer[aid] = li
    if not layer_agents:
        return _resp(500, {"error": "could not parse layers from state machine (ResumeRouter requires a recompiled ecosystem)"})
    max_layer = max(layer_agents)

    # 2. Resolve target layer N.
    if body.get("resume_from_index") is not None:
        try:
            target = int(body["resume_from_index"])
        except Exception:
            return _resp(400, {"error": "resume_from_index must be an integer"})
    else:
        node = body.get("resume_from") or ""
        if node not in agent_layer:
            return _resp(400, {"error": f"unknown resume_from agent '{node}'", "known_agents": sorted(agent_layer)})
        target = agent_layer[node]
    if target <= 0 or target > max_layer:
        return _resp(400, {"error": f"resume_from_index {target} out of range 1..{max_layer} (0 = full re-run; launch normally)"})

    # 3. Reused = agents in layers strictly before target; rerun = target onward.
    reused = [aid for li, ids in sorted(layer_agents.items()) if li < target for aid in ids]
    rerun = [aid for li, ids in sorted(layer_agents.items()) if li >= target for aid in ids]

    # 4. New session; copy reused agents' S3 outputs prior→new + hydrate $.results.
    new_session = f"{eco_id}-{_uuid.uuid4().hex[:8]}"
    results: dict = {}
    missing = []
    for aid in reused:
        seg = _safe_seg(aid, 64)
        src_prefix = f"runs/{_safe_seg(exec_name)}/{seg}/"
        try:
            output_txt = s3.get_object(Bucket=RUNS_BUCKET, Key=src_prefix + "output.txt")["Body"].read().decode("utf-8", "replace")
        except Exception:
            missing.append(aid)
            continue
        # Mirror normal execution's COMPACT $.results shape (Phase 4.5): compact
        # `structured` + S3 pointers to the full output/tool_results — NEVER the full
        # text inline. Inlining every reused agent's output blew the 256KB
        # StartExecution cap on doc/citation-heavy runs (observed: 395KB of outputs).
        # This is byte-for-byte the state a normal run already carries at this layer.
        _res = {
            "agent_id": aid,
            "structured": _structured_lite(output_txt),
            "output_s3_uri": f"s3://{RUNS_BUCKET}/runs/{new_session}/{seg}/output.txt",
            "tool_results_s3_uri": f"s3://{RUNS_BUCKET}/runs/{new_session}/{seg}/tool_results.json",
            "output_preview": output_txt[:500],
            "output_size": len(output_txt),
        }
        if aid in kind_by_id:
            _res["output_kind"] = kind_by_id[aid]
        results[aid] = _res
        try:
            for obj in s3.list_objects_v2(Bucket=RUNS_BUCKET, Prefix=src_prefix).get("Contents", []):
                dst = obj["Key"].replace(f"runs/{_safe_seg(exec_name)}/", f"runs/{new_session}/", 1)
                s3.copy_object(Bucket=RUNS_BUCKET, CopySource={"Bucket": RUNS_BUCKET, "Key": obj["Key"]}, Key=dst)
        except Exception:
            pass
    if missing:
        return _resp(409, {
            "error": "cannot resume — prior outputs missing for reused agents",
            "missing": missing,
            "hint": "pick an earlier node, or launch a full run instead",
        })

    # 5. Carry prior execution input (mission / inputs / run_config / user).
    try:
        prior_in = json.loads(sfn.describe_execution(executionArn=prior_arn).get("input") or "{}")
    except Exception:
        prior_in = {}

    # 6. Operator guidance → new session's _operator_guidance.txt (carry prior + append).
    guidance = (body.get("guidance") or "").strip()
    try:
        prior_guid = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{_safe_seg(exec_name)}/_operator_guidance.txt")["Body"].read().decode("utf-8")
    except Exception:
        prior_guid = ""
    target_label = body.get("resume_from") or f"layer {target}"
    guid_parts = []
    if prior_guid.strip():
        guid_parts.append(prior_guid.rstrip())
    if guidance:
        guid_parts.append(f"[resumed from {target_label} — operator guidance]\n{guidance}")
    if guid_parts:
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{new_session}/_operator_guidance.txt",
                          Body=("\n\n".join(guid_parts)).encode("utf-8"), ContentType="text/plain; charset=utf-8")
        except Exception:
            pass

    # 6b. Carry the attachment manifest so the RESUMED session's deliverable can presign
    #     doc citations (agent-invoker reads runs/<sid>/_attachments.json). Resume creates a
    #     new session, so re-derive it. Source robustly: prior execution input first, else the
    #     prior session's OWN _attachments.json file — so a resume-OF-a-resume (whose SFN input
    #     may not carry `attachments`) still resolves doc links.
    _atts = prior_in.get("attachments") or []
    if not _atts:
        try:
            _atts = json.loads(s3.get_object(
                Bucket=RUNS_BUCKET, Key=f"runs/{_safe_seg(exec_name)}/_attachments.json")["Body"].read()) or []
        except Exception:
            _atts = []
    if _atts:
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{new_session}/_attachments.json",
                          Body=json.dumps([{"name": a.get("name"), "ref": a.get("ref")} for a in _atts]).encode("utf-8"),
                          ContentType="application/json")
        except Exception:
            pass

    # 7. Start the new execution at the target layer (ResumeRouter jumps there).
    sfn_input = {
        "mission": prior_in.get("mission", ""),
        "session_id": new_session,
        "user_id": user.get("user_id", "") or prior_in.get("user_id", ""),
        "dry_run": bool(prior_in.get("dry_run", False)),
        "run_config": prior_in.get("run_config", {}) or {},
        "dependency_context": prior_in.get("dependency_context", "") or "",
        "input_context": prior_in.get("input_context", "") or "",
        "results": results,
        "resume_from_index": target,
        "resumed_from_session": exec_name,
        "attachments": _atts,   # propagate so a resume-of-a-resume keeps doc citations working
    }
    # SFN caps StartExecution input at 256KB. Doc-heavy runs inline the full attachment text
    # into `mission`, which blows the cap on resume. When oversized, fall back to the clean
    # `mission_display` — downstream re-run agents work from the reused `results` (which already
    # encode the doc-derived findings), and the deliverable's doc links resolve via
    # _attachments.json (written above), so no evidence is lost.
    if len(json.dumps(sfn_input)) > 240_000:
        sfn_input["mission"] = prior_in.get("mission_display") or prior_in.get("mission", "")
    try:
        sfn.start_execution(stateMachineArn=sm_arn, name=new_session, input=json.dumps(sfn_input))
    except Exception as e:
        return _resp(500, {"error": f"resume start failed: {str(e)[:300]}"})

    return _resp(200, {
        "status": "resume started",
        "session_id": new_session,
        "resumed_from_session": exec_name,
        "resume_from_index": target,
        "reused_agents": reused,
        "rerun_agents": rerun,
        "guidance_applied": bool(guidance),
    })


def _redrive(eco_id: str, exec_name: str) -> dict:
    """POST /runs/{execName}/redrive — resume failed run from failed task.

    Calls stepfunctions:RedriveExecution. Returns 200 with new execution status,
    or 409 if not redrivable (with the SFN-supplied reason).
    """
    sm_arn = _state_machine_arn(eco_id)
    account = sm_arn.split(":")[4]
    exec_arn = f"arn:aws:states:{REGION}:{account}:execution:{PROJECT}-{ENVIRONMENT}-{eco_id}:{exec_name}"

    # Pre-flight: make sure it's actually redrivable so we return a clean 409
    # instead of a 500 from the Lambda's catch-all.
    desc = sfn.describe_execution(executionArn=exec_arn)
    redrive_status = desc.get("redriveStatus")
    if redrive_status != "REDRIVABLE":
        return _resp(409, {
            "error": "execution is not redrivable",
            "redriveStatus": redrive_status,
            "redriveStatusReason": desc.get("redriveStatusReason"),
            "executionStatus": desc.get("status"),
        })

    try:
        resp = sfn.redrive_execution(executionArn=exec_arn)
    except sfn.exceptions.InvalidArn as e:
        return _resp(400, {"error": f"invalid execution arn: {e}"})
    except sfn.exceptions.ExecutionDoesNotExist:
        return _resp(404, {"error": "execution not found"})
    except sfn.exceptions.ExecutionNotRedrivable as e:
        return _resp(409, {"error": f"not redrivable: {e}"})
    except Exception as e:
        return _resp(500, {"error": f"redrive failed: {str(e)[:300]}"})

    return _resp(200, {
        "executionArn": exec_arn,
        "redriveDate": _isoformat(resp.get("redriveDate")),
        "status": "redrive initiated",
    })


def _fetch_s3(uri) -> str:
    if not uri or not isinstance(uri, str) or not uri.startswith("s3://"):
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


def _fetch_declared_agents(eco_id: str) -> list:
    """Best-effort fetch of agent IDs declared in the ecosystem YAML.

    Returns a list of explicit agent IDs (does NOT include compiler-injected
    challenger_agent / report_agent — those only show up if the YAML has
    quality.challenger:true or report:true, which we can't easily know
    without compiling. The frontend handles this by treating any agent in
    the ecosystem config OR seen in the run as known.)
    """
    if not ECOSYSTEMS_BUCKET:
        return []
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        body = obj["Body"].read().decode("utf-8", errors="replace")
    except Exception:
        return []
    # Lightweight YAML parse — avoid pulling pyyaml just for this. Match `id:`
    # entries inside the agents: list. Good enough for our schema.
    import re as _re
    out = []
    in_agents = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("agents:"):
            in_agents = True
            continue
        if in_agents:
            # exit when a top-level YAML key starts (no leading space)
            if stripped and not line.startswith((" ", "\t")) and ":" in stripped:
                break
            m = _re.match(r"-\s+id:\s*([A-Za-z0-9_-]+)", stripped)
            if m:
                out.append(m.group(1))
    # Always append the platform-injected agent ids; UI can discriminate.
    out.extend(["challenger_agent", "report_agent"])
    return out


def _isoformat(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt


def _state_machine_arn(eco_id: str) -> str:
    """Resolve SM arn deterministically from project/env/eco_id."""
    sm_name = f"{PROJECT}-{ENVIRONMENT}-{eco_id}"
    sts = boto3.client("sts", region_name=REGION)
    account = sts.get_caller_identity()["Account"]
    return f"arn:aws:states:{REGION}:{account}:stateMachine:{sm_name}"


# ─── Phase 5f: outcome resolution ───────────────────────────────────────────
#
# When an ecosystem declares ecosystem.outcome.artifacts[], we compute a
# typed outcome record per run by:
#   1. Loading the eco YAML to get the declaration
#   2. For each declared artifact, head-checking S3 for the typed file
#   3. Returning {declared_artifacts, produced_artifacts, status}
# Status semantics:
#   complete  — every declared artifact has a corresponding S3 file
#   partial   — some artifacts produced, some missing
#   empty     — none of the declared artifacts produced
#   none      — ecosystem has no declared artifacts (kind != file_artifact)


def _load_eco_yaml(eco_id: str) -> dict:
    """Fetch the ecosystem YAML from the ecosystems bucket. Returns parsed
    dict or empty dict if not found / unparseable.
    """
    if not ECOSYSTEMS_BUCKET:
        return {}
    try:
        obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
        return yaml.safe_load(obj["Body"].read()) or {}
    except Exception as e:
        print(f"_load_eco_yaml({eco_id}) failed: {e}")
        return {}


_GATE_VERDICT_RE = re.compile(r"##\s*VERDICT\s*\n\s*(STRONG|ADEQUATE|WEAK)\b", re.IGNORECASE)
_GATE_WEAK_RE = re.compile(r"##\s*WEAKNESSES\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL | re.IGNORECASE)
_GATE_BLOCKER_RE = re.compile(r"\bseverity\s*[:=]\s*BLOCKER\b", re.IGNORECASE)


def _summarize_challenger_findings(findings: str) -> dict:
    """Extract verdict + weaknesses + first BLOCKER finding from a challenger
    critique so the operator gate dialogue can lead with the actual assessment
    (mirror of the agent-invoker helper; kept inline since lambdas don't share)."""
    out: dict = {}
    if not findings:
        return out
    vm = _GATE_VERDICT_RE.search(findings)
    if vm:
        out["verdict"] = vm.group(1).upper()
    wm = _GATE_WEAK_RE.search(findings)
    if wm and wm.group(1).strip():
        weak = wm.group(1).strip()
        out["weaknesses"] = weak[:4000]
        for item in re.split(r"\n(?=-\s*finding:)", weak):
            if _GATE_BLOCKER_RE.search(item):
                fm = re.search(r"finding:\s*(.+)", item)
                if fm:
                    out["blocker"] = fm.group(1).strip()[:300]
                break
    return out


def _resolve_pending_gates(eco_id: str, session_id: str, open_states: set | None = None) -> list:
    """Check S3 for gate_decision.json files — indicates an agent is paused in ask mode.
    Returns list of pending gate decisions the operator needs to act on.
    Only returns gates for RUNNING executions (not resolved ones).

    `open_states` is the set of state names currently entered-but-not-exited in
    the execution. A challenger checkpoint gate is only genuinely pending while
    its `Checkpoint{N}` state is still waiting on its task token; once the
    operator resolves it (Accept/Loop/Abort) the state exits. We use this as the
    authoritative signal so a resolved gate's leftover gate_decision.json (S3
    cleanup is best-effort) never re-surfaces on a run-window refresh."""
    pending = []
    prefix = f"runs/{session_id}/"
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=RUNS_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key.endswith("/gate_decision.json"):
                    try:
                        resp = s3.get_object(Bucket=RUNS_BUCKET, Key=key)
                        gate_meta = json.loads(resp["Body"].read())
                    except Exception:
                        continue
                    # Enrich with the reviewer's verdict + first BLOCKER (from the
                    # gate payload) so the dialogue leads with the real assessment
                    # rather than the raw rule-violation string. Back-fills gates
                    # written before this enrichment existed.
                    if "verdict" not in gate_meta and gate_meta.get("payload_key"):
                        try:
                            pobj = s3.get_object(Bucket=RUNS_BUCKET, Key=gate_meta["payload_key"])
                            _findings = ((json.loads(pobj["Body"].read()) or {}).get("structured") or {}).get("findings", "")
                            gate_meta.update(_summarize_challenger_findings(_findings))
                        except Exception:
                            pass
                    # Authoritative staleness filter for per-level checkpoint gates:
                    # only surface if the matching Checkpoint{N} state is still open.
                    if open_states is not None and gate_meta.get("gate_type") == "challenger":
                        layer = gate_meta.get("checkpoint_layer")
                        if layer is None:
                            aid = gate_meta.get("agent_id", "")
                            layer = aid.rsplit("_", 1)[-1] if aid.startswith("checkpoint_") else None
                        state_name = f"Checkpoint{layer}" if layer is not None else None
                        if state_name and state_name not in open_states:
                            print(f"[pending-gates] session={session_id} skipping STALE gate agent={gate_meta.get('agent_id')} (state {state_name} not in open_states={sorted(open_states)})")
                            continue  # resolved/superseded — skip stale gate
                    pending.append(gate_meta)
    except Exception:
        pass
    if pending:
        print(f"[pending-gates] session={session_id} returning {len(pending)} pending: {[ (g.get('agent_id'), g.get('gate_type')) for g in pending ]}")
    return pending


def _resolve_outcome(eco_id: str, session_id: str) -> dict:
    """Compute the typed outcome record for this run.

    Returns:
      {
        kind: <declared kind>,
        declared_artifacts: [{ id, agent, format, save_as, download_label }],
        produced_artifacts: [<artifact_id>, ...],   # those that landed in S3
        missing_artifacts:  [<artifact_id>, ...],   # declared but not produced
        status: "complete" | "partial" | "empty" | "none"
      }
    """
    eco_cfg = _load_eco_yaml(eco_id)
    eco_block = eco_cfg.get("ecosystem") or {}
    outcome_decl = eco_block.get("outcome") or eco_cfg.get("outcome") or {}
    if not isinstance(outcome_decl, dict):
        outcome_decl = {}

    kind = outcome_decl.get("kind", "report")
    declared = outcome_decl.get("artifacts") or []

    # Default outcome for ecosystems without artifact declarations
    if not declared:
        return {
            "kind": kind,
            "declared_artifacts": [],
            "produced_artifacts": [],
            "missing_artifacts": [],
            "status": "none",
        }

    produced: list = []
    missing: list = []
    declared_summary = []
    for art in declared:
        if not isinstance(art, dict):
            continue
        aid = art.get("id")
        producer = art.get("agent")
        fmt = art.get("format", "markdown")
        save_as = art.get("save_as") or f"output.{fmt}"
        s3_key = f"runs/{session_id}/{producer}/{save_as}"
        artifact_key = f"{producer}/{save_as}"
        declared_summary.append({
            "id": aid,
            "agent": producer,
            "format": fmt,
            "save_as": save_as,
            "artifact_key": artifact_key,
            "download_label": art.get("download_label") or aid,
        })
        try:
            s3.head_object(Bucket=RUNS_BUCKET, Key=s3_key)
            produced.append(aid)
        except Exception:
            missing.append(aid)

    if not produced:
        status = "empty"
    elif missing:
        status = "partial"
    else:
        status = "complete"

    return {
        "kind": kind,
        "declared_artifacts": declared_summary,
        "produced_artifacts": produced,
        "missing_artifacts": missing,
        "status": status,
    }
