"""Unit tests for the Ecosystem Evaluation Harness core (spec §3-§10).

Covers: config parse/validate (§3), deterministic scorers (§4), noise-band
aggregation + per-issue miss-rate (§6.2), scorecard + deploy gate (§6.3), eval
state machine compilation (§5.1), independent judge selection (§7.2), online
sampling/risk-trigger (§7.4), and the builder proposal (§8). Eco-AGNOSTIC: the
domain vocabulary here is TEST FIXTURE data, never platform code.
"""
import json
import pytest

from tessera_platform.evaluation import (
    parse_evaluation_block, validate_evaluation_block, EvaluationConfigError,
    score_metric, aggregate_metric, per_issue_miss_rate, clears_band,
    build_scorecard, apply_deploy_gate,
)
from tessera_platform.evaluation.config import MetricBinding
from tessera_platform.evaluation.eval_compiler import compile_eval_state_machine
from tessera_platform.evaluation.judge import (
    select_independent_judge, ecosystem_agent_models, JudgeSelectionError,
    build_byoi_dataset, build_custom_metric_defs,
)
from tessera_platform.evaluation.online import should_evaluate_run, build_golden_candidate
from tessera_platform.evaluation.propose import propose_evaluation, compile_approved_proposal
from tessera_platform.evaluation.runner import (
    load_golden_cases, expand_work_items, score_deterministic, assemble_scorecard,
    config_from_dict,
)
from tessera_platform.evaluation.trajectory import extract_trajectory, score_trajectory_heuristic


# ── §3 config ────────────────────────────────────────────────────────────────

def _eco(evaluation=None, agents=None):
    c = {"ecosystem": {"name": "t", "inputs": [{"id": "x"}]},
         "agents": agents or [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]}
    if evaluation is not None:
        c["evaluation"] = evaluation
    return c


def test_no_evaluation_block_returns_none():
    assert parse_evaluation_block(_eco()) is None


def test_full_block_parses():
    ev = parse_evaluation_block(_eco({
        "golden_set": "s3://b/evaluations/t/v1/cases.jsonl",
        "replicates": 5, "run_mode": "headless", "trajectory": True,
        "metrics": [
            {"id": "disp", "field": "disposition", "scorer": "categorical",
             "cost_weights": {"false_negative": 5, "false_positive": 1}},
            {"id": "amt", "field": "reimburse_amount", "scorer": "numeric_tolerance", "tol": 0},
            {"id": "reasons", "field": "decline_reasons", "scorer": "set_recall"},
            {"id": "faithful", "field": "*", "scorer": "citation_resolves"},
            {"id": "abstain", "field": "disposition", "scorer": "abstention", "expected_value": "REFER"},
            {"id": "sar", "scorer": "bedrock_judge", "builtin": ["faithfulness", "completeness"]},
            {"id": "reg", "scorer": "bedrock_judge", "custom_rubric": "is it regulator-ready?"},
        ],
        "thresholds": {"disp": 0.95, "faithful": 1.0, "sar": 0.9},
        "gate": "block",
        "online": {"enabled": True, "baseline_sample_rate": 0.1,
                   "always_eval_when": ["gate_fired", {"faithfulness_below": 0.8}]},
    }))
    assert ev.replicates == 5 and ev.gate == "block" and ev.trajectory
    assert len(ev.metrics) == 7
    assert ev.metric("disp").threshold == 0.95
    assert ev.metric("faithful").reference_free       # citation_resolves is RF
    assert ev.metric("sar").reference_free             # builtin all RF
    assert not ev.metric("disp").reference_free        # deterministic → not RF
    assert ev.online.enabled and ev.online.baseline_sample_rate == 0.1


