"""Run settings (quorum/index.html §12).

The point of these tests is not that a dict merges. It is that the settings are (a) behaviour-neutral
by default, so introducing them changed nothing, (b) actually wired — an override must change what
the room does, otherwise the panel is decoration, and (c) fail closed, because a silently-defaulted
bad setting would make a recorded run a lie.
"""
import pytest

from tessera_platform.quorum import settings as S
from tessera_platform.quorum import session as SS
from tessera_platform.quorum.engine import quorum_policy


# --- (a) defaults reproduce today's behaviour -------------------------------------------
def test_defaults_are_valid_and_change_nothing():
    r = S.resolve()
    assert S.validate(r) == []
    assert S.changed_from_defaults(r) == {}


@pytest.mark.parametrize("path,constant", [
    ("gates.saturation.remarks", SS.SATURATED_REMARKS),
    ("gates.saturation.member_cap", SS.MEMBER_TOPIC_CAP),
    ("whiteboard.sketch_calls_per_turn", SS._SKETCH_CALLS_PER_TURN),
])
def test_defaults_match_the_constants_they_replaced(path, constant):
    """If these drift apart, the code and the recorded config disagree about the same run."""
    cur = S.DEFAULTS
    for part in path.split("."):
        cur = cur[part]
    assert cur == constant


def test_policy_carries_settings_without_dropping_the_legacy_keys():
    pol = quorum_policy({"quorum": {"deliberation": {"max_rounds": 2},
                                    "whiteboard": {"enabled": True}}})
    assert pol["max_rounds"] == 2 and pol["whiteboard"]["enabled"] is True   # legacy readers
    assert pol["settings"]["limits"]["max_rounds"] == 2                      # new home
    assert pol["settings"]["whiteboard"]["enabled"] is True


def test_eco_shape_survives_the_move_to_settings():
    """The eco YAML on disk predates settings; its values must still land."""
    eco = {"quorum": {"deliberation": {"max_rounds": 3, "turn_policy": "moderated"},
                      "whiteboard": {"enabled": True, "infer_affinity": True}}}
    r = S.resolve(eco)
    assert r["limits"]["max_rounds"] == 3
    assert r["room"]["turn_allocation"] == "moderated"
    assert r["whiteboard"]["enabled"] is True


# --- (b) the knobs are wired ------------------------------------------------------------
def _state(overrides):
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.policy = quorum_policy({}, overrides)
    return st


def test_setting_lookup_falls_back_to_defaults_for_older_sessions():
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.policy = {}                              # a session persisted before settings existed
    assert SS._setting(st, "gates.saturation.remarks") == SS.SATURATED_REMARKS


def test_raising_the_saturation_cap_lets_a_topic_be_worked_longer():
    """The gate that rejects turns — and so costs a whole extra model call — is configurable."""
    topic = "single region failover"
    tight = _state({"gates": {"saturation": {"remarks": 2}}})
    loose = _state({"gates": {"saturation": {"remarks": 6}}})
    for st in (tight, loose):
        st.agenda = [{"topic": topic, "by": "a", "status": "open", "remarks": 3,
                      "by_member": {"m1": 1}}]
    raw = {"type": "objection", "topic": topic, "text": "still not convinced"}
    assert SS._restates_closed_topic(tight, raw, "m2") is True     # 3 >= 2, closed
    assert SS._restates_closed_topic(loose, raw, "m2") is False    # 3 < 6, still open


def test_raising_the_member_cap_lets_one_member_keep_a_topic():
    topic = "pii in prompts"
    st = _state({"gates": {"saturation": {"member_cap": 4}}})
    st.agenda = [{"topic": topic, "by": "m1", "status": "open", "remarks": 1,
                  "by_member": {"m1": 3}}]
    raw = {"type": "objection", "topic": topic, "text": "one more thing on this"}
    assert SS._restates_closed_topic(st, raw, "m1") is False
    tight = _state({"gates": {"saturation": {"member_cap": 2}}})
    tight.agenda = [dict(a) for a in st.agenda]
    assert SS._restates_closed_topic(tight, raw, "m1") is True


def test_answered_question_gate_can_be_switched_off():
    st = _state({"gates": {"answered_questions": {"enforce": False}}})
    st.asked_questions = [{"member": "m1", "text": "what is the RTO?", "status": "answered",
                           "topic": "rto"}]
    raw = {"type": "question", "topic": "rto", "text": "what is the RTO?"}
    assert SS._repeats_answered_question(st, raw, "m1") is False
    on = _state({"gates": {"answered_questions": {"enforce": True}}})
    on.asked_questions = list(st.asked_questions)
    assert SS._repeats_answered_question(on, raw, "m1") is True


def test_board_budget_of_zero_stops_the_room_drawing_at_all():
    st = _state({"whiteboard": {"sketch_calls_per_turn": 0}})
    st._sketch_calls = 0
    assert SS._sketch_ops(st, "m1", "show it", speaker=None) == []


# --- (c) fail closed --------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"gates": {"mode": "yolo"}},
    {"gates": {"saturation": {"remarks": 999}}},
    {"gates": {"novelty": {"threshold": 4.2}}},
    {"floor": {"pause_on": "whenever"}},
    {"floor": {"grant": "eventually"}},
    {"room": {"chair_mode": "dictator"}},
    {"personas": {"agreeableness": "grumpy"}},
    {"whiteboard": {"critique": {"when": "maybe"}}},
    {"whiteboard": {"triggers": {"on_worked_topic": "sometimes"}}},
    {"whiteboard": {"enabled": "yes"}},
    {"nonsense": {"x": 1}},
    {"gates": {"nonsense": 1}},
])
def test_bad_settings_are_refused_not_defaulted(bad):
    assert S.validate(S.resolve(None, bad)), f"{bad} should have been refused"


@pytest.mark.parametrize("ok", [
    {"gates": {"mode": "prevent", "max_retries_per_turn": 0}},
    {"room": {"chair_mode": "moderator", "agenda_ownership": "chaired"}},
    {"personas": {"agreeableness": "contrarian", "overrides": {"x": {"agreeableness": "balanced"}}}},
    {"whiteboard": {"triggers": {"on_worked_topic": "remarks:2"}}},
    {"models": {"per_seat": {"security": "some.model.id"}}},
    {"floor": {"pause_on": "question_only", "chair_offers_floor_every": 4}},
])
def test_legitimate_settings_are_accepted(ok):
    assert S.validate(S.resolve(None, ok)) == []


# --- comparability ----------------------------------------------------------------------
def test_the_record_is_enough_to_compare_two_runs():
    a = S.record(S.resolve(None, {"room": {"chair_mode": "facilitator"}}), {}, "v1")
    b = S.record(S.resolve(None, {"room": {"chair_mode": "moderator"}}), {}, "v1")
    assert a["config_hash"] != b["config_hash"]
    assert b["changed_from_defaults"] == {"room.chair_mode": "moderator"}
    # the RESOLVED settings are stored, not just the diff — defaults will move over time
    assert b["settings"]["gates"]["saturation"]["remarks"] == S.DEFAULTS["gates"]["saturation"]["remarks"]


