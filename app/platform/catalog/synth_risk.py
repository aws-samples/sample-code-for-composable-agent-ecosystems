"""tessera_platform.catalog.synth_risk — the cyber-security-risk synthesis plugin.

The OPTIONAL Synthesizer plugin (spec-declarative-ingest.md §5) for the risk domain,
runnable on the GENERIC ingest path: `synthesis: {plugin: tessera_platform.catalog.synth_risk}`.

Two halves:
  • PURE functions (cluster / cluster_agentic / cluster_scenarios / enumerate_slots /
    covered_cells / plan_slots / cluster_digest / plan) — deterministic, unit-tested,
    the single source of truth (the legacy lambdas/catalog-ingest/synth_plan.py now
    re-exports these, so there is no copy-paste — AC #4).
  • plan_synthesis(store, domain, event) — the LIVE loader the generic orchestrator calls
    via the Synthesizer contract. Reads inputs from the (version-scoped) CatalogStore, so
    synthesis reads the exact catalog version being built (blue/green safe), clusters +
    enumerates standards-anchored risk slots, and plans only the un-covered slots (additive
    top-up). Stashes each to_run cluster's mission in the refresh store so the SFN Map
    fetches it per-cluster (get_mission), keeping SFN state under the 256KB limit.
"""
import json
import hashlib
import re


def cluster(techniques, controls, vulns, per_cluster=12):
    """(techniques, controls, vulns) -> bounded clusters (one per ATT&CK tactic)."""
    controls_by_id = {c.get("control_id"): c for c in controls}
    by_tactic = {}
    for t in techniques:
        tac = (t.get("tactics") or ["uncategorized"])[0]
        by_tactic.setdefault(tac, []).append(t)
    exploited = [{"cve": v.get("cve_or_ref"), "name": v.get("name"), "product": v.get("product")}
                 for v in vulns if v.get("known_ransomware")][:10]
    clusters = []
    for tac, techs in sorted(by_tactic.items()):
        chosen = sorted(techs, key=lambda x: (-len(x.get("mitigations") or []), x.get("technique_id", "")))[:per_cluster]
        ctrl_ids = sorted({m for t in chosen for m in (t.get("mitigations") or [])})
        clusters.append({
            "cluster_id": f"cluster-{tac}", "tactic": tac,
            "technique_ids": [t.get("technique_id") for t in chosen],
            "techniques": [{"technique_id": t.get("technique_id"), "name": t.get("name"),
                            "description": (t.get("description", "") or "")[:400],
                            "platforms": t.get("platforms", []), "mitigations": t.get("mitigations", [])} for t in chosen],
            "control_ids": ctrl_ids,
            "controls": [{"control_id": cid, "name": (controls_by_id.get(cid) or {}).get("name"),
                          "description": ((controls_by_id.get(cid) or {}).get("description", "") or "")[:300]} for cid in ctrl_ids],
            "exploited_context": exploited, "size": len(chosen),
        })
    return sorted(clusters, key=lambda c: -c["size"])


def cluster_agentic(agentic_threats, gai_risks=None):
    """Cluster AI/agentic threats by OWASP category (llm-application vs agentic) into AI
    risk clusters. `kind: ai` tells the synthesizer to anchor to NIST AI RMF / AI 600-1 /
    OWASP instead of ATT&CK/CSF. Fixes the agentic-threat pipeline dead-end."""
    by_cat = {}
    for t in agentic_threats:
        tid = str(t.get("threat_id") or "")
        cat = t.get("category") or ("llm-application" if tid.upper().startswith("LLM") else "agentic")
        by_cat.setdefault(cat, []).append(t)
    gai = [{"gai_risk_id": g.get("gai_risk_id"), "name": g.get("name")} for g in (gai_risks or [])]
    clusters = []
    for cat, threats in sorted(by_cat.items()):
        chosen = sorted(threats, key=lambda x: x.get("threat_id", ""))
        clusters.append({
            "cluster_id": f"cluster-ai-{cat}", "kind": "ai", "owasp_category": cat,
            "threats": [{"threat_id": t.get("threat_id"), "name": t.get("name"),
                         "description": (t.get("description", "") or "")[:400],
                         "framework": t.get("framework")} for t in chosen],
            "gai_risk_catalog": gai,
            "size": len(chosen),
        })
    return sorted(clusters, key=lambda c: -c["size"])


