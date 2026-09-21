"""Regression GATE for the Quorum deliberation room (Fix 0 of the agreed plan).

Two jobs:

1. RATCHET THE PROMPT BUDGET. Instruction-following degrades non-linearly as rules are
   added (arXiv 2607.19257 / 2608.02639), which is why each prompt-level "fix" to this room
   previously broke something else. The ceilings below FAIL if the member-turn prompt grows,
   so new behaviour has to be implemented in code or in a separate focused call.

2. RECORD THE DIAGNOSED DEFECTS AS EXECUTABLE TARGETS. Each defect is a strict-xfail test
   describing the behaviour we want. strict=True means an unexpected PASS fails the suite —
   so when a fix lands, the ratchet forces us to come back and promote the test to a real
   assertion instead of leaving a stale "known bug" marker.

Baselines measured 2026-08-14 against the scripted room in tests/harness/quorum_harness.py.
"""
import pytest

from tessera_platform.quorum import session as S
from tests.harness.quorum_harness import (ScriptedSpeaker, metrics, prompt_metrics, run_room)


@pytest.fixture(autouse=True)
def _board_on(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")


@pytest.fixture
def bad():
    """The room as observed in real sessions (members address the submitter via objections)."""
    st, ev, pauses = run_room()
    return metrics(st, ev, pauses)


# ---- 1. prompt budget ratchet ---------------------------------------------------------
# Ceilings, not targets: they may only ever be LOWERED (deliberately, in a commit that says
# so). If a change needs more prompt, it is the wrong change — put it in code or its own call.
MAX_SYSTEM_CHARS = 4900      # measured 4832 (was 5710 before Fix 4 moved geometry out)
MAX_IMPERATIVES = 7          # measured 6 (MUST/NEVER/ALWAYS/DO NOT/CRITICAL/ONLY/HARD MAX)


def test_member_turn_prompt_stays_within_budget():
    p = prompt_metrics()
    assert p["system_chars"] <= MAX_SYSTEM_CHARS, (
        f"member-turn system prompt grew to {p['system_chars']} chars (ceiling "
        f"{MAX_SYSTEM_CHARS}). Adding rules costs compliance elsewhere — implement this in "
        f"code or a separate call instead.")
    assert p["imperatives"] <= MAX_IMPERATIVES, (
        f"{p['imperatives']} hard imperatives in the member-turn prompt (ceiling "
        f"{MAX_IMPERATIVES}); each one competes with the output-format constraint.")


def test_turn_token_budget_is_documented():
    # The whole turn (spoken + detail + meta JSON) shares this budget. Draw geometry does not
    # fit in it, which is why sketching must become its own call (Fix 4).
    assert prompt_metrics()["max_tokens"] == 520


# ---- 2. the mechanism behind "I can never get in" ------------------------------------
def test_a_question_to_the_human_does_pause_the_room():
    """Control: the SAME turns, tagged `question` instead of `objection`, always pause.
    This documents the mechanism the defect below exploits; it must keep passing."""
    st, ev, pauses = run_room(ScriptedSpeaker(address_human_as="question", dominator=None))
    m = metrics(st, ev, pauses)
    assert m["human_targeted"] > 0
    assert m["pauses_for_human"] > 0
    assert m["floor_reachability"] == 1.0
    assert S.AWAIT_HUMAN in pauses


def test_scenario_is_representative(bad):
    """Sanity: the scripted room really does address the submitter repeatedly."""
    # NOTE: this count fell from 12 to ~3 once Fix 3 landed — the scripted speaker keeps pushing
    # ONE topic, and the room now makes it yield instead of restating. Fewer turns is the fix
    # working, so the assertion checks the SHAPE (demands aimed at the submitter, never questions).
    assert bad["human_targeted"] >= 3
    assert bad["human_questions"] == 0          # exactly as observed: demands, not questions


# ---- 3. diagnosed defects, as executable targets --------------------------------------
@pytest.fixture
def panel_only():
    """A room where members talk only among themselves — isolates speaker-selection and topic
    behaviour from human interleaving (so the Fix 3 measurements stay meaningful after Fix 1)."""
    st, ev, pauses = run_room(ScriptedSpeaker(address_human=False))
    return metrics(st, ev, pauses)


@pytest.fixture
def panel_fair():
    """Panel-only room where every turn opens a genuinely NEW topic — isolates speaker-selection
    fairness from the anti-repetition path (which would otherwise end the room in 3 turns)."""
    st, ev, pauses = run_room(ScriptedSpeaker(address_human=False, repeat_same_demand=False))
    return metrics(st, ev, pauses)


# ---- 3. FIXED: the floor is server-authoritative (Fix 1) ------------------------------
def test_fix1_turns_aimed_at_the_submitter_reach_them(bad):
    """The submitter must be reachable. Before Fix 1 this was 0.0 — twelve turns addressed the human
    and the room never once stopped.

    It is deliberately NOT 1.0 for demands: members address the submitter on nearly every turn, and
    pausing for each one turns the meeting into ping-pong. So a demand reaches them within a couple of
    turns, while a real QUESTION always stops the room immediately (asserted below)."""
    assert bad["human_targeted"] > 0
    assert bad["floor_reachability"] > 0.0, (
        f"{bad['human_targeted']} turns were addressed to the submitter and the room never paused")


def test_a_question_to_the_submitter_is_never_rate_limited():
    """The rate limit applies to demands in passing, never to a direct question."""
    st, ev, pauses = run_room(ScriptedSpeaker(address_human_as="question", dominator=None))
    m = metrics(st, ev, pauses)
    assert m["human_questions"] == m["human_targeted"] > 0
    assert m["floor_reachability"] == 1.0


def test_fix1_panel_cannot_start_a_turn_while_the_human_holds_the_floor():
    """Server-authoritative: once the floor is requested, `step` refuses to run a new turn —
    no client timer involved, and it survives reload/second tab because it lives in state."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False)
    st = S.convene("floor", CFG, DECISION, CONTEXT, pool(), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    before = len(st.transcript.entries)

    S.request_floor(st)                       # the human raises a hand
    for _ in range(5):
        assert S.step(st, sp) == S.AWAIT_HUMAN
    assert len(st.transcript.entries) == before, "the panel spoke while the human held the floor"
    assert st.floor == S.FLOOR_HUMAN

    S.inject_human(st, "here is my answer", None, "claim")   # speaking releases the floor
    assert st.floor == S.FLOOR_PANEL
    assert S.step(st, sp) != S.AWAIT_HUMAN                   # the panel may resume
    assert len(st.transcript.entries) > before + 1


def test_fix1_floor_survives_a_json_round_trip():
    """Each turn is a fresh Lambda; the floor must persist with the session."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False)
    st = S.convene("floor2", CFG, DECISION, CONTEXT, pool(), sp)
    S.request_floor(st)
    revived = S.SessionState.from_dict(st.to_dict())
    # the floor is granted immediately now — an intermediate "requested" state deadlocked the room
    assert revived.floor == S.FLOOR_HUMAN


def test_fix2_prompt_forbids_inventing_a_submitter_name():
    """Real runs addressed a phantom ("Marc, ...") because nothing forbade it and no identity is
    passed (we only have a Cognito sub, not a name). Asserted on the prompt because a scripted
    speaker cannot demonstrate prompt compliance."""
    sysp = prompt_metrics()["system"]
    assert "NEVER invent a name" in sysp
    assert "THE SUBMITTER" in sysp


def test_fix2_a_turn_addressed_to_the_submitter_in_WORDS_reaches_them():
    """`target` is model-reported and was wrong 13 times out of 13 in a real run (it pointed at
    colleagues' entry ids while the prose said "the submitter"). Detection must be code-side."""
    st, ev, pauses = run_room(ScriptedSpeaker(address_in_prose=True, dominator=None))
    m = metrics(st, ev, pauses)
    assert m["prose_directed"] > 0, "scenario should address the submitter in prose"
    assert m["human_targeted"] == 0, "and should NOT tag target=human (that's the point)"
    assert m["prose_reachability"] > 0.0, (
        f"{m['prose_directed']} turns spoke to the submitter but the room paused "
        f"{m['pauses_for_human']} times")


def test_fix2_interactive_room_has_no_second_design_owner():
    """The human IS the design owner. A proponent seat duplicates them and, measured in a real
    run, took 36% of all turns while the panel discussed the submitter in the third person."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool

    class WithProponent(ScriptedSpeaker):
        def empanel(self, decision, context, p, policy):
            out = super().empanel(decision, context, p, policy)
            return out

    people = pool()
    people[3].role = "proponent"          # seat a design owner
    sp = ScriptedSpeaker(dominator=None)
    st = S.convene("noowner", CFG, DECISION, CONTEXT, people, sp)
    seated_roles = [st.personas[pid].role for pid in st.plan.seated if pid in st.personas]
    assert "proponent" not in seated_roles, f"an in-room design owner survived: {seated_roles}"
    assert len(st.plan.seated) >= 4


def test_fix3_a_topic_is_not_hammered(panel_only):
    """A saturated / settled / personally-capped topic is refused in CODE: the turn is regenerated
    once and the member yields rather than restating. Before Fix 3 one topic absorbed 12 remarks
    in the harness and 16 of 22 real turns restated a single agreed fix."""
    assert panel_only["max_topic_remarks"] <= 3, (
        f"one agenda topic absorbed {panel_only['max_topic_remarks']} remarks")


def test_fix3_no_single_member_dominates(panel_fair):
    """A member already ahead of the least-heard member waits their turn. Measured before the
    guard: one voice took 36% of a real meeting (64% for two); harness showed 0.50-0.67."""
    assert panel_fair["dominance"] <= 0.4
    assert panel_fair["member_turns"] >= 8, "need enough turns for the ratio to mean anything"


def test_fix4_a_promised_sketch_is_actually_drawn(bad):
    """Saying "let me sketch the data flow" IS the intent — detected in code, then drawn by the
    dedicated sketch() call. Previously the member narrated it and the board stayed empty."""
    assert bad["unfulfilled_sketch_promises"] == 0


def test_fix4_board_is_not_empty_when_drawing_personas_are_seated(bad):
    """The chair maps the open agenda at each round boundary, so the board reflects the discussion
    even when no expert volunteers a diagram (measured before: 0 ops across a whole review)."""
    assert bad["board_ops"] >= 1


# ---- board QUALITY: the failures a screenshot would show -------------------------------
def test_board_has_no_overlapping_boxes(panel_fair):
    """Model-chosen coordinates produced 62 overlapping box pairs on a real board — every sketch
    starts in the same corner because each call sees a blank canvas. The server now owns layout and
    stacks each sketch below the last, so collisions are structurally impossible."""
    assert panel_fair["overlapping_box_pairs"] == 0


def test_board_has_no_dangling_arrows(panel_fair):
    """A real sketch had 0 of 4 arrows landing on a box. Arrows are now bound to rendered
    rectangles, so an arrow always connects two real nodes."""
    assert panel_fair["dangling_arrows"] == 0


def test_board_does_not_repeat_labels(panel_fair):
    """'Review Scope & Process' was drawn three times, twice within one sketch."""
    assert panel_fair["duplicate_box_labels"] == 0


def test_every_sketch_states_its_point():
    """A drawing that cannot say what it demonstrates is not drawn: the renderer refuses a spec with
    no `point` (that is what turned sketches into lists of nouns)."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    b = Whiteboard()
    assert render_sketch({"type": "flow", "nodes": [{"label": "a"}, {"label": "b"}]}, b) == []
    assert render_sketch({"point": "x", "type": "flow", "nodes": [{"label": "a"}]}, b) == []
    ops = render_sketch({"point": "PII crosses here", "type": "flow",
                         "nodes": [{"label": "note"}, {"label": "prompt"}]}, b)
    assert ops and ops[0]["kind"] == "text" and "PII crosses here" in ops[0]["text"]


# ---- Fix 7: relabelling and re-asking (defects D1, D2) ---------------------------------
def test_fix7_relabelled_topics_collapse_to_one_subject():
    """A real session recorded SIX topics for TWO subjects — 'RTO/RPO definition', 'RTO cost
    justification' … — because the tokeniser dropped words of ≤3 chars and so discarded the very
    acronyms (RTO, RPO, PII) that identify the subject. Each wording parked just under the cap."""
    from tessera_platform.quorum.session import _subject_key
    rto = ["RTO/RPO and regional resilience", "RTO/RPO definition", "RTO cost justification"]
    pii = ["PII in Bedrock logs", "PII erasure & jurisdiction", "PII tokenisation accepted"]
    for group in (rto, pii):
        base = _subject_key(group[0])
        for other in group[1:]:
            assert base & _subject_key(other), f"{group[0]!r} vs {other!r} should be one subject"
    assert not (_subject_key(rto[0]) & _subject_key(pii[0])), "different subjects must not merge"


def test_fix7_an_answered_question_cannot_be_reasked():
    from tessera_platform.quorum.session import _repeats_answered_question
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False)
    st = S.convene("reask", CFG, DECISION, CONTEXT, pool(), sp)
    st.asked_questions = [{"member": "sre", "status": "answered",
                           "text": "Does the submitter have a documented RTO and RPO?"}]
    reworded = {"type": "question", "topic": "RTO cost justification",
                "text": "I still need the actual RTO downtime number."}
    assert _repeats_answered_question(st, reworded, "risk") is True
    fresh = {"type": "question", "topic": "ledger reconciliation",
             "text": "Who reconciles approvals against the payment ledger?"}
    assert _repeats_answered_question(st, fresh, "risk") is False


