"""tessera_platform.catalog.orchestrate — generic, descriptor-driven ingest verbs.

The single place ingestion is orchestrated for ANY domain. The Lambda handler is a thin
shim: load the domain's descriptor, dispatch the action here. Core functions take
injectable deps (store, refresh, descriptor) so they're unit-testable with fakes.

Actions: ingest_all | mark_source | mark_run | plan_synthesis | get_mission
         | checkpoint_cluster | coverage
"""
import os

from . import descriptor as descriptor_mod
from . import store as refresh_store_mod
from .engine import CatalogStore, diff_snapshots, today_iso
from .synthesis import load_synthesizer


def _report_summary(report):
    return {
        "added": sum(v.get("added", 0) for v in report.values()),
        "updated": sum(v.get("updated", 0) for v in report.values()),
        "skipped": sum(v.get("skipped", 0) for v in report.values()),
        "deprecated": sum(v.get("deprecated", 0) for v in report.values()),
        "aborted": any(v.get("aborted") for v in report.values()),
        "changed": bool([t for t, v in report.items()
                         if (v.get("added") or v.get("updated") or v.get("deprecated"))]),
    }


def ingest_all(descriptor, store, today=None, sources=None, refresh=refresh_store_mod):
    """Fetch + normalize + diff-upsert every (non-agent-mode) source in-process.
    Returns {domain, sources: {name: summary}, any_changed}."""
    today = today or today_iso()
    out = {}
    names = sources or descriptor.source_names()
    for a in descriptor.adapters():
        if a.name not in names:
            continue
        try:
            items = a.build(a.fetch(), today)
            report = store.upsert(items)
            refresh.put_source_manifest(descriptor.domain, a.name, report, len(items))
            out[a.name] = dict(_report_summary(report), status="aborted" if _report_summary(report)["aborted"] else "done")
        except Exception as e:
            refresh.put_source(descriptor.domain, a.name, {"status": "failed", "error": str(e)[:200]})
            out[a.name] = {"status": "failed", "error": str(e)[:160]}
    return {"domain": descriptor.domain, "sources": out,
            "any_changed": any(v.get("changed") for v in out.values())}


def ingest_source(descriptor, store, source, today=None, refresh=refresh_store_mod):
    """Ingest a single named source (the per-source entry point / EventBridge event)."""
    today = today or today_iso()
    a = descriptor.adapter(source)
    items = a.build(a.fetch(), today)
    report = store.upsert(items)
    refresh.put_source_manifest(descriptor.domain, source, report, len(items))
    return dict(_report_summary(report), source=source, normalized=len(items), report=report)


def mark_source(descriptor, store, source, produces=None, refresh=refresh_store_mod):
    """Count a source's live records (for agent sources with no diff report) -> manifest."""
    produces = produces or []
    count = 0
    for t in produces:
        for rec in store.load_type(t):
            if source in ((rec.get("provenance") or {}).get("sources") or []):
                count += 1
    refresh.put_source(descriptor.domain, source, {"status": "done", "mode": "agent", "count": count, "changed": True})
    return {"source": source, "count": count}


def mark_run(domain, state="done", refresh=refresh_store_mod):
    refresh.release_lock(domain, state)
    return {"domain": domain, "state": state}


def plan_synthesis(descriptor, store, event, refresh=refresh_store_mod):
    """Delegate to the domain's synthesis plugin (or no-op if none)."""
    synth = load_synthesizer(descriptor.synthesis)
    return synth.plan(store, descriptor.domain, event or {})


def get_mission(domain, cluster_id, refresh=refresh_store_mod):
    """Fetch a cluster's stashed synthesis mission (set by plan_synthesis). Fetched
    per-cluster inside the SFN Map so orchestrator state never carries all missions at
    once. Returns {cluster_id, mission (JSON string), input_digest}."""
    it = refresh.get_cluster(domain, cluster_id) or {}
    return {"cluster_id": cluster_id, "mission": it.get("mission", ""),
            "input_digest": it.get("input_digest", "")}


def checkpoint_cluster(domain, cluster_id, input_digest="", output_digest=None, refresh=refresh_store_mod):
    """Record a cluster's synthesis checkpoint (input_digest + output present) so an
    unchanged cluster is skipped and a resumed run doesn't redo it."""
    import hashlib
    outd = output_digest or hashlib.sha256((str(input_digest) + "|synthesized").encode()).hexdigest()[:16]
    refresh.put_cluster(domain, cluster_id, {"input_digest": input_digest, "output_digest": outd, "status": "done"})
    return {"cluster_id": cluster_id, "checkpointed": True}


def coverage(descriptor, store, categories=None):
    counts = {}
    for t in {s.get("normalize", {}).get("entity_type") for s in descriptor.sources} - {None}:
        for rec in store.load_type(t):
            c = rec.get("category", t)
            counts[c] = counts.get(c, 0) + 1
    missing = [c for c in (categories or []) if not counts.get(c)]
    return {"total": sum(counts.values()), "by_category": counts, "missing": missing}