# Business-outcome scenarios that ATT&CK-tactic clustering under-produces. Each is seeded
# from matching techniques (by id/tactic/keyword) + relevant breach exemplars so the
# catalog always covers them (WS-2). Guaranteed-coverage clusters.
_SCENARIO_SEEDS = {
    "insider-threat": {
        "tactics": {"collection", "exfiltration"},
        "ids": {"T1078", "T1074", "T1114", "T1213", "T1530", "T1567", "T1052", "T1020", "T1119"},
        "kw": r"insider|valid account|email collection|data from|staged|privilege",
        "breach_kw": r"internal|employee|privilege|misuse",
    },
    "supply-chain": {
        "tactics": set(),
        "ids": {"T1195", "T1199", "T1072", "T1554", "T1601", "T1074"},
        "kw": r"supply.?chain|trusted relationship|third.?party|software deployment|compromise software|dependenc",
        "breach_kw": r"supply|partner|third.?party|vendor",
    },
    "regulatory-compliance": {
        "tactics": set(),
        "ids": {"T1213", "T1530", "T1114", "T1567", "T1048", "T1041"},
        "kw": r"\bpii\b|\bphi\b|personal data|customer data|credit card|health record|gdpr|hipaa|pci|regulat|complian",
        "breach_kw": r"personal|medical|payment|financial|healthcare|pci|hipaa",
    },
    "reputational": {
        "tactics": set(),
        "ids": {"T1491", "T1485", "T1565", "T1657", "T1489", "T1490"},
        "kw": r"deface|leak|public|reputation|extortion|doxx|disclos|embarrass",
        "breach_kw": r"public|leak|expos|disclos",
    },
}


def cluster_scenarios(techniques, controls, vulns, breaches, per_cluster=12, per_breach=6):
    """One guaranteed cluster per under-covered business scenario, seeded from matching
    techniques + breach exemplars. kind='scenario' tells the synthesizer to emit risks in
    the given `category` framed as that business scenario."""
    controls_by_id = {c.get("control_id"): c for c in controls}
    out = []
    for scen, seed in _SCENARIO_SEEDS.items():
        ids, tacs, kw = seed["ids"], seed["tactics"], seed["kw"]
        matched = []
        for t in techniques:
            tid = str(t.get("technique_id") or "")
            ttac = set(t.get("tactics") or [])
            text = (str(t.get("name", "")) + " " + str(t.get("description", ""))).lower()
            if (any(tid == i or tid.startswith(i + ".") for i in ids) or (ttac & tacs) or re.search(kw, text)):
                matched.append(t)
        chosen = sorted(matched, key=lambda x: (-len(x.get("mitigations") or []), x.get("technique_id", "")))[:per_cluster]
        ctrl_ids = sorted({m for t in chosen for m in (t.get("mitigations") or [])})
        bex = [b for b in breaches if re.search(seed["breach_kw"], json.dumps(b, default=str).lower())][:per_breach]
        out.append({
            "cluster_id": f"cluster-scenario-{scen}", "kind": "scenario", "category": scen, "scenario": scen,
            "technique_ids": [t.get("technique_id") for t in chosen],
            "techniques": [{"technique_id": t.get("technique_id"), "name": t.get("name"),
                            "description": (t.get("description", "") or "")[:400],
                            "mitigations": t.get("mitigations", [])} for t in chosen],
            "control_ids": ctrl_ids,
            "controls": [{"control_id": cid, "name": (controls_by_id.get(cid) or {}).get("name"),
                          "description": ((controls_by_id.get(cid) or {}).get("description", "") or "")[:300]} for cid in ctrl_ids],
            "breach_exemplars": [{"org": b.get("org"), "year": b.get("year"), "industry": b.get("industry"),
                                  "summary": (b.get("summary", "") or "")[:300]} for b in bex],
            "size": len(chosen),
        })
    return out


