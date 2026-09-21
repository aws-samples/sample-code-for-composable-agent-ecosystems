"""Pure runner glue used by the eval Lambdas (no boto3).

Ties the scorer primitives → noise-band aggregation → scorecard together so the
eval-runner / eval-aggregator Lambda handlers stay thin. Unit-testable.
"""

from __future__ import annotations

import json
from typing import Any

from .config import EvaluationConfig, parse_evaluation_block
from .scorers import score_metric, MetricScore
from .aggregate import aggregate_metric
from .scorecard import build_scorecard, apply_deploy_gate


def load_golden_cases(jsonl_body: str) -> list[dict]:
    """Parse a cases.jsonl body (spec §5.3). Fails loudly on a malformed line."""
    cases = []
    for i, line in enumerate(jsonl_body.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"golden set line {i+1} is not valid JSON: {e}") from e
    if not cases:
        raise ValueError("golden set is empty")
    return cases


def expand_work_items(cases: list[dict], replicates: int) -> list[dict]:
    """Flatten (case × replicate) into Map work items for the eval SM (§5.1)."""
    items = []
    for case in cases:
        name = case.get("name")
        if not name:
            raise ValueError(f"golden case missing `name`: {case!r}")
        inputs = case.get("inputs", {})
        # The ENTRY agent receives its run-time input via `mission` — the compiled
        # run SM's Layer0 payload forwards mission but NOT input_context (input_context
        # is only threaded to downstream layers). So the golden-case data MUST be in
        # `mission` for the entry agent to see it. We keep input_context too (used by
        # downstream layers). Generic: we serialise whatever inputs the case declares.
        serialized = json.dumps(inputs, indent=2, default=str) if inputs else ""
        mission = case.get("mission") or ""
        if not mission and serialized:
            mission = ("Here is the run-time input (an already-scored alert / case) to "
                       "process for this evaluation:\n\n" + serialized)
        input_context = case.get("input_context") or serialized
        for r in range(replicates):
            items.append({
                "case_name": name,
                "replicate": r,
                "mission": mission,
                "inputs": inputs,
                "input_context": input_context,
                "session_id": f"eval-{name}-r{r}",
            })
    return items


def resolve_load_plan(cases: list[dict], default_replicates: int,
                      overrides: dict | None) -> dict:
    """Apply per-run Quick-run OVERRIDES to a loaded golden set (spec: Quick-run
    controls). PURE + eco-agnostic — no S3, no domain vocabulary. Overrides never
    mutate the stored `evaluation:` block; they yield EFFECTIVE values for this run
    only. Fails loudly on malformed values (no silent fallback).

    overrides (optional): {replicates?: int >= 1, max_cases?: int >= 1}
      - replicates → used INSTEAD of `default_replicates`.
      - max_cases  → truncates `cases[:max_cases]` BEFORE expanding work items.

    Returns {cases, work_items, replicates (effective), case_count,
             overrides_applied}.
    """
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"overrides must be an object, got {type(overrides).__name__}")

    effective_replicates = default_replicates
    if overrides.get("replicates") is not None:
        try:
            effective_replicates = int(overrides["replicates"])
        except (TypeError, ValueError):
            raise ValueError(f"overrides.replicates must be an int, got {overrides['replicates']!r}")
        if effective_replicates < 1:
            raise ValueError(f"overrides.replicates must be >= 1, got {effective_replicates}")

    effective_cases = cases
    if overrides.get("max_cases") is not None:
        try:
            max_cases = int(overrides["max_cases"])
        except (TypeError, ValueError):
            raise ValueError(f"overrides.max_cases must be an int, got {overrides['max_cases']!r}")
        if max_cases < 1:
            raise ValueError(f"overrides.max_cases must be >= 1, got {max_cases}")
        effective_cases = cases[:max_cases]

    return {
        "cases": effective_cases,
        "work_items": expand_work_items(effective_cases, effective_replicates),
        "replicates": effective_replicates,
        "case_count": len(effective_cases),
        "overrides_applied": {
            "replicates": overrides.get("replicates"),
            "max_cases": overrides.get("max_cases"),
        },
    }


def _extract_output(run_result: dict) -> dict:
    """Pull the eco's structured output from a headless run result. The run SM
    returns its Output as a JSON string or dict; we normalise to a dict. A failed
    run (captured by the Map Catch) yields {} + an error marker."""
    run = run_result.get("run") or {}
    out = run.get("output")
    if out is None:
        return {"_run_error": run.get("error", "no output")}
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except json.JSONDecodeError:
            return {"_raw_output": out}
    if isinstance(out, dict):
        # runs commonly nest the structured findings under a known key; accept a
        # few generic shapes without inventing domain keys.
        for key in ("structured_findings", "output", "result", "results"):
            if key in out and isinstance(out[key], dict):
                return out[key]
        return out
    return {"_raw_output": str(out)}