@pytest.mark.parametrize("bad,msg", [
    ({"metrics": [{"id": "x", "scorer": "nope"}]}, "not in"),
    ({"metrics": [{"scorer": "categorical", "field": "f"}]}, "id is required"),
    ({"metrics": [{"id": "x", "scorer": "categorical"}]}, "requires a `field`"),
    ({"metrics": [{"id": "x", "scorer": "abstention", "field": "f"}]}, "requires `expected_value`"),
    ({"gate": "banana"}, "gate must be one of"),
    ({"replicates": 0}, "replicates must be an integer >= 1"),
    ({"metrics": [{"id": "d", "scorer": "categorical", "field": "f"}], "thresholds": {"zzz": 0.5}}, "unknown metric"),
    ({"metrics": [{"id": "d", "scorer": "bedrock_judge", "builtin": ["not_a_metric"]}]}, "not in"),
])
def test_malformed_block_fails_loudly(bad, msg):
    with pytest.raises(EvaluationConfigError) as e:
        parse_evaluation_block(_eco(bad))
    assert msg in str(e.value)


def test_online_without_reference_free_fails():
    with pytest.raises(EvaluationConfigError):
        parse_evaluation_block(_eco({
            "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
            "online": {"enabled": True, "baseline_sample_rate": 0.5},
        }))


def test_validate_warnings():
    w = validate_evaluation_block(_eco({
        "gate": "block", "replicates": 1,
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
    }))
    kinds = {x["kind"] for x in w}
    assert "eval_block_gate_without_golden_set" in kinds
    assert "eval_no_thresholds" in kinds
    assert "eval_low_replicates_for_block" in kinds


# ── §4 deterministic scorers ───────────────────────────────────────────────────

def test_categorical():
    mb = MetricBinding(id="d", scorer="categorical", field="disposition")
    assert score_metric(mb, {"disposition": "ESCALATE"}, {"disposition": "escalate"}).hit
    assert not score_metric(mb, {"disposition": "APPROVE"}, {"disposition": "ESCALATE"}).hit


def test_numeric_tolerance():
    mb = MetricBinding(id="a", scorer="numeric_tolerance", field="amt", tol=100)
    assert score_metric(mb, {"amt": 85050}, {"amt": 85000}).hit
    assert not score_metric(mb, {"amt": 90000}, {"amt": 85000}).hit
    assert not score_metric(mb, {"amt": "n/a"}, {"amt": 85000}).hit   # non-numeric → miss


def test_set_recall_any_of():
    mb = MetricBinding(id="r", scorer="set_recall", field="reasons")
    expected = {"reasons": [{"issue": "fraud", "any_of": ["fraud", "abuse"]}, "limit"]}
    full = score_metric(mb, {"reasons": ["abuse detected", "over the limit"]}, expected)
    assert full.hit and full.value == 1.0
    partial = score_metric(mb, {"reasons": ["over the limit"]}, expected)
    assert not partial.hit and partial.value == 0.5
    assert "fraud" in partial.detail["missed"]


def test_citation_resolves():
    mb = MetricBinding(id="f", scorer="citation_resolves", field="*")
    ok = score_metric(mb, {"citations": [{"source": "doc1"}, {"source": "doc2"}],
                           "evidence": ["doc1", "doc2", "doc3"]}, {})
    assert ok.hit and ok.value == 1.0
    bad = score_metric(mb, {"citations": [{"source": "ghost"}], "evidence": ["doc1"]}, {})
    assert not bad.hit and "ghost" in bad.detail["unresolved"]


def test_abstention():
    mb = MetricBinding(id="ab", scorer="abstention", field="disposition", expected_value="REFER")
    # should abstain and did
    assert score_metric(mb, {"disposition": "REFER"}, {"should_abstain": True}).hit
    # should abstain but decided
    assert not score_metric(mb, {"disposition": "APPROVE"}, {"should_abstain": True}).hit
    # should NOT abstain and didn't
    assert score_metric(mb, {"disposition": "APPROVE"}, {"should_abstain": False}).hit
    # should NOT abstain but did → miss (over-abstention)
    assert not score_metric(mb, {"disposition": "REFER"}, {"should_abstain": False}).hit


def test_judge_metric_not_scored_here():
    mb = MetricBinding(id="j", scorer="bedrock_judge", builtin=["faithfulness"])
    with pytest.raises(ValueError, match="judge-delegated"):
        score_metric(mb, {}, {})


# ── §6.2 aggregation + noise bands ─────────────────────────────────────────────

def test_aggregate_band_never_tighter_than_default():
    agg = aggregate_metric("d", "categorical", [1.0, 1.0, 1.0], threshold=0.95)
    assert agg.band >= 0.02 and agg.median == 1.0 and agg.passes()


