"""Builder eval harness — regression suite for the conversational builder.

Two layers:

1. DETERMINISTIC (always runs, no Bedrock): given an agreed DesignState, assert
   the builder's compile contract (`_compile_design_to_yaml` + `_extract_*`)
   produces the expected ecosystem SHAPE — deliverable format/cardinality,
   drafter presence, quality posture. This catches builder-output regressions
   without paying for / flaking on model calls.

2. LIVE (opt-in via TESSERA_BUILDER_LIVE=1): runs `converse()` against Bedrock
   for a few canonical prompts and checks the resulting design shape. Skipped by
   default so CI stays fast/cheap.

Post-generation edit capture: `record_post_gen_edit()` writes the diff between
what the builder produced and what the operator finally saved into
tests/builder_eval/captured_edits/ — a quality signal for future prompt tuning.
"""
import os
import json
import importlib.util
from pathlib import Path

import pytest

_HANDLER = Path(__file__).resolve().parents[2] / "lambdas" / "suggest-ecosystem" / "handler.py"


def _load_builder():
    os.environ.setdefault("ECOSYSTEMS_BUCKET", "amzn-s3-demo-test-bucket")
    spec = importlib.util.spec_from_file_location("suggest_eco_builder", _HANDLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


builder = _load_builder()


# ── Deterministic compile-contract cases ────────────────────────────────────

def _bundle_design():
    return {
        "intent": {"name": "kiro-spec-forge", "goal": "Kiro steering docs"},
        "agents": [
            {"id": "researcher", "name": "R", "swarm": "research", "depends_on": [], "tools": ["web_search"], "prompt": "research"},
            {"id": "drafter", "name": "D", "swarm": "synthesis", "depends_on": ["researcher"],
             "tools": ["code_interpreter"], "prompt": "draft", "output_kind": "drafter",
             "output_format": {"type": "bundle", "save_as": "kiro.zip", "validate": True}},
        ],
        "outcome": {"kind": "file_artifact", "artifacts": [
            {"id": "b", "agent": "drafter", "format": "bundle", "save_as": "kiro.zip", "download_label": "Bundle"}]},
        "quality": {"challenger": "per_level", "block_below": "ADEQUATE", "grounding": True,
                    "gates": {"citation": "ask", "format": "ask", "grounding": "ask", "output_kind": "ask"}},
    }


def _report_design():
    return {
        "intent": {"name": "debate", "goal": "Synthesize a debate"},
        "agents": [{"id": "a", "name": "A", "swarm": "research", "depends_on": [], "tools": [], "prompt": "x"}],
        "outcome": {"kind": "report"},
        "quality": {"challenger": "final", "grounding": True},
    }


def test_bundle_deliverable_shape():
    import yaml
    eco = yaml.safe_load(builder._compile_design_to_yaml(_bundle_design()))
    out = eco["outcome"]
    assert out["kind"] == "file_artifact"
    assert out["artifacts"][0]["format"] == "bundle"
    assert out["artifacts"][0]["save_as"].endswith(".zip")
    drafter = [a for a in eco["agents"] if a["id"] == "drafter"][0]
    assert drafter["output_format"]["type"] == "bundle"
    # producer/outcome format must match (compiler invariant the validator enforces)
    assert drafter["output_format"]["save_as"] == out["artifacts"][0]["save_as"]
    assert "code_interpreter" in drafter["tools"]


def test_per_level_quality_block():
    import yaml
    eco = yaml.safe_load(builder._compile_design_to_yaml(_bundle_design()))
    q = eco["quality"]
    assert q["challenger"] == "per_level"
    assert q["challenger_gate"]["block_below"] == "ADEQUATE"
    assert q["gates"]["citation"] == "ask"


def test_report_only_has_no_drafter():
    import yaml
    eco = yaml.safe_load(builder._compile_design_to_yaml(_report_design()))
    assert eco["outcome"]["kind"] == "report"
    assert all(a.get("output_kind") != "drafter" for a in eco["agents"])


def test_unsupported_format_is_clamped():
    import yaml
    ds = _bundle_design()
    ds["outcome"]["artifacts"][0]["format"] = "docx-bundle-xyz"  # bogus
    ds["agents"][1]["output_format"]["type"] = "docx-bundle-xyz"
    eco = yaml.safe_load(builder._compile_design_to_yaml(ds))
    assert eco["outcome"]["artifacts"][0]["format"] in {"csv", "docx", "html", "json", "markdown", "pdf", "png", "yaml", "bundle"}


def test_extract_quality_normalizes_legacy_bool():
    assert builder._extract_quality({"quality": {"challenger": True}})["challenger"] == "final"
    assert builder._extract_quality({"quality": {"challenger": False}})["challenger"] == "off"
    assert builder._extract_quality({})["challenger"] == "per_level"


# ── Live eval (opt-in) ───────────────────────────────────────────────────────

LIVE_CASES = [
    {"prompt": "Produce Kiro steering docs that enforce our AWS ASH scanner findings",
     "expect": {"first_turn_no_agents": True}},
]


@pytest.mark.skipif(os.environ.get("TESSERA_BUILDER_LIVE") != "1", reason="live builder eval is opt-in")
@pytest.mark.parametrize("case", LIVE_CASES)
def test_live_first_turn_is_chat_first(case):
    turn = builder.converse([], case["prompt"], {})
    # Chat-first: the opening turn must NOT dump a full agent design.
    if case["expect"].get("first_turn_no_agents"):
        assert not (turn.get("design") or {}).get("agents"), "first turn should ask, not design"
    assert turn.get("reply"), "must produce a reply"


# ── Post-generation edit capture ─────────────────────────────────────────────

def record_post_gen_edit(eco_id: str, generated_yaml: str, saved_yaml: str):
    """Persist the builder-vs-final diff as a quality signal for prompt tuning.
    Called opportunistically (e.g. from a save hook); safe no-op on error."""
    try:
        out_dir = Path(__file__).resolve().parent / "captured_edits"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{eco_id}.json").write_text(json.dumps({
            "eco_id": eco_id, "generated": generated_yaml, "saved": saved_yaml,
            "changed": generated_yaml.strip() != saved_yaml.strip(),
        }, indent=2))
    except Exception:
        pass
