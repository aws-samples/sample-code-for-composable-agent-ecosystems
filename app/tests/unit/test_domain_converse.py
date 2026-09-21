"""Unit tests for the conversational domain builder (Phase 1) in lambdas/domain-seed.

Covers the pure two-part parser (_parse_domain_turn) and the handler's mode=converse
dispatch. No boto3 / no network — the Bedrock call (_converse_text) is monkeypatched.
"""
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "domain-seed", "handler.py"))


def _mod():
    os.environ.setdefault("ECOSYSTEMS_BUCKET", "amzn-s3-demo-test-bucket")
    if "boto3" not in sys.modules:
        b = types.ModuleType("boto3")
        b.client = lambda *a, **k: None
        sys.modules["boto3"] = b
    spec = importlib.util.spec_from_file_location("domain_seed", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_TWO_PART_OPEN = (
    "Great — let's shape the AU financial advice knowledge. Should this cover personal "
    "insurance too?\n"
    "===DOMAIN===\n"
    "{\n"
    '  "next_question": {"id": "scope", "text": "Include personal insurance?", "options": ["Yes", "No"]},\n'
    '  "design": {\n'
    '    "domain": {"suggested_id": "au-financial-advice", "suggested_name": "AU Financial Advice", "description": "Durable AU advice reference knowledge."},\n'
    '    "taxonomy": [\n'
    '      {"type": "regulatory-figure", "key": "{measure}-{fy}", "description": "A dated statutory figure."},\n'
    '      {"type": "advice-standard", "description": "An advice-law standard."}\n'
    "    ],\n"
    '    "conclusions_never_cataloged": ["a client Statement of Advice"]\n'
    "  },\n"
    '  "converged": false\n'
    "}"
)

_TWO_PART_CONVERGED = (
    "All set — here's the final taxonomy.\n"
    "===DOMAIN===\n"
    '{"next_question": null, "design": {"domain": {"suggested_id": "au-financial-advice", '
    '"suggested_name": "AU Financial Advice", "description": "..."}, "taxonomy": '
    '[{"type": "regulatory-figure", "key": "{measure}-{fy}", "description": "x", "status": "proposed"}], '
    '"conclusions_never_cataloged": ["a SOA"]}, "converged": true}'
)


def test_parse_turn_open_normalizes_taxonomy():
    m = _mod()
    turn = m._parse_domain_turn(_TWO_PART_OPEN)
    assert turn["reply"].startswith("Great")
    assert "===DOMAIN===" not in turn["reply"]
    assert turn["converged"] is False
    assert turn["next_question"]["options"] == ["Yes", "No"]
    tax = turn["design"]["taxonomy"]
    assert len(tax) == 2
    # missing key defaults to {id}; missing status defaults to proposed
    adv = next(t for t in tax if t["type"] == "advice-standard")
    assert adv["key"] == "{id}"
    assert adv["status"] == "proposed"
    assert turn["design"]["conclusions_never_cataloged"] == ["a client Statement of Advice"]


def test_parse_turn_converged():
    m = _mod()
    turn = m._parse_domain_turn(_TWO_PART_CONVERGED)
    assert turn["converged"] is True
    assert turn["next_question"] is None
    assert turn["design"]["domain"]["suggested_id"] == "au-financial-advice"


def test_parse_turn_no_marker_is_safe():
    m = _mod()
    turn = m._parse_domain_turn("Just chatting, no design yet.")
    assert turn["reply"] == "Just chatting, no design yet."
    assert turn["design"]["taxonomy"] == []
    assert turn["converged"] is False


def test_handler_converse_dispatch(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "_converse_text", lambda *a, **k: _TWO_PART_OPEN)
    event = {"body": json.dumps({"mode": "converse", "message": "AU financial advice", "history": [], "design_state": {}})}
    resp = m.handler(event, None)
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["origin"] == "converse"
    assert out["converged"] is False
    assert out["next_question"]["id"] == "scope"
    assert any(t["type"] == "regulatory-figure" for t in out["design"]["taxonomy"])
