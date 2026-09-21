"""catalog-ingest — platform ingestion Lambda (INFRASTRUCTURE, not an ecosystem).

Deterministic ETL that populates the cyber-security-risk domain catalog (LTM) from
authoritative sources. EventBridge-scheduled. Per-source NORMALIZERS map raw feeds
to the entity-keyed record schema (domains/schemas/cyber-security-risk.records.json)
and write via the self-managed `tessera_catalog` strategy. No LLM — this is not
reasoning. See docs/spec-risk-reasoning.md §3a.

Sources:
  mitre-attack : ATT&CK STIX -> attack-technique + security-control (mitigations),
                 with technique<->control cross-links from `mitigates` relationships.
  cisa-kev     : CISA Known Exploited Vulnerabilities -> vulnerability.

Event: {"source": "mitre-attack", "limit": 0}   (limit 0 = all; caps the primary type)

The build_* functions are PURE (no network, no boto3) so they are unit-testable.
"""
import os, re, json, uuid, urllib.request, urllib.parse
from datetime import datetime, timezone

CATALOG_STRATEGY_NAME = "tessera_catalog"
ATTACK_STIX = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack.json"
CISA_KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


# ── helpers ────────────────────────────────────────────────────────────────────
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")[:80]


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _mitre_ext(obj):
    """(external_id, url) for the mitre-attack external reference, else (None, None)."""
    for ref in obj.get("external_references", []) or []:
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


def _live(obj):
    return not obj.get("revoked") and not obj.get("x_mitre_deprecated")


# ── pure builders (unit-tested) ─────────────────────────────────────────────────
def build_attack_records(objects, today=None, tech_limit=0):
    """ATT&CK STIX objects -> [(entity_type, key_field, record)] for attack-technique
    (with `mitigations` cross-links) and security-control (with `mitigates`)."""
    today = today or _today()
    id2ext = {}
    for o in objects:
        if o.get("type") in ("attack-pattern", "course-of-action") and _live(o):
            eid, _ = _mitre_ext(o)
            if eid:
                id2ext[o["id"]] = eid
    tech_mit, ctrl_tech = {}, {}
    for o in objects:
        if o.get("type") == "relationship" and o.get("relationship_type") == "mitigates":
            se, te = id2ext.get(o.get("source_ref")), id2ext.get(o.get("target_ref"))
            if se and te:
                ctrl_tech.setdefault(se, set()).add(te)
                tech_mit.setdefault(te, set()).add(se)

    techniques, controls = [], []
    for o in objects:
        if o.get("type") != "attack-pattern" or not _live(o):
            continue
        tid, url = _mitre_ext(o)
        if not tid:
            continue
        techniques.append(("attack-technique", "technique_id", {
            "technique_id": tid, "name": o.get("name", ""), "framework": "mitre-attack",
            "tactics": [p["phase_name"] for p in o.get("kill_chain_phases", []) or []
                        if p.get("kill_chain_name") == "mitre-attack"],
            "description": (o.get("description", "") or "")[:1200],
            "platforms": o.get("x_mitre_platforms", []) or [],
            "data_sources": o.get("x_mitre_data_sources", []) or [],
            "detections": [o.get("x_mitre_detection", "")[:400]] if o.get("x_mitre_detection") else [],
            "mitigations": sorted(tech_mit.get(tid, set())),
            "references": [url] if url else [],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["mitre-attack/enterprise"],
                           "refresh_date": today},
        }))
    if tech_limit:
        techniques = techniques[:tech_limit]

    for o in objects:
        if o.get("type") != "course-of-action" or not _live(o):
            continue
        cid, url = _mitre_ext(o)
        if not cid or not cid.startswith("M"):
            continue
        controls.append(("security-control", "control_id", {
            "control_id": cid, "name": o.get("name", ""), "framework": "mitre-attack-mitigation",
            "description": (o.get("description", "") or "")[:1200],
            "mitigates": sorted(ctrl_tech.get(cid, set())),
            "references": [url] if url else [],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["mitre-attack/mitigations"],
                           "refresh_date": today},
        }))
    return techniques + controls