# ── blue/green versioned release (spec-declarative-ingest.md §5a) ─────────────────
def mint_version():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("v%Y%m%d-%H%M%S")


def build_version(descriptor, version, today=None, memory_id=None, refresh=refresh_store_mod,
                  store=None):
    """Build the INPUT layer of a fresh GREEN version into its own namespace
    (/tessera/catalog/<version>/...) via the declarative engine. Non-destructive to blue.

    This is only the deterministic ingest phase. Synthesized types (e.g. `risk`) are NOT
    produced here — they are generated by the Synthesizer running INTO this version as a
    separate orchestrated step (long-running per-cluster fan-out), then verified by the
    coverage gate in validate_version. So green is built end-to-end from ingestion +
    synthesis — never copied from blue."""
    today = today or today_iso()
    green = store or CatalogStore(memory_id=memory_id, version=version)
    refresh.put_version(descriptor.domain, version, {"status": "building"})
    result = ingest_all(descriptor, green, today=today, refresh=refresh)
    counts = {t: len(green.load_type(t)) for t in descriptor.produced_types()}
    refresh.put_version(descriptor.domain, version, {"status": "ingested", "counts": counts})
    return dict(result, version=version, counts=counts)


def validate_version(descriptor, version, compare_to=None, memory_id=None,
                     refresh=refresh_store_mod, green_store=None, blue_store=None):
    """The promotion gate. Two checks:
      • identical — GREEN vs BLUE source-produced (deterministic) types match by content
        hash: the declarative engine reproduces the ingested inputs exactly.
      • coverage — each SYNTHESIZED type (descriptor.synthesis_produces) meets the domain's
        DECLARED thresholds (synthesis.coverage: min_total + required_categories). Synthesis
        is non-deterministic, so it is gated on coverage/quality, not byte-identity.
    `ready = identical and coverage_ok` is the safe-to-promote signal."""
    types = descriptor.produced_types()
    blue_version = compare_to if compare_to is not None else refresh.get_active_version(descriptor.domain)
    green = green_store or CatalogStore(memory_id=memory_id, version=version)
    blue = blue_store or CatalogStore(memory_id=memory_id, version=blue_version)
    diff = diff_snapshots(blue.snapshot(types), green.snapshot(types))
    cov_spec = descriptor.coverage or {}
    min_total = int(cov_spec.get("min_total") or 0)
    required = set(cov_spec.get("required_categories") or [])
    coverage, coverage_ok = {}, True
    for t in descriptor.synthesis_produces:
        recs = green.load_type(t)
        cats = {r.get("category") for r in recs if r.get("category")}
        missing = sorted(required - cats)
        ok = len(recs) >= min_total and not missing
        coverage[t] = {"total": len(recs), "min_total": min_total, "missing_categories": missing, "ok": ok}
        coverage_ok = coverage_ok and ok
    ready = bool(diff["identical"] and coverage_ok)
    refresh.put_version(descriptor.domain, version,
                        {"status": "validated", "compare_to": blue_version,
                         "identical": diff["identical"], "coverage_ok": coverage_ok, "ready": ready})
    return dict(diff, version=version, compare_to=blue_version,
                coverage=coverage, coverage_ok=coverage_ok, ready=ready)


def promote_version(domain, version, refresh=refresh_store_mod):
    """Atomically point readers at `version` (single write). Instant rollback = re-promote
    the prior version."""
    prior = refresh.get_active_version(domain)
    refresh.set_active_version(domain, version)
    refresh.put_version(domain, version, {"status": "active"})
    if prior and prior != version:
        refresh.put_version(domain, prior, {"status": "superseded"})
    return {"domain": domain, "active": version, "previous": prior}


def retire_version(descriptor, version, memory_id=None, refresh=refresh_store_mod, store=None):
    """Hard-delete a retired version's records (post-soak cleanup). Refuses to retire the
    currently-active version."""
    if version == refresh.get_active_version(descriptor.domain):
        raise ValueError(f"refusing to retire the ACTIVE version {version!r}")
    old = store or CatalogStore(memory_id=memory_id, version=version)
    res = old.retire(descriptor.produced_types() + descriptor.synthesis_produces)
    refresh.put_version(descriptor.domain, version, {"status": "retired"})
    return res


def resolve_active(domain, refresh=refresh_store_mod):
    """Reader helper: the version a run should pin at launch (None => legacy namespace)."""
    return refresh.get_active_version(domain)


