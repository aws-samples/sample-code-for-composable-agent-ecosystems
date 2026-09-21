"""Integration: the report-open seam end-to-end (offline) — a REAL shipped ecosystem's
declared outcome (from content/ecosystems/) resolved by runs-api against produced
artifacts in S3. Cross-component: eco YAML (declared) + S3 (produced) -> runs-api status.

This is the exact flow the frontend's openReport() depends on. It uses the shipped
agentic-ai-sirs ecosystem (two declared HTML artifacts) rather than a synthetic fixture,
so a drift between the YAML and the resolver is caught.
"""
import importlib.util
import os
import sys

import boto3
import pytest
from moto import mock_aws

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_RUNS_API = os.path.join(_APP, "lambdas", "runs-api", "handler.py")
_SIRS_YAML = os.path.join(_APP, "content", "ecosystems", "agentic-ai-sirs.yaml")

ECO_BUCKET = "amzn-s3-demo-ecosystems-it"
RUNS_BUCKET = "amzn-s3-demo-runs-it"
ECO_ID = "agentic-ai-sirs"

# The two declared artifacts (agent, save_as) — kept in sync with the shipped YAML.
ARTIFACTS = [
    ("workshop_assembler", "sirs-execution-plan.html"),
    ("presentation_drafter", "sirs-workshop-deck.html"),
]


@pytest.fixture
def runs_api(monkeypatch):
    monkeypatch.setenv("ECOSYSTEMS_BUCKET", ECO_BUCKET)
    monkeypatch.setenv("TESSERA_RUNS_BUCKET", RUNS_BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=ECO_BUCKET)
        s3.create_bucket(Bucket=RUNS_BUCKET)
        # Seed the REAL shipped ecosystem YAML into the ecosystems bucket.
        with open(_SIRS_YAML, "rb") as f:
            s3.put_object(Bucket=ECO_BUCKET, Key=f"ecosystems/{ECO_ID}.yaml", Body=f.read())
        sys.modules.pop("handler_runs_api_it", None)
        spec = importlib.util.spec_from_file_location("handler_runs_api_it", _RUNS_API)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


def _seed_artifact(mod, sid, agent, save_as):
    mod.s3.put_object(
        Bucket=RUNS_BUCKET,
        Key=f"runs/{sid}/{agent}/{save_as}",
        Body=b"<html><body>report</body></html>",
    )


def test_declared_matches_shipped_yaml(runs_api):
    """Guard: the ARTIFACTS this test seeds match what the shipped YAML declares.
    If the YAML's outcome changes, this fails first (a clear signal, not a mystery)."""
    out = runs_api._resolve_outcome(ECO_ID, f"{ECO_ID}-none")
    declared = {(a["agent"], a["save_as"]) for a in out["declared_artifacts"]}
    assert declared == set(ARTIFACTS)


def test_outcome_complete_when_all_artifacts_produced(runs_api):
    sid = f"{ECO_ID}-complete"
    for agent, save_as in ARTIFACTS:
        _seed_artifact(runs_api, sid, agent, save_as)
    out = runs_api._resolve_outcome(ECO_ID, sid)
    assert out["status"] == "complete"
    assert out["missing_artifacts"] == []
    assert len(out["produced_artifacts"]) == len(ARTIFACTS)


def test_outcome_partial_when_one_missing(runs_api):
    sid = f"{ECO_ID}-partial"
    agent, save_as = ARTIFACTS[0]
    _seed_artifact(runs_api, sid, agent, save_as)  # seed only the first
    out = runs_api._resolve_outcome(ECO_ID, sid)
    assert out["status"] == "partial"
    assert len(out["produced_artifacts"]) == 1
    assert len(out["missing_artifacts"]) == 1


def test_outcome_empty_when_nothing_produced(runs_api):
    out = runs_api._resolve_outcome(ECO_ID, f"{ECO_ID}-empty")
    assert out["status"] == "empty"
    assert out["produced_artifacts"] == []
