"""Parity proof for the declarative mapping DSL (docs/spec-declarative-ingest.md, Stage 3).

Asserts that a DECLARATIVE `mode: map` spec reproduces the hand-written Python
normalizers in lambdas/catalog-ingest/handler.py BYTE-FOR-BYTE on the same fixtures.
If these pass, the deterministic ingest path can be expressed as config with no
per-domain code — the core claim of the reusable model.
"""
import importlib.util
import os

from tessera_platform.catalog import Mapping, content_hash, diff_plan

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "catalog-ingest", "handler.py"))


def _ingest():
    spec = importlib.util.spec_from_file_location("catalog_ingest", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── declarative specs (exactly what would live in a domain descriptor's ingest:) ──
KEV_MAP = {
    "entity_type": "vulnerability",
    "key_field": "cve_or_ref",
    "select": "vulnerabilities[?cveID]",
    "fields": {
        "cve_or_ref": "cveID",
        "name": "vulnerabilityName || ''",
        "vendor": "vendorProject || ''",
        "product": "product || ''",
        "description": "truncate(shortDescription, `1200`)",
        "known_exploited": "`true`",
        "known_ransomware": "eq(lower(knownRansomwareCampaignUse || ''), 'known')",
        "date_added": "dateAdded || ''",
        "required_action": "requiredAction || ''",
        "references": "[join('', ['https://nvd.nist.gov/vuln/detail/', cveID])]",
    },
    "provenance": {"ingested_by": "catalog-ingest", "sources": ["cisa-kev"]},
}

OSCAL_MAP = {
    "entity_type": "security-control",
    "key_field": "control_id",
    "select": {"primitive": "oscal_controls"},
    "fields": {
        "control_id": "upper(control.id)",
        "name": "control.title || ''",
        "description": "oscal_statement(control)",
        "family": "family",
    },
    "const": {
        "framework": "nist-800-53-rev5",
        "references": ["https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final"],
    },
    "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-800-53"]},
}


def test_kev_declarative_matches_python_builder():
    m = _ingest()
    kev = {"vulnerabilities": [
        {"cveID": "CVE-2021-44228", "vulnerabilityName": "Log4Shell", "vendorProject": "Apache",
         "product": "Log4j", "shortDescription": "RCE", "dateAdded": "2021-12-10",
         "knownRansomwareCampaignUse": "Known"},
        {"cveID": "CVE-2020-0001", "vulnerabilityName": "X", "knownRansomwareCampaignUse": "Unknown"},
        {"vulnerabilityName": "no-cve"},  # skipped (no cveID)
    ]}
    expected = m.build_kev_records(kev, today="2026-07-01")
    got = Mapping.from_spec(KEV_MAP).apply(kev, today="2026-07-01")
    assert got == expected


def test_kev_declarative_limit_matches():
    m = _ingest()
    kev = {"vulnerabilities": [{"cveID": f"CVE-2021-{i:04d}"} for i in range(10)]}
    expected = m.build_kev_records(kev, today="2026-07-01", limit=3)
    spec = dict(KEV_MAP, limit=3)
    got = Mapping.from_spec(spec).apply(kev, today="2026-07-01")
    assert got == expected
    assert len(got) == 3


def test_nist80053_oscal_declarative_matches_python_builder():
    m = _ingest()
    data = {"catalog": {"groups": [
        {"id": "ac", "title": "Access Control", "controls": [
            {"id": "ac-1", "title": "Policy and Procedures",
             "parts": [{"name": "statement", "prose": "Develop, document...",
                        "parts": [{"prose": "Review annually."}]}],
             "controls": [{"id": "ac-2.1", "title": "Automated Account Management",
                           "parts": [{"name": "statement", "prose": "Support automation."}]}]},
        ]},
    ]}}
    expected = m.build_nist80053(data, today="2026-07-02")
    got = Mapping.from_spec(OSCAL_MAP).apply(data, today="2026-07-02")
    assert got == expected


def test_engine_pure_functions_match_handler():
    """The extracted engine primitives must be byte-identical to the handler's."""
    m = _ingest()
    rec = {"control_id": "AC-1", "name": "Policy",
           "provenance": {"sources": ["nist-800-53"], "refresh_date": "2026-07-01"}}
    assert content_hash(rec) == m._content_hash(rec)
    existing = {"a": "h1", "b": "h2", "c": "h3"}
    incoming = {"a": "h1", "b": "hX", "d": "h4"}
    assert diff_plan(existing, incoming) == m._diff_plan(existing, incoming)
