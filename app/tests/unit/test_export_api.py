"""Unit tests for the export-ecosystem Lambda (POST /api/ecosystems/{id}/export).

export-ecosystem/handler.py binds boto3 + reads ECOSYSTEMS_BUCKET / EXPORT_BUCKET at
import, so we set env + enter moto BEFORE importing it via the `export_api` fixture.
It runs the export toolchain (BoM analyze + Terraform generate) server-side and returns
a presigned download URL for the generated self-serve package.

These tests run entirely offline (moto for S3). The export inputs (export-inputs/*.zip)
are NOT published in the test bucket, so `complete` is False and the generator falls back
to the repo-vendored eco-lite module / runtime — the package still generates.
"""
import importlib.util
import json
import os
import sys
import zipfile
from io import BytesIO

import boto3
import pytest
from moto import mock_aws

_HANDLER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lambdas", "export-ecosystem", "handler.py")
)
ECO_BUCKET = "amzn-s3-demo-ecosystems-test"
EXPORT_BUCKET = "amzn-s3-demo-exports-test"

_ECO_YAML = (
    "ecosystem:\n"
    "  name: vendor-assessment\n"
    "  description: Assess a vendor.\n"
    "  version: '1.0'\n"
    "strategy: orchestrated\n"
    "agents:\n"
    "  - id: intake\n"
    "    name: Intake\n"
    "    depends_on: []\n"
    "    tools: [web_search]\n"
    "    prompt: |\n"
    "      Your specific contribution: gather the vendor facts.\n"
    "  - id: synth\n"
    "    name: Synthesizer\n"
    "    depends_on: [intake]\n"
    "    tools: [memory_read]\n"
    "    prompt: |\n"
    "      Your specific contribution: synthesize.\n"
    "outcome:\n"
    "  kind: report\n"
)


@pytest.fixture
def export_api(monkeypatch):
    monkeypatch.setenv("ECOSYSTEMS_BUCKET", ECO_BUCKET)
    monkeypatch.setenv("EXPORT_BUCKET", EXPORT_BUCKET)
    monkeypatch.setenv("EXPORT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=ECO_BUCKET)
        s3.create_bucket(Bucket=EXPORT_BUCKET)
        sys.modules.pop("handler_export_api", None)
        spec = importlib.util.spec_from_file_location("handler_export_api", _HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def _put_eco(mod, eco_id, yaml_text):
    mod.s3.put_object(Bucket=ECO_BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=yaml_text.encode())


def _auth_event(eco_id, sub="user-1", groups=""):
    return {
        "pathParameters": {"id": eco_id},
        "requestContext": {"authorizer": {"claims": {"sub": sub, "email": "u@example.com", "cognito:groups": groups}}},
    }


def _body(resp):
    return json.loads(resp["body"])


# ── Guards ────────────────────────────────────────────────────────────────────

def test_missing_id_returns_400(export_api):
    resp = export_api.handler({"pathParameters": {}}, None)
    assert resp["statusCode"] == 400


def test_unauthorized_returns_401(export_api):
    # No authorizer claims → require_auth raises → 401.
    resp = export_api.handler({"pathParameters": {"id": "vendor-assessment"}}, None)
    assert resp["statusCode"] == 401


def test_not_found_returns_404(export_api):
    resp = export_api.handler(_auth_event("does-not-exist"), None)
    assert resp["statusCode"] == 404


def test_no_agents_returns_400(export_api):
    _put_eco(export_api, "empty-eco", "ecosystem:\n  name: empty-eco\n")
    resp = export_api.handler(_auth_event("empty-eco"), None)
    assert resp["statusCode"] == 400


def test_non_owner_allowed_by_default(export_api):
    # Default policy is OPEN: any authenticated user can export another user's eco
    # (export is a read-only source package). Owner-restriction is opt-in via
    # EXPORT_REQUIRE_OWNER (see test_non_owner_forbidden_when_restricted).
    _put_eco(export_api, "owned-eco", _ECO_YAML + "owner: other-user\n")
    resp = export_api.handler(_auth_event("owned-eco", sub="user-1"), None)
    assert resp["statusCode"] == 200


def test_non_owner_forbidden_when_restricted(export_api, monkeypatch):
    # With EXPORT_REQUIRE_OWNER=true, a non-owner non-admin is blocked (403).
    monkeypatch.setenv("EXPORT_REQUIRE_OWNER", "true")
    _put_eco(export_api, "owned-eco", _ECO_YAML + "owner: other-user\n")
    resp = export_api.handler(_auth_event("owned-eco", sub="user-1"), None)
    assert resp["statusCode"] == 403


def test_admin_can_export_others_eco_when_restricted(export_api, monkeypatch):
    monkeypatch.setenv("EXPORT_REQUIRE_OWNER", "true")
    _put_eco(export_api, "owned-eco", _ECO_YAML + "owner: other-user\n")
    resp = export_api.handler(_auth_event("owned-eco", sub="admin-1", groups="admin"), None)
    assert resp["statusCode"] == 200


# ── Happy path ──────────────────────────────────────────────────────────────

def test_export_produces_downloadable_package(export_api):
    _put_eco(export_api, "vendor-assessment", _ECO_YAML)
    resp = export_api.handler(_auth_event("vendor-assessment"), None)
    assert resp["statusCode"] == 200, resp["body"]
    body = _body(resp)

    # Contract the frontend depends on.
    assert body["ecosystem_id"] == "vendor-assessment"
    assert body["download_url"].startswith("https://")
    assert body["expires_days"] == 7
    assert body["bytes"] > 0
    assert "bom" in body and body["bom"].get("strategy") == "orchestrated"
    # No export inputs were published, so the package is flagged incomplete.
    assert body["complete"] is False

    # The uploaded object is a valid, non-empty zip under exports/<user>/<eco>/.
    assert body["key"].startswith("exports/user-1/vendor-assessment/")
    obj = export_api.s3.get_object(Bucket=EXPORT_BUCKET, Key=body["key"])
    data = obj["Body"].read()
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
    # The generator always emits the Terraform root + the source definition.
    assert any(n == "main.tf" for n in names)
    assert any(n == "ecosystem.yaml" for n in names)