def build_kev_records(kev, today=None, limit=0):
    """CISA KEV json -> [(entity_type, key_field, vulnerability record)]."""
    today = today or _today()
    out = []
    for v in (kev.get("vulnerabilities") or []):
        cve = v.get("cveID")
        if not cve:
            continue
        out.append(("vulnerability", "cve_or_ref", {
            "cve_or_ref": cve, "name": v.get("vulnerabilityName", ""),
            "vendor": v.get("vendorProject", ""), "product": v.get("product", ""),
            "description": (v.get("shortDescription", "") or "")[:1200],
            "known_exploited": True,
            "known_ransomware": (v.get("knownRansomwareCampaignUse", "") or "").lower() == "known",
            "date_added": v.get("dateAdded", ""), "required_action": v.get("requiredAction", ""),
            "references": [f"https://nvd.nist.gov/vuln/detail/{cve}"],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["cisa-kev"],
                           "refresh_date": today},
        }))
        if limit and len(out) >= limit:
            break
    return out


# ── source normalizers (fetch + build) ──────────────────────────────────────────
def _safe_urlopen(url, timeout=180):
    """urlopen restricted to http/https — refuses file://, ftp://, data://, etc.
    Catalog source URLs come from governed declarative descriptors / module constants,
    but we enforce the scheme as defense-in-depth against SSRF / local-file reads (B310)."""
    scheme = (urllib.parse.urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"refusing to fetch non-http(s) URL (scheme={scheme!r})")
    return urllib.request.urlopen(url, timeout=timeout)  # nosec B310 — scheme validated above


def _fetch_json(url, timeout=180):
    with _safe_urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def normalize_attack(limit):
    return build_attack_records(_fetch_json(ATTACK_STIX).get("objects", []), tech_limit=limit)


def normalize_cisa_kev(limit):
    return build_kev_records(_fetch_json(CISA_KEV), limit=limit)


NORMALIZERS = {"mitre-attack": normalize_attack, "cisa-kev": normalize_cisa_kev}


# ── VERIS / VCDB → breach (single combined JSON of ~9k incidents) ─────────────────
VERIS_VCDB = "https://raw.githubusercontent.com/walkerandco/VCDB-JSON-Merged/master/vcdb-complete.json"


def _veris_keys(d):
    return sorted([k for k in (d or {}).keys()]) if isinstance(d, dict) else []


def build_veris(incidents, today=None, limit=1500):
    today = today or _today()
    def yr(i):
        try:
            return int(((i.get("timeline") or {}).get("incident") or {}).get("year") or 0)
        except Exception:
            return 0
    out = []
    for inc in sorted(incidents, key=yr, reverse=True):
        vic = inc.get("victim") or {}
        org = vic.get("victim_id") or "Unknown"
        year = yr(inc)
        mid = (inc.get("plus") or {}).get("master_id") or inc.get("incident_id") or ""
        country = vic.get("country")
        assets = [a.get("variety") for a in ((inc.get("asset") or {}).get("assets") or []) if isinstance(a, dict) and a.get("variety")]
        out.append(("breach", "breach_key", {
            "breach_key": f"{_slug(org)}-{year}-{_slug(str(mid))[:8]}".strip("-"),
            "org": org, "year": year, "industry": vic.get("industry", ""),
            "country": (country[0] if isinstance(country, list) and country else country) or "",
            "actors": _veris_keys(inc.get("actor")), "actions": _veris_keys(inc.get("action")),
            "assets": assets[:6], "attributes": _veris_keys(inc.get("attribute")),
            "summary": (inc.get("summary") if isinstance(inc.get("summary"), str) else "")[:800],
            "references": [inc.get("reference")] if inc.get("reference") else [],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["veris-vcdb"], "refresh_date": today},
        }))
        if limit and len(out) >= limit:
            break
    return out


def normalize_veris(limit):
    data = _fetch_json(VERIS_VCDB, timeout=240)
    inc = data if isinstance(data, list) else (data.get("incidents") or [])
    return build_veris(inc, limit=limit or 1500)


