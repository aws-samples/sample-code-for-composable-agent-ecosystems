"""Unit tests for the deterministic decision engine (docs/spec-decision-engine.md)."""
import copy

import pytest

from tessera_platform.decision import validate_config, evaluate, DecisionConfigError


# ── the enterprise-risk-board instantiation from the spec (primary fixture) ──────
ERB = {
    "item_noun": "finding",
    "dimensions": {
        "likelihood": {"type": "ordinal", "levels": ["low", "medium", "high"]},
        "impact": {"type": "ordinal", "levels": ["low", "medium", "high"]},
        "is_day1_breach": {"type": "boolean"},
        "enforceability": {"type": "ordinal", "levels": ["low", "medium", "high"]},
        "verifiability": {"use": "verifiability"},
        "control_status": {"type": "category", "values": ["evidenced", "partial", "absent"]},
        "time_critical": {"type": "boolean"},
    },
    "labels": ["blocker", "material", "minor"],
    "rubric": [
        {"label": "blocker", "when": "(is_day1_breach and verifiability >= grounded)"
                                     " or (control_status == absent and enforceability >= high)"
                                     " or time_critical"},
        {"label": "material", "when": "impact >= high or likelihood >= high"},
        {"label": "minor", "default": True},
    ],
    "decision": {
        "outcomes": ["NO_GO", "GO_WITH_CONDITIONS", "GO"],
        "rule": [
            {"outcome": "NO_GO", "when": "count(label == blocker and status == open) > 0"},
            {"outcome": "GO_WITH_CONDITIONS", "when": "count(label in [blocker, material]) > 0"},
            {"outcome": "GO", "default": True},
        ],
        "conditions_from": ["blocker", "material"],
    },
}


def _item(id, **dims):
    return {"id": id, "dimensions": dims}


# ── validation ───────────────────────────────────────────────────────────────
def test_erb_config_is_valid():
    assert validate_config(ERB) == []


def test_unknown_identifier_flagged():
    bad = copy.deepcopy(ERB)
    bad["rubric"][0]["when"] = "is_dayX_breach and verifiability >= grounded"  # typo
    errs = validate_config(bad)
    assert any("unknown identifier" in e and "is_dayX_breach" in e for e in errs)


def test_missing_default_flagged():
    bad = copy.deepcopy(ERB)
    bad["rubric"] = bad["rubric"][:2]  # drop the default minor rule
    errs = validate_config(bad)
    assert any("default" in e for e in errs)


def test_label_not_in_labels_flagged():
    bad = copy.deepcopy(ERB)
    bad["rubric"][0]["label"] = "showstopper"
    errs = validate_config(bad)
    assert any("showstopper" in e for e in errs)


def test_bad_dimension_type_flagged():
    bad = copy.deepcopy(ERB)
    bad["dimensions"]["impact"] = {"type": "scale"}
    errs = validate_config(bad)
    assert any("impact" in e and "type" in e for e in errs)


def test_ordinal_requires_levels():
    bad = copy.deepcopy(ERB)
    bad["dimensions"]["impact"] = {"type": "ordinal"}
    assert any("ordinal requires" in e for e in validate_config(bad))


def test_decision_needs_default_outcome():
    bad = copy.deepcopy(ERB)
    bad["decision"]["rule"] = bad["decision"]["rule"][:2]
    assert any("default" in e for e in validate_config(bad))


# ── rubric labelling ─────────────────────────────────────────────────────────
def test_blocker_via_day1_breach():
    it = _item("B4", is_day1_breach=True, verifiability="grounded",
               impact="high", likelihood="high", enforceability="high",
               control_status="partial", time_critical=False)
    assert evaluate(ERB, [it])["items"][0]["label"] == "blocker"


def test_blocker_via_absent_control_on_enforceable():
    it = _item("F1", is_day1_breach=False, verifiability="grounded",
               impact="medium", likelihood="medium", enforceability="high",
               control_status="absent", time_critical=False)
    assert evaluate(ERB, [it])["items"][0]["label"] == "blocker"


def test_day1_breach_not_grounded_is_not_blocker():
    # breach asserted but only reasoning-level evidence -> not a hard blocker
    it = _item("X", is_day1_breach=True, verifiability="reasoning",
               impact="high", likelihood="low", enforceability="low",
               control_status="evidenced", time_critical=False)
    # falls through to material (impact high)
    assert evaluate(ERB, [it])["items"][0]["label"] == "material"


def test_material_via_impact():
    it = _item("R2", is_day1_breach=False, verifiability="verified",
               impact="high", likelihood="low", enforceability="medium",
               control_status="partial", time_critical=False)
    assert evaluate(ERB, [it])["items"][0]["label"] == "material"


def test_minor_default():
    it = _item("N1", is_day1_breach=False, verifiability="grounded",
               impact="low", likelihood="low", enforceability="low",
               control_status="evidenced", time_critical=False)
    assert evaluate(ERB, [it])["items"][0]["label"] == "minor"


