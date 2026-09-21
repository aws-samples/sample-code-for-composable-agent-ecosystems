"""online-eval Lambda (spec §7.4, decision §13.4, Phase 6).

Invoked ASYNCHRONOUSLY off the trace / S3 output of a COMPLETED production run
(zero added latency to the live run — decision D). Decides whether to evaluate
this run (B: per-eco opt-in baseline sample; C: risk-triggered to 100%), runs
ONLY reference-free scorers (never accuracy), records a drift datapoint, and —
when the run is flagged — writes a CANDIDATE golden case for SME labelling
(golden-set feedback loop, ties into spec-quality-feedback-loop.md).

Eco-AGNOSTIC: reference-free metrics + risk conditions come from the generic
`evaluation.online` block.
"""
import datetime
import json
import os

import boto3
import yaml

from tessera_platform.evaluation.config import parse_evaluation_block
from tessera_platform.evaluation.online import (
    should_evaluate_run, online_metrics, build_golden_candidate,
)
from tessera_platform.evaluation.scorers import score_metric

ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
s3 = boto3.client("s3")


def _read_eco(eco_id):
    return yaml.safe_load(s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read())


def _normalize_event(event):
    """If invoked by the EventBridge Step Functions 'Execution Status Change' rule,
    map it to the flat {eco_id, session_id, output, ...} shape. Direct invocations
    (already flat) pass through unchanged. Skips the eval SMs themselves
    (names ending in '-eval') so eval runs don't recursively trigger online eval."""
    if "eco_id" in event:
        return event
    detail = event.get("detail") or {}
    sm_arn = detail.get("stateMachineArn", "")
    status = detail.get("status", "")
    # tessera-<env>-<eco_id> → eco_id (strip project-env prefix)
    sm_name = sm_arn.rsplit(":", 1)[-1]
    eco_id = sm_name
    for pref in ("tessera-dev-", "tessera-prod-", "tessera-"):
        if sm_name.startswith(pref):
            eco_id = sm_name[len(pref):]
            break
    if eco_id.endswith("-eval"):
        return {"_skip": True}   # never online-eval an eval run
    out = {}
    try:
        out = json.loads(detail.get("output") or "{}")
    except (json.JSONDecodeError, TypeError):
        out = {}
    inp = {}
    try:
        inp = json.loads(detail.get("input") or "{}")
    except (json.JSONDecodeError, TypeError):
        inp = {}
    return {
        "eco_id": eco_id,
        "session_id": inp.get("session_id") or detail.get("name") or detail.get("executionArn", ""),
        "inputs": inp.get("inputs") or {},
        "output": out if isinstance(out, dict) else {},
        "run_signals": {
            "run_status": status,
            **(inp.get("run_signals") or {}),
        },
    }


def handler(event, context):
    """event: {eco_id, session_id, inputs, output, run_signals}. `run_signals`
    carries the generic run-level flags the platform already tracks (gate_fired,
    human_override, faithfulness, value/amount, …).

    Also accepts an EventBridge Step Functions "Execution Status Change" envelope
    (async trigger, decision D — zero added latency to the live run): we normalise
    it to the flat shape below."""
    event = _normalize_event(event)
    if event.get("_skip"):
        return {"evaluated": False, "reason": "skipped (eval run or non-eco execution)"}
    eco_id = event["eco_id"]
    session_id = event["session_id"]
    config = _read_eco(eco_id)
    ev = parse_evaluation_block(config)
    if ev is None or not ev.online.enabled:
        return {"evaluated": False, "reason": "online eval not enabled"}

    decision = should_evaluate_run(ev, session_id, event.get("run_signals") or {})
    if not decision.evaluate:
        return {"evaluated": False, "reason": decision.reason}

    output = event.get("output") or {}
    # reference-free scoring ONLY (§7.4). Deterministic RF = citation_resolves;
    # judge RF metrics are delegated (recorded but scored by a judge job — here we
    # compute the in-platform RF signals synchronously and note judge metrics).
    scores = {}
    for mb in online_metrics(ev):
        if mb.scorer == "citation_resolves":
            ms = score_metric(mb, output, {})
            scores[mb.id] = {"value": ms.value, "hit": ms.hit, "detail": ms.detail}
        else:
            scores[mb.id] = {"deferred_to_judge": True, "builtin": mb.builtin}

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    drift_key = f"evaluations/{eco_id}/online/{ts}-{session_id[:12]}.json"
    record = {
        "eco_id": eco_id, "session_id": session_id, "timestamp": ts,
        "trigger": decision.reason, "force_full": decision.force_full,
        "scores": scores,
    }
    s3.put_object(Bucket=RUNS_BUCKET, Key=drift_key,
                  Body=json.dumps(record, default=str).encode(),
                  ContentType="application/json")

    # Golden-set feedback loop: a risk-flagged run becomes a CANDIDATE golden case.
    candidate_key = None
    if decision.force_full or any(
            isinstance(v, dict) and v.get("hit") is False for v in scores.values()):
        candidate = build_golden_candidate(
            eco_id=eco_id, session_id=session_id, inputs=event.get("inputs") or {},
            output=output, online_scores=scores, trigger_reason=decision.reason)
        candidate_key = f"evaluations/{eco_id}/candidates/{ts}-{session_id[:12]}.json"
        s3.put_object(Bucket=RUNS_BUCKET, Key=candidate_key,
                      Body=json.dumps(candidate, default=str).encode(),
                      ContentType="application/json")

    return {"evaluated": True, "reason": decision.reason,
            "drift_key": drift_key, "candidate_key": candidate_key, "scores": scores}
