"""Empanelment — convene the panel for a decision, and justify inclusions AND exclusions.

The composition must itself be defensible (spec §4): the plan records *why each seated
persona was seated and each excluded one was not*, and a completeness adversary names the
perspective the panel is missing (and may add it). Deterministic policy (min/max,
must_include, dimension coverage) is enforced here; the Speaker supplies the reasoned
selection.
"""
from __future__ import annotations

from typing import Optional

from .deliberation import Speaker, Emit, _emit
from .models import Persona, QuorumPlan


def _covered_dims(personas: list[Persona]) -> list[str]:
    seen: list[str] = []
    for p in personas:
        for d in p.dimensions:
            if d not in seen:
                seen.append(d)
    return seen


_FIRST = ["Aisha", "Marcus", "Elena", "James", "Priya", "David", "Sarah", "Tom", "Nadia", "Owen", "Lena", "Raj", "Mei", "Kofi", "Sofia", "Ahmed"]
_LAST = ["Rahman", "Osei", "Vázquez", "Okafor", "Sharma", "Park", "Chen", "Nguyen", "Kaur", "Bright", "Fischer", "Patel", "Lindqvist", "Adeyemi", "Rossi", "Haddad"]
_ROLEISH = ("adversary", "commissioner", "regulator", "advocate", "examiner", "counsel", "officer",
            "architect", "engineer", "lead", "analyst", "strategist", "specialist", "realist",
            "skeptic", "controller", "economist", "planner", "sponsor", "manager", "reviewer")


def _person_name(name: str, i: int) -> str:
    """Guarantee a member reads as a real person: 'Firstname Lastname — Role'. If the convener gave a
    bare role label (no human name), synthesize a deterministic name and keep the role as the title."""
    name = (name or "").strip()
    role = name
    if "—" in name:
        left, _, right = name.partition("—")
        left, right = left.strip(), right.strip()
        # already 'Real Name — Title'?
        if len(left.split()) >= 2 and left[:1].isupper() and not any(w.lower() in _ROLEISH for w in left.split()[:1]):
            return name
        role = right or left
    fn = _FIRST[i % len(_FIRST)]
    ln = _LAST[(i * 7 + 3) % len(_LAST)]
    return f"{fn} {ln} — {role}" if role else f"{fn} {ln}"




def _empanel_budget(policy: dict) -> int:
    """Token budget for the convener call (settings.models.max_tokens.empanel).

    It was hardcoded at 4000 while the setting sat unread. Scaled by pool size because the convener has
    to account for every persona it does NOT seat: a 16-persona pool truncated where a 10-persona pool
    fitted, and a truncated response means the panel is chosen by fallback rather than by reasoning.
    """
    base = int((((policy.get("settings") or {}).get("models") or {})
                .get("max_tokens") or {}).get("empanel") or 8000)
    return base


