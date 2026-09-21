"""Unit tests for the mission-console Lambda (standalone run-planner for exported ecos).

Covers the pure turn-parsing + sufficiency gate (no AWS), the handler guards
(OPTIONS / non-mission mode), and the synchronous mission turn with Bedrock stubbed.
"""
import importlib.util
import json
import os
import sys

import pytest

_HANDLER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lambdas", "mission-console", "handler.py")
)


@pytest.fixture
def mc(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    sys.modules.pop("handler_mission_console", None)
    spec = importlib.util.spec_from_file_location("handler_mission_console", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── _parse_mission_turn (pure) ─────────────────────────────────────────────────

def test_parse_two_part_response(mc):
    text = (
        "Got it — let's plan this run.\n"
        "===MISSION===\n"
        '{"next_question": {"id": "q1", "text": "Which vendor?"},'
        ' "mission_state": {"goal": "assess", "checklist": []},'
        ' "converged": false, "mission": null, "inputs": {}}'
    )
    turn = mc._parse_mission_turn(text)
    assert turn["reply"].startswith("Got it")
    assert turn["next_question"]["id"] == "q1"
    assert turn["converged"] is False
    assert turn["_parse_error"] is False


def test_parse_error_flag_on_bad_json(mc):
    text = "Reply here.\n===MISSION===\n{ this is not json"
    turn = mc._parse_mission_turn(text)
    assert turn["_parse_error"] is True
    assert turn["mission_state"] == {}
    assert turn["converged"] is False


def test_sufficiency_gate_overrides_eager_convergence(mc):
    # Model claims converged=true but a checklist item is unresolved → gate resets it.
    text = (
        "All set!\n"
        "===MISSION===\n"
        '{"mission_state": {"checklist": [{"id": "scope", "label": "scope", "resolved": false}]},'
        ' "converged": true, "mission": "Do the thing", "inputs": {"a": "b"}}'
    )
    turn = mc._parse_mission_turn(text)
    assert turn["converged"] is False
    assert turn["mission"] is None
    # A follow-up question is synthesized for the first unresolved item.
    assert turn["next_question"]["id"] == "scope"


def test_convergence_allowed_when_checklist_resolved(mc):
    text = (
        "Ready.\n"
        "===MISSION===\n"
        '{"mission_state": {"checklist": [{"id": "scope", "label": "scope", "resolved": true}]},'
        ' "converged": true, "mission": "Assess vendor X", "inputs": {"vendor": "X"}}'
    )
    turn = mc._parse_mission_turn(text)
    assert turn["converged"] is True
    assert turn["mission"] == "Assess vendor X"
    assert turn["inputs"] == {"vendor": "X"}


# ── handler guards ──────────────────────────────────────────────────────────

def test_options_preflight(mc):
    resp = mc.handler({"httpMethod": "OPTIONS"}, None)
    assert resp["statusCode"] == 200


def test_rejects_non_mission_mode(mc):
    event = {"body": json.dumps({"mode": "converse", "message": "hi"})}
    resp = mc.handler(event, None)
    assert resp["statusCode"] == 400


# ── synchronous mission turn (Bedrock stubbed) ───────────────────────────────

class _StubBedrockBody:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class _StubBedrock:
    def __init__(self, text):
        self._text = text

    def invoke_model(self, **_kwargs):
        return {"body": _StubBedrockBody({"content": [{"text": self._text}], "stop_reason": "end_turn"})}


def test_sync_turn_returns_parsed_turn(mc, monkeypatch):
    reply_text = (
        "Let's start.\n"
        "===MISSION===\n"
        '{"next_question": {"id": "q1", "text": "Which vendor?"},'
        ' "mission_state": {"goal": "assess", "checklist": []},'
        ' "converged": false, "mission": null, "inputs": {}}'
    )

    def _fake_client(service, **_kwargs):
        # bedrock-runtime is stubbed; other services (e.g. s3 for attachment
        # enrichment) are unused with no attachments, so a bare object suffices.
        if service == "bedrock-runtime":
            return _StubBedrock(reply_text)
        return object()

    monkeypatch.setattr(mc.boto3, "client", _fake_client)

    # No clientId → synchronous JSON turn (no WebSocket).
    event = {"body": json.dumps({
        "mode": "mission", "message": "assess a vendor",
        "eco_context": {"name": "vendor-assessment", "inputs": []},
    })}
    resp = mc.handler(event, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["next_question"]["id"] == "q1"
    assert body["converged"] is False
