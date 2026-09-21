"""catalog-api — read-only Risk Catalog Browser API (private, JWT-authorized).

Serves the cyber-security-risk domain catalog (AgentCore Memory) to the frontend
Risk Browser. Fronted by an API Gateway HTTP API with a Cognito JWT authorizer
(NO public Lambda Function URL — see cdk/stacks/ingest_stack.py); the gateway
verifies the Cognito token and this handler trusts the verified claims. CORS is
restricted to the frontend origin. Lives in the separate ingest stack because the
main stack is at CloudFormation's 500-resource limit.

Routes (HTTP API path, proxied as /catalog/{proxy+}):
  GET /catalog/types                 -> [{type, count}]
  GET /catalog/{type}?q=&tactic=&severity=&limit=  -> {type, count, records[]}
  GET /catalog/{type}/{key}          -> one record

The parse/filter/shape helpers are PURE (no boto3, no network) so they unit-test.
"""
import os, json, re
from concurrent.futures import ThreadPoolExecutor

REGION = os.environ.get("AWS_REGION", "us-east-1")
POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
CATALOG_DOMAIN = os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")

# Fallback types if the domain descriptor can't be loaded (kept in sync with the
# cyber-security-risk taxonomy). Normally the entity types + ingest sources come from
# the domain record (domains/<CATALOG_DOMAIN>.yaml), so this Lambda is domain-agnostic.
FALLBACK_TYPES = [
    "risk", "attack-technique", "security-control", "vulnerability", "agentic-threat",
    "breach", "cyber-target-profile", "vendor", "indicator-of-compromise", "asset",
    "defense-pattern", "trust-boundary", "org-profile", "risk-method-profile",
]
_DESC: dict = {}


def _descriptor(domain: str = "") -> dict:
    """Lazy-load + cache a domain record (taxonomy + catalog descriptor) from S3."""
    dom = domain or CATALOG_DOMAIN
    if dom in _DESC:
        return _DESC[dom]
    d = {}
    try:
        import boto3, yaml
        body = boto3.client("s3", region_name=REGION).get_object(
            Bucket=ECOSYSTEMS_BUCKET, Key=f"domains/{dom}.yaml")["Body"].read()
        d = yaml.safe_load(body) or {}
    except Exception as e:
        print(f"[descriptor] load failed for {dom}: {type(e).__name__}: {str(e)[:200]}")
    _DESC[dom] = d
    return d


def _known_types(domain: str = "") -> list:
    tax = [t.get("type") for t in (_descriptor(domain).get("taxonomy") or []) if t.get("type")]
    return tax or FALLBACK_TYPES


def _ingest_sources(domain: str = "") -> list:
    srcs = (((_descriptor(domain).get("catalog") or {}).get("ingestion") or {}).get("sources") or [])
    active = [s.get("id") for s in srcs if s.get("id") and s.get("status", "active") != "planned"]
    return active or ["mitre-attack", "cisa-kev"]


def _active_source_records(domain: str = "") -> list:
    """Full source descriptors (not just ids) for active (non-planned) sources —
    needed to route ingestion by `mode` (fetch -> Lambda, agent -> state machine)."""
    srcs = (((_descriptor(domain).get("catalog") or {}).get("ingestion") or {}).get("sources") or [])
    return [dict(s) for s in srcs if s.get("id") and s.get("status", "active") != "planned"]


def _agent_mission(src: dict) -> str:
    """Build the `## INPUT CONTEXT` (key: value lines) the catalog-agent-ingest eco
    reads. `produces:` is parsed by agent-invoker to template the write namespace."""
    produces = src.get("produces") or []
    lines = [
        f"source: {src.get('id','')}",
        f"authoritative_url: {src.get('authoritative_url','')}",
        f"produces: {produces[0] if produces else ''}",
        f"key_field: {src.get('key_field','')}",
        f"id_scheme: {src.get('id_scheme','')}",
        f"record_schema: name, framework, description, references",
        f"scope: {src.get('scope','')}",
    ]
    return "\n".join(lines)


def _active_version(domain: str = "") -> str:
    """The catalog version this domain currently serves (blue/green), or None => legacy
    unversioned namespace. Resolved per request so a promotion is reflected immediately."""
    try:
        from tessera_platform.catalog import store as refresh_store
        return refresh_store.get_active_version(domain or CATALOG_DOMAIN)
    except Exception:
        return None


