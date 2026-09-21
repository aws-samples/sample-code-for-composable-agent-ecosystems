"""tessera_platform.catalog.store — refresh state store (DynamoDB single-table).

THE single shared copy (previously duplicated byte-for-byte in lambdas/catalog-ingest/
and lambdas/catalog-api/). Backs incremental checkpoints (resume), the per-source
manifest (versioning), per-cluster synthesis digests, and live refresh progress.
Keyed (pk, sk):
  pk=DOMAIN#<d> sk=RUN           -> current run status/progress + concurrency lock
  pk=DOMAIN#<d> sk=SRC#<source>  -> per-source manifest (report/count/changed/last_refresh)
  pk=DOMAIN#<d> sk=CLU#<cluster> -> per-cluster synthesis checkpoint (in/out digest)
"""
import os
from datetime import datetime, timezone


def _table():
    name = os.environ.get("CATALOG_REFRESH_TABLE", "")
    if not name:
        return None
    import boto3
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(name)


def _now():
    return datetime.now(timezone.utc).isoformat()


def put_source_manifest(domain, source, report, count):
    """Checkpoint a source's diff-upsert result (idempotent — a resume overwrites it)."""
    t = _table()
    if not t:
        return
    changed_types = [k for k, v in (report or {}).items() if (v.get("added") or v.get("updated") or v.get("deprecated"))]
    aborted = any(v.get("aborted") for v in (report or {}).values())
    t.put_item(Item={
        "pk": f"DOMAIN#{domain}", "sk": f"SRC#{source}", "source": source,
        "status": "aborted" if aborted else "done",
        "count": int(count),
        "added": int(sum(v.get("added", 0) for v in report.values())),
        "updated": int(sum(v.get("updated", 0) for v in report.values())),
        "skipped": int(sum(v.get("skipped", 0) for v in report.values())),
        "deprecated": int(sum(v.get("deprecated", 0) for v in report.values())),
        "changed": bool(changed_types), "changed_types": changed_types,
        "last_refresh": _now(),
    })


def get_source_manifest(domain, source):
    t = _table()
    if not t:
        return None
    return t.get_item(Key={"pk": f"DOMAIN#{domain}", "sk": f"SRC#{source}"}).get("Item")


def put_source(domain, source, item):
    """Generic per-source write (used for agent sources, which have no diff report)."""
    t = _table()
    if not t:
        return
    it = {"pk": f"DOMAIN#{domain}", "sk": f"SRC#{source}", "source": source, "last_refresh": _now()}
    it.update(item or {})
    t.put_item(Item=it)


def put_cluster(domain, cluster, item):
    t = _table()
    if not t:
        return
    it = {"pk": f"DOMAIN#{domain}", "sk": f"CLU#{cluster}", "cluster": cluster, "last_synth": _now()}
    it.update(item or {})
    t.put_item(Item=it)


def get_cluster(domain, cluster):
    t = _table()
    if not t:
        return None
    return t.get_item(Key={"pk": f"DOMAIN#{domain}", "sk": f"CLU#{cluster}"}).get("Item")


def get_run(domain):
    t = _table()
    if not t:
        return None
    return t.get_item(Key={"pk": f"DOMAIN#{domain}", "sk": "RUN"}).get("Item")


def put_run(domain, item):
    t = _table()
    if not t:
        return
    it = {"pk": f"DOMAIN#{domain}", "sk": "RUN"}
    it.update(item or {})
    t.put_item(Item=it)


def query_domain(domain):
    """All items for a domain (RUN + SRC#* + CLU#*) — for the status endpoint."""
    t = _table()
    if not t:
        return []
    from boto3.dynamodb.conditions import Key
    out, kw = [], {"KeyConditionExpression": Key("pk").eq(f"DOMAIN#{domain}")}
    while True:
        r = t.query(**kw)
        out.extend(r.get("Items") or [])
        if not r.get("LastEvaluatedKey"):
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return out


def acquire_lock(domain, run_id):
    """Conditional-write lock: only one active refresh per domain."""
    t = _table()
    if not t:
        return True
    try:
        t.put_item(
            Item={"pk": f"DOMAIN#{domain}", "sk": "RUN", "run_id": run_id, "state": "running", "started": _now()},
            ConditionExpression="attribute_not_exists(pk) OR #s IN (:done, :failed, :aborted)",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":done": "done", ":failed": "failed", ":aborted": "aborted"},
        )
        return True
    except Exception:
        return False


def release_lock(domain, state="done"):
    r = get_run(domain) or {}
    r.update({"state": state, "ended": _now()})
    put_run(domain, r)


# ── blue/green version registry + atomic active pointer ──────────────────────────
#   pk=DOMAIN#<d> sk=ACTIVE    -> {version, promoted_at}  (readers resolve this)
#   pk=DOMAIN#<d> sk=VER#<v>   -> {version, status: building|built|validated|active|retired,
#                                  counts, digest, ...}
def get_active_version(domain):
    t = _table()
    if not t:
        return None
    it = t.get_item(Key={"pk": f"DOMAIN#{domain}", "sk": "ACTIVE"}).get("Item") or {}
    return it.get("version")


def set_active_version(domain, version):
    """Atomic promotion: point readers at `version`. Single conditional-free put."""
    t = _table()
    if not t:
        return
    t.put_item(Item={"pk": f"DOMAIN#{domain}", "sk": "ACTIVE", "version": version, "promoted_at": _now()})


def put_version(domain, version, item):
    t = _table()
    if not t:
        return
    it = {"pk": f"DOMAIN#{domain}", "sk": f"VER#{version}", "version": version, "updated": _now()}
    it.update(item or {})
    t.put_item(Item=it)


def get_version(domain, version):
    t = _table()
    if not t:
        return None
    return t.get_item(Key={"pk": f"DOMAIN#{domain}", "sk": f"VER#{version}"}).get("Item")


def list_versions(domain):
    t = _table()
    if not t:
        return []
    from boto3.dynamodb.conditions import Key
    out, kw = [], {"KeyConditionExpression": Key("pk").eq(f"DOMAIN#{domain}") & Key("sk").begins_with("VER#")}
    while True:
        r = t.query(**kw)
        out.extend(r.get("Items") or [])
        if not r.get("LastEvaluatedKey"):
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return out
