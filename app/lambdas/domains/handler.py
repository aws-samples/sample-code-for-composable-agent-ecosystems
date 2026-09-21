"""Domains CRUD (Phase D.2) — persist + list + get domain taxonomies.

A Domain owns a canonical, entity-keyed catalog taxonomy that ecosystems mount
and inherit (see docs/spec-domains-knowledge-taxonomy.md). This Lambda stores
domains as governed assets under domains/<id>.yaml in the ecosystems bucket.

Routes (REST):
  POST /api/domains          — create/update a domain (body = domain object)
  GET  /api/domains          — list domains (id, name, taxonomy count)
  GET  /api/domains/{id}      — get one domain
"""
import os
import re
import io
import json
import time
import uuid
import boto3
import yaml
from auth import get_user_from_event, is_admin

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
PREFIX = "domains/"
SAVE_FN = os.environ.get("SAVE_ECOSYSTEM_FN", "")
s3 = boto3.client("s3")
_lambda = boto3.client("lambda")


def _load(dom_id: str):
    try:
        return yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def _put(dom_id: str, record: dict):
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml",
                  Body=yaml.safe_dump(record, sort_keys=False).encode(), ContentType="application/x-yaml")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:64] or "domain"


def _cors():
    return {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
            "Content-Type": "application/json"}


def _save(req: dict, caller: dict) -> dict:
    # Accept either a raw domain object or {domain: {...}}.
    dom = req.get("domain") if isinstance(req.get("domain"), dict) else req
    dom_id = _slug(dom.get("id") or dom.get("domain") or dom.get("name") or "")
    if not dom_id:
        return {"statusCode": 400, "headers": _cors(),
                "body": json.dumps({"error": "domain needs id or name"})}
    existing = _load(dom_id)
    # Governance: existing domains are shared — only the owner (or an admin) may
    # edit the taxonomy directly. Others must propose a change (see _propose).
    if existing:
        owner = existing.get("owner", "")
        if owner and caller.get("user_id") != owner and not is_admin(caller):
            return {"statusCode": 403, "headers": _cors(), "body": json.dumps({
                "error": "not the domain owner — submit a proposal instead",
                "owner_email": existing.get("owner_email", ""), "can_propose": True})}
    record = {
        "domain": dom_id,
        "name": dom.get("name") or dom_id,
        "description": dom.get("description", ""),
        # Governance fields are owned by the server, not the client.
        "owner": (existing or {}).get("owner") or caller.get("user_id", ""),
        "owner_email": (existing or {}).get("owner_email") or caller.get("email", ""),
        "version": int((existing or {}).get("version") or 1),
        "status": dom.get("status", "active"),
        "taxonomy": dom.get("taxonomy", []) or [],
        "conclusions_never_cataloged": dom.get("conclusions_never_cataloged", []) or [],
        "seeded_from": dom.get("seeded_from", (existing or {}).get("seeded_from", [])) or [],
        "ecosystems": (existing or {}).get("ecosystems", []) or [],
        "pending_changes": (existing or {}).get("pending_changes", []) or [],
        "change_log": (existing or {}).get("change_log", []) or [],
        # Catalog descriptor (view/ingestion/synthesis) — drives the generic Catalog
        # browser + ingestion. Client may set it; preserved across edits if omitted.
        "catalog": dom.get("catalog") or (existing or {}).get("catalog") or {},
    }
    if not existing:
        record["change_log"] = [{"version": 1, "action": "created", "by": caller.get("email", ""),
                                 "at": int(time.time()), "detail": f"{len(record['taxonomy'])} catalogs"}]
    _put(dom_id, record)
    return {"statusCode": 200, "headers": _cors(),
            "body": json.dumps({"status": "saved", "domain": dom_id,
                                "key": f"{PREFIX}{dom_id}.yaml", "record": record})}


def _propose(dom_id: str, req: dict, caller: dict) -> dict:
    """Anyone may PROPOSE a change to a shared domain (add/remove a catalog). It lands
    as a pending change for the owner to ratify — it does NOT modify the taxonomy."""
    dom = _load(dom_id)
    if not dom:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "domain not found"})}
    kind = req.get("kind")  # add_catalog | remove_catalog
    payload = req.get("payload") or {}
    if kind not in ("add_catalog", "remove_catalog"):
        return {"statusCode": 400, "headers": _cors(), "body": json.dumps({"error": "kind must be add_catalog|remove_catalog"})}
    change = {"id": uuid.uuid4().hex[:12], "kind": kind, "payload": payload,
              "proposed_by": caller.get("email", "") or caller.get("user_id", ""),
              "proposed_at": int(time.time()), "status": "pending"}
    dom.setdefault("pending_changes", []).append(change)
    _put(dom_id, dom)
    return {"statusCode": 200, "headers": _cors(), "body": json.dumps({
        "status": "proposed", "change": change,
        "affects_ecosystems": dom.get("ecosystems", []) or []})}


