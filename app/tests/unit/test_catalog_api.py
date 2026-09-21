"""Unit tests for catalog-api pure helpers (no boto3, no network)."""
import importlib.util, os, sys, types

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "catalog-api", "handler.py"))


def _mod():
    os.environ.setdefault("TESSERA_MEMORY_ID", "test")
    os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
    if "boto3" not in sys.modules:
        b = types.ModuleType("boto3"); b.client = lambda *a, **k: None
        sys.modules["boto3"] = b
    spec = importlib.util.spec_from_file_location("catalog_api", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_parse_route():
    m = _mod()
    assert m.parse_route("/catalog") == []
    assert m.parse_route("/catalog/types") == ["types"]
    assert m.parse_route("/catalog/risk") == ["risk"]
    assert m.parse_route("/catalog/attack-technique/t1078") == ["attack-technique", "t1078"]
    assert m.parse_route("/") == []


def test_record_from_summary_parses_json_and_attaches_ns():
    m = _mod()
    s = {"namespaces": ["/tessera/catalog/risk/rsk-cred-001"],
         "content": {"text": '{"risk_id": "RSK-CRED-001", "severity_default": "HIGH"}'}}
    r = m.record_from_summary(s)
    assert r["risk_id"] == "RSK-CRED-001"
    assert r["_ns"] == "/tessera/catalog/risk/rsk-cred-001"


def test_record_from_summary_handles_nonjson():
    m = _mod()
    r = m.record_from_summary({"namespaces": ["x"], "content": {"text": "not json"}})
    assert r["_raw"] == "not json"
    assert r["_ns"] == "x"


def test_filter_records_severity_tactic_q():
    m = _mod()
    recs = [
        {"risk_id": "R1", "severity_default": "CRITICAL", "title": "AD CS abuse"},
        {"risk_id": "R2", "severity_default": "HIGH", "title": "Kerberoasting"},
        {"technique_id": "T1", "tactics": ["credential-access"], "name": "x"},
        {"technique_id": "T2", "tactics": ["execution"], "name": "y"},
    ]
    assert [r.get("risk_id") for r in m.filter_records(recs, severity="critical")] == ["R1"]
    assert [r.get("technique_id") for r in m.filter_records(recs, tactic="Execution")] == ["T2"]
    assert [r.get("risk_id") for r in m.filter_records(recs, q="kerber")] == ["R2"]


def test_cors_headers_present():
    m = _mod()
    resp = m._cors(200, {"ok": True})
    assert resp["headers"]["content-type"] == "application/json"
    assert resp["statusCode"] == 200


def test_agent_mission_builds_input_context():
    m = _mod()
    src = {"id": "owasp", "authoritative_url": "https://genai.owasp.org/llm-top-10/",
           "produces": ["agentic-threat"], "key_field": "threat_id",
           "id_scheme": "LLM01..LLM10", "scope": "OWASP LLM + ASI."}
    mission = m._agent_mission(src)
    assert "source: owasp" in mission
    assert "produces: agentic-threat" in mission          # parsed by agent-invoker for the write type
    assert "authoritative_url: https://genai.owasp.org/llm-top-10/" in mission
    assert "key_field: threat_id" in mission