# ── OWASP GenAI (LLM Top-10 2025 + Agentic ASI Top-10) → agentic-threat ───────────
_OWASP_LLM = [
    ("LLM01", "Prompt Injection", "Crafted inputs manipulate the model to bypass instructions, guardrails, or safety controls."),
    ("LLM02", "Sensitive Information Disclosure", "The model leaks PII, secrets, or proprietary data via its outputs."),
    ("LLM03", "Supply Chain", "Compromised models, datasets, plugins, or dependencies introduce risk."),
    ("LLM04", "Data and Model Poisoning", "Training/fine-tuning/RAG data is tampered to bias or backdoor the model."),
    ("LLM05", "Improper Output Handling", "Unvalidated model output flows into downstream systems (XSS, SQLi, RCE)."),
    ("LLM06", "Excessive Agency", "Over-permissioned tools/autonomy let the model take harmful actions."),
    ("LLM07", "System Prompt Leakage", "System prompts containing sensitive config/instructions are exposed."),
    ("LLM08", "Vector and Embedding Weaknesses", "RAG vector stores are poisoned, leaked, or manipulated."),
    ("LLM09", "Misinformation", "Confident but false outputs (hallucination) mislead users/systems."),
    ("LLM10", "Unbounded Consumption", "Uncontrolled inference cost/DoS via excessive or looping requests."),
]
_OWASP_ASI = [
    ("ASI01", "Memory Poisoning", "Malicious data corrupts agent memory to alter future decisions."),
    ("ASI02", "Tool Misuse", "The agent is tricked into abusing its tools/integrations."),
    ("ASI03", "Privilege Compromise", "The agent escalates or abuses granted privileges."),
    ("ASI04", "Resource Overload", "Compute/memory/service exhaustion via the agent's actions."),
    ("ASI05", "Cascading Hallucination", "False outputs propagate and compound across steps/agents."),
    ("ASI06", "Intent Breaking & Goal Manipulation", "The agent's objectives are subverted mid-task."),
    ("ASI07", "Misaligned & Deceptive Behaviors", "The agent pursues hidden or harmful objectives."),
    ("ASI08", "Repudiation & Untraceability", "Agent actions can't be attributed or audited."),
    ("ASI09", "Identity Spoofing & Impersonation", "The agent is impersonated or spoofs identities."),
    ("ASI10", "Overwhelming Human-in-the-Loop", "Approval fatigue defeats human oversight controls."),
]


def build_owasp(today=None):
    today = today or _today()
    out = []
    for tid, name, desc in _OWASP_LLM + _OWASP_ASI:
        agentic = tid.startswith("ASI")
        out.append(("agentic-threat", "threat_id", {
            "threat_id": tid, "name": name, "category": "agentic" if agentic else "llm-application",
            "framework": "owasp-agentic" if agentic else "owasp-llm-top10-2025",
            "description": desc,
            "references": ["https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/" if agentic else "https://genai.owasp.org/llm-top-10/"],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["owasp"], "refresh_date": today},
        }))
    return out


def normalize_owasp(limit):
    r = build_owasp()
    return r[:limit] if limit else r


# ── NIST AI RMF trustworthiness taxonomy → risk-method-profile ────────────────────
_NIST_AI = [
    ("valid-reliable", "Valid & Reliable", "Accuracy, robustness, and reliability of the AI system's outputs."),
    ("safe", "Safe", "The system does not, under defined conditions, lead to a state that endangers life/health/property/environment."),
    ("secure-resilient", "Secure & Resilient", "Resistance to adversarial attack and ability to recover; confidentiality/integrity/availability."),
    ("accountable-transparent", "Accountable & Transparent", "Traceability, documentation, and governance over the AI lifecycle."),
    ("explainable-interpretable", "Explainable & Interpretable", "Mechanisms behind outputs are understandable to relevant stakeholders."),
    ("privacy-enhanced", "Privacy-Enhanced", "Safeguards for anonymity, confidentiality, and data control."),
    ("fair-bias-managed", "Fair - Harmful Bias Managed", "Systemic, computational, and human-cognitive bias are identified and managed."),
]