# ---- Fix 8/9: board hygiene and marker vocabulary (D4, D5, D6) -------------------------
def test_fix8_labels_never_break_mid_word(panel_fair):
    from tessera_platform.quorum.whiteboard import fit_label
    assert panel_fair["midword_labels"] == 0
    out = fit_label("Tom's right that 99.9% is an availability target not recovery")
    assert "avail" not in out.split("\n")[-1] or out.endswith("…")
    for line in out.split("\n"):
        assert len(line) <= 21


def test_fix8_the_same_diagram_is_not_visible_twice(panel_fair):
    """The chair redrew an identical decision map every round ('Where the decision stands right now'
    ×3), so the operator watched the same drawing re-animate. A superseded map is erased, and a
    redraw is only allowed once the decision has actually moved."""
    assert panel_fair["repeated_sketch_points"] == 0


def test_fix8_chair_does_not_redraw_an_unchanged_map():
    """Replacement alone was not enough — the map has to be WORTH redrawing."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False, repeat_same_demand=False)
    st = S.convene("map", CFG, DECISION, CONTEXT, pool(), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    seated_n = len(st.plan.seated)
    for _ in range(seated_n * 2):            # run far enough for at least one map
        if S.step(st, sp):
            break
    drawn = len(st.whiteboard.live_ops())
    sig = st.wb_map_sig
    st.wb_last_map_round = -1                # allow another attempt with UNCHANGED state
    S._chair_maps_the_board(st, sp, seated_n)
    assert st.wb_map_sig == sig
    assert len(st.whiteboard.live_ops()) == drawn, "an identical map was drawn again"


def test_fix9_board_is_more_than_boxes_and_lines():
    """Measured board was '10 boxes, 8 arrows, 3 text' — a flowchart. Emphasis (a circled punchline)
    and semantic colour make it read like a whiteboard."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    ops = render_sketch({"point": "One disk full stops every write", "type": "failure",
                         "nodes": [{"label": "Shared Postgres", "kind": "store"},
                                   {"label": "Disk full", "kind": "risk"},
                                   {"label": "All 12 services stall", "kind": "impact"}]},
                        Whiteboard())
    kinds = {o["kind"] for o in ops}
    assert "stroke" in kinds, "no hand-drawn emphasis on the punchline"
    assert any(o.get("color") for o in ops), "no semantic colour"


