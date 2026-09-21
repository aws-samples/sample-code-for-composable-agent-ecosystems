"""Re-grounding router + worker (spec-regrounding-bus.md).

Consumes a ChangeSignal from the `tessera-regrounding` EventBridge bus, maps it to
SUBSCRIBING catalogs via a data-driven registry, and runs the appropriate worker.
The SOURCE never decides consumers — this router does.

Env: TESSERA_MEMORY_ID, CATALOG_REFRESH_TABLE, ECOSYSTEMS_BUCKET, REGION
Built-now worker: self-eco-reground (incremental self-model update).
Future workers (catalog-build/version-check) are registry rows + stubs.
"""
import os, re, json, fnmatch, datetime
import boto3, yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEM = os.environ.get("TESSERA_MEMORY_ID", "")
ECOS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
os.environ.setdefault("CATALOG_REFRESH_TABLE", os.environ.get("CATALOG_REFRESH_TABLE", "tessera-dev-catalog-refresh"))

# ── Subscription registry (data-driven; move to S3/DynamoDB to add subscribers as config) ──
SUBSCRIPTIONS = [
    {"subscriber": "tessera-self", "on": {"change_type": "ecosystem.*"},
     "worker": "self-eco-reground", "mode": "incremental"},
    # ── FUTURE (design only — stubs below log intent, do not act) ──
    {"subscriber": "cyber-security-risk", "on": {"change_type": "authority.changed", "subject": "att&ck|cve|owasp|atlas"},
     "worker": "catalog-build", "mode": "version-check"},
    {"subscriber": "au-financial-advice", "on": {"change_type": "figure.changed", "subject": "ato|asic|abs|rba"},
     "worker": "catalog-build", "mode": "version-check"},
]


def _matches(sub, change_type, subject):
    on = sub["on"]
    if not fnmatch.fnmatch(change_type or "", on.get("change_type", "*")):
        return False
    subj_pat = on.get("subject")
    return True if not subj_pat else bool(re.search(subj_pat, subject or "", re.I))


def _eco_record(eco_id):
    """Deterministic ecosystem record for the self-model (no Bedrock)."""
    s3 = boto3.client("s3", region_name=REGION)
    d = yaml.safe_load(s3.get_object(Bucket=ECOS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()) or {}
    eco = d.get("ecosystem") or {}
    name = (eco.get("name") if isinstance(eco, dict) else None) or d.get("name") or eco_id
    desc = (eco.get("description") if isinstance(eco, dict) else None) or d.get("description") or ""
    doms = d.get("domains") or d.get("domain") or []
    doms = doms if isinstance(doms, list) else [doms]
    agents = [a.get("id") for a in (d.get("agents") or []) if a.get("id")]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "eco_id": eco_id, "name": name, "purpose": " ".join(desc.split())[:200],
        "domains_mounted": [str(x) for x in doms if x], "agents": agents,
        "quality_posture": ", ".join(f"{k}:{v}" for k, v in (d.get("quality") or {}).items() if k in ("challenger", "grounding")) or "none",
        "deploy_status": "deployed",
        # cheap-per-event: mark classification stale; the scheduled sweep re-classifies.
        "alignment": {"stale": True},
        "provenance": {"layer": "runtime-observed", "source_ref": f"eco:{eco_id}",
                       "confidence": "medium", "captured_at": now},
    }


def _reground_self_eco(eco_id):
    from tessera_platform.catalog import engine, store as refresh
    active = refresh.get_active_version("tessera-self")
    if not active:
        return {"status": "skipped", "reason": "no active tessera-self version"}
    store = engine.CatalogStore(memory_id=MEM, version=active)
    rec = _eco_record(eco_id)
    # PRESERVE prior signal: refresh operational fields but keep the existing
    # classification/maturity; only FLAG stale (a scheduled sweep re-classifies).
    # Never destroy classification on a deploy event.
    try:
        prior = next((r for r in store.load_type("ecosystem") if r.get("eco_id") == eco_id), None)
    except Exception:
        prior = None
    if prior:
        rec["maturity"] = prior.get("maturity", rec.get("maturity"))
        prior_align = prior.get("alignment") or {}
        rec["alignment"] = {**prior_align, "stale": True}
    store.upsert([("ecosystem", "eco_id", rec)])
    return {"status": "ok", "subscriber": "tessera-self", "version": active, "eco_id": eco_id,
            "preserved_classification": bool(prior and (prior.get("alignment") or {}).get("classification"))}


def handler(event, context):
    detail = event.get("detail") if isinstance(event, dict) and "detail" in event else event
    detail = detail or {}
    change_type = detail.get("change_type", "")
    subject = detail.get("subject", "")
    # Events are UNTRUSTED input. `subject` flows into an S3 key + catalog namespace, so
    # allowlist it to an ecosystem-id slug; reject anything else (no path/key injection).
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,120}", subject or ""):
        print(json.dumps({"rejected": "invalid subject", "subject": str(subject)[:120]}))
        return {"statusCode": 400, "error": "invalid subject"}
    if not re.fullmatch(r"[a-z0-9][a-z0-9.*_-]{0,60}", change_type or ""):
        print(json.dumps({"rejected": "invalid change_type", "change_type": str(change_type)[:60]}))
        return {"statusCode": 400, "error": "invalid change_type"}
    results = []
    for sub in SUBSCRIPTIONS:
        if not _matches(sub, change_type, subject):
            continue
        worker = sub["worker"]
        if worker == "self-eco-reground":
            try:
                results.append(_reground_self_eco(subject))
            except Exception as e:
                results.append({"status": "error", "subscriber": sub["subscriber"], "error": f"{type(e).__name__}: {e}"})
        else:
            # FUTURE: dispatch catalog-build / version-check (start the domain build SM).
            results.append({"status": "todo", "subscriber": sub["subscriber"], "worker": worker,
                            "note": "future worker — logged, not executed"})
    print(json.dumps({"change_type": change_type, "subject": subject, "dispatched": results}))
    return {"statusCode": 200, "dispatched": results}