def test_fingerprint_is_stable_across_key_order():
    one = S.resolve(None, {"gates": {"saturation": {"remarks": 5, "member_cap": 3}}})
    two = S.resolve(None, {"gates": {"saturation": {"member_cap": 3, "remarks": 5}}})
    assert S.fingerprint(one) == S.fingerprint(two)


# --- the room models (step 4) -----------------------------------------------------------
def test_emergent_agenda_puts_nothing_on_the_floor():
    """Today's room: nobody owns the agenda, so there is no 'current item' to hand anyone."""
    st = _state({})
    st.agenda = [{"topic": "single region", "by": "m1", "status": "open", "remarks": 1, "by_member": {}}]
    assert SS._current_item(st) == ""
    assert SS._agenda_view(st)["chair_owns"] is False


def test_a_chaired_agenda_puts_one_item_on_the_floor():
    st = _state({"room": {"agenda_ownership": "chaired"}})
    st.agenda = [
        {"topic": "single region", "by": "m1", "status": "open", "remarks": 2, "by_member": {}},
        {"topic": "pii in prompts", "by": "m2", "status": "open", "remarks": 1, "by_member": {}},
    ]
    view = SS._agenda_view(st)
    assert view["chair_owns"] is True
    # the item with the most capacity left, so a subject is finished before another is started
    assert view["on_the_floor"] == "pii in prompts"


def test_a_chaired_agenda_snaps_a_new_label_onto_the_live_item():
    """The label-collision problem: each member invents its own topic name, so ONE subject becomes
    several agenda items and members are then punished for saturating something they never joined."""
    st = _state({"room": {"agenda_ownership": "chaired"}})
    st.agenda = [{"topic": "rto definition", "by": "m1", "status": "open", "remarks": 1,
                  "by_member": {"m1": 1}}]
    SS._add_entry(st, 1, "m2", {"type": "objection", "topic": "rto cost justification",
                                "text": "the cost case is unproven"}, None)
    assert len(st.agenda) == 1, "a relabelling opened a second agenda item"
    assert st.agenda[0]["remarks"] == 2
    assert getattr(st, "_relabelled", 0) == 1


def test_an_emergent_agenda_still_multiplies_labels():
    """The contrast that makes the setting worth having — this is today's behaviour, unchanged."""
    st = _state({})
    st.agenda = [{"topic": "rto definition", "by": "m1", "status": "open", "remarks": 1,
                  "by_member": {"m1": 1}}]
    SS._add_entry(st, 1, "m2", {"type": "objection", "topic": "vendor exit terms",
                                "text": "no exit clause"}, None)
    assert len(st.agenda) == 2


def test_a_moderator_parks_exhausted_items_but_a_facilitator_does_not():
    exhausted = {"topic": "single region", "by": "m1", "status": "open", "remarks": 3, "by_member": {}}
    mod = _state({"room": {"chair_mode": "moderator"}})
    mod.agenda = [dict(exhausted)]
    assert SS._chair_rules_agenda(mod) == ["single region"]
    assert mod.agenda[0]["status"] == "parked"

    fac = _state({})                                  # today: nobody closes it
    fac.agenda = [dict(exhausted)]
    assert SS._chair_rules_agenda(fac) == []
    assert fac.agenda[0]["status"] == "open"


def test_the_saturation_setting_reaches_the_agenda_view_too():
    """These were two independent hardcoded 3s — the view and the gate could disagree about the same
    topic, telling a member a topic was live and then rejecting the turn for restating it."""
    st = _state({"gates": {"saturation": {"remarks": 2}}})
    st.agenda = [{"topic": "single region", "by": "m1", "status": "open", "remarks": 2, "by_member": {}}]
    view = SS._agenda_view(st)
    assert view["saturated"] == ["single region"] and view["raised"] == []
    raw = {"type": "objection", "topic": "single region", "text": "again"}
    assert SS._restates_closed_topic(st, raw, "m2") is True    # view and gate now agree


# --- per-seat models + agreeableness (step 5) -------------------------------------------
def test_per_seat_model_routing_picks_the_seat_model_then_falls_back():
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker
    sp = BedrockSpeaker.__new__(BedrockSpeaker)        # no boto3 client needed
    sp.model_id = "base.model"
    sp._per_seat = {"security_sme": "other.model"}
    sp._seat = ""
    assert sp._model == "base.model"
    sp.seat("security_sme")
    assert sp._model == "other.model"
    sp.seat("privacy_counsel")                        # no override for this seat
    assert sp._model == "base.model"


def test_agreeableness_stance_text_exists_for_the_non_neutral_settings():
    from tessera_platform.quorum import bedrock_speaker as BS
    assert "balanced" not in BS._STANCE, "balanced must inject no prompt text"
    assert "concede" in BS._STANCE["contrarian"]
    assert "WORKS" in BS._STANCE["accommodating"]


# --- the guard that was missing ---------------------------------------------------------
def test_every_shipped_ecosystem_validates():
    """Fail-closed validation refused to launch the real ecosystem, because DEFAULTS omitted the
    `verdict.scale` / `must_state` that its YAML has carried all along (and that §12.2 lists as
    settings). Fail-closed is right; being wrong about the schema is not. This is the test that turns
    that from a launch-time refusal into a unit failure."""
    import glob
    import os
    import yaml
    _ecos_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "content", "ecosystems"))
    ecos = glob.glob(os.path.join(_ecos_dir, "**", "*.yaml"), recursive=True)
    assert ecos, "no ecosystem YAML found — the guard would pass vacuously"
    checked = 0
    for path in ecos:
        try:
            cfg = yaml.safe_load(open(path)) or {}
        except Exception:
            continue
        if not isinstance(cfg, dict) or "quorum" not in cfg:
            continue
        checked += 1
        assert S.validate(S.resolve(cfg, {})) == [], f"{path} would be refused at launch"
    assert checked, "no quorum ecosystem exercised this guard"


# --- metrics must survive the invocation boundary ---------------------------------------
def test_cost_counters_survive_the_save_load_cycle():
    """One `advance` is one lambda invocation that reloads the session from S3. Counters kept only in
    memory reported the last turn alone — the first live metrics run showed no retries for a room that
    had retried plenty."""
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._gate_retries, st._model_calls, st._relabelled, st._floor_grants = 4, 17, 2, 3
    back = SS.SessionState.from_dict(st.to_dict())
    assert (back._gate_retries, back._model_calls, back._relabelled, back._floor_grants) == (4, 17, 2, 3)


