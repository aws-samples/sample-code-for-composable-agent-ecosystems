"""Scorecard artifact + deploy gate (spec §6.3, §9, §10).

The scorecard is the demo surface and the MRM sign-off artifact. It carries:
  * per-metric MEDIAN ± BAND vs threshold (noise-aware),
  * per-case pass/fail grid + drill-down,
  * the per-issue miss-rate (primary signal),
  * judge explanations (retained as MRM documentation, §6.1),
  * full lineage {ecosystem_version, config_hash, golden_set_version,
    judge_model, timestamp} (§9.2).

The deploy gate consumes the scorecard and behaves per `evaluation.gate`
(block|warn|off), extending the ground-truth `assert_threshold` semantics with
noise bands (§2, §6.3). Everything here is eco-agnostic.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from .aggregate import AggregatedMetric, is_regression, per_issue_miss_rate
from .config import EvaluationConfig


@dataclass
class Scorecard:
    ecosystem_id: str
    ecosystem_version: str
    config_hash: str
    golden_set_version: str
    judge_model: str | None
    timestamp: str
    gate: str
    metrics: list[AggregatedMetric] = field(default_factory=list)
    per_case: dict[str, dict[str, Any]] = field(default_factory=dict)
    case_miss_rate: dict[str, dict[str, Any]] = field(default_factory=dict)
    judge_explanations: dict[str, str] = field(default_factory=dict)
    trajectory: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def metric(self, metric_id: str) -> AggregatedMetric | None:
        for m in self.metrics:
            if m.metric_id == metric_id:
                return m
        return None

    def as_dict(self) -> dict:
        return {
            "lineage": {
                "ecosystem_id": self.ecosystem_id,
                "ecosystem_version": self.ecosystem_version,
                "config_hash": self.config_hash,
                "golden_set_version": self.golden_set_version,
                "judge_model": self.judge_model,
                "timestamp": self.timestamp,
            },
            "gate": self.gate,
            "metrics": [m.as_dict() for m in self.metrics],
            "per_case": self.per_case,
            "case_miss_rate": self.case_miss_rate,
            "judge_explanations": self.judge_explanations,
            "trajectory": self.trajectory,
            "notes": self.notes,
        }


def build_scorecard(
    *,
    ecosystem_id: str,
    ecosystem_version: str,
    config_hash: str,
    golden_set_version: str,
    judge_model: str | None,
    gate: str,
    aggregated: list[AggregatedMetric],
    per_case: dict[str, dict[str, Any]] | None = None,
    case_hits: dict[str, list[bool]] | None = None,
    judge_explanations: dict[str, str] | None = None,
    trajectory: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> Scorecard:
    return Scorecard(
        ecosystem_id=ecosystem_id,
        ecosystem_version=ecosystem_version,
        config_hash=config_hash,
        golden_set_version=golden_set_version,
        judge_model=judge_model,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        gate=gate,
        metrics=list(aggregated),
        per_case=dict(per_case or {}),
        case_miss_rate=per_issue_miss_rate(case_hits) if case_hits else {},
        judge_explanations=dict(judge_explanations or {}),
        trajectory=dict(trajectory or {}),
        notes=list(notes or []),
    )


@dataclass
class GateOutcome:
    """Result of applying the deploy gate to a scorecard."""

    gate: str                          # block | warn | off
    allow_deploy: bool
    failures: list[dict] = field(default_factory=list)     # threshold failures (beyond band)
    regressions: list[dict] = field(default_factory=list)  # vs previous version
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "allow_deploy": self.allow_deploy,
            "failures": self.failures,
            "regressions": self.regressions,
            "summary": self.summary,
        }


def apply_deploy_gate(
    ev: EvaluationConfig,
    scorecard: Scorecard,
    baseline_scorecard: Scorecard | None = None,
) -> GateOutcome:
    """Evaluate the deploy gate (spec §6.3, §10).

    A metric FAILS if, on the noise-aware side of the band, it is still below its
    threshold (median + band < threshold). A REGRESSION is a drop vs the previous
    version that CLEARS the band. Behaviour:
      * block → refuse deploy on any threshold failure OR band-clearing regression
      * warn  → allow deploy, flag the scorecard
      * off   → informational only
    """
    gate = ev.gate
    failures: list[dict] = []
    regressions: list[dict] = []

    for m in scorecard.metrics:
        if m.threshold is not None and not m.passes():
            failures.append({
                "metric_id": m.metric_id,
                "median": round(m.median, 4),
                "band": round(m.band, 4),
                "threshold": m.threshold,
                "shortfall_beyond_band": round(m.threshold - (m.median + m.band), 4),
            })
        if baseline_scorecard is not None:
            base = baseline_scorecard.metric(m.metric_id)
            if base is not None and is_regression(m, base.median):
                regressions.append({
                    "metric_id": m.metric_id,
                    "current_median": round(m.median, 4),
                    "baseline_median": round(base.median, 4),
                    "drop": round(base.median - m.median, 4),
                    "band": round(m.band, 4),
                })

    blocking = bool(failures) or bool(regressions)

    if gate == "off":
        allow = True
        summary = "gate: off — informational only."
    elif gate == "warn":
        allow = True
        summary = ("gate: warn — deploy proceeds; scorecard flagged."
                   if blocking else "gate: warn — no threshold failures or regressions.")
    elif gate == "block":
        allow = not blocking
        if blocking:
            summary = (f"gate: block — DEPLOY REFUSED: {len(failures)} threshold "
                       f"failure(s), {len(regressions)} regression(s) beyond noise band.")
        else:
            summary = "gate: block — all thresholds met (within noise); deploy allowed."
    else:  # defensive; config validation should have caught this
        raise ValueError(f"unknown gate {gate!r}")

    return GateOutcome(gate=gate, allow_deploy=allow, failures=failures,
                       regressions=regressions, summary=summary)
