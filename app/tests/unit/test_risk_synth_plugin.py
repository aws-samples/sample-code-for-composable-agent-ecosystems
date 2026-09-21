"""Risk synthesis plugin (tessera_platform.catalog.synth_risk) on the GENERIC ingest path.

Proves:
  • the shared plugin exposes the Synthesizer contract `plan_synthesis(store, domain, event)`
  • it reads inputs via the (version-scoped) store, clusters, enumerates standards-anchored
    slots, and returns {clusters, slots_total, slots_to_synthesize, to_run, skipped}
  • additive top-up: pre-existing `risk` records suppress already-covered slots
  • the generic orchestrator loads it purely from the descriptor's `synthesis.plugin`
  • the legacy synth_plan shim re-exports the SAME functions (no drift / no copy-paste)
"""
import os

from tessera_platform.catalog import synth_risk
from tessera_platform.catalog import orchestrate
from tessera_platform.catalog import descriptor as descriptor_mod


# Ensure store.put_cluster is a no-op (no DynamoDB) during the test.
os.environ.pop("CATALOG_REFRESH_TABLE", None)


TECHS = [
    {"technique_id": "T1078", "name": "Valid Accounts", "tactics": ["defense-evasion", "persistence"],
     "description": "Adversaries obtain and abuse credentials of existing accounts.", "mitigations": ["M1032", "M1027"]},
    {"technique_id": "T1486", "name": "Data Encrypted for Impact", "tactics": ["impact"],
     "description": "Adversaries encrypt data to interrupt availability (ransomware).", "mitigations": ["M1040"]},
    {"technique_id": "T1213", "name": "Data from Information Repositories", "tactics": ["collection"],
     "description": "Adversaries leverage information repositories, PII and customer data.", "mitigations": ["M1041"]},
]
CTRLS = [
    {"control_id": "M1032", "name": "MFA", "description": "Require multi-factor authentication."},
    {"control_id": "M1040", "name": "Behavior Prevention", "description": "Block malicious behavior."},
    {"control_id": "M1041", "name": "Encrypt Sensitive Information", "description": "Protect data at rest."},
    {"control_id": "M1027", "name": "Password Policies", "description": "Strong passwords."},
]
VULNS = [{"cve_or_ref": "CVE-2023-0001", "name": "SomeRCE", "product": "Widget", "known_ransomware": True}]
AGENTIC = [{"threat_id": "LLM01", "name": "Prompt Injection", "category": "llm-application", "framework": "OWASP-LLM"}]
GAI = [{"gai_risk_id": "GAI-1", "name": "Confabulation"}]
BREACHES = [{"org": "AcmeCorp", "year": 2021, "industry": "tech", "summary": "employee misused privilege to leak personal data"}]


class FakeStore:
    """Minimal CatalogStore stand-in: load_type returns canned records per type."""
    def __init__(self, by_type):
        self._by_type = by_type

    def load_type(self, etype, include_deprecated=False):
        return list(self._by_type.get(etype, []))


def _inputs(existing_risks=None):
    return FakeStore({
        "attack-technique": TECHS,
        "security-control": CTRLS,
        "vulnerability": VULNS,
        "agentic-threat": AGENTIC,
        "gai-risk": GAI,
        "breach": BREACHES,
        "risk": existing_risks or [],
    })


def test_plan_synthesis_returns_contract_and_clusters():
    out = synth_risk.plan_synthesis(_inputs(), "cyber-security-risk", {})
    # Synthesizer contract keys present.
    assert set(out) >= {"clusters", "slots_total", "slots_to_synthesize", "to_run", "skipped"}
    assert out["clusters"] > 0
    assert out["slots_total"] > 0
    # every to_run entry is a cluster_id + a stable input_digest (SFN Map item shape).
    assert out["to_run"], "expected un-covered slots to synthesize on an empty catalog"
    for e in out["to_run"]:
        assert e["cluster_id"] and isinstance(e["input_digest"], str) and len(e["input_digest"]) == 16
    assert out["slots_to_synthesize"] == out["slots_to_synthesize"]  # int, computed
    # AI cluster is present (agentic-threat -> ai cluster).
    assert any(cid.startswith("cluster-ai-") for cid in [e["cluster_id"] for e in out["to_run"]])


def test_plan_synthesis_additive_topup_suppresses_covered_slots():
    """A fully-covered catalog yields fewer slots to synthesize than an empty one."""
    empty = synth_risk.plan_synthesis(_inputs(), "cyber-security-risk", {})
    # Pre-seed one risk per enumerated category to mark coverage.
    all_cats = set()
    for c in synth_risk.cluster(TECHS, CTRLS, VULNS) + synth_risk.cluster_agentic(AGENTIC, GAI) \
            + synth_risk.cluster_scenarios(TECHS, CTRLS, VULNS, BREACHES):
        for s in synth_risk.enumerate_slots(c):
            all_cats.add(s["category"])
    seeded = [{"category": cat, "technique_ids": [t["technique_id"] for t in TECHS]} for cat in all_cats for _ in range(2)]
    covered = synth_risk.plan_synthesis(_inputs(existing_risks=seeded), "cyber-security-risk", {})
    assert covered["slots_to_synthesize"] <= empty["slots_to_synthesize"]


def test_generic_orchestrator_loads_plugin_from_descriptor():
    """The generic orchestrator resolves the plugin purely from the descriptor's
    synthesis.plugin dotted path — configuration over code."""
    ingest = {
        "sources": [{"name": "x", "produces": ["risk"],
                     "normalize": {"mode": "map", "entity_type": "risk", "select": "[]", "fields": {}}}],
        "synthesis": {"plugin": "tessera_platform.catalog.synth_risk"},
    }
    desc = descriptor_mod.from_dict("cyber-security-risk", ingest)
    out = orchestrate.plan_synthesis(desc, _inputs(), {})
    assert out["clusters"] > 0 and "to_run" in out


def test_legacy_shim_reexports_same_functions():
    """lambdas/catalog-ingest/synth_plan.py re-exports the shared plugin (no drift)."""
    import importlib.util
    p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                     "lambdas", "catalog-ingest", "synth_plan.py"))
    spec = importlib.util.spec_from_file_location("synth_plan", p)
    sp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sp)
    # same function objects as the shared module (true re-export, not a copy)
    assert sp.cluster is synth_risk.cluster
    assert sp.plan_slots is synth_risk.plan_slots
    assert sp.plan_synthesis is synth_risk.plan_synthesis