def test_metrics_measure_a_session_without_extra_model_calls():
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.agenda = [{"topic": "pii", "by": "m1", "status": "open", "remarks": 3, "by_member": {"m1": 2}},
                 {"topic": "rto", "by": "m2", "status": "accepted", "remarks": 1, "by_member": {}}]
    st._gate_retries, st._model_calls = 3, 20
    m = QM.collect(st, "hash1")
    assert m["config_hash"] == "hash1"
    assert m["agenda"] == {"topics": 2, "distinct_subjects": 2, "settled": 1,
                           "max_remarks_on_one_topic": 3, "open_at_end": 1}
    assert m["cost"]["gate_retries"] == 3 and m["cost"]["model_calls"] == 20


# --- who sits where (models.chair / models.members / balance) ---------------------------
def _room(overrides, seats=("chair1", "m1", "m2", "m3")):
    from tessera_platform.quorum.models import Persona, QuorumPlan
    st = _state(overrides)
    st.personas = {s: Persona(persona_id=s, name=s, stance="", dimensions=[],
                              role=("facilitator" if s == "chair1" else "challenger")) for s in seats}
    st.plan = QuorumPlan(seated=list(seats), excluded=[])
    return st


def test_by_default_every_seat_shares_the_default_model():
    """One model in every seat, and since E4b that model is Sonnet 5 — measured to reach coverage 1.0.
    Uniformity is no longer suspected of causing agreement: vendor diversity was refuted at both tiers."""
    m = SS._assign_seat_models(_room({}), "env.model")
    assert set(m.values()) == {"us.anthropic.claude-sonnet-5"}
    m2 = SS._assign_seat_models(_room({"models": {"default": ""}}), "env.model")
    assert set(m2.values()) == {"env.model"}, "an empty default must fall back to the runtime model"


def test_the_chair_can_be_pinned_while_members_stay_on_the_default():
    st = _room({"models": {"chair": "chair.model", "default": "base.model"}})
    m = SS._assign_seat_models(st, "env.model")
    assert m[SS.CHAIR_SEAT] == "chair.model" and m["chair1"] == "chair.model"
    assert m["m1"] == m["m2"] == m["m3"] == "base.model"


def test_balance_deals_member_seats_across_the_pool_and_leaves_the_chair_pinned():
    st = _room({"models": {"chair": "chair.model", "members": "balance",
                           "balance_pool": ["a.model", "b.model"]}})
    m = SS._assign_seat_models(st, "base.model")
    assert m[SS.CHAIR_SEAT] == "chair.model", "the judge and turn allocator must stay pinned"
    assert [m["m1"], m["m2"], m["m3"]] == ["a.model", "b.model", "a.model"]
    assert len({m["m1"], m["m2"]}) == 2, "a balanced room must actually differ between seats"


def test_balance_is_deterministic_so_a_recorded_run_can_be_reproduced():
    o = {"models": {"members": "balance", "balance_pool": ["a.model", "b.model", "c.model"]}}
    first = SS._assign_seat_models(_room(o), "base.model")
    second = SS._assign_seat_models(_room(o), "base.model")
    assert first == second


def test_picking_one_model_for_all_members_keeps_the_chair_separate():
    st = _room({"models": {"chair": "chair.model", "members": "one.model"}})
    m = SS._assign_seat_models(st, "base.model")
    assert m["chair1"] == "chair.model"
    assert m["m1"] == m["m2"] == m["m3"] == "one.model"


def test_an_explicit_per_seat_override_beats_the_balance_deal():
    st = _room({"models": {"members": "balance", "balance_pool": ["a.model", "b.model"],
                           "per_seat": {"m2": "special.model"}}})
    m = SS._assign_seat_models(st, "base.model")
    assert m["m2"] == "special.model"


def test_seat_models_survive_the_invocation_boundary():
    st = _room({"models": {"members": "balance", "balance_pool": ["a.model", "b.model"]}})
    st.seat_models = SS._assign_seat_models(st, "base.model")
    back = SS.SessionState.from_dict(st.to_dict())
    assert back.seat_models == st.seat_models, "a reloaded session would re-deal the room"


def test_balance_without_a_pool_is_refused_at_launch():
    """Otherwise the room convenes and dies on turn one."""
    assert S.validate(S.resolve(None, {"models": {"members": "balance", "balance_pool": []}}))


# --- a configured model must be invokable BEFORE the room convenes ----------------------
def test_configured_models_lists_everything_a_run_could_call():
    r = S.resolve(None, {"models": {"chair": "chair.model", "members": "balance",
                                    "balance_pool": ["a.model", "b.model"],
                                    "per_seat": {"m9": "special.model"}}})
    got = S.configured_models(r)
    assert set(got) >= {"chair.model", "a.model", "b.model", "special.model"}


def test_an_unusable_model_is_refused_with_the_providers_own_reason():
    """Measured twice on a live room: a model that lists as ACTIVE but refuses Converse as 'Legacy',
    and one that rejects the temperature field we always send. Both convened a full panel and then
    killed it on the third turn. No substitution — refuse, and say why."""
    class Boom(Exception):
        response = {"Error": {"Message": "This Model is marked by provider as Legacy"}}

    def probe(mid):
        if "dead" in mid:
            raise Boom()

    errs = S.models_usable(["good.model", "dead.model"], probe)
    assert len(errs) == 1
    assert "dead.model" in errs[0] and "Legacy" in errs[0]


def test_a_working_pool_raises_no_problems():
    assert S.models_usable(["a", "b"], lambda mid: None) == []


def test_the_default_pool_contains_only_probed_models():
    """nova-premier and gpt-5.6 were in the first version of this pool; both failed a real Converse."""
    pool = S.DEFAULTS["models"]["balance_pool"]
    assert pool, "a balance pool of nothing cannot deal a room"
    assert not any("nova-premier" in m or "gpt-5.6" in m for m in pool)


def test_board_and_verdict_metrics_read_the_REAL_field_names():
    """A live board with 17 ops reported no authors, no annotations and no genres, and a rendered
    verdict reported an empty level — because the metric read "by"/"op"/"level" while the data carries
    "author"/"kind"/"grade". Silent zeros look like the room doing nothing."""
    from tessera_platform.quorum import metrics as QM
    from tessera_platform.quorum.models import Verdict
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.whiteboard.ops = [
        {"op_id": "wb-1", "author": "m1", "kind": "text", "data": {}, "refs": ["e0"]},
        {"op_id": "wb-2", "author": "m2", "kind": "stroke", "data": {}, "refs": ["wb-1"]},
        {"op_id": "wb-3", "author": "m1", "kind": "box", "genre": "bowtie", "data": {}, "refs": []},
    ]
    st.verdict = Verdict(grade="defensible_with_conditions", indefensible_elements=["a"],
                         remediation=["r1", "r2"], dissent_preserved="one dissent held")
    m = QM.collect(st, "h")
    assert m["board"]["authors"] == 2, m["board"]
    assert m["board"]["annotations"] == 1
    assert m["board"]["genres"] == ["bowtie"]
    assert m["board"]["kinds"] == {"text": 1, "stroke": 1, "box": 1}
    assert m["verdict"]["grade"] == "defensible_with_conditions"
    assert m["verdict"]["remediation"] == 2 and m["verdict"]["dissent_preserved"] is True