def test_aggregate_empirical_band_widens():
    agg = aggregate_metric("r", "set_recall", [0.5, 1.0, 0.75])
    assert agg.empirical_band == pytest.approx(0.25)
    assert agg.band == pytest.approx(0.25)   # wider than default 0.08


def test_passes_within_noise():
    # median just below threshold but inside band → passes (spec §6.2)
    agg = aggregate_metric("d", "categorical", [0.94, 0.94, 0.94], threshold=0.95)
    # band 0.02 → 0.94+0.02=0.96 >= 0.95 → passes
    assert agg.passes()


def test_clears_band():
    assert clears_band(0.5, 0.9, 0.15)
    assert not clears_band(0.85, 0.9, 0.15)


def test_per_issue_miss_rate():
    r = per_issue_miss_rate({"caseA": [True, True, False], "caseB": [False, False, False]})
    assert r["caseA"]["miss_rate"] == pytest.approx(1/3, abs=1e-3)
    assert r["caseB"]["display"] == "3/3"


# ── §6.3 scorecard + deploy gate ───────────────────────────────────────────────

def _sc(gate, aggs, case_hits=None):
    return build_scorecard(ecosystem_id="t", ecosystem_version="v1", config_hash="h",
                           golden_set_version="g1", judge_model="us.anthropic.claude-opus-4-7",
                           gate=gate, aggregated=aggs, case_hits=case_hits)


def test_gate_block_refuses_on_failure():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
        "thresholds": {"d": 0.95}, "gate": "block"}))
    agg = aggregate_metric("d", "categorical", [0.5, 0.5, 0.5], threshold=0.95)
    out = apply_deploy_gate(ev, _sc("block", [agg]))
    assert not out.allow_deploy and out.failures


def test_gate_warn_allows_but_flags():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
        "thresholds": {"d": 0.95}, "gate": "warn"}))
    agg = aggregate_metric("d", "categorical", [0.5, 0.5, 0.5], threshold=0.95)
    out = apply_deploy_gate(ev, _sc("warn", [agg]))
    assert out.allow_deploy and out.failures


def test_gate_off_allows():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
        "thresholds": {"d": 0.95}, "gate": "off"}))
    agg = aggregate_metric("d", "categorical", [0.0], threshold=0.95)
    out = apply_deploy_gate(ev, _sc("off", [agg]))
    assert out.allow_deploy


def test_gate_regression_vs_baseline():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
        "thresholds": {"d": 0.5}, "gate": "block"}))
    cur = aggregate_metric("d", "categorical", [0.6, 0.6, 0.6], threshold=0.5)   # passes threshold
    base = aggregate_metric("d", "categorical", [0.95, 0.95, 0.95], threshold=0.5)
    base_sc = _sc("block", [base])
    out = apply_deploy_gate(ev, _sc("block", [cur]), baseline_scorecard=base_sc)
    # dropped 0.35, band 0.02 → clears band → regression → block refuses
    assert not out.allow_deploy and out.regressions


# ── §5.1 eval state machine compilation ─────────────────────────────────────────

def test_compile_eval_sm_shapes():
    ev = parse_evaluation_block(_eco({
        "golden_set": "s3://b/e/t/v1/cases.jsonl", "replicates": 3, "trajectory": True,
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"},
                    {"id": "j", "scorer": "bedrock_judge", "builtin": ["faithfulness"]}]}))
    asl = compile_eval_state_machine(_eco(), ev, "t")
    assert asl["StartAt"] == "LoadGoldenSet"
    s = asl["States"]
    assert s["RunGoldenCases"]["Type"] == "Map"
    # headless run mode injected into the target run SM (§5.2)
    it = s["RunGoldenCases"]["Iterator"]["States"]["RunCase"]
    assert it["Parameters"]["Input"]["run_mode"] == "headless"
    # judge + trajectory present, chained to Aggregate
    assert "Judge" in s and "Trajectory" in s and s["Aggregate"]["End"] is True
    assert s["ScoreDeterministic"]["Next"] == "Judge"


