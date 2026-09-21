"""Unit tests for save-ecosystem's per-agent tool-budget resolution + the
research-tool backstop (a dependent research agent must not be silently zeroed).

Ported from Tessera-Org (Tier 1 quality fix). _HANDLER resolves to
app/lambdas/save-ecosystem/handler.py (parents[2] of this file == app/).
"""
import importlib.util
import os
from pathlib import Path

_HANDLER = Path(__file__).resolve().parents[2] / "lambdas" / "save-ecosystem" / "handler.py"


def _load():
    os.environ.setdefault("ECOSYSTEMS_BUCKET", "amzn-s3-demo-test-bucket")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("save_eco_budget_h", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mod = _load()


def test_dependent_research_agent_keeps_web_search_budget():
    # The bug: a research agent with depends_on was classified synthesizer -> 0.
    b = mod._resolve_agent_tool_budget(
        {"id": "technicals", "depends_on": ["scanner"],
         "tools": ["web_search", "memory_read"]})
    assert b["web_search"] == 6


def test_leaf_research_agent_web_search_budget():
    b = mod._resolve_agent_tool_budget(
        {"id": "scanner", "depends_on": [], "tools": ["web_search"]})
    assert b["web_search"] == 6


def test_output_kind_researcher_gets_budget():
    b = mod._resolve_agent_tool_budget(
        {"id": "t", "depends_on": ["s"], "tools": ["web_search"], "output_kind": "researcher"})
    assert b["web_search"] == 6


def test_explicit_zero_override_respected():
    # A deliberate opt-out via YAML still wins over the backstop.
    b = mod._resolve_agent_tool_budget(
        {"id": "t", "depends_on": ["s"], "tools": ["web_search"],
         "tool_budget": {"web_search": 0}})
    assert b["web_search"] == 0


def test_pure_synthesizer_without_web_search_unaffected():
    # No research tool listed -> no web_search key; other synth defaults intact.
    b = mod._resolve_agent_tool_budget(
        {"id": "y", "depends_on": ["t"], "tools": ["memory_read", "code_interpreter"]})
    assert "web_search" not in b
    assert b["code_interpreter"] == 8  # synthesizer code_interpreter default


def test_drafter_listing_web_search_not_promoted():
    # Backstop only corrects the synthesizer misclassification, not drafters.
    b = mod._resolve_agent_tool_budget(
        {"id": "d", "depends_on": ["s"], "tools": ["web_search", "code_interpreter"],
         "output_kind": "drafter"})
    assert b["web_search"] == 0
    assert b["code_interpreter"] == 20  # drafter code_interpreter default


def test_custom_gateway_tool_budget_preserved():
    # Operator budget for a non-platform tool is honored even without a default.
    b = mod._resolve_agent_tool_budget(
        {"id": "g", "depends_on": [], "tools": ["fair_value_model"],
         "tool_budget": {"fair_value_model": 4}})
    assert b["fair_value_model"] == 4
