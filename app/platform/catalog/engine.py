"""tessera_platform.catalog.engine — the domain-agnostic catalog write engine.

Extracted VERBATIM (behavior-preserving) from lambdas/catalog-ingest/handler.py so
every domain shares one incremental, non-destructive upsert engine instead of forking
it. Operates purely on `(entity_type, key_field, record)` tuples — it knows nothing
about ATT&CK, super rates, or any domain.

  • content_hash / diff_plan     — PURE, unit-tested (pinned by tests/unit/test_catalog_ingest.py)
  • CatalogStore                 — Memory (LTM) plumbing: load_type / upsert (dry_run) /
                                   snapshot / retire; version-scoped for blue/green releases
  • diff_snapshots               — green-vs-blue content diff (the promotion gate)

The pure functions here MUST stay byte-for-byte equivalent to the originals so the
diff/hash of already-ingested records is unchanged (backfill-free incrementality).
"""
import os
import re
import json
import uuid
from datetime import datetime, timezone


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")[:80]


def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


# ── pure: content hash + diff plan (parity-pinned) ───────────────────────────────
def content_hash(rec):
    """Stable hash of a record's MEANINGFUL content — excludes volatile provenance
    (refresh_date) and any prior content_hash, so re-ingesting identical data hashes
    identically."""
    import copy, hashlib
    r = copy.deepcopy(rec)
    r.pop("content_hash", None)
    r.pop("content_ref", None)
    pv = r.get("provenance")
    if isinstance(pv, dict):
        pv.pop("refresh_date", None)
    return hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:16]


def diff_plan(existing, incoming):
    """Pure diff. existing/incoming: {key: hash}. Returns key lists.
    create = new keys; update = same key, changed hash; skip = identical."""
    create = [k for k in incoming if k not in existing]
    update = [k for k in incoming if k in existing and existing[k] != incoming[k]]
    skip = [k for k in incoming if k in existing and existing[k] == incoming[k]]
    return {"create": create, "update": update, "skip": skip}


def diff_snapshots(blue, green):
    """Compare two CatalogStore.snapshot() outputs ({type: sorted([content_hash])}).
    Returns per-type {added, removed, blue, green, identical} and a top-level `identical`
    (True iff green == blue exactly). This is the blue/green promotion gate — needs no
    key field: any added/removed/changed record shows as a hash-multiset difference."""
    from collections import Counter
    types = set(blue) | set(green)
    out, all_same = {}, True
    for t in sorted(types):
        bc, gc = Counter(blue.get(t, [])), Counter(green.get(t, []))
        added = sum((gc - bc).values())      # in green, not blue (new/changed)
        removed = sum((bc - gc).values())    # in blue, not green (dropped/changed)
        same = (added == 0 and removed == 0)
        all_same = all_same and same
        out[t] = {"added": added, "removed": removed,
                  "blue": sum(bc.values()), "green": sum(gc.values()), "identical": same}
    return {"identical": all_same, "by_type": out}


# ── CatalogStore: AgentCore Memory plumbing + non-destructive upsert ─────────────
CATALOG_STRATEGY_NAME = "tessera_catalog"


