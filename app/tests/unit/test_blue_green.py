"""Tests for the blue/green versioned catalog release pattern (spec §5a):
build a fresh GREEN version, gate on a green-vs-blue content diff, atomically promote,
retire the old. Uses in-memory fakes for Memory + the refresh store (no AWS).
"""
from tessera_platform.catalog import diff_snapshots, content_hash, orchestrate
from tessera_platform.catalog import descriptor as desc_mod
from tessera_platform.catalog.engine import CatalogStore


def test_catalogstore_namespaces_are_version_scoped():
    assert CatalogStore(memory_id="m", version=None)._ns("t") == "/tessera/catalog/t"
    assert CatalogStore(memory_id="m", version="v2")._ns("t") == "/tessera/catalog/v2/t"


def test_diff_snapshots_detects_identity_and_drift():
    blue = {"a": ["h1", "h2"], "b": ["h3"]}
    assert diff_snapshots(blue, dict(blue))["identical"] is True
    d = diff_snapshots(blue, {"a": ["h1", "h2"], "b": ["hX"]})     # b changed
    assert d["identical"] is False and d["by_type"]["b"]["added"] == 1 and d["by_type"]["b"]["removed"] == 1
    d2 = diff_snapshots(blue, {"a": ["h1", "h2", "h9"], "b": ["h3"]})  # a gained one
    assert d2["identical"] is False and d2["by_type"]["a"]["added"] == 1 and d2["by_type"]["a"]["removed"] == 0


# ── in-memory fakes ───────────────────────────────────────────────────────────────
class FakeStore:
    """Version-scoped in-memory catalog: {type: {key: record}}."""

    def __init__(self, version=None):
        self.version = version
        self.data = {}

    def upsert(self, items, dry_run=False):
        report = {}
        for et, kf, rec in items:
            self.data.setdefault(et, {})[rec[kf]] = rec
            report.setdefault(et, {"added": 0, "updated": 0, "skipped": 0, "deprecated": 0, "aborted": False})
            report[et]["added"] += 1
        return report

    def load_type(self, t, include_deprecated=False):
        return list(self.data.get(t, {}).values())

    def snapshot(self, types):
        return {t: sorted(content_hash(r) for r in self.load_type(t)) for t in types}

    def retire(self, types):
        n = sum(len(self.data.get(t, {})) for t in types)
        for t in types:
            self.data.pop(t, None)
        return {"version": self.version, "deleted": n}


class FakeRefresh:
    def __init__(self):
        self.active = None
        self.versions = {}

    def get_active_version(self, domain):
        return self.active

    def set_active_version(self, domain, version):
        self.active = version

    def put_version(self, domain, version, item):
        self.versions.setdefault(version, {}).update(item)

    def get_version(self, domain, version):
        return self.versions.get(version)

    def put_source_manifest(self, *a, **k):
        pass

    def put_source(self, *a, **k):
        pass


def _owasp_descriptor():
    return desc_mod.from_dict("demo", {"sources": [
        {"name": "owasp", "produces": ["agentic-threat"], "fetch": {"transport": "static"},
         "normalize": {"mode": "builder", "builder": "owasp"}},
    ]})


def test_blue_green_build_validate_promote_retire():
    d = _owasp_descriptor()
    refresh = FakeRefresh()
    green, blue = FakeStore("vGreen"), FakeStore("vBlue")

    # build green (blue is empty) -> validate says NOT identical (20 added)
    res = orchestrate.build_version(d, "vGreen", today="2026-07-07", refresh=refresh, store=green)
    assert res["counts"]["agentic-threat"] == 20
    diff = orchestrate.validate_version(d, "vGreen", compare_to="vBlue", refresh=refresh,
                                        green_store=green, blue_store=blue)
    assert diff["identical"] is False and diff["by_type"]["agentic-threat"]["added"] == 20

    # build blue identically -> green == blue -> gate passes
    orchestrate.build_version(d, "vBlue", today="2026-07-07", refresh=refresh, store=blue)
    diff2 = orchestrate.validate_version(d, "vGreen", compare_to="vBlue", refresh=refresh,
                                         green_store=green, blue_store=blue)
    assert diff2["identical"] is True

    # promote green atomically
    p = orchestrate.promote_version("demo", "vGreen", refresh=refresh)
    assert p["active"] == "vGreen" and refresh.get_active_version("demo") == "vGreen"

    # retiring the ACTIVE version is refused; retiring the old one deletes it
    try:
        orchestrate.retire_version(d, "vGreen", refresh=refresh, store=green)
        assert False, "should refuse to retire active version"
    except ValueError:
        pass
    r = orchestrate.retire_version(d, "vBlue", refresh=refresh, store=blue)
    assert r["deleted"] == 20 and refresh.versions["vBlue"]["status"] == "retired"


def test_promote_records_previous_for_rollback():
    refresh = FakeRefresh()
    orchestrate.promote_version("demo", "v1", refresh=refresh)
    out = orchestrate.promote_version("demo", "v2", refresh=refresh)
    assert out["previous"] == "v1" and refresh.get_active_version("demo") == "v2"
    # rollback = re-promote the previous version
    orchestrate.promote_version("demo", "v1", refresh=refresh)
    assert refresh.get_active_version("demo") == "v1"