def test_compile_eval_sm_no_judge_no_trajectory():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "d", "scorer": "categorical", "field": "f"}]}))
    asl = compile_eval_state_machine(_eco(), ev, "t")
    assert asl["States"]["ScoreDeterministic"]["Next"] == "Aggregate"
    assert "Judge" not in asl["States"] and "Trajectory" not in asl["States"]


# ── §7.2 independent judge ──────────────────────────────────────────────────────

def test_agent_models_include_tier_defaults():
    models = ecosystem_agent_models(_eco())   # a (leaf), b (synth) → sonnet + opus
    assert "us.anthropic.claude-sonnet-4-5-20250929-v1:0" in models
    assert "us.anthropic.claude-opus-4-7" in models


def test_independent_judge_avoids_agent_models():
    ev = parse_evaluation_block(_eco({"metrics": [{"id": "j", "scorer": "bedrock_judge", "builtin": ["faithfulness"]}]}))
    judge = select_independent_judge(_eco(), ev)
    assert judge not in ecosystem_agent_models(_eco())


def test_pinned_non_independent_judge_fails():
    ev = parse_evaluation_block(_eco({
        "judge_model": "opus",   # opus is the synthesizer default → not independent
        "metrics": [{"id": "j", "scorer": "bedrock_judge", "builtin": ["faithfulness"]}]}))
    with pytest.raises(JudgeSelectionError):
        select_independent_judge(_eco(), ev)


def test_byoi_dataset_and_custom_metrics():
    ev = parse_evaluation_block(_eco({"metrics": [
        {"id": "reg", "scorer": "bedrock_judge", "custom_rubric": "regulator ready?"}]}))
    ds = build_byoi_dataset(ev, [{"name": "c1", "inputs": {"x": 1}, "expected": {"y": 2}}],
                            {"c1": [{"y": 2}, {"y": 3}]})
    assert len(ds) == 2 and ds[0]["case"] == "c1"
    defs = build_custom_metric_defs(ev)
    assert defs[0]["instructions"] == "regulator ready?"


# ── §7.4 online ─────────────────────────────────────────────────────────────────

def test_online_disabled():
    ev = parse_evaluation_block(_eco({"metrics": [{"id": "f", "field": "*", "scorer": "citation_resolves"}]}))
    assert not should_evaluate_run(ev, "s1").evaluate


def test_online_risk_trigger_forces_full():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "f", "field": "*", "scorer": "citation_resolves"}],
        "online": {"enabled": True, "baseline_sample_rate": 0.0,
                   "always_eval_when": ["gate_fired", {"faithfulness_below": 0.8}]}}))
    d = should_evaluate_run(ev, "s1", {"gate_fired": True})
    assert d.evaluate and d.force_full and d.reason == "risk:gate_fired"
    d2 = should_evaluate_run(ev, "s2", {"faithfulness": 0.5})
    assert d2.evaluate and d2.reason == "risk:faithfulness_below"


def test_online_baseline_sampling_stable():
    ev = parse_evaluation_block(_eco({
        "metrics": [{"id": "f", "field": "*", "scorer": "citation_resolves"}],
        "online": {"enabled": True, "baseline_sample_rate": 1.0}}))
    assert should_evaluate_run(ev, "any", {}).evaluate
    ev0 = parse_evaluation_block(_eco({
        "metrics": [{"id": "f", "field": "*", "scorer": "citation_resolves"}],
        "online": {"enabled": True, "baseline_sample_rate": 0.0}}))
    assert not should_evaluate_run(ev0, "any", {}).evaluate


def test_golden_candidate_shape():
    c = build_golden_candidate(eco_id="t", session_id="sess12345678", inputs={"x": 1},
                               output={"y": 2}, online_scores={"faithfulness": 0.6},
                               trigger_reason="risk:gate_fired")
    assert c["labels"]["reviewed"] is False and c["expected"] == {}


# ── §8 proposal ─────────────────────────────────────────────────────────────────

