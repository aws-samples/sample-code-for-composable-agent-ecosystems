"""The Speaker protocol + the moderated deliberation loop.

The engine owns all *deterministic* control (rounds, id assignment, dissent/open-issue
tracking, escalation policy, event emission); the `Speaker` supplies only *content* (what a
persona argues, who to summon, the verdict). This split is what makes the whole pipeline
unit-testable offline with a scripted FakeSpeaker while remaining swappable for a real LLM.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from .models import Persona, QuorumPlan, Transcript, TranscriptEntry, SEVERITIES, ENTRY_TYPES

Emit = Callable[[dict], None]


class Speaker(Protocol):
    """All LLM interaction goes through here. Implementations: FakeSpeaker (offline/tests),
    BedrockSpeaker (production)."""

    def digest(self, decision: str, context: str) -> str:
        """Optional: one-time compact brief of the submitted material (keeps per-turn prompts small)."""
        ...

    def empanel(self, decision: str, context: str, pool: list[Persona], policy: dict) -> dict:
        """-> {seated: [persona_id], excluded: [{persona_id, reason}], justification: str}"""
        ...

    def completeness(self, decision: str, context: str, seated: list[Persona], pool: list[Persona]) -> dict:
        """The 'who's missing?' adversary. -> {missing: [dimension], add: [persona_id], note: str}"""
        ...

    def speak(self, persona: Persona, decision: str, context: str, view: list[dict], round: int, on_token=None, **kwargs) -> list[dict]:
        """-> [{type, text, target?, grounding?, severity?}] contributed by this persona this round.
        `on_token(delta)` streams the spoken words live (optional; ignored by offline speakers)."""
        ...

    def escalate(self, decision: str, view: list[dict], available: list[Persona]) -> Optional[str]:
        """-> a persona_id to summon mid-deliberation, or None."""
        ...

    def next_speaker(self, decision: str, context: str, view: list[dict], seated: list[Persona], recent: list[str], **kwargs) -> Optional[str]:
        """The MODERATOR picks who speaks next from the live discussion (a seated persona_id), or
        None / 'none' when the debate is exhausted. Not implemented => callers fall back to a
        least-recently-heard rotation."""
        ...

    def verdict(self, decision: str, context: str, transcript: Transcript, plan: QuorumPlan) -> dict:
        """-> {grade, summary, against_whom, indefensible_elements, remediation}"""
        ...


def _emit(emit: Optional[Emit], event: dict) -> None:
    if emit:
        try:
            emit(event)
        except Exception:
            pass  # telemetry must never break the run


def _mk_entry(tid: int, round: int, member: str, raw: dict) -> TranscriptEntry:
    etype = raw.get("type", "claim")
    if etype not in ENTRY_TYPES:
        etype = "claim"
    sev = raw.get("severity", "note")
    if sev not in SEVERITIES:
        sev = "note"
    return TranscriptEntry(
        entry_id=f"e{tid}",
        round=round,
        member=member,
        type=etype,
        text=str(raw.get("text", "")).strip(),
        detail=str(raw.get("detail", "") or "").strip(),
        target=raw.get("target"),
        grounding=list(raw.get("grounding") or []),
        severity=sev,
    )


def deliberate(
    plan: QuorumPlan,
    personas_by_id: dict[str, Persona],
    decision: str,
    context: str,
    policy: dict,
    speaker: Speaker,
    emit: Optional[Emit] = None,
) -> Transcript:
    """Run the moderated, multi-round deliberation. Preserves dissent; may summon members."""
    transcript = Transcript()
    max_rounds = int(policy.get("max_rounds", 4))
    allow_escalation = bool(policy.get("escalation", True))
    preserve_dissent = bool(policy.get("preserve_dissent", True))
    hi = int((policy.get("select") or {}).get("max", 9))
    tid = 0

    for rnd in range(max_rounds):
        _emit(emit, {"type": "ROUND_ADVANCED", "round": rnd})
        new_entries_this_round = 0

        for pid in list(plan.seated):        # snapshot: a mid-round summon speaks next round
            persona = personas_by_id.get(pid)
            if persona is None:
                continue
            _emit(emit, {"type": "MEMBER_TURN", "round": rnd, "member": pid})
            raws = speaker.speak(persona, decision, context, transcript.view(), rnd) or []
            for raw in raws:
                if not preserve_dissent and raw.get("type") == "dissent":
                    raw = {**raw, "type": "objection"}  # (only if a caller disables dissent)
                entry = _mk_entry(tid, rnd, pid, raw)
                tid += 1
                transcript.add(entry)
                new_entries_this_round += 1
                _emit(emit, {"type": "DELIBERATION_ENTRY", **entry.to_dict()})
                if entry.type == "dissent":
                    _emit(emit, {"type": "DISSENT_RECORDED", "entry_id": entry.entry_id, "member": pid})

        # Emergent escalation: summon a member the debate revealed we need. Mutate plan.seated
        # (the source of truth / persisted panel) and reconcile exclusions + coverage.
        if allow_escalation and len(plan.seated) < hi:
            available = [p for pid, p in personas_by_id.items() if pid not in plan.seated]
            summon = speaker.escalate(decision, transcript.view(), available)
            if summon and summon in personas_by_id and summon not in plan.seated:
                plan.seated.append(summon)
                plan.summoned.append(summon)
                plan.excluded = [e for e in plan.excluded if e.get("persona_id") != summon]
                newp = personas_by_id[summon]
                for d in newp.dimensions:
                    if d not in plan.covered_dimensions:
                        plan.covered_dimensions.append(d)
                plan.missing_dimensions = [d for d in plan.missing_dimensions if d not in plan.covered_dimensions]
                _emit(emit, {"type": "MEMBER_SUMMONED", "member": summon, "round": rnd})

        # Convergence: stop once a round adds nothing and no issues remain open.
        if new_entries_this_round == 0 and not transcript.open_issues:
            break

    return transcript