def _ratify(dom_id: str, req: dict, caller: dict) -> dict:
    """Owner (or admin) approves/rejects a pending change. Approve mutates the taxonomy,
    bumps the version, and appends to the change log (audit). Reject just drops it."""
    dom = _load(dom_id)
    if not dom:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "domain not found"})}
    owner = dom.get("owner", "")
    if owner and caller.get("user_id") != owner and not is_admin(caller):
        return {"statusCode": 403, "headers": _cors(), "body": json.dumps({
            "error": "only the domain owner may ratify", "owner_email": dom.get("owner_email", "")})}
    change_id = req.get("change_id"); decision = req.get("decision")
    pend = dom.get("pending_changes", []) or []
    change = next((c for c in pend if c.get("id") == change_id), None)
    if not change:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "pending change not found"})}
    dom["pending_changes"] = [c for c in pend if c.get("id") != change_id]
    if decision == "approve":
        tax = dom.setdefault("taxonomy", [])
        p = change.get("payload") or {}
        if change["kind"] == "add_catalog":
            if not any(t.get("type") == p.get("type") for t in tax):
                tax.append({"type": p.get("type"), "key": p.get("key", "{id}"),
                            "description": p.get("description", ""), "status": "active"})
        elif change["kind"] == "remove_catalog":
            # Deprecate, don't delete — preserve knowledge.
            for t in tax:
                if t.get("type") == p.get("type"):
                    t["status"] = "deprecated"
        dom["version"] = int(dom.get("version", 1)) + 1
        dom.setdefault("change_log", []).append({
            "version": dom["version"], "action": f"{change['kind']}:{p.get('type')}",
            "by": caller.get("email", ""), "at": int(time.time()),
            "proposed_by": change.get("proposed_by", "")})
    _put(dom_id, dom)
    return {"statusCode": 200, "headers": _cors(), "body": json.dumps({
        "status": decision, "version": dom.get("version"),
        "affects_ecosystems": dom.get("ecosystems", []) or []})}


def _get(dom_id: str) -> dict:
    try:
        body = s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml")["Body"].read()
        return {"statusCode": 200, "headers": _cors(), "body": json.dumps(yaml.safe_load(body), default=str)}
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "not found"})}


def _list() -> dict:
    out = []
    token = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": PREFIX}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents", []) or []:
            key = obj["Key"]
            if not key.endswith(".yaml"):
                continue
            # Only top-level flat descriptors `domains/<id>.yaml` are domains. Skip
            # subdir assets — `domains/<id>/catalog.yaml` (declarative ingest descriptors)
            # and anything under `domains/schemas/` — they are NOT domains and would
            # otherwise pollute the list as e.g. "<id>/catalog".
            if "/" in key[len(PREFIX):]:
                continue
            try:
                d = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()) or {}
            except Exception:
                continue
            out.append({
                "id": d.get("domain") or key[len(PREFIX):-5],
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "taxonomy_count": len(d.get("taxonomy", []) or []),
                "version": d.get("version", 1),
                "seeded_from": d.get("seeded_from", []),
                "has_catalog": bool((d.get("catalog") or {}).get("view")),
            })
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return {"statusCode": 200, "headers": _cors(), "body": json.dumps(sorted(out, key=lambda x: x["id"]))}


def _declared_catalogs(eco: dict) -> list:
    out = []
    for ns in (eco.get("memory", {}) or {}).get("shared_namespaces", []) or []:
        path = (ns.get("path") or "").strip("/")
        if path.startswith("catalog/"):
            out.append(path[len("catalog/"):].rstrip("/"))
    return out


