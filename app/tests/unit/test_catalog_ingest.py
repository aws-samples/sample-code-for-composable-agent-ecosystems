"""Unit tests for the catalog-ingest pure builders (no network, no boto3)."""
import importlib.util, os

_HERE = os.path.dirname(__file__)
_HANDLER = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", "catalog-ingest", "handler.py"))


def _mod():
    spec = importlib.util.spec_from_file_location("catalog_ingest", _HANDLER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_build_attack_records_techniques_controls_and_xrefs():
    m = _mod()
    objects = [
        {"type": "attack-pattern", "id": "attack-pattern--1", "name": "Phishing",
         "external_references": [{"source_name": "mitre-attack", "external_id": "T1566",
                                  "url": "https://attack.mitre.org/techniques/T1566"}],
         "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "initial-access"}],
         "x_mitre_platforms": ["Windows"], "description": "d"},
        {"type": "course-of-action", "id": "course-of-action--1", "name": "User Training",
         "external_references": [{"source_name": "mitre-attack", "external_id": "M1017",
                                  "url": "https://attack.mitre.org/mitigations/M1017"}],
         "description": "train users"},
        {"type": "relationship", "relationship_type": "mitigates",
         "source_ref": "course-of-action--1", "target_ref": "attack-pattern--1"},
        # revoked technique must be skipped
        {"type": "attack-pattern", "id": "attack-pattern--2", "revoked": True,
         "external_references": [{"source_name": "mitre-attack", "external_id": "T9999"}]},
    ]
    recs = m.build_attack_records(objects, today="2026-07-01")
    techs = {r[2]["technique_id"]: r[2] for r in recs if r[0] == "attack-technique"}
    ctrls = {r[2]["control_id"]: r[2] for r in recs if r[0] == "security-control"}
    assert set(techs) == {"T1566"}                      # revoked T9999 dropped
    assert techs["T1566"]["mitigations"] == ["M1017"]   # xref technique->control
    assert techs["T1566"]["tactics"] == ["initial-access"]
    assert set(ctrls) == {"M1017"}
    assert ctrls["M1017"]["mitigates"] == ["T1566"]     # xref control->technique
    assert ctrls["M1017"]["framework"] == "mitre-attack-mitigation"


def test_build_attack_records_tech_limit_caps_techniques_only():
    m = _mod()
    objects = [{"type": "attack-pattern", "id": f"attack-pattern--{i}", "name": f"T{i}",
                "external_references": [{"source_name": "mitre-attack", "external_id": f"T100{i}"}]}
               for i in range(5)]
    objects.append({"type": "course-of-action", "id": "course-of-action--1", "name": "C",
                    "external_references": [{"source_name": "mitre-attack", "external_id": "M0001"}]})
    recs = m.build_attack_records(objects, today="2026-07-01", tech_limit=2)
    assert sum(1 for r in recs if r[0] == "attack-technique") == 2
    assert sum(1 for r in recs if r[0] == "security-control") == 1  # controls not capped


def test_build_kev_records_shapes_vulnerability_and_ransomware_flag():
    m = _mod()
    kev = {"vulnerabilities": [
        {"cveID": "CVE-2021-44228", "vulnerabilityName": "Log4Shell", "vendorProject": "Apache",
         "product": "Log4j", "shortDescription": "RCE", "dateAdded": "2021-12-10",
         "knownRansomwareCampaignUse": "Known"},
        {"cveID": "CVE-2020-0001", "vulnerabilityName": "X", "knownRansomwareCampaignUse": "Unknown"},
        {"vulnerabilityName": "no-cve"},  # skipped (no cveID)
    ]}
    recs = m.build_kev_records(kev, today="2026-07-01")
    vulns = {r[2]["cve_or_ref"]: r[2] for r in recs}
    assert set(vulns) == {"CVE-2021-44228", "CVE-2020-0001"}
    assert vulns["CVE-2021-44228"]["known_ransomware"] is True
    assert vulns["CVE-2020-0001"]["known_ransomware"] is False
    assert vulns["CVE-2021-44228"]["references"] == ["https://nvd.nist.gov/vuln/detail/CVE-2021-44228"]


def test_build_kev_records_limit():
    m = _mod()
    kev = {"vulnerabilities": [{"cveID": f"CVE-2021-{i:04d}"} for i in range(10)]}
    assert len(m.build_kev_records(kev, limit=3)) == 3


def test_build_owasp_llm_and_agentic():
    m = _mod()
    recs = m.build_owasp(today="2026-07-02")
    ids = [r[2]["threat_id"] for r in recs]
    assert "LLM01" in ids and "ASI01" in ids and len(recs) == 20
    assert all(r[0] == "agentic-threat" for r in recs)
    llm01 = next(r[2] for r in recs if r[2]["threat_id"] == "LLM01")
    assert llm01["framework"] == "owasp-llm-top10-2025" and "Prompt Injection" in llm01["name"]


def test_build_nist_ai_rmf():
    m = _mod()
    recs = m.build_nist(today="2026-07-02")
    assert all(r[0] == "risk-method-profile" for r in recs)
    assert any(r[2]["risk_type"] == "ai-secure-resilient" for r in recs)
    assert recs[0][2]["framework"] == "nist-ai-rmf"


def test_build_veris_breach_shaping_and_cap():
    m = _mod()
    incidents = [
        {"plus": {"master_id": "ABC123"}, "victim": {"victim_id": "AcmeCorp", "industry": "52", "country": ["US"]},
         "timeline": {"incident": {"year": 2024}}, "actor": {"external": {}}, "action": {"hacking": {}},
         "attribute": {"confidentiality": {}}, "summary": "Breach via phishing."},
        {"plus": {"master_id": "OLD"}, "victim": {"victim_id": "OldCo"}, "timeline": {"incident": {"year": 2001}}},
    ]
    recs = m.build_veris(incidents, today="2026-07-02", limit=1)
    assert len(recs) == 1  # cap + most-recent-first -> 2024 kept
    r = recs[0][2]
    assert recs[0][0] == "breach" and r["year"] == 2024 and r["org"] == "AcmeCorp"
    assert r["actors"] == ["external"] and r["actions"] == ["hacking"] and r["attributes"] == ["confidentiality"]
    assert r["breach_key"].startswith("acmecorp-2024-")


def test_build_wiz_cloud_vuln():
    m = _mod()
    entries = [
        {"title": "S3 bucket takeover", "slug": "s3-takeover", "cves": [], "affectedPlatforms": ["AWS"],
         "affectedServices": ["S3"], "severity": "high", "summary": "Dangling bucket reference.",
         "knownITWExploitation": True, "disclosedAt": "2023/01/01", "references": ["https://x"]},
        {"title": "No ref", "slug": "", "cves": ["CVE-2024-9"], "summary": "x"},
        "not-a-dict",
    ]
    recs = m.build_wiz(entries, today="2026-07-02")
    keys = [r[2]["cve_or_ref"] for r in recs]
    assert keys == ["s3-takeover", "CVE-2024-9"]  # slug used when no CVE; CVE used otherwise; junk skipped
    r0 = recs[0][2]
    assert recs[0][0] == "vulnerability" and r0["cloud_vuln"] is True and r0["known_exploited"] is True
    assert r0["provenance"]["sources"] == ["wiz-cvdb"]


def test_build_nist80053_oscal_controls_and_enhancements():
    m = _mod()
    data = {"catalog": {"groups": [
        {"id": "ac", "title": "Access Control", "controls": [
            {"id": "ac-1", "title": "Policy and Procedures",
             "parts": [{"name": "statement", "prose": "Develop, document...", "parts": [{"prose": "Review annually."}]}],
             "controls": [{"id": "ac-2.1", "title": "Automated Account Management",
                           "parts": [{"name": "statement", "prose": "Support automation."}]}]},
        ]},
    ]}}
    recs = m.build_nist80053(data, today="2026-07-02")
    by = {r[2]["control_id"]: r[2] for r in recs}
    assert set(by) == {"AC-1", "AC-2.1"}                       # control + enhancement
    assert all(r[0] == "security-control" for r in recs)
    assert by["AC-1"]["family"] == "Access Control" and by["AC-1"]["framework"] == "nist-800-53-rev5"
    assert "Develop, document" in by["AC-1"]["description"] and "Review annually" in by["AC-1"]["description"]
    assert by["AC-2.1"]["provenance"]["sources"] == ["nist-800-53"]


def test_content_hash_ignores_volatile_provenance():
    m = _mod()
    a = {"control_id": "AC-1", "name": "Policy", "provenance": {"sources": ["nist-800-53"], "refresh_date": "2026-07-01"}}
    b = {"control_id": "AC-1", "name": "Policy", "provenance": {"sources": ["nist-800-53"], "refresh_date": "2026-07-02"}}
    c = {"control_id": "AC-1", "name": "Policy CHANGED", "provenance": {"sources": ["nist-800-53"], "refresh_date": "2026-07-02"}}
    assert m._content_hash(a) == m._content_hash(b)      # only refresh_date differs -> same hash
    assert m._content_hash(a) != m._content_hash(c)      # meaningful change -> different hash
    # a stored content_hash field must not affect the hash (backfill-free)
    d = dict(a); d["content_hash"] = "deadbeef"
    assert m._content_hash(d) == m._content_hash(a)


def test_diff_plan_create_update_skip():
    m = _mod()
    existing = {"ac-1": "h1", "ac-2": "h2", "ac-3": "h3"}
    incoming = {"ac-1": "h1", "ac-2": "hX", "ac-4": "h4"}  # ac-1 same, ac-2 changed, ac-4 new, ac-3 gone
    p = m._diff_plan(existing, incoming)
    assert p["skip"] == ["ac-1"]
    assert p["update"] == ["ac-2"]
    assert p["create"] == ["ac-4"]


def _synth_plan():
    import importlib.util, os
    p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lambdas", "catalog-ingest", "synth_plan.py"))
    spec = importlib.util.spec_from_file_location("synth_plan", p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_cluster_and_digest_stability():
    sp = _synth_plan()
    techs = [{"technique_id": "T1078", "name": "Valid Accounts", "tactics": ["defense-evasion"], "mitigations": ["M1"]},
             {"technique_id": "T1110", "name": "Brute Force", "tactics": ["credential-access"], "mitigations": []}]
    ctrls = [{"control_id": "M1", "name": "MFA", "description": "use mfa"}]
    cs = sp.cluster(techs, ctrls, [])
    ids = {c["cluster_id"] for c in cs}
    assert ids == {"cluster-defense-evasion", "cluster-credential-access"}
    # digest is stable across identical rebuilds, changes when content changes
    d1 = sp.cluster_digest(cs[0])
    assert d1 == sp.cluster_digest(sp.cluster(techs, ctrls, [])[0])
    techs2 = [dict(techs[0], description="now with detail"), techs[1]]
    de = sp.cluster_digest([c for c in sp.cluster(techs2, ctrls, []) if c["tactic"] == "defense-evasion"][0])
    assert de != d1


def test_plan_skips_unchanged_runs_changed_and_missing():
    sp = _synth_plan()
    clusters = [{"cluster_id": "cluster-a", "technique_ids": ["T1"], "control_ids": [], "techniques": [], "controls": [], "exploited_context": []},
                {"cluster_id": "cluster-b", "technique_ids": ["T2"], "control_ids": [], "techniques": [], "controls": [], "exploited_context": []},
                {"cluster_id": "cluster-c", "technique_ids": ["T3"], "control_ids": [], "techniques": [], "controls": [], "exploited_context": []}]
    da = sp.cluster_digest(clusters[0])
    checkpoints = {
        "cluster-a": {"input_digest": da, "output_digest": "x"},   # unchanged + has output -> skip
        "cluster-b": {"input_digest": "STALE", "output_digest": "x"},  # digest changed -> run
        # cluster-c: no checkpoint -> run
    }
    p = sp.plan(clusters, checkpoints)
    assert [e["cluster_id"] for e in p["skip"]] == ["cluster-a"]
    assert sorted(e["cluster_id"] for e in p["to_run"]) == ["cluster-b", "cluster-c"]


def test_build_atlas_ai_techniques():
    m = _mod()
    data = {"matrices": [{"id": "ATLAS",
        "tactics": [{"id": "AML.TA0002", "name": "Reconnaissance"}],
        "techniques": [
            {"id": "AML.T0043", "name": "Craft Adversarial Data", "description": "Adversary crafts inputs.", "tactics": ["AML.TA0002"]},
            {"id": "AML.T0043.000", "name": "White-Box", "description": "full access", "tactics": ["AML.TA0002"], "subtechnique-of": "AML.T0043"},
            {"name": "no id skipped"},
        ]}]}
    recs = m.build_atlas(data, today="2026-07-02")
    keys = [r[2]["technique_id"] for r in recs]
    assert keys == ["AML.T0043", "AML.T0043.000"]
    assert recs[0][0] == "ai-attack-technique"
    assert recs[0][2]["tactics"] == ["Reconnaissance"] and recs[0][2]["framework"] == "mitre-atlas"
    assert recs[1][2]["subtechnique_of"] == "AML.T0043"


def test_cluster_agentic_by_owasp_category():
    sp = _synth_plan()
    threats = [
        {"threat_id": "LLM01", "name": "Prompt Injection", "category": "llm-application", "framework": "owasp-llm-top10-2025"},
        {"threat_id": "LLM02", "name": "Sensitive Info Disclosure", "category": "llm-application", "framework": "owasp-llm-top10-2025"},
        {"threat_id": "ASI01", "name": "Memory Poisoning", "category": "agentic", "framework": "owasp-agentic"},
    ]
    gai = [{"gai_risk_id": "data-privacy", "name": "Data Privacy"}]
    cs = sp.cluster_agentic(threats, gai)
    ids = {c["cluster_id"] for c in cs}
    assert ids == {"cluster-ai-llm-application", "cluster-ai-agentic"}
    assert all(c["kind"] == "ai" for c in cs)
    llm = [c for c in cs if c["owasp_category"] == "llm-application"][0]
    assert llm["size"] == 2 and llm["gai_risk_catalog"] == [{"gai_risk_id": "data-privacy", "name": "Data Privacy"}]
    # digest changes if a threat's content changes
    d1 = sp.cluster_digest(llm)
    threats2 = [dict(threats[0], description="now detailed"), threats[1], threats[2]]
    llm2 = [c for c in sp.cluster_agentic(threats2, gai) if c["owasp_category"] == "llm-application"][0]
    assert sp.cluster_digest(llm2) != d1


def test_cluster_scenarios_guarantees_business_categories():
    sp = _synth_plan()
    techs = [
        {"technique_id": "T1195.002", "name": "Compromise Software Supply Chain", "tactics": ["initial-access"], "description": "supply chain", "mitigations": ["M1051"]},
        {"technique_id": "T1114", "name": "Email Collection", "tactics": ["collection"], "description": "insider reads email", "mitigations": ["M1041"]},
        {"technique_id": "T1491", "name": "Defacement", "tactics": ["impact"], "description": "public defacement reputation", "mitigations": []},
        {"technique_id": "T1213", "name": "Data from Info Repositories", "tactics": ["collection"], "description": "PII customer data GDPR", "mitigations": ["M1047"]},
    ]
    ctrls = [{"control_id": "M1051", "name": "Update Software", "description": "patch"}, {"control_id": "M1041", "name": "Encrypt Sensitive Info", "description": "enc"}]
    breaches = [{"org": "Acme", "year": 2024, "industry": "Health", "summary": "internal employee misuse of medical records", "actors": ["internal"]}]
    cs = sp.cluster_scenarios(techs, ctrls, [], breaches)
    cats = {c["category"] for c in cs}
    assert cats == {"insider-threat", "supply-chain", "regulatory-compliance", "reputational"}
    assert all(c["kind"] == "scenario" for c in cs)
    sc = {c["category"]: c for c in cs}
    assert "T1195.002" in sc["supply-chain"]["technique_ids"]
    assert "T1491" in sc["reputational"]["technique_ids"]
    # digest stable + changes with content
    d = sp.cluster_digest(sc["insider-threat"])
    assert d == sp.cluster_digest(sp.cluster_scenarios(techs, ctrls, [], breaches)[[c["category"] for c in cs].index("insider-threat")])


def test_enumerate_slots_are_deterministic_and_standards_anchored():
    sp = _synth_plan()
    tactic_cluster = {"cluster_id": "cluster-impact", "tactic": "impact", "techniques": [
        {"technique_id": "T1486", "name": "Data Encrypted for Impact", "description": "ransomware encrypts data"},
        {"technique_id": "T1491", "name": "Defacement", "description": "public defacement and leak"},
    ]}
    slots = sp.enumerate_slots(tactic_cluster)
    ids = {s["slot_id"] for s in slots}
    cats = {s["category"] for s in slots}
    assert "RSK-impact-ransomware" in ids and "ransomware" in cats
    assert "reputational" in cats                       # defacement/leak matcher
    assert sp.enumerate_slots(tactic_cluster) == slots  # deterministic
    ai = {"cluster_id": "cluster-ai-agentic", "kind": "ai", "threats": [
        {"threat_id": "ASI06", "name": "Excessive Agency"}]}
    aslots = sp.enumerate_slots(ai)
    assert aslots[0]["slot_id"] == "RSK-AI-asi06" and aslots[0]["category"] == "ai-agent-compromise"


def test_covered_cells_and_plan_slots_additive_topup():
    sp = _synth_plan()
    tech_tactics = {"T1486": ["impact"]}
    existing = [{"category": "ransomware", "sources": {"attack_techniques": ["T1486"]}},
                {"category": "ai-agent-compromise"}]
    covered, counts, _ids = sp.covered_cells(existing, tech_tactics)
    assert ("impact", "ransomware") in covered
    assert counts["ransomware"] == 1
    cluster = {"cluster_id": "cluster-impact", "tactic": "impact", "techniques": [
        {"technique_id": "T1486", "name": "Data Encrypted for Impact", "description": "ransomware encrypts data"},
        {"technique_id": "T1485", "name": "Data Destruction", "description": "destroy wipe availability"},
    ]}
    plan = sp.plan_slots([cluster], covered, counts)
    run_ids = {s["slot_id"] for e in plan["to_run"] for s in e["slots"]}
    assert "RSK-impact-ransomware" not in run_ids            # already covered
    assert "RSK-impact-service-disruption" in run_ids        # T1485 -> missing cell
    ai = {"cluster_id": "cluster-ai-agentic", "kind": "ai", "threats": [{"threat_id": "ASI06", "name": "x"}]}
    assert sp.plan_slots([ai], covered, counts)["to_run"] == []   # AI covered by category
