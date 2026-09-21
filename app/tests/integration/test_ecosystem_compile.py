"""Integration: every shipped ecosystem compiles/loads correctly — the cross-component
seam between the ecosystem YAMLs (content/ecosystems/) and the compiler
(save-ecosystem.compile_to_asl) + the runtime graph loader (Orchestrator).

Offline: no AWS calls. compile_to_asl is pure (given the ARN env vars); the ASL it
produces must be a valid States-language shape referencing the platform Lambdas, and
every declared outcome artifact must point at a real producing agent.
"""
import glob
import importlib.util
import os
import sys

import pytest
import yaml

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ECO_DIR = os.path.join(_APP, "content", "ecosystems")
_SAVE_HANDLER = os.path.join(_APP, "lambdas", "save-ecosystem", "handler.py")

_INVOKER_ARN = "arn:aws:lambda:us-east-1:123456789012:function:tessera-dev-agent-invoker"


def _all_ecosystem_files():
    files = sorted(glob.glob(os.path.join(_ECO_DIR, "*.yaml")))
    # samples/ are illustrative fragments, not necessarily complete ecos — skip.
    return [f for f in files if os.path.isfile(f)]


def _load_save_ecosystem():
    """Import save-ecosystem/handler.py with the ARN env vars set (read at import)."""
    os.environ.setdefault("ECOSYSTEMS_BUCKET", "amzn-s3-demo-ecosystems-test")
    os.environ["AGENT_INVOKER_ARN"] = _INVOKER_ARN
    os.environ["GROUNDING_CHECK_ARN"] = "arn:aws:lambda:us-east-1:123456789012:function:tessera-dev-grounding-check"
    os.environ["EXTERNAL_GROUNDING_ARN"] = "arn:aws:lambda:us-east-1:123456789012:function:tessera-dev-external-grounding"
    sys.modules.pop("handler_compile", None)
    spec = importlib.util.spec_from_file_location("handler_compile", _SAVE_HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ECOSYSTEM_FILES = _all_ecosystem_files()


def test_there_are_ecosystems_to_check():
    assert ECOSYSTEM_FILES, "no ecosystem YAMLs found — the parametrized checks would be vacuous"


@pytest.mark.parametrize("eco_path", ECOSYSTEM_FILES, ids=lambda p: os.path.basename(p))
def test_ecosystem_compiles_or_is_runtime_assembled(eco_path):
    """Orchestrated ecos compile to a valid ASL referencing agent-invoker. Interactive
    and quorum ecos are assembled at runtime (no ASL) — those are exercised via
    deploy_state_machine elsewhere; here we just require they parse + name a strategy."""
    mod = _load_save_ecosystem()
    with open(eco_path) as f:
        config = yaml.safe_load(f)

    strat = config.get("strategy") or (config.get("ecosystem") or {}).get("strategy") or "orchestrated"
    if strat in ("interactive", "quorum"):
        assert config.get("agents"), f"{os.path.basename(eco_path)}: {strat} eco has no agents"
        return

    eco_id = os.path.splitext(os.path.basename(eco_path))[0]
    asl = mod.compile_to_asl(config, eco_id)

    # Valid States-language shape.
    assert isinstance(asl, dict)
    assert asl.get("StartAt") == "Start"
    assert isinstance(asl.get("States"), dict) and asl["States"], "empty States"

    # Somewhere in the compiled machine, the agent-invoker Lambda is the task target.
    import json as _json
    blob = _json.dumps(asl)
    assert _INVOKER_ARN in blob, f"{eco_id}: compiled ASL does not reference the agent-invoker"


@pytest.mark.parametrize("eco_path", ECOSYSTEM_FILES, ids=lambda p: os.path.basename(p))
def test_declared_outcome_artifacts_reference_real_agents(eco_path):
    """If an eco declares outcome.artifacts, each must name an agent that exists and
    (for file artifacts) match that agent's output_format.save_as. This is the exact
    check that made the report-open flow reliable for the HTML-report ecosystems."""
    with open(eco_path) as f:
        config = yaml.safe_load(f)

    eco_block = config.get("ecosystem") or {}
    outcome = eco_block.get("outcome") or config.get("outcome") or {}
    artifacts = (outcome or {}).get("artifacts") or []
    if not artifacts:
        pytest.skip("no declared outcome artifacts")

    agents = {a.get("id"): a for a in config.get("agents", [])}
    for art in artifacts:
        agent_id = art.get("agent")
        assert agent_id in agents, f"outcome artifact names unknown agent {agent_id!r}"
        of = agents[agent_id].get("output_format") or {}
        if art.get("save_as") and of.get("save_as"):
            assert of["save_as"] == art["save_as"], (
                f"artifact save_as {art['save_as']!r} != agent output_format save_as {of.get('save_as')!r}"
            )
