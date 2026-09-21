"""Online evaluation — B+C+D production monitoring + golden-set feedback loop
(spec §7.4, decision §13.4, Phase 6).

Production has NO ground truth, so online eval uses only REFERENCE-FREE scorers
(faithfulness, completeness, refusal, harmfulness, coherence, relevance,
trajectory-validity, citation_resolves) — it measures QUALITY, never ACCURACY.

Scope (all eco-declared, §3 `online:`):
  * B — per-eco opt-in with `baseline_sample_rate` (owner controls cost);
  * C — risk-triggered to 100% on `always_eval_when` conditions;
  * D — always ASYNC off the trace/S3 output → ZERO added latency to the live run.

Value: drift detection, continuous MRM monitoring, and a golden-set feedback
loop — flagged production runs become CANDIDATE golden cases that, once
SME-labelled, join the offline set (§7.4; ties into spec-quality-feedback-loop).

The sampling decision (`should_evaluate_run`) is pure and unit-testable; the
async dispatch + candidate write are isolated for the Lambda.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .config import EvaluationConfig, OnlineConfig


@dataclass
class OnlineDecision:
    evaluate: bool
    reason: str                 # "risk:<cond>" | "baseline_sample" | "not_sampled" | "disabled"
    force_full: bool = False    # risk-triggered → 100%


def _deterministic_sample(session_id: str, rate: float) -> bool:
    """Stable per-session sampling so a run is either in or out consistently
    (no double-dipping across retries). Hash the session id to [0,1)."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    h = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    return frac < rate


def _risk_triggered(online: OnlineConfig, run_signals: dict) -> str | None:
    """Return the matched risk condition name, or None. `run_signals` carries
    generic run-level flags the platform already tracks: gate_fired,
    human_override, faithfulness score, value/amount, etc. The condition VALUES
    are author-declared (§3 always_eval_when)."""
    for cond in online.always_eval_when:
        if isinstance(cond, str):
            if run_signals.get(cond):
                return cond
        elif isinstance(cond, dict):
            for key, threshold in cond.items():
                # generic comparators: "<field>_below" / "<field>_above" / exact
                if key.endswith("_below"):
                    field = key[:-len("_below")]
                    val = run_signals.get(field)
                    if isinstance(val, (int, float)) and val < threshold:
                        return key
                elif key.endswith("_above"):
                    field = key[:-len("_above")]
                    val = run_signals.get(field)
                    if isinstance(val, (int, float)) and val > threshold:
                        return key
                else:
                    if run_signals.get(key) == threshold:
                        return key
    return None


def should_evaluate_run(ev: EvaluationConfig, session_id: str,
                        run_signals: dict | None = None) -> OnlineDecision:
    """Decide whether a production run should be evaluated online (B+C).

    Order: disabled → risk-trigger to 100% (C) → baseline sample (B)."""
    online = ev.online
    if not online.enabled:
        return OnlineDecision(False, "disabled")
    run_signals = run_signals or {}
    risk = _risk_triggered(online, run_signals)
    if risk is not None:
        return OnlineDecision(True, f"risk:{risk}", force_full=True)
    if _deterministic_sample(session_id, online.baseline_sample_rate):
        return OnlineDecision(True, "baseline_sample")
    return OnlineDecision(False, "not_sampled")


def online_metrics(ev: EvaluationConfig) -> list:
    """The metrics that run online: reference-free ONLY (§7.4). Never accuracy."""
    return ev.reference_free_metrics()


def build_golden_candidate(*, eco_id: str, session_id: str, inputs: dict,
                           output: dict, online_scores: dict,
                           trigger_reason: str) -> dict:
    """A flagged production run → CANDIDATE golden case (unlabelled). The SME
    later fills `expected`/`labels` and promotes it into the offline set
    (§7.4). Generic shape mirrors §5.3 with `reviewed: false`."""
    return {
        "name": f"prod-{session_id[:12]}",
        "inputs": inputs,
        "expected": {},                      # SME fills this in
        "labels": {
            "source": "online_eval",
            "eco_id": eco_id,
            "session_id": session_id,
            "trigger": trigger_reason,
            "online_scores": online_scores,
            "reviewed": False,
        },
        "_observed_output": output,          # kept to help the SME label
    }