def migrate_version(descriptor, version=None, promote_if_identical=False, today=None,
                    memory_id=None, refresh=refresh_store_mod, green_store=None, blue_store=None):
    """Route 1 — build a fresh GREEN with the declarative engine, diff it against the
    CURRENT live catalog (the active version, or the legacy unversioned namespace when
    none is active), and optionally promote iff identical. Non-destructive; promotion is
    gated. Returns {version, diff, promoted}."""
    today = today or today_iso()
    version = version or mint_version()
    build_version(descriptor, version, today=today, memory_id=memory_id, refresh=refresh, store=green_store)
    diff = validate_version(descriptor, version, compare_to=None, memory_id=memory_id,
                            refresh=refresh, green_store=green_store, blue_store=blue_store)
    promoted = False
    if promote_if_identical and diff.get("ready"):
        promote_version(descriptor.domain, version, refresh=refresh)
        promoted = True
    return {"version": version, "diff": diff, "promoted": promoted,
            "hint": ("promoted (inputs identical + carried types covered)" if promoted else
                     "NOT ready — review diff.by_type / diff.carry_forward before promoting" if not diff.get("ready")
                     else "ready — re-run with promote_if_identical or call promote")}


def bootstrap_version(descriptor, version, memory_id=None, refresh=refresh_store_mod,
                      src_store=None, dst_store=None, set_active=True):
    """Route 2 — stamp the CURRENT live records into a `version` namespace without a
    rebuild (copy legacy/active -> versioned), then set it active. For adopting versioning
    on an existing unversioned catalog cheaply."""
    src = src_store or CatalogStore(memory_id=memory_id, version=resolve_active(descriptor.domain))
    dst = dst_store or CatalogStore(memory_id=memory_id, version=version)
    refresh.put_version(descriptor.domain, version, {"status": "building", "origin": "bootstrap"})
    copied = {}
    for t in descriptor.produced_types():
        kf = descriptor.key_field(t)
        if not kf:
            raise ValueError(f"no key field for type {t!r} (schema x-tessera-keys missing); cannot bootstrap")
        recs = src.load_type(t)
        items = [(t, kf, rec) for rec in recs if rec.get(kf)]
        if items:
            dst.upsert(items)
        copied[t] = len(items)
    refresh.put_version(descriptor.domain, version, {"status": "built", "counts": copied, "origin": "bootstrap"})
    if set_active:
        promote_version(descriptor.domain, version, refresh=refresh)
    return {"version": version, "copied": copied, "active": set_active}


# ── Lambda handler (thin shim) ─────────────────────────────────────────────────────
def _store(domain=None, version=None):
    """Routine ingest/synthesis writes into the ACTIVE version (blue/green); a versioned
    build passes an explicit `version` (green) so ingest + synthesis target that namespace.
    Legacy namespace if neither is set for the domain."""
    if version is None and domain:
        version = resolve_active(domain)
    return CatalogStore(version=version)


def _descriptor(event):
    domain = (event or {}).get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    return descriptor_mod.load(domain)


def handler(event, context=None):
    event = event or {}
    action = event.get("action")
    desc = _descriptor(event)
    # blue/green release actions
    if action == "build_version":
        return build_version(desc, event.get("version") or mint_version())
    if action == "new_version":
        v = event.get("version") or mint_version()
        refresh_store_mod.put_version(desc.domain, v, {"status": "minted"})
        return {"domain": desc.domain, "version": v}
    if action == "migrate":
        return migrate_version(desc, version=event.get("version"),
                               promote_if_identical=bool(event.get("promote_if_identical")))
    if action == "bootstrap":
        return bootstrap_version(desc, event.get("version") or mint_version(),
                                 set_active=event.get("set_active", True))
    if action == "validate_version":
        return validate_version(desc, event["version"], compare_to=event.get("compare_to"))
    if action == "promote":
        return promote_version(desc.domain, event["version"])
    if action == "retire":
        return retire_version(desc, event["version"])
    if action == "active":
        return {"domain": desc.domain, "active": resolve_active(desc.domain)}
    store = _store(desc.domain, event.get("version"))
    if action == "ingest_all":
        return ingest_all(desc, store, sources=event.get("fetch_sources"))
    if action == "mark_source":
        return mark_source(desc, store, event.get("source"), event.get("produces"))
    if action == "mark_run":
        return mark_run(desc.domain, event.get("state", "done"))
    if action == "plan_synthesis":
        return plan_synthesis(desc, store, event)
    if action == "get_mission":
        return get_mission(desc.domain, event.get("cluster_id"))
    if action == "checkpoint_cluster":
        return checkpoint_cluster(desc.domain, event.get("cluster_id"),
                                  input_digest=event.get("input_digest", ""),
                                  output_digest=event.get("output_digest"))
    if action == "coverage":
        return coverage(desc, store, event.get("categories"))
    # default: ingest a single source
    source = event.get("source") or (desc.source_names()[0] if desc.source_names() else None)
    if not source:
        return {"error": "no source to ingest", "domain": desc.domain}
    return ingest_source(desc, store, source)
