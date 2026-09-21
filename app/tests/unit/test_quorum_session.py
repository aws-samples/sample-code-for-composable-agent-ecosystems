"""Quorum SESSION tests — the interactive, resumable run model (deterministic via FakeSpeaker).

Covers: convene stops at the empanel gate; accept begins deliberation; steps grow the
transcript and reach a convergence offer; the human can interject and be answered; the
verdict renders; and SessionState survives a JSON round-trip (each turn is a fresh Lambda).
"""
from tessera_platform.quorum.personas import from_seed_file
from tessera_platform.quorum.run import FakeSpeaker
from tessera_platform.quorum.models import GRADES
from tessera_platform.quorum import session as S

POOL = from_seed_file()
CFG = {
    "name": "defensibility-rehearsal",
    "quorum": {
        "empanelment": {"select": {"min": 3, "max": 7,
                        "diversity": ["regulatory", "conduct", "customer", "security", "accountability"]}},
        "deliberation": {"max_rounds": 2, "escalation": True, "preserve_dissent": True},
    },
}
DECISION = "Auto-approve retail loan hardship variations with an agentic AI over customer PII."


def _convene():
    return S.convene("sess-1", CFG, DECISION, "AU retail bank", list(POOL), FakeSpeaker())


def test_convene_stops_at_empanel_gate():
    st = _convene()
    assert st.phase == S.EMPANEL_GATE
    assert 3 <= len(st.plan.seated) <= 7
    assert st.plan.justification
    # every seated persona is available in the session's persona map (incl. any synthesized)
    assert all(pid in st.personas for pid in st.plan.seated)


