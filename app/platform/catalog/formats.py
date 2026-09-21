"""tessera_platform.catalog.formats — shared, cross-domain STANDARD-FORMAT builders.

These parse well-known authoritative formats (MITRE ATT&CK STIX, NIST OSCAL, MITRE
ATLAS, VERIS/VCDB, OWASP, NIST AI RMF, Wiz CVDB) into entity-keyed catalog records.
They are PLATFORM primitives — a NEW domain that reuses one of these formats references
it declaratively (`normalize.mode: builder`, `builder: attack`) instead of writing code.

Extracted VERBATIM (behavior-preserving) from lambdas/catalog-ingest/handler.py; pinned
by tests/unit/test_formats_parity.py against the originals. Each builder is PURE
(no network, no boto3): `build_*(raw, today=None, **args) -> [(entity_type, key_field, record)]`.
"""
import re
from datetime import datetime, timezone


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")[:80]


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _mitre_ext(obj):
    for ref in obj.get("external_references", []) or []:
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


def _live(obj):
    return not obj.get("revoked") and not obj.get("x_mitre_deprecated")


# ── MITRE ATT&CK STIX → attack-technique + security-control (with cross-links) ────
def build_attack_records(objects, today=None, tech_limit=0):
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


# ── CISA KEV → vulnerability ──────────────────────────────────────────────────────
def build_kev_records(kev, today=None, limit=0):
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


# ── VERIS / VCDB → breach ───────────────────────────────────────────────────────
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


def build_owasp(raw=None, today=None):
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