def test_board_metrics_read_whiteboard_OBJECTS_not_just_dicts():
    """Ops are WhiteboardOp objects in memory and dicts only after to_dict(). The first live A/B
    reported a 24-op board with zero authors because the metric only understood dicts."""
    from tessera_platform.quorum import metrics as QM
    from tessera_platform.quorum.whiteboard import Whiteboard
    st = SS.SessionState(session_id="t", decision="d", context="c")
    wb = Whiteboard()
    first = wb.add_op("m1", "text", {"x": 0, "y": 0, "text": "a"}, refs=["e0"])
    # an annotation is a mark REFERRING to an existing element — there is no "annotate" op kind
    wb.add_op("m2", "stroke", {"points": [[0, 0], [1, 1]]}, refs=[first.op_id])
    st.whiteboard = wb
    board = QM.collect(st, "h")["board"]
    assert board["ops"] == 2
    assert board["authors"] == 2, board
    assert board["annotations"] == 1, board


def test_relabelling_is_reported_so_a_chaired_agenda_can_be_explained():
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._relabelled = 3
    assert QM.collect(st, "h")["cost"]["relabelled_onto_floor"] == 3


# --- gates.mode: prevent must actually PREVENT ------------------------------------------
def test_allowed_topics_excludes_what_the_gates_would_reject():
    """The list handed to the member is computed from the same predicates that would reject the turn,
    so a compliant turn cannot then be rejected for the thing it was told to do."""
    st = _state({"gates": {"saturation": {"remarks": 3, "member_cap": 2}}})
    st.agenda = [
        {"topic": "live one", "by": "m1", "status": "open", "remarks": 1, "by_member": {"m1": 1}},
        {"topic": "saturated", "by": "m1", "status": "open", "remarks": 3, "by_member": {}},
        {"topic": "settled", "by": "m1", "status": "accepted", "remarks": 1, "by_member": {}},
        {"topic": "mine already", "by": "m2", "status": "open", "remarks": 2, "by_member": {"m2": 2}},
        {"topic": "parked", "by": "m1", "status": "parked", "remarks": 3, "by_member": {}},
    ]
    assert SS._allowed_topics(st, "m2") == ["live one"]
    # and what it offers is exactly what would survive the gate
    raw = {"type": "objection", "topic": "live one", "text": "x"}
    assert SS._restates_closed_topic(st, raw, "m2") is False
    for banned in ("saturated", "settled", "mine already", "parked"):
        assert banned not in SS._allowed_topics(st, "m2")


def test_prevent_mode_states_what_is_open_instead_of_three_prohibitions():
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker
    from tessera_platform.quorum.models import Persona
    sp = BedrockSpeaker.__new__(BedrockSpeaker)
    p = Persona(persona_id="m1", name="M", stance="s", dimensions=["privacy"])
    captured = {}

    def fake_ask(system, user, max_tokens=520):
        captured["system"] = system
        return 'A point.\n===META===\n{"type":"claim","topic":"t","text":"x"}'
    sp._ask = fake_ask
    sp._per_seat, sp._seat, sp.model_id = {}, "", "m"

    sp.speak(p, "d", "c", [], 1, gates_mode="prevent",
             allowed_topics=["single region", "pii in prompts"],
             agenda={"raised": ["single region"], "saturated": ["rto"], "settled_by_human": []})
    sys_prevent = captured["system"]
    assert "you may speak to ONE of these OPEN items" in sys_prevent
    assert "single region, pii in prompts" in sys_prevent
    assert "EXHAUSTED, closed for debate" not in sys_prevent, "prevent must not also ship the bans"

    sp.speak(p, "d", "c", [], 1,
             agenda={"raised": ["single region"], "saturated": ["rto"], "settled_by_human": []})
    assert "EXHAUSTED, closed for debate" in captured["system"], "default mode keeps today's bans"


def test_prevent_costs_no_retries():
    st = _state({"gates": {"mode": "prevent", "max_retries_per_turn": 3}})
    assert SS._setting(st, "gates.mode") == "prevent"


def test_yields_are_counted_because_they_leave_no_transcript_entry():
    """gates.mode: prevent reported 0 retries AND 0 forced passes while the log showed six faults. A
    turn that yields adds nothing to the transcript, so counting entries of type 'pass' could only ever
    report zero — the room going quiet looked identical to the room complying."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._gate_faults, st._yields, st._gate_retries = 6, 4, 0
    cost = QM.collect(st, "h")["cost"]
    assert cost["gate_faults"] == 6 and cost["yields"] == 4 and cost["gate_retries"] == 0


def test_fault_and_yield_counters_survive_the_invocation_boundary():
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._gate_faults, st._yields = 6, 4
    back = SS.SessionState.from_dict(st.to_dict())
    assert (back._gate_faults, back._yields) == (6, 4)


# --- the baseline: time and quality in the record ----------------------------------------
def test_attempt_timing_makes_wasted_seconds_visible():
    """Entry timestamps only show turns that HAPPENED; the cost of the ones that produced nothing hides
    in the gaps between them (a measured 11-entry run had a 58.6s gap). Attempts are timed where they
    occur, so a yield's cost is in seconds, not just in calls."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._attempts = [[10.0, True], [12.0, True], [8.0, False], [20.0, True], [10.0, False]]
    SS._add_entry(st, 1, "m1", {"type": "claim", "topic": "t", "text": "x"}, None)
    t = QM.collect(st, "h")["timing"]
    assert t["attempts"] == 5
    assert t["turn_p50_s"] == 12.0 and t["turn_max_s"] == 20.0
    assert t["wasted_s"] == 18.0
    assert t["wasted_share"] == 0.3          # 18s of 60s produced nothing


def test_attempt_rows_survive_the_invocation_boundary():
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._attempts = [[1.5, True], [2.5, False]]
    back = SS.SessionState.from_dict(st.to_dict())
    assert back._attempts == [[1.5, True], [2.5, False]]


def test_coverage_scores_paraphrase_but_not_absence():
    """The verdict grade was identical in all three arms of the first experiment, so quality needed a
    measure that can actually move."""
    from tessera_platform.quorum import metrics as QM
    st = _state({"evaluation": {"expected_issues": [
        "single region failover", "PII in prompts", "shared database coupling", "audit trail"]}})
    for txt in ["The single region failover story is unproven.",
                "PII inside prompts is a notifiable risk.",
                "Twelve services share one database, so coupling is total."]:
        SS._add_entry(st, 1, "m1", {"type": "objection", "topic": txt[:30], "text": txt}, None)
    q = QM.collect(st, "h")["quality"]
    assert q["expected_issues"] == 4
    assert q["covered"] == 3, q["missed"]        # paraphrase counts
    assert q["missed"] == ["audit trail"]        # silence does not
    assert q["coverage"] == 0.75


