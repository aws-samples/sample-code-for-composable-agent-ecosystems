"""Quorum data models — plain dataclasses, JSON-serialisable, no AWS deps.

These are the persisted, replayable artifacts (spec §5): the quorum plan (composition +
justification), the structured deliberation transcript (the communication substrate AND the
audit trail, spec §6), and the verdict.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---- entry/verdict vocabularies -------------------------------------------------------
ENTRY_TYPES = {"claim", "objection", "rebuttal", "concession", "question", "dissent"}
SEVERITIES = {"blocker", "material", "note"}
GRADES = ("defensible", "defensible_with_conditions", "indefensible")


@dataclass
class Persona:
    """A convened scrutineer (from the scrutineer catalog)."""
    persona_id: str
    name: str
    stance: str = ""
    description: str = ""
    dimensions: list[str] = field(default_factory=list)
    invokes: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    key_questions: list[str] = field(default_factory=list)
    voice: str = ""
    severity_bias: str = "balanced"
    whiteboard_affinity: str = "none"   # none | low | medium | high — propensity to draw (spec-quorum-whiteboard §1,2)
    role: str = "challenger"            # facilitator | proponent | synthesiser | challenger (meeting realism)
    disposition: str = "balanced"       # supportive | balanced | critical
    # The shapes THIS person draws. Visual style is a discipline skill, not a global menu: a safety
    # engineer reaches for a bowtie, an SRE for a failure chain or a timeline, an architect for a flow
    # or a trust boundary, a facilitator for a 2x2 or a decision map. Offering every genre to everyone
    # is why the model kept picking the default one.
    draw_repertoire: list[str] = field(default_factory=list)

    @classmethod
    def from_record(cls, rec: dict) -> "Persona":
        _aff = (rec.get("whiteboard_affinity") or "none").strip().lower()
        if _aff not in ("none", "low", "medium", "high"):
            _aff = "none"                          # unknown -> none (fail-safe, rules 1,2)
        _role = (rec.get("role") or "challenger").strip().lower()
        if _role not in ("facilitator", "proponent", "synthesiser", "synthesizer", "challenger"):
            _role = "challenger"
        if _role == "synthesizer":
            _role = "synthesiser"
        return cls(
            persona_id=rec.get("persona_id") or rec.get("id") or rec.get("name", "?"),
            name=rec.get("name", rec.get("persona_id", "?")),
            stance=rec.get("stance", ""),
            description=rec.get("description", ""),
            dimensions=list(rec.get("dimensions") or []),
            invokes=list(rec.get("invokes") or []),
            cites=list(rec.get("cites") or []),
            key_questions=list(rec.get("key_questions") or []),
            voice=rec.get("voice", ""),
            severity_bias=rec.get("severity_bias", "balanced"),
            whiteboard_affinity=_aff,
            role=_role,
            disposition=(rec.get("disposition") or "balanced").strip().lower(),
            draw_repertoire=[str(g).strip().lower() for g in (rec.get("draw_repertoire") or [])],
        )


@dataclass
class QuorumPlan:
    """The convened panel + the justification for inclusions AND exclusions (spec §4).
    The composition must itself be defensible — this artifact is what a scrutineer of the
    *process* interrogates."""
    seated: list[str] = field(default_factory=list)                 # persona_ids
    excluded: list[dict] = field(default_factory=list)              # [{persona_id, reason}]
    justification: str = ""
    covered_dimensions: list[str] = field(default_factory=list)
    missing_dimensions: list[str] = field(default_factory=list)     # from the completeness adversary
    summoned: list[str] = field(default_factory=list)               # added mid-deliberation
    meeting_frame: str = ""     # how THIS review runs in real life (spec-quorum-whiteboard.md realism)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TranscriptEntry:
    entry_id: str
    round: int
    member: str                         # persona_id
    type: str                           # ENTRY_TYPES
    text: str                           # what they SAY OUT LOUD (short, human-sized) — this is displayed
    detail: str = ""                    # the substance behind it: read by colleagues, expandable by the
                                        # human on demand. Never shown inline (a person doesn't recite
                                        # 10 lines of prose in a meeting).
    target: Optional[str] = None        # entry_id this responds to
    grounding: list[str] = field(default_factory=list)   # cited obligations/precedents
    severity: str = "note"              # SEVERITIES
    # Ordering clock. A HUMAN entry is stamped at the moment their message was RECEIVED (before any
    # lock contention); an agent entry is stamped when their remark COMPLETED. Ordering by this =
    # "the order things became visible to the human", which is what the room should read like.
    seq: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Transcript:
    entries: list[TranscriptEntry] = field(default_factory=list)
    rounds: int = 0

    def add(self, e: TranscriptEntry) -> TranscriptEntry:
        if not e.seq:
            e.seq = time.time()
        self.entries.append(e)
        self.entries.sort(key=lambda x: x.seq)   # stable: equal seq keeps insertion order
        self.rounds = max(self.rounds, e.round + 1)
        return e

    def by_type(self, t: str) -> list[TranscriptEntry]:
        return [e for e in self.entries if e.type == t]

    @property
    def dissent(self) -> list[TranscriptEntry]:
        return self.by_type("dissent")

    @property
    def open_issues(self) -> list[TranscriptEntry]:
        """Objections with no later concession/rebuttal targeting them."""
        resolved = {e.target for e in self.entries if e.type in ("concession", "rebuttal") and e.target}
        return [e for e in self.by_type("objection") if e.entry_id not in resolved]

    @property
    def blockers(self) -> list[TranscriptEntry]:
        return [e for e in self.entries if e.severity == "blocker"]

    def view(self, limit: int = 40) -> list[dict]:
        """Salient recent state fed to members each round (avoids context bloat, spec §6). The
        DECISION-MAKER's ('human') turns are always pinned in, even if they've aged out of the
        recent window, so the panel never loses sight of what the human told them. Colleagues see
        BOTH the spoken line and the (truncated) detail — the substance is what they must engage."""
        recent = self.entries[-limit:]
        recent_ids = {e.entry_id for e in recent}
        pinned = [e for e in self.entries if e.member == "human" and e.entry_id not in recent_ids]
        out = []
        for e in (pinned + recent):
            d = e.to_dict()
            if d.get("detail"):
                d["detail"] = d["detail"][:400]
            out.append(d)
        return out

    def to_dict(self) -> dict:
        return {"rounds": self.rounds, "entries": [e.to_dict() for e in self.entries],
                "open_issues": [e.entry_id for e in self.open_issues],
                "dissent": [e.entry_id for e in self.dissent]}


@dataclass
class Verdict:
    grade: str                                  # GRADES
    summary: str = ""
    against_whom: list[str] = field(default_factory=list)         # which scrutineers it fails
    indefensible_elements: list[dict] = field(default_factory=list)  # [{element, argument, by}]
    remediation: list[str] = field(default_factory=list)
    dissent_preserved: list[dict] = field(default_factory=list)   # minority positions kept
    composition_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QuorumResult:
    """Everything a run produces — the replayable record (spec §5)."""
    decision_ref: str
    plan: QuorumPlan
    transcript: Transcript
    verdict: Verdict

    def to_dict(self) -> dict:
        return {
            "decision_ref": self.decision_ref,
            "plan": self.plan.to_dict(),
            "transcript": self.transcript.to_dict(),
            "verdict": self.verdict.to_dict(),
        }
