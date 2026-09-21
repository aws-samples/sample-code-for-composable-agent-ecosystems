"""Fresh Eval State Machine compiler (spec §5.1, decision §13.1).

Compiles the `evaluation:` block into a DEDICATED per-ecosystem Step Functions
state machine — separate from the run state machine and from quorum. The runner
is new and general; only the arena's STATISTICS are lifted (into aggregate.py).

Shape (spec §5.1):

    LoadGoldenSet (eval-runner: mode=load)         # read cases.jsonl from S3
      → Map over (cases × replicates):
           headless StartExecution.sync of the TARGET eco's run state machine
             (run_mode=headless → gates emit `warn`, never pause; "would it
              have fired" captured as a scored signal — §5.2)
      → ScoreDeterministic (eval-runner: mode=score)   # §4 primitives
      → [Judge]      (eval-judge: Bedrock BYOI)         # §7.1/§7.2  (if judge metrics)
      → [Trajectory] (eval-trajectory: AgentCore)       # §7.3        (if trajectory: true)
      → Aggregate    (eval-aggregator)                  # §6 medians ± bands, gate, scorecard

The ASL uses ${...} placeholders that the compiler host (save-ecosystem)
substitutes with real ARNs — mirroring the run-SM compiler's convention
(`${AgentInvokerLambdaArn}`). This module is pure (no boto3) so it is unit-testable.
"""

from __future__ import annotations

from .config import EvaluationConfig

# Placeholders resolved by the host compiler (save-ecosystem/handler.py).
EVAL_RUNNER_ARN = "${EvalRunnerLambdaArn}"
EVAL_JUDGE_ARN = "${EvalJudgeLambdaArn}"
EVAL_TRAJECTORY_ARN = "${EvalTrajectoryLambdaArn}"
EVAL_AGGREGATOR_ARN = "${EvalAggregatorLambdaArn}"
TARGET_RUN_SM_ARN = "${TargetRunStateMachineArn}"


def _lambda_task(arn: str, payload: dict, result_path: str, next_state: str | None,
                 end: bool = False, timeout: int = 900) -> dict:
    task = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {"FunctionName": arn, "Payload": payload},
        "ResultSelector": {"body.$": "$.Payload"},
        "ResultPath": result_path,
        "TimeoutSeconds": timeout,
        "Retry": [{
            "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException",
                            "Lambda.SdkClientException", "States.Timeout"],
            "IntervalSeconds": 5, "MaxAttempts": 2, "BackoffRate": 2.0,
        }],
    }
    if end:
        task["End"] = True
    elif next_state:
        task["Next"] = next_state
    return task