def build_nist(today=None):
    today = today or _today()
    return [("risk-method-profile", "risk_type", {
        "risk_type": "ai-" + rid, "name": name, "framework": "nist-ai-rmf", "dimension": name,
        "description": desc, "functions": ["GOVERN", "MAP", "MEASURE", "MANAGE"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-ai-rmf"], "refresh_date": today},
    }) for rid, name, desc in _NIST_AI]


def normalize_nist(limit):
    r = build_nist()
    return r[:limit] if limit else r


NORMALIZERS.update({"owasp": normalize_owasp, "veris-vcdb": normalize_veris, "nist-ai-rmf": normalize_nist})


# ── Wiz Cloud VDB → vulnerability (cloud/CSP issues; per-file YAML) ────────────────
WIZ_CONTENTS = "https://api.github.com/repos/wiz-sec/open-cvdb/contents/vulnerabilities"


def build_wiz(entries, today=None):
    """entries: list of parsed CVDB yaml dicts -> vulnerability records."""
    today = today or _today()
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        cves = e.get("cves") or []
        ref = (cves[0] if cves else None) or e.get("slug") or _slug(e.get("title", ""))
        if not ref:
            continue
        out.append(("vulnerability", "cve_or_ref", {
            "cve_or_ref": ref, "name": e.get("title", ""), "vendor": "cloud",
            "product": ", ".join(e.get("affectedServices") or [])[:120],
            "platforms": e.get("affectedPlatforms") or [],
            "description": (e.get("summary") or "")[:1200] if isinstance(e.get("summary"), str) else "",
            "severity": e.get("severity"),
            "cloud_vuln": True,
            "known_exploited": bool(e.get("knownITWExploitation")),
            "date_added": str(e.get("disclosedAt") or e.get("publishedAt") or ""),
            "references": [r for r in (e.get("references") or []) if r][:5],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["wiz-cvdb"], "refresh_date": today},
        }))
    return out


def normalize_wiz(limit):
    listing = _fetch_json(WIZ_CONTENTS, timeout=60)
    files = [f for f in listing if isinstance(f, dict) and f.get("name", "").endswith((".yaml", ".yml")) and f.get("download_url")]
    if limit:
        files = files[:limit]
    import yaml as _yaml
    entries = []
    for f in files:
        try:
            with _safe_urlopen(f["download_url"], timeout=30) as r:
                entries.append(_yaml.safe_load(r.read().decode()))
        except Exception:
            continue
    return build_wiz(entries)


NORMALIZERS.update({"wiz-cvdb": normalize_wiz})


# ── NIST SP 800-53 Rev5 (OSCAL JSON) → security-control (~1000 controls) ───────────
NIST_80053_OSCAL = "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"


def _oscal_statement(control):
    out = []
    def walk(parts):
        for p in parts or []:
            if p.get("prose"):
                out.append(p["prose"])
            walk(p.get("parts"))
    for part in control.get("parts", []) or []:
        if part.get("name") == "statement":
            if part.get("prose"):
                out.append(part["prose"])
            walk(part.get("parts"))
    return " ".join(out)[:1200]


def build_nist80053(data, today=None):
    today = today or _today()
    cat = data.get("catalog") or data
    out = []
    def emit(control, family):
        cid = (control.get("id") or "").upper()
        if not cid:
            return
        out.append(("security-control", "control_id", {
            "control_id": cid, "name": control.get("title", ""), "framework": "nist-800-53-rev5",
            "family": family, "description": _oscal_statement(control),
            "references": ["https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final"],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-800-53"], "refresh_date": today},
        }))
        for enh in control.get("controls", []) or []:  # control enhancements
            emit(enh, family)
    def walk_group(g):
        fam = g.get("title", "")
        for c in g.get("controls", []) or []:
            emit(c, fam)
        for sg in g.get("groups", []) or []:
            walk_group(sg)
    for g in cat.get("groups", []) or []:
        walk_group(g)
    return out


def normalize_nist80053(limit):
    data = _fetch_json(NIST_80053_OSCAL, timeout=120)
    recs = build_nist80053(data)
    return recs[:limit] if limit else recs


NORMALIZERS.update({"nist-800-53": normalize_nist80053})


# ── MITRE ATLAS (Adversarial Threat Landscape for AI Systems) → ai-attack-technique ──
ATLAS_YAML = "https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml"


def build_atlas(data, today=None):
    today = today or _today()
    out = []
    for m in (data.get("matrices") or []):
        tac = {t.get("id"): t.get("name") for t in (m.get("tactics") or [])}
        for t in (m.get("techniques") or []):
            tid = t.get("id")
            if not tid:
                continue
            tacs = t.get("tactics") or []
            out.append(("ai-attack-technique", "technique_id", {
                "technique_id": tid, "name": t.get("name", ""),
                "description": (t.get("description", "") or "")[:1200],
                "tactics": [tac.get(x, x) for x in tacs] if isinstance(tacs, list) else [],
                "framework": "mitre-atlas",
                "subtechnique_of": t.get("subtechnique-of"),
                "references": [f"https://atlas.mitre.org/techniques/{tid}"],
                "provenance": {"ingested_by": "catalog-ingest", "sources": ["mitre-atlas"], "refresh_date": today},
            }))
    return out