def score_deterministic(ev: EvaluationConfig, cases: list[dict],
                        run_results: list[dict]) -> dict:
    """Run deterministic scorers over all (case × replicate) outputs.

    `run_results` is the Map output: a list aligned to the flattened work items,
    each carrying `case_name`, `replicate`, and the run `output`. Returns:
      {
        "per_metric": {metric_id: [score,...] across all replicates},
        "per_case_hits": {case_name: [all-metrics-passed? per replicate]},
        "per_case_detail": {case_name: {metric_id: {replicate: score_dict}}},
      }
    """
    case_by_name = {c["name"]: c for c in cases}
    det_metrics = [m for m in ev.metrics if m.is_deterministic]

    per_metric: dict[str, list[float]] = {m.id: [] for m in det_metrics}
    # per case → replicate → all-metric-pass
    per_case_reps: dict[str, dict[int, bool]] = {}
    per_case_detail: dict[str, dict[str, dict]] = {}

    for rr in run_results:
        name = rr.get("case_name")
        rep = int(rr.get("replicate", 0))
        expected = (case_by_name.get(name) or {}).get("expected", {})
        output = _extract_output(rr)
        all_hit = True
        for mb in det_metrics:
            ms: MetricScore = score_metric(mb, output, expected)
            per_metric[mb.id].append(ms.value)
            all_hit = all_hit and ms.hit
            per_case_detail.setdefault(name, {}).setdefault(mb.id, {})[rep] = ms.as_dict()
        per_case_reps.setdefault(name, {})[rep] = all_hit

    per_case_hits = {name: [reps[r] for r in sorted(reps)]
                     for name, reps in per_case_reps.items()}
    return {
        "per_metric": per_metric,
        "per_case_hits": per_case_hits,
        "per_case_detail": per_case_detail,
    }


def assemble_scorecard(
    *,
    ev: EvaluationConfig,
    eco_id: str,
    ecosystem_version: str,
    config_hash: str,
    golden_set_version: str,
    judge_model: str | None,
    deterministic: dict,
    judge: dict | None = None,
    trajectory: dict | None = None,
    baseline_scorecard=None,
) -> dict:
    """Merge deterministic + judge + trajectory scores into a Scorecard, apply the
    deploy gate, and return {scorecard, gate}. Judge/trajectory results are
    optional (present only when those phases ran)."""
    aggregated = []
    per_metric = deterministic.get("per_metric", {})
    for mb in ev.metrics:
        if mb.is_deterministic:
            scores = per_metric.get(mb.id) or []
            if not scores:
                continue
            aggregated.append(aggregate_metric(
                mb.id, mb.scorer, scores,
                replicate_hits=[s >= 1.0 for s in scores], threshold=mb.threshold))

    judge_explanations: dict[str, str] = {}
    if judge:
        for mid, jr in (judge.get("per_metric") or {}).items():
            scores = jr.get("scores") or []
            if not scores:
                continue
            mb = ev.metric(mid)
            aggregated.append(aggregate_metric(
                mid, "bedrock_judge", scores, threshold=(mb.threshold if mb else None)))
            if jr.get("explanation"):
                judge_explanations[mid] = jr["explanation"]

    traj_scores = None
    if trajectory:
        pv = trajectory.get("path_validity_scores") or []
        if pv:
            aggregated.append(aggregate_metric(
                "trajectory_path_validity", "trajectory", pv,
                threshold=trajectory.get("threshold")))
        traj_scores = trajectory

    scorecard = build_scorecard(
        ecosystem_id=eco_id,
        ecosystem_version=ecosystem_version,
        config_hash=config_hash,
        golden_set_version=golden_set_version,
        judge_model=judge_model,
        gate=ev.gate,
        aggregated=aggregated,
        per_case=deterministic.get("per_case_detail", {}),
        case_hits=deterministic.get("per_case_hits", {}),
        judge_explanations=judge_explanations,
        trajectory=traj_scores or {},
    )
    outcome = apply_deploy_gate(ev, scorecard, baseline_scorecard=baseline_scorecard)
    return {"scorecard": scorecard.as_dict(), "gate": outcome.as_dict()}


def config_from_dict(evaluation_config: dict) -> EvaluationConfig:
    """Re-hydrate an EvaluationConfig from a serialised `{evaluation: {...}}` (the
    Lambdas pass the block around as JSON)."""
    if "evaluation" in evaluation_config:
        wrapper = evaluation_config
    else:
        wrapper = {"evaluation": evaluation_config}
    ev = parse_evaluation_block(wrapper)
    if ev is None:
        raise ValueError("no evaluation block to hydrate")
    return ev