class CatalogStore:
    """Wraps a domain's catalog namespace in AgentCore Memory (LTM). Domain-agnostic:
    all methods take/return `(entity_type, key_field, record)` shaped data.

    `version` scopes every namespace to `/tessera/catalog/<version>/<type>/<key>` for the
    blue/green release pattern (build a fresh green version, validate, atomically promote,
    retire the old). version=None => the legacy namespace `/tessera/catalog/<type>/<key>`."""

    def __init__(self, memory_id=None, region=None, sanity_drop=None, version=None):
        self.memory_id = memory_id or os.environ["TESSERA_MEMORY_ID"]
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.sanity_drop = float(os.environ.get("CATALOG_SANITY_DROP", "0.5")) if sanity_drop is None else sanity_drop
        self.version = version
        self._ac = None
        self._cpc = None

    # ── namespace scoping (blue/green) ───────────────────────────────────────────
    def _base(self):
        return f"/tessera/catalog/{self.version}" if self.version else "/tessera/catalog"

    def _ns(self, etype):
        return f"{self._base()}/{etype}"

    # lazy boto3 so the pure functions above import without AWS
    def _clients(self):
        if self._ac is None:
            import boto3
            self._ac = boto3.client("bedrock-agentcore", region_name=self.region)
            self._cpc = boto3.client("bedrock-agentcore-control", region_name=self.region)
        return self._ac, self._cpc

    def _strategy_id(self):
        _ac, cpc = self._clients()
        for s in (cpc.get_memory(memoryId=self.memory_id)["memory"].get("strategies") or []):
            if s.get("name") == CATALOG_STRATEGY_NAME or s.get("type") == "CUSTOM":
                return s.get("strategyId")
        return None

    def load_type(self, etype, include_deprecated=False, resolve=True):
        """Load all (non-deprecated) records of one catalog type (within this version).
        Oversized records are reconstituted from S3 (resolve=True) so callers always see the
        FULL dossier; pass resolve=False for count/hash-only reads (avoids S3 fetches)."""
        from . import blob
        ac, _cpc = self._clients()
        out, tok = [], None
        while True:
            kw = {"memoryId": self.memory_id, "namespacePath": self._ns(etype), "maxResults": 100}
            if tok:
                kw["nextToken"] = tok
            r = ac.list_memory_records(**kw)
            for x in (r.get("memoryRecordSummaries") or []):
                try:
                    rec = json.loads((x.get("content") or {}).get("text", "{}"))
                    if not (include_deprecated or rec.get("status") != "deprecated"):
                        continue
                    out.append(blob.resolve(rec) if resolve else rec)
                except Exception:
                    pass
            tok = r.get("nextToken")
            if not tok:
                break
        return out

    def _record_ids(self, etype):
        """(memoryRecordId, record) for every record of a type — for retire."""
        ac, _cpc = self._clients()
        out, tok = [], None
        while True:
            kw = {"memoryId": self.memory_id, "namespacePath": self._ns(etype), "maxResults": 100}
            if tok:
                kw["nextToken"] = tok
            r = ac.list_memory_records(**kw)
            for x in (r.get("memoryRecordSummaries") or []):
                rid = x.get("memoryRecordId")
                if not rid:
                    continue
                try:
                    rec = json.loads((x.get("content") or {}).get("text", "{}") or "{}")
                except Exception:
                    rec = {}
                out.append((rid, rec))
            tok = r.get("nextToken")
            if not tok:
                break
        return out

    def snapshot(self, types):
        """Content fingerprint of this version: {type: sorted([content_hash, ...])}.
        Uses each record's STORED content_hash (present inline + on offloaded lean records),
        so diffing needs no S3 fetch and is consistent whether a record is inline or in S3."""
        return {t: sorted((rec.get("content_hash") or content_hash(rec))
                          for rec in self.load_type(t, resolve=False)) for t in types}

    def retire(self, types):
        """Hard-delete every record of this version's types (post-promotion cleanup),
        including any S3-offloaded dossier blobs."""
        from . import blob
        ac, _cpc = self._clients()
        deleted = 0
        for t in types:
            batch = []
            for rid, rec in self._record_ids(t):
                blob.delete_ref(rec)
                batch.append({"memoryRecordId": rid})
                if len(batch) == 100:
                    ac.batch_delete_memory_records(memoryId=self.memory_id, records=batch)
                    deleted += len(batch); batch = []
            if batch:
                ac.batch_delete_memory_records(memoryId=self.memory_id, records=batch)
                deleted += len(batch)
        return {"version": self.version, "deleted": deleted}

    def upsert(self, items, dry_run=False):
        """Incremental, non-destructive diff-upsert per (type, source). With dry_run=True,
        computes the plan (create/update/skip/deprecate) and writes NOTHING — used for the
        green-vs-blue / all-skip parity gate. Returns a change report per type: {added,
        updated, skipped, deprecated, aborted, prior, incoming, changed_keys}. Records the
        source no longer emits are soft-tombstoned (status=deprecated), never hard-deleted."""
        ac, cpc = self._clients()
        sid = None if dry_run else self._strategy_id()
        if not dry_run and not sid:
            raise RuntimeError("catalog strategy unavailable")
        now = datetime.now(timezone.utc)
        report = {}
        by_type = {}
        for etype, kf, rec in items:
            by_type.setdefault(etype, []).append((kf, rec))

        def _mk(etype, leaf, rec):
            """Build one create-record. Oversized records (rich dossiers) are offloaded to
            S3 with a lean record + content_ref kept in Memory (blob.pack) — full fidelity,
            no truncation, no 16k-cap failure."""
            from . import blob
            chash = content_hash(rec)
            stored, _offloaded = blob.pack(rec, f"{self._ns(etype)}/{leaf}", chash)
            return {"requestIdentifier": str(uuid.uuid4()),
                    "namespaces": [f"{self._ns(etype)}/{leaf}"],
                    "content": {"text": json.dumps(stored, separators=(",", ":"), ensure_ascii=False)},
                    "timestamp": now, "memoryStrategyId": sid}

        for etype, rows in by_type.items():
            kf = rows[0][0]
            incoming = {}
            for _kf, rec in rows:
                incoming[slug(rec[kf])] = (content_hash(rec), rec)
            incoming_srcs = set()
            for _kf, rec in rows:
                incoming_srcs.update((rec.get("provenance") or {}).get("sources") or [])

            existing = {}
            tok = None
            while True:
                kw = {"memoryId": self.memory_id, "namespacePath": self._ns(etype), "maxResults": 100}
                if tok:
                    kw["nextToken"] = tok
                r = ac.list_memory_records(**kw)
                for x in (r.get("memoryRecordSummaries") or []):
                    rid = x.get("memoryRecordId")
                    if not rid:
                        continue
                    try:
                        old = json.loads((x.get("content") or {}).get("text", "{}") or "{}")
                    except Exception:
                        old = {}
                    k = slug(str(old.get(kf, "")))
                    if not k:
                        continue
                    existing[k] = {"id": rid, "hash": old.get("content_hash") or content_hash(old),
                                   "srcs": set((old.get("provenance") or {}).get("sources") or []),
                                   "deprecated": (old.get("status") == "deprecated"), "rec": old}
                tok = r.get("nextToken")
                if not tok:
                    break

            prior_same = sum(1 for v in existing.values() if v["srcs"] & incoming_srcs)
            if len(incoming) == 0 or (prior_same > 0 and len(incoming) < prior_same * (1 - self.sanity_drop)):
                report[etype] = {"added": 0, "updated": 0, "skipped": 0, "deprecated": 0,
                                 "aborted": True, "prior": prior_same, "incoming": len(incoming), "changed_keys": []}
                continue

            plan = diff_plan({k: v["hash"] for k, v in existing.items()},
                             {k: h for k, (h, _r) in incoming.items()})
            deprecate = [k for k, v in existing.items()
                         if k not in incoming and not v["deprecated"] and (v["srcs"] & incoming_srcs)]
            changed = []
            if not dry_run:
                creates, deletes = [], []
                for k in plan["create"]:
                    creates.append(_mk(etype, k, incoming[k][1])); changed.append(k)
                for k in plan["update"]:
                    deletes.append({"memoryRecordId": existing[k]["id"]})
                    creates.append(_mk(etype, k, incoming[k][1])); changed.append(k)
                for k in deprecate:
                    v = existing[k]
                    dr = dict(v["rec"]); dr["status"] = "deprecated"; dr["deprecated_at"] = now.isoformat()
                    deletes.append({"memoryRecordId": v["id"]})
                    creates.append(_mk(etype, k, dr))
                # Batch writes at the API max (100/call): deletes first (frees old ids for
                # updates + tombstones), then creates. ~100x fewer calls than per-record
                # writes — a full-catalog green build now fits the Lambda timeout.
                for i in range(0, len(deletes), 100):
                    ac.batch_delete_memory_records(memoryId=self.memory_id, records=deletes[i:i + 100])
                for i in range(0, len(creates), 100):
                    ac.batch_create_memory_records(memoryId=self.memory_id, records=creates[i:i + 100])
            report[etype] = {"added": len(plan["create"]), "updated": len(plan["update"]),
                             "skipped": len(plan["skip"]), "deprecated": len(deprecate), "aborted": False,
                             "prior": prior_same, "incoming": len(incoming), "changed_keys": changed,
                             "dry_run": dry_run}
        return report
