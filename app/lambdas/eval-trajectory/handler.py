"""eval-trajectory Lambda (spec §7.3, Phase 3).

Scores the agent PATH (tool selection / params / synthesis) from the ADOT/OTel
spans emitted by each headless run. Runs the domain-blind mechanics heuristic
in-platform (invalid-tool / empty-param detection) and, when AgentCore
Evaluations is available, submits a trajectory eval over the run's trace.

Eco-AGNOSTIC: tool-use mechanics are domain-blind. Spans are read from the runs
bucket where the runtime writes them (traces/<session_id>/spans.json)."""
import json
import os

import boto3
import yaml

from tessera_platform.evaluation.runner import config_from_dict
from tessera_platform.evaluation.trajectory import (
    extract_trajectory, score_trajectory_heuristic,
)

ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
s3 = boto3.client("s3")


def _read_eco(eco_id):
    return yaml.safe_load(s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read())


def _allowed_tools(config):
    tools = set()
    for a in config.get("agents", []) or []:
        for t in (a.get("tools") or []):
            tools.add(t)
    return tools


def _load_spans(session_id):
    if not session_id:
        return []
    for key in (f"traces/{session_id}/spans.json", f"runs/{session_id}/_spans.json"):
        try:
            return json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read())
        except Exception:
            continue
    return []


def handler(event, context):
    eco_id = event["eco_id"]
    config = _read_eco(eco_id)
    allowed = _allowed_tools(config)
    run_results = event.get("run_results", [])
    if isinstance(run_results, dict):
        run_results = run_results.get("body", run_results)

    path_validity_scores = []
    per_run = []
    for item in run_results or []:
        run = item.get("run") or {}
        session_id = (run.get("session_id") or item.get("session_id")
                      or f"eval-{item.get('case_name')}-r{item.get('replicate', 0)}")
        spans = _load_spans(session_id)
        traj = extract_trajectory(spans, session_id)
        h = score_trajectory_heuristic(traj, allowed)
        path_validity_scores.append(h["path_validity"])
        per_run.append({"session_id": session_id, **h})

    # threshold (optional) can be declared as a trajectory metric threshold; the
    # aggregator applies it. Reported here for the scorecard trajectory panel.
    return {
        "path_validity_scores": path_validity_scores,
        "per_run": per_run,
        "tool_calls_total": sum(r["tool_calls"] for r in per_run),
    }
