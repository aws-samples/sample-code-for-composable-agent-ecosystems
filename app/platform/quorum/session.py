"""Resumable Quorum deliberation SESSION — the interactive run model.

Where `engine.run_quorum` runs the whole archetype in ONE batch pass, a session runs it as
discrete, human-in-the-loop STEPS that stream turn-by-turn and can PAUSE for the human and
RESUME (spec: docs/spec-quorum-deliberation-room.md). The human is a seat at the table
(member="human"): their interjections are transcript entries the panel must answer.

Design (all deterministic control here; the Speaker supplies only content):
- convene()        -> run empanelment (with synthesis) and stop at the empanelment GATE.
- apply_empanel()  -> human adjusts the panel (accept / drop / summon) before debate.
- step()           -> ONE adaptive turn (a member speaks; may trigger ONE immediate rebuttal);
                      returns a pause reason when a member questions the human or on convergence.
- inject_human()   -> insert the human's contribution; the addressed member answers next.
- summon()         -> seat an additional (previously-excluded) scrutineer mid-debate.
- render_verdict() -> render the ruling on demand or at convergence.

SessionState is JSON-serialisable (persisted to runs/<sid>/_session.json) so each turn can be
a fresh Lambda invocation. Synthesized personas are stored IN the state — the seed pool does
not contain them.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .deliberation import Speaker, _emit, _mk_entry
from .empanelment import empanel
from .engine import quorum_policy
from .models import Persona, QuorumPlan, Transcript, TranscriptEntry, Verdict
from .verdict import render_verdict as _render_verdict
from .whiteboard import (Whiteboard, apply_affinity as _wb_apply_affinity,
                         whiteboard_enabled as _wb_enabled, OP_KINDS as _WB_OP_KINDS,
                         render_sketch as _wb_render, audit_ops as _wb_audit,
                         describe_ops as _wb_describe, render_png as _wb_png,
                         render_annotations as _wb_annotate,
                         repertoire_for as _wb_repertoire)

Emit = Callable[[dict], None]

HUMAN = "human"

# Rotating opening styles — assigned per turn in code so six independently-generated speakers stop
# echoing each other's phrasing ("I'm going to…").
_OPENING_MODES = [
    "open by naming the specific person you're responding to",
    "open with a concrete number, figure or clause from the proposal",
    "open with a blunt one-line judgement, then justify it",
    "open with the question you most need answered",
    "open by conceding one point before you push back",
    "open by describing the failure scenario you're picturing",
]

# phases
CONVENING = "convening"
EMPANEL_GATE = "empanel_gate"
DELIBERATING = "deliberating"
AWAIT_HUMAN = "await_human"
CONVERGENCE_OFFER = "convergence_offer"
VERDICT = "verdict"
DONE = "done"

# floor holders (server-authoritative turn allocation — see SessionState.floor)
FLOOR_PANEL = "panel"
FLOOR_REQUESTED = "human_requested"
FLOOR_HUMAN = "human"


@dataclass
class SessionState:
    session_id: str
    decision: str = ""
    context: str = ""
    phase: str = CONVENING
    policy: dict = field(default_factory=dict)
    plan: QuorumPlan = field(default_factory=QuorumPlan)
    transcript: Transcript = field(default_factory=Transcript)
    personas: dict = field(default_factory=dict)     # persona_id -> Persona (incl. synthesized)
    round: int = 0
    seat_idx: int = 0                                 # (legacy; dynamic moderator now picks speakers)
    tid: int = 0                                      # transcript entry id counter
    pending_speaker: Optional[str] = None             # forced next speaker (e.g. answering the human)
    entries_this_round: int = 0
    turns: int = 0                                    # total member turns taken (bounds the debate)
    passes: int = 0                                   # consecutive turns that added nothing new (-> converge)
    owed_reply: bool = False                          # the human spoke and has NOT yet been answered
    owed_question: str = ""                           # their exact words, handed to the next speaker
    brief: str = ""                                   # one-time compact digest of the submitted doc
    owed_answers: int = 0                             # how many members still owe the human an answer
    asked_questions: list = field(default_factory=list)  # [{member, text, status}] asked OF the human
    answer_givers: list = field(default_factory=list)     # members who already answered the current ask
    no_progress: int = 0                              # consecutive turns adding nothing new (chair nudges)
    agenda: list = field(default_factory=list)        # [{topic, by, status: open|accepted|answered}]
    converged: bool = False
    verdict: Optional[Verdict] = None
    whiteboard: Whiteboard = field(default_factory=Whiteboard)   # spec-quorum-whiteboard.md
    meeting_frame: str = ""   # how this review runs in real life (set at convene)
    # Floor control is SERVER-AUTHORITATIVE (Fix 1). A client can never be relied on to stop the
    # panel talking over the human: its gate races the turn it is trying to prevent, and it does
    # not survive reload or a second tab. `floor` is the single source of truth.
    #   panel           — members speak; the room advances normally
    #   human_requested — the human raised a hand; the CURRENT turn finishes, then the floor is theirs
    #   human           — the human holds the floor; `advance` is refused until they release it
    floor: str = FLOOR_PANEL
    wb_last_map_round: int = -1   # last round the chair mapped the agenda on the board (Fix 4)
    wb_map_sig: str = ""          # signature of the last map drawn — skip an identical redraw
    wb_sketched: list = field(default_factory=list)   # topics already drawn (no repeats)
    wb_annotated: list = field(default_factory=list)  # "<element>|<mark>" pairs already applied
    seat_models: dict = field(default_factory=dict)   # persona_id -> model id (see _assign_seat_models)
    # Model capabilities DISCOVERED at launch (e.g. models that reject `temperature`). Must be
    # serialised: every command is a separate lambda invocation with a fresh speaker, so a capability
    # learned during `start` is gone by the first `advance` — which killed six runs of an experiment
    # with zero member turns and a ValidationException per attempt.
    model_caps: dict = field(default_factory=dict)
    last_pause_turn: int = -99     # rate-limits handing the floor over for passing remarks
    # --- cost counters, for _run_metrics.json. These MUST be serialised: one `advance` is one lambda
    # invocation that reloads state from S3, so an in-memory counter would only ever report the last
    # turn (measured: the first metrics run reported "n/a" for a room that had retried plenty).
    _model_calls: int = 0
    _gate_retries: int = 0
    _gate_faults: int = 0          # turns that tripped a gate at all (retried OR yielded)
    _yields: int = 0               # turns that produced NO entry — the room simply said less
    # [[seconds, produced?], ...] one row per ATTEMPT. Entry timestamps only show turns that happened;
    # the cost of the ones that produced nothing hides inside the gaps between them (measured: an 11-entry
    # run had a 58.6s gap). Wasted seconds are the point, so they must be timed where they occur.
    _attempts: list = field(default_factory=list)
    _sketch_calls_total: int = 0
    _critique_calls: int = 0
    _critique_kept: int = 0
    _draw_refusals: int = 0
    _floor_grants: int = 0
    _relabelled: int = 0
    _board_problems: int = 0       # audit faults found at DRAW time (E6: does self-review earn its call?)
    yielded_at: dict = field(default_factory=dict)    # member -> turn index of their last pass

    # ---- serialisation ----------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "decision": self.decision, "context": self.context,
            "phase": self.phase, "policy": self.policy, "plan": self.plan.to_dict(),
            "transcript": {"rounds": self.transcript.rounds,
                           "entries": [e.to_dict() for e in self.transcript.entries]},
            "personas": {pid: _persona_dict(p) for pid, p in self.personas.items()},
            "round": self.round, "seat_idx": self.seat_idx, "tid": self.tid,
            "pending_speaker": self.pending_speaker, "entries_this_round": self.entries_this_round,
            "turns": self.turns, "passes": self.passes,
            "owed_reply": self.owed_reply, "owed_question": self.owed_question,
            "brief": self.brief, "agenda": self.agenda,
            "owed_answers": self.owed_answers, "asked_questions": self.asked_questions,
            "answer_givers": self.answer_givers, "no_progress": self.no_progress,
            "converged": self.converged,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "whiteboard": self.whiteboard.to_dict(),
            "meeting_frame": self.meeting_frame,
            "floor": self.floor,
            "seat_models": dict(self.seat_models or {}),
            "model_caps": {k: sorted(v) if isinstance(v, (set, list)) else v
                           for k, v in (self.model_caps or {}).items()},
            "wb_last_map_round": self.wb_last_map_round,
            "wb_map_sig": self.wb_map_sig,
            "wb_sketched": self.wb_sketched,
            "wb_annotated": self.wb_annotated,
            "last_pause_turn": self.last_pause_turn,
            "attempts": [[round(float(a[0]), 2), bool(a[1])] for a in (self._attempts or [])][-80:],
            "cost": {k: int(getattr(self, k, 0) or 0) for k in
                     ("_model_calls", "_gate_retries", "_gate_faults", "_yields",
                      "_board_problems", "_sketch_calls_total", "_critique_calls",
                      "_critique_kept", "_draw_refusals", "_floor_grants", "_relabelled")},
            "yielded_at": self.yielded_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        plan = QuorumPlan(**{k: d.get("plan", {}).get(k) for k in
                             ("seated", "excluded", "justification", "covered_dimensions",
                              "missing_dimensions", "summoned", "meeting_frame") if d.get("plan", {}).get(k) is not None})
        t = Transcript()
        for e in (d.get("transcript", {}) or {}).get("entries", []):
            t.add(TranscriptEntry(**{k: e.get(k) for k in
                                     ("entry_id", "round", "member", "type", "text", "detail", "target",
                                      "grounding", "severity", "seq") if k in e}))
        personas = {pid: Persona.from_record(pr) for pid, pr in (d.get("personas") or {}).items()}
        v = d.get("verdict")
        return cls(
            session_id=d["session_id"], decision=d.get("decision", ""), context=d.get("context", ""),
            phase=d.get("phase", CONVENING), policy=d.get("policy", {}) or {}, plan=plan, transcript=t,
            personas=personas, round=d.get("round", 0), seat_idx=d.get("seat_idx", 0),
            tid=d.get("tid", 0), pending_speaker=d.get("pending_speaker"),
            entries_this_round=d.get("entries_this_round", 0), turns=d.get("turns", 0),
            passes=d.get("passes", 0), owed_reply=bool(d.get("owed_reply")),
            owed_question=d.get("owed_question", "") or "", brief=d.get("brief", "") or "",
            owed_answers=int(d.get("owed_answers") or 0), asked_questions=d.get("asked_questions") or [],
            answer_givers=d.get("answer_givers") or [], no_progress=int(d.get("no_progress") or 0),
            agenda=d.get("agenda") or [],
            converged=bool(d.get("converged")),
            verdict=Verdict(**v) if v else None,
            whiteboard=Whiteboard.from_dict(d.get("whiteboard") or {}),
            meeting_frame=d.get("meeting_frame", "") or "",
            floor=d.get("floor", FLOOR_PANEL) or FLOOR_PANEL,
            wb_last_map_round=int(d.get("wb_last_map_round", -1)),
            wb_map_sig=d.get("wb_map_sig", "") or "",
            wb_sketched=list(d.get("wb_sketched") or []),
            wb_annotated=list(d.get("wb_annotated") or []),
            seat_models=dict(d.get("seat_models") or {}),
            model_caps=dict(d.get("model_caps") or {}),
            _attempts=[list(a) for a in (d.get("attempts") or [])],
            last_pause_turn=int(d.get("last_pause_turn", -99)),
            **{k: int(v or 0) for k, v in (d.get("cost") or {}).items()
               if k in ("_model_calls", "_gate_retries", "_gate_faults", "_yields",
                        "_board_problems", "_sketch_calls_total", "_critique_calls",
                        "_critique_kept", "_draw_refusals", "_floor_grants", "_relabelled")},
            yielded_at=dict(d.get("yielded_at") or {}),
        )


def _persona_dict(p: Persona) -> dict:
    return {"persona_id": p.persona_id, "name": p.name, "stance": p.stance,
            "description": p.description, "dimensions": p.dimensions, "invokes": p.invokes,
            "cites": p.cites, "key_questions": p.key_questions, "voice": p.voice,
            "severity_bias": p.severity_bias, "whiteboard_affinity": p.whiteboard_affinity,
            "role": p.role, "disposition": p.disposition,
            "draw_repertoire": list(p.draw_repertoire or [])}


def _card(p: Persona) -> dict:
    """A compact member card for the UI (id + human-readable name + stance)."""
    return {"persona_id": p.persona_id, "name": p.name, "stance": p.stance}


# ---- lifecycle -----------------------------------------------------------------------

def _unseat_in_room_design_owner(st: "SessionState", policy: dict,
                                 emit: Optional[Emit] = None) -> None:
    """INTERACTIVE sessions only: the human IS the design owner, so a `proponent` seat duplicates
    them — and measurably displaces them. In a real run the in-room owner took 36% of all turns,
    the two loudest voices 64%, and the panel discussed "the submitter" in the third person 13
    times while never once addressing them. Unseat it so the questions come to the human.
    (The batch/headless engine keeps its proponent: there, nobody is present to defend the design.)
    """
    lo = int(((policy.get("select") or {}).get("min")) or 3)
    for pid in list(st.plan.seated):
        p = st.personas.get(pid)
        if not p or getattr(p, "role", "") != "proponent":
            continue
        if len(st.plan.seated) <= lo:
            p.role = "challenger"          # can't spare the seat — at least stop it defending
            continue
        st.plan.seated.remove(pid)
        st.plan.excluded.append({"persona_id": pid, "name": p.name,
                                 "reason": "the submitter is in the room and owns the design — a "
                                           "second design owner would answer in their place"})
        _emit(emit, {"type": "QUORUM_MEMBER_EXCLUDED", "persona_id": pid, "name": p.name,
                     "reason": "the submitter owns the design in an interactive review"})


def convene(session_id: str, config: dict, decision: str, context: str,
            pool: list[Persona], speaker: Speaker, emit: Optional[Emit] = None,
            settings_overrides: Optional[dict] = None) -> SessionState:
    """Run empanelment (with synthesis) and stop at the empanelment gate for human review."""
    policy = quorum_policy(config, settings_overrides)
    st = SessionState(session_id=session_id, decision=decision, context=context, policy=policy)
    st.personas = {p.persona_id: p for p in pool}
    _emit(emit, {"type": "QUORUM_CONVENING", "pool_size": len(pool)})
    # Compress the submitted material ONCE. Panelists then reason over the brief instead of the whole
    # document, which is what made every turn 45s+ (and the client's watchdog race visible).
    try:
        st.brief = speaker.digest(decision, context) or ""
    except Exception:
        st.brief = (context or "")[:4000]
    st.plan = empanel(decision, context, pool, policy, speaker, emit, personas_by_id=st.personas)
    st.meeting_frame = st.plan.meeting_frame or ""
    # Apply per-eco whiteboard affinity overrides (no-op unless declared).
    _wb_apply_affinity(st.personas, policy)
    for _p in st.personas.values():
        if not getattr(_p, "draw_repertoire", None):
            _p.draw_repertoire = _wb_repertoire(_p)      # a discipline skill, assigned once
    _unseat_in_room_design_owner(st, policy, emit)
    _emit(emit, {"type": "QUORUM_PLAN", **st.plan.to_dict(),
                 "members": [_card(st.personas[p]) for p in st.plan.seated if p in st.personas],
                 "excluded_cards": [{**e, **(_card(st.personas[e["persona_id"]])
                                             if e.get("persona_id") in st.personas else {})}
                                    for e in st.plan.excluded]})
    # Which model sits in which seat, decided ONCE now that the seats exist.
    st.seat_models = _assign_seat_models(st, getattr(speaker, "model_id", ""))
    if st.seat_models:
        print(f"[quorum.convene] seat models: {st.seat_models}")

    st.phase = EMPANEL_GATE
    _emit(emit, {"type": "QUORUM_PAUSE", "reason": EMPANEL_GATE})
    return st


def apply_empanel(st: SessionState, action: dict, speaker: Speaker,
                  emit: Optional[Emit] = None) -> SessionState:
    """Human adjusts the panel at the gate: {accept} | {drop:[id]} | {summon:[id]}."""
    if st.phase not in (EMPANEL_GATE, DELIBERATING, AWAIT_HUMAN, CONVERGENCE_OFFER):
        return st
    for pid in (action.get("drop") or []):
        if pid in st.plan.seated:
            st.plan.seated.remove(pid)
            st.plan.excluded.append({"persona_id": pid, "reason": "removed by the human"})
            _emit(emit, {"type": "QUORUM_MEMBER_EXCLUDED", "persona_id": pid, "reason": "removed by the human"})
    for pid in (action.get("summon") or []):
        if pid in st.personas and pid not in st.plan.seated:
            st.plan.seated.append(pid)
            st.plan.summoned.append(pid)
            st.plan.excluded = [e for e in st.plan.excluded if e.get("persona_id") != pid]
            _emit(emit, {"type": "QUORUM_MEMBER_SEATED", "member": pid, "reason": "summoned by the human",
                         **(_card(st.personas[pid]) if pid in st.personas else {})})
    if action.get("accept"):
        st.phase = DELIBERATING
        _emit(emit, {"type": "DELIBERATION_STARTED", "seated": st.plan.seated})
        # The panel begins on its own. We do NOT force an opening question at the human — like a real
        # meeting, they watch until they raise their hand, and panelists pull them in only when a
        # member genuinely needs an answer only the decision-maker can give.
    return st


def _recent_speakers(st: SessionState, n: int = 3) -> list[str]:
    ids = [e.member for e in st.transcript.entries if e.member not in (HUMAN, "moderator")]
    return ids[-n:]


def _fallback_speaker(st: SessionState) -> Optional[str]:
    """Least-recently-heard member (fair rotation). Seat-order scanning made the first seat answer
    almost every human message."""
    if not st.plan.seated:
        return None
    last_at = {pid: -1 for pid in st.plan.seated}
    for i, e in enumerate(st.transcript.entries):
        if e.member in last_at:
            last_at[e.member] = i
    return min(st.plan.seated, key=lambda pid: last_at[pid])


def _add_entry(st: SessionState, rnd: int, member: str, raw: dict,
               emit: Optional[Emit], seq: Optional[float] = None) -> TranscriptEntry:
    preserve = bool(st.policy.get("preserve_dissent", True))
    if not preserve and raw.get("type") == "dissent":
        raw = {**raw, "type": "objection"}
    entry = _mk_entry(st.tid, rnd, member, raw)
    entry.seq = float(seq) if seq else time.time()
    st.tid += 1
    st.transcript.add(entry)
    st.entries_this_round += 1
    # AGENDA (structural non-repetition): the speaker labels its own topic in META, for free.
    topic = str(raw.get("topic") or "").strip()
    if topic:
        item = _find_topic(st, topic)
        if item is None and _setting(st, "room.agenda_ownership", "emergent") == "chaired":
            # The chair owns the agenda: a new label while an item is on the floor is a relabelling of
            # that item, not a new subject. Free — no model call — and it is what stops the agenda
            # multiplying labels ("RTO definition" vs "RTO cost justification") for one subject.
            floor_item = _current_item(st)
            if floor_item:
                item = _find_topic(st, floor_item)
                if item is not None:
                    st._relabelled = int(getattr(st, "_relabelled", 0)) + 1
        if item is None:
            st.agenda.append({"topic": topic[:80], "by": member, "status": "open", "remarks": 1,
                              "by_member": {member: 1}})
        else:
            item["remarks"] = int(item.get("remarks", 1)) + 1
            bm = item.setdefault("by_member", {})       # who has worked this topic, how often
            bm[member] = int(bm.get(member, 0)) + 1
    resolved = str(raw.get("resolve_topic") or "").strip().lower()
    if resolved:
        for a in st.agenda:
            if a.get("topic", "").lower() == resolved:
                a["status"] = "accepted"
    if entry.type == "question" and entry.target == HUMAN:
        st.asked_questions.append({"member": member, "text": entry.text[:300], "status": "open"})
    _emit(emit, {"type": "QUORUM_ENTRY", **entry.to_dict()})
    if entry.type == "dissent":
        _emit(emit, {"type": "DISSENT_RECORDED", "entry_id": entry.entry_id, "member": member})
    return entry


_ACCEPT_SIGNALS = ("accept", "accepted", "agreed", "agree", "fine", "ok ", "okay", "noted",
                   "assume", "assumed", "will provide", "letter to follow", "signed off",
                   "move on", "out of scope", "stakeholders are", "comfortable with")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "have", "will", "your", "their",
         "about", "there", "which", "would", "could", "should", "single", "risk", "gap"}


def _lane_map(st: SessionState) -> list:
    """Who owns what. Published to every member so they can DEFER to the owner instead of duplicating
    (AutoGen calls this `send_introductions`; the literature shows agents otherwise pile onto whatever
    was just said)."""
    out = []
    for pid in st.plan.seated:
        p = st.personas.get(pid)
        if not p:
            continue
        lane = ", ".join((p.dimensions or [])[:4]) or (p.stance or "")[:70]
        out.append({"id": pid, "name": p.name, "lane": lane})
    return out


def _content_tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z']+", (text or "").lower()) if len(w) > 3 and w not in _STOP}


def _too_similar(text: str, others: list, thresh: float = 0.55) -> bool:
    """Novelty gate. 'Lack of progress' (redundancy / circular discussion / repetition) is the single
    most common cause of problem drift in multi-agent debate (Becker et al., EACL 2026 Findings), so we
    check it in CODE rather than asking the model not to repeat itself."""
    a = _content_tokens(text)
    if len(a) < 8:
        return False
    head = (text or "").strip()[:120].lower()
    for o in others:
        if not o:
            continue
        if head and head == (o or "").strip()[:120].lower():
            return True
        b = _content_tokens(o)
        if not b:
            continue
        if len(a & b) / max(1, len(a | b)) >= thresh:
            return True
    return False


def _topic_tokens(topic: str) -> set:
    return {w for w in re.findall(r"[a-z]+", (topic or "").lower()) if len(w) > 3 and w not in _STOP}


# Words that describe the ANGLE on a subject rather than the subject itself. Two topics that share
# only these are still the same argument being relabelled.
_TOPIC_GENERIC = {
    "definition", "define", "cost", "costs", "model", "target", "targets", "design", "plan",
    "planning", "risk", "risks", "issue", "issues", "gap", "gaps", "control", "controls", "story",
    "scope", "process", "review", "approach", "strategy", "justification", "justify", "requirement",
    "requirements", "concern", "concerns", "question", "questions", "detail", "details", "evidence",
    "accepted", "acceptance", "status", "update", "clarification", "discussion", "point", "points",
}


def _subject_key(topic: str) -> frozenset:
    """The SUBJECT a topic is about, independent of how it was worded.

    The saturation cap was bypassed simply by relabelling: one real session recorded 'RTO/RPO and
    regional resilience', 'RTO/RPO definition' and 'RTO cost justification' as three separate topics
    (each parked just under the cap) because `_topic_tokens` drops tokens of three characters or
    fewer — which silently discarded the very acronyms that identify the subject (RTO, RPO, PII,
    SLA). We keep acronyms, drop words that merely describe the angle, and treat topics sharing any
    salient term as the same subject."""
    raw = topic or ""
    acronyms = {a.lower() for a in re.findall(r"\b[A-Z]{2,5}\b", raw)}
    words = {w for w in re.findall(r"[a-z]+", raw.lower())
             if len(w) >= 5 and w not in _STOP and w not in _TOPIC_GENERIC}
    return frozenset(acronyms | words)


def _find_topic(st: SessionState, topic: str):
    """Match a topic SEMANTICALLY (token overlap), not by exact string — models rephrase the same
    issue every turn ('pre-flight entitlement check/gate/data'), which defeated exact dedupe and made
    the panel loop forever."""
    new = _topic_tokens(topic)
    new_subj = _subject_key(topic)
    if not new and not new_subj:
        return None
    for a in st.agenda:
        if new_subj and (new_subj & _subject_key(a.get("topic", ""))):
            return a                      # same subject, different wording
        ex = _topic_tokens(a.get("topic", ""))
        if ex and len(new & ex) / max(1, min(len(new), len(ex))) >= 0.5:
            return a
    return None


def _settle_agenda_from_human(st: SessionState, text: str) -> list[str]:
    """When the DECISION-MAKER accepts/answers something, mark those agenda items settled — from THEIR
    words, not from a panelist volunteering a label. Settled items must not be re-litigated."""
    low = (text or "").lower()
    if not any(sig in low for sig in _ACCEPT_SIGNALS):
        return []
    words = {w.strip(".,;:()'\"").lower() for w in low.split()}
    words = {w for w in words if len(w) > 4 and w not in _STOP}
    settled = []
    for a in st.agenda:
        if a.get("status") != "open":
            continue
        topic_words = {w.strip(".,;:").lower() for w in a.get("topic", "").split() if len(w) > 4}
        topic_words -= _STOP
        if topic_words & words:
            a["status"] = "accepted"
            settled.append(a["topic"])
    return settled


SATURATED_REMARKS = 3      # a topic the room has already chewed on this many times is closed
MEMBER_TOPIC_CAP = 2       # and no single member may work the same topic more than twice


def _setting(st: "SessionState", path: str, default=None):
    """Read a run setting by dotted path. Sessions created before run settings existed fall back to
    the module DEFAULTS, which are today's behaviour — so nothing changes for them."""
    from .settings import DEFAULTS
    for source in ((st.policy or {}).get("settings") or {}, DEFAULTS):
        cur = source
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur is not None:
            return cur
    return default