def test_coverage_is_none_when_no_rubric_was_supplied():
    from tessera_platform.quorum import metrics as QM
    assert QM.collect(_state({}), "h")["quality"]["coverage"] is None


def test_a_rubric_issue_may_list_acceptable_phrasings():
    """The first live rubric scored a room that plainly discussed "single region", "shared Postgres",
    "RTO" and "audit" as missing all four, because it demanded FAILOVER / COUPLING / OBJECTIVE / TRAIL.
    The synonyms belong in the rubric, where they can be audited — not in a fuzzier matcher."""
    from tessera_platform.quorum import metrics as QM
    st = _state({"evaluation": {"expected_issues": [
        {"issue": "single-region SPOF", "any_of": ["single region", "one region", "region outage"]},
        {"issue": "recovery objective", "any_of": ["recovery time", "RTO"]},
        {"issue": "fraud abuse", "any_of": ["fraud", "abuse of the refund"]},
    ]}})
    for txt in ["Single region is the whole problem.", "Their RTO is undefined."]:
        SS._add_entry(st, 1, "m1", {"type": "objection", "topic": txt[:28], "text": txt}, None)
    q = QM.collect(st, "h")["quality"]
    assert q["covered"] == 2 and q["missed"] == ["fraud abuse"], q
    assert q["coverage"] == 0.67


def test_a_yield_is_counted_once_not_twice():
    """A gate-caused yield used to increment the counter twice — once when the gate emptied the turn and
    again because the turn was then empty. A live run recorded 12 yields against 6 unproductive attempts,
    and the doubled figure reached the paper before the attempt log contradicted it."""
    import re
    import tessera_platform.quorum.session as _sess
    src = open(_sess.__file__).read()
    assert len(re.findall(r"_yields = int\(getattr", src)) == 1, "more than one counting point"


def test_yields_agree_with_the_attempt_log():
    """The attempt log is the arbiter: unproductive attempts ARE the yields."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._attempts = [[9.0, True], [7.0, False], [8.0, False]]
    st._yields = 2
    m = QM.collect(st, "h")
    unproductive = m["timing"]["attempts"] - len([a for a in st._attempts if a[1]])
    assert m["cost"]["yields"] == unproductive == 2


# --- instrumentation the experiment programme needs (§16) --------------------------------
def test_discourse_metrics_expose_the_MIX_not_just_the_volume():
    """E4 (model diversity) and E5 (agreeableness) both turn on whether a room objects, concedes or
    asks — a room that says a lot while agreeing is the failure we are trying to detect."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    for t in ("objection", "objection", "concession", "claim", "question", "dissent"):
        SS._add_entry(st, 1, "m1", {"type": t, "topic": f"top {t}", "text": f"a {t} about the system"}, None)
    d = QM.collect(st, "h")["discourse"]
    assert d["types"]["objection"] == 2
    assert d["objection_share"] == 0.5          # objections + dissents over member turns
    assert d["concession_share"] == 0.17
    assert d["question_share"] == 0.17


def test_near_duplicate_pairs_measure_what_the_gates_exist_to_prevent():
    """E2 asks whether the gates buy anything; that is unanswerable without measuring repetition."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    same = ("The single region deployment is an unacceptable single point of failure for refunds "
            "and the recovery story is undefined.")
    SS._add_entry(st, 1, "m1", {"type": "objection", "topic": "region", "text": same}, None)
    SS._add_entry(st, 1, "m2", {"type": "objection", "topic": "region", "text": same}, None)
    SS._add_entry(st, 1, "m3", {"type": "claim", "topic": "cost",
                                "text": "OpenSearch spend scales with index size and will dominate."}, None)
    assert QM.collect(st, "h")["discourse"]["near_duplicate_pairs"] == 1


def test_board_audit_problems_are_counted_at_draw_time():
    """The auditor inspects a RENDERED spec, so its findings cannot be recomputed from the op log —
    recomputing them there raised AttributeError and reported None, which would have read as 'no
    problems' in E6."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st._board_problems = 3
    b = QM.collect(st, "h")["board"]
    assert b["audit_problems"] == 3
    back = SS.SessionState.from_dict(st.to_dict())
    assert back._board_problems == 3


def test_agenda_labels_are_emitted_verbatim_for_manual_classification():
    """A keyword-overlap proxy scored 1 of 4 procedural labels, because "Conditional approval" and
    "Decision convergence" share vocabulary with the decision text. Emit the labels; do not pretend."""
    from tessera_platform.quorum import metrics as QM
    st = SS.SessionState(session_id="t", decision="refund approval decision", context="")
    st.agenda = [{"topic": "Conditional approval", "by": "m1", "status": "open", "remarks": 1,
                  "by_member": {}},
                 {"topic": "PII in prompts", "by": "m1", "status": "open", "remarks": 1, "by_member": {}}]
    d = QM.collect(st, "h")["discourse"]
    assert d["labels"] == ["Conditional approval", "PII in prompts"]


# --- E1: the deterministic allocator, measured with zero model calls ---------------------
def _panel(overrides, seats=("chair1", "m1", "m2", "m3", "m4")):
    from tessera_platform.quorum.models import Persona, QuorumPlan
    st = _state(overrides)
    lanes = {"chair1": ["facilitation"], "m1": ["privacy", "PII"], "m2": ["reliability", "failover"],
             "m3": ["cost", "finance"], "m4": ["security", "fraud"]}
    st.personas = {s: Persona(persona_id=s, name=s, stance="", dimensions=lanes[s],
                              role=("facilitator" if s == "chair1" else "challenger")) for s in seats}
    st.plan = QuorumPlan(seated=list(seats), excluded=[])
    return st


def test_the_allocator_never_picks_a_member_with_nothing_it_may_say():
    """The measured cause of wasted attempts: the moderator names a member, the member finds every live
    item closed to it, and the turn evaporates after two model calls. Code can see this; the model cannot."""
    st = _panel({"room": {"turn_allocation": "fairness_first"},
                 "gates": {"saturation": {"remarks": 3, "member_cap": 2}}})
    # one live topic, and m1 has already worked it to its personal cap
    st.agenda = [{"topic": "PII in prompts", "by": "m1", "status": "open", "remarks": 2,
                  "by_member": {"m1": 2}}]
    for _ in range(40):
        pid = SS._allocate_speaker(st)
        assert pid != "m1", "picked the one member with no capacity left"
        assert pid is not None
        st.transcript.entries.append(
            SS.TranscriptEntry(entry_id=f"e{len(st.transcript.entries)}", round=1, member=pid,
                               type="claim", text="x"))
        st.turns += 1