def test_time_critical_forces_blocker():
    it = _item("P1", is_day1_breach=False, verifiability="assertion",
               impact="low", likelihood="low", enforceability="low",
               control_status="evidenced", time_critical=True)
    assert evaluate(ERB, [it])["items"][0]["label"] == "blocker"


# ── decision rule (outcome) ────────────────────────────────────────────────────
def test_open_blocker_forces_no_go():
    items = [
        _item("B4", is_day1_breach=True, verifiability="grounded", impact="high",
              likelihood="high", enforceability="high", control_status="absent", time_critical=False),
        _item("N1", is_day1_breach=False, verifiability="grounded", impact="low",
              likelihood="low", enforceability="low", control_status="evidenced", time_critical=False),
    ]
    res = evaluate(ERB, items)
    assert res["outcome"] == "NO_GO"
    assert res["outcome_trace"]["open_by_label"].get("blocker") == 1
    assert [c["id"] for c in res["conditions"]] == ["B4"]


def test_closed_blocker_does_not_block():
    it = _item("B4", is_day1_breach=True, verifiability="grounded", impact="high",
               likelihood="high", enforceability="high", control_status="absent", time_critical=False)
    it["status"] = "resolved"
    res = evaluate(ERB, [it])
    # blocker exists but is resolved -> not NO_GO; a labelled item remains -> conditions path
    assert res["outcome"] == "GO_WITH_CONDITIONS"
    assert res["conditions"] == []  # only OPEN items become conditions


def test_material_only_is_go_with_conditions():
    it = _item("R2", is_day1_breach=False, verifiability="verified", impact="high",
               likelihood="low", enforceability="medium", control_status="partial", time_critical=False)
    assert evaluate(ERB, [it])["outcome"] == "GO_WITH_CONDITIONS"


def test_all_minor_is_go():
    items = [_item(f"N{i}", is_day1_breach=False, verifiability="grounded", impact="low",
                   likelihood="low", enforceability="low", control_status="evidenced", time_critical=False)
             for i in range(3)]
    assert evaluate(ERB, items)["outcome"] == "GO"


# ── determinism ────────────────────────────────────────────────────────────────
def test_deterministic_same_inputs_same_output():
    items = [
        _item("B4", is_day1_breach=True, verifiability="grounded", impact="high",
              likelihood="high", enforceability="high", control_status="absent", time_critical=False),
        _item("R2", is_day1_breach=False, verifiability="verified", impact="high",
              likelihood="low", enforceability="medium", control_status="partial", time_critical=False),
    ]
    a = evaluate(ERB, copy.deepcopy(items))
    b = evaluate(ERB, copy.deepcopy(items))
    assert a == b


# ── borderline detection ─────────────────────────────────────────────────────
def test_borderline_flag_on_reasoning_evidence():
    it = _item("X", is_day1_breach=False, verifiability="reasoning", impact="high",
               likelihood="low", enforceability="low", control_status="partial", time_critical=False)
    res = evaluate(ERB, [it])
    assert res["items"][0]["borderline"] is True
    assert "X" in res["borderline"]


def test_borderline_flag_when_one_level_flips_label():
    # impact=high -> material; nudging impact down to medium -> minor => borderline
    it = _item("R", is_day1_breach=False, verifiability="grounded", impact="high",
               likelihood="low", enforceability="low", control_status="evidenced", time_critical=False)
    assert evaluate(ERB, [it])["items"][0]["borderline"] is True


# ── evaluate() rejects invalid config loudly ──────────────────────────────────
def test_evaluate_raises_on_invalid_config():
    bad = copy.deepcopy(ERB)
    bad["rubric"][0]["when"] = "totally bogus <"
    with pytest.raises(DecisionConfigError):
        evaluate(bad, [_item("z", impact="low")])


# ── generality: a totally different (hedge-fund-style) config on the same engine ──
HEDGE = {
    "dimensions": {
        "conviction": {"type": "ordinal", "levels": ["low", "med", "high"]},
        "downside": {"type": "ordinal", "levels": ["low", "med", "high"]},
    },
    "labels": ["buy", "hold", "sell"],
    "rubric": [
        {"label": "sell", "when": "downside >= high"},
        {"label": "buy", "when": "conviction >= high and downside <= med"},
        {"label": "hold", "default": True},
    ],
    "decision": {
        "outcomes": ["ALLOCATE", "PASS"],
        "rule": [
            {"outcome": "ALLOCATE", "when": "count(label == buy) >= 2"},
            {"outcome": "PASS", "default": True},
        ],
    },
}


def test_hedge_fund_config_generality():
    assert validate_config(HEDGE) == []
    items = [
        {"id": "A", "dimensions": {"conviction": "high", "downside": "low"}},
        {"id": "B", "dimensions": {"conviction": "high", "downside": "med"}},
        {"id": "C", "dimensions": {"conviction": "low", "downside": "high"}},
    ]
    res = evaluate(HEDGE, items)
    labels = {i["id"]: i["label"] for i in res["items"]}
    assert labels == {"A": "buy", "B": "buy", "C": "sell"}
    assert res["outcome"] == "ALLOCATE"  # two buys