def build_nist(raw=None, today=None):
    today = today or _today()
    return [("risk-method-profile", "risk_type", {
        "risk_type": "ai-" + rid, "name": name, "framework": "nist-ai-rmf", "dimension": name,
        "description": desc, "functions": ["GOVERN", "MAP", "MEASURE", "MANAGE"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-ai-rmf"], "refresh_date": today},
    }) for rid, name, desc in _NIST_AI]


# ── NIST AI 600-1 (Generative AI Profile) → gai-risk ─────────────────────────────
# The 12 GAI risks enumerated in NIST AI 600-1 (July 2024). Durable reference knowledge
# the risk synthesizer anchors AI risks to (frameworks.nist_ai_600_1). Static/constant.
_NIST_AI_600_1 = [
    ("cbrn", "CBRN Information or Capabilities", "Eased access to or synthesis of materially nefarious chemical, biological, radiological, or nuclear (CBRN) information or capabilities."),
    ("confabulation", "Confabulation", "Confidently stated but erroneous or false content ('hallucination') that may mislead or deceive users."),
    ("dangerous-violent-hateful", "Dangerous, Violent, or Hateful Content", "Eased production of and access to violent, inciting, radicalizing, threatening, or hateful content."),
    ("data-privacy", "Data Privacy", "Leakage and unauthorized disclosure or de-anonymization of biometric, health, location, or other sensitive personal data."),
    ("environmental", "Environmental Impacts", "Adverse ecological effects from the resource consumption of training and operating GAI models."),
    ("harmful-bias", "Harmful Bias or Homogenization", "Amplified, systemic, computational, or human-cognitive bias, and homogenization of outputs."),
    ("human-ai-config", "Human-AI Configuration", "Misconfiguration, misuse, or over-reliance arising from how humans interact with or oversee the GAI system."),
    ("info-integrity", "Information Integrity", "Generation and spread of deceptive or misleading content (e.g. mis/disinformation) at scale."),
    ("info-security", "Information Security", "Lowered barriers to offensive cyber capability and expanded attack surface (prompt injection, data poisoning, model exfiltration)."),
    ("intellectual-property", "Intellectual Property", "Infringement, plagiarism, or unauthorized reproduction of IP or proprietary content."),
    ("obscene-abusive", "Obscene, Degrading, and/or Abusive Content", "Eased production of obscene, degrading, or abusive imagery/content, including synthetic CSAM or NCII."),
    ("value-chain", "Value Chain and Component Integration", "Non-transparent or untraceable integration of third-party components, data, or models across the GAI value chain."),
]


def build_gai_risk(raw=None, today=None):
    today = today or _today()
    return [("gai-risk", "gai_risk_id", {
        "gai_risk_id": rid, "name": name, "framework": "nist-ai-600-1",
        "description": desc,
        "references": ["https://doi.org/10.6028/NIST.AI.600-1"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-ai-600-1"], "refresh_date": today},
    }) for rid, name, desc in _NIST_AI_600_1]


# ── EU AI Act (Regulation (EU) 2024/1689) → regulatory-obligation ────────────────
# Curated key obligations by Article (authoritative, deterministic). tier drives the UI;
# category maps to a governance dimension for synthesis anchoring.
_EU_AI_ACT = [
    ("art-5", "Prohibited AI practices", "prohibited", "societal-harm",
     "Bans AI for subliminal/manipulative techniques, exploiting vulnerabilities, social scoring, "
     "individual predictive policing by profiling alone, untargeted facial-image scraping, emotion "
     "inference in workplace/education, sensitive-attribute biometric categorization, and (with narrow "
     "exceptions) real-time remote biometric identification in public spaces for law enforcement."),
    ("art-6", "Classification rules for high-risk AI systems", "high-risk", "accountability",
     "Defines high-risk AI systems (safety components + Annex III areas: biometrics, critical "
     "infrastructure, education, employment, essential public/private services, law enforcement, "
     "migration/asylum, administration of justice)."),
    ("art-9", "Risk management system", "high-risk", "safety",
     "High-risk providers must establish, implement, document and maintain a continuous risk management "
     "system across the AI lifecycle."),
    ("art-10", "Data and data governance", "high-risk", "fairness-bias",
     "Training/validation/testing data must meet quality criteria; examine for possible biases and take "
     "measures to detect/prevent/mitigate them."),
    ("art-11", "Technical documentation", "high-risk", "transparency",
     "Draw up and keep up-to-date technical documentation demonstrating conformity before placing on market."),
    ("art-12", "Record-keeping (logging)", "high-risk", "accountability",
     "High-risk systems must technically allow automatic recording of events (logs) over their lifetime."),
    ("art-13", "Transparency and provision of information to deployers", "high-risk", "transparency",
     "Design for sufficient transparency; provide clear instructions for use enabling deployers to "
     "interpret output and use it appropriately."),
    ("art-14", "Human oversight", "high-risk", "accountability",
     "Design high-risk systems so they can be effectively overseen by natural persons (incl. stop/override)."),
    ("art-15", "Accuracy, robustness and cybersecurity", "high-risk", "safety",
     "Achieve appropriate levels of accuracy, robustness and cybersecurity, consistent throughout the lifecycle."),
    ("art-26", "Obligations of deployers of high-risk AI systems", "high-risk", "accountability",
     "Deployers must use per instructions, ensure human oversight, monitor operation, and keep logs."),
    ("art-50", "Transparency obligations for providers and deployers of certain AI", "transparency", "transparency",
     "Disclose when users interact with AI; mark synthetic audio/image/video/text; label deep fakes; "
     "disclose emotion-recognition and biometric-categorization use."),
    ("art-53", "Obligations for providers of general-purpose AI models", "gpai", "transparency",
     "GPAI providers: keep technical documentation, provide info to downstream providers, implement a "
     "copyright policy, and publish a sufficiently detailed summary of training content."),
    ("art-55", "Obligations for providers of GPAI models with systemic risk", "gpai-systemic", "safety",
     "Perform model evaluation incl. adversarial testing, assess and mitigate systemic risks, track and "
     "report serious incidents, and ensure adequate cybersecurity."),
    ("art-72", "Post-market monitoring", "high-risk", "accountability",
     "Providers must establish and document a post-market monitoring system proportionate to the risks."),
    ("art-73", "Reporting of serious incidents", "high-risk", "safety",
     "Providers must report serious incidents to market surveillance authorities."),
]


def build_eu_ai_act(raw=None, today=None):
    today = today or _today()
    return [("regulatory-obligation", "obligation_id", {
        "obligation_id": f"eu-ai-act-{art}", "framework": "eu-ai-act", "article": art.replace("art-", "Article "),
        "name": name, "title": name, "tier": tier, "category": cat, "description": desc,
        "references": [f"https://eur-lex.europa.eu/eli/reg/2024/1689/oj (CELEX:32024R1689) {art}"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["eu-ai-act"], "refresh_date": today},
    }) for art, name, tier, cat, desc in _EU_AI_ACT]


# ── NIST AI RMF 1.0 (NIST.AI.100-1) → ai-governance-control ───────────────────────
# The 19 categories across GOVERN/MAP/MEASURE/MANAGE (authoritative titles). These are the
# governance control backbone the synthesizer maps risks to.
_NIST_AI_RMF = [
    ("GV.1", "GOVERN", "Policies, processes, procedures and practices for AI risk management are in place, transparent, and implemented effectively."),
    ("GV.2", "GOVERN", "Accountability structures are in place so relevant teams and individuals are empowered, responsible, and trained."),
    ("GV.3", "GOVERN", "Workforce diversity, equity, inclusion, and accessibility processes are prioritized in AI risk management."),
    ("GV.4", "GOVERN", "Organizational teams are committed to a culture that considers and communicates AI risk."),
    ("GV.5", "GOVERN", "Processes are in place for robust engagement with relevant AI actors."),
    ("GV.6", "GOVERN", "Policies address AI risks associated with third-party software, data, and supply chain."),
    ("MP.1", "MAP", "Context is established and understood."),
    ("MP.2", "MAP", "Categorization of the AI system is performed."),
    ("MP.3", "MAP", "AI capabilities, targeted usage, goals, and expected benefits and costs are understood."),
    ("MP.4", "MAP", "Risks and benefits are mapped for all components, including third-party software and data."),
    ("MP.5", "MAP", "Impacts to individuals, groups, communities, organizations, and society are characterized."),
    ("MS.1", "MEASURE", "Appropriate methods and metrics are identified and applied."),
    ("MS.2", "MEASURE", "AI systems are evaluated for trustworthy characteristics."),
    ("MS.3", "MEASURE", "Mechanisms for tracking identified AI risks over time are in place."),
    ("MS.4", "MEASURE", "Feedback about efficacy of measurement is gathered and assessed."),
    ("MG.1", "MANAGE", "AI risks based on assessments are prioritized, responded to, and managed."),
    ("MG.2", "MANAGE", "Strategies to maximize benefits and minimize negative impacts are planned and implemented."),
    ("MG.3", "MANAGE", "AI risks and benefits from third-party entities are managed."),
    ("MG.4", "MANAGE", "Risk treatments, response/recovery, and communication plans are documented and monitored."),
]


def build_nist_ai_rmf_playbook(raw=None, today=None):
    today = today or _today()
    return [("ai-governance-control", "control_id", {
        "control_id": cid, "name": f"{cid} ({func})", "function": func, "framework": "nist-ai-rmf",
        "description": desc,
        "references": ["https://doi.org/10.6028/NIST.AI.100-1",
                       "https://airc.nist.gov/AI_RMF_Knowledge_Base/Playbook"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-ai-rmf-playbook"], "refresh_date": today},
    }) for cid, func, desc in _NIST_AI_RMF]


# ── Notable AI incidents (real, documented) → ai-incident ─────────────────────────
# Curated real-world AI harm exemplars — ground governance risks the way breaches ground
# security risks. Each is a documented public incident.
_AI_INCIDENTS = [
    ("compas-2016", "COMPAS recidivism scoring racial bias", 2016, "fairness-bias",
     "ProPublica found the COMPAS recidivism-risk tool was biased against Black defendants (higher "
     "false-positive rate), raising due-process and fairness concerns in criminal justice."),
    ("amazon-hiring-2018", "Amazon AI recruiting tool gender bias", 2018, "fairness-bias",
     "Amazon scrapped an experimental AI recruiting tool that penalized resumes containing 'women's' and "
     "downgraded graduates of women's colleges, learned from historically male-skewed hiring data."),
    ("microsoft-tay-2016", "Microsoft Tay chatbot toxic outputs", 2016, "societal-harm",
     "Microsoft's Tay Twitter chatbot began producing racist and offensive content within 24 hours after "
     "coordinated adversarial user inputs; it was shut down."),
    ("google-photos-2015", "Google Photos mislabels Black people as gorillas", 2015, "fairness-bias",
     "Google Photos image classifier tagged photos of Black people as 'gorillas', a severe representational harm."),
    ("apple-card-2019", "Apple Card alleged gender bias in credit limits", 2019, "fairness-bias",
     "Users alleged the Apple Card/Goldman Sachs algorithm granted men far higher credit limits than women "
     "with similar profiles; prompted a regulator inquiry."),
    ("dutch-toeslagen-2021", "Dutch childcare benefits fraud algorithm scandal", 2021, "societal-harm",
     "A Dutch tax-authority risk-profiling algorithm wrongly accused tens of thousands of families "
     "(disproportionately dual-nationality) of benefits fraud; the government resigned."),
    ("air-canada-chatbot-2024", "Air Canada liable for chatbot misinformation", 2024, "accountability",
     "A tribunal held Air Canada liable for its support chatbot giving a passenger incorrect bereavement-fare "
     "refund advice — the company was responsible for its AI's statements."),
    ("mata-avianca-2023", "ChatGPT-fabricated legal citations (Mata v. Avianca)", 2023, "transparency",
     "Lawyers were sanctioned after submitting a brief citing non-existent cases hallucinated by ChatGPT, "
     "a landmark confabulation/accountability incident."),
    ("clearview-ai-2020", "Clearview AI facial-recognition scraping", 2020, "privacy",
     "Clearview AI scraped billions of facial images from the web without consent to build a face-search "
     "tool sold to law enforcement, drawing multiple privacy regulator fines/bans."),
    ("zillow-offers-2021", "Zillow Offers home-pricing algorithm losses", 2021, "safety",
     "Zillow shut down its algorithmic iBuying business after its home-price models mispriced purchases, "
     "leading to a ~$500M writedown and layoffs — an AI reliability/robustness failure."),
]


def build_ai_incidents(raw=None, today=None):
    today = today or _today()
    return [("ai-incident", "incident_id", {
        "incident_id": iid, "name": name, "year": year, "harm_category": cat, "description": desc,
        "references": ["https://incidentdatabase.ai/", "https://airisk.mit.edu/"],
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["ai-incidents"], "refresh_date": today},
    }) for iid, name, year, cat, desc in _AI_INCIDENTS]


# ── Framework crosswalk → framework-crosswalk ─────────────────────────────────────
# Curated cross-framework navigation: per governance/AI category, the corresponding
# EU AI Act articles, NIST AI RMF functions, NIST AI 600-1 risks, and OWASP/ATLAS anchors.
# Lets a consumer answer "which frameworks cover this risk area" across standards.
_CROSSWALK = [
    ("fairness-bias", "Fairness & Non-discrimination",
     {"eu_ai_act": ["eu-ai-act-art-10"], "nist_ai_rmf": ["GV.3", "MS.2", "MP.5"],
      "nist_ai_600_1": ["harmful-bias"], "owasp": []}),
    ("transparency", "Transparency & Explainability",
     {"eu_ai_act": ["eu-ai-act-art-13", "eu-ai-act-art-50", "eu-ai-act-art-11"],
      "nist_ai_rmf": ["MS.2", "GV.4"], "nist_ai_600_1": ["confabulation", "info-integrity"], "owasp": []}),
    ("accountability", "Accountability & Governance",
     {"eu_ai_act": ["eu-ai-act-art-14", "eu-ai-act-art-26", "eu-ai-act-art-72"],
      "nist_ai_rmf": ["GV.1", "GV.2", "MG.1"], "nist_ai_600_1": ["human-ai-config", "value-chain"], "owasp": []}),
    ("safety", "Safety & Robustness",
     {"eu_ai_act": ["eu-ai-act-art-9", "eu-ai-act-art-15", "eu-ai-act-art-55"],
      "nist_ai_rmf": ["MS.2", "MG.2", "MG.4"], "nist_ai_600_1": ["cbrn", "info-security"], "owasp": ["LLM01"]}),
    ("privacy", "Privacy & Data Protection",
     {"eu_ai_act": ["eu-ai-act-art-10"], "nist_ai_rmf": ["MP.4", "MS.2"],
      "nist_ai_600_1": ["data-privacy"], "owasp": ["LLM02"]}),
    ("societal-harm", "Societal & Human-Rights Harm",
     {"eu_ai_act": ["eu-ai-act-art-5"], "nist_ai_rmf": ["MP.5", "GV.5"],
      "nist_ai_600_1": ["dangerous-violent-hateful", "obscene-abusive"], "owasp": []}),
    ("environmental", "Environmental Impact",
     {"eu_ai_act": [], "nist_ai_rmf": ["MP.3", "MG.2"], "nist_ai_600_1": ["environmental"], "owasp": []}),
    ("ai-agent-compromise", "AI/Agentic Security Compromise",
     {"eu_ai_act": ["eu-ai-act-art-15"], "nist_ai_rmf": ["MS.2", "MG.4"],
      "nist_ai_600_1": ["info-security"], "owasp": ["LLM01", "ASI01"], "atlas": ["AML.T0051"]}),
]


def build_framework_crosswalk(raw=None, today=None):
    today = today or _today()
    return [("framework-crosswalk", "crosswalk_id", {
        "crosswalk_id": f"xwalk-{cat}", "category": cat, "name": name,
        "mappings": maps,
        "description": f"Cross-framework mapping for {name}: EU AI Act ↔ NIST AI RMF ↔ NIST AI 600-1 ↔ OWASP/ATLAS.",
        "provenance": {"ingested_by": "catalog-ingest", "sources": ["framework-crosswalk"], "refresh_date": today},
    }) for cat, name, maps in _CROSSWALK]


# ── Wiz Cloud VDB → vulnerability (cloud/CSP issues) ──────────────────────────────
def build_wiz(entries, today=None):
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


# ── NIST SP 800-53 Rev5 (OSCAL JSON) → security-control ───────────────────────────
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
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["nist-800-53"],
                           "refresh_date": today},
        }))
        for enh in control.get("controls", []) or []:
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