# ---- Governance clustering (AI governance, not just AI-security) ------------------
# Map each NIST AI 600-1 GAI risk to a GOVERNANCE category. These become `risk` records
# framed as governance risks (fairness, transparency, accountability, safety, privacy,
# societal-harm, environmental) — beyond the security-framed `ai-agent-compromise`.
_GAI_TO_GOV = {
    "harmful-bias": "fairness-bias",
    "data-privacy": "privacy",
    "confabulation": "transparency",
    "info-integrity": "transparency",
    "dangerous-violent-hateful": "societal-harm",
    "obscene-abusive": "societal-harm",
    "cbrn": "safety",
    "environmental": "environmental",
    "human-ai-config": "accountability",
    "intellectual-property": "accountability",
    "value-chain": "accountability",
    "info-security": "safety",
}
_GOV_CATEGORIES = ["fairness-bias", "transparency", "accountability", "safety",
                   "privacy", "societal-harm", "environmental"]


def _obligation_gov_cat(o):
    """Infer a governance category for an obligation lacking an explicit `category`
    (e.g. full-text EU AI Act articles), by keyword over title/description. Deterministic."""
    t = (str(o.get("title", "")) + " " + str(o.get("description", ""))).lower()
    for pat, cat in (
        (r"prohibit|manipulat|social scor|subliminal|exploit", "societal-harm"),
        (r"bias|discriminat|data governance|training data|fairness", "fairness-bias"),
        (r"transparen|inform|logging|record-keeping|documentation|disclos|label|marking", "transparency"),
        (r"human oversight|accountab|deployer|post-market|governance|responsib|conformity|registration", "accountability"),
        (r"risk management|accuracy|robust|cybersecur|safety|resilien|systemic risk|adversarial", "safety"),
        (r"privacy|personal data|biometric", "privacy"),
        (r"environment|energy|sustainab", "environmental"),
    ):
        if re.search(pat, t):
            return cat
    return "accountability"


def cluster_governance(gai_risks, obligations, gov_controls, incidents):
    """One cluster per GOVERNANCE category, seeded from NIST AI 600-1 GAI risks mapped to
    that category + the relevant EU AI Act obligations + the NIST AI RMF control backbone +
    real AI-incident exemplars. kind='governance' tells the synthesizer to anchor to
    EU AI Act / NIST AI RMF / AI 600-1 (NOT ATT&CK/CSF)."""
    by_cat = {}
    for g in gai_risks or []:
        cat = _GAI_TO_GOV.get(g.get("gai_risk_id"), "accountability")
        by_cat.setdefault(cat, []).append(g)
    obl_by_cat, inc_by_cat = {}, {}
    for o in obligations or []:
        obl_by_cat.setdefault(o.get("category") or _obligation_gov_cat(o), []).append(o)
    for i in incidents or []:
        inc_by_cat.setdefault(i.get("harm_category"), []).append(i)
    controls = [{"control_id": c.get("control_id"), "name": c.get("name"),
                 "description": (c.get("description", "") or "")[:200]} for c in (gov_controls or [])]
    out = []
    for cat, gais in sorted(by_cat.items()):
        out.append({
            "cluster_id": f"cluster-gov-{cat}", "kind": "governance", "category": cat,
            "gai_risks": [{"gai_risk_id": g.get("gai_risk_id"), "name": g.get("name"),
                           "description": (g.get("description", "") or "")[:400]} for g in gais],
            "obligations": [{"obligation_id": o.get("obligation_id"), "article": o.get("article"),
                             "title": o.get("title"), "tier": o.get("tier")} for o in obl_by_cat.get(cat, [])],
            "controls": controls,   # the NIST AI RMF GOVERN/MAP/MEASURE/MANAGE backbone
            "incident_exemplars": [{"incident_id": i.get("incident_id"), "name": i.get("name"),
                                    "year": i.get("year"), "description": (i.get("description", "") or "")[:250]}
                                   for i in inc_by_cat.get(cat, [])],
            "size": len(gais),
        })
    return sorted(out, key=lambda c: -c["size"])


# ---- Strategic enumeration -------------------------------------------------------
# The risk COUNT is anchored to standards, not to the model's discretion. We enumerate
# a deterministic set of "risk slots" from the standardized inputs:
#   • AI clusters  -> one slot per OWASP LLM/Agentic threat (a standardized list).
#   • tactic clusters -> one slot per (ATT&CK tactic × business-outcome) cell that the
#     tactic's techniques MATERIALLY drive (data-grounded via _OUTCOME_MATCHERS keyword/
#     id matchers — NOT a hardcoded tactic→outcome table).
# Each slot has a STABLE, derived risk_id, so the count is reproducible across runs and
# top-up is additive (only un-covered cells synthesize; existing records are kept).

