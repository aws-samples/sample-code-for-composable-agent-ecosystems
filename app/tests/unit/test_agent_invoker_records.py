"""Unit tests for agent-invoker multi-record catalog extraction helpers."""
import importlib.util, os, sys, types

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "agent-invoker", "handler.py"))


def _mod():
    # agent-invoker/handler.py imports boto3 + env at module scope; stub what's needed.
    for k in ("TESSERA_MEMORY_ID", "TESSERA_AGENT_RUNTIME_ARN", "TESSERA_RUNS_BUCKET",
              "WS_ENDPOINT", "CONNECTIONS_TABLE", "GROUNDING_CHECK_ARN",
              "EXTERNAL_GROUNDING_ARN", "AWS_REGION"):
        os.environ.setdefault(k, "test")
    # Force a boto3 stub for the handler import: the handler builds real clients
    # (e.g. boto3.client("bedrock-agentcore")) at module scope, and the pinned boto3
    # doesn't know that newer service. These tests only exercise pure helpers, so a
    # MagicMock boto3 is sufficient. Swap it in for the import, then restore.
    from unittest.mock import MagicMock
    _real_boto3 = sys.modules.get("boto3")
    b = types.ModuleType("boto3")
    b.client = lambda *a, **k: MagicMock()
    b.resource = lambda *a, **k: MagicMock()
    sys.modules["boto3"] = b
    try:
        spec = importlib.util.spec_from_file_location("agent_invoker_h", _HANDLER)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        if _real_boto3 is not None:
            sys.modules["boto3"] = _real_boto3
        else:
            sys.modules.pop("boto3", None)
    return m


def test_extract_json_records_objects_and_arrays():
    m = _mod()
    out = (
        "## STRUCTURED FINDINGS\n- claim: two risks\n\n"
        "```json\n{\"risk_id\": \"RSK-EXEC-001\", \"title\": \"Exec Hijack\"}\n```\n"
        "some prose\n"
        "```json\n[{\"risk_id\": \"RSK-CRED-002\"}, {\"risk_id\": \"RSK-EVA-003\"}]\n```\n"
    )
    recs = m._extract_json_records(out)
    assert [r["risk_id"] for r in recs] == ["RSK-EXEC-001", "RSK-CRED-002", "RSK-EVA-003"]


def test_extract_json_records_ignores_malformed():
    m = _mod()
    out = "```json\n{not valid json}\n```\n```json\n{\"risk_id\": \"RSK-1\"}\n```"
    recs = m._extract_json_records(out)
    assert [r["risk_id"] for r in recs] == ["RSK-1"]


def test_record_key_resolves_and_slugifies():
    m = _mod()
    assert m._record_key({"risk_id": "RSK-EXEC-001"}) == "rsk-exec-001"
    assert m._record_key({"technique_id": "T1078.004"}) == "t1078-004"
    assert m._record_key({"title": "no id here"}) == ""   # empty-key guard input


def test_extract_json_records_empty_when_none():
    m = _mod()
    assert m._extract_json_records("just prose, no fences") == []


def test_record_key_agent_mode_fields():
    m = _mod()
    # agent-ingested taxonomies key on threat_id / risk_type (must match fetch-mode)
    assert m._record_key({"threat_id": "LLM01"}) == "llm01"
    assert m._record_key({"risk_type": "ai-safe"}) == "ai-safe"
    assert m._record_key({"breach_key": "VCDB-9"}) == "vcdb-9"


def test_mission_field_parses_produces():
    m = _mod()
    mission = "source: owasp\nproduces: agentic-threat\nkey_field: threat_id\n"
    assert m._mission_field(mission, "produces") == "agentic-threat"
    assert m._mission_field(mission, "source") == "owasp"
    assert m._mission_field(mission, "missing") == ""


def test_catalog_type_templates_namespace():
    m = _mod()
    tv = m._template_vars(session_id="catalog-agent-ingest-abcd1234", agent_id="catalog_ingestor",
                          catalog_type="agentic-threat")
    assert tv["catalog_type"] == "agentic-threat"
    assert m._substitute_template("/catalog/{catalog_type}/", tv) == "/catalog/agentic-threat/"
