"""Unit tests — cyber-security-risk catalog contract + ATT&CK ingestion (R.1).

No AWS required. Guards:
  - the record JSON Schema is meta-valid (draft 2020-12)
  - the schema's key patterns match the live domain taxonomy
  - the producer's normalize() emits schema-valid attack-technique records
  - entity-key slug derivation is stable

Run: pytest tests/unit/test_risk_catalog.py -q
"""
import json, os, importlib.util
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCHEMA_PATH = os.path.join(ROOT, "content", "domains", "schemas", "cyber-security-risk.records.json")
DOMAIN_PATH = os.path.join(ROOT, "content", "domains", "cyber-security-risk.yaml")
# NOTE: scripts/ingest_attack.py was not carried into the migrated layout; the
# _ingest_module()-dependent tests skip when it's absent (see _ingest_module).
INGEST_PATH = os.path.join(ROOT, "scripts", "ingest_attack.py")


def _load_schema():
    return json.load(open(SCHEMA_PATH))


def _ingest_module():
    if not os.path.exists(INGEST_PATH):
        pytest.skip("scripts/ingest_attack.py not present in this layout (not migrated)")
    spec = importlib.util.spec_from_file_location("ingest_attack", INGEST_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)   # pure funcs only; boto3 import is lazy in main()
    return m


# A representative STIX attack-pattern (shape mirrors enterprise-attack.json).
STIX_AP = {
    "type": "attack-pattern",
    "name": "Command and Scripting Interpreter: PowerShell",
    "description": "Adversaries may abuse PowerShell commands and scripts for execution.",
    "external_references": [
        {"source_name": "mitre-attack", "external_id": "T1059.001",
         "url": "https://attack.mitre.org/techniques/T1059/001"}
    ],
    "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}],
    "x_mitre_platforms": ["Windows"],
    "x_mitre_data_sources": ["Command: Command Execution"],
    "x_mitre_detection": "Monitor for PowerShell execution and module loading.",
}


def test_schema_is_meta_valid():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(_load_schema())


def test_schema_keys_match_domain():
    yaml = pytest.importorskip("yaml")
    schema = _load_schema()
    dom = yaml.safe_load(open(DOMAIN_PATH))
    dom_keys = {t["type"]: t["key"] for t in dom["taxonomy"]}
    for t, k in schema["x-tessera-keys"].items():
        assert t in dom_keys, f"{t} in schema but not domain taxonomy"
        assert k == dom_keys[t], f"key mismatch for {t}: schema {k} vs domain {dom_keys[t]}"


def test_every_keyed_type_has_a_def():
    schema = _load_schema()
    for t in schema["x-tessera-keys"]:
        assert t in schema["$defs"], f"no $def for keyed type {t}"


def test_normalize_produces_schema_valid_record():
    jsonschema = pytest.importorskip("jsonschema")
    m = _ingest_module()
    rec = m.normalize(STIX_AP, "2026-07-01")
    assert rec["technique_id"] == "T1059.001"
    assert rec["framework"] == "mitre-attack"
    assert rec["tactics"] == ["execution"]
    assert rec["provenance"]["refresh_date"] == "2026-07-01"
    schema = _load_schema()
    full = {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": schema["$defs"], "$ref": "#/$defs/attack-technique"}
    jsonschema.Draft202012Validator(full).validate(rec)   # raises on invalid


def test_normalize_skips_pattern_without_mitre_id():
    m = _ingest_module()
    assert m.normalize({"type": "attack-pattern", "name": "x", "external_references": []}, "2026-07-01") is None


def test_slug_is_stable_and_safe():
    m = _ingest_module()
    assert m.slug("T1059.001") == "t1059-001"
    assert m.slug("T1059.001") == m.slug("T1059.001")        # idempotent
    assert m.slug("  Heartland / 2008!! ") == "heartland-2008"  # safe namespace segment


def test_build_records_entity_keys_and_payload():
    m = _ingest_module()
    recs = [{"technique_id": "T1059.001", "name": "PowerShell"},
            {"technique_id": "T1003", "name": "OS Credential Dumping"}]
    out = m.build_records(recs, sid="strat-xyz")
    assert [r["namespaces"][0] for r in out] == [
        "/tessera/catalog/attack-technique/t1059-001",
        "/tessera/catalog/attack-technique/t1003",
    ]
    assert all(r["memoryStrategyId"] == "strat-xyz" for r in out)
    # content round-trips the record JSON
    assert json.loads(out[0]["content"]["text"])["name"] == "PowerShell"
    # request identifiers are unique
    assert len({r["requestIdentifier"] for r in out}) == 2
