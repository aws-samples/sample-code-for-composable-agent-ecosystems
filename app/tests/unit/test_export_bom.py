"""Unit tests for the export BoM analyzer (app/platform/export/bom.py). Pure/offline —
no AWS. Uses real shipped ecosystems (loaded via bom.load_config)."""
from tessera_platform.export import bom as _bom


def test_analyze_orchestrated_eco_shape():
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    assert b.eco_id == "agentic-ai-sirs"
    assert b.ok is True
    assert b.strategy == "orchestrated"
    assert b.orchestration == "step-functions-asl"
    assert b.agents_declared  # non-empty
    # always-ships run-plane lambdas are present
    for name in ("agent-invoker", "websocket", "runs-api", "run-artifact", "get-ecosystem"):
        assert name in b.lambdas.ships


def test_web_search_eco_flags_external_grounding_and_region_gate():
    # risk-assessment has agents that list web_search.
    cfg = _bom.load_config("risk-assessment")
    b = _bom.analyze(cfg, region="us-east-1")
    if "web_search" in b.tools.platform_inline:
        assert "external-grounding" in b.lambdas.ships
        # us-east-1 is a supported web-search region → no region gate
        assert "agentcore-web-search" not in b.region_gate.unavailable


def test_web_search_region_gate_off_region():
    cfg = _bom.load_config("risk-assessment")
    b = _bom.analyze(cfg, region="ap-southeast-2")  # NOT a web-search region
    if "web_search" in b.tools.platform_inline:
        assert "agentcore-web-search" in b.region_gate.unavailable
        assert any("web_search" in w for w in b.warnings)


def test_business_tools_unverified_offline():
    # hedge-fund-trading-desk uses many custom (business) tools; offline they're unverified.
    cfg = _bom.load_config("hedge-fund-trading-desk")
    b = _bom.analyze(cfg, region="us-east-1")
    if b.tools.business:
        assert set(b.tools.unverified) == set(b.tools.business)
        # unverified is a warning, not a hard error (offline)
        assert b.ok is True


def test_unregistered_business_tool_fails_closed():
    # With a live registered set that EXCLUDES a declared business tool -> error.
    cfg = _bom.load_config("hedge-fund-trading-desk")
    b = _bom.analyze(cfg, region="us-east-1", registered_tools=set())  # nothing registered
    if b.tools.business:
        assert b.tools.unregistered  # the business tools are unregistered
        assert b.ok is False
        assert any("unregistered" in e for e in b.errors)


def test_no_agents_is_error():
    b = _bom.analyze({"ecosystem": {"name": "empty"}, "agents": []})
    assert b.ok is False
    assert any("no agents" in e for e in b.errors)


def test_to_dict_roundtrips_and_reports():
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg)
    d = b.to_dict()
    assert d["eco_id"] == "agentic-ai-sirs" and d["ok"] is True
    report = _bom.render_report(b)
    assert "agentic-ai-sirs" in report and "[OK]" in report


def test_resolve_path_missing_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        _bom.resolve_path("this-eco-does-not-exist")
