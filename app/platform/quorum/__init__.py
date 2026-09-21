"""
tessera_platform.quorum — the Quorum ecosystem archetype engine.

A Quorum eco does not compile to a fixed DAG. Given a decision, it (1) convenes a panel of
scrutineer personas at runtime with a justification for who was seated AND excluded,
(2) runs a moderated, adversarial deliberation that preserves dissent and can summon members
mid-deliberation, and (3) renders a defensibility verdict. See docs/spec-quorum-archetype.md.

The engine is orchestration-substrate-agnostic and LLM-agnostic: all model calls go through
the `Speaker` protocol (deliberation.py). A deterministic `FakeSpeaker` (run.py) makes the
whole pipeline runnable and unit-testable offline; `BedrockSpeaker` is the production impl.
"""
from .models import Persona, QuorumPlan, TranscriptEntry, Transcript, Verdict, QuorumResult
from .engine import run_quorum

__all__ = [
    "Persona", "QuorumPlan", "TranscriptEntry", "Transcript", "Verdict", "QuorumResult",
    "run_quorum",
]