def normalize_atlas(limit):
    import yaml as _yaml
    with _safe_urlopen(ATLAS_YAML, timeout=60) as r:
        data = _yaml.safe_load(r.read().decode())
    recs = build_atlas(data)
    return recs[:limit] if limit else recs


NORMALIZERS.update({"mitre-atlas": normalize_atlas})
# Planned (declared in the descriptor, not yet wired): wiz-cvdb (cloud vulns, per-file YAML),
# attack-cri (FSI control mappings), enisa (threat landscape PDF).


# ── catalog write (lazy boto3; upsert by entity key) ─────────────────────────────
def _memory_id():
    return os.environ["TESSERA_MEMORY_ID"]


def _clients():
    import boto3
    region = os.environ.get("AWS_REGION", "us-east-1")
    return (boto3.client("bedrock-agentcore", region_name=region),
            boto3.client("bedrock-agentcore-control", region_name=region))


def _strategy_id(cpc, memory_id):
    for s in (cpc.get_memory(memoryId=memory_id)["memory"].get("strategies") or []):
        if s.get("name") == CATALOG_STRATEGY_NAME or s.get("type") == "CUSTOM":
            return s.get("strategyId")
    return None


def _content_hash(rec):
    """Stable hash of a record's MEANINGFUL content — excludes volatile provenance
    (refresh_date) and any prior content_hash, so re-ingesting identical data hashes
    identically. Computed on BOTH existing and incoming records => backfill-free
    incrementality (no migration needed to start skipping unchanged records)."""
    import copy, hashlib
    r = copy.deepcopy(rec)
    r.pop("content_hash", None)
    pv = r.get("provenance")
    if isinstance(pv, dict):
        pv.pop("refresh_date", None)
    return hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _diff_plan(existing, incoming):
    """Pure diff. existing/incoming: {key: hash}. Returns key lists.
    create = new keys; update = same key, changed hash; skip = identical."""
    create = [k for k in incoming if k not in existing]
    update = [k for k in incoming if k in existing and existing[k] != incoming[k]]
    skip = [k for k in incoming if k in existing and existing[k] == incoming[k]]
    return {"create": create, "update": update, "skip": skip}


# On a suspicious fetch (empty, or a large drop vs prior same-source count) we ABORT
# that type's writes and keep prior data — never destroy good records on a bad fetch.
_SANITY_DROP = float(os.environ.get("CATALOG_SANITY_DROP", "0.5"))


