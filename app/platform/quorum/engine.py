"""The Quorum engine — the single entrypoint that runs a decision through the archetype.

    run_quorum(config, decision, context, pool, speaker, emit) -> QuorumResult

Pure orchestration: convene (empanelment) → deliberate → verdict, emitting AG-UI-style
events via `emit` and returning the replayable artifacts (plan + transcript + verdict).
Substrate/LLM-agnostic — the caller supplies the persona pool and a Speaker.
"""
from __future__ import annotations

from typing import Callable, Optional

from .deliberation import Speaker, deliberate, _emit
from .empanelment import empanel
from .models import Persona, QuorumResult
from .verdict import render_verdict

Emit = Callable[[dict], None]


def quorum_policy(config: dict, overrides: Optional[dict] = None) -> dict:
    """Extract the `quorum:` policy block from an eco config, with defaults.

    Also resolves the full RUN SETTINGS (settings.py) and carries them under "settings", so every
    behavioural knob has one home and can be recorded with the run. The flat legacy keys are kept so
    existing callers are untouched."""
    from .settings import resolve as _resolve_settings
    q = (config or {}).get("quorum", {}) or {}
    empan = q.get("empanelment", {}) or {}
    delib = q.get("deliberation", {}) or {}
    return {
        "select": empan.get("select", {}) or {},
        "completeness_adversary": empan.get("completeness_adversary", True),
        "justify_exclusions": empan.get("justify_exclusions", True),
        "max_rounds": delib.get("max_rounds", 4),
        "turn_policy": delib.get("turn_policy", "moderated"),
        "escalation": delib.get("escalation", True),
        "preserve_dissent": delib.get("preserve_dissent", True),
        "verdict": q.get("verdict", {}) or {},
        "whiteboard": q.get("whiteboard", {}) or {},   # spec-quorum-whiteboard.md
        "settings": _resolve_settings(config, overrides),
    }


def run_quorum(
    config: dict,
    decision: str,
    context: str,
    pool: list[Persona],
    speaker: Speaker,
    emit: Optional[Emit] = None,
) -> QuorumResult:
    policy = quorum_policy(config)
    by_id = {p.persona_id: p for p in pool}
    decision_ref = (config.get("name") or "quorum") + "-run"

    _emit(emit, {"type": "QUORUM_CONVENING", "decision_ref": decision_ref, "pool_size": len(pool)})
    plan = empanel(decision, context, pool, policy, speaker, emit, personas_by_id=by_id)

    # Apply per-eco whiteboard affinity overrides onto the (now complete, incl. synthesized)
    # persona map. No-op unless the eco declares quorum.whiteboard.persona_affinity.
    from .whiteboard import apply_affinity as _wb_apply_affinity
    _wb_apply_affinity(by_id, policy)

    _emit(emit, {"type": "DELIBERATION_STARTED", "seated": plan.seated})
    transcript = deliberate(plan, by_id, decision, context, policy, speaker, emit)

    verdict = render_verdict(decision, context, transcript, plan, by_id, policy, speaker)
    _emit(emit, {"type": "VERDICT_RENDERED", **verdict.to_dict()})

    return QuorumResult(decision_ref=decision_ref, plan=plan, transcript=transcript, verdict=verdict)
