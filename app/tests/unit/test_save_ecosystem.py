"""Unit tests for the save-ecosystem handler — routing, ownership/platform guards,
injected-agent stripping, and the interactive/quorum deploy branches.

save-ecosystem/handler.py builds boto3 clients (s3/sfn/avp) at import and reads
ECOSYSTEMS_BUCKET at import, so we set env + enter moto BEFORE importing it, via the
`save_eco` fixture. The heavy orchestrated ASL-compile path is exercised elsewhere
(seeded ecosystems in test_quorum_settings); here we cover the control-flow around it.
"""
import importlib.util
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

_HANDLER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lambdas", "save-ecosystem", "handler.py")
)
BUCKET = "amzn-s3-demo-ecosystems-test"


@pytest.fixture
def save_eco(monkeypatch):
    """Import save-ecosystem/handler.py against a moto-mocked AWS + a test bucket."""
    monkeypatch.setenv("ECOSYSTEMS_BUCKET", BUCKET)
    monkeypatch.setenv("SFN_ROLE_ARN", "arn:aws:iam::123456789012:role/test-sfn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # POLICY_STORE_ID unset -> _create_owner_policy is a no-op (safe for tests).
    monkeypatch.delenv("POLICY_STORE_ID", raising=False)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        # Fresh import so module-level clients bind to the moto session.
        sys.modules.pop("handler_save_eco", None)
        spec = importlib.util.spec_from_file_location("handler_save_eco", _HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def _put(mod, eco_id, yaml_text):
    mod.s3.put_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=yaml_text.encode())


def _get_yaml(mod, eco_id):
    return mod.s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read().decode()


def _event(eco_id, path, body=None, claims=None):
    return {
        "pathParameters": {"id": eco_id},
        "path": path,
        "body": json.dumps(body or {}),
        "requestContext": {"authorizer": {"claims": claims or {}}},
    }


# ── routing ───────────────────────────────────────────────────────────────────

def test_handler_routes_deploy_path_to_deploy(save_eco, monkeypatch):
    called = {}
    monkeypatch.setattr(save_eco, "deploy_state_machine", lambda eid: called.setdefault("eid", eid) or save_eco.response({"deployed": True}))
    save_eco.handler(_event("eco-x", "/api/ecosystems/eco-x/deploy"), None)
    assert called["eid"] == "eco-x"


def test_handler_routes_non_deploy_to_save_yaml(save_eco, monkeypatch):
    called = {}
    monkeypatch.setattr(save_eco, "save_yaml", lambda eid, ev: called.setdefault("eid", eid) or save_eco.response({"saved": True}))
    save_eco.handler(_event("eco-y", "/api/ecosystems/eco-y"), None)
    assert called["eid"] == "eco-y"


# ── auth + guards in save_yaml ─────────────────────────────────────────────────

def test_save_yaml_401_when_unauthenticated(save_eco):
    r = save_eco.save_yaml("eco-1", _event("eco-1", "/api/ecosystems/eco-1", {"yaml": "ecosystem:\n  name: eco-1\n"}, claims={}))
    assert r["statusCode"] == 401


def test_save_yaml_403_when_incoming_is_platform(save_eco):
    body = {"yaml": "platform: true\necosystem:\n  name: eco-1\n"}
    r = save_eco.save_yaml("eco-1", _event("eco-1", "/api/ecosystems/eco-1", body, claims={"sub": "u1"}))
    assert r["statusCode"] == 403
    assert json.loads(r["body"]).get("platform") is True


def test_save_yaml_403_when_existing_is_platform(save_eco):
    _put(save_eco, "eco-p", "platform: true\necosystem:\n  name: eco-p\n")
    body = {"yaml": "ecosystem:\n  name: eco-p\n"}
    r = save_eco.save_yaml("eco-p", _event("eco-p", "/api/ecosystems/eco-p", body, claims={"sub": "u1"}))
    assert r["statusCode"] == 403


def test_save_yaml_403_when_updating_someone_elses_eco(save_eco, monkeypatch):
    _put(save_eco, "eco-o", "ecosystem:\n  name: eco-o\nowner: owner-user\n")
    monkeypatch.setattr(save_eco, "deploy_state_machine", lambda eid: save_eco.response({"deployed": True}))
    body = {"yaml": "ecosystem:\n  name: eco-o\n"}
    r = save_eco.save_yaml("eco-o", _event("eco-o", "/api/ecosystems/eco-o", body, claims={"sub": "not-owner"}))
    assert r["statusCode"] == 403


def test_save_yaml_admin_can_update_others_eco(save_eco, monkeypatch):
    _put(save_eco, "eco-o", "ecosystem:\n  name: eco-o\nowner: owner-user\n")
    monkeypatch.setattr(save_eco, "deploy_state_machine", lambda eid: save_eco.response({"deployed": True}))
    body = {"yaml": "ecosystem:\n  name: eco-o\n"}
    r = save_eco.save_yaml("eco-o", _event("eco-o", "/api/ecosystems/eco-o", body,
                                           claims={"sub": "admin-user", "cognito:groups": "admin"}))
    assert r["statusCode"] == 200


def test_save_yaml_injects_owner_on_create_and_strips_platform_agents(save_eco, monkeypatch):
    monkeypatch.setattr(save_eco, "deploy_state_machine", lambda eid: save_eco.response({"deployed": True}))
    yaml_text = (
        "ecosystem:\n  name: eco-new\n"
        "agents:\n"
        "- id: writer\n"
        "- id: challenger_agent\n"
        "- id: report_agent\n"
        "- id: checkpoint_0\n"
    )
    r = save_eco.save_yaml("eco-new", _event("eco-new", "/api/ecosystems/eco-new", {"yaml": yaml_text}, claims={"sub": "creator-1"}))
    assert r["statusCode"] == 200
    import yaml as _yaml
    stored = _yaml.safe_load(_get_yaml(save_eco, "eco-new"))
    assert stored["owner"] == "creator-1"
    ids = [a["id"] for a in stored["agents"]]
    assert ids == ["writer"]  # injected platform agents stripped


# ── deploy_state_machine strategy branches ─────────────────────────────────────

def test_deploy_interactive_skips_compile(save_eco):
    _put(save_eco, "eco-i", "strategy: interactive\necosystem:\n  name: eco-i\n")
    r = save_eco.deploy_state_machine("eco-i")
    b = json.loads(r["body"])
    assert b["deployed"] is True and b["interactive"] is True and b["state_machine_arn"] == ""


def test_deploy_quorum_fails_closed_when_disabled(save_eco, monkeypatch):
    monkeypatch.delenv("QUORUM_ENABLED", raising=False)
    _put(save_eco, "eco-q", "strategy: quorum\necosystem:\n  name: eco-q\n")
    r = save_eco.deploy_state_machine("eco-q")
    assert r["statusCode"] == 400
    assert "Quorum" in json.loads(r["body"])["error"]


def test_deploy_quorum_registers_when_enabled(save_eco, monkeypatch):
    monkeypatch.setenv("QUORUM_ENABLED", "true")
    _put(save_eco, "eco-q", "strategy: quorum\necosystem:\n  name: eco-q\n")
    r = save_eco.deploy_state_machine("eco-q")
    b = json.loads(r["body"])
    assert b.get("deployed") is True and b.get("quorum") is True


def test_deploy_missing_eco_returns_400(save_eco):
    r = save_eco.deploy_state_machine("does-not-exist")
    assert r["statusCode"] == 400