def _upsert(items, ac, cpc, memory_id):
    """Incremental, non-destructive diff-upsert per (type, source). Returns a change
    report: {type: {added, updated, skipped, deprecated, aborted, prior, incoming,
    changed_keys}}. Only create/update writes; identical records are skipped; records
    dropped by the source are soft-tombstoned (status=deprecated), never hard-deleted."""
    sid = _strategy_id(cpc, memory_id)
    if not sid:
        raise RuntimeError("catalog strategy unavailable")
    now = datetime.now(timezone.utc)
    report = {}
    by_type = {}
    for etype, kf, rec in items:
        by_type.setdefault(etype, []).append((kf, rec))

    def _create(etype, kf, rec):
        rec = dict(rec); rec["content_hash"] = _content_hash(rec)
        ac.batch_create_memory_records(memoryId=memory_id, records=[{
            "requestIdentifier": str(uuid.uuid4()),
            "namespaces": [f"/tessera/catalog/{etype}/{_slug(rec[kf])}"],
            "content": {"text": json.dumps(rec)}, "timestamp": now, "memoryStrategyId": sid}])

    for etype, rows in by_type.items():
        kf = rows[0][0]
        incoming = {}       # key -> (hash, rec)
        for _kf, rec in rows:
            incoming[_slug(rec[kf])] = (_content_hash(rec), rec)
        incoming_srcs = set()
        for _kf, rec in rows:
            incoming_srcs.update((rec.get("provenance") or {}).get("sources") or [])

        # snapshot existing records of this type (compute hash on-the-fly from content)
        existing = {}       # key -> {id, hash, srcs, deprecated}
        tok = None
        while True:
            kw = {"memoryId": memory_id, "namespacePath": f"/tessera/catalog/{etype}", "maxResults": 100}
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
                k = _slug(str(old.get(kf, "")))
                if not k:
                    continue
                existing[k] = {"id": rid, "hash": _content_hash(old),
                               "srcs": set((old.get("provenance") or {}).get("sources") or []),
                               "deprecated": (old.get("status") == "deprecated"), "rec": old}
            tok = r.get("nextToken")
            if not tok:
                break

        # sanity guard — abort this type's writes on empty/large-drop fetch
        prior_same = sum(1 for v in existing.values() if v["srcs"] & incoming_srcs)
        if len(incoming) == 0 or (prior_same > 0 and len(incoming) < prior_same * (1 - _SANITY_DROP)):
            report[etype] = {"added": 0, "updated": 0, "skipped": 0, "deprecated": 0,
                             "aborted": True, "prior": prior_same, "incoming": len(incoming), "changed_keys": []}
            continue

        plan = _diff_plan({k: v["hash"] for k, v in existing.items()},
                          {k: h for k, (h, _r) in incoming.items()})
        changed = []
        for k in plan["create"]:
            _create(etype, kf, incoming[k][1]); changed.append(k)
        for k in plan["update"]:
            ac.batch_delete_memory_records(memoryId=memory_id, records=[{"memoryRecordId": existing[k]["id"]}])
            _create(etype, kf, incoming[k][1]); changed.append(k)
        # soft-tombstone: existing SAME-SOURCE keys the source no longer emits
        deprecated = 0
        for k, v in existing.items():
            if k in incoming or v["deprecated"] or not (v["srcs"] & incoming_srcs):
                continue
            dr = dict(v["rec"]); dr["status"] = "deprecated"; dr["deprecated_at"] = now.isoformat()
            dr["content_hash"] = _content_hash(dr)
            ac.batch_delete_memory_records(memoryId=memory_id, records=[{"memoryRecordId": v["id"]}])
            ac.batch_create_memory_records(memoryId=memory_id, records=[{
                "requestIdentifier": str(uuid.uuid4()),
                "namespaces": [f"/tessera/catalog/{etype}/{k}"],
                "content": {"text": json.dumps(dr)}, "timestamp": now, "memoryStrategyId": sid}])
            deprecated += 1
        report[etype] = {"added": len(plan["create"]), "updated": len(plan["update"]),
                         "skipped": len(plan["skip"]), "deprecated": deprecated, "aborted": False,
                         "prior": prior_same, "incoming": len(incoming), "changed_keys": changed}
    return report


def _load_type(ac, memory_id, etype):
    """Load all non-deprecated records of one catalog type."""
    out, tok = [], None
    while True:
        kw = {"memoryId": memory_id, "namespacePath": f"/tessera/catalog/{etype}", "maxResults": 100}
        if tok:
            kw["nextToken"] = tok
        r = ac.list_memory_records(**kw)
        for x in (r.get("memoryRecordSummaries") or []):
            try:
                rec = json.loads((x.get("content") or {}).get("text", "{}"))
                if rec.get("status") != "deprecated":
                    out.append(rec)
            except Exception:
                pass
        tok = r.get("nextToken")
        if not tok:
            break
    return out


def _load_synth_inputs(ac, memory_id):
    """Load the (non-deprecated) synthesis-input entities from Memory for clustering."""
    def load(prefix):
        out, tok = [], None
        while True:
            kw = {"memoryId": memory_id, "namespacePath": prefix, "maxResults": 100}
            if tok:
                kw["nextToken"] = tok
            r = ac.list_memory_records(**kw)
            for x in (r.get("memoryRecordSummaries") or []):
                try:
                    rec = json.loads((x.get("content") or {}).get("text", "{}"))
                    if rec.get("status") != "deprecated":
                        out.append(rec)
                except Exception:
                    pass
            tok = r.get("nextToken")
            if not tok:
                break
        return out
    return (load("/tessera/catalog/attack-technique"),
            load("/tessera/catalog/security-control"),
            load("/tessera/catalog/vulnerability"))