def test_the_allocator_returns_none_when_nobody_may_speak():
    """Which the caller reads as 'the room is done' — better than spending two calls to discover it."""
    st = _panel({"room": {"turn_allocation": "fairness_first"},
                 "gates": {"saturation": {"remarks": 3, "member_cap": 1}}})
    st.agenda = [{"topic": "PII in prompts", "by": "m1", "status": "open", "remarks": 1,
                  "by_member": {p: 1 for p in st.plan.seated}}]
    assert SS._allocate_speaker(st) is None


def test_the_allocator_is_fair_and_never_repeats_a_speaker(capsys):
    st = _panel({"room": {"turn_allocation": "fairness_first"}})
    picks = []
    for _ in range(20):
        pid = SS._allocate_speaker(st)
        picks.append(pid)
        st.transcript.entries.append(
            SS.TranscriptEntry(entry_id=f"e{len(st.transcript.entries)}", round=1, member=pid,
                               type="claim", text="x"))
        st.turns += 1
    import collections
    counts = collections.Counter(picks)
    assert max(counts.values()) - min(counts.values()) <= 1, counts    # even to within one turn
    assert all(picks[i] != picks[i + 1] for i in range(len(picks) - 1)), "spoke twice in a row"
    dominance = max(counts.values()) / len(picks)
    assert dominance <= 0.25, f"dominance {dominance} — worse than the model allocator's measured 0.29"


def test_the_allocator_prefers_the_lane_that_matches_the_floor():
    st = _panel({"room": {"turn_allocation": "fairness_first", "agenda_ownership": "chaired"}})
    st.agenda = [{"topic": "fraud abuse of refunds", "by": "chair1", "status": "open", "remarks": 1,
                  "by_member": {}}]
    assert SS._allocate_speaker(st) == "m4", "the security/fraud lane should take a fraud item"


def test_the_allocator_is_deterministic():
    """A nondeterministic allocator makes two runs of one config differ for reasons no setting controls —
    which is a measured source of the noise that invalidated the first round of comparisons."""
    a = SS._allocate_speaker(_panel({"room": {"turn_allocation": "fairness_first"}}))
    b = SS._allocate_speaker(_panel({"room": {"turn_allocation": "fairness_first"}}))
    assert a == b


def test_the_code_allocator_is_now_the_DEFAULT():
    """Changed after E1: model calls per turn 3.95 -> 1.95 and yields 7 -> 2 at unchanged coverage."""
    assert SS._setting(_panel({}), "room.turn_allocation") == "fairness_first"
    assert SS._setting(_panel({"room": {"turn_allocation": "moderated"}}),
                       "room.turn_allocation") == "moderated"   # the model call is still available


def test_model_capabilities_survive_the_invocation_boundary():
    """Every command is a separate lambda invocation with a FRESH speaker, so a capability discovered
    while probing at launch is unknown to the next advance unless it travels through the session. It did
    not, and six runs of an experiment died with zero member turns and a ValidationException per attempt
    ('temperature is deprecated for this model')."""
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.model_caps = {"no_temperature": ["us.anthropic.claude-sonnet-5"]}
    back = SS.SessionState.from_dict(st.to_dict())
    assert back.model_caps == {"no_temperature": ["us.anthropic.claude-sonnet-5"]}


def test_route_restores_capabilities_and_seat_together():
    class FakeSpeaker:
        def __init__(self):
            self._per_seat, self._seat, self._no_temperature = {}, "", set()
            self.model_id = "base"
        def seat(self, pid):
            self._seat = pid
    st = SS.SessionState(session_id="t", decision="d", context="c")
    st.seat_models = {"m1": "us.anthropic.claude-sonnet-5"}
    st.model_caps = {"no_temperature": ["us.anthropic.claude-sonnet-5"]}
    sp = FakeSpeaker()
    SS._route(sp, st, "m1")
    assert sp._seat == "m1"
    assert sp._per_seat == {"m1": "us.anthropic.claude-sonnet-5"}
    assert "us.anthropic.claude-sonnet-5" in sp._no_temperature, "capability not restored"


def test_a_reasoning_model_with_too_small_a_turn_budget_is_refused():
    """DeepSeek R1 at the default 520 tokens was truncated mid-thought and produced no usable turn —
    six sessions convened and recorded zero member turns. Refuse the combination, do not silently
    convene a room that cannot speak."""
    bad = {"models": {"members": "us.deepseek.r1-v1:0", "max_tokens": {"turn": 520}}}
    errs = S.validate(S.resolve(None, bad))
    assert any("reasoning model" in e for e in errs), errs
    ok = {"models": {"members": "us.deepseek.r1-v1:0"}}     # 1200 is now the default budget
    assert S.validate(S.resolve(None, ok)) == []


def test_sonnet_5_needs_no_special_configuration():
    """Its temperature quirk is discovered by probing at launch, not declared here."""
    assert S.validate(S.resolve(None, {"models": {"default": "us.anthropic.claude-sonnet-5"}})) == []


# --- empanelment must SEAT the dimensions the ecosystem declares -------------------------
def test_declared_dimensions_are_seated_not_merely_reported_missing():
    """Measured: an architecture review board convened a chair plus four conduct personas and reported
    missing: [reliability, data, cost]. The gap was computed, named in prose by the completeness
    adversary, and then nobody was seated to raise it."""
    from tessera_platform.quorum.empanelment import _cover_required_dims
    from tessera_platform.quorum.models import Persona

    def P(pid, dims):
        return Persona(persona_id=pid, name=pid, stance="", dimensions=dims)

    pool = [P("chair", ["governance"]), P("conduct", ["conduct", "customer"]),
            P("privacy", ["privacy", "customer"]), P("legal", ["legal", "reputation"]),
            P("sre", ["reliability"]), P("data", ["data"]), P("cost", ["cost"])]
    by_id = {p.persona_id: p for p in pool}
    seated = ["chair", "conduct", "privacy", "legal"]
    required = ["reliability", "data", "cost"]

    out = _cover_required_dims(list(seated), by_id, pool, required, ["chair"], hi=6)
    covered = {d for pid in out for d in by_id[pid].dimensions}
    assert all(d in covered for d in required), (out, covered)
    assert "chair" in out, "a must_include seat must never be swapped out"
    assert len(out) <= 6, "the seat budget must be respected"


def test_coverage_swaps_the_redundant_seat_when_the_panel_is_full():
    from tessera_platform.quorum.empanelment import _cover_required_dims
    from tessera_platform.quorum.models import Persona

    def P(pid, dims):
        return Persona(persona_id=pid, name=pid, stance="", dimensions=dims)

    # `dup` adds nothing: every dimension it holds is already held by others, and none is required
    pool = [P("chair", ["governance"]), P("conduct", ["conduct", "customer"]),
            P("dup", ["conduct"]), P("sre", ["reliability"])]
    by_id = {p.persona_id: p for p in pool}
    out = _cover_required_dims(["chair", "conduct", "dup"], by_id, pool, ["reliability"], ["chair"], hi=3)
    assert "sre" in out and "dup" not in out, out
    assert len(out) == 3, "the seat budget must be respected"