# Business-outcome matchers for ALL traditional (non-AI) outcomes. The 4 hard scenarios
# reuse their _SCENARIO_SEEDS patterns; the other 5 are added here. Each: technique ids +
# a keyword regex over technique name/description. Data-grounded, no per-tactic hardcoding.
_OUTCOME_MATCHERS = {
    "data-breach": {"ids": {"T1213", "T1530", "T1005", "T1039", "T1114", "T1074", "T1119", "T1602"},
                    "kw": r"data|database|sensitive|confidential|records|collect|exfiltrat|steal"},
    "ransomware": {"ids": {"T1486", "T1490", "T1489", "T1491", "T1561", "T1529"},
                   "kw": r"ransom|encrypt|extort|inhibit (system )?recovery|destroy data|wipe"},
    "identity-access": {"ids": {"T1078", "T1110", "T1556", "T1558", "T1550", "T1212", "T1528", "T1621", "T1098"},
                        "kw": r"credential|password|token|account|authentic|kerberos|\bmfa\b|session|privilege"},
    "service-disruption": {"ids": {"T1499", "T1498", "T1489", "T1485", "T1561", "T1529", "T1490"},
                           "kw": r"denial.of.service|\bdos\b|disrupt|availability|shutdown|outage|wipe|destroy"},
    "ip-theft": {"ids": {"T1213", "T1005", "T1039", "T1560", "T1114", "T1602"},
                 "kw": r"intellectual property|source code|trade secret|proprietary|blueprint|patent|research data"},
    "insider-threat": _SCENARIO_SEEDS["insider-threat"],
    "supply-chain": _SCENARIO_SEEDS["supply-chain"],
    "regulatory-compliance": _SCENARIO_SEEDS["regulatory-compliance"],
    "reputational": _SCENARIO_SEEDS["reputational"],
}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def _tech_matches(tech, matcher):
    tid = str(tech.get("technique_id") or "")
    text = (str(tech.get("name", "")) + " " + str(tech.get("description", ""))).lower()
    if any(tid == i or tid.startswith(i + ".") for i in matcher.get("ids", set())):
        return True
    kw = matcher.get("kw")
    return bool(kw and re.search(kw, text))