def _base(domain: str = "") -> str:
    """Namespace base for reads: /tessera/catalog/<version> when a version is active,
    else the legacy /tessera/catalog."""
    v = _active_version(domain)
    return f"/tessera/catalog/{v}" if v else "/tessera/catalog"


# ── pure helpers (unit-tested) ───────────────────────────────────────────────────
def parse_route(path: str) -> list:
    """Function URL rawPath -> route segments, with a leading 'catalog' stripped."""
    parts = [p for p in (path or "").strip("/").split("/") if p]
    if parts and parts[0] == "catalog":
        parts = parts[1:]
    return parts


def record_from_summary(summary: dict, resolve: bool = False) -> dict:
    """Parse a memory record summary's JSON content; attach its namespace as _ns."""
    txt = (summary.get("content") or {}).get("text", "") or ""
    ns = (summary.get("namespaces") or [None])[0]
    try:
        rec = json.loads(txt)
        if not isinstance(rec, dict):
            rec = {"_raw": txt[:4000]}
    except Exception:
        rec = {"_raw": txt[:4000]}
    if resolve and isinstance(rec, dict) and rec.get("content_ref"):
        # Oversized dossier offloaded to S3 — reconstitute the FULL record for detail views.
        try:
            from tessera_platform.catalog import blob
            rec = blob.resolve(rec)
        except Exception:
            pass
    rec["_ns"] = ns
    return rec


def filter_records(records: list, q=None, tactic=None, severity=None, framework=None) -> list:
    """Filter parsed records by free-text q, ATT&CK tactic, severity, and framework."""
    out = records
    if framework:
        fl = framework.lower()
        out = [r for r in out if str(r.get("framework", "")).lower() == fl]
    if severity:
        sl = severity.lower()
        out = [r for r in out if str(r.get("severity_default", "")).lower() == sl]
    if tactic:
        tl = tactic.lower()
        out = [r for r in out if tl in [str(t).lower() for t in (r.get("tactics") or [])]]
    if q:
        ql = q.lower()
        out = [r for r in out if ql in json.dumps(r, default=str).lower()]
    return out


# ── auth (JWKS-verified Cognito ID token) ────────────────────────────────────────
_JWK_CLIENT = None


def _verify_token(token: str):
    global _JWK_CLIENT
    if not token or not POOL_ID:
        return None
    try:
        import jwt
        from jwt import PyJWKClient
        if _JWK_CLIENT is None:
            _JWK_CLIENT = PyJWKClient(
                f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json")
        key = _JWK_CLIENT.get_signing_key_from_jwt(token).key
        opts = {"verify_aud": bool(CLIENT_ID)}
        return jwt.decode(
            token, key, algorithms=["RS256"],
            audience=CLIENT_ID if CLIENT_ID else None,
            issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}",
            options=opts,
        )
    except Exception as e:
        print(f"[auth] token verification failed: {type(e).__name__}: {str(e)[:200]}")
        return None


# ── memory reads (lazy boto3) ────────────────────────────────────────────────────
def _mem():
    import boto3
    return boto3.client("bedrock-agentcore", region_name=REGION), os.environ["TESSERA_MEMORY_ID"]


def _list_ns(ac, memory_id, prefix, cap=2000):
    out, tok = [], None
    while len(out) < cap:
        kw = {"memoryId": memory_id, "namespacePath": prefix, "maxResults": 100}
        if tok:
            kw["nextToken"] = tok
        r = ac.list_memory_records(**kw)
        out.extend(r.get("memoryRecordSummaries") or [])
        tok = r.get("nextToken")
        if not tok:
            break
    return out


def _list_page(ac, memory_id, prefix, limit, next_token):
    """ONE page of records (server-side pagination) — avoids listing an entire large type."""
    kw = {"memoryId": memory_id, "namespacePath": prefix, "maxResults": max(1, min(limit, 100))}
    if next_token:
        kw["nextToken"] = next_token
    r = ac.list_memory_records(**kw)
    return (r.get("memoryRecordSummaries") or []), r.get("nextToken")


_TYPES_CACHE: dict = {}   # base -> (epoch, result); counts only change on build/refresh
_TYPES_TTL = 300          # seconds