def test_a_declared_dimension_outranks_a_seat_the_convener_merely_preferred():
    """Policy, stated deliberately: if the ecosystem declares a dimension, it gets a seat even when no
    seat is strictly redundant — the convener's extra preference yields. Without this an architecture
    review stayed at four conduct seats and never sat reliability, because each conduct persona held one
    dimension nobody else did."""
    from tessera_platform.quorum.empanelment import _cover_required_dims
    from tessera_platform.quorum.models import Persona

    def P(pid, dims):
        return Persona(persona_id=pid, name=pid, stance="", dimensions=dims)

    pool = [P("a", ["x"]), P("b", ["y"]), P("sre", ["reliability"])]
    by_id = {p.persona_id: p for p in pool}
    out = _cover_required_dims(["a", "b"], by_id, pool, ["reliability"], [], hi=2)
    assert "sre" in out and len(out) == 2, out


def test_coverage_stops_once_every_remaining_seat_covers_a_requirement():
    """The stop condition: nobody covering a declared dimension is ever unseated for another one."""
    from tessera_platform.quorum.empanelment import _cover_required_dims
    from tessera_platform.quorum.models import Persona

    def P(pid, dims):
        return Persona(persona_id=pid, name=pid, stance="", dimensions=dims)

    pool = [P("sre", ["reliability"]), P("data", ["data"]), P("cost", ["cost"])]
    by_id = {p.persona_id: p for p in pool}
    out = _cover_required_dims(["sre", "data"], by_id, pool, ["reliability", "data", "cost"], [], hi=2)
    assert sorted(out) == ["data", "sre"], out      # cost stays uncovered rather than displacing them


# --- the convener call must be able to FINISH answering ----------------------------------
def test_a_truncated_convener_response_is_retried_with_more_room():
    """Measured: Sonnet 5 returns a reasoning block alongside its text and both draw on maxTokens, and
    the pool grew 10 -> 16 so there are more exclusions to account for. The response truncated mid-JSON
    at the hardcoded 4000 and the panel was silently chosen by pool order instead."""
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker
    from tessera_platform.quorum.models import Persona
    sp = BedrockSpeaker.__new__(BedrockSpeaker)
    sp._per_seat, sp._seat, sp.model_id, sp._no_temperature = {}, "", "m", set()
    calls = []

    def fake_ask(system, user, max_tokens=8000):
        calls.append(max_tokens)
        if len(calls) == 1:
            return '{"seated":[{"persona_id":"a","name":"A","stance":"s","dimensi'   # truncated
        return '{"seated":[{"persona_id":"a","name":"A","stance":"s","dimensions":["x"]}],"excluded":[]}'
    sp._ask = fake_ask
    pool = [Persona(persona_id="a", name="A", stance="s", dimensions=["x"])]
    out = sp.empanel("d", "c", pool, {"select": {"min": 1, "max": 2}}, max_tokens=8000)
    assert calls == [8000, 16000], calls
    assert [x["persona_id"] for x in out["seated"]] == ["a"]


def test_the_empanel_budget_comes_from_settings():
    from tessera_platform.quorum.empanelment import _empanel_budget
    assert _empanel_budget({}) == 8000
    pol = {"settings": {"models": {"max_tokens": {"empanel": 12000}}}}
    assert _empanel_budget(pol) == 12000
    assert S.DEFAULTS["models"]["max_tokens"]["empanel"] == 8000


def test_a_pool_filled_panel_is_recorded_on_the_plan_not_just_logged():
    """A whole live run was composed by pool order and nothing surfaced it but one log line."""
    from tessera_platform.quorum.empanelment import empanel
    from tessera_platform.quorum.models import Persona

    class MuteConvener:
        def empanel(self, decision, context, pool, policy, max_tokens=8000):
            return {"seated": [], "excluded": [], "justification": ""}
        def completeness(self, *a, **k):
            return {}
    pool = [Persona(persona_id=f"p{i}", name=f"P{i}", stance="s", dimensions=[f"d{i}"]) for i in range(4)]
    events = []
    plan = empanel("d", "c", pool, {"select": {"min": 3, "max": 4}}, MuteConvener(), events.append)
    assert len(plan.seated) == 3
    assert "COMPOSITION FALLBACK" in plan.justification
    assert any(e.get("type") == "QUORUM_EMPANEL_FALLBACK" for e in events), events


# --- whiteboard realism ------------------------------------------------------------------
def test_a_sketch_extends_an_occupied_board_instead_of_starting_again():
    """A person at a whiteboard adds a box, talks, adds an arrow, comes back and extends it. Each sketch
    call composed from scratch, so the board arrived complete and unrelated every time. The instruction
    changes with the BOARD STATE rather than being an option the model may ignore — the same lesson as
    every other affordance that was offered and never used."""
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker
    from tessera_platform.quorum.models import Persona
    sp = BedrockSpeaker.__new__(BedrockSpeaker)
    sp._per_seat, sp._seat, sp.model_id, sp._no_temperature = {}, "", "m", set()
    seen = {}

    def ask(system, user, max_tokens=700):
        seen["sys"], seen["usr"] = system, user
        return '{"point":"x","type":"flow","nodes":[]}'
    sp._ask = ask
    p = Persona(persona_id="m1", name="M", stance="s", dimensions=["reliability"],
                draw_repertoire=["flow"])
    sp.sketch(p, "show the cascade", "d", inventory=[], repertoire=["flow"])
    assert "ALREADY IN USE" not in seen["sys"], "a blank board must not ask for an extension"
    sp.sketch(p, "show the cascade", "d", repertoire=["flow"],
              inventory=[{"id": "wb-1", "label": "Shared Postgres", "author": "m2", "kind": "box"}])
    assert "ALREADY IN USE" in seen["sys"]
    assert "AT MOST 3 new nodes" in seen["sys"]
    assert "Shared Postgres" in seen["usr"], "the board contents must still be listed"


