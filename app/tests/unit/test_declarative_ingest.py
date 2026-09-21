"""Tests for the declarative ingest framework (docs/spec-declarative-ingest.md).

Covers: shared builders parity vs the handler; connector parsers; descriptor
validation; SourceAdapter (map/builder); agentic normalizer + schema validation;
generic orchestrator with fakes; and the risk domain running through its DECLARATIVE
descriptor at parity.
"""
import importlib.util
import os

import pytest

from tessera_platform.catalog import (
    formats, connectors, descriptor as desc_mod, normalize, SourceAdapter, orchestrate,
)

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "catalog-ingest", "handler.py"))
_DOMAINS = os.path.abspath(os.path.join(_HERE, "..", "..", "content", "domains"))


def _handler():
    spec = importlib.util.spec_from_file_location("catalog_ingest", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── shared builders parity vs the original handler builders ──────────────────────
def test_formats_builders_match_handler():
    h = _handler()
    kev = {"vulnerabilities": [{"cveID": "CVE-1", "vulnerabilityName": "n", "knownRansomwareCampaignUse": "Known"}]}
    assert formats.build_kev_records(kev, today="2026-07-01") == h.build_kev_records(kev, today="2026-07-01")

    attack = [
        {"type": "attack-pattern", "id": "attack-pattern--1", "name": "Phishing",
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1566", "url": "u"}],
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "initial-access"}]},
        {"type": "course-of-action", "id": "course-of-action--1", "name": "Training",
         "external_references": [{"source_name": "mitre-attack", "external_id": "M1017", "url": "u2"}]},
        {"type": "relationship", "relationship_type": "mitigates",
         "source_ref": "course-of-action--1", "target_ref": "attack-pattern--1"},
    ]
    assert formats.build_attack_records(attack, today="2026-07-01") == h.build_attack_records(attack, today="2026-07-01")

    owasp = formats.build_owasp(today="2026-07-02")
    assert owasp == h.build_owasp(today="2026-07-02")
    assert formats.build_nist(today="2026-07-02") == h.build_nist(today="2026-07-02")

    oscal = {"catalog": {"groups": [{"id": "ac", "title": "Access Control", "controls": [
        {"id": "ac-1", "title": "P", "parts": [{"name": "statement", "prose": "x"}]}]}]}}
    assert formats.build_nist80053(oscal, today="2026-07-02") == h.build_nist80053(oscal, today="2026-07-02")

    atlas = {"matrices": [{"id": "ATLAS", "tactics": [{"id": "T", "name": "Recon"}],
             "techniques": [{"id": "AML.T0043", "name": "x", "tactics": ["T"]}]}]}
    assert formats.build_atlas(atlas, today="2026-07-02") == h.build_atlas(atlas, today="2026-07-02")

    veris = [{"plus": {"master_id": "A"}, "victim": {"victim_id": "Acme"}, "timeline": {"incident": {"year": 2024}}}]
    assert formats.build_veris(veris, today="2026-07-02") == h.build_veris(veris, today="2026-07-02")

    wiz = [{"title": "S3", "slug": "s3", "cves": [], "affectedServices": ["S3"], "knownITWExploitation": True}]
    assert formats.build_wiz(wiz, today="2026-07-02") == h.build_wiz(wiz, today="2026-07-02")


# ── connector parsers (pure, offline) ────────────────────────────────────────────
def test_connector_parsers():
    assert connectors.parse_json(b'{"a":1}') == {"a": 1}
    assert connectors.parse_csv("x,y\n1,2\n3,4") == {"rows": [{"x": "1", "y": "2"}, {"x": "3", "y": "4"}]}
    assert connectors.parse_yaml("a: 1\nb: [2,3]") == {"a": 1, "b": [2, 3]}
    html = connectors.parse_html("<html><body><h1>Hi</h1><script>x=1</script><p>There</p></body></html>", url="u")
    assert html["url"] == "u" and "Hi There" in html["text"] and "x=1" not in html["text"]


def test_connector_static_no_network():
    assert connectors.fetch({"transport": "static", "data": {"k": 1}}) == {"k": 1}


def test_connector_unknown_transport_raises():
    with pytest.raises(ValueError):
        connectors.fetch({"transport": "smoke-signals"})


# ── descriptor validation ─────────────────────────────────────────────────────────
def test_descriptor_validates_and_lists_sources():
    d = desc_mod.from_dict("demo", {"sources": [
        {"name": "s1", "produces": ["thing"], "fetch": {"transport": "static", "data": {}},
         "normalize": {"mode": "builder", "builder": "owasp"}},
    ]})
    assert d.source_names() == ["s1"] and d.needs_ingest()


def test_descriptor_rejects_bad_specs():
    with pytest.raises(ValueError):
        desc_mod.from_dict("demo", {"sources": [{"produces": ["x"], "normalize": {"mode": "map"}}]})  # no name
    with pytest.raises(ValueError):
        desc_mod.from_dict("demo", {"sources": [{"name": "a", "normalize": {"mode": "map"}}]})  # no produces
    with pytest.raises(ValueError):
        desc_mod.from_dict("demo", {"sources": [{"name": "a", "produces": ["x"], "normalize": {"mode": "spooky"}}]})
    with pytest.raises(ValueError):
        desc_mod.from_dict("demo", {"sources": [{"name": "a", "produces": ["x"], "normalize": {"mode": "map"}}]})  # map needs entity_type