def _list_types(ac, memory_id, domain=""):
    # Counting each type means paging its whole namespace; doing that sequentially per
    # type made /catalog/types ~15s. Count all types CONCURRENTLY (bounded pool). Order
    # is preserved (pool.map), so the UI's taxonomy ordering is unchanged. Results are
    # cached briefly per catalog version (base includes the active version), so repeat
    # loads on a warm instance are instant — counts only change on a build/refresh.
    import time
    base = _base(domain)
    known = _known_types(domain)
    if not known:
        return []
    # Cache key MUST include the domain: versionless domains all share the legacy
    # `base` (/tessera/catalog), but `known` (and therefore the result) is
    # domain-specific — keying by base alone leaked one domain's type counts to every
    # other domain (pipeline/browse showed the wrong types, or 0).
    ckey = f"{base}||{domain}"
    hit = _TYPES_CACHE.get(ckey)
    if hit and (time.time() - hit[0]) < _TYPES_TTL:
        return hit[1]

    def _count(t):
        return t, len(_list_ns(ac, memory_id, f"{base}/{t}"))

    with ThreadPoolExecutor(max_workers=min(8, len(known))) as pool:
        counts = list(pool.map(_count, known))
    result = [{"type": t, "count": n} for t, n in counts if n]
    _TYPES_CACHE[ckey] = (time.time(), result)
    return result


# ── http ─────────────────────────────────────────────────────────────────────────
def _cors(status, body):
    # CORS is handled by the API Gateway HTTP API (cors_preflight). The Lambda only
    # returns content — adding ACAO here too would duplicate the header.
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=str),
    }


def _claims(event):
    return (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}


def _is_admin(event):
    g = _claims(event).get("cognito:groups", "")
    return "admin" in (g if isinstance(g, list) else str(g))


