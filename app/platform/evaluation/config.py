"""Parse & validate the generic `evaluation:` block (spec §3).

The platform reads KEYS; the ecosystem supplies VALUES. This module contains NO
domain knowledge — it validates structure/types only. A malformed block raises
`EvaluationConfigError` (fail loudly / no silent fallback — README principle 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any


class EvaluationConfigError(ValueError):
    """Raised on a structurally invalid `evaluation:` block."""


# The fixed, domain-blind scorer catalog (spec §4). Kept here so config
# validation and the scorer engine agree on one source of truth.
DETERMINISTIC_SCORER_IDS = {
    "categorical",
    "exact_match",       # alias of categorical
    "numeric_tolerance",
    "set_recall",
    "coverage",          # alias of set_recall
    "citation_resolves",
    "abstention",
}
JUDGE_SCORER_IDS = {"bedrock_judge"}
ALL_SCORER_IDS = DETERMINISTIC_SCORER_IDS | JUDGE_SCORER_IDS

# Bedrock built-in LLM-as-judge metrics we support (spec §7.2).
BUILTIN_JUDGE_METRICS = {
    "faithfulness",
    "correctness",
    "completeness",
    "refusal",
    "harmfulness",
    "stereotyping",
    "coherence",
    "relevance",
}
# Reference-free subset usable in production (no ground truth — spec §7.4).
REFERENCE_FREE_JUDGE_METRICS = {
    "faithfulness",
    "completeness",
    "refusal",
    "harmfulness",
    "coherence",
    "relevance",
}

VALID_GATES = {"block", "warn", "off"}
VALID_RUN_MODES = {"headless", "attended"}


@dataclass
class MetricBinding:
    """One row of `evaluation.metrics` (spec §3). Generic: `field`/`expected_value`
    /`custom_rubric` are author values, never platform vocabulary."""

    id: str
    scorer: str
    field: str | None = None
    tol: float = 0.0
    expected_value: Any = None
    cost_weights: dict[str, float] = dc_field(default_factory=dict)
    builtin: list[str] = dc_field(default_factory=list)
    custom_rubric: str | None = None
    threshold: float | None = None

    @property
    def is_deterministic(self) -> bool:
        return self.scorer in DETERMINISTIC_SCORER_IDS

    @property
    def is_judge(self) -> bool:
        return self.scorer in JUDGE_SCORER_IDS

    @property
    def reference_free(self) -> bool:
        """True if this metric needs no golden `expected` (usable online, spec §7.4).

        Deterministic scorers compare to `expected` → NOT reference-free.
        `citation_resolves` compares cited claims to provided evidence → it IS
        reference-free (grounding is self-contained). Judge metrics are
        reference-free only when every builtin is in the reference-free subset
        and there is no custom rubric requiring ground truth.
        """
        if self.scorer == "citation_resolves":
            return True
        if self.is_deterministic:
            return False
        if self.is_judge:
            if self.custom_rubric:
                # custom rubric MAY reference ground truth; conservative → not RF
                return False
            return bool(self.builtin) and all(
                m in REFERENCE_FREE_JUDGE_METRICS for m in self.builtin
            )
        return False


@dataclass
class OnlineConfig:
    """`evaluation.online` (spec §7.4 — B+C+D)."""

    enabled: bool = False
    baseline_sample_rate: float = 0.0
    always_eval_when: list[Any] = dc_field(default_factory=list)


@dataclass
class EvaluationConfig:
    golden_set: str | None = None
    replicates: int = 1
    run_mode: str = "headless"
    trajectory: bool = False
    metrics: list[MetricBinding] = dc_field(default_factory=list)
    thresholds: dict[str, float] = dc_field(default_factory=dict)
    gate: str = "warn"
    online: OnlineConfig = dc_field(default_factory=OnlineConfig)
    judge_model: str | None = None   # optional pin; else auto-selected independent

    @property
    def metric_ids(self) -> list[str]:
        return [m.id for m in self.metrics]

    def metric(self, metric_id: str) -> MetricBinding | None:
        for m in self.metrics:
            if m.id == metric_id:
                return m
        return None

    def reference_free_metrics(self) -> list[MetricBinding]:
        return [m for m in self.metrics if m.reference_free]


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise EvaluationConfigError(msg)


def parse_evaluation_block(config: dict) -> EvaluationConfig | None:
    """Return an `EvaluationConfig` for the ecosystem, or None if none declared.

    Raises `EvaluationConfigError` on a malformed block (fail loudly).
    """
    raw = config.get("evaluation")
    if raw is None:
        return None
    _require(isinstance(raw, dict), "`evaluation` must be a mapping")

    ev = EvaluationConfig()
    ev.golden_set = raw.get("golden_set")
    if ev.golden_set is not None:
        _require(isinstance(ev.golden_set, str), "evaluation.golden_set must be a string (S3 URI)")

    replicates = raw.get("replicates", 1)
    _require(isinstance(replicates, int) and replicates >= 1,
             "evaluation.replicates must be an integer >= 1")
    ev.replicates = replicates

    ev.run_mode = str(raw.get("run_mode", "headless"))
    _require(ev.run_mode in VALID_RUN_MODES,
             f"evaluation.run_mode must be one of {sorted(VALID_RUN_MODES)}")

    ev.trajectory = bool(raw.get("trajectory", False))
    ev.judge_model = raw.get("judge_model")
    if ev.judge_model is not None:
        _require(isinstance(ev.judge_model, str), "evaluation.judge_model must be a string")

    gate = str(raw.get("gate", "warn"))
    _require(gate in VALID_GATES, f"evaluation.gate must be one of {sorted(VALID_GATES)}")
    ev.gate = gate

    # ---- metrics ----
    metrics_raw = raw.get("metrics", []) or []
    _require(isinstance(metrics_raw, list), "evaluation.metrics must be a list")
    seen_ids: set[str] = set()
    for i, m in enumerate(metrics_raw):
        _require(isinstance(m, dict), f"evaluation.metrics[{i}] must be a mapping")
        mid = m.get("id")
        _require(isinstance(mid, str) and mid, f"evaluation.metrics[{i}].id is required")
        _require(mid not in seen_ids, f"duplicate metric id {mid!r}")
        seen_ids.add(mid)
        scorer = m.get("scorer")
        _require(scorer in ALL_SCORER_IDS,
                 f"metric {mid!r}: scorer {scorer!r} not in {sorted(ALL_SCORER_IDS)}")

        mb = MetricBinding(id=mid, scorer=scorer)
        mb.field = m.get("field")
        mb.expected_value = m.get("expected_value")
        mb.custom_rubric = m.get("custom_rubric")
        cw = m.get("cost_weights") or {}
        _require(isinstance(cw, dict), f"metric {mid!r}: cost_weights must be a mapping")
        mb.cost_weights = {str(k): float(v) for k, v in cw.items()}
        if "tol" in m:
            mb.tol = float(m["tol"])

        if scorer in DETERMINISTIC_SCORER_IDS and scorer not in ("citation_resolves",):
            _require(isinstance(mb.field, str) and mb.field,
                     f"deterministic metric {mid!r} requires a `field`")
        if scorer == "citation_resolves" and not mb.field:
            mb.field = "*"          # every cited claim
        if scorer == "abstention":
            _require(mb.expected_value is not None,
                     f"abstention metric {mid!r} requires `expected_value`")

        if scorer == "bedrock_judge":
            builtin = m.get("builtin") or []
            _require(isinstance(builtin, list),
                     f"judge metric {mid!r}: builtin must be a list")
            for b in builtin:
                _require(b in BUILTIN_JUDGE_METRICS,
                         f"judge metric {mid!r}: builtin {b!r} not in {sorted(BUILTIN_JUDGE_METRICS)}")
            mb.builtin = list(builtin)
            _require(bool(mb.builtin) or bool(mb.custom_rubric),
                     f"judge metric {mid!r} needs `builtin` and/or `custom_rubric`")
        ev.metrics.append(mb)

    # ---- thresholds ----
    thr = raw.get("thresholds", {}) or {}
    _require(isinstance(thr, dict), "evaluation.thresholds must be a mapping")
    for k, v in thr.items():
        _require(k in seen_ids, f"threshold references unknown metric id {k!r}")
        _require(isinstance(v, (int, float)), f"threshold {k!r} must be numeric")
        ev.thresholds[k] = float(v)
        mb = ev.metric(k)
        if mb:
            mb.threshold = float(v)

    # ---- online (spec §7.4) ----
    online_raw = raw.get("online", {}) or {}
    _require(isinstance(online_raw, dict), "evaluation.online must be a mapping")
    oc = OnlineConfig()
    oc.enabled = bool(online_raw.get("enabled", False))
    rate = online_raw.get("baseline_sample_rate", 0.0)
    _require(isinstance(rate, (int, float)) and 0.0 <= float(rate) <= 1.0,
             "evaluation.online.baseline_sample_rate must be in [0, 1]")
    oc.baseline_sample_rate = float(rate)
    awe = online_raw.get("always_eval_when", []) or []
    _require(isinstance(awe, list), "evaluation.online.always_eval_when must be a list")
    oc.always_eval_when = list(awe)
    ev.online = oc

    # ---- cross-field: online eval must not use reference-based metrics ----
    if oc.enabled:
        offenders = [m.id for m in ev.metrics if not m.reference_free]
        # Not an error — online simply SKIPS reference-based metrics (spec §7.4).
        # But if EVERY declared metric is reference-based, online has nothing to do.
        if offenders and not ev.reference_free_metrics():
            raise EvaluationConfigError(
                "evaluation.online.enabled but no reference-free metrics declared; "
                "online eval computes quality, never accuracy (spec §7.4). Add at "
                "least one reference-free metric (citation_resolves or a "
                "reference-free bedrock_judge)."
            )
    return ev


def validate_evaluation_block(config: dict) -> list[dict]:
    """Return a list of soft-warning dicts (compiler surfaces them). Hard errors
    still raise via `parse_evaluation_block`. Empty list ⇒ clean."""
    warnings: list[dict] = []
    ev = parse_evaluation_block(config)
    if ev is None:
        return warnings

    if ev.gate == "block" and not ev.golden_set:
        warnings.append({
            "kind": "eval_block_gate_without_golden_set",
            "detail": "evaluation.gate: block but no golden_set declared — the "
                      "gate will have nothing to score against and will fail closed.",
        })
    if ev.metrics and not ev.thresholds:
        warnings.append({
            "kind": "eval_no_thresholds",
            "detail": "evaluation.metrics declared but no thresholds — the deploy "
                      "gate is informational only (nothing can fail).",
        })
    if ev.replicates < 3 and ev.gate == "block":
        warnings.append({
            "kind": "eval_low_replicates_for_block",
            "detail": f"evaluation.replicates={ev.replicates} with gate: block — "
                      "noise bands calibrate poorly below ~3 replicates (spec §6.2); "
                      "a single-run regression may be noise.",
        })
    # declared thresholds on judge metrics with no reference-free path online
    return warnings
