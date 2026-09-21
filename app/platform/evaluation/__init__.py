"""Tessera Ecosystem Evaluation Harness (docs/spec-ecosystem-evaluation.md).

Eco-AGNOSTIC by construction: this package reads GENERIC config keys from the
`evaluation:` block and applies a small, fixed catalog of domain-blind scorer
primitives. Domain terms (field names, expected values, rubric text) only ever
appear as *author-supplied values*, never in this code. See spec §3, §4.

Phases (spec §12):
  1. Core       — config parse/validate + deterministic scorers + eval state
                  machine + scorecard + deploy gate.        (config, scorers,
                  eval_compiler, scorecard)
  2. Judge      — Bedrock Evaluations BYOI + builtin/custom metrics + pinned
                  INDEPENDENT judge model.                  (judge)
  3. Trajectory — AgentCore Evaluations over ADOT/OTel spans (offline+online).
                  (trajectory)
  4. Stability  — replication + noise bands + per-issue miss-rate.  (aggregate)
  5. Authoring  — builder eval-proposal pass.               (propose)
  6. Online     — B+C+D reference-free production monitoring + golden-set
                  feedback loop.                            (online)

Nothing here imports boto3 at module load beyond what a given adapter needs, so
the pure scoring/aggregation/compiler core stays importable and unit-testable in
any environment.
"""

from .config import (
    EvaluationConfig,
    MetricBinding,
    OnlineConfig,
    EvaluationConfigError,
    parse_evaluation_block,
    validate_evaluation_block,
)
from .scorers import (
    SCORER_PRIMITIVES,
    DETERMINISTIC_SCORERS,
    JUDGE_SCORERS,
    score_metric,
    MetricScore,
)
from .aggregate import (
    DEFAULT_NOISE_BANDS,
    aggregate_metric,
    per_issue_miss_rate,
    clears_band,
    AggregatedMetric,
)
from .scorecard import (
    Scorecard,
    build_scorecard,
    apply_deploy_gate,
    GateOutcome,
)

__all__ = [
    "EvaluationConfig",
    "MetricBinding",
    "OnlineConfig",
    "EvaluationConfigError",
    "parse_evaluation_block",
    "validate_evaluation_block",
    "SCORER_PRIMITIVES",
    "DETERMINISTIC_SCORERS",
    "JUDGE_SCORERS",
    "score_metric",
    "MetricScore",
    "DEFAULT_NOISE_BANDS",
    "aggregate_metric",
    "per_issue_miss_rate",
    "clears_band",
    "AggregatedMetric",
    "Scorecard",
    "build_scorecard",
    "apply_deploy_gate",
    "GateOutcome",
]