# ── ACSC ISM (Australian Government Information Security Manual, OSCAL JSON) → security-control ─
_E8_PROFILES = {
    "ML1": "https://cdn.jsdelivr.net/gh/AustralianCyberSecurityCentre/ism-oscal@main/ISM_E8_ML1-baseline_profile.json",
    "ML2": "https://cdn.jsdelivr.net/gh/AustralianCyberSecurityCentre/ism-oscal@main/ISM_E8_ML2-baseline_profile.json",
    "ML3": "https://cdn.jsdelivr.net/gh/AustralianCyberSecurityCentre/ism-oscal@main/ISM_E8_ML3-baseline_profile.json",
}


def _e8_maturity_map():
    """Map ISM control id (lowercase) -> Essential Eight maturity level, read from a single
    precomputed structured file in S3 (catalog-content/acsc-e8-maturity.json), derived once from
    the authoritative ASD E8 OSCAL profiles. Best-effort (returns {} on any failure)."""
    try:
        import os
        import json
        import boto3
        bucket = os.environ.get("CATALOG_CONTENT_BUCKET") or os.environ.get("ECOSYSTEMS_BUCKET")
        body = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1")).get_object(
            Bucket=bucket, Key="catalog-content/acsc-e8-maturity.json")["Body"].read()
        return json.loads(body).get("e8_maturity") or {}
    except Exception:
        return {}