def _handle_post(event, parts):
    """Privileged POST actions — admin-group only. Currently: trigger catalog ingestion."""
    if not _is_admin(event):
        return _cors(403, {"error": "admin group required"})
    action = parts[0] if parts else ""
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        body = {}
    if action == "ingest":
        import boto3, uuid
        src = body.get("source", "all")
        domain = body.get("domain", "")
        try:
            limit = int(body.get("limit", 0))
        except (TypeError, ValueError):
            limit = 0
        # Resolve the active source descriptors, then route each by ingestion mode.
        all_srcs = _active_source_records(domain)
        if src != "all":
            all_srcs = [s for s in all_srcs if s.get("id") == src]
        fetch_srcs = [s for s in all_srcs if (s.get("mode") or "fetch") != "agent"]
        agent_srcs = [s for s in all_srcs if (s.get("mode") or "fetch") == "agent"]

        started_fetch, started_agent, errors = [], [], []

        # fetch-mode -> deterministic catalog-ingest Lambda (async, one per source)
        if fetch_srcs:
            fn = os.environ.get("CATALOG_INGEST_FUNCTION", "")
            if not fn:
                return _cors(500, {"error": "CATALOG_INGEST_FUNCTION not configured"})
            lam = boto3.client("lambda", region_name=REGION)
            for s in fetch_srcs:
                lam.invoke(FunctionName=fn, InvocationType="Event",
                           Payload=json.dumps({"source": s["id"], "limit": limit}).encode())
                started_fetch.append(s["id"])

        # agent-mode -> ONE catalog-agent-ingest state-machine execution per source
        # (pattern A: parallel per-source agents). Grounded LLM extraction of prose/PDF
        # taxonomies that have no clean machine-readable feed.
        if agent_srcs:
            sm_arn = os.environ.get("CATALOG_AGENT_STATE_MACHINE_ARN", "")
            if not sm_arn:
                return _cors(500, {"error": "CATALOG_AGENT_STATE_MACHINE_ARN not configured"})
            sfn = boto3.client("stepfunctions", region_name=REGION)
            user_sub = _claims(event).get("sub", "") or "system-catalog-ingest"
            for s in agent_srcs:
                session_id = f"catalog-agent-ingest-{uuid.uuid4().hex[:8]}"
                try:
                    sfn.start_execution(
                        stateMachineArn=sm_arn,
                        name=session_id,
                        input=json.dumps({
                            "mission": (
                                f"Ingest the '{s.get('label', s['id'])}' taxonomy into the catalog.\n\n"
                                "## INGESTION SPEC\n" + _agent_mission(s)
                            ),
                            "session_id": session_id,
                            "user_id": user_sub,
                            "dry_run": False,
                            "run_config": {},
                            "dependency_context": "",
                            "input_context": _agent_mission(s),
                            "results": {},
                        }),
                    )
                    started_agent.append(s["id"])
                except Exception as e:
                    errors.append(f"{s['id']}: {type(e).__name__}: {str(e)[:160]}")

        return _cors(202, {
            "started_fetch": started_fetch,
            "started_agent": started_agent,
            "errors": errors,
            "note": "Fetch sources land in ~1–2 min; agent sources take longer (grounded LLM run). Reload the catalog.",
        })
    if action == "refresh":
        return _handle_refresh(event, body)
    # Blue/green: atomically promote a validated green version to active (single write).
    if action == "promote":
        from tessera_platform.catalog import orchestrate
        domain = body.get("domain", "") or CATALOG_DOMAIN
        version = body.get("version", "")
        if not version:
            return _cors(400, {"error": "version required"})
        try:
            return _cors(200, orchestrate.promote_version(domain, version))
        except Exception as e:
            return _cors(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})
    # Wizard: persist the authored DECLARATIVE ingest (spec §6) to S3 so the generic ingest
    # Lambda can load it: domains/<id>/catalog.yaml + domains/schemas/<id>.records.json.
    if action == "save-ingest":
        import boto3, re as _re
        domain = _re.sub(r"[^a-z0-9-]+", "-", (body.get("domain") or "").lower()).strip("-")
        ing = body.get("ingest") or {}
        if not domain or not ing.get("sources"):
            return _cors(400, {"error": "domain + ingest.sources required"})
        bucket = os.environ.get("ECOSYSTEMS_BUCKET", "")
        if not bucket:
            return _cors(500, {"error": "ECOSYSTEMS_BUCKET not configured"})
        s3 = boto3.client("s3", region_name=REGION)
        try:
            import yaml as _yaml
            s3.put_object(Bucket=bucket, Key=f"domains/{domain}/catalog.yaml",
                          Body=_yaml.safe_dump({"ingest": ing}, sort_keys=False).encode(),
                          ContentType="application/x-yaml")
            schema = body.get("schema")
            wrote = False
            if isinstance(schema, dict) and schema:
                s3.put_object(Bucket=bucket, Key=f"domains/schemas/{domain}.records.json",
                              Body=json.dumps(schema, indent=2).encode(), ContentType="application/json")
                wrote = True
            return _cors(200, {"domain": domain, "sources": len(ing.get("sources") or []),
                               "catalog_yaml": f"domains/{domain}/catalog.yaml", "schema_written": wrote})
        except Exception as e:
            return _cors(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})
    # Wizard "Start ingestion" (first population): async ingest_all via the GENERIC
    # (domain-from-event) ingest Lambda — declarative sources land in the active namespace.
    if action == "start-ingest":
        import boto3
        domain = body.get("domain", "") or CATALOG_DOMAIN
        gfn = os.environ.get("CATALOG_GENERIC_INGEST_FUNCTION", "")
        if not gfn:
            return _cors(500, {"error": "CATALOG_GENERIC_INGEST_FUNCTION not configured"})
        try:
            boto3.client("lambda", region_name=REGION).invoke(
                FunctionName=gfn, InvocationType="Event",
                Payload=json.dumps({"action": "ingest_all", "domain": domain}).encode())
            return _cors(202, {"domain": domain, "started": True,
                               "note": "Declarative ingest started (fetch + agent sources). Reload the catalog shortly."})
        except Exception as e:
            return _cors(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})
    # Blue/green: start a versioned BUILD (mint -> ingest + synthesis into a fresh green ->
    # validate). Non-destructive; operator promotes after reviewing coverage/diff.
    if action == "build":
        import boto3, uuid
        domain = body.get("domain", "") or CATALOG_DOMAIN
        sf = boto3.client("stepfunctions", region_name=REGION)
        # Prefer the PER-DOMAIN build SM when one exists: it carries the synthesis stage
        # (PlanVersion -> MapClusters) that produces SYNTHESIZED types like `risk`. The
        # GENERIC build SM is domain-parameterized but INGEST-ONLY (no synthesis), so using
        # it for a synthesis-gated domain silently yields coverage-incomplete green versions
        # (coverage_ok=false, no CLU# clusters). Fall back to the generic SM only when no
        # per-domain SM exists for this domain.
        generic = os.environ.get("CATALOG_GENERIC_BUILD_SM_ARN", "")
        prefix = os.environ.get("CATALOG_BUILD_SM_PREFIX", "")
        candidates = []
        if prefix:
            candidates.append((f"{prefix}{domain}", "{}"))                 # per-domain (synthesis) — preferred
        if generic:
            candidates.append((generic, json.dumps({"domain": domain})))   # generic (ingest-only) — fallback
        if not candidates:
            return _cors(500, {"error": "no build state machine configured"})
        last_err = None
        for sm_arn, sm_input in candidates:
            try:
                ex = sf.start_execution(stateMachineArn=sm_arn,
                                        name=f"build-{uuid.uuid4().hex[:12]}", input=sm_input)
                return _cors(202, {"domain": domain, "execution": ex["executionArn"], "state_machine": sm_arn,
                                   "note": "Building a fresh green version (ingest + synthesis). Poll /catalog/versions."})
            except sf.exceptions.StateMachineDoesNotExist as e:
                last_err = e
                continue  # no per-domain SM for this domain -> try the generic fallback
            except Exception as e:
                return _cors(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})
        return _cors(500, {"error": f"no build state machine found for {domain!r}"})
    return _cors(404, {"error": f"unknown action {action!r}"})


