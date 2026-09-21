"""Real CatalogStore.upsert write path (batched Memory writes).

The upsert write mechanics (create/update/deprecate + batching at the 100-record API cap)
were previously only exercised indirectly. This pins them with a fake bedrock-agentcore
client, guarding the change that batches writes (was 1 record/call -> up to 100/call).
"""
import json

from tessera_platform.catalog.engine import CatalogStore, content_hash, slug


class FakeAC:
    """In-memory bedrock-agentcore stand-in. Records every batch call + its size."""
    def __init__(self, existing=None):
        # existing: {namespacePath_prefix: [ (recordId, record_dict) ]}
        self._store = {}   # namespacePath -> list of summaries
        self.create_calls = []   # list of batch sizes
        self.delete_calls = []
        self._auto = 0
        for ns, recs in (existing or {}).items():
            self._store[ns] = [{"memoryRecordId": rid,
                                 "content": {"text": json.dumps(r)},
                                 "namespaces": [f"{ns}/{slug(str(r.get(list(r.keys())[0])))}"]}
                                for rid, r in recs]

    def list_memory_records(self, memoryId, namespacePath, maxResults=100, nextToken=None):
        return {"memoryRecordSummaries": self._store.get(namespacePath, [])}

    def batch_create_memory_records(self, memoryId, records):
        assert len(records) <= 100, "exceeded API max of 100 records/call"
        self.create_calls.append(len(records))
        return {}

    def batch_delete_memory_records(self, memoryId, records):
        assert len(records) <= 100
        self.delete_calls.append(len(records))
        return {}


class FakeCPC:
    def get_memory(self, memoryId):
        return {"memory": {"strategies": [{"name": "tessera_catalog", "type": "CUSTOM", "strategyId": "sid-1"}]}}


def _store(existing=None):
    s = CatalogStore(memory_id="m", version=None)
    s._ac = FakeAC(existing)
    s._cpc = FakeCPC()
    return s


def test_upsert_batches_creates_at_100_cap():
    s = _store()
    # 250 fresh vulnerabilities -> all creates -> ceil(250/100)=3 batched create calls.
    items = [("vulnerability", "cve_or_ref", {"cve_or_ref": f"CVE-{i}", "name": f"v{i}"}) for i in range(250)]
    report = s.upsert(items)
    assert report["vulnerability"]["added"] == 250
    assert s._ac.create_calls == [100, 100, 50]        # batched, not 250 single calls
    assert s._ac.delete_calls == []                     # nothing to delete on a fresh build


def test_upsert_update_and_deprecate_semantics():
    # blue has A (will change), B (will be dropped -> deprecated), from the same source.
    old_a = {"cve_or_ref": "CVE-A", "name": "old", "provenance": {"sources": ["src"]}}
    old_b = {"cve_or_ref": "CVE-B", "name": "b", "provenance": {"sources": ["src"]}}
    existing = {"/tessera/catalog/vulnerability": [("id-a", old_a), ("id-b", old_b)]}
    s = _store(existing)
    # incoming: A changed, C new — both from the same source; B no longer emitted.
    items = [
        ("vulnerability", "cve_or_ref", {"cve_or_ref": "CVE-A", "name": "NEW", "provenance": {"sources": ["src"]}}),
        ("vulnerability", "cve_or_ref", {"cve_or_ref": "CVE-C", "name": "c", "provenance": {"sources": ["src"]}}),
    ]
    report = s.upsert(items)
    r = report["vulnerability"]
    assert r["added"] == 1 and r["updated"] == 1 and r["deprecated"] == 1 and r["aborted"] is False
    # update (A) + deprecate (B) each delete the old id -> 2 deletes; create C + new A + tombstone B -> 3 creates.
    assert sum(s._ac.delete_calls) == 2
    assert sum(s._ac.create_calls) == 3


def test_upsert_sanity_guard_aborts_on_mass_drop():
    # blue has 10 from src; incoming has 2 (<50%) -> sanity guard aborts, writes NOTHING.
    existing = {"/tessera/catalog/vulnerability": [
        ("id-%d" % i, {"cve_or_ref": f"CVE-{i}", "provenance": {"sources": ["src"]}}) for i in range(10)]}
    s = _store(existing)
    items = [("vulnerability", "cve_or_ref", {"cve_or_ref": f"CVE-{i}", "provenance": {"sources": ["src"]}}) for i in range(2)]
    report = s.upsert(items)
    assert report["vulnerability"]["aborted"] is True
    assert s._ac.create_calls == [] and s._ac.delete_calls == []