def test_migrate_builds_and_gates_promotion():
    """Route 1: build green with the declarative engine, diff vs the live catalog, promote
    only when identical."""
    d = _owasp_descriptor()
    refresh = FakeRefresh()
    green, blue = FakeStore("vG"), FakeStore()   # blue = legacy live (empty here)
    out = orchestrate.migrate_version(d, version="vG", promote_if_identical=True,
                                      today="2026-07-07", refresh=refresh,
                                      green_store=green, blue_store=blue)
    assert out["diff"]["identical"] is False and out["promoted"] is False   # green 20 vs blue 0

    orchestrate.build_version(d, "vBlue", today="2026-07-07", refresh=refresh, store=blue)  # blue now == green
    green2 = FakeStore("vG2")
    out2 = orchestrate.migrate_version(d, version="vG2", promote_if_identical=True,
                                       today="2026-07-07", refresh=refresh,
                                       green_store=green2, blue_store=blue)
    assert out2["diff"]["identical"] is True and out2["promoted"] is True
    assert refresh.get_active_version("demo") == "vG2"


def test_bootstrap_stamps_existing_into_version():
    """Route 2: copy the current live records into a version namespace + set active."""
    d = desc_mod.from_dict("demo", {"sources": [
        {"name": "owasp", "produces": ["agentic-threat"], "fetch": {"transport": "static"},
         "normalize": {"mode": "builder", "builder": "owasp"}}]},
        schema={"x-tessera-keys": {"agentic-threat": "{threat_id}"}})
    refresh = FakeRefresh()
    src, dst = FakeStore(), FakeStore("v1")
    orchestrate.build_version(d, "legacy", today="2026-07-07", refresh=refresh, store=src)  # populate live
    out = orchestrate.bootstrap_version(d, "v1", refresh=refresh, src_store=src, dst_store=dst)
    assert out["copied"]["agentic-threat"] == 20
    assert len(dst.load_type("agentic-threat")) == 20
    assert refresh.get_active_version("demo") == "v1"


def _synthesis_descriptor():
    """A descriptor with a source-produced type + a synthesized type gated on coverage."""
    return desc_mod.from_dict("demo", {
        "sources": [{"name": "owasp", "produces": ["agentic-threat"],
                     "fetch": {"transport": "static"},
                     "normalize": {"mode": "builder", "builder": "owasp"}}],
        "synthesis": {"plugin": "x.y", "produces": ["risk"],
                      "coverage": {"min_total": 3, "required_categories": ["data-breach", "ransomware"]}},
    }, schema={"x-tessera-keys": {"agentic-threat": "{threat_id}", "risk": "{risk_id}"}})


def _synth_into(store, cats):
    """Simulate synthesis writing risk records INTO a (green) store."""
    store.upsert([("risk", "risk_id", {"risk_id": f"RSK-{i}", "category": c})
                  for i, c in enumerate(cats)])


def test_versioned_build_ready_when_inputs_identical_and_coverage_met():
    """Green = ingest inputs (identical to blue) + synthesis run INTO green meeting the
    declared coverage -> ready to promote. Synthesis is generated, never copied."""
    d = _synthesis_descriptor()
    refresh = FakeRefresh()
    blue, green = FakeStore(), FakeStore("vG")
    # blue = live: inputs + synthesized risks (whatever they are).
    orchestrate.build_version(d, "legacy", today="2026-07-07", refresh=refresh, store=blue)
    _synth_into(blue, ["data-breach", "ransomware", "ip-theft"])
    # green: rebuild inputs declaratively, then SYNTHESIZE fresh into green (not copied).
    orchestrate.build_version(d, "vG", today="2026-07-07", refresh=refresh, store=green)
    _synth_into(green, ["data-breach", "ransomware", "insider-threat", "supply-chain"])  # different set, fresh
    v = orchestrate.validate_version(d, "vG", compare_to="legacy", refresh=refresh,
                                     green_store=green, blue_store=blue)
    assert v["identical"] is True                      # inputs reproduced exactly
    assert v["coverage"]["risk"]["total"] == 4 and v["coverage"]["risk"]["ok"] is True
    assert v["coverage_ok"] is True and v["ready"] is True


def test_versioned_build_not_ready_when_synthesis_coverage_unmet():
    """Even with byte-identical inputs, insufficient synthesized coverage blocks promotion
    (too few risks OR a required category missing)."""
    d = _synthesis_descriptor()
    refresh = FakeRefresh()
    blue, green = FakeStore(), FakeStore("vG")
    orchestrate.build_version(d, "legacy", today="2026-07-07", refresh=refresh, store=blue)
    orchestrate.build_version(d, "vG", today="2026-07-07", refresh=refresh, store=green)
    # green synth ran but under-produced: only 2 risks and missing 'ransomware'.
    _synth_into(green, ["data-breach", "data-breach"])
    v = orchestrate.validate_version(d, "vG", compare_to="legacy", refresh=refresh,
                                     green_store=green, blue_store=blue)
    assert v["identical"] is True                       # inputs fine
    assert v["coverage"]["risk"]["ok"] is False          # < min_total(3) AND missing ransomware
    assert "ransomware" in v["coverage"]["risk"]["missing_categories"]
    assert v["ready"] is False                           # -> NOT safe to promote