def _restates_closed_topic(st: SessionState, raw: dict, pid: str) -> bool:
    """True when this turn re-opens a topic the room has finished with: already saturated, already
    settled by the human, or one this member has personally worked to its cap.

    Concessions and dissents were originally exempt, on the theory that they CLOSE a topic rather
    than reheat it. Measured consequence: 'refund deterministic gate' reached SEVEN remarks against a
    cap of three, as five different members took turns agreeing with a control the room had already
    accepted. Agreeing with a settled point is not progress — it is the excessive-agreement failure
    in a new costume. So a concession may still close a LIVE topic, but adds nothing to a settled or
    saturated one."""
    _closing = raw.get("type") in ("concession", "dissent")
    topic = (raw.get("topic") or "").strip()
    if not topic:
        return False
    item = _find_topic(st, topic)
    if not item:
        return False                                   # a genuinely new topic is progress
    if item.get("status") in ("accepted", "answered"):
        return True                                    # settled: agreeing again adds nothing either
    if int(item.get("remarks", 1)) >= int(_setting(st, "gates.saturation.remarks", SATURATED_REMARKS)):
        return True
    if _closing and not _setting(st, "gates.saturation.count_concessions", True):
        return False                                   # a concession that closes a LIVE topic is progress
    if _closing:
        return False
    return int((item.get("by_member") or {}).get(pid, 0)) >= int(
        _setting(st, "gates.saturation.member_cap", MEMBER_TOPIC_CAP))


