"""Bedrock Evaluations bring-your-own-inference (BYOI) judge adapter (spec §7.1,
§7.2, Phase 2).

Tessera outputs come from a multi-agent Strands pipeline, not a single FM call,
so classic model-eval does not fit. Bedrock Evaluations' BYOI mode evaluates
outputs from ANY system given a dataset. This adapter:
  * selects an INDEPENDENT judge model, pinned distinct from every model the
    eco's agents use (decision §13.2) — recorded on the scorecard (§9.2);
  * formats {input, output, ground_truth} per golden case (BYOI dataset);
  * maps builtin metrics (faithfulness/…/stereotyping) + custom rubrics (≤10);
  * submits the eval job and (caller) polls to completion.

Model-selection + dataset-building are PURE and unit-testable; the actual
`bedrock:CreateEvaluationJob` call is isolated in `submit_judge_job` which the
Lambda invokes. Domain-specificity flows only through author `custom_rubric`
values — the pipe is generic (§7.2).
"""

from __future__ import annotations

from typing import Any

from .config import EvaluationConfig

# Independent-judge candidate pool, strongest-first. Pinned distinct from the
# eco's agent models. These mirror the runtime's MODEL_ALIASES resolved ids
# (tessera_platform/agentcore/src/main.py) so "distinct" compares like-for-like.
JUDGE_CANDIDATES = [
    "us.anthropic.claude-opus-4-7",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.meta.llama3-3-70b-instruct-v1:0",
]

# Model aliases → resolved ids (subset of runtime's map) so we can normalise an
# eco's declared `model:` alias to the concrete id for the independence check.
_ALIAS_TO_ID = {
    "opus": "us.anthropic.claude-opus-4-7",
    "sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "sonnet5": "us.anthropic.claude-sonnet-5",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "nova-lite": "us.amazon.nova-lite-v1:0",
    "nova-pro": "us.amazon.nova-pro-v1:0",
    "llama-70b": "us.meta.llama3-3-70b-instruct-v1:0",
    "deepseek-r1": "us.deepseek.r1-v1:0",
    "deepseek-v3": "deepseek.v3.2",
}

# Platform tier defaults (mirror runtime SYNTHESIZER/LEAF model ids), so an eco
# that pins no `model:` still contributes its EFFECTIVE agent models to the
# independence check.
DEFAULT_SYNTHESIZER_ID = "us.anthropic.claude-opus-4-7"
DEFAULT_LEAF_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


class JudgeSelectionError(RuntimeError):
    """Raised when no independent judge can be pinned (fail loudly)."""


def _resolve_alias(model: str | None) -> str | None:
    if not model:
        return None
    return _ALIAS_TO_ID.get(model, model)


def ecosystem_agent_models(config: dict) -> set[str]:
    """Return the set of concrete model ids the eco's agents will use — explicit
    `model:` pins PLUS the tier defaults for agents that pin nothing. This is the
    set the judge must be distinct from (§7.2)."""
    models: set[str] = set()
    any_leaf = any_synth = False
    for a in config.get("agents", []) or []:
        m = _resolve_alias((a or {}).get("model"))
        if m:
            models.add(m)
            continue
        # no explicit pin → contributes its tier default
        if (a or {}).get("depends_on") and str((a or {}).get("output_kind", "")).lower() not in ("researcher",):
            any_synth = True
        else:
            any_leaf = True
    # a model_profile at run time can change these, but the DEFAULT config is the
    # honest baseline for the independence guarantee.
    if any_synth:
        models.add(DEFAULT_SYNTHESIZER_ID)
    if any_leaf:
        models.add(DEFAULT_LEAF_ID)
    return models


def select_independent_judge(config: dict, ev: EvaluationConfig) -> str:
    """Pick a judge model pinned DISTINCT from every model the eco's agents use
    (decision §13.2). If the author pinned `evaluation.judge_model`, honor it
    only if it is genuinely independent (else fail loudly — do not silently use a
    non-independent judge). Otherwise pick the first independent candidate."""
    agent_models = ecosystem_agent_models(config)

    if ev.judge_model:
        pinned = _resolve_alias(ev.judge_model)
        if pinned in agent_models:
            raise JudgeSelectionError(
                f"evaluation.judge_model={ev.judge_model!r} is ALSO used by the "
                "ecosystem's agents — an independent judge is required (spec §7.2). "
                "Pin a different judge model or remove the pin to auto-select.")
        return pinned

    for cand in JUDGE_CANDIDATES:
        if cand not in agent_models:
            return cand

    raise JudgeSelectionError(
        "no independent judge available: the ecosystem's agents span every judge "
        f"candidate {JUDGE_CANDIDATES}. Add a distinct model to the judge pool.")