def _clean(obj):
    """Convert DynamoDB Decimals -> int/float so json.dumps works."""
    from decimal import Decimal
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def _handle_refresh(event, body):
    """Start the catalog-refresh orchestrator (one click -> full producer refresh).
    Acquires a per-domain lock (one refresh at a time) and fans sources by mode."""
    import boto3, uuid
    from tessera_platform.catalog import store as refresh_store
    sm = os.environ.get("CATALOG_REFRESH_STATE_MACHINE_ARN", "")
    if not sm:
        return _cors(500, {"error": "CATALOG_REFRESH_STATE_MACHINE_ARN not configured"})
    domain = body.get("domain", "") or CATALOG_DOMAIN
    run_id = uuid.uuid4().hex[:12]
    if not refresh_store.acquire_lock(domain, run_id):
        cur = refresh_store.get_run(domain) or {}
        return _cors(409, {"error": "a refresh is already running", "run": _clean(cur)})
    srcs = _active_source_records(domain)
    fetch_sources = [s["id"] for s in srcs if (s.get("mode") or "fetch") != "agent"]
    agent_sources = [{"id": s["id"], "mission": _agent_mission(s),
                      "session_id": f"catalog-agent-ingest-{uuid.uuid4().hex[:8]}",
                      "produces": s.get("produces") or []}
                     for s in srcs if (s.get("mode") or "fetch") == "agent"]
    # seed per-source pending status for immediate UI feedback
    for s in srcs:
        refresh_store.put_source(domain, s["id"], {"status": "pending", "mode": s.get("mode") or "fetch"})
    boto3.client("stepfunctions", region_name=REGION).start_execution(
        stateMachineArn=sm, name=f"refresh-{domain}-{run_id}",
        input=json.dumps({"domain": domain, "run_id": run_id,
                          "fetch_sources": fetch_sources, "agent_sources": agent_sources}))
    return _cors(202, {"run_id": run_id, "fetch": fetch_sources,
                       "agent": [a["id"] for a in agent_sources], "state": "running"})


def _refresh_status(domain):
    from tessera_platform.catalog import store as refresh_store
    items = refresh_store.query_domain(domain)
    run, sources, clusters, versions = None, {}, {}, {}
    for it in items:
        sk = str(it.get("sk", ""))
        if sk == "RUN":
            run = it
        elif sk.startswith("SRC#"):
            sources[it.get("source") or sk[4:]] = it
        elif sk.startswith("CLU#"):
            clusters[it.get("cluster") or sk[4:]] = it
        elif sk.startswith("VER#"):
            versions[sk[4:]] = it
    clu_done = sum(1 for c in clusters.values() if c.get("status") == "done")
    src_done = sum(1 for s in sources.values() if s.get("status") in ("done", "aborted", "failed"))
    return _cors(200, _clean({
        "domain": domain,
        "state": (run or {}).get("state", "idle"),
        "run": run,
        "sources": sources,
        "clusters": clusters,
        # Blue/green version lifecycle (minted -> ingested -> validated; coverage_ok; ready)
        # + the active pointer readers resolve. Surfaces the versioned build/promote model.
        "active_version": refresh_store.get_active_version(domain),
        "versions": versions,
        "progress": {"sources_done": src_done, "sources_total": len(sources),
                     "clusters_done": clu_done, "clusters_total": len(clusters)},
    }))