def enumerate_slots(cluster, min_hits=1):
    """Deterministic list of risk slots for a cluster. Each slot has a STABLE slot_id used
    verbatim as the risk_id, so the risk COUNT is standards-derived + reproducible."""
    cid = cluster["cluster_id"]
    if cluster.get("kind") == "governance":
        out = []
        for g in cluster.get("gai_risks") or []:
            gid = g.get("gai_risk_id")
            if not gid:
                continue
            out.append({"slot_id": f"RSK-{_slug(cluster.get('category'))}-{_slug(gid)}",
                        "category": cluster.get("category"), "cluster_id": cid, "kind": "governance",
                        "focus_gai": gid, "focus_name": g.get("name")})
        return out
    if cluster.get("kind") == "ai":
        out = []
        for t in cluster.get("threats") or []:
            tid = t.get("threat_id")
            if not tid:
                continue
            out.append({"slot_id": f"RSK-AI-{_slug(tid)}", "category": "ai-agent-compromise",
                        "cluster_id": cid, "kind": "ai", "focus_threat": tid, "focus_name": t.get("name")})
        return out
    if cluster.get("kind") == "scenario":
        scen = cluster.get("category")
        techs = cluster.get("techniques") or []
        n = min(3, max(1, (len(techs) + 3) // 4))   # 1-3 deterministic slots per hard scenario
        return [{"slot_id": f"RSK-{_slug(scen)}-{i + 1:02d}", "category": scen, "cluster_id": cid,
                 "kind": "scenario", "scenario": scen,
                 "focus_technique_ids": [t.get("technique_id") for t in techs[i::n]][:6]} for i in range(n)]
    tac = cluster.get("tactic") or cluster.get("category") or "general"
    techs = cluster.get("techniques") or []
    slots = []
    for outcome, m in _OUTCOME_MATCHERS.items():
        hits = [t.get("technique_id") for t in techs if _tech_matches(t, m)]
        if len(hits) >= min_hits:
            slots.append({"slot_id": f"RSK-{_slug(tac)}-{_slug(outcome)}", "category": outcome,
                          "cluster_id": cid, "kind": "tactic", "tactic": tac,
                          "focus_technique_ids": hits[:8]})
    return slots


def covered_cells(existing_risks, tech_tactics):
    """Coverage already satisfied by existing risks — enables ADDITIVE top-up. Returns
    (covered_set, cat_counts):
      • covered_set: (tactic-slug, category) keys (tactic inferred from technique ids) plus
        ("__ai__","ai-agent-compromise"); a tactic slot is covered if its cell is present.
      • cat_counts: {category: n} so a hard-scenario slot is covered once its category
        already has enough records (keeps existing supply-chain/data-breach, tops up the
        empty categories)."""
    covered, cat_counts, ids = set(), {}, set()
    for r in existing_risks or []:
        rid = r.get("risk_id")
        if rid:
            ids.add(rid)
        cat = r.get("category")
        if not cat:
            continue
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if cat == "ai-agent-compromise":
            covered.add(("__ai__", "ai-agent-compromise"))
            continue
        tids = ((r.get("sources") or {}).get("attack_techniques")) or r.get("technique_ids") or []
        tacs = {_slug(tac) for tid in tids for tac in (tech_tactics.get(tid) or [])}
        for tac in (tacs or {"__none__"}):
            covered.add((tac, cat))
    return covered, cat_counts, ids


def _slot_covered(slot, covered, cat_counts, scen_min, ids=None):
    kind = slot.get("kind")
    if kind == "governance":
        # exact-id coverage: one governance risk per (category, GAI risk)
        return slot.get("slot_id") in (ids or set())
    if kind == "ai":
        return ("__ai__", "ai-agent-compromise") in covered
    if kind == "scenario":
        return cat_counts.get(slot.get("category"), 0) >= scen_min
    return (_slug(slot.get("tactic")), slot.get("category")) in covered


def plan_slots(clusters, covered, cat_counts=None, scen_min=2, ids=None):
    """Per-cluster plan of the MISSING (un-covered) slots. Clusters with no missing slot
    are skipped -> the run only synthesizes the additive top-up. The input_digest is over
    the missing slot_ids so a changed slot set re-runs; a stable one resumes cleanly."""
    cat_counts = cat_counts or {}
    to_run, skip = [], []
    for c in clusters:
        missing = [s for s in enumerate_slots(c) if not _slot_covered(s, covered, cat_counts, scen_min, ids)]
        digest = hashlib.sha256(json.dumps(sorted(s["slot_id"] for s in missing)).encode()).hexdigest()[:16]
        entry = {"cluster_id": c["cluster_id"], "input_digest": digest,
                 "slots": missing, "n_slots": len(missing)}
        (to_run if missing else skip).append(entry)
    return {"to_run": to_run, "skip": skip}


def cluster_digest(c):
    """Stable hash of a cluster's MEANINGFUL inputs. Works for both ATT&CK-tactic clusters
    and AI/agentic clusters (threats). Changes iff a member's content or membership changes."""
    payload = {
        "technique_ids": sorted(c.get("technique_ids") or []),
        "control_ids": sorted(c.get("control_ids") or []),
        "techniques": c.get("techniques"),
        "controls": c.get("controls"),
        "exploited_context": c.get("exploited_context"),
        "threats": c.get("threats"),
        "breach_exemplars": c.get("breach_exemplars"),
        "category": c.get("category"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def plan(clusters, checkpoints):
    """Decide which clusters need (re)synthesis. checkpoints: {cluster_id: item|digest}.
    Run if the input digest changed OR there is no recorded output (missing/never run).
    Returns {to_run: [{cluster_id, input_digest}], skip: [{cluster_id, input_digest}]}."""
    to_run, skip = [], []
    for c in clusters:
        d = cluster_digest(c)
        cp = checkpoints.get(c["cluster_id"])
        if isinstance(cp, dict):
            stored, has_output = cp.get("input_digest"), bool(cp.get("output_digest"))
        else:
            stored, has_output = cp, False
        entry = {"cluster_id": c["cluster_id"], "input_digest": d}
        (to_run if (stored != d or not has_output) else skip).append(entry)
    return {"to_run": to_run, "skip": skip}


# ── live loader — the Synthesizer plugin contract (generic path) ─────────────────────
def plan_synthesis(store, domain, event=None):
    """Live risk synthesis planner for the GENERIC ingest path.

    Reads synthesis inputs from the (version-scoped) `store` — so synthesis reads the same
    catalog version being built (blue/green safe) — clusters them deterministically, then
    plans only the un-covered risk slots (additive top-up). Each to_run cluster's mission
    is stashed in the refresh store; the SFN Map fetches it per-cluster via `get_mission`
    (keeps orchestrator state under the 256KB service limit).

    Returns the Synthesizer contract:
      {clusters, slots_total, slots_to_synthesize, to_run:[{cluster_id, input_digest}],
       skipped:[cluster_id]}
    """
    from . import store as refresh_store
    event = event or {}

    techs = store.load_type("attack-technique")
    ctrls = store.load_type("security-control")
    vulns = store.load_type("vulnerability")
    clusters = cluster(techs, ctrls, vulns)
    # AI/agentic clusters (fixes the agentic-threat dead-end).
    clusters += cluster_agentic(store.load_type("agentic-threat"), store.load_type("gai-risk"))
    # Guaranteed business-scenario clusters (WS-2) for under-produced categories.
    clusters += cluster_scenarios(techs, ctrls, vulns, store.load_type("breach"))
    # AI-GOVERNANCE clusters: one per governance category from GAI risks x EU AI Act
    # obligations x NIST AI RMF controls x real AI-incident exemplars.
    clusters += cluster_governance(store.load_type("gai-risk"),
                                   store.load_type("regulatory-obligation"),
                                   store.load_type("ai-governance-control"),
                                   store.load_type("ai-incident"))

    # Standards-anchored enumeration + additive top-up (only synthesize un-covered slots).
    tech_tactics = {t.get("technique_id"): (t.get("tactics") or []) for t in techs if t.get("technique_id")}
    covered, cat_counts, ids = covered_cells(store.load_type("risk"), tech_tactics)
    p = plan_slots(clusters, covered, cat_counts, ids=ids)

    cby = {c["cluster_id"]: c for c in clusters}
    # Blue/green: when synthesizing into a versioned build, `store.version` is the green
    # version. Prepend a plain `catalog_version:` line (the generic convention agent-invoker
    # reads via _mission_field) so each synthesized risk auto-persists into that version's
    # namespace. version=None (routine/active run) => no header => active/legacy namespace.
    version = getattr(store, "version", None)
    header = f"catalog_version: {version}\n" if version else ""
    # Completeness fix: a synth run handed MANY slots reliably under-emits (the LLM drops a
    # few). Batch each cluster's missing slots into small groups (SLOTS_PER_RUN) so every
    # run is small enough that the model emits ALL its slots. More runs, each complete;
    # the additive top-up (covered_cells) converges to full coverage on any re-run.
    SLOTS_PER_RUN = 4
    to_run = []
    for e in p["to_run"]:
        cid = e["cluster_id"]
        slots = e["slots"] or []
        batches = [slots[i:i + SLOTS_PER_RUN] for i in range(0, len(slots), SLOTS_PER_RUN)] or [[]]
        for bi, batch in enumerate(batches):
            key = cid if len(batches) == 1 else f"{cid}-b{bi}"   # safe session-id chars (no '#')
            mission = dict(cby[cid])
            mission["slots"] = batch   # the explicit, pre-enumerated risks THIS run must produce
            refresh_store.put_cluster(domain, key, {"input_digest": e["input_digest"],
                                                    "mission": header + json.dumps(mission),
                                                    "status": "pending", "n_slots": len(batch)})
            to_run.append({"cluster_id": key, "input_digest": e["input_digest"]})
    return {"clusters": len(clusters),
            "slots_total": sum(len(enumerate_slots(c)) for c in clusters),
            "slots_to_synthesize": sum(e["n_slots"] for e in p["to_run"]),
            "to_run": to_run, "skipped": [e["cluster_id"] for e in p["skip"]]}