def build_ism(data, today=None):
    today = today or _today()
    cat = data.get("catalog") or data
    e8 = _e8_maturity_map()
    out = []

    def emit(control, family):
        cid = (control.get("id") or "").upper()
        if not cid:
            return
        # prefer the numeric ISM control identifier if present in props
        ism_id = ""
        for p in control.get("props", []) or []:
            if (p.get("name") or "").lower() in ("label", "sort-id", "ism-identifier"):
                ism_id = p.get("value") or ism_id
        rec = {
            "control_id": cid, "name": control.get("title", ""), "framework": "acsc-ism",
            "family": family, "ism_identifier": ism_id, "description": _oscal_statement(control),
            "references": ["https://www.cyber.gov.au/ism"],
            "provenance": {"ingested_by": "catalog-ingest", "sources": ["acsc-ism"],
                           "refresh_date": today},
        }
        lvl = e8.get(cid.lower())
        if lvl:
            rec["essential_eight"] = True
            rec["e8_maturity"] = lvl
        out.append(("security-control", "control_id", rec))
        for enh in control.get("controls", []) or []:
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


# ── MITRE ATLAS (Adversarial Threat Landscape for AI Systems) → ai-attack-technique ──
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


# ── builder registry: name -> (callable, input_jmespath_or_None, needs_fetch) ─────
# input_key: JMESPath applied to the fetched raw to get the builder's argument
# (e.g. ATT&CK STIX json -> "objects"). None => pass the whole fetched object.
# needs_fetch=False => static/constant source (raw ignored; e.g. OWASP, NIST AI RMF).
BUILDERS = {
    "attack":      {"fn": build_attack_records, "input": "objects", "needs_fetch": True},
    "kev":         {"fn": build_kev_records,    "input": None,      "needs_fetch": True},
    "veris":       {"fn": build_veris,          "input": None,      "needs_fetch": True},
    "owasp":       {"fn": build_owasp,          "input": None,      "needs_fetch": False},
    "nist_ai_rmf": {"fn": build_nist,           "input": None,      "needs_fetch": False},
    "nist_ai_600_1": {"fn": build_gai_risk,     "input": None,      "needs_fetch": False},
    "eu_ai_act":     {"fn": build_eu_ai_act,     "input": None,      "needs_fetch": False},
    "nist_ai_rmf_playbook": {"fn": build_nist_ai_rmf_playbook, "input": None, "needs_fetch": False},
    "ai_incidents":  {"fn": build_ai_incidents,  "input": None,      "needs_fetch": False},
    "framework_crosswalk": {"fn": build_framework_crosswalk, "input": None, "needs_fetch": False},
    "wiz":         {"fn": build_wiz,            "input": "files",   "needs_fetch": True},
    "nist_800_53": {"fn": build_nist80053,      "input": None,      "needs_fetch": True},
    "ism": {"fn": build_ism,      "input": None,      "needs_fetch": True},
    "atlas":       {"fn": build_atlas,          "input": None,      "needs_fetch": True},
}
