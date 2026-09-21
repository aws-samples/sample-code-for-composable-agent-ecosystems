"""Quorum engine tests — fully deterministic via FakeSpeaker (no AWS).

Covers the invariants that make the archetype defensible (spec-quorum-archetype.md):
empanelment bounds + justified exclusions, the completeness/escalation addition, dissent
preservation, and the verdict enforcement (open blocker ⇒ indefensible; dissent ⇒ at least
conditions).
"""
from tessera_platform.quorum import run_quorum
from tessera_platform.quorum.personas import from_seed_file
from tessera_platform.quorum.run import FakeSpeaker

POOL = from_seed_file()

BASE_CFG = {
    "name": "defensibility-rehearsal",
    "quorum": {
        "empanelment": {"select": {"min": 3, "max": 7,
                        "diversity": ["regulatory", "conduct", "customer", "security", "accountability"]}},
        "deliberation": {"max_rounds": 3},
    },
}
DECISION = "Auto-approve retail loan hardship variations with an agentic AI over customer PII."


def _run(cfg=BASE_CFG, pool=POOL):
    return run_quorum(cfg, DECISION, "AU retail bank", list(pool), FakeSpeaker())


def test_seed_pool_loaded():
    assert len(POOL) >= 8
    assert {"regulators_eye", "security_adversary", "ethicist"} <= {p.persona_id for p in POOL}


def test_empanelment_bounds_and_justified_exclusions():
    res = _run()
    plan = res.plan
    assert 3 <= len(plan.seated) <= 7
    assert plan.justification
    # composition defensibility: every non-seated persona is accounted for with a reason
    non_seated = {p.persona_id for p in POOL} - set(plan.seated)
    excluded_ids = {e["persona_id"] for e in plan.excluded}
    assert non_seated <= excluded_ids
    assert all(e.get("reason") for e in plan.excluded)


def test_no_persona_is_both_seated_and_excluded():
    plan = _run().plan
    assert not (set(plan.seated) & {e["persona_id"] for e in plan.excluded})
    # a summoned member is seated, never left in excluded
    for pid in plan.summoned:
        assert pid in plan.seated
        assert pid not in {e["persona_id"] for e in plan.excluded}


def test_completeness_or_escalation_adds_a_member():
    plan = _run().plan
    # FakeSpeaker escalates once and completeness flags the uncovered 'fairness' dim
    assert plan.summoned, "expected at least one summoned member"


def test_dissent_preserved_and_blocker_forces_indefensible():
    res = _run()
    assert res.transcript.dissent, "dissent must be preserved"
    assert res.transcript.open_issues, "expected open issues"
    assert any(e.severity == "blocker" for e in res.transcript.open_issues)
    assert res.verdict.grade == "indefensible"
    assert res.verdict.against_whom
    assert res.verdict.dissent_preserved


def test_deterministic():
    a, b = _run(), _run()
    assert a.verdict.grade == b.verdict.grade
    assert set(a.plan.seated) == set(b.plan.seated)
    assert len(a.transcript.entries) == len(b.transcript.entries)


def test_dissent_without_blocker_yields_conditions_not_indefensible():
    # A panel of only non-harsh personas raises a dissent but no blocker.
    gentle = [p for p in POOL if p.severity_bias != "harsh"]
    cfg = {"name": "gentle", "quorum": {"empanelment": {"select": {"min": 2, "max": 6}},
                                        "deliberation": {"max_rounds": 3, "escalation": False}}}
    res = run_quorum(cfg, DECISION, "", gentle, FakeSpeaker())
    assert not any(e.severity == "blocker" for e in res.transcript.open_issues)
    assert res.verdict.grade == "defensible_with_conditions"


def test_result_is_json_serialisable():
    import json
    json.dumps(_run().to_dict())  # must not raise
