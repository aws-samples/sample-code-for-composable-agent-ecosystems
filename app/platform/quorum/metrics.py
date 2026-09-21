"""Quorum RUN METRICS — the outcome half of a comparable run (quorum/index.html §12.3).

`_run_config.json` records what the room was told to do. This records what it actually did, using the
same measures the regression harness uses in tests, so a live run and a test run can be read on the
same axes. Without this, "it repeats again" and "it feels slower" stay arguments from memory.

Everything here is derived from state that already exists — no extra model calls, no new bookkeeping
in the hot path.
"""
from __future__ import annotations

import collections
from typing import Any


def _subject_keys(st, topic: str) -> set:
    from .session import _subject_key
    return _subject_key(topic or "")


def collect(st, config_hash: str = "") -> dict:
    """Measure a finished (or in-flight) session. Safe on a partial session."""
    entries = list(getattr(getattr(st, "transcript", None), "entries", []) or [])
    members = [e for e in entries if getattr(e, "member", "") != "human"]
    human = [e for e in entries if getattr(e, "member", "") == "human"]
    seated = list(getattr(getattr(st, "plan", None), "seated", []) or [])

    # --- participation ------------------------------------------------------------------
    by_member = collections.Counter(getattr(e, "member", "") for e in members)
    turns = len(members) or 1
    dominance = (by_member.most_common(1)[0][1] / turns) if by_member else 0.0
    silent = [p for p in seated if by_member.get(p, 0) == 0]

    # --- the agenda: labels vs actual subjects ------------------------------------------
    agenda = list(getattr(st, "agenda", []) or [])
    # "topics" counts LABELS; "distinct_subjects" counts what they are actually about, because each
    # member names its own topic and one subject can wear several labels. A label whose subject key is
    # empty (an acronym-only or stopword-only label) counts as itself rather than merging with every
    # other empty one — which is what made two unrelated topics read as one subject.
    subjects: set = set()
    for a in agenda:
        key = frozenset(_subject_keys(st, a.get("topic", "")))
        subjects.add(key if key else ("label:" + str(a.get("topic", "")).lower()))
    settled = [a for a in agenda if a.get("status") in ("accepted", "answered")]
    max_remarks = max([int(a.get("remarks", 1)) for a in agenda] or [0])

    # --- reaching the human -------------------------------------------------------------
    asked = list(getattr(st, "asked_questions", []) or [])
    answered = [q for q in asked if q.get("status") == "answered"]
    directed = [e for e in members if getattr(e, "target", "") == "human"]
    questions = [e for e in members if getattr(e, "type", "") == "question"]

    # --- the board ----------------------------------------------------------------------
    wb = getattr(st, "whiteboard", None)
    ops = list(getattr(wb, "ops", []) or []) if wb else []
    if not ops and isinstance(wb, dict):            # a session dict rather than a live object
        ops = list(wb.get("ops") or [])
    # Field names matter: reading "by"/"op"/"genre" instead of the real "author"/"kind" reported a board
    # with 17 ops as having no authors, no annotations and no genres — three silent zeros that looked
    # like the board doing nothing rather than the metric being wrong.
    def _op(o, *keys, default=""):
        """Ops are WhiteboardOp OBJECTS in memory and dicts only after to_dict(). Guarding on
        isinstance(o, dict) reported a live board of 24 ops as having no authors, no kinds and no
        genres — a metric that reads one representation is a metric that lies about the other."""
        for k in keys:
            v = o.get(k) if isinstance(o, dict) else getattr(o, k, None)
            if v:
                return v
        return default

    board = {
        "ops": len(ops),
        "authors": len({_op(o, "author", "by") for o in ops if _op(o, "author", "by")}),
        # An annotation is not its own op KIND (there are only box/arrow/stroke/text). It is a mark
        # that REFERS to something already on the board, so the honest signal is refs pointing at
        # another op — counting kind.startswith("annotate") could never match anything.
        "annotations": sum(1 for o in ops
                           if any(str(r).startswith("wb-") for r in (_op(o, "refs", default=[]) or []))),
        "kinds": dict(collections.Counter(str(_op(o, "kind", "op", default="?")) for o in ops)),
        "genres": sorted({str(_op(o, "genre")) for o in ops if _op(o, "genre")}),
        "refusals": int(getattr(st, "_draw_refusals", 0) or 0),
    }

    # --- cost of the quality gates (the latency dial) -----------------------------------
    cost = {
        "model_calls": int(getattr(st, "_model_calls", 0) or 0),
        "gate_retries": int(getattr(st, "_gate_retries", 0) or 0),
        "sketch_calls": int(getattr(st, "_sketch_calls_total", 0) or 0),
        "critique_calls": int(getattr(st, "_critique_calls", 0) or 0),
        # A yielded turn produces NO transcript entry, so counting entries of type "pass" could only
        # ever report zero. gates.mode: prevent measured 0 retries and 0 "forced passes" while the log
        # showed six faults - the room was not complying, it was going quiet, and the metric hid it.
        "gate_faults": int(getattr(st, "_gate_faults", 0) or 0),
        "yields": int(getattr(st, "_yields", 0) or 0),
        # A chaired agenda snaps a new label onto the item on the floor; without this the comparison
        # showed 3 labels vs 5 and no explanation for the difference.
        "relabelled_onto_floor": int(getattr(st, "_relabelled", 0) or 0),
        "draw_refusals": int(getattr(st, "_draw_refusals", 0) or 0),
    }
    if turns:
        cost["retries_per_turn"] = round(cost["gate_retries"] / turns, 2)
        cost["model_calls_per_turn"] = round(cost["model_calls"] / turns, 2)

    # --- TIME, in the record rather than only in CloudWatch ------------------------------
    def _pct(vals, q):
        if not vals:
            return 0.0
        v = sorted(vals)
        return round(v[min(len(v) - 1, int(len(v) * q))], 1)

    attempts = [(float(a[0]), bool(a[1])) for a in (getattr(st, "_attempts", []) or [])
                if isinstance(a, (list, tuple)) and len(a) >= 2]
    productive = [d for d, ok in attempts if ok]
    wasted = [d for d, ok in attempts if not ok]
    stamps = [float(getattr(e, "seq", 0) or 0) for e in entries if float(getattr(e, "seq", 0) or 0) > 1e9]
    timing = {
        "attempts": len(attempts),
        "turn_p50_s": _pct(productive, 0.5),
        "turn_p90_s": _pct(productive, 0.9),
        "turn_max_s": round(max(productive), 1) if productive else 0.0,
        # The cost of saying nothing. A yield has already paid for the allocator AND the turn.
        "wasted_s": round(sum(wasted), 1),
        "wasted_share": round(sum(wasted) / sum(d for d, _ in attempts), 2) if attempts else 0.0,
        "wall_clock_s": round(max(stamps) - min(stamps), 1) if len(stamps) > 1 else 0.0,
        "s_per_spoken_turn": round((max(stamps) - min(stamps)) / len(members), 1)
                             if (len(stamps) > 1 and members) else 0.0,
    }

    # --- QUALITY: did the room surface what it had to? ------------------------------------
    # Matched on SUBJECT KEYS, the same reduction the agenda and the gates use, so "single region
    # failover" and "the single-region database" count as the same issue. A substring match would
    # reward paraphrase; requiring an exact label would punish it.
    from .session import _subject_key
    # An issue is either a phrase (every stem must appear) or {"issue": ..., "any_of": [...]} where any
    # listed phrasing counts. The first rubric scored a room that plainly discussed "single region",
    # "shared Postgres", "RTO" and "audit" as having missed all four, because the rubric said
    # "single region FAILOVER", "shared database COUPLING", "recovery time OBJECTIVE", "audit TRAIL".
    # The measure was wrong, not the room — so the synonyms live in the rubric, where they can be
    # audited, and the matcher stays strict.
    raw_expected = (((getattr(st, "policy", {}) or {}).get("settings") or {}).get("evaluation") or {}) \
        .get("expected_issues", []) or []
    expected = []
    for x in raw_expected:
        if isinstance(x, dict):
            name = str(x.get("issue") or "").strip()
            alts = [str(a) for a in (x.get("any_of") or []) if str(a).strip()] or ([name] if name else [])
        else:
            name, alts = str(x).strip(), [str(x).strip()]
        if name:
            expected.append((name, alts))
    said = " ".join(f"{getattr(e, 'text', '')} {getattr(e, 'detail', '')}" for e in entries)
    # Crude stem: compare 5-char prefixes, so "shared database" matches "twelve services SHARE one
    # DATABASE". Exact key equality scored a plain hit as a miss; a substring search would have scored
    # any mention of "database" as covering "shared database coupling".
    def _stems(text: str) -> set:
        return {k[:5] for k in _subject_key(text)}

    said_keys = _stems(said)
    covered, missed = [], []
    for name, alts in expected:
        hit = any(_stems(a) and _stems(a) <= said_keys for a in alts)
        (covered if hit else missed).append(name)
    quality = {
        "expected_issues": len(expected),
        "covered": len(covered),
        "coverage": round(len(covered) / len(expected), 2) if expected else None,
        "missed": missed,
        # Substance per turn: distinct agenda subjects raised per spoken turn, so a room that says more
        # about fewer things scores lower than one that opens new ground.
        "subjects_per_turn": round(len(subjects) / len(members), 2) if members else 0.0,
    }

    # --- instrumentation the experiment programme needs (quorum/index.html §16) -----------
    # E2 asks whether the gates buy anything, which cannot be answered without measuring the thing
    # they exist to prevent. Same predicate the novelty gate uses, applied pairwise after the fact.
    from .session import _too_similar
    texts = [f"{getattr(e, 'text', '')} {getattr(e, 'detail', '')}".strip() for e in members]
    dup_pairs = sum(1 for i in range(len(texts)) for j in range(i + 1, len(texts))
                    if _too_similar(texts[j], [texts[i]]))
    # E4 (model diversity) and E5 (agreeableness) both turn on the MIX of what members say — whether a
    # room objects, rebuts, concedes or dissents — not on how much it says.
    types = collections.Counter(str(getattr(e, "type", "")) for e in members)
    n_mem = len(members) or 1
    # E7: the room opens agenda items for its own PROCEDURE ("Conditional approval", "Decision
    # convergence"), not only for subjects under review, and each one consumes remarks budget. Counted
    # by overlap with the decision's own vocabulary: a label sharing no subject key with the decision
    # is procedural or drifting.
    decision_keys = _subject_keys(st, f"{getattr(st, 'decision', '')} {getattr(st, 'context', '')}")
    off_subject = [str(a.get("topic") or "") for a in agenda
                   if not (_subject_keys(st, a.get("topic", "")) & decision_keys)]
    discourse = {
        "near_duplicate_pairs": dup_pairs,
        "types": dict(types),
        "objection_share": round((types.get("objection", 0) + types.get("dissent", 0)) / n_mem, 2),
        "concession_share": round(types.get("concession", 0) / n_mem, 2),
        "question_share": round(types.get("question", 0) / n_mem, 2),
        # E7 stage 1 is deliberately MANUAL. "Conditional approval" and "Decision convergence" are
        # procedural, but they share vocabulary with the decision text ("approval", "decision"), so a
        # keyword overlap test scored 1 of 4 — worse than useless. The labels are emitted verbatim so
        # the classification can be done by inspection instead of by a proxy nobody should trust.
        "labels": [str(a.get("topic") or "") for a in agenda],
        "labels_sharing_no_subject_with_decision": len(off_subject),
    }
    # E6: does the board's self-review earn the slowest call in the system? The auditor inspects a
    # RENDERED spec, so its findings cannot be recomputed from the op log afterwards — they are counted
    # at draw time instead (session._board_problems).
    board["audit_problems"] = int(getattr(st, "_board_problems", 0) or 0)
    board["critique_kept"] = int(getattr(st, "_critique_kept", 0) or 0)

    verdict = getattr(st, "verdict", None)

    return {
        "config_hash": config_hash,
        "phase": getattr(st, "phase", ""),
        "rounds": int(getattr(st, "round", 0) or 0),
        "participation": {
            "member_turns": len(members),
            "human_turns": len(human),
            "seats": len(seated),
            "dominance": round(dominance, 2),          # share held by the loudest member
            "silent_members": len(silent),
            "per_member": dict(by_member),
        },
        "agenda": {
            "topics": len(agenda),
            "distinct_subjects": len(subjects),        # labels can multiply while subjects do not
            "settled": len(settled),
            "max_remarks_on_one_topic": max_remarks,
            "open_at_end": len(agenda) - len(settled),
        },
        "human_reach": {
            "questions_asked": len(questions),
            "questions_recorded": len(asked),
            "questions_answered": len(answered),
            "turns_directed_at_human": len(directed),
            "floor_grants": int(getattr(st, "_floor_grants", 0) or 0),
        },
        "board": board,
        "timing": timing,
        "quality": quality,
        "discourse": discourse,
        "cost": cost,
        "verdict": {
            # Verdict carries `grade`, `indefensible_elements` and `dissent_preserved` — not
            # level/blockers/dissents, which read as empty on a verdict that was actually rendered.
            "grade": getattr(verdict, "grade", "") if verdict else "",
            "indefensible_elements": len(getattr(verdict, "indefensible_elements", []) or []) if verdict else 0,
            "remediation": len(getattr(verdict, "remediation", []) or []) if verdict else 0,
            "dissent_preserved": bool(getattr(verdict, "dissent_preserved", "")) if verdict else False,
        },
    }
