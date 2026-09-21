"""Version-aware synthesis wiring + the gai-risk builder.

- gai-risk builder (NIST AI 600-1) produces the 12 durable GAI-risk reference records.
- synth_risk.plan_synthesis prepends a `catalog_version:` line to each mission when the
  store is version-scoped, so agent-invoker auto-persists synthesized risks INTO that
  version's namespace (the linchpin of building green from genuine synthesis, not a copy).
"""
from tessera_platform.catalog import formats
from tessera_platform.catalog import synth_risk


def test_gai_risk_builder_produces_nist_ai_600_1_records():
    b = formats.BUILDERS["nist_ai_600_1"]
    assert b["needs_fetch"] is False
    recs = b["fn"](today="2026-07-07")
    assert len(recs) == 12                                   # the 12 NIST AI 600-1 GAI risks
    types = {t for t, _kf, _r in recs}
    assert types == {"gai-risk"}
    for _t, kf, r in recs:
        assert kf == "gai_risk_id" and r["gai_risk_id"]      # keyable
        assert r["framework"] == "nist-ai-600-1"
        assert r["provenance"]["sources"] == ["nist-ai-600-1"]
    # a couple of canonical NIST AI 600-1 risks are present
    ids = {r["gai_risk_id"] for _t, _kf, r in recs}
    assert {"confabulation", "info-security", "data-privacy"} <= ids


class _VersionStore:
    """Version-scoped store stub: load_type returns canned inputs; captures nothing else."""
    def __init__(self, version, by_type):
        self.version = version
        self._by_type = by_type

    def load_type(self, etype, include_deprecated=False):
        return list(self._by_type.get(etype, []))


_INPUTS = {
    "attack-technique": [{"technique_id": "T1486", "name": "Data Encrypted for Impact",
                          "tactics": ["impact"], "description": "ransomware encrypt", "mitigations": ["M1040"]}],
    "security-control": [{"control_id": "M1040", "name": "Behavior Prevention", "description": "block"}],
    "vulnerability": [], "agentic-threat": [], "gai-risk": [], "breach": [], "risk": [],
}


def test_plan_synthesis_embeds_catalog_version_in_missions(monkeypatch):
    """When the store is version-scoped, each stashed mission carries a `catalog_version:`
    line so agent-invoker writes the synthesized risks into that version namespace."""
    captured = {}
    # capture put_cluster payloads instead of hitting DynamoDB
    from tessera_platform.catalog import store as refresh_store

    def fake_put_cluster(domain, cluster, item):
        captured[cluster] = item
    monkeypatch.setattr(refresh_store, "put_cluster", fake_put_cluster)

    store = _VersionStore("v20260708-999999", _INPUTS)
    out = synth_risk.plan_synthesis(store, "cyber-security-risk", {})
    assert out["to_run"], "expected clusters to synthesize"
    # every stashed mission is prefixed with the version header (agent-invoker convention)
    for cid, item in captured.items():
        assert item["mission"].startswith("catalog_version: v20260708-999999\n"), cid


def test_plan_synthesis_no_version_header_for_unversioned_store(monkeypatch):
    """Routine (active/legacy) synthesis has no version -> no header -> legacy namespace."""
    captured = {}
    from tessera_platform.catalog import store as refresh_store
    monkeypatch.setattr(refresh_store, "put_cluster", lambda d, c, i: captured.setdefault(c, i))

    store = _VersionStore(None, _INPUTS)     # version=None
    synth_risk.plan_synthesis(store, "cyber-security-risk", {})
    for item in captured.values():
        assert not item["mission"].startswith("catalog_version:")