def _plan_synthesis(event):
    """Return the clusters that need (re)synthesis — inputs changed or output missing.
    Each to_run entry carries the cluster payload (mission) the synthesizer reads."""
    import synth_plan, refresh_store
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    ac, _cpc = _clients()
    techs, ctrls, vulns = _load_synth_inputs(ac, _memory_id())
    clusters = synth_plan.cluster(techs, ctrls, vulns)
    # AI/agentic clusters (fixes the agentic-threat dead-end): cluster OWASP LLM/Agentic
    # threats by category -> AI risk clusters, with the GAI-risk catalog as reference.
    agentic = _load_type(ac, _memory_id(), "agentic-threat")
    gai = _load_type(ac, _memory_id(), "gai-risk")
    clusters += synth_plan.cluster_agentic(agentic, gai)
    # Guaranteed business-scenario clusters (WS-2) for categories ATT&CK-tactic clustering
    # under-produces (insider-threat, supply-chain, regulatory-compliance, reputational).
    breaches = _load_type(ac, _memory_id(), "breach")
    clusters += synth_plan.cluster_scenarios(techs, ctrls, vulns, breaches)

    # ---- Standards-anchored enumeration (strategic) --------------------------------
    # The risk COUNT is derived from the standardized inputs (one slot per OWASP/agentic
    # AI threat; one slot per material ATT&CK-tactic × business-outcome cell), NOT from the
    # model's discretion — so it is reproducible. We then synthesize only the slots not yet
    # covered by existing risks (ADDITIVE top-up: keep what's there, fill the gaps).
    tech_tactics = {t.get("technique_id"): (t.get("tactics") or []) for t in techs if t.get("technique_id")}
    existing = _load_type(ac, _memory_id(), "risk")
    covered, cat_counts = synth_plan.covered_cells(existing, tech_tactics)
    p = synth_plan.plan_slots(clusters, covered, cat_counts)
    cby = {c["cluster_id"]: c for c in clusters}
    to_run = []
    for e in p["to_run"]:
        cid = e["cluster_id"]
        mission = dict(cby[cid])
        mission["slots"] = e["slots"]   # the explicit, pre-enumerated risks this cluster must produce
        # Stash the (large) cluster mission in DynamoDB so the SFN state stays under the
        # 256KB service limit; the Map fetches it per-cluster via `get_mission`.
        refresh_store.put_cluster(domain, cid, {"input_digest": e["input_digest"],
                                                "mission": json.dumps(mission), "status": "pending",
                                                "n_slots": e["n_slots"]})
        to_run.append({"cluster_id": cid, "input_digest": e["input_digest"]})
    return {"action": "plan_synthesis", "domain": domain, "clusters": len(clusters),
            "slots_total": sum(len(synth_plan.enumerate_slots(c)) for c in clusters),
            "slots_to_synthesize": sum(e["n_slots"] for e in p["to_run"]),
            "to_run": to_run, "skipped": [e["cluster_id"] for e in p["skip"]]}


def _get_mission(event):
    """Return a cluster's stashed synthesis mission (fetched per-cluster inside the Map so
    the orchestrator state never carries all missions at once)."""
    from tessera_platform.catalog import store as refresh_store
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    cid = event.get("cluster_id")
    it = refresh_store.get_cluster(domain, cid) or {}
    return {"cluster_id": cid, "mission": it.get("mission", ""), "input_digest": it.get("input_digest", "")}


def _checkpoint_cluster(event):
    """Record a cluster's synthesis checkpoint (input_digest + output present) so an
    unchanged cluster is skipped next run, and a resumed run doesn't redo it."""
    from tessera_platform.catalog import store as refresh_store
    import hashlib
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    cid = event.get("cluster_id")
    ind = event.get("input_digest", "")
    outd = event.get("output_digest") or hashlib.sha256((str(ind) + "|synthesized").encode()).hexdigest()[:16]
    refresh_store.put_cluster(domain, cid, {"input_digest": ind, "output_digest": outd, "status": "done"})
    return {"cluster_id": cid, "checkpointed": True}


def _mark_source(event):
    """Write a per-source status for AGENT sources (which have no diff report) — counts
    the source's live (non-deprecated) records of its produced types for the UI."""
    from tessera_platform.catalog import store as refresh_store
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    source = event.get("source")
    produces = event.get("produces") or []
    ac, _cpc = _clients()
    mid = _memory_id()
    count = 0
    for t in produces:
        tok = None
        while True:
            kw = {"memoryId": mid, "namespacePath": f"/tessera/catalog/{t}", "maxResults": 100}
            if tok:
                kw["nextToken"] = tok
            r = ac.list_memory_records(**kw)
            for x in (r.get("memoryRecordSummaries") or []):
                try:
                    rec = json.loads((x.get("content") or {}).get("text", "{}"))
                    if source in ((rec.get("provenance") or {}).get("sources") or []) and rec.get("status") != "deprecated":
                        count += 1
                except Exception:
                    pass
            tok = r.get("nextToken")
            if not tok:
                break
    refresh_store.put_source(domain, source, {"status": "done", "mode": "agent", "count": count, "changed": True})
    return {"source": source, "count": count}


