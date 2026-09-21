"""Unit tests for the shared auth module (app/lambdas/auth.py).

Pure — no AWS. `auth` is importable because the test conftest adds app/lambdas to
sys.path (it's shipped in the Lambda shared layer under that name).
"""
import json

import pytest

import auth


def _event(claims):
    return {"requestContext": {"authorizer": {"claims": claims}}}


# ── get_user_from_event ───────────────────────────────────────────────────────

def test_get_user_parses_sub_email_and_group_string():
    u = auth.get_user_from_event(_event({
        "sub": "u-123",
        "email": "a@b.co",
        "cognito:groups": "admin, editors ,  ",
    }))
    assert u["user_id"] == "u-123"
    assert u["email"] == "a@b.co"
    # comma-separated string is split, trimmed, and empties dropped
    assert u["groups"] == ["admin", "editors"]


def test_get_user_accepts_group_list():
    u = auth.get_user_from_event(_event({"sub": "u-1", "cognito:groups": ["admin", "x"]}))
    assert u["groups"] == ["admin", "x"]


def test_get_user_no_groups_defaults_empty():
    u = auth.get_user_from_event(_event({"sub": "u-1"}))
    assert u["groups"] == []
    assert u["email"] == ""


def test_get_user_missing_claims_returns_empty_identity():
    assert auth.get_user_from_event({}) == {"user_id": "", "email": "", "groups": []}


def test_get_user_missing_sub_returns_empty_identity():
    u = auth.get_user_from_event(_event({"email": "a@b.co"}))
    assert u["user_id"] == ""


# ── is_admin ──────────────────────────────────────────────────────────────────

def test_is_admin_true_when_in_admin_group():
    assert auth.is_admin({"groups": ["admin"]}) is True


def test_is_admin_false_without_admin_group():
    assert auth.is_admin({"groups": ["editors"]}) is False
    assert auth.is_admin({}) is False


# ── require_auth ──────────────────────────────────────────────────────────────

def test_require_auth_returns_user_when_authenticated():
    u = auth.require_auth(_event({"sub": "u-9", "email": "x@y.z"}))
    assert u["user_id"] == "u-9"


def test_require_auth_raises_when_unauthenticated():
    with pytest.raises(PermissionError):
        auth.require_auth({})


# ── auth_error_response ───────────────────────────────────────────────────────

def test_auth_error_response_defaults_to_401():
    r = auth.auth_error_response()
    assert r["statusCode"] == 401
    assert json.loads(r["body"]) == {"message": "Unauthorized"}
    assert "Access-Control-Allow-Origin" in r["headers"]


def test_auth_error_response_custom_status_and_message():
    r = auth.auth_error_response(403, "Forbidden")
    assert r["statusCode"] == 403
    assert json.loads(r["body"])["message"] == "Forbidden"


def test_auth_error_response_uses_cors_origin_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGIN", "https://example.test")
    r = auth.auth_error_response()
    assert r["headers"]["Access-Control-Allow-Origin"] == "https://example.test"