class _St:
    """Minimal stand-in so board metrics can be measured on a hand-built board."""
    def __init__(self, board):
        self.whiteboard = board
        self.agenda = []
        self.turns = 0
        class _T:
            entries = []
        self.transcript = _T()


# ---- Observed 2026-08-17 (session 98b29405): agreement pile-up and a silent board ------
def test_a_settled_topic_cannot_absorb_more_agreement():
    """Measured: 'refund deterministic gate' reached SEVEN remarks against a cap of three, because
    concessions were exempt from saturation — five different members agreed with a control the room
    had already accepted. That is the excessive-agreement failure wearing a new costume."""
    from tessera_platform.quorum.session import _restates_closed_topic
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False)
    st = S.convene("settled", CFG, DECISION, CONTEXT, pool(), sp)
    st.agenda = [{"topic": "refund deterministic gate", "status": "accepted", "remarks": 4,
                  "by_member": {}}]
    agree = {"type": "concession", "topic": "refund deterministic gate",
             "text": "Agreed, the deterministic gate solves it."}
    assert _restates_closed_topic(st, agree, "sre") is True, "agreement piled onto a settled topic"
    # a concession that CLOSES a live topic is still progress
    st.agenda = [{"topic": "regional failover", "status": "open", "remarks": 1, "by_member": {}}]
    closing = {"type": "concession", "topic": "regional failover", "text": "Fair — that covers it."}
    assert _restates_closed_topic(st, closing, "sre") is False