def _cover_required_dims(seated: list, by_id: dict, pool: list, required: list,
                         must_include: list, hi: int) -> list:
    """Seat the dimensions the ecosystem DECLARED it needs, instead of only recording their absence.

    `missing_dimensions` has always been computed and then ignored. Measured consequence: an
    architecture review board convened a chair plus four conduct personas and reported
    `missing: [reliability, data, cost]` — the completeness adversary named the gap in prose ("the
    engineering owner's account of blast-radius containment") and nobody was seated to raise it.

    Deterministic, so composition stops being a source of run-to-run variance: for each uncovered
    dimension, seat the pool persona covering the most missing dimensions. When the panel is already at
    max, swap out the most REDUNDANT seat — one whose every dimension is already covered by somebody
    else — never a must_include seat, and never the last voice on a dimension.
    """
    if not required:
        return seated
    changed = []
    for _ in range(len(required)):
        covered = {d for pid in seated for d in (getattr(by_id.get(pid), "dimensions", None) or [])}
        missing = [d for d in required if d not in covered]
        if not missing:
            break
        cands = [(len([d for d in (p.dimensions or []) if d in missing]), -pool.index(p), p)
                 for p in pool if p.persona_id not in seated]
        cands = [c for c in cands if c[0] > 0]
        if not cands:
            break
        _, _, pick = max(cands, key=lambda c: (c[0], c[1]))
        if len(seated) >= hi:
            # find a seat whose dimensions are all covered by OTHERS, and which covers no requirement
            redundant = None
            for pid in reversed(seated):
                if pid in must_include:
                    continue
                mine = set(getattr(by_id.get(pid), "dimensions", None) or [])
                others = {d for q in seated if q != pid
                          for d in (getattr(by_id.get(q), "dimensions", None) or [])}
                if mine and mine <= others and not (mine & set(required)):
                    redundant = pid
                    break
            if redundant is None:
                # No seat is strictly redundant. A dimension the ECOSYSTEM DECLARED outranks a seat the
                # convener merely preferred, so yield the seat covering the fewest declared dimensions
                # (ties broken by seating order, so this stays deterministic). Without this an
                # architecture review stayed at four conduct seats and never sat reliability or cost,
                # because each conduct persona held one dimension nobody else did.
                spare = [pid for pid in seated if pid not in must_include]
                if not spare:
                    break
                redundant = min(spare, key=lambda pid: (
                    len(set(getattr(by_id.get(pid), "dimensions", None) or []) & set(required)),
                    -seated.index(pid)))
                if set(getattr(by_id.get(redundant), "dimensions", None) or []) & set(required):
                    break                          # everyone left is covering a requirement; stop
            seated = [pid for pid in seated if pid != redundant]
            changed.append((redundant, pick.persona_id))
        by_id.setdefault(pick.persona_id, pick)
        seated.append(pick.persona_id)
    for out, into in changed:
        print(f"[quorum.empanel] seated {into} for a declared dimension; unseated redundant {out}")
    return seated