def handler(event, context):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method", "GET")
    path = event.get("rawPath") or http.get("path", "/")
    if method == "OPTIONS":
        return _cors(204, {})

    # Auth is enforced by the API Gateway HTTP API's Cognito JWT authorizer BEFORE the
    # request reaches this Lambda. The Lambda is private to the gateway (no Function URL),
    # so any request arriving here already carries a validated token — re-verifying (and
    # re-fetching JWKS) per request was redundant and fragile. Trust the gateway.
    parts = parse_route(path)

    # Privileged writes (trigger ingestion) — admin-group only, from the gateway-verified
    # claims. Read paths below are open to any authenticated user.
    if method == "POST":
        return _handle_post(event, parts)

    qs = event.get("queryStringParameters") or {}
    domain = qs.get("domain") or CATALOG_DOMAIN
    ac, mid = _mem()

    # Refresh progress (per-source + per-cluster + overall) — any authed user.
    if parts and parts[0] == "refresh":
        return _refresh_status(domain)

    # Active catalog version (blue/green) — lets the UI show/pin the served version.
    if parts and parts[0] == "version":
        return _cors(200, {"domain": domain, "active": _active_version(domain)})

    # Blue/green version registry: list all versions + their build/validate status
    # (status, identical, coverage_ok, ready, counts) + the active pointer.
    if parts and parts[0] == "versions":
        from tessera_platform.catalog import store as refresh_store
        vers = {}
        for it in refresh_store.query_domain(domain):
            sk = str(it.get("sk", ""))
            if sk.startswith("VER#"):
                vers[sk[4:]] = it
        return _cors(200, _clean({"domain": domain, "active": refresh_store.get_active_version(domain),
                                  "versions": vers}))

    if not parts or parts[0] == "types":
        return _cors(200, {"types": _list_types(ac, mid, domain)})

    etype = parts[0]
    if etype not in _known_types(domain):
        return _cors(404, {"error": f"unknown type {etype!r}"})

    base = _base(domain)
    if len(parts) == 1:
        prefix = f"{base}/{etype}"
        q, tactic, severity = qs.get("q"), qs.get("tactic"), qs.get("severity")
        framework, facet = qs.get("framework"), qs.get("facet")
        try:
            limit = max(1, min(int(qs.get("limit", 60)), 100))
        except (TypeError, ValueError):
            limit = 60
        if facet:
            # Distinct values (+counts) of a field across the full type — powers UI facets.
            from collections import Counter
            recs = [record_from_summary(s) for s in _list_ns(ac, mid, prefix, cap=10000)]
            c = Counter(str(r.get(facet)) for r in recs if r.get(facet) not in (None, ""))
            return _cors(200, {"type": etype, "facet": facet,
                               "values": [{"value": v, "count": n} for v, n in c.most_common()]})
        if q or tactic or severity or framework:
            # Filtered search scans the full type, then filters (bounded) — no paging token.
            recs = [record_from_summary(s) for s in _list_ns(ac, mid, prefix, cap=10000)]
            recs = filter_records(recs, q=q, tactic=tactic, severity=severity, framework=framework)
            return _cors(200, {"type": etype, "count": len(recs), "records": recs[:1000], "next_token": None})
        # Unfiltered browse -> one server-side page + a cursor for "load more".
        summaries, ntok = _list_page(ac, mid, prefix, limit, qs.get("next_token"))
        recs = [record_from_summary(s) for s in summaries]
        return _cors(200, {"type": etype, "records": recs, "next_token": ntok})

    key = parts[1]
    recs = [record_from_summary(s, resolve=True) for s in _list_ns(ac, mid, f"{base}/{etype}/{key}")]
    return _cors(200, recs[0] if recs else {"error": "not found"})