def test_a_worked_topic_gets_a_picture_without_being_asked():
    """Measured: the live board was 10 ops, ALL authored by the chair, with zero member sketches in
    17 turns — Fix 4 replaced a hard drawing rule with an optional field and members never set it.
    A topic the room keeps working should get drawn by the most visual member, deterministically."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    # `repeat_same_demand` keeps ONE topic in play so it reaches saturation, which is now the bar for
    # a triggered sketch — drawing on every twice-mentioned topic made the board out-talk the room.
    sp = ScriptedSpeaker(address_human=False, repeat_same_demand=True, never_sketch=True,
                         dominator=None)
    st = S.convene("needpic", CFG, DECISION, CONTEXT, pool(), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    for _ in range(len(st.plan.seated) * 3):
        if S.step(st, sp):
            break
    authors = {o.author for o in st.whiteboard.live_ops()}
    assert authors, "nothing was drawn even though a topic was worked to saturation"
    chair = next((p for p in st.plan.seated
                  if getattr(st.personas.get(p), "role", "") == "facilitator"), None)
    assert chair not in authors, f"the chair drew it despite others being able to: {authors}"


def test_midword_metric_uses_the_box_capacity():
    """'data residency & privacy regime' is a complete 31-char phrase wrapped on word boundaries; a
    fixed 30-char threshold flagged it as broken text. Capacity comes from the box width."""
    from tessera_platform.quorum.whiteboard import Whiteboard, fit_label
    b = Whiteboard()
    b.add_op("chair", "box", {"x": 0, "y": 0, "w": 300, "h": 62,
                              "label": fit_label("data residency & privacy regime", per_line=31)})
    m = metrics(_St(b), [], [])
    assert m["midword_labels"] == 0


def test_a_paraphrased_point_is_not_drawn_twice():
    """String matching missed paraphrases: one live board carried both "Shared Postgres failure
    cascades to all 12 services simultaneously" and "Postgres down kills all 12 services, no refunds
    possible" — the same diagram twice, on a board that had grown to 148 ops."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    b = Whiteboard()
    first = render_sketch({"point": "Shared Postgres failure cascades to all 12 services simultaneously",
                           "type": "failure",
                           "nodes": [{"label": "Shared Postgres"}, {"label": "12 services"}]}, b)
    assert first
    for op in first:
        b.add_op("architect", op["kind"], {k: v for k, v in op.items() if k != "kind"})
    paraphrase = render_sketch({"point": "Postgres down kills all 12 services, no refunds possible",
                                "type": "failure",
                                "nodes": [{"label": "Postgres"}, {"label": "refunds"}]}, b)
    assert paraphrase == [], "the same claim was drawn a second time in new words"
    distinct = render_sketch({"point": "PII collected for refunds flows into analytics without consent",
                              "type": "flow",
                              "nodes": [{"label": "refund form"}, {"label": "analytics"}]}, b)
    assert distinct, "a genuinely different claim must still be drawable"