def test_accept_then_step_grows_transcript_and_converges():
    sp = FakeSpeaker()
    st = S.convene("sess-2", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    assert st.phase == S.DELIBERATING          # the panel starts on its own — no forced question
    reason = None
    for _ in range(60):
        reason = S.step(st, sp)
        if reason == S.CONVERGENCE_OFFER:
            break
    assert reason == S.CONVERGENCE_OFFER
    assert len(st.transcript.entries) > 0
    assert st.converged


def test_drop_removes_member_at_gate():
    sp = FakeSpeaker()
    st = S.convene("sess-3", CFG, DECISION, "ctx", list(POOL), sp)
    victim = st.plan.seated[0]
    S.apply_empanel(st, {"drop": [victim]}, sp)
    assert victim not in st.plan.seated
    assert victim in {e["persona_id"] for e in st.plan.excluded}


def test_human_interjection_is_recorded_and_answered_next():
    sp = FakeSpeaker()
    st = S.convene("sess-4", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    target = st.plan.seated[0]
    S.inject_human(st, "What about the customers who can't self-advocate?", target=target)
    human_entries = [e for e in st.transcript.entries if e.member == S.HUMAN]
    assert human_entries and human_entries[-1].target == target
    assert st.pending_speaker == target        # the addressed member answers next
    S.step(st, sp)
    assert st.pending_speaker is None          # forced speaker consumed


def test_human_message_keeps_its_place_by_receipt_time():
    """The human's message must sit where they SPOKE, even if a panelist turn that was already in
    flight commits afterwards (the old bug: transcript ordered by write-completion)."""
    import time
    sp = FakeSpeaker()
    st = S.convene("sess-order", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)                                  # some panel turns land first
    spoke_at = time.time()                          # the human speaks NOW
    time.sleep(0.01)
    S.step(st, sp)                                  # a panelist turn completes AFTER they spoke
    S.inject_human(st, "MY INTERJECTION", received_at=spoke_at)   # ...but arrives late (lock wait)
    texts = [e.text for e in st.transcript.entries]
    idx = texts.index("MY INTERJECTION")
    # it must NOT be last: the later-completing panelist turn sorts after it
    assert idx < len(texts) - 1, "human message was pushed to the end instead of where they spoke"
    seqs = [e.seq for e in st.transcript.entries]
    assert seqs == sorted(seqs), "transcript is not ordered by speak/receipt time"


def test_panel_owes_the_human_a_reply_and_cannot_converge_first():
    sp = FakeSpeaker()
    st = S.convene("sess-floor", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.inject_human(st, "What must change for approval?")
    assert st.owed_reply is True                    # the floor is owed to the human
    before = len(st.transcript.entries)
    st.turns = 999                                  # even at the backstop it must ANSWER first
    S.step(st, sp)
    assert len(st.transcript.entries) > before, "panel wound down without answering the human"
    assert st.owed_reply is False                   # the next speaker answered them
    # and the answer comes from a panel member, immediately after the human's message
    assert st.transcript.entries[-1].member not in (S.HUMAN, "moderator")


def test_seq_survives_json_round_trip():
    """Each turn is a fresh Lambda that reloads state — if seq were dropped, every entry would be
    re-stamped at load time and the human's message would jump to the wrong place."""
    sp = FakeSpeaker()
    st = S.convene("sess-seq", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    S.inject_human(st, "MY LINE", received_at=1_700_000_000.5)
    before = [(e.member, e.seq) for e in st.transcript.entries]
    st2 = S.SessionState.from_dict(st.to_dict())
    after = [(e.member, e.seq) for e in st2.transcript.entries]
    assert before == after, "seq/order was not preserved across reload"
    assert any(e.seq == 1_700_000_000.5 for e in st2.transcript.entries), "human receipt time lost"


def test_near_duplicate_topics_collapse_to_one_agenda_item():
    """'pre-flight entitlement check/gate/data' are ONE issue — exact-string dedupe was why the
    panel re-raised the same concern six times."""
    sp = FakeSpeaker()
    st = S.convene("sess-topics", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    for t in ("Pre-flight entitlement check", "Pre-flight entitlement gate",
              "Pre-flight entitlement data", "Single-region RTO/RPO"):
        S._add_entry(st, 0, st.plan.seated[0], {"type": "objection", "text": "x", "topic": t}, None)
    topics = [a["topic"] for a in st.agenda]
    assert len(topics) == 2, f"near-duplicate topics were not collapsed: {topics}"


def test_answering_a_question_closes_it_and_routes_the_floor_to_the_asker():
    sp = FakeSpeaker()
    st = S.convene("sess-ask", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    asker = st.plan.seated[2]
    S._add_entry(st, 0, asker, {"type": "question", "target": S.HUMAN,
                                "text": "Does the tool return the return-window?", "topic": "tool fields"}, None)
    assert st.asked_questions and st.asked_questions[-1]["status"] == "open"
    S.inject_human(st, "yes it does")                      # a plain answer, no 'accept' keywords
    assert st.asked_questions[-1]["status"] == "answered"  # closed -> can never be re-asked
    assert st.pending_speaker == asker                     # the one who asked replies
    assert st.owed_answers == 1


def test_all_of_you_requires_multiple_answers():
    sp = FakeSpeaker()
    st = S.convene("sess-all", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.inject_human(st, "can you all tell me what needs to change?")
    assert st.owed_answers == 3
    S.step(st, sp)
    assert st.owed_answers == 2 and st.owed_reply is True   # still owed after one reply
    S.step(st, sp); S.step(st, sp)
    assert st.owed_answers == 0 and st.owed_reply is False


def test_floor_rotation_is_fair_not_seat_ordered():
    sp = FakeSpeaker()
    st = S.convene("sess-fair", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    answerers = []
    for _ in range(3):
        S.inject_human(st, "answer me please")
        S.step(st, sp)
        answerers.append(st.transcript.entries[-1].member)
    assert len(set(answerers)) > 1, f"same member answered every time: {answerers}"


def test_novelty_gate_blocks_a_repeated_remark():
    """'Lack of progress' (repetition/circular discussion) is the top cause of problem drift — a turn
    that merely repeats the room must be regenerated, and dropped if still redundant."""
    original = ("The dual-model refund check collapses if both models ground on the same poisoned RAG "
                "corpus, so consensus proves nothing about correctness of the entitlement decision.")
    near_copy = ("The dual-model refund check collapses when both models ground on the same poisoned RAG "
                 "corpus, so consensus proves nothing about correctness of that entitlement decision.")
    different = ("Seven-year immutable retention of invocation logs cannot honour an APP 13 erasure "
                 "request; you need cryptographic deletion or a documented compliance gap.")
    assert S._too_similar(near_copy, [original]) is True
    assert S._too_similar(different, [original]) is False


def test_saturated_topic_is_closed_for_debate():
    sp = FakeSpeaker()
    st = S.convene("sess-sat", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    for i in range(3):
        S._add_entry(st, 0, st.plan.seated[i], {"type": "objection", "text": f"remark {i}",
                                                "topic": "Pre-flight entitlement gate"}, None)
    view = S._agenda_view(st)
    assert "Pre-flight entitlement gate" in view["saturated"]
    assert "Pre-flight entitlement gate" not in view["raised"]


def test_chair_nudges_when_the_room_stalls():
    sp = FakeSpeaker()
    st = S.convene("sess-chair", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    st.no_progress = 2
    note = S._chair_note(st)
    assert "CHAIR" in note and ("un-aired" in note or "CLOSED" in note)


def test_lane_map_lists_every_seated_member():
    sp = FakeSpeaker()
    st = S.convene("sess-lane", CFG, DECISION, "ctx", list(POOL), sp)
    lanes = S._lane_map(st)
    assert len(lanes) == len(st.plan.seated)
    assert all(l["name"] and l["id"] for l in lanes)


def test_spoken_line_is_short_and_detail_carries_the_substance():
    """A person can't recite ten lines in a meeting: what's HEARD is short; the substance rides along
    in `detail` (read by colleagues, expandable by the human) and is never shown inline."""
    sp = FakeSpeaker()
    st = S.convene("sess-two", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    spoken = [e for e in st.transcript.entries if e.member not in (S.HUMAN, "moderator")]
    assert spoken, "no panel turn recorded"
    e = spoken[0]
    assert e.detail, "the turn carried no detail record"
    assert e.text and e.text != e.detail, "spoken line must differ from the detail"


def test_detail_survives_the_session_round_trip():
    sp = FakeSpeaker()
    st = S.convene("sess-two-rt", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    st2 = S.SessionState.from_dict(st.to_dict())
    before = [(e.text, e.detail) for e in st.transcript.entries]
    after = [(e.text, e.detail) for e in st2.transcript.entries]
    assert before == after, "detail was lost across reload"


def test_colleagues_see_the_detail_in_their_view():
    sp = FakeSpeaker()
    st = S.convene("sess-two-view", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    view = st.transcript.view()
    assert any(v.get("detail") for v in view), "panel view has no substance to engage with"
    assert all(len(v.get("detail") or "") <= 400 for v in view), "detail not truncated in the view"


def test_verdict_renders_with_valid_grade():
    sp = FakeSpeaker()
    st = S.convene("sess-5", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    for _ in range(60):
        if S.step(st, sp) == S.CONVERGENCE_OFFER:
            break
    v = S.render_verdict(st, sp)
    assert v.grade in GRADES
    assert st.phase == S.DONE


def test_state_survives_json_round_trip():
    sp = FakeSpeaker()
    st = S.convene("sess-6", CFG, DECISION, "ctx", list(POOL), sp)
    S.apply_empanel(st, {"accept": True}, sp)
    S.step(st, sp)
    d = st.to_dict()
    st2 = S.SessionState.from_dict(d)
    assert st2.plan.seated == st.plan.seated
    assert len(st2.transcript.entries) == len(st.transcript.entries)
    assert set(st2.personas) == set(st.personas)
    # resuming from the restored state keeps working
    assert S.step(st2, sp) in (None, S.AWAIT_HUMAN, S.CONVERGENCE_OFFER)