def build_byoi_dataset(ev: EvaluationConfig, cases: list[dict],
                        run_outputs: dict[str, list[dict]]) -> list[dict]:
    """Format the BYOI dataset (spec §7.1): one record per (case × replicate),
    each `{prompt/input, modelResponse/output, referenceResponse/ground_truth}`.

    `run_outputs` maps case_name → list of per-replicate output dicts. Generic:
    we serialise the eco's own inputs/output/expected verbatim."""
    records: list[dict] = []
    for case in cases:
        name = case.get("name")
        outs = run_outputs.get(name, [])
        for rep_idx, out in enumerate(outs):
            records.append({
                "case": name,
                "replicate": rep_idx,
                "prompt": case.get("inputs") or case.get("mission") or {},
                "modelResponse": out,
                "referenceResponse": case.get("expected") or {},
            })
    return records


def build_custom_metric_defs(ev: EvaluationConfig) -> list[dict]:
    """Collect author custom rubrics as Bedrock custom-metric definitions (≤10
    per job, §7.2). The rubric TEXT is an author value — the pipe is generic."""
    defs: list[dict] = []
    for m in ev.metrics:
        if m.is_judge and m.custom_rubric:
            defs.append({
                "metric_id": m.id,
                "name": m.id,
                "instructions": m.custom_rubric,     # author-supplied
            })
    if len(defs) > 10:
        raise ValueError(
            f"{len(defs)} custom judge rubrics declared; Bedrock allows <=10 per "
            "job (spec §7.2). Split the evaluation or reduce custom metrics.")
    return defs


def collect_builtin_metrics(ev: EvaluationConfig) -> list[str]:
    builtins: set[str] = set()
    for m in ev.metrics:
        if m.is_judge:
            builtins.update(m.builtin)
    return sorted(builtins)


def submit_judge_job(*, bedrock_client, job_name: str, judge_model: str,
                     dataset_s3_uri: str, output_s3_uri: str,
                     builtin_metrics: list[str], custom_metrics: list[dict],
                     eval_role_arn: str) -> str:
    """Submit a Bedrock Evaluations BYOI job (isolated boto3 call). Returns the
    job ARN. Fails loudly on API error (no silent fallback)."""
    evaluation_config: dict[str, Any] = {
        "automated": {
            "datasetMetricConfigs": [{
                "taskType": "General",
                "dataset": {"name": "tessera-byoi", "datasetLocation": {"s3Uri": dataset_s3_uri}},
                # Bedrock requires custom-metric names to ALSO appear here alongside
                # the built-in metric names, else: "custom metrics are not configured
                # in the dataset metric config".
                "metricNames": builtin_metrics + [cm["name"] for cm in custom_metrics],
            }],
            "evaluatorModelConfig": {
                "bedrockEvaluatorModels": [{"modelIdentifier": judge_model}],
            },
        }
    }
    if custom_metrics:
        # Bedrock requires customMetricConfig to carry its OWN evaluatorModelConfig
        # (the judge model that scores the custom rubrics). Omitting it raises:
        # "Missing required parameter in evaluationConfig.automated.customMetricConfig:
        #  evaluatorModelConfig".
        evaluation_config["automated"]["customMetricConfig"] = {
            "customMetrics": [
                {"customMetricDefinition": {
                    "name": cm["name"], "instructions": cm["instructions"]}}
                for cm in custom_metrics
            ],
            "evaluatorModelConfig": {
                "bedrockEvaluatorModels": [{"modelIdentifier": judge_model}],
            },
        }

    resp = bedrock_client.create_evaluation_job(
        jobName=job_name,
        roleArn=eval_role_arn,
        applicationType="ModelEvaluation",
        evaluationConfig=evaluation_config,
        inferenceConfig={"models": [{"precomputedInferenceSource": {"inferenceSourceIdentifier": "tessera-byoi"}}]},
        outputDataConfig={"s3Uri": output_s3_uri},
    )
    return resp["jobArn"]