# ---- diagram aesthetics: layered layout (move 1) ---------------------------------------
def _aes(spec):
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    from tests.harness.quorum_harness import diagram_aesthetics
    return diagram_aesthetics(render_sketch(spec, Whiteboard()))


def test_common_shapes_are_drawn_without_crossings():
    """"Reducing the number of edge crosses is by far the most important aesthetic" (Purchase et al.),
    and row/column packing produced crossings as soon as a graph had a fan-out or a skip edge."""
    fan = {"point": "One database backs every service", "type": "failure",
           "nodes": [{"label": "Shared Postgres"}, {"label": "Payments"}, {"label": "Refunds"},
                     {"label": "Auth"}, {"label": "Orders"}],
           "edges": [{"from": 0, "to": 1}, {"from": 0, "to": 2}, {"from": 0, "to": 3},
                     {"from": 0, "to": 4}]}
    diamond = {"point": "Both paths write the ledger", "type": "flow",
               "nodes": [{"label": "Request"}, {"label": "Auto path"}, {"label": "Manual path"},
                         {"label": "Ledger"}],
               "edges": [{"from": 0, "to": 1}, {"from": 0, "to": 2}, {"from": 1, "to": 3},
                         {"from": 2, "to": 3}]}
    chain = {"point": "PII reaches the log", "type": "flow",
             "nodes": [{"label": "Order note"}, {"label": "Prompt"}, {"label": "Bedrock log"}]}
    for name, spec in (("fan-out", fan), ("diamond", diamond), ("chain", chain)):
        a = _aes(spec)
        assert a["crossings"] == 0, f"{name} drew {a['crossings']} crossings"
        assert a["grid_aligned"] == 1.0, f"{name} is not aligned to the grid"


def test_a_cyclic_graph_stays_readable():
    """A back edge plus a skip edge cannot always be drawn planar; hold the line at one crossing and
    keep every node on the grid so the drawing still reads."""
    spec = {"point": "Retry path re-enters the gate and duplicates the refund", "type": "flow",
            "nodes": [{"label": "Customer"}, {"label": "Agent"}, {"label": "Policy gate"},
                      {"label": "Refund API"}, {"label": "Ledger"}, {"label": "Retry queue"}],
            "edges": [{"from": 0, "to": 1}, {"from": 1, "to": 2}, {"from": 2, "to": 3},
                      {"from": 3, "to": 4}, {"from": 3, "to": 5}, {"from": 5, "to": 2},
                      {"from": 1, "to": 4}]}
    a = _aes(spec)
    assert a["crossings"] <= 1
    assert a["grid_aligned"] == 1.0