# ── SourceAdapter: map + builder ──────────────────────────────────────────────────
def test_adapter_map_mode():
    spec = {"name": "kevmap", "produces": ["vulnerability"], "fetch": {"transport": "static"},
            "normalize": {"mode": "map", "entity_type": "vulnerability", "key_field": "cve_or_ref",
                          "select": "vulnerabilities[?cveID]", "fields": {"cve_or_ref": "cveID"},
                          "provenance": {"ingested_by": "x", "sources": ["cisa-kev"]}}}
    a = SourceAdapter(spec)
    raw = {"vulnerabilities": [{"cveID": "CVE-9"}, {"noid": 1}]}
    got = a.build(raw, "2026-07-01")
    assert got == [("vulnerability", "cve_or_ref",
                    {"cve_or_ref": "CVE-9", "provenance": {"ingested_by": "x", "sources": ["cisa-kev"], "refresh_date": "2026-07-01"}})]


def test_adapter_builder_static_needs_no_fetch():
    spec = {"name": "owasp", "produces": ["agentic-threat"], "fetch": {"transport": "static"},
            "normalize": {"mode": "builder", "builder": "owasp"}}
    a = SourceAdapter(spec)
    assert a.needs_fetch is False
    got = a.build(a.fetch(), "2026-07-02")
    assert len(got) == 20 and got[0][0] == "agentic-threat"


# ── agentic normalizer + schema validation ───────────────────────────────────────
def test_agent_normalizer_validates_and_keys_and_drops_bad():
    schema = {"$defs": {"regulatory-figure": {
        "type": "object",
        "properties": {"measure": {"type": "string"}, "fy": {"type": "string"}, "value": {"type": "string"}},
        "required": ["measure", "fy"]}},
        "x-tessera-keys": {"regulatory-figure": "{measure}-{fy}"}}
    spec = {"entity_type": "regulatory-figure", "key": "{measure}-{fy}", "key_field": "figure_key",
            "guidance": "x", "provenance": {"ingested_by": "wiz-agent", "sources": ["ato"]}}

    def fake_model(system, payload):
        return '{"records": [{"measure": "concessional-cap", "fy": "2026-27", "value": "$32,500"},' \
               ' {"value": "orphan-no-required-fields"}]}'

    an = normalize.AgentNormalizer(spec, schema, model=fake_model)
    out = an.build("<source>", "2026-07-07")
    assert len(out) == 1                                   # the invalid record is dropped
    etype, kf, rec = out[0]
    assert etype == "regulatory-figure" and kf == "figure_key"
    assert rec["figure_key"] == "concessional-cap-2026-27"  # key rendered from template
    assert rec["provenance"]["sources"] == ["ato"] and rec["provenance"]["refresh_date"] == "2026-07-07"


def test_validate_record_reports_errors():
    schema = {"$defs": {"t": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}}}
    assert normalize.validate_record({"a": "x"}, "t", schema) == []
    assert normalize.validate_record({}, "t", schema)  # missing required -> errors


# ── generic orchestrator with fakes ───────────────────────────────────────────────
class _FakeStore:
    def __init__(self):
        self.upserted = []

    def upsert(self, items):
        self.upserted.append(items)
        types = {t for t, _k, _r in items}
        return {t: {"added": sum(1 for x in items if x[0] == t), "updated": 0, "skipped": 0,
                    "deprecated": 0, "aborted": False} for t in types}

    def load_type(self, t):
        return []


class _FakeRefresh:
    def __init__(self):
        self.manifests = []

    def put_source_manifest(self, domain, source, report, count):
        self.manifests.append((source, count))

    def put_source(self, *a, **k):
        pass

    def release_lock(self, *a, **k):
        pass


def test_orchestrate_ingest_all_static_sources():
    d = desc_mod.from_dict("demo", {"sources": [
        {"name": "owasp", "produces": ["agentic-threat"], "fetch": {"transport": "static"},
         "normalize": {"mode": "builder", "builder": "owasp"}},
        {"name": "nist-ai", "produces": ["risk-method-profile"], "fetch": {"transport": "static"},
         "normalize": {"mode": "builder", "builder": "nist_ai_rmf"}},
    ]})
    store, refresh = _FakeStore(), _FakeRefresh()
    out = orchestrate.ingest_all(d, store, today="2026-07-07", refresh=refresh)
    assert out["domain"] == "demo" and out["any_changed"] is True
    assert out["sources"]["owasp"]["status"] == "done" and out["sources"]["owasp"]["added"] == 20
    assert {m[0] for m in refresh.manifests} == {"owasp", "nist-ai"}


# ── the risk domain, running through its DECLARATIVE descriptor, at parity ────────
def test_risk_descriptor_loads_and_matches_handler_builders():
    h = _handler()
    d = desc_mod.load("cyber-security-risk", domains_dir=_DOMAINS)
    assert set(d.source_names()) == {"mitre-attack", "cisa-kev", "veris-vcdb", "owasp",
                                     "nist-ai-rmf", "wiz-cvdb", "nist-800-53", "mitre-atlas",
                                     "nist-ai-600-1", "eu-ai-act", "nist-ai-rmf-playbook",
                                     "ai-incidents", "framework-crosswalk", "acsc-ism"}
    # ATT&CK through the descriptor's builder path == the original handler builder
    stix = {"objects": [
        {"type": "attack-pattern", "id": "attack-pattern--1", "name": "Phishing",
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1566", "url": "u"}],
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "initial-access"}]},
        {"type": "course-of-action", "id": "course-of-action--1", "name": "Training",
         "external_references": [{"source_name": "mitre-attack", "external_id": "M1017", "url": "u2"}]},
        {"type": "relationship", "relationship_type": "mitigates",
         "source_ref": "course-of-action--1", "target_ref": "attack-pattern--1"},
    ]}
    a = d.adapter("mitre-attack")
    got = a.build(stix, "2026-07-01")
    assert got == h.build_attack_records(stix["objects"], today="2026-07-01")
    # OWASP static source needs no fetch
    assert d.adapter("owasp").needs_fetch is False
