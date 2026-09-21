"""Unit tests for runs-api — the outcome/artifact resolution that drives report-open,
ecosystem-owner lookup, and guidance CRUD.

runs-api/handler.py binds boto3 clients + reads TESSERA_RUNS_BUCKET / ECOSYSTEMS_BUCKET
at import, so we set env + enter moto BEFORE importing it via the `runs_api` fixture.
"""
import importlib.util
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

_HANDLER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lambdas", "runs-api", "handler.py")
)
ECO_BUCKET = "amzn-s3-demo-ecosystems-test"
RUNS_BUCKET = "amzn-s3-demo-runs-test"


@pytest.fixture
def runs_api(monkeypatch):
    monkeypatch.setenv("ECOSYSTEMS_BUCKET", ECO_BUCKET)
    monkeypatch.setenv("TESSERA_RUNS_BUCKET", RUNS_BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=ECO_BUCKET)
        s3.create_bucket(Bucket=RUNS_BUCKET)
        sys.modules.pop("handler_runs_api", None)
        spec = importlib.util.spec_from_file_location("handler_runs_api", _HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def _put_eco(mod, eco_id, yaml_text):
    mod.s3.put_object(Bucket=ECO_BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=yaml_text.encode())


def _put_artifact(mod, session_id, agent, save_as, body=b"<html></html>"):
    mod.s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent}/{save_as}", Body=body)


_ECO_WITH_HTML = (
    "ecosystem:\n  name: eco-html\n"
    "outcome:\n"
    "  kind: file_artifact\n"
    "  artifacts:\n"
    "  - id: report_html\n"
    "    agent: composer\n"
    "    format: html\n"
    "    save_as: report.html\n"
)


# ── _resolve_outcome (report-open resolution) ─────────────────────────────────

def test_outcome_none_when_no_declaration(runs_api):
    _put_eco(runs_api, "eco-plain", "ecosystem:\n  name: eco-plain\n")
    out = runs_api._resolve_outcome("eco-plain", "eco-plain-abc123")
    assert out["status"] == "none"
    assert out["declared_artifacts"] == [] and out["produced_artifacts"] == []


def test_outcome_complete_when_artifact_produced(runs_api):
    _put_eco(runs_api, "eco-html", _ECO_WITH_HTML)
    sid = "eco-html-abc123"
    _put_artifact(runs_api, sid, "composer", "report.html")
    out = runs_api._resolve_outcome("eco-html", sid)
    assert out["status"] == "complete"
    assert out["produced_artifacts"] == ["report_html"]
    assert out["missing_artifacts"] == []
    # declared summary carries the artifact_key the frontend opens
    assert out["declared_artifacts"][0]["artifact_key"] == "composer/report.html"


def test_outcome_empty_when_artifact_missing(runs_api):
    _put_eco(runs_api, "eco-html", _ECO_WITH_HTML)
    out = runs_api._resolve_outcome("eco-html", "eco-html-nofile")
    assert out["status"] == "empty"
    assert out["missing_artifacts"] == ["report_html"]
    assert out["produced_artifacts"] == []


def test_outcome_partial_when_some_produced(runs_api):
    two = _ECO_WITH_HTML + (
        "  - id: deck_html\n"
        "    agent: drafter\n"
        "    format: html\n"
        "    save_as: deck.html\n"
    )
    _put_eco(runs_api, "eco-two", two)
    sid = "eco-two-abc"
    _put_artifact(runs_api, sid, "composer", "report.html")  # only one of two produced
    out = runs_api._resolve_outcome("eco-two", sid)
    assert out["status"] == "partial"
    assert set(out["produced_artifacts"]) == {"report_html"}
    assert set(out["missing_artifacts"]) == {"deck_html"}


# ── _get_ecosystem_owner ──────────────────────────────────────────────────────

def test_get_ecosystem_owner_reads_owner(runs_api):
    _put_eco(runs_api, "eco-o", "ecosystem:\n  name: eco-o\nowner: owner-123\n")
    assert runs_api._get_ecosystem_owner("eco-o") == "owner-123"


def test_get_ecosystem_owner_empty_when_absent_or_missing(runs_api):
    _put_eco(runs_api, "eco-n", "ecosystem:\n  name: eco-n\n")
    assert runs_api._get_ecosystem_owner("eco-n") == ""
    assert runs_api._get_ecosystem_owner("does-not-exist") == ""


# ── guidance CRUD ─────────────────────────────────────────────────────────────

def test_add_guidance_rejects_empty_text(runs_api):
    r = runs_api._add_guidance("eco-g", {"text": "  "}, {"user_id": "u1"})
    assert r["statusCode"] == 400


def test_add_guidance_roundtrip(runs_api):
    r = runs_api._add_guidance("eco-g", {"text": "Prefer concise summaries"}, {"user_id": "u1"})
    assert r["statusCode"] == 201
    entry = json.loads(r["body"])["guidance"]
    assert entry["text"] == "Prefer concise summaries"
    assert entry["created_by"] == "u1" and entry["enabled"] is True
    # read back
    g = runs_api._read_guidance("eco-g")
    assert [e["text"] for e in g["entries"]] == ["Prefer concise summaries"]


def test_read_guidance_empty_default(runs_api):
    g = runs_api._read_guidance("eco-never-written")
    assert g["entries"] == [] and g["ecosystem_id"] == "eco-never-written"