def _repeats_answered_question(st: SessionState, raw: dict, pid: str) -> bool:
    """True when this turn re-asks something the human has ALREADY answered.

    Measured failure: one member put the same demand four times in four wordings — all recorded as
    `answered` — ending with "You said you'd already answered — but you gave me…". The prompt lists
    answered questions and asks the panel not to repeat them; that is advisory, and the panel simply
    refused to accept the answer. Enforced here on the SUBJECT, so rewording cannot evade it."""
    if raw.get("type") != "question" or not _setting(st, "gates.answered_questions.enforce", True):
        return False
    subj = _subject_key(str(raw.get("topic") or "") + " " + str(raw.get("text") or ""))
    if not subj:
        return False
    for q in st.asked_questions:
        if q.get("status") != "answered":
            continue
        if subj & _subject_key(str(q.get("text") or "")):
            return True
    # also blocked if the human already settled this subject on the agenda
    for a in st.agenda:
        if a.get("status") in ("accepted", "answered") and (subj & _subject_key(a.get("topic", ""))):
            return True
    return False


def _allowed_topics(st: SessionState, pid: str) -> list[str]:
    """What this member may actually speak to right now, computed from the same state the gates use.

    Every rule in the turn prompt is phrased as a prohibition — do not re-raise, do not add to, do not
    re-litigate — and the model ignores them (measured: 16 of 22 turns restated one agreed fix), so the
    room rejects the turn afterwards and pays for a whole extra model call. `gates.mode: prevent`
    inverts it: hand the member the LIVE items it has capacity for, and let the same predicates that
    would have rejected the turn decide the list. A compliant turn then costs nothing extra, because
    compliance is the easy path rather than a rule to remember.
    """
    cap = int(_setting(st, "gates.saturation.remarks", SATURATED_REMARKS))
    mine = int(_setting(st, "gates.saturation.member_cap", MEMBER_TOPIC_CAP))
    out = []
    for a in st.agenda:
        if a.get("status") != "open":
            continue
        if int(a.get("remarks", 1)) >= cap:
            continue
        if int((a.get("by_member") or {}).get(pid, 0)) >= mine:
            continue
        out.append(str(a.get("topic") or ""))
    return [t for t in out if t]


