"""Deterministic decision engine (spec-decision-engine.md).

Domain-blind: scored items -> labels (rubric) -> outcome (decision rule), computed
deterministically with a trace. All semantics come from the eco's `decision:` config;
this package only provides the evaluator, validation, and trace.
"""
from .engine import (
    COMMON_DIMENSIONS,
    STATUS_VALUES,
    DecisionConfigError,
    validate_config,
    evaluate,
)

__all__ = [
    "COMMON_DIMENSIONS",
    "STATUS_VALUES",
    "DecisionConfigError",
    "validate_config",
    "evaluate",
]