def test_routed_edges_carry_their_endpoints():
    """Endpoints travel with the op so crossing counts can ignore edges that share a node (a bus),
    and so the client can bind an arrow to real boxes."""
    a = _aes({"point": "x", "type": "flow",
              "nodes": [{"label": "a"}, {"label": "b"}], "edges": [{"from": 0, "to": 1}]})
    assert a["arrows"] == 1


# ---- move 2: the room looks at its own drawing -----------------------------------------
def test_audit_finds_the_things_a_reader_would_notice():
    from tessera_platform.quorum.whiteboard import Whiteboard, audit_ops, render_sketch
    crowded = {"point": "Everything about refunds", "type": "flow",
               "nodes": [{"label": f"thing {i}"} for i in range(9)]}
    a = audit_ops(render_sketch(crowded, Whiteboard()))
    assert any("too many" in p for p in a["problems"]), a["problems"]
    clean = {"point": "PII reaches the log", "type": "flow",
             "nodes": [{"label": "Order note"}, {"label": "Prompt"}, {"label": "Bedrock log"}]}
    assert audit_ops(render_sketch(clean, Whiteboard()))["problems"] == []


# Board self-review is OFF by default since E6 (critique_kept was 0 in every run ever recorded, and
# disabling it cut turn p90 by 3.6s). The mechanism still works when switched on, which is what this
# locks — so the capability cannot rot while it is dormant.
def test_a_flagged_drawing_is_revised_and_a_worse_revision_is_refused(monkeypatch):
    """The member is shown what it drew and may replace it once — but the revision only survives if
    it audits better, so a critique can never make the board worse."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    from tessera_platform.quorum import session as SS

    crowded = {"point": "Refund flow end to end", "type": "flow",
               "nodes": [{"label": f"step {i}"} for i in range(9)]}
    tidy = {"point": "Refund flow end to end", "type": "flow",
            "nodes": [{"label": "Request"}, {"label": "Gate"}, {"label": "Ledger"}]}

    class Reviewer(ScriptedSpeaker):
        def __init__(self, revision):
            super().__init__(address_human=False)
            self.revision = revision
            self.saw_audit = None
        def sketch(self, persona, purpose, decision, board_digest="", agenda=None):
            return crowded
        def critique_sketch(self, persona, spec, audit, description, png=None):
            self.saw_audit = audit
            return self.revision

    sp = Reviewer(tidy)
    st = SS.convene("crit", CFG, DECISION, CONTEXT, pool(), sp,
                    # Board self-review is OFF by default since E6 — critique_kept was 0 in every run
                    # ever recorded and disabling it cut turn p90 by 3.6s. Switch it ON here so the
                    # mechanism cannot rot while dormant, and so the guarantee it provides stays locked.
                    settings_overrides={"whiteboard": {"critique": {"when": "on_problems"}}})
    # pick a member whose repertoire actually contains `flow` — a chair draws decisions and matrices,
    # and asking it for a flow is now refused rather than silently redrawn
    drawer = next(p for p in st.plan.seated
                  if "flow" in (st.personas[p].draw_repertoire or []))
    ops = SS._sketch_ops(st, drawer, "show the refund flow", sp)
    boxes = [o for o in ops if o["kind"] == "box"]
    assert sp.saw_audit and any("too many" in p for p in sp.saw_audit["problems"])
    assert len(boxes) == 3, "the tidier revision should have been kept"

    sp2 = Reviewer(crowded)          # "revision" is just as bad -> keep the original
    st2 = SS.convene("crit2", CFG, DECISION, CONTEXT, pool(), sp2)
    drawer2 = next(p for p in st2.plan.seated
                   if "flow" in (st2.personas[p].draw_repertoire or []))
    ops2 = SS._sketch_ops(st2, drawer2, "show the refund flow", sp2)
    assert len([o for o in ops2 if o["kind"] == "box"]) == 7   # capped, unchanged


def test_the_board_can_be_rendered_for_the_model_to_see():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_png, render_sketch
    ops = render_sketch({"point": "PII reaches the log", "type": "flow",
                         "nodes": [{"label": "Order note"}, {"label": "Bedrock log"}]}, Whiteboard())
    png = render_png(ops)
    if png is None:
        pytest.skip("imaging library unavailable — the loop degrades to the geometry audit")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 200


def test_a_member_with_nothing_new_does_not_stall_the_room():
    """Measured deadlock: the fairness guard picked the least-heard member, that member restated a
    closed topic, was refused and yielded — and because yielding did not count as being heard it
    stayed least-heard and was picked again, six times in ninety seconds. An attempt must count, and
    a member who has just yielded steps aside."""
    from tests.harness.quorum_harness import CFG, CONTEXT, DECISION, pool
    sp = ScriptedSpeaker(address_human=False, repeat_same_demand=False, stuck_member="privacy",
                         never_sketch=True)
    events = []
    st = S.convene("stuck", CFG, DECISION, CONTEXT, pool(), sp, events.append)
    S.apply_empanel(st, {"accept": True}, sp, events.append)
    for _ in range(16):
        if S.step(st, sp, events.append):
            break
    passes = [e for e in events if e.get("type") == "MEMBER_PASSED"]
    stuck_passes = [e for e in passes if e.get("member") == "privacy"]
    assert len(stuck_passes) <= 3, f"the stuck member burned {len(stuck_passes)} turns"
    # the meeting still progressed: several DIFFERENT members put something on the record
    speakers = {e.member for e in st.transcript.entries if e.member != S.HUMAN}
    assert len(speakers) >= 3, f"the room stalled; only {speakers} spoke"


# ---- W1: the board became addressable --------------------------------------------------
def _board_with_a_flow():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    b = Whiteboard()
    for o in render_sketch({"point": "Refund path crosses three services", "type": "flow",
                            "nodes": [{"label": "Agent"}, {"label": "Policy gate"},
                                      {"label": "Refund API"}]}, b):
        b.add_op("architect", o["kind"], {k: v for k, v in o.items() if k != "kind"})
    return b


def test_the_board_is_addressable():
    """Members used to receive the board only as prose, so they could not point at anything on it and
    every sketch had to be a brand-new diagram."""
    inv = _board_with_a_flow().inventory()
    assert [e["id"] for e in inv][:2] == ["wb-1", "wb-2"]
    assert any(e["label"] == "Agent" for e in inv)
    assert all(e["author"] == "architect" for e in inv)


def test_a_lasso_encloses_the_whole_group_it_targets():
    """A person circles a CLUSTER; the old emphasis drew a fixed ellipse on one box's outline."""
    from tessera_platform.quorum.whiteboard import render_annotations
    b = _board_with_a_flow()
    boxes = [e["id"] for e in b.inventory() if e["kind"] == "box"][:2]
    r1, r2 = b.rect_of(boxes[0]), b.rect_of(boxes[1])
    ops = render_annotations([{"targets": boxes, "mark": "circle", "text": "unowned handoff"}], b)
    stroke = next(o for o in ops if o["kind"] == "stroke")
    xs = [p[0] for p in stroke["points"]]
    assert min(xs) < r1[0] and max(xs) > r2[0] + r2[2], "the loop does not enclose both targets"
    assert any(o["kind"] == "text" and "unowned" in o["text"] for o in ops)