def _coverage(event):
    """10-scenario coverage matrix over current risk records (WS-2b report/gate)."""
    BIZ = ["data-breach", "ransomware", "insider-threat", "supply-chain", "identity-access",
           "service-disruption", "regulatory-compliance", "ip-theft", "reputational", "ai-agent-compromise"]
    ac, _cpc = _clients()
    counts = {}
    for rec in _load_type(ac, _memory_id(), "risk"):
        c = rec.get("category", "?")
        counts[c] = counts.get(c, 0) + 1
    return {"total": sum(counts.values()), "by_category": counts,
            "missing": [b for b in BIZ if not counts.get(b)]}


def _mark_run(event):
    from tessera_platform.catalog import store as refresh_store
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    state = event.get("state", "done")
    refresh_store.release_lock(domain, state)
    return {"domain": domain, "state": state}


def _ingest_all(event):
    """Orchestrate all FETCH-mode sources in-process (fast, incremental, checkpointed).
    Each source runs the diff-upsert + writes its SRC manifest. Returns per-source
    change summary; the orchestrator uses this for progress. Agent sources + synthesis
    are handled by the catalog-refresh state machine (parallel child executions)."""
    from tessera_platform.catalog import store as refresh_store
    domain = event.get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
    sources = event.get("fetch_sources") or [s for s in NORMALIZERS]
    ac, cpc = _clients()
    mid = _memory_id()
    out = {}
    for s in sources:
        if s not in NORMALIZERS:
            out[s] = {"status": "unknown"}
            continue
        try:
            items = NORMALIZERS[s](0)
            report = _upsert(items, ac, cpc, mid)
            refresh_store.put_source_manifest(domain, s, report, len(items))
            out[s] = {"status": "aborted" if any(v.get("aborted") for v in report.values()) else "done",
                      "added": sum(v["added"] for v in report.values()),
                      "updated": sum(v["updated"] for v in report.values()),
                      "skipped": sum(v["skipped"] for v in report.values()),
                      "changed": bool([t for t, v in report.items() if (v["added"] or v["updated"] or v["deprecated"])])}
        except Exception as e:
            refresh_store.put_source(domain, s, {"status": "failed", "error": str(e)[:200]})
            out[s] = {"status": "failed", "error": str(e)[:160]}
    any_changed = any(v.get("changed") for v in out.values())
    return {"domain": domain, "sources": out, "any_changed": any_changed}


def handler(event, context):
    event = event or {}
    action = event.get("action")
    if action == "plan_synthesis":
        return _plan_synthesis(event)
    if action == "checkpoint_cluster":
        return _checkpoint_cluster(event)
    if action == "mark_source":
        return _mark_source(event)
    if action == "mark_run":
        return _mark_run(event)
    if action == "ingest_all":
        return _ingest_all(event)
    if action == "coverage":
        return _coverage(event)
    if action == "get_mission":
        return _get_mission(event)
    source = (event or {}).get("source", "mitre-attack")
    limit = int((event or {}).get("limit", 0))
    if source not in NORMALIZERS:
        return {"error": f"unknown source {source!r}; known: {sorted(NORMALIZERS)}"}
    items = NORMALIZERS[source](limit)
    ac, cpc = _clients()
    report = _upsert(items, ac, cpc, _memory_id())
    added = sum(r["added"] for r in report.values())
    updated = sum(r["updated"] for r in report.values())
    skipped = sum(r["skipped"] for r in report.values())
    deprecated = sum(r["deprecated"] for r in report.values())
    changed_types = [t for t, r in report.items() if (r["added"] or r["updated"] or r["deprecated"])]
    # Checkpoint this source's manifest (resume + status + versioning). Best-effort.
    try:
        from tessera_platform.catalog import store as refresh_store
        domain = (event or {}).get("domain") or os.environ.get("CATALOG_DOMAIN", "cyber-security-risk")
        refresh_store.put_source_manifest(domain, source, report, len(items))
    except Exception as e:
        print(f"[refresh_store] manifest write skipped: {type(e).__name__}: {str(e)[:160]}")
    return {"source": source, "normalized": len(items),
            "added": added, "updated": updated, "skipped": skipped, "deprecated": deprecated,
            "changed": bool(changed_types), "changed_types": changed_types, "report": report}