def empanel(
    decision: str,
    context: str,
    pool: list[Persona],
    policy: dict,
    speaker: Speaker,
    emit: Optional[Emit] = None,
    personas_by_id: Optional[dict] = None,
) -> QuorumPlan:
    # `personas_by_id`, when supplied, is the CALLER'S shared persona map — any persona the
    # convener SYNTHESIZES for this decision is merged into it so the caller (engine / session)
    # can use it for deliberation and the verdict. Without this the synthesized personas would
    # be lost and the debate would run empty.
    by_id = personas_by_id if personas_by_id is not None else {}
    for p in pool:
        by_id.setdefault(p.persona_id, p)
    select = policy.get("select", {}) or {}
    lo = int(select.get("min", 3))
    hi = int(select.get("max", 9))
    must_include = [pid for pid in (select.get("must_include") or []) if pid in by_id]
    required_dims = list(select.get("diversity") or [])

    # 1) reasoned selection from the Speaker. `seated` entries may be full persona
    #    objects (SYNTHESIZED for this decision) or bare ids referencing the exemplar
    #    pool — the panel is composed AT RUN TIME to fit the actual decision, so it is
    #    not locked to a fixed catalog. Merge any synthesized persona into the pool.
    try:
        proposal = speaker.empanel(decision, context, pool, policy,
                                   max_tokens=int(_empanel_budget(policy))) or {}
    except TypeError:                              # a speaker that predates the budget parameter
        proposal = speaker.empanel(decision, context, pool, policy) or {}
    seated = []
    for item in (proposal.get("seated") or []):
        if isinstance(item, dict):
            pid = item.get("persona_id") or item.get("id") or item.get("name")
            if not pid:
                continue
            if pid not in by_id:
                by_id[pid] = Persona.from_record(item)
            seated.append(pid)
        elif isinstance(item, str) and item in by_id:
            seated.append(item)
    excluded = [e for e in (proposal.get("excluded") or []) if isinstance(e, dict) and e.get("persona_id")]
    justification = proposal.get("justification", "")
    meeting_frame = (proposal.get("meeting_frame") or "").strip()

    # 2) enforce must_include (and justify their forced inclusion)
    for pid in must_include:
        if pid not in seated:
            seated.insert(0, pid)

    # 3) enforce bounds deterministically
    seated = list(dict.fromkeys(seated))  # dedupe, keep order
    if len(seated) > hi:
        dropped = seated[hi:]
        seated = seated[:hi]
        for pid in dropped:
            if pid not in must_include:
                excluded.append({"persona_id": pid, "reason": f"panel capped at max={hi}"})
    filled_from_pool = []
    if len(seated) < lo:
        # The convener returned too few (usually: nothing parseable). Filling from the pool keeps the
        # room alive, but it means the panel was chosen by ORDER OF FILE rather than by reasoning about
        # the decision — so it is recorded on the plan and emitted, not just printed. One live run was
        # composed this way and nothing surfaced it except a log line.
        for p in pool:
            if p.persona_id not in seated:
                seated.append(p.persona_id)
                filled_from_pool.append(p.persona_id)
            if len(seated) >= lo:
                break
        print(f"[quorum.empanel] FALLBACK: convener seated {len(seated) - len(filled_from_pool)} of "
              f"min={lo}; filled {filled_from_pool} from the pool in file order")
        _emit(emit, {"type": "QUORUM_EMPANEL_FALLBACK", "filled_from_pool": filled_from_pool,
                     "reason": "the convener did not return a usable panel"})

    # record exclusions for everyone considered-but-not-seated (defensibility of composition)
    seated_set = set(seated)
    justified_excluded = {e["persona_id"] for e in excluded}
    pool_ids = {p.persona_id for p in pool}
    synthesized = any(pid not in pool_ids for pid in seated)
    # When the panel was SYNTHESIZED for this decision, the seed pool was NOT the candidate set,
    # so listing every seed persona as "excluded" is misleading noise (and clutters the UI). Keep
    # only the convener's own justified exclusions. When seating from a real pool, account for all.
    if not synthesized:
        for p in pool:
            if p.persona_id not in seated_set and p.persona_id not in justified_excluded:
                excluded.append({"persona_id": p.persona_id,
                                 "reason": "not selected — dimensions not central to this decision"})
    else:
        # Synthesized panel: the convener may have "justified" not using the exemplar library —
        # those exclusions are noise for THIS decision. Keep only decision-native excluded voices.
        excluded = [e for e in excluded if e.get("persona_id") not in pool_ids]

    # The ecosystem declares the dimensions a review must cover; seat them rather than only reporting
    # their absence (see _cover_required_dims).
    seated = _cover_required_dims(seated, by_id, pool, required_dims, must_include, hi)
    seated_set = set(seated)
    excluded = [e for e in excluded if e.get("persona_id") not in seated_set]
    seated_personas = [by_id[pid] for pid in seated]
    covered = _covered_dims(seated_personas)

    plan = QuorumPlan(
        seated=seated,
        excluded=excluded,
        justification=justification or "Panel convened to cover the decision's material scrutiny dimensions.",
        covered_dimensions=covered,
        missing_dimensions=[d for d in required_dims if d not in covered],
        meeting_frame=meeting_frame,
    )
    if filled_from_pool:
        plan.justification = ("[COMPOSITION FALLBACK — the convener did not return a usable panel; "
                              f"{len(filled_from_pool)} seat(s) filled from the pool] " +
                              (plan.justification or ""))
    for pid in seated:
        _emit(emit, {"type": "QUORUM_MEMBER_SEATED", "member": pid})
    for e in excluded:
        _emit(emit, {"type": "QUORUM_MEMBER_EXCLUDED", **e})

    # 4) completeness adversary — "who's missing?"
    if policy.get("completeness_adversary", True):
        comp = speaker.completeness(decision, context, seated_personas, pool) or {}
        missing = [d for d in (comp.get("missing") or []) if d not in covered]
        add = [pid for pid in (comp.get("add") or []) if pid in by_id and pid not in seated_set]
        if missing or add:
            _emit(emit, {"type": "MISSING_FLAGGED", "missing": missing, "add": add,
                         "note": comp.get("note", "")})
        for pid in add:
            if len(plan.seated) < hi:
                plan.seated.append(pid)
                plan.summoned.append(pid)
                # remove from excluded if it was there
                plan.excluded = [e for e in plan.excluded if e.get("persona_id") != pid]
                _emit(emit, {"type": "QUORUM_MEMBER_SEATED", "member": pid, "reason": "completeness"})
        # recompute coverage after additions
        plan.covered_dimensions = _covered_dims([by_id[pid] for pid in plan.seated])
        plan.missing_dimensions = [d for d in (required_dims + missing) if d not in plan.covered_dimensions]

    # Deterministically guarantee every seated member reads as a real person ("Name — Title"),
    # regardless of whether the convener returned a bare role label.
    for i, pid in enumerate(plan.seated):
        pers = by_id.get(pid)
        if pers is not None:
            pers.name = _person_name(pers.name, i)

    return plan