def _allocate_speaker(st: SessionState) -> Optional[str]:
    """Choose the next speaker in CODE, with no model call (room.turn_allocation: fairness_first).

    The moderator call spends a model round trip to produce a name, on every attempt — including the
    ones that then yield. Everything it is given is already in state, and one thing it does NOT have is
    decisive: whether a member has anything left it is allowed to say. Measured, 18-28% of the room's
    working time goes on attempts that produce nothing, and a member with every live item closed to it
    cannot do anything but yield.

    Note what this replaces. The model's pick was already overridden by code most of the time — a
    fairness guard, a post-yield cooldown and a no-twice-in-a-row rule all sit downstream of it. This
    keeps those rules and drops the call.

    Returns None when nobody can usefully speak, which the caller reads as "the room is done".
    """
    seated = list(st.plan.seated)
    if not seated:
        return None
    heard = {p: 0 for p in seated}
    for e in st.transcript.entries:
        if e.member in heard:
            heard[e.member] += 1
    for p, t in (st.yielded_at or {}).items():
        if p in heard:
            heard[p] += 1                      # an attempt counts as being heard, even if nothing landed
    last = _recent_speakers(st, 1)
    cooldown = max(2, len(seated) // 2)

    # Who could say something new? A member whose every live agenda item is closed to it has nothing
    # but a yield to offer — unless the agenda is empty, when anyone may open the first item.
    agenda_open = any(a.get("status") == "open" for a in st.agenda)
    def _has_room(p: str) -> bool:
        return (not agenda_open) or bool(_allowed_topics(st, p))

    # Lane fit: prefer a member whose discipline overlaps the item on the floor (or the newest live
    # item), so the room reads like people speaking to what is in front of them.
    floor_item = _current_item(st) or next(
        (a["topic"] for a in reversed(st.agenda) if a.get("status") == "open"), "")
    floor_keys = _subject_key(floor_item) if floor_item else set()
    def _lane_fit(p: str) -> int:
        persona = st.personas.get(p)
        dims = " ".join((getattr(persona, "dimensions", None) or []) + [getattr(persona, "stance", "")])
        return 1 if (floor_keys and (_subject_key(dims) & floor_keys)) else 0

    candidates = [p for p in seated
                  if p not in last
                  and st.turns - int((st.yielded_at or {}).get(p, -99)) > cooldown
                  and _has_room(p)]
    if not candidates:                          # relax the soft rules before giving up, in this order
        candidates = [p for p in seated if p not in last and _has_room(p)]
    if not candidates:
        candidates = [p for p in seated if _has_room(p)]
    if not candidates:
        return None                             # nobody has anything they are allowed to raise
    # Least heard first, then lane fit, then seat order — deterministic, so a run can be reproduced.
    candidates.sort(key=lambda p: (heard[p], -_lane_fit(p), seated.index(p)))
    return candidates[0]


def _route(speaker, st: SessionState, seat: str) -> None:
    """Point the speaker at a seat's model AND restore capabilities discovered at launch.

    Capabilities have to be re-applied every invocation: the handler builds a fresh speaker per command,
    so what `start` learned about a model is unknown to `advance` unless it travels through the session.
    """
    if not hasattr(speaker, "seat"):
        return
    speaker._per_seat = dict(st.seat_models or {})
    no_temp = (st.model_caps or {}).get("no_temperature") or []
    try:
        speaker._no_temperature = set(no_temp) | set(getattr(speaker, "_no_temperature", set()) or set())
    except Exception:                          # noqa: BLE001 - a speaker that predates capabilities
        pass
    speaker.seat(seat)


CHAIR_SEAT = "__chair__"        # the chair AND the machinery it stands for: turn allocation, verdict


def _assign_seat_models(st: SessionState, default_model: str = "") -> dict:
    """Decide which model sits in which seat, ONCE, at convene.

    One model in every seat is a structural cause of consensus collapse: the room can agree because it
    is one mind wearing six name badges. `models.members: balance` spreads the seats across
    `balance_pool` so disagreement has somewhere to come from.

    The chair — and the machinery that speaks for it, turn allocation and the verdict — is pinned
    separately and deliberately: if the judge or the turn allocator changes between two runs, the
    comparison measures THEM, not the room. Assignment is deterministic (seat order, round-robin) so a
    recorded run can be reproduced, and the resolved map is written into _run_config.json.
    """
    mods = _setting(st, "models", {}) or {}
    base = str(mods.get("default") or default_model or "")
    seated = list(getattr(st.plan, "seated", []) or [])
    chair = next((pid for pid in seated
                  if getattr(st.personas.get(pid), "role", "") == "facilitator"), "")
    out: dict = {}
    chair_model = str(mods.get("chair") or base or "")
    if chair_model:
        out[CHAIR_SEAT] = chair_model
        if chair:
            out[chair] = chair_model
    members = str(mods.get("members") or "")
    if members == "balance":
        pool = [str(m) for m in (mods.get("balance_pool") or []) if str(m).strip()]
        others = [pid for pid in seated if pid != chair]
        for i, pid in enumerate(others):
            out[pid] = pool[i % len(pool)]
    elif members:
        for pid in seated:
            if pid != chair:
                out[pid] = members
    elif base:
        for pid in seated:
            out.setdefault(pid, base)
    out.update({str(k): str(v) for k, v in (mods.get("per_seat") or {}).items() if v})
    return out


def _current_item(st: SessionState) -> str:
    """The item the CHAIR has put on the floor, when the chair owns the agenda.

    Today (agenda_ownership: emergent) nobody owns the agenda: each member invents its own 2-5 word
    topic label, code fuzzy-matches it onto an existing item, and the member is then rejected for
    saturating a topic it never knew it had joined. That is why a gate fires on a third of turns.
    Under `chaired`, the live item is chosen here — by remaining capacity, so the room finishes a
    subject before starting another — and handed to the speaker, which turns "don't re-raise" from a
    ban applied afterwards into an instruction given up front."""
    if _setting(st, "room.agenda_ownership", "emergent") != "chaired":
        return ""
    cap = int(_setting(st, "gates.saturation.remarks", SATURATED_REMARKS))
    live = [a for a in st.agenda if a.get("status") == "open" and int(a.get("remarks", 1)) < cap]
    if not live:
        return ""
    live.sort(key=lambda a: (-(cap - int(a.get("remarks", 1))), st.agenda.index(a)))
    return str(live[0].get("topic") or "")


def _agenda_view(st: SessionState) -> dict:
    """What's been raised and what the decision-maker has already settled — so 'don't repeat' and
    'move on' are LOOKUPS rather than pleas."""
    # Breadth over depth: prolonged debate on ONE point measurably degrades quality. Configurable
    # (gates.saturation.remarks) — this used to be a second hardcoded 3 that silently disagreed with
    # the gate enforcing it.
    SATURATED = int(_setting(st, "gates.saturation.remarks", SATURATED_REMARKS))
    return {
        "raised": [a["topic"] for a in st.agenda
                   if a.get("status") == "open" and int(a.get("remarks", 1)) < SATURATED],
        "saturated": [a["topic"] for a in st.agenda
                      if a.get("status") == "open" and int(a.get("remarks", 1)) >= SATURATED],
        "settled_by_human": [a["topic"] for a in st.agenda if a.get("status") in ("accepted", "answered")],
        "parked": [a["topic"] for a in st.agenda if a.get("status") == "parked"],
        "on_the_floor": _current_item(st),
        "chair_owns": _setting(st, "room.agenda_ownership", "emergent") == "chaired",
    }


def _chair_rules_agenda(st: SessionState) -> list[str]:
    """A moderator (chair_mode: moderator) PARKS what the room has exhausted, rather than leaving it
    open and relying on every member to notice. Returns the topics parked this turn, for the record."""
    if _setting(st, "room.chair_mode", "facilitator") != "moderator":
        return []
    cap = int(_setting(st, "gates.saturation.remarks", SATURATED_REMARKS))
    parked = []
    for a in st.agenda:
        if a.get("status") == "open" and int(a.get("remarks", 1)) >= cap:
            a["status"] = "parked"
            parked.append(str(a.get("topic") or ""))
    return parked


def _chair_note(st: SessionState) -> str:
    """A policy-feedback nudge when the room stalls. DRIFTPolicy (Becker et al.) mitigated ~31% of
    problem-drift cases with exactly this: situational feedback injected into the discussion."""
    ag = _agenda_view(st)
    if ag["saturated"]:
        covered = "; ".join(ag["saturated"][:3])
        return (f"CHAIR: the panel has exhausted [{covered}] — that ground is CLOSED. Do not add to it. "
                "Raise an issue not yet aired, or state the conditions you need for approval.")
    return ("CHAIR: the last turns added nothing new. Do not restate the discussion — either raise a "
            "genuinely un-aired issue from your discipline, or state your conditions for approval.")


def _recent_openings(st: SessionState, n: int = 4) -> list[str]:
    """The opening words of the last few remarks — passed to the next speaker as BANNED openings so
    the panel stops echoing each other ('I'm going to…')."""
    outs = []
    for e in reversed(st.transcript.entries):
        if e.member in (HUMAN, "moderator"):
            continue
        words = (e.text or "").strip().split()
        if words:
            outs.append(" ".join(words[:4]))
        if len(outs) >= n:
            break
    return outs


def _targets_human(entry: TranscriptEntry) -> bool:
    """Does this turn require the human? Pause whenever a member DIRECTS a turn at the submitter
    — not only a literal question. Real sessions addressed the human with demands ("I need to
    see the DR architecture") typed as objection/rebuttal: 16 of 26 turns were aimed at the
    submitter but only ONE was a question, so the room never stopped and the human could not get
    in. If a member is talking TO you, the room waits for you."""
    return entry.target == HUMAN and entry.member != HUMAN


# A turn can be addressed to the submitter IN WORDS while `target` points somewhere else: in a
# real run "the submitter" appeared in 13 entries and `target == human` in ZERO, so every rule
# keyed on that field was dead code. `target` is model-reported and cannot be trusted for control
# flow — we detect the address ourselves.
# "Sure — let me sketch the data flow" followed by nothing on the board was a real, repeated
# failure. Promising a drawing is treated as the intent itself; we do not rely on the model also
# remembering to fill in a separate field.
_DRAW_PROMISE = re.compile(r"\b(let me (?:sketch|draw|map)|i(?:'ll| will) (?:sketch|draw|map)|"
                           r"put (?:this|these|that|it) on the board|sketch (?:this|it|the))\b", re.I)
_SUBMITTER_PROSE = re.compile(r"\b(the submitter|submitter'?s)\b", re.I)
_SECOND_PERSON_ASK = re.compile(
    r"\b(can you|could you|would you|will you|do you|did you|have you|are you|i need you to|"
    r"i need from you|confirm that|walk (?:me|us) through|show (?:me|us)|what(?:'s| is) your)\b", re.I)


def _addresses_submitter(st: "SessionState", entry: TranscriptEntry) -> bool:
    if entry.member == HUMAN:
        return False
    txt = entry.text or ""
    if _SUBMITTER_PROSE.search(txt):
        return True
    # Requiring type=="question" made the human UNREACHABLE: a real session produced nine turns, all
    # objections, so nothing qualified as directed at them and they were never asked anything. But a
    # second-person phrase alone is not an address either — "can you cite the clause?" thrown at the
    # room is rhetorical, and pausing for it turns the meeting into ping-pong. So an EXPLICIT address
    # counts: a question, or a turn tagged to them, whatever its type.
    if not _SECOND_PERSON_ASK.search(txt):
        return False
    if entry.type != "question" and entry.target != HUMAN:
        return False
    # "Priya, can you…" addresses a COLLEAGUE by name — not the submitter.
    firsts = {(p.name or "").split()[0].lower()
              for pid, p in st.personas.items() if pid != entry.member and p.name}
    lead = txt.strip().split(",")[0].strip().lower()
    return lead not in firsts


def _addressed_colleague(st: "SessionState", entry: TranscriptEntry) -> str:
    """The seated member this turn is speaking TO, by name, or "".

    Vocative anywhere in the text, not just at the start: the measured failure was
    "Good — let's take R-1 first... Priya, walk us through whether server-side validation is a control
    gate", tagged target=human, which handed the SUBMITTER the floor for a question aimed at the security
    architect. The chair then had to post "that question was directed at Priya" — also tagged at the
    human — and the human's own turns in that session were "ok", "?", "?", "ok".
    """
    txt = entry.text or ""
    for pid, p in (st.personas or {}).items():
        if pid == entry.member or not getattr(p, "name", ""):
            continue
        first = (p.name or "").split()[0].strip()
        if len(first) < 3:
            continue
        # "Priya, walk us through" / "over to you, Priya" / "Priya — can you"
        if re.search(rf"(^|[\s(]){re.escape(first)}\s*[,—:]", txt, re.I) or \
           re.search(rf",\s*{re.escape(first)}\b", txt, re.I):
            return pid
    return ""


def _is_for_human(st: "SessionState", entry: TranscriptEntry) -> bool:
    """Does the room owe the SUBMITTER the floor for this turn?

    The model's `target` field is unreliable in BOTH directions and cannot be trusted alone. Sonnet 4.5
    under-tagged: "the submitter" appeared in 13 entries with target==human in zero, so the human was
    never asked anything. Sonnet 5 over-tags: 5 of 13 turns claimed target=human, including a
    floor-opening remark and two questions aimed at named colleagues. So the addressee is decided here,
    from the text, with the model's field as one signal among several.
    """
    if entry.member == HUMAN:
        return False
    if _addressed_colleague(st, entry):
        return False                     # a named colleague outranks any target field
    txt = entry.text or ""
    if _SUBMITTER_PROSE.search(txt):
        return True
    if entry.target == HUMAN:
        # Fix 1 stands: a DEMAND aimed at the submitter ("I need to see the DR architecture") is typed
        # objection/rebuttal, not question — 16 of 26 such turns were aimed at the human and only one
        # was a question, so requiring a question made them unreachable. Over-tagging is damped by the
        # pause rate limit rather than by second-guessing the tag here.
        return True
    if entry.type == "question" and "?" in txt and _SECOND_PERSON_ASK.search(txt):
        # Must be an EXPLICIT address. A second-person phrase inside a CLAIM is rhetorical — "which
        # obligation does this trigger, and can you cite the clause?" thrown at the room reads as a
        # question but is not aimed at anyone, and pausing for it turns the meeting into ping-pong.
        # Loosening this re-broke a case that had already been fixed deliberately.
        return True
    return False


# ---- floor control (server-authoritative) --------------------------------------------
# A client can never be relied on to stop the panel talking over the human: its gate races the
# very turn it is trying to prevent, and it does not survive a reload or a second tab. The
# server owns the floor; the client only reflects it.
def request_floor(st: SessionState, emit: Optional[Emit] = None) -> str:
    """The human raised a hand — the floor is theirs immediately.

    This used to set an intermediate REQUESTED state and leave the promotion to `human` inside
    `step()`. That deadlocked the room: the client stops calling `advance` the moment the floor is
    requested (correctly — it must not talk over the human), so the one code path that could grant the
    floor was never reached again. The session froze with no input box and a silent panel. Commands are
    serialised per session, so by the time this runs any turn in flight has already finished and there
    is nothing to wait for."""
    st._floor_grants = int(getattr(st, "_floor_grants", 0)) + 1
    st.floor = FLOOR_HUMAN
    st.phase = AWAIT_HUMAN
    _emit(emit, {"type": "FLOOR", "holder": st.floor})
    _emit(emit, {"type": "QUORUM_PAUSE", "reason": AWAIT_HUMAN, "question": "", "asked_by": ""})
    return st.floor


def release_floor(st: SessionState, emit: Optional[Emit] = None) -> str:
    """The human lowered their hand or finished speaking — the panel may resume."""
    st.floor = FLOOR_PANEL
    _emit(emit, {"type": "FLOOR", "holder": st.floor})
    return st.floor


def floor_state_event(st: SessionState, emit: Optional[Emit] = None) -> None:
    """Tell a (re)connecting client who holds the floor — the client must never infer it."""
    _emit(emit, {"type": "FLOOR", "holder": st.floor,
                 "awaiting": st.phase == AWAIT_HUMAN, "owed_reply": st.owed_reply})


def _floor_is_the_humans(st: SessionState) -> bool:
    return st.floor in (FLOOR_REQUESTED, FLOOR_HUMAN)


_SKETCH_CALLS_PER_TURN = 1     # the board must not stack model calls onto one turn


def _sketch_ops(st: "SessionState", pid: str, purpose: str, speaker) -> list:
    """Ask the drawing persona's dedicated sketch() call for geometry (Fix 4). Failure is never
    fatal: a board that doesn't draw must not break the meeting."""
    # Measured: gaps between turns reached a median of 39s and a peak of 82s, because one step could
    # make three sketch calls (a member's intent, a worked topic, and the chair's map) on top of the
    # turn itself — the conversation was being slowed down to serve the board. One per turn.
    if getattr(st, "_sketch_calls", 0) >= int(
            _setting(st, "whiteboard.sketch_calls_per_turn", _SKETCH_CALLS_PER_TURN)):
        return []
    st._sketch_calls = getattr(st, "_sketch_calls", 0) + 1
    _route(speaker, st, pid)
    st._sketch_calls_total = int(getattr(st, "_sketch_calls_total", 0)) + 1
    st._model_calls = int(getattr(st, "_model_calls", 0)) + 1
    try:
        try:
            spec = speaker.sketch(st.personas.get(pid), purpose, st.decision,
                                  board_digest=st.whiteboard.digest(), agenda=_agenda_view(st),
                                  inventory=st.whiteboard.inventory(),
                                  repertoire=list(getattr(st.personas.get(pid),
                                                          "draw_repertoire", []) or []))
        except TypeError:                      # a speaker that predates board addressing
            spec = speaker.sketch(st.personas.get(pid), purpose, st.decision,
                                  board_digest=st.whiteboard.digest(), agenda=_agenda_view(st))
    except Exception as e:                                     # noqa: BLE001 - board is optional
        print(f"[quorum.sketch] {pid} could not sketch: {type(e).__name__}: {e}")
        return []
    if isinstance(spec, list):                 # legacy speakers may still return raw ops
        return spec
    spec = spec if isinstance(spec, dict) else {}
    rep = list(getattr(st.personas.get(pid), "draw_repertoire", []) or [])
    ops = _wb_render(spec, st.whiteboard, rep)
    if not ops:
        # No substitution: an out-of-repertoire or absent shape is refused, and recorded as refused.
        st._draw_refusals = int(getattr(st, "_draw_refusals", 0)) + 1
        print(f"[quorum.sketch] {pid} produced no drawable point — nothing added to the board")
        return ops

    # LOOK AT IT before the room does. The pipeline used to be blind — a spec in, ops out, nobody
    # ever seeing the result — which is why boards came back cluttered and nobody noticed. Code
    # supplies the geometry facts; the member judges whether the picture makes its point, with the
    # rendered image in hand where the imaging library is available. One revision, and only when
    # there is something to fix or the diagram is complex enough to be worth a second look.
    audit = _wb_audit(ops, str(spec.get("type") or ""))
    # Recorded HERE because the auditor inspects a rendered spec, not the op log — it cannot be
    # recomputed after the fact from the board's history.
    st._board_problems = int(getattr(st, "_board_problems", 0)) + len(audit.get("problems") or [])
    # Self-review costs a whole extra model call, and the multimodal one is the slowest call in the
    # system: measured p90 for a turn reached 14s and the worst case 67s once every drawing turn paid
    # for speak + sketch + critique. So review only when something is actually WRONG, and only pay for
    # the image when the fault is visual (crossings, overlaps) rather than structural.
    _crit = _setting(st, "whiteboard.critique.when", "on_problems")
    if _crit == "off" or (_crit == "on_problems" and not audit.get("problems")):
        return ops
    if not hasattr(speaker, "critique_sketch"):
        return ops
    try:
        _img = _setting(st, "whiteboard.critique.attach_image", "visual_only")
        visual = (_img == "always") or (_img == "visual_only" and any(
            ("crossing" in p or "overlap" in p) for p in (audit.get("problems") or [])))
        st._critique_calls = int(getattr(st, "_critique_calls", 0)) + 1
        st._model_calls = int(getattr(st, "_model_calls", 0)) + 1
        revised = speaker.critique_sketch(st.personas.get(pid), spec, audit,
                                          _wb_describe(ops), _wb_png(ops) if visual else None)
    except Exception as e:                     # noqa: BLE001 - the board is never fatal
        print(f"[quorum.sketch] {pid} could not review the drawing: {type(e).__name__}: {e}")
        return ops
    if not revised:
        return ops
    ops2 = _wb_render(revised, st.whiteboard, rep)
    if not ops2:
        return ops
    a2 = _wb_audit(ops2, str((revised or {}).get("type") or ""))
    better = (len(a2.get("problems") or []), a2.get("crossings", 9), a2.get("bends", 99)) < \
             (len(audit.get("problems") or []), audit.get("crossings", 9), audit.get("bends", 99))
    print(f"[quorum.sketch] {pid} reviewed its drawing: "
          f"{len(audit.get('problems') or [])} problem(s) -> {len(a2.get('problems') or [])}"
          f"{' (kept revision)' if better else ' (kept original)'}")
    return ops2 if better else ops


def _commit_ops(st: "SessionState", pid: str, ops: list, entry_id: str = "",
                emit: Optional[Emit] = None, max_ops: int = 99) -> int:
    """Append draw ops to the board, authored BY the member, and stream them for animation.
    `max_ops` enforces the persona's affinity draw budget (rule 1/2)."""
    opened = bool(st.whiteboard.live_ops())
    n = 0
    for d in ops:
        if n >= max_ops:
            break
        kind = (d.get("kind") or "").strip().lower()
        if kind not in _WB_OP_KINDS:
            continue
        data = {k: v for k, v in d.items() if k != "kind"}
        op = st.whiteboard.add_op(pid, kind, data, refs=[entry_id] if entry_id else [])
        if not opened:
            _emit(emit, {"type": "WHITEBOARD_OPENED", "by": pid})
            opened = True
        _emit(emit, {"type": "WHITEBOARD_OP", **op.to_dict()})
        n += 1
    return n


def _handle_whiteboard(st: "SessionState", pid: str, raw: dict,
                       entry: Optional[TranscriptEntry], emit: Optional[Emit],
                       speaker=None) -> None:
    """Translate a member's optional turn-JSON `whiteboard` block into board ops + WHITEBOARD_*
    events (spec-quorum-whiteboard.md). Gated (rules 1,2) by env+opt-in and persona affinity, with
    a per-member draw budget scaled by affinity so `high` architects draw readily and `low`
    personas rarely. `ask_submitter` (rule 5) and `request_erase` (rule 3, consent-required) are
    allowed for any drawing persona regardless of draw budget. Inert unless enabled."""
    if not _wb_enabled(st.policy):
        return
    persona = st.personas.get(pid)
    aff = getattr(persona, "whiteboard_affinity", "none") if persona else "none"
    if aff == "none":
        return
    blk = raw.get("whiteboard")
    if not isinstance(blk, dict):
        blk = {}
    # Fix 4: the intent now arrives as flat fields on the turn ("sketch"/"ask_submitter"); the older
    # nested `whiteboard` block is still accepted so batch runs and fixtures keep working.
    if raw.get("sketch"):
        blk = {**blk, "sketch": raw["sketch"]}
    if raw.get("ask_submitter"):
        blk = {**blk, "ask_submitter": raw["ask_submitter"]}
    if not blk and not _DRAW_PROMISE.search(raw.get("text") or ""):
        return
    board = st.whiteboard
    entry_id = entry.entry_id if entry else ""
    budget = {"low": 1, "medium": 3, "high": 99}.get(aff, 0)
    mine = len({o.op_id for o in board.ops if o.author == pid})

    # Fix 4 — the member declares INTENT ("sketch": "map the PII boundary"); geometry comes from a
    # dedicated call with its own token budget. Members used to narrate sketches and emit nothing
    # because coordinates never fit in the 520-token turn.
    intent = (blk.get("sketch") or blk.get("why") or "").strip() if isinstance(blk, dict) else ""
    spoken = (raw.get("text") or "")
    if not intent and _DRAW_PROMISE.search(spoken):
        intent = spoken[:110]          # they said they'd draw it, so draw it
    room = max(0, budget - mine)
    if intent and room and speaker is not None:
        _commit_ops(st, pid, _sketch_ops(st, pid, intent, speaker), entry_id, emit, max_ops=room)
    elif blk.get("draw") and room and (blk.get("why") or "").strip():
        # legacy path: geometry supplied inline, still need-gated by an explicit `why`
        _commit_ops(st, pid, [d for d in blk["draw"] if isinstance(d, dict)], entry_id, emit,
                    max_ops=room)

    # rule 5 — ask the submitter to draw/show something
    ask = (blk.get("ask_submitter") or "").strip()
    if ask:
        _emit(emit, {"type": "WHITEBOARD_REQUEST", "by": pid, "text": ask[:400]})

    # rule 3 — request to erase ANOTHER author's op (dispreferred; consent required, never applied here)
    er = blk.get("request_erase")
    if isinstance(er, dict) and er.get("op_id"):
        req = board.request_erase(str(er["op_id"]), by=pid, reason=(er.get("reason") or "")[:200])
        if req:
            _emit(emit, {"type": "WHITEBOARD_ERASE_REQUEST", **req})


def submitter_draw(st: "SessionState", ops: list, emit: Optional[Emit] = None) -> None:
    """Rule 4: the human submitter adds their own marks to the board."""
    if not _wb_enabled(st.policy):
        return
    opened = bool(st.whiteboard.live_ops())
    for d in ops or []:
        if not isinstance(d, dict):
            continue
        kind = (d.get("kind") or "").strip().lower()
        if kind not in _WB_OP_KINDS:
            continue
        data = {k: v for k, v in d.items() if k != "kind"}
        op = st.whiteboard.add_op(HUMAN, kind, data)
        if not opened:
            _emit(emit, {"type": "WHITEBOARD_OPENED", "by": HUMAN})
            opened = True
        _emit(emit, {"type": "WHITEBOARD_OP", **op.to_dict()})


def resolve_erase(st: "SessionState", op_id: str, approve: bool, emit: Optional[Emit] = None) -> None:
    """Rule 3 (apply): the submitter (or an op's owner) consents to / denies an erase — or the
    submitter erases directly. Consent is the ONLY path that removes another author's mark."""
    if not _wb_enabled(st.policy) or not op_id:
        return
    if approve:
        if st.whiteboard.apply_erase(op_id, approved_by=HUMAN):
            _emit(emit, {"type": "WHITEBOARD_ERASE", "op_id": op_id, "approved_by": HUMAN})
    else:
        st.whiteboard.pending_erase = [r for r in st.whiteboard.pending_erase if r.get("op_id") != op_id]
        _emit(emit, {"type": "WHITEBOARD_ERASE_DENIED", "op_id": op_id})


def whiteboard_state_event(st: "SessionState", emit: Optional[Emit] = None) -> None:
    """Emit the full board on (re)connect so a client can rebuild/replay it (fast-fill then animate)."""
    if _wb_enabled(st.policy):
        _emit(emit, {"type": "WHITEBOARD_STATE", **st.whiteboard.to_dict()})



_AFFINITY_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _draw_the_worked_topic(st: "SessionState", speaker, emit: Optional[Emit] = None) -> None:
    """When the room keeps working a topic and nothing on the board shows it, ask the most visual
    member to draw it — deterministically, rather than hoping someone volunteers.

    Fix 4 moved geometry out of the member turn and left an OPTIONAL `sketch` field in its place.
    Members then never set it: a measured live session had 10 board ops, all authored by the chair,
    and ZERO member sketches across 17 turns. Drawing propensity has to be triggered by the room's
    state, not by the model remembering an affordance. Preferring a non-chair author also spreads
    authorship, which was 100% chair in that session."""
    if not _wb_enabled(st.policy) or speaker is None:
        return
    if st.wb_last_map_round == st.round and st.wb_sketched:
        return                              # one triggered sketch per round; the board must not
                                            # out-talk the room (it drew 8 points for 8 turns)
    worked = [a for a in st.agenda
              if int(a.get("remarks", 1)) >= SATURATED_REMARKS
              and a.get("status") == "open"
              and a.get("topic")
              and a["topic"] not in st.wb_sketched]
    if not worked:
        return
    topic = max(worked, key=lambda a: int(a.get("remarks", 1)))["topic"]
    # the most visual member, preferring someone other than the chair
    def _rank(pid: str):
        p = st.personas.get(pid)
        aff = _AFFINITY_RANK.get(getattr(p, "whiteboard_affinity", "none"), 0)
        is_chair = 1 if getattr(p, "role", "") == "facilitator" else 0
        return (aff, -is_chair)
    candidates = [p for p in st.plan.seated
                  if _AFFINITY_RANK.get(getattr(st.personas.get(p), "whiteboard_affinity", "none"), 0) > 0]
    # The chair already owns the decision map. When it ALSO holds the highest affinity it won every
    # triggered sketch too and authored 56 of 66 marks, so exclude it whenever anyone else can draw.
    non_chair = [p for p in candidates
                 if getattr(st.personas.get(p), "role", "") != "facilitator"]
    candidates = non_chair or candidates
    if not candidates:
        return
    author = max(candidates, key=_rank)
    st.wb_sketched.append(topic)
    ops = _sketch_ops(st, author, f"show '{topic}' concretely: what flows where, and where it breaks",
                      speaker)
    if _commit_ops(st, author, ops, "", emit):
        _emit(emit, {"type": "CHAIR_NOTE",
                     "text": f"{(st.personas.get(author).name if st.personas.get(author) else author)}"
                             f" is putting {topic} on the board."})


def _element_for_topic(st: "SessionState", text: str):
    """Find the board element this remark is about, by subject rather than wording."""
    subj = _subject_key(text or "")
    if not subj:
        return None
    best, best_n = None, 0
    for e in st.whiteboard.inventory():
        n = len(subj & _subject_key(e["label"]))
        if n > best_n:
            best, best_n = e, n
    return best


def _annotate_from_state(st: "SessionState", pid: str, raw: dict,
                         entry: Optional[TranscriptEntry], emit: Optional[Emit] = None) -> None:
    """Mark up the board from what just happened in the room.

    W1 gave members the ability to annotate and a prompt that teaches it; a live run then used it
    ZERO times. That is the third affordance the model ignored (after `kind`/`emphasis` and the
    optional sketch field), so the lesson is settled: an offered capability does not get used, it has
    to be triggered by state. A blocker aimed at something on the board gets circled; a point the room
    settles gets underlined. Each element/mark pair is applied once."""
    if not _wb_enabled(st.policy) or entry is None:
        return
    if not st.whiteboard.live_ops():
        return
    mark = colour = note = None
    if entry.severity == "blocker":
        mark, colour, note = "circle", "#e03131", "BLOCKER"
    elif entry.type in ("concession",) or (raw.get("resolve_topic") or "").strip():
        mark, colour = "underline", "#2f9e44"
    if not mark:
        return
    target = _element_for_topic(st, (raw.get("topic") or "") + " " + (entry.text or ""))
    if not target:
        return
    key = f"{target['id']}|{mark}"
    if key in st.wb_annotated:
        return
    st.wb_annotated.append(key)
    ann = {"targets": [target["id"]], "mark": mark, "color": colour}
    if note:
        ann["text"] = note
    ops = _wb_annotate([ann], st.whiteboard)
    if ops and _commit_ops(st, pid, ops, entry.entry_id, emit):
        print(f"[quorum.board] {pid} {mark}d '{target['label'][:28]}' from a {entry.severity} remark")


def _chair_maps_the_board(st: "SessionState", speaker, seated_n: int,
                          emit: Optional[Emit] = None) -> None:
    """Once per round the FACILITATOR draws where the DECISION stands — not a list of topics.

    The first version of this asked the chair to "map the open threads", and it dutifully drew the
    agenda as rectangles: 11 boxes of headings like 'Operational Visibility', the same label three
    times, appended again every round. That is decoration, not facilitation. A chair's board should
    answer "where are we?": the verdict currently on the table and what it hangs on. It also
    REPLACES its own previous map instead of stacking another copy (own marks need no consent)."""
    if not _wb_enabled(st.policy) or speaker is None:
        return
    if st.turns == 0 or st.turns % max(1, seated_n) != 0:
        return
    if st.wb_last_map_round == st.round:
        return
    chair = next((pid for pid in st.plan.seated
                  if getattr(st.personas.get(pid), "role", "") == "facilitator"
                  and getattr(st.personas.get(pid), "whiteboard_affinity", "none") != "none"), None)
    if not chair:
        return
    # Only worth drawing once the room has produced structure to summarise.
    blockers = [e for e in st.transcript.open_issues if e.severity == "blocker"]
    conditions = [e for e in st.transcript.entries
                  if e.severity == "material" and e.member != HUMAN]
    if not blockers and len(conditions) < 2:
        return
    st.wb_last_map_round = st.round
    verdict = "NO-GO while a blocker stands" if blockers else "GO with conditions"
    # Label conditions with their short AGENDA TOPIC, never the spoken sentence: quoting speech put
    # things like "Understood - PII logging i..." in boxes, which reads as noise on a board.
    def _label_for(e) -> str:
        item = _find_topic(st, e.text or "")
        # No agenda topic => no box. Falling back to the spoken line put quotes like
        # "Thank you - that's accepted as a c..." on the board, which reads as noise.
        return (item.get("topic") or "")[:34] if item else ""
    seen_l, nodes = set(), [{"label": verdict}]
    for e in (blockers + conditions):
        lbl = _label_for(e)
        if lbl and lbl.lower() not in seen_l:
            seen_l.add(lbl.lower())
            nodes.append({"label": lbl})
        if len(nodes) >= 5:      # W6: a compact map; the chair should not fill the board
            break
    # Redraw ONLY when the decision has actually MOVED. The first version replaced its own map every
    # round with an IDENTICAL one, so the operator watched the same diagram re-animate ("the
    # whiteboard repeats the same thing") and the chair authored 20 of 21 ops on the board.
    sig = "|".join(n["label"] for n in nodes)
    if sig == st.wb_map_sig:
        return
    st.wb_map_sig = sig
    for op in list(st.whiteboard.live_ops()):        # now supersede the previous map
        if op.author == chair and (op.data or {}).get("_map"):
            st.whiteboard.apply_erase(op.op_id, approved_by=chair)
            _emit(emit, {"type": "WHITEBOARD_ERASE", "op_id": op.op_id, "approved_by": chair})
    ops = _wb_render({"point": "Where the decision stands right now", "type": "decision",
                      "nodes": nodes}, st.whiteboard,
                     list(getattr(st.personas.get(chair), "draw_repertoire", []) or []) + ["decision"])
    for o in ops:
        o["_map"] = True                       # tag so the next map can replace it
    if _commit_ops(st, chair, ops, "", emit):
        _emit(emit, {"type": "CHAIR_NOTE", "text": "Let me put where we've landed on the board."})


def step(st: SessionState, speaker: Speaker, emit: Optional[Emit] = None) -> Optional[str]:
    """Run ONE turn, with the MODERATOR choosing who speaks next from the LIVE discussion (not
    round-robin): whoever must answer a challenge aimed at them, strongly disagrees, or hasn't
    been heard on a live thread. Pauses (await_human) when a member puts a direct question to the
    human; offers the verdict when the moderator judges the debate exhausted or the turn budget hits."""
    if st.phase not in (DELIBERATING, AWAIT_HUMAN, CONVERGENCE_OFFER):
        return None
    # The human holds (or has just claimed) the floor: no new turn may start. This is the
    # authoritative version of "don't talk over me" — it cannot race a client timer.
    if _floor_is_the_humans(st):
        st.floor = FLOOR_HUMAN
        _emit(emit, {"type": "FLOOR", "holder": FLOOR_HUMAN})
        st.phase = AWAIT_HUMAN
        return AWAIT_HUMAN
    if st.phase in (AWAIT_HUMAN, CONVERGENCE_OFFER):
        _emit(emit, {"type": "QUORUM_RESUMED"})
    st.phase = DELIBERATING
    seated_n = max(1, len(st.plan.seated))
    _t_started = time.time()              # this ATTEMPT — productive or not (see SessionState._attempts)
    st._sketch_calls = 0                  # per-turn board budget (see _sketch_ops)
    for _p in _chair_rules_agenda(st):    # a moderator closes exhausted items itself
        _emit(emit, {"type": "AGENDA_PARKED", "topic": _p, "by": "chair"})
    st._model_calls = int(getattr(st, "_model_calls", 0))       # cumulative, for _run_metrics.json
    hard_cap = max(int(st.policy.get("max_rounds", 4)), 3) * seated_n * 2  # generous safety backstop

    def _converge():
        if st.owed_reply:
            return None                      # never wind down while the human is unanswered
        st.converged = True
        st.phase = CONVERGENCE_OFFER
        _emit(emit, {"type": "QUORUM_PAUSE", "reason": CONVERGENCE_OFFER,
                     "open_issues": len(st.transcript.open_issues),
                     "dissent": len(st.transcript.dissent)})
        return CONVERGENCE_OFFER

    # 1) who holds the floor next? (a member answering the human takes priority)
    if st.pending_speaker and st.pending_speaker in st.plan.seated:
        pid = st.pending_speaker
        st.pending_speaker = None
    else:
        seated_personas = [st.personas[p] for p in st.plan.seated if p in st.personas]
        choice = None
        if st.owed_reply:
            # Floor is owed to the human. Prefer the member who ASKED them something (natural: you
            # answer the person who asked); otherwise the least-recently-heard member. No model call.
            asker = next((q.get("member") for q in reversed(st.asked_questions)
                          if q.get("status") == "answered" and q.get("member") in st.plan.seated), None)
            choice = asker if asker and asker not in _recent_speakers(st, 1) else _fallback_speaker(st)
            if choice in st.answer_givers:      # each member answers the ask at most once
                nxt = [p for p in st.plan.seated if p not in st.answer_givers
                       and p not in _recent_speakers(st, 1)]
                choice = nxt[0] if nxt else choice
        _alloc = _setting(st, "room.turn_allocation", "moderated")
        if choice is None and _alloc != "moderated":
            choice = _allocate_speaker(st)        # E1: no model call, and it cannot pick an empty hand
            if choice is None and st.turns >= seated_n:
                return _converge()
        try:
            if choice is None:
                st._model_calls = int(getattr(st, "_model_calls", 0)) + 1
                _route(speaker, st, CHAIR_SEAT)   # allocation is the chair's job, not a member's
                choice = speaker.next_speaker(st.decision, st.context, st.transcript.view(),
                                              seated_personas, _recent_speakers(st),
                                              agenda=_agenda_view(st), owed_reply=bool(st.owed_reply))
        except Exception:
            choice = None
        if choice in (None, "none", "None", "") and st.turns >= seated_n:
            return _converge()                     # moderator judged the room done (after ≥ one round)
        pid = choice if (choice in st.plan.seated) else _fallback_speaker(st)
        # Fairness guard: the moderator kept returning the same voice (measured 36% of all turns for
        # one member, 64% for two). Nobody is silenced — a member well ahead of the least-heard
        # member simply waits their turn.
        #
        # Counting only SUCCESSFUL turns deadlocked the room: a member with nothing new restated a
        # closed topic, was refused, yielded — and because yielding did not count as being heard it
        # stayed "least heard" and was picked again immediately, six times in ninety seconds. So an
        # attempted turn counts, and a member who has just yielded steps aside while others speak.
        _heard = {p: 0 for p in st.plan.seated}
        for e in st.transcript.entries:
            if e.member in _heard:
                _heard[e.member] += 1
        for p, t in (st.yielded_at or {}).items():
            if p in _heard:
                _heard[p] += 1                       # the attempt counts, even though nothing landed
        cooldown = max(2, len(st.plan.seated) // 2)
        fresh = [p for p in st.plan.seated
                 if st.turns - int((st.yielded_at or {}).get(p, -99)) > cooldown]
        pool = fresh or list(st.plan.seated)         # everyone stuck? fall back to the whole panel
        if pid not in pool:
            pid = min(pool, key=lambda p: (_heard[p], st.plan.seated.index(p)))
        elif pid in _heard and _heard[pid] > min(_heard[p] for p in pool) + 1:
            pid = min(pool, key=lambda p: (_heard[p], st.plan.seated.index(p)))
        # HARD rule: never let the same member speak twice in a row (stops one voice monopolising/looping)
        last = _recent_speakers(st, 1)
        if last and pid == last[0] and len(st.plan.seated) > 1:
            fb = _fallback_speaker(st)
            if fb and fb != pid:
                pid = fb
    persona = st.personas.get(pid) if pid else None
    if persona is None:
        return None

    # 2) they speak — streamed live
    # 2) they speak. We GENERATE first, validate novelty, and regenerate ONCE if the remark merely
    #    repeats the room (regenerating the offending turn is the strongest published mitigation for
    #    problem drift), and only THEN reveal it — so nothing half-formed or redundant ever appears.
    must_answer = bool(st.owed_reply)
    chair = "" if must_answer else (_chair_note(st) if st.no_progress >= 2 else "")
    _emit(emit, {"type": "MEMBER_TURN", "round": st.round, "member": pid,
                 **({"answering_human": True} if must_answer else {})})
    if chair:
        _emit(emit, {"type": "CHAIR_NOTE", "text": chair})

    # Novelty is judged on the SUBSTANCE (the detail), not the short spoken line.
    recent_texts = [((e.detail or "") + " " + (e.text or "")).strip()
                    for e in st.transcript.entries if e.member not in (HUMAN, "moderator")][-12:]

    def _generate(avoid):
        st._model_calls = int(getattr(st, "_model_calls", 0)) + 1
        _route(speaker, st, pid)
        return speaker.speak(
            persona, st.decision, (st.brief or st.context), st.transcript.view(), st.round,
            must_answer_human=must_answer,
            human_question=(st.owed_question if must_answer else ""),
            prior_answers=([e.text for e in st.transcript.entries
                            if e.member in st.answer_givers][-3:] if must_answer else []),
            agenda=_agenda_view(st),
            lane_map=_lane_map(st),
            banned_openings=_recent_openings(st),
            asked_and_answered=[q["text"] for q in st.asked_questions
                                if q.get("status") == "answered"][-4:],
            chair_note=chair,
            avoid_texts=avoid,
            opening_mode=("" if must_answer else _OPENING_MODES[st.turns % len(_OPENING_MODES)]),
            whiteboard=_wb_enabled(st.policy),
            board_digest=(st.whiteboard.digest() if _wb_enabled(st.policy) else ""),
            meeting_frame=st.meeting_frame,
            max_tokens=int(_setting(st, "models.max_tokens.turn", 520)),
            gates_mode=str(_setting(st, "gates.mode", "reject_and_regenerate")),
            allowed_topics=_allowed_topics(st, pid),
            agreeableness=str(((_setting(st, "personas.overrides", {}) or {}).get(pid) or {})
                              .get("agreeableness")
                              or _setting(st, "personas.agreeableness", "balanced")),
        ) or []

    def _subst(r):
        return ((r.get("detail") or "") + " " + (r.get("text") or "")).strip()

    def _faults(cand: dict) -> list[str]:
        """Every reason this turn should not be spoken, evaluated TOGETHER.

        These checks were originally chained: each rejection regenerated the turn, and the fresh turn
        was then judged by the next check, so one turn could cost four model calls (measured: median
        11.2s, p90 20.1s, worst 64.9s, with a gate firing on a third of turns). Worse, each retry was
        blind — a turn regenerated to fix repetition was then rejected for a closed topic it was never
        told about. Evaluating all of them at once means at most ONE retry, and that retry carries
        every reason.

        Fix 3's finding still holds: the prompt already forbids all of this and models ignore it
        (16 of 22 turns restated one agreed fix), so it must be enforced in code — but enforcement
        does not have to be serial."""
        out = []
        if _setting(st, "gates.novelty.enabled", True) and _too_similar(_subst(cand), recent_texts):
            out.append("repeated what the room has already said")
        if _repeats_answered_question(st, cand, pid):
            out.append("re-asked a question the submitter already answered")
        if _restates_closed_topic(st, cand, pid):
            closed = (cand.get("topic") or "").strip()
            out.append(f"restated '{closed}', which is settled or already worked to its cap")
        return out

    gate_mode = _setting(st, "gates.mode", "reject_and_regenerate")
    budget = 0 if gate_mode in ("off", "warn_only", "prevent") else int(
        _setting(st, "gates.max_retries_per_turn", 1))

    raws = _generate([])
    faults = _faults(raws[0]) if raws else []
    if faults:
        st._gate_faults = int(getattr(st, "_gate_faults", 0)) + 1
    if faults and gate_mode != "off":
        print(f"[quorum.step] {pid}: {'; '.join(faults)}" +
              (f" — regenerating (budget {budget})" if budget else " — allowed through (warn_only)"))
    attempts = 0
    while raws and faults and attempts < budget:
        attempts += 1
        st._gate_retries = int(getattr(st, "_gate_retries", 0)) + 1
        retry = _generate([_subst(raws[0])] + recent_texts[-3:])
        if not retry:
            raws = []
            break
        retry_faults = _faults(retry[0])
        if not retry_faults:
            raws, faults = retry, []
            break
        raws, faults = retry, retry_faults
    if raws and faults and gate_mode not in ("off", "warn_only"):
        # Out of budget and still faulty: yielding is honest and free, where another call is neither.
        # NOT counted here: emptying raws makes the turn unproductive, and the single counting point
        # below sees that. Incrementing in both places double-counted every gate-caused yield — a live
        # run recorded 12 yields for 6 unproductive attempts, and the inflated figure reached the paper.
        raws = []
        _emit(emit, {"type": "MEMBER_PASSED", "member": pid, "reason": faults[0]})

    def _record_attempt(produced: bool) -> None:
        st._attempts = list(getattr(st, "_attempts", []) or [])
        st._attempts.append([time.time() - _t_started, bool(produced)])

    paused = False
    added_any = False
    # A member may address the human on nearly every turn; pausing for each one turns the meeting into
    # ping-pong. A real question always stops the room; a demand in passing waits for a gap.
    _recent_pause = st.turns - int(getattr(st, "last_pause_turn", -99))
    for raw in raws:
        # Reveal ONLY what they say out loud; the detail travels on the entry and is expandable.
        _emit(emit, {"type": "QUORUM_DELTA", "member": pid, "text": raw.get("text", "")})
        entry = _add_entry(st, st.round, pid, raw, emit)
        added_any = True
        _handle_whiteboard(st, pid, raw, entry, emit, speaker)
        _annotate_from_state(st, pid, raw, entry, emit)
        if _is_for_human(st, entry):
            _limit = int(_setting(st, "floor.pause_rate_limit_turns", 2))
            _mode = _setting(st, "floor.pause_on", "explicit_address")
            if _mode != "never" and (entry.type == "question" or _mode == "any_directed"
                                     or _recent_pause >= _limit):
                paused = True
    if added_any and must_answer:
        # A turn that merely lobs ANOTHER question back at the human does not discharge the debt.
        answered = any(not (r.get("type") == "question" and r.get("target") == HUMAN) for r in raws)
        if answered:
            if pid not in st.answer_givers:
                st.answer_givers.append(pid)
            st.owed_answers = max(0, st.owed_answers - 1)
            if st.owed_answers == 0:
                st.owed_reply = False
                st.owed_question = ""

    st.turns += 1
    st.round = st.turns // seated_n
    _draw_the_worked_topic(st, speaker, emit)
    _chair_maps_the_board(st, speaker, seated_n, emit)
    # Progress = a NEW agenda item, a rebuttal/concession (real engagement), or answering the human.
    if added_any:
        prog = any((r.get("topic") and _find_topic(st, r.get("topic")) and
                    int(_find_topic(st, r.get("topic")).get("remarks", 1)) <= 1)
                   or r.get("type") in ("rebuttal", "concession", "dissent") for r in raws) or must_answer
        st.no_progress = 0 if prog else st.no_progress + 1
    _record_attempt(added_any)                # one row per attempt, before any exit branch
    if added_any:
        st.passes = 0
    else:
        st.passes += 1                        # a member yielded — nothing new to add
        st.yielded_at[pid] = st.turns         # …and steps aside for a few turns
        st._yields = int(getattr(st, "_yields", 0)) + 1
        _emit(emit, {"type": "MEMBER_PASSED", "member": pid})

    if paused:
        st.phase = AWAIT_HUMAN
        st.last_pause_turn = st.turns
        st.floor = FLOOR_HUMAN        # a member addressed them: the floor is theirs, server-side
        q = next((e for e in reversed(st.transcript.entries) if e.target == HUMAN and e.member != HUMAN), None)
        _emit(emit, {"type": "FLOOR", "holder": FLOOR_HUMAN})
        _emit(emit, {"type": "QUORUM_PAUSE", "reason": AWAIT_HUMAN,
                     "question": (q.text if q else ""), "asked_by": (q.member if q else "")})
        return AWAIT_HUMAN
    # Converge only when the room has REALLY run dry (several consecutive yields after genuine
    # discussion) or the backstop hits. The human can End & rule any time.
    if (st.passes >= 3 and st.turns >= seated_n) or st.turns >= hard_cap:
        return _converge()
    return None


def inject_human(st: SessionState, text: str, target: Optional[str] = None,
                 entry_type: str = "question", emit: Optional[Emit] = None,
                 received_at: Optional[float] = None) -> SessionState:
    """Insert the human's contribution AT THE POINT THEY SPOKE (receipt time), and record that the
    panel now OWES them a reply — the next speaker must address it (enforced in step())."""
    raw = {"type": entry_type if entry_type in ("question", "objection", "claim") else "question",
           "text": text, "target": target, "severity": "material"}
    _add_entry(st, st.round, HUMAN, raw, emit, seq=received_at)
    # They have spoken — the floor returns to the panel (which now owes them an answer).
    st.floor = FLOOR_PANEL
    _emit(emit, {"type": "FLOOR", "holder": FLOOR_PANEL})
    low = (text or "").lower()
    # "can you ALL tell me…" must get more than one answer; a plain reply gets one.
    st.owed_answers = 3 if re.search(r"\b(all of you|you all|everyone|each of you|all\b)", low) else 1
    st.owed_reply = True
    st.answer_givers = []
    st.owed_question = (text or "").strip()[:600]     # the exact words the next speaker must answer
    # If a member had a question outstanding, THIS is its answer: close it (so it is never re-asked)
    # and mark that topic answered — regardless of whether the human used "acceptance" words.
    for q in reversed(st.asked_questions):
        if q.get("status") == "open":
            q["status"] = "answered"
            q["answer"] = (text or "")[:300]
            st.pending_speaker = st.pending_speaker or q.get("member")   # the ASKER replies first
            a = _find_topic(st, q.get("text", ""))
            if a and a.get("status") == "open":
                a["status"] = "answered"
            break
    settled = _settle_agenda_from_human(st, text)
    if settled:
        _emit(emit, {"type": "AGENDA_SETTLED", "topics": settled})
    if target and target in st.plan.seated:
        st.pending_speaker = target
    if st.phase in (AWAIT_HUMAN, CONVERGENCE_OFFER):
        st.phase = DELIBERATING
    st.converged = False
    _emit(emit, {"type": "QUORUM_RESUMED"})
    return st


def render_verdict(st: SessionState, speaker: Speaker, emit: Optional[Emit] = None) -> Verdict:
    """Render the ruling now (human-called or accepted convergence offer)."""
    # The judge is pinned to the chair's model: a verdict rendered by a different model between two
    # runs makes the comparison about the judge rather than the deliberation.
    _route(speaker, st, CHAIR_SEAT)
    # Rule 6: the shared whiteboard is weighed in the ruling — fold its digest into the context.
    _ctx = st.context
    _bd = st.whiteboard.digest() if _wb_enabled(st.policy) else ""
    if _bd:
        _ctx = f"{st.context}\n\nON THE SHARED WHITEBOARD (drawn by the panel/submitter — weigh this):\n{_bd}"
    v = _render_verdict(st.decision, _ctx, st.transcript, st.plan,
                        st.personas, st.policy, speaker, agenda=_agenda_view(st))
    st.verdict = v
    st.phase = DONE
    _emit(emit, {"type": "VERDICT_RENDERED", **v.to_dict()})
    return v
