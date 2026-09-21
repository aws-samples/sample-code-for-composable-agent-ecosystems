"""AI-governance layer: curated builders + governance synthesis clustering.

Verifies the EU AI Act / NIST AI RMF / AI-incident / crosswalk builders and that the synth
plugin clusters governance risks (fairness-bias, transparency, …) from GAI risks x obligations
x controls x incidents, with exact-id additive coverage.
"""
from tessera_platform.catalog import formats, synth_risk


def test_governance_builders_shape():
    exp = {
        "eu_ai_act": ("regulatory-obligation", "obligation_id"),
        "nist_ai_rmf_playbook": ("ai-governance-control", "control_id"),
        "ai_incidents": ("ai-incident", "incident_id"),
        "framework_crosswalk": ("framework-crosswalk", "crosswalk_id"),
    }
    for b, (etype, kf) in exp.items():
        recs = formats.BUILDERS[b]["fn"](today="2026-07-08")
        assert recs, b
        assert {t for t, _, _ in recs} == {etype}
        assert {k for _, k, _ in recs} == {kf}
        for _t, _k, r in recs:
            assert r.get(kf) and r.get("provenance", {}).get("sources")
    # spot-checks
    eu = {r["obligation_id"]: r for _t, _k, r in formats.BUILDERS["eu_ai_act"]["fn"]()}
    assert "eu-ai-act-art-5" in eu and eu["eu-ai-act-art-5"]["tier"] == "prohibited"
    rmf = {r["control_id"] for _t, _k, r in formats.BUILDERS["nist_ai_rmf_playbook"]["fn"]()}
    assert {"GV.1", "MP.1", "MS.2", "MG.1"} <= rmf


GAI = [
    {"gai_risk_id": "harmful-bias", "name": "Harmful Bias or Homogenization", "description": "bias"},
    {"gai_risk_id": "data-privacy", "name": "Data Privacy", "description": "privacy"},
    {"gai_risk_id": "confabulation", "name": "Confabulation", "description": "hallucination"},
    {"gai_risk_id": "environmental", "name": "Environmental Impacts", "description": "energy"},
]
OBL = [{"obligation_id": "eu-ai-act-art-10", "article": "Article 10", "title": "Data governance", "category": "fairness-bias", "tier": "high-risk"}]
CTRL = [{"control_id": "GV.1", "name": "GV.1 (GOVERN)", "description": "policies"}]
INC = [{"incident_id": "compas-2016", "name": "COMPAS bias", "year": 2016, "harm_category": "fairness-bias", "description": "bias"}]


def test_cluster_governance_and_slots():
    clusters = synth_risk.cluster_governance(GAI, OBL, CTRL, INC)
    cats = {c["category"] for c in clusters}
    assert cats == {"fairness-bias", "privacy", "transparency", "environmental"}
    fb = next(c for c in clusters if c["category"] == "fairness-bias")
    assert fb["kind"] == "governance" and fb["obligations"] and fb["incident_exemplars"] and fb["controls"]
    slots = synth_risk.enumerate_slots(fb)
    assert slots and slots[0]["slot_id"] == "RSK-fairness-bias-harmful-bias" and slots[0]["kind"] == "governance"


def test_governance_additive_coverage_exact_id():
    clusters = synth_risk.cluster_governance(GAI, OBL, CTRL, INC)
    covered, counts, ids = synth_risk.covered_cells([], {})
    plan = synth_risk.plan_slots(clusters, covered, counts, ids=ids)
    run_ids = {s["slot_id"] for e in plan["to_run"] for s in e["slots"]}
    assert "RSK-fairness-bias-harmful-bias" in run_ids and "RSK-privacy-data-privacy" in run_ids
    # once a governance risk exists (exact id), it's covered -> not re-run
    covered2, counts2, ids2 = synth_risk.covered_cells([{"risk_id": "RSK-fairness-bias-harmful-bias", "category": "fairness-bias"}], {})
    plan2 = synth_risk.plan_slots(clusters, covered2, counts2, ids=ids2)
    run_ids2 = {s["slot_id"] for e in plan2["to_run"] for s in e["slots"]}
    assert "RSK-fairness-bias-harmful-bias" not in run_ids2 and "RSK-privacy-data-privacy" in run_ids2