def test_propose_from_decision_block():
    eco = {"ecosystem": {"name": "erb", "inputs": [{"id": "initiative"}]},
           "agents": [{"id": "a"}],
           "decision": {"dimensions": {"likelihood": {"type": "ordinal"},
                                       "impact": {"type": "ordinal"}},
                        "labels": ["blocker", "material", "minor"],
                        "decision": {"outcomes": ["NO_GO", "GO_WITH_CONDITIONS", "GO"]}}}
    prop = propose_evaluation(eco)
    ids = {m["id"] for m in prop["evaluation"]["metrics"]}
    assert "likelihood_acc" in ids and "evidence_faithful" in ids and "label_acc" in ids
    # NO_GO is abstain-like → abstention metric proposed
    assert any(m["scorer"] == "abstention" for m in prop["evaluation"]["metrics"])
    assert len(prop["golden_cases"]) == 4       # four archetypes


def test_compile_approved_proposal_roundtrips():
    eco = {"agents": [{"id": "a"}]}
    approved = {
        "evaluation": {"metrics": [{"id": "d", "scorer": "categorical", "field": "f"}],
                       "thresholds": {"d": 0.9}, "gate": "warn"},
        "golden_cases": [{"name": "c1", "inputs": {}, "expected": {"f": "X"}}],
    }
    block, jsonl = compile_approved_proposal(eco, approved)
    assert block["gate"] == "warn"
    assert json.loads(jsonl.strip())["name"] == "c1"


# ── runner glue + end-to-end (no boto3) ─────────────────────────────────────────

def test_load_and_expand_work_items():
    jsonl = '{"name":"c1","inputs":{"x":1},"expected":{"disposition":"GO"}}\n' \
            '{"name":"c2","inputs":{"x":2},"expected":{"disposition":"NO_GO"}}\n'
    cases = load_golden_cases(jsonl)
    items = expand_work_items(cases, replicates=3)
    assert len(items) == 6 and items[0]["session_id"] == "eval-c1-r0"


def test_load_empty_golden_fails():
    with pytest.raises(ValueError, match="empty"):
        load_golden_cases("\n \n")


def test_end_to_end_score_and_gate():
    ev = config_from_dict({"evaluation": {
        "replicates": 2,
        "metrics": [{"id": "disp", "field": "disposition", "scorer": "categorical"}],
        "thresholds": {"disp": 0.75}, "gate": "block"}, "agents": [{"id": "a"}]})
    cases = [{"name": "c1", "expected": {"disposition": "GO"}},
             {"name": "c2", "expected": {"disposition": "NO_GO"}}]
    # c1 right both reps; c2 right once, wrong once → per-replicate scores [1,1,1,0]
    run_results = [
        {"case_name": "c1", "replicate": 0, "run": {"output": {"disposition": "GO"}}},
        {"case_name": "c1", "replicate": 1, "run": {"output": {"disposition": "GO"}}},
        {"case_name": "c2", "replicate": 0, "run": {"output": {"disposition": "NO_GO"}}},
        {"case_name": "c2", "replicate": 1, "run": {"output": {"disposition": "GO"}}},
    ]
    det = score_deterministic(ev, cases, run_results)
    result = assemble_scorecard(ev=ev, eco_id="t", ecosystem_version="v1", config_hash="h",
                                golden_set_version="g1", judge_model="us.amazon.nova-pro-v1:0",
                                deterministic=det)
    sc, gate = result["scorecard"], result["gate"]
    disp = next(m for m in sc["metrics"] if m["metric_id"] == "disp")
    # median of [1,1,1,0] = 1.0 (median is the noise-aware signal, not the mean)
    assert disp["median"] == pytest.approx(1.0)
    assert gate["allow_deploy"]
    assert sc["case_miss_rate"]["c2"]["display"] == "1/2"


def test_trajectory_extraction_and_heuristic():
    spans = [
        {"span_id": "1", "attributes": {"gen_ai.operation.name": "execute_tool",
         "gen_ai.tool.name": "web_search", "gen_ai.tool.input": {"q": "x"}}},
        {"span_id": "2", "attributes": {"gen_ai.operation.name": "execute_tool",
         "gen_ai.tool.name": "rogue_tool", "gen_ai.tool.input": {}}},
    ]
    traj = extract_trajectory(spans, "sess")
    assert traj.tools_used() == ["web_search", "rogue_tool"]
    h = score_trajectory_heuristic(traj, allowed_tools={"web_search", "memory_read"})
    assert h["path_validity"] == 0.5 and h["invalid_tool_calls"][0]["tool"] == "rogue_tool"