def _assign(dom_id: str, req: dict) -> dict:
    """Link an ecosystem to a domain (recorded on the domain asset — non-destructive)
    and compute the apply-plan (what the ecosystem rewrite WOULD change). Does not
    mutate the ecosystem YAML; the destructive rewire + recompile is the gated apply."""
    try:
        dom = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "domain not found"})}
    eco_id = req.get("ecosystem_id")
    if not eco_id:
        return {"statusCode": 400, "headers": _cors(), "body": json.dumps({"error": "ecosystem_id required"})}
    agents = req.get("agents", []) or []

    # Record the link on the domain (dedupe), persist.
    ecos = dom.get("ecosystems", []) or []
    if eco_id not in ecos:
        ecos.append(eco_id)
    dom["ecosystems"] = ecos
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml",
                  Body=yaml.safe_dump(dom, sort_keys=False).encode(), ContentType="application/x-yaml")

    # Compute the apply-plan against the ecosystem (read-only).
    try:
        eco = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read())
    except s3.exceptions.NoSuchKey:
        eco = {}
    existing = set(_declared_catalogs(eco))
    dom_types = [t.get("type") for t in dom.get("taxonomy", []) or []]
    catalogs_to_add = [t for t in dom_types if t and t not in existing]
    writers, leak_fixes = [], []
    eco_agents = {a.get("id"): a for a in eco.get("agents", []) or []}
    for a in agents:
        disp, et, aid = a.get("disposition"), a.get("entity_type"), a.get("id")
        if disp in ("use", "promote") and et:
            writers.append({"agent": aid, "writes": et})
        if disp == "none":
            mem = (eco_agents.get(aid, {}) or {}).get("memory", {}) or {}
            if any("/catalog/" in (str(p)) for p in (mem.get("writable") or [])):
                leak_fixes.append({"agent": aid, "remove_catalog_write": True})
    return {"statusCode": 200, "headers": _cors(), "body": json.dumps({
        "assigned": True, "domain": dom_id, "ecosystem": eco_id,
        "apply_plan": {
            "mount_domain": dom_id,
            "catalogs_to_add": catalogs_to_add,
            "catalogs_inherited": dom_types,
            "agent_writers": writers,
            "leak_fixes": leak_fixes,
            "note": "Preview only — the ecosystem YAML rewrite + ASL recompile is the gated apply step (D.3).",
        },
    })}


def _seg(path: str) -> str:
    return (path or "").strip("/").split("/")[-1]


def _matches(t: str, seg: str) -> bool:
    """Loose singular/plural match so a domain type 'property' doesn't duplicate an
    existing 'properties' namespace."""
    t = (t or "").lower().replace("_", "-"); seg = (seg or "").lower().replace("_", "-")
    if not t or not seg:
        return False
    if t == seg or t + "s" == seg or seg + "s" == t:
        return True
    if t.endswith("y") and t[:-1] + "ies" == seg:
        return True
    if seg.endswith("y") and seg[:-1] + "ies" == t:
        return True
    return False


def _apply(dom_id: str, req: dict) -> dict:
    """Wire an ecosystem to a domain FOR REAL: add domains[], add the domain's catalog
    namespaces (normalized, no dupes) to memory.shared_namespaces with the right
    writers, remove conclusion-leak writers, preserve comments (ruamel), back up the
    original, and recompile the state machine."""
    from ruamel.yaml import YAML
    eco_id = req.get("ecosystem_id")
    if not eco_id:
        return {"statusCode": 400, "headers": _cors(), "body": json.dumps({"error": "ecosystem_id required"})}
    agents = req.get("agents", []) or []
    try:
        dom = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}.yaml")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "domain not found"})}
    try:
        raw = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": _cors(), "body": json.dumps({"error": "ecosystem not found"})}

    ry = YAML(); ry.preserve_quotes = True; ry.width = 4096
    eco = ry.load(raw.decode())

    # Backup the original before mutating.
    import time
    backup_key = f"ecosystems/_backups/{eco_id}-predomain-{int(time.time())}.yaml"
    s3.put_object(Bucket=BUCKET, Key=backup_key, Body=raw, ContentType="application/x-yaml")

    # 1) domains[]
    doms = list(eco.get("domains") or [])
    if dom_id not in doms:
        doms.append(dom_id)
    eco["domains"] = doms

    # 2) add domain catalogs to shared_namespaces (normalized, no dupes)
    mem = eco.get("memory")
    if mem is None:
        mem = {}; eco["memory"] = mem
    sns = mem.get("shared_namespaces")
    if sns is None:
        sns = []; mem["shared_namespaces"] = sns
    existing_segs = [_seg(ns.get("path", "")) for ns in sns]
    writers_by_type: dict = {}
    for a in agents:
        if a.get("disposition") in ("use", "promote") and a.get("entity_type"):
            writers_by_type.setdefault(a["entity_type"], []).append(a["id"])
    added, reused, inherited = [], [], []
    for t in dom.get("taxonomy", []) or []:
        ttype = t.get("type")
        if not ttype:
            continue
        if any(_matches(ttype, seg) for seg in existing_segs):
            reused.append(ttype); continue
        prod = writers_by_type.get(ttype)
        if prod:
            # This eco PRODUCES this entity -> producing agent(s) write + auto-persist.
            sns.append({"name": ttype.replace("-", "_") + "_catalog",
                        "path": f"/catalog/{ttype}/", "write": prod, "read": ["all"], "auto_persist": True})
            added.append(ttype)
        else:
            # Inherited READ-ONLY: no producer in this eco, but it can READ the domain's
            # accumulated dossiers (the compounding payoff). No write / no auto_persist,
            # so it can never become the write:[all]+auto_persist fan-out anti-pattern.
            sns.append({"name": ttype.replace("-", "_") + "_catalog",
                        "path": f"/catalog/{ttype}/", "write": [], "read": ["all"], "auto_persist": False})
            inherited.append(ttype)
        existing_segs.append(ttype)

    # 3) leak fixes — remove conclusion agents from any namespace write list
    none_agents = {a["id"] for a in agents if a.get("disposition") == "none"}
    leak_removed = []
    for ns in sns:
        w = ns.get("write")
        if isinstance(w, list):
            removed = [x for x in w if x in none_agents]
            if removed:
                ns["write"] = [x for x in w if x not in none_agents]
                leak_removed.append({"namespace": ns.get("name"), "removed": removed})

    # write back (comments preserved)
    buf = io.BytesIO(); ry.dump(eco, buf)
    s3.put_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml", Body=buf.getvalue(), ContentType="application/x-yaml")

    # 4) recompile the state machine via save-ecosystem's deploy path
    recompiled, warnings, recompile_error = False, [], None
    if SAVE_FN:
        try:
            inv = _lambda.invoke(FunctionName=SAVE_FN, Payload=json.dumps({
                "httpMethod": "POST", "resource": "/api/ecosystems/{id}/deploy",
                "path": f"/api/ecosystems/{eco_id}/deploy", "pathParameters": {"id": eco_id},
            }).encode())
            body = json.loads(json.loads(inv["Payload"].read() or b"{}").get("body", "{}") or "{}")
            recompiled = bool(body.get("deployed")); warnings = body.get("warnings", []) or []
            recompile_error = body.get("error")
        except Exception as e:
            recompile_error = f"{type(e).__name__}: {str(e)[:200]}"

    return {"statusCode": 200, "headers": _cors(), "body": json.dumps({
        "applied": True, "domain": dom_id, "ecosystem": eco_id,
        "added_catalogs": added, "reused_catalogs": reused, "inherited_readonly": inherited, "leak_removed": leak_removed,
        "recompiled": recompiled, "warnings": warnings, "recompile_error": recompile_error,
        "backup_key": backup_key,
    })}