def compile_eval_state_machine(config: dict, ev: EvaluationConfig, eco_id: str) -> dict:
    """Build the Eval State Machine ASL for one ecosystem.

    `ev` is the parsed EvaluationConfig; `config` the full eco config (for
    lineage). Returns an ASL dict with ${...} placeholders."""
    eco_name = (config.get("ecosystem") or {}).get("name", eco_id)
    replicates = ev.replicates
    has_judge = any(m.is_judge for m in ev.metrics)
    has_trajectory = bool(ev.trajectory)

    states: dict[str, dict] = {}

    # 1. Load golden set (eval-runner mode=load) → $.cases (list of {name, inputs, expected})
    #    `overrides` (per-run replicates / max_cases) is threaded through from the
    #    StartExecution input. The launch ALWAYS sends an `overrides` object (default
    #    {}), so this JSONPath always resolves. An empty {} → full stored config.
    states["LoadGoldenSet"] = _lambda_task(
        EVAL_RUNNER_ARN,
        {
            "mode": "load",
            "eco_id": eco_id,
            "overrides.$": "$.overrides",
        },
        result_path="$.loaded",
        next_state="RunGoldenCases",
        timeout=120,
    )

    # 2. Map over cases × replicates → headless run of the TARGET run SM.
    #    We expand (case × replicate) into work items in the loader so the Map is flat.
    run_case_task = {
        "Type": "Task",
        "Resource": "arn:aws:states:::states:startExecution.sync:2",
        "Parameters": {
            "StateMachineArn": TARGET_RUN_SM_ARN,
            "Input": {
                "mission.$": "$.mission",
                "inputs.$": "$.inputs",
                "input_context.$": "$.input_context",   # required by the run SM (MergeLayer0) + carries the case data to agents
                "session_id.$": "$.session_id",
                "user_id": "eval-harness",           # headless system identity — the run SM's Layer0 requires $.user_id
                "run_mode": "headless",              # §5.2 — gates warn, never pause
                "dry_run": False,
                "run_config": {},
                "dependency_context": "",            # run SM threads $.dependency_context (string, matching the launch contract)
                "results": {},
                "AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID.$": "$$.Execution.Id",
            },
        },
        "ResultSelector": {
            "run_arn.$": "$.ExecutionArn",
            "output.$": "$.Output",
        },
        "ResultPath": "$.run",
        "TimeoutSeconds": 3600,
        "Retry": [{
            "ErrorEquals": ["States.ALL"],
            "IntervalSeconds": 10, "MaxAttempts": 1, "BackoffRate": 2.0,
        }],
        "Catch": [{
            # A failed headless run is a DATA POINT (the eco errored on this case),
            # not a harness crash — capture it and continue so one bad case does
            # not abort the whole eval (loud but non-fatal per-item).
            "ErrorEquals": ["States.ALL"],
            "ResultPath": "$.run_error",
            "Next": "RecordRunError",
        }],
        "End": True,
    }
    states["RunGoldenCases"] = {
        "Type": "Map",
        "ItemsPath": "$.loaded.body.work_items",
        "MaxConcurrency": int((config.get("max_concurrency") or 4)),
        "Parameters": {
            "mission.$": "$$.Map.Item.Value.mission",
            "inputs.$": "$$.Map.Item.Value.inputs",
            "input_context.$": "$$.Map.Item.Value.input_context",
            "session_id.$": "$$.Map.Item.Value.session_id",
            "case_name.$": "$$.Map.Item.Value.case_name",
            "replicate.$": "$$.Map.Item.Value.replicate",
        },
        "Iterator": {
            "StartAt": "RunCase",
            "States": {
                "RunCase": run_case_task,
                "RecordRunError": {
                    "Type": "Pass",
                    "Parameters": {
                        "run": {"output": None, "error.$": "$.run_error.Error"},
                    },
                    "End": True,
                },
            },
        },
        "ResultPath": "$.run_results",
        "Next": "ScoreDeterministic",
    }

    # 3. Deterministic scoring (eval-runner mode=score) → per-metric per-replicate scores.
    next_after_score = "Judge" if has_judge else ("Trajectory" if has_trajectory else "Aggregate")
    states["ScoreDeterministic"] = _lambda_task(
        EVAL_RUNNER_ARN,
        {
            "mode": "score",
            "eco_id": eco_id,
            "evaluation_config.$": "$.loaded.body.evaluation_config",
            "cases.$": "$.loaded.body.cases",
            "run_results.$": "$.run_results",
        },
        result_path="$.deterministic",
        next_state=next_after_score,
        timeout=300,
    )

    # 4. Judge (optional) — Bedrock BYOI (§7.1/§7.2).
    if has_judge:
        next_after_judge = "Trajectory" if has_trajectory else "Aggregate"
        states["Judge"] = _lambda_task(
            EVAL_JUDGE_ARN,
            {
                "eco_id": eco_id,
                "evaluation_config.$": "$.loaded.body.evaluation_config",
                "cases.$": "$.loaded.body.cases",
                "run_results.$": "$.run_results",
            },
            result_path="$.judge",
            next_state=next_after_judge,
            timeout=900,
        )

    # 5. Trajectory (optional) — AgentCore Evaluations over ADOT/OTel spans (§7.3).
    if has_trajectory:
        states["Trajectory"] = _lambda_task(
            EVAL_TRAJECTORY_ARN,
            {
                "eco_id": eco_id,
                "evaluation_config.$": "$.loaded.body.evaluation_config",
                "run_results.$": "$.run_results",
            },
            result_path="$.trajectory",
            next_state="Aggregate",
            timeout=900,
        )

    # 6. Aggregate + gate + write scorecard (eval-aggregator, §6).
    aggregate_payload = {
        "eco_id": eco_id,
        "evaluation_config.$": "$.loaded.body.evaluation_config",
        "deterministic.$": "$.deterministic",
        "cases.$": "$.loaded.body.cases",
    }
    if has_judge:
        aggregate_payload["judge.$"] = "$.judge"
    if has_trajectory:
        aggregate_payload["trajectory.$"] = "$.trajectory"
    states["Aggregate"] = _lambda_task(
        EVAL_AGGREGATOR_ARN,
        aggregate_payload,
        result_path="$.scorecard",
        next_state=None,
        end=True,
        timeout=120,
    )

    return {
        "Comment": f"Tessera Eval State Machine: {eco_name} "
                   f"(replicates={replicates}, judge={has_judge}, trajectory={has_trajectory})",
        "StartAt": "LoadGoldenSet",
        "States": states,
    }