def test_a_declared_node_kind_becomes_a_SHAPE_not_just_a_colour():
    """`kind` (risk/control/store/actor/service/impact) has been declared or inferred all along and only
    ever changed the colour, so a datastore, a person and a risk were three identical rectangles in three
    colours. Costs nothing on the model side — the data was already being sent and thrown away."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch, NODE_SHAPES
    spec = {"point": "the shared store is the single failure domain", "type": "failure",
            "nodes": [{"label": "Refund agent", "kind": "actor"},
                      {"label": "Shared Postgres", "kind": "store"},
                      {"label": "Prompt injection", "kind": "risk"},
                      {"label": "12 services down", "kind": "impact"},
                      {"label": "Server-side validation", "kind": "control"},
                      {"label": "Executor API", "kind": "service"}],
            "edges": [{"from": 0, "to": 1}, {"from": 2, "to": 1}, {"from": 1, "to": 3}]}
    boxes = [o for o in render_sketch(spec, Whiteboard(), ["failure"]) if o.get("kind") == "box"]
    shapes = {str(o.get("label")).split("\n")[0]: o.get("shape") for o in boxes}
    assert shapes["Refund agent"] == "person"
    assert shapes["Shared Postgres"] == "cylinder"
    assert shapes["Prompt injection"] == "diamond"
    assert shapes["12 services down"] == "burst"
    assert shapes["Executor API"] == "rounded"
    assert len({o.get("shape") for o in boxes}) >= 5, "a board of one silhouette is a board of boxes"
    assert NODE_SHAPES.get("nonsense") is None      # unknown kinds stay rectangles


def test_an_unkinded_node_is_still_a_rectangle():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    spec = {"point": "a plain sequence", "type": "flow",
            "nodes": [{"label": "Step one"}, {"label": "Step two"}], "edges": [{"from": 0, "to": 1}]}
    boxes = [o for o in render_sketch(spec, Whiteboard(), ["flow"]) if o.get("kind") == "box"]
    assert all(o.get("shape") in (None, "rect") for o in boxes), [o.get("shape") for o in boxes]


def test_a_one_box_extension_attached_to_the_board_is_drawn_and_connected():
    """Two rules I wrote collided. The sketch prompt asks for "AT MOST 3 new nodes" once the board is in
    use, while the renderer refused any spec with "fewer than 2 nodes" — so adding one box and joining it
    to something already up there, an entirely ordinary whiteboard move, produced nothing. Measured: 3 of
    4 sketch calls refused in one live run, with from_label appearing only in the prompt and nowhere in
    the renderer."""
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    wb = Whiteboard()
    wb.add_op("m1", "box", {"x": 100, "y": 100, "w": 160, "h": 60, "label": "Shared Postgres"}, refs=[])
    spec = {"point": "the executor writes straight to the shared store", "type": "flow",
            "nodes": [{"label": "Executor API", "kind": "service"}],
            "edges": [{"from": 0, "to": 0, "from_label": "Shared Postgres", "label": "writes"}]}
    ops = render_sketch(spec, wb, ["flow"])
    kinds = [o.get("kind") for o in ops]
    assert "box" in kinds, ops
    assert "arrow" in kinds, "an unconnected box is not an extension, it is a box floating in space"


def test_an_unresolvable_label_does_not_fake_a_connection():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch, resolve_board_label
    wb = Whiteboard()
    wb.add_op("m1", "box", {"x": 0, "y": 0, "w": 160, "h": 60, "label": "Shared Postgres"}, refs=[])
    assert resolve_board_label(wb, "Kafka") is None
    assert resolve_board_label(wb, "postgres") is not None       # case and partial match
    spec = {"point": "a claim", "type": "flow", "nodes": [{"label": "Only node"}],
            "edges": [{"from": 0, "to": 0, "from_label": "Kafka"}]}
    assert render_sketch(spec, wb, ["flow"]) == [], "one node and no real anchor is not a diagram"


def test_a_fresh_multi_node_sketch_is_unaffected():
    from tessera_platform.quorum.whiteboard import Whiteboard, render_sketch
    spec = {"point": "cascade", "type": "failure",
            "nodes": [{"label": "Injection", "kind": "risk"}, {"label": "Store", "kind": "store"},
                      {"label": "12 services", "kind": "impact"}],
            "edges": [{"from": 0, "to": 1}, {"from": 1, "to": 2}]}
    ops = render_sketch(spec, Whiteboard(), ["failure"])
    assert sum(1 for o in ops if o.get("kind") == "arrow") == 2
    assert sum(1 for o in ops if o.get("kind") == "box") == 3


# --- the verdict is a board artefact, not a transcript dump ------------------------------
def test_the_verdict_strips_meeting_talk_from_a_blocker():
    """A live verdict listed 'Priya, on the injection surface - I'll add the data angle: ...' and
    'Picking up where I left off - R-4's Bedrock invocation logs...' as INDEFENSIBLE ELEMENTS. Those are
    turn-openers, not architecture defects."""
    from tessera_platform.quorum.verdict import _claim_of
    names = {"Priya", "Aisha", "James"}
    assert _claim_of("Priya, on the injection surface — I'll add the data angle: refund args carrying "
                     "order-IDs are a data-integrity vector.", names).startswith("refund args")
    assert _claim_of("Picking up where I left off — R-4's Bedrock invocation logs are unanswered.",
                     names).startswith("R-4's")
    # a genuine technical claim whose leading clause IS the claim must survive untouched
    kept = "Single-region deployment with undefined RTO/RPO for AgentCore Memory and OpenSearch"
    assert _claim_of(kept, names) == kept


def test_one_element_per_blocker_not_per_restatement():
    """The same live verdict listed SEVEN indefensible elements while its own summary said 'three
    unresolved blockers' — the count measured how much the room talked. Grouping uses the agenda, which
    already collapses paraphrase, because deduping on claim text alone could not separate a restatement
    (one shared distinctive word) from two different blockers (one shared common word)."""
    from tessera_platform.quorum.verdict import render_verdict
    from tessera_platform.quorum.models import Transcript, TranscriptEntry, QuorumPlan, Persona
    said = [("sec", "The chair asked me to take R-1 directly, and here it is: policy-cap validation is "
                    "not the same as a deterministic rules gate on amount and order-ID."),
            ("sec", "The deterministic gate is still missing — that's the blocker I need closed."),
            ("sre", "Single-region deployment with undefined RTO/RPO for AgentCore Memory.")]
    tr = Transcript()
    for i, (m, t) in enumerate(said):
        tr.entries.append(TranscriptEntry(entry_id=f"e{i}", round=1, member=m, type="objection",
                                          text=t, detail="why it blocks go-live", severity="blocker"))
    ps = {"sec": Persona(persona_id="sec", name="Priya Haddad", stance="", dimensions=[]),
          "sre": Persona(persona_id="sre", name="James Kaur", stance="", dimensions=[])}

    class Sp:
        def verdict(self, *a, **k):
            return {"grade": "indefensible", "summary": "two unresolved blockers", "remediation": []}

    v = render_verdict("d", "c", tr, QuorumPlan(seated=["sec", "sre"], excluded=[]), ps, {}, Sp(),
                       agenda={"raised": ["deterministic rules gate", "single-region RTO/RPO"],
                               "saturated": [], "settled_by_human": [], "parked": []})
    assert len(v.indefensible_elements) == 2, [e["element"] for e in v.indefensible_elements]
    for e in v.indefensible_elements:
        assert e["argument"] != e["element"], "element and argument were literally the same string"
        assert not e["element"].startswith("The chair asked me")