def _save_ingest(dom_id: str, req: dict) -> dict:
    """Persist the wizard-authored DECLARATIVE ingest (spec-declarative-ingest.md §6):
      domains/<id>/catalog.yaml   — the `ingest:` block (sources + refresh + synthesis)
      domains/schemas/<id>.records.json — the records schema (optional)
    The generic ingest Lambda loads these to run ingestion — zero per-domain code/CDK."""
    dom_id = _slug(dom_id)
    ingest = req.get("ingest") or {}
    if not ingest.get("sources"):
        return {"statusCode": 400, "headers": _cors(), "body": json.dumps({"error": "ingest.sources required"})}
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}{dom_id}/catalog.yaml",
                  Body=yaml.safe_dump({"ingest": ingest}, sort_keys=False).encode(),
                  ContentType="application/x-yaml")
    schema = req.get("schema")
    wrote_schema = False
    if isinstance(schema, dict) and schema:
        s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}schemas/{dom_id}.records.json",
                      Body=json.dumps(schema, indent=2).encode(), ContentType="application/json")
        wrote_schema = True
    return {"statusCode": 200, "headers": _cors(),
            "body": json.dumps({"domain": dom_id, "sources": len(ingest.get("sources") or []),
                                "catalog_yaml": f"{PREFIX}{dom_id}/catalog.yaml", "schema_written": wrote_schema})}


def handler(event, context):
    method = (event.get("httpMethod") or "GET").upper()
    params = event.get("pathParameters") or {}
    path = event.get("resource") or event.get("path") or ""
    try:
        body = event.get("body")
        req = json.loads(body) if isinstance(body, str) else (body or {})
        caller = get_user_from_event(event)
        if method == "POST" and path.endswith("/assign") and params.get("id"):
            return _assign(params["id"], req)
        if method == "POST" and path.endswith("/ingest") and params.get("id"):
            return _save_ingest(params["id"], req)
        if method == "POST" and path.endswith("/apply") and params.get("id"):
            return _apply(params["id"], req)
        if method == "POST" and path.endswith("/propose") and params.get("id"):
            return _propose(params["id"], req, caller)
        if method == "POST" and path.endswith("/ratify") and params.get("id"):
            return _ratify(params["id"], req, caller)
        if method == "POST":
            return _save(req, caller)
        if params.get("id"):
            return _get(params["id"])
        return _list()
    except Exception as e:
        return {"statusCode": 500, "headers": _cors(),
                "body": json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"})}
