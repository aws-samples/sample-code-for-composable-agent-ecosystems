"""S3 overflow for oversized catalog records (tessera_platform.catalog.blob) + engine wiring.

Verifies full-fidelity storage without truncation: a record that exceeds the 16k Memory cap
is written whole to S3 with a lean record + content_ref kept in Memory; resolve() reconstitutes
the full dossier; small records stay inline; snapshot/diff use the stored hash (no S3 fetch).
"""
import json
import os

import pytest

from tessera_platform.catalog import blob, engine
from tessera_platform.catalog.engine import CatalogStore, content_hash, slug


class FakeS3:
    def __init__(self):
        self.store = {}
        self.puts = 0
        self.gets = 0
    def put_object(self, Bucket, Key, Body, **kw):
        self.store[(Bucket, Key)] = Body.decode() if isinstance(Body, bytes) else Body
        self.puts += 1
        return {}
    def get_object(self, Bucket, Key):
        self.gets += 1
        return {"Body": _Body(self.store[(Bucket, Key)])}
    def delete_object(self, Bucket, Key):
        self.store.pop((Bucket, Key), None)
        return {}


class _Body:
    def __init__(self, s): self._s = s
    def read(self): return self._s.encode() if isinstance(self._s, str) else self._s


@pytest.fixture
def fake_s3(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(blob, "_s3", lambda: s3)
    monkeypatch.setenv("CATALOG_CONTENT_BUCKET", "amzn-s3-demo-test-bucket")
    return s3


def test_small_record_stays_inline(fake_s3):
    rec = {"risk_id": "RSK-1", "category": "data-breach", "threat_narrative": "short"}
    stored, off = blob.pack(rec, "/tessera/catalog/risk/rsk-1", content_hash(rec))
    assert off is False
    assert "content_ref" not in stored and stored["content_hash"]
    assert fake_s3.puts == 0


def test_large_record_offloads_full_to_s3_and_resolves(fake_s3):
    big = "X" * 40000
    rec = {"risk_id": "RSK-2", "category": "ransomware", "severity_default": "Critical",
           "threat_narrative": big, "mapped_controls": [{"control_id": "M1040"}]}
    chash = content_hash(rec)
    stored, off = blob.pack(rec, "/tessera/catalog/risk/rsk-2", chash)
    assert off is True and fake_s3.puts == 1
    # lean record: scalars/short strings kept, long prose excerpted, ref + hash present
    assert stored["content_ref"].startswith("s3://amzn-s3-demo-test-bucket/catalog-content/")
    assert stored["category"] == "ransomware" and stored["severity_default"] == "Critical"
    assert stored["threat_narrative"].endswith("\u2026") and len(stored["threat_narrative"]) <= 400
    assert "mapped_controls" not in stored          # big field lives only in S3
    assert stored["content_hash"] == chash
    # resolve reconstitutes the FULL dossier
    full = blob.resolve(stored)
    assert full["threat_narrative"] == big and full["mapped_controls"] == [{"control_id": "M1040"}]
    # hash stable across full vs stored (content_hash ignores content_ref/content_hash)
    assert content_hash(full) == chash


# ── engine end-to-end: offloaded records list back full + snapshot uses stored hash ──
class FakeAC:
    def __init__(self): self.recs = {}   # namespace -> content text
    def list_memory_records(self, memoryId, namespacePath, maxResults=100, nextToken=None):
        out = [{"memoryRecordId": ns, "content": {"text": t}, "namespaces": [ns]}
               for ns, t in self.recs.items() if ns.startswith(namespacePath)]
        return {"memoryRecordSummaries": out}
    def batch_create_memory_records(self, memoryId, records):
        for r in records: self.recs[r["namespaces"][0]] = r["content"]["text"]
        return {}
    def batch_delete_memory_records(self, memoryId, records): return {}


class FakeCPC:
    def get_memory(self, memoryId):
        return {"memory": {"strategies": [{"name": "tessera_catalog", "type": "CUSTOM", "strategyId": "sid"}]}}


def test_engine_offloads_and_loads_full(fake_s3, monkeypatch):
    s = CatalogStore(memory_id="m", version="vG")
    s._ac = FakeAC(); s._cpc = FakeCPC()
    big = "Y" * 30000
    s.upsert([("risk", "risk_id", {"risk_id": "RSK-9", "category": "ip-theft", "threat_narrative": big})])
    assert fake_s3.puts == 1                                  # offloaded
    # what's stored in Memory is lean (has content_ref)
    raw = s.load_type("risk", resolve=False)[0]
    assert raw.get("content_ref") and raw["threat_narrative"].endswith("\u2026")
    # load_type(resolve=True) returns the FULL dossier
    full = s.load_type("risk")[0]
    assert full["threat_narrative"] == big
    # snapshot uses the stored hash (no extra S3 fetch beyond none needed)
    gets_before = fake_s3.gets
    snap = s.snapshot(["risk"])
    assert snap["risk"] and fake_s3.gets == gets_before      # snapshot did NOT resolve S3
