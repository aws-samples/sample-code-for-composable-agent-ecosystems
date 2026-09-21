"""Noise-aware aggregation (spec §6.2, Phase 4).

Lifts the methodology from scripts/quorum_arena_report.py:
  * report each metric as MEDIAN ± BAND over `replicates`;
  * a change counts as a REGRESSION only if its delta CLEARS the noise band
    ("otherwise it is the room being itself");
  * the PRIMARY regression signal is per-issue BINARY miss-rate, which the arena
    showed is far more stable than mean accuracy.

The bands here are eval-metric bands (accuracy/recall/judge-score in [0,1]),
distinct from the arena's discourse bands. A conservative default band is used
where a metric has no calibrated band, and — per the arena's E0 lesson that n=3
estimates ran 3–16× too tight — we also DERIVE an empirical band from the
observed replicate spread and take the LARGER of {default, empirical}. This keeps
the harness honest about LLM noise (spec §1, §11).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


# Conservative default half-range bands for eval metrics (fraction, in [0,1]).
# Deterministic exact-match metrics are near-deterministic per fixed input, so a
# tight band; judge/recall metrics are noisier. Authors never set these — they
# are platform calibration. A metric with no entry uses `_DEFAULT_BAND`.
DEFAULT_NOISE_BANDS: dict[str, float] = {
    "categorical": 0.02,
    "exact_match": 0.02,
    "numeric_tolerance": 0.02,
    "citation_resolves": 0.03,
    "abstention": 0.03,
    "set_recall": 0.08,
    "coverage": 0.08,
    "bedrock_judge": 0.10,
    "trajectory": 0.10,
}
_DEFAULT_BAND = 0.10


def _empirical_band(values: list[float]) -> float:
    """Half-range of observed replicates = (max-min)/2. The arena's spread
    measure. With <2 samples we cannot estimate → 0 (falls back to default)."""
    if len(values) < 2:
        return 0.0
    return (max(values) - min(values)) / 2.0


@dataclass
class AggregatedMetric:
    metric_id: str
    scorer: str
    median: float
    band: float                       # half-range noise band applied
    empirical_band: float             # observed spread/2
    n: int                            # replicate count
    values: list[float] = field(default_factory=list)
    miss_rate: float = 0.0            # fraction of replicates the binary hit failed
    threshold: float | None = None

    def passes(self) -> bool:
        """Threshold check on the noise-aware side of the band. A metric passes
        if median + band >= threshold (i.e. we do NOT fail it for a shortfall
        that sits inside the noise)."""
        if self.threshold is None:
            return True
        return (self.median + self.band) >= self.threshold

    def as_dict(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "scorer": self.scorer,
            "median": round(self.median, 6),
            "band": round(self.band, 6),
            "empirical_band": round(self.empirical_band, 6),
            "n": self.n,
            "miss_rate": round(self.miss_rate, 6),
            "threshold": self.threshold,
            "passes": self.passes(),
            "display": f"{round(self.median, 3)} ±{round(self.band, 3)}",
        }


def aggregate_metric(
    metric_id: str,
    scorer: str,
    replicate_scores: list[float],
    replicate_hits: list[bool] | None = None,
    threshold: float | None = None,
) -> AggregatedMetric:
    """Aggregate one metric across replicates into median ± band + miss-rate."""
    if not replicate_scores:
        raise ValueError(f"metric {metric_id!r}: no replicate scores to aggregate")
    median = statistics.median(replicate_scores)
    emp = _empirical_band(replicate_scores)
    default_band = DEFAULT_NOISE_BANDS.get(scorer, _DEFAULT_BAND)
    band = max(default_band, emp)      # never tighter than default (E0 lesson)
    hits = replicate_hits if replicate_hits is not None else [s >= 1.0 for s in replicate_scores]
    misses = sum(1 for h in hits if not h)
    miss_rate = misses / len(hits) if hits else 0.0
    return AggregatedMetric(
        metric_id=metric_id, scorer=scorer, median=median, band=band,
        empirical_band=emp, n=len(replicate_scores),
        values=list(replicate_scores), miss_rate=miss_rate, threshold=threshold,
    )


def clears_band(current: float, baseline: float, band: float) -> bool:
    """True iff |current - baseline| exceeds the noise band — i.e. the change is
    a real difference, not the room being itself (spec §6.2)."""
    return abs(current - baseline) > band


def is_regression(current: AggregatedMetric, baseline_median: float | None) -> bool:
    """A regression vs a previous version: current dropped below baseline by MORE
    than the band. If there is no baseline, there is no regression (only a
    threshold check applies)."""
    if baseline_median is None:
        return False
    drop = baseline_median - current.median
    return drop > current.band


def per_issue_miss_rate(
    case_hits: dict[str, list[bool]],
) -> dict[str, dict[str, Any]]:
    """Per-golden-case binary miss-rate — the PRIMARY, most stable regression
    signal (spec §6.2). `case_hits` maps a case name → list of per-replicate
    booleans (did the case pass ALL its metrics on that replicate?). Returns
    per-case {missed, runs, miss_rate}."""
    out: dict[str, dict[str, Any]] = {}
    for case, hits in sorted(case_hits.items()):
        runs = len(hits)
        missed = sum(1 for h in hits if not h)
        out[case] = {
            "missed": missed,
            "runs": runs,
            "miss_rate": round(missed / runs, 4) if runs else 0.0,
            "display": f"{missed}/{runs}",
        }
    return out
