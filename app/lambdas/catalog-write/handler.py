"""catalog-write — the `catalog_write` MCP tool (AgentCore Gateway).

Exposes the shared CatalogStore.upsert engine as a tool so ANY ecosystem/agent can write
durable, entity-keyed catalog records (non-destructive incremental diff-upsert with
provenance + soft-tombstone + sanity guard). This is what lets ingestion be modeled as an
ordinary Tessera ecosystem, and lets run-fed catalogs accrete knowledge from runs.

Tool input (JSON):
  { "domain": "<domain>",                     # for logging / future per-domain memory
    "records": [ {"entity_type": "...", "key_field": "...", "record": { ... }} ] }

Returns the per-type change report from the engine.
"""
import json
import os

from tessera_platform.catalog import CatalogStore, today_iso


def _coerce(records):
    """[{entity_type,key_field,record}] -> [(entity_type,key_field,record)] with a
    refresh_date stamped into provenance if absent."""
    today = today_iso()
    out = []
    for r in records or []:
        et = r.get("entity_type")
        kf = r.get("key_field")
        rec = r.get("record") or {}
        if not (et and kf and rec.get(kf)):
            continue
        prov = rec.get("provenance")
        if isinstance(prov, dict):
            prov.setdefault("refresh_date", today)
        else:
            rec["provenance"] = {"ingested_by": "catalog_write", "refresh_date": today}
        out.append((et, kf, rec))
    return out


def handler(event, context=None):
    # Accept either a direct invoke payload or an API-GW/Gateway-wrapped body.
    body = event.get("body") if isinstance(event, dict) else None
    req = json.loads(body) if isinstance(body, str) else (event or {})
    records = req.get("records") or []
    items = _coerce(records)
    if not items:
        return {"written": 0, "report": {}, "note": "no valid records (need entity_type, key_field, and the keyed field)"}
    store = CatalogStore(memory_id=req.get("memory_id") or os.environ.get("TESSERA_MEMORY_ID"))
    report = store.upsert(items)
    return {"domain": req.get("domain"), "written": len(items), "report": report}