def test_an_annotation_alone_is_a_complete_contribution():
    """Marking up what is already there needs no fresh diagram wrapped around it."""
    from tessera_platform.quorum.whiteboard import render_sketch
    b = _board_with_a_flow()
    tgt = [e["id"] for e in b.inventory() if e["kind"] == "box"][0]
    ops = render_sketch({"annotations": [{"targets": [tgt], "mark": "strike"},
                                         {"targets": [tgt], "mark": "note", "text": "needs an owner"}]}, b)
    kinds = [o["kind"] for o in ops]
    assert kinds.count("stroke") == 2 and "text" in kinds       # strike + leader line + note text


def test_annotating_someone_elses_mark_needs_no_consent():
    """Erasing another author's mark requires approval; ADDING one does not — a person circling a
    colleague's box is ordinary. So an annotation never enters the pending-erase queue."""
    from tessera_platform.quorum.whiteboard import render_annotations
    b = _board_with_a_flow()
    tgt = [e["id"] for e in b.inventory() if e["kind"] == "box"][0]
    ops = render_annotations([{"targets": [tgt], "mark": "underline"}], b)
    assert ops and b.pending_erase == [] and b.erased == []


# ---- W3/W4/W5: a vocabulary beyond boxes and lines -------------------------------------
def test_the_new_genres_render_cleanly():
    """"Boxes and lines" was our alphabet, not the model's limit: there were five genres and all five
    were rectangles-and-arrows. A bowtie says something a flowchart cannot — where the controls sit."""
    from tessera_platform.quorum.whiteboard import Whiteboard, audit_ops, render_sketch
    bowtie = {"point": "Controls sit either side of the failure", "type": "bowtie",
              "nodes": [{"label": "Prompt injection"}, {"label": "Retry storm"},
                        {"label": "Wrong refund"}, {"label": "Customer loss"},
                        {"label": "Regulatory breach"}]}
    lanes = {"point": "Nobody owns the reversal", "type": "swimlane",
             "nodes": [{"label": "Raise refund", "lane": "Customer"},
                       {"label": "Assess", "lane": "Agent"}, {"label": "Reverse", "lane": "Ops"}]}
    when = {"point": "The gate lands after go-live", "type": "timeline",
            "nodes": [{"label": "Pilot"}, {"label": "Go-live"}, {"label": "Audit gate"}]}
    for name, spec in (("bowtie", bowtie), ("swimlane", lanes), ("timeline", when)):
        ops = render_sketch(spec, Whiteboard())
        a = audit_ops(ops, spec["type"])
        assert ops, f"{name} drew nothing"
        assert a["crossings"] == 0, f"{name} has {a['crossings']} crossings"
        assert a["problems"] == [], f"{name}: {a['problems']}"
    # a bowtie carries barriers (strokes) as well as boxes and arrows
    kinds = {o["kind"] for o in render_sketch(bowtie, Whiteboard())}
    assert {"box", "arrow", "stroke", "text"} <= kinds


