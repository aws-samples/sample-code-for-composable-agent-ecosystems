"""Verdict synthesis — render the defensibility ruling from the deliberation.

The Speaker proposes grade/rationale; the engine *enforces* the invariants that make the
verdict defensible: an unresolved blocker or preserved dissent cannot yield a clean
"defensible", and minority positions are attached rather than averaged away (spec §4).
"""
from __future__ import annotations

import re

from typing import Optional

from .deliberation import Speaker
from .models import GRADES, Persona, QuorumPlan, Transcript, Verdict




def _subject_of(text: str) -> set:
    """Content words of a claim, for deciding whether two blockers are the same one twice."""
    from .session import _subject_key
    return _subject_key(text or "")


def _claim_of(text: str, names: set) -> str:
    """The defect being alleged, with the meeting-talk stripped off the front.

    A verdict is a board artefact, and it was quoting turn-openers verbatim: "Priya, on the injection
    surface — I'll add the data angle", "Picking up where I left off — R-4's Bedrock invocation logs",
    "The chair asked me to take R-1 directly, and here it is: ...". Those are conversational scaffolding,
    not architecture defects. Two narrow, explainable rules rather than a phrase list:
      1. a leading VOCATIVE addressed to a colleague we can name, and
      2. a short leading clause (before an em-dash or colon) that talks about the SPEAKER rather than the
         system — it contains a first-person pronoun and no more than a dozen words.
    """
    t = " ".join(str(text or "").split())
    for n in names:
        if n and t.lower().startswith(n.lower() + ","):
            t = t[len(n) + 1:].strip()
            break
    for sep in ("—", " - ", ":"):
        head, _, tail = t.partition(sep)
        if not tail:
            continue
        words = head.split()
        if len(words) <= 12 and re.search(r"\b(I|me|my|we|us)\b", head) and tail.strip():
            t = tail.strip()
            break
    return t[:400]


def render_verdict(
    decision: str,
    context: str,
    transcript: Transcript,
    plan: QuorumPlan,
    personas_by_id: dict[str, Persona],
    policy: dict,
    speaker: Speaker,
    agenda: Optional[dict] = None,
) -> Verdict:
    try:
        raw = speaker.verdict(decision, context, transcript, plan, agenda=agenda) or {}
    except TypeError:
        raw = speaker.verdict(decision, context, transcript, plan) or {}
    grade = raw.get("grade") if raw.get("grade") in GRADES else "defensible"

    open_blockers = [e for e in transcript.open_issues if e.severity == "blocker"]
    dissents = transcript.dissent

    # Enforce defensibility invariants — the verdict cannot be cleaner than the record.
    if open_blockers:
        grade = "indefensible"
    elif dissents and grade == "defensible":
        grade = "defensible_with_conditions"

    def _member_name(pid: str) -> str:
        p = personas_by_id.get(pid)
        return p.name if p else pid

    against = sorted({_member_name(e.member) for e in (open_blockers + dissents)})

    # One element per DISTINCT blocker. The same blocker restated in three turns produced three
    # "indefensible elements": a live verdict listed SEVEN while its own summary said "three unresolved
    # blockers", so the count measured how much the room talked rather than how much was wrong. And the
    # argument is the entry's DETAIL, which the entry has been carrying all along while this duplicated
    # the spoken line into both fields.
    _names = {str(getattr(p, "name", "") or "").split()[0]
              for p in (personas_by_id or {}).values() if getattr(p, "name", "")}
    # Group by the room's OWN grouping — the agenda. Deduping on claim text alone is unreliable: the
    # same blocker restated shared a single distinctive word ("deterministic") with its own paraphrase,
    # which no threshold separates safely from two different blockers that happen to share "data". The
    # agenda already collapses paraphrase into one item, which is why the summary said "three unresolved
    # blockers" while this list showed seven.
    _topics = [t for k in ("raised", "saturated", "settled_by_human", "parked")
               for t in ((agenda or {}).get(k) or []) if isinstance(t, str)]

    def _bucket(claim: str) -> str:
        subj = _subject_of(claim)
        best, score = "", 0.0
        for t in _topics:
            ts = _subject_of(t)
            if not (subj and ts):
                continue
            ov = len(subj & ts) / max(1, min(len(subj), len(ts)))
            if ov > score:
                best, score = t, ov
        return best if score >= 0.5 else "claim:" + " ".join(sorted(subj))[:60]

    indefensible_elements, _buckets = [], set()
    for e in open_blockers:
        claim = _claim_of(e.text, _names)
        key = _bucket(claim)
        if key in _buckets:
            continue                      # the same blocker, already listed once
        _buckets.add(key)
        indefensible_elements.append({
            "element": claim,
            "argument": (e.detail or "").strip()[:900] or claim,
            "by": _member_name(e.member), "severity": e.severity, "entry_id": e.entry_id,
            "topic": key if not key.startswith("claim:") else "",
        })
    indefensible_elements = indefensible_elements or raw.get("indefensible_elements", [])

    dissent_preserved = [
        {"by": _member_name(e.member), "position": e.text, "entry_id": e.entry_id}
        for e in dissents
    ]

    return Verdict(
        grade=grade,
        summary=raw.get("summary", ""),
        against_whom=raw.get("against_whom") or against,
        indefensible_elements=indefensible_elements,
        remediation=raw.get("remediation", []),
        dissent_preserved=dissent_preserved,
        composition_summary=(
            f"{len(plan.seated)} seated, {len(plan.excluded)} excluded"
            + (f"; missing: {', '.join(plan.missing_dimensions)}" if plan.missing_dimensions else "; no coverage gaps")
        ),
    )