def test_importance_and_edge_meaning_are_visible():
    """W3/W5: every node used to be the same size and every arrow the same line, so importance and
    'optional' were inexpressible."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    ops = render_sketch({"point": "Injection reaches the money", "type": "flow",
                         "nodes": [{"label": "Order note"},
                                   {"label": "Prompt injection", "kind": "risk"},
                                   {"label": "Refund issued", "kind": "impact"}],
                         "edges": [{"from": 0, "to": 1, "label": "optional"},
                                   {"from": 1, "to": 2}]}, Whiteboard())
    boxes = [o for o in ops if o["kind"] == "box"]
    assert boxes[1]["w"] > boxes[0]["w"], "a risk node should carry more visual weight"
    arrows = [o for o in ops if o["kind"] == "arrow"]
    assert arrows[0].get("style") == "dashed", "'optional' should read as a dashed line"
    assert any(a.get("width", 0) >= 3 for a in arrows), "a route into impact should be heavier"


# ---- visual style is a persona skill, and there is no fallback --------------------------
def test_repertoire_comes_from_the_discipline():
    """Visual style is a skill, not a global menu: a safety engineer reaches for a bowtie, an SRE for a
    failure chain or a timeline, a chair for a decision map. Offering all eight shapes to everyone is
    why the model kept picking the default one."""
    from tessera_platform.quorum.models import Persona
    from tessera_platform.quorum.whiteboard import repertoire_for
    safety = Persona.from_record({"persona_id": "s", "name": "Dana — Safety Engineer",
                                  "stance": "hazard and risk controls", "dimensions": ["safety"]})
    sre = Persona.from_record({"persona_id": "o", "name": "Tom — Principal SRE",
                               "stance": "reliability and incident response",
                               "dimensions": ["operational-resilience"]})
    chair = Persona.from_record({"persona_id": "c", "name": "Sarah — ARB Chair",
                                 "stance": "runs the review", "role": "facilitator",
                                 "dimensions": ["governance"]})
    assert "bowtie" in repertoire_for(safety)
    assert "timeline" in repertoire_for(sre) and "bowtie" not in repertoire_for(sre)
    assert "decision" in repertoire_for(chair)


def test_a_shape_outside_the_repertoire_is_refused_not_substituted():
    """No fallback: a fallback would hide the fact that nobody chose how to make the point."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    spec = {"point": "Injection reaches the money", "type": "flow",
            "nodes": [{"label": "Order note"}, {"label": "Refund"}]}
    assert render_sketch(spec, Whiteboard(), ["decision", "matrix"]) == []   # refused
    assert render_sketch(spec, Whiteboard(), ["flow", "boundary"])           # drawn


def test_a_sketch_with_no_shape_chosen_is_not_drawn():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    assert render_sketch({"point": "something", "nodes": [{"label": "a"}, {"label": "b"}]},
                         Whiteboard(), ["flow"]) == []


def test_annotations_need_no_shape():
    """Marking up what is already on the board is not a diagram, so the shape rule does not apply."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    b = _board_with_a_flow()
    tgt = [e["id"] for e in b.inventory() if e["kind"] == "box"][0]
    ops = render_sketch({"annotations": [{"targets": [tgt], "mark": "underline"}]}, b, ["decision"])
    assert ops, "an annotation was refused for lacking a shape it does not need"


def test_the_keyword_genre_guesser_is_gone():
    """It hid the failure: the room could look like it was choosing shapes when nobody was."""
    import tessera_platform.quorum.whiteboard as W
    assert not hasattr(W, "infer_genre") and not hasattr(W, "_GENRE_HINTS")
