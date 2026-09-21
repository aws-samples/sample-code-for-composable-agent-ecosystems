"""eval-judge Lambda (spec §7.1, §7.2, Phase 2).

Bedrock Evaluations bring-your-own-inference (BYOI): formats {input, output,
ground_truth} per golden case, selects an INDEPENDENT judge model (pinned
distinct from the eco's agent models, §7.2), submits the eval job and records the
judge model on the result. The scorecard lineage carries judge_model (§9.2).

Domain-specificity flows ONLY through author `custom_rubric` values — generic pipe.
Fails loudly on judge-selection or API error.
"""
import json
import os
import re
import time

import boto3
import yaml

from tessera_platform.evaluation.runner import config_from_dict
from tessera_platform.evaluation.judge import (
    select_independent_judge, build_byoi_dataset, build_custom_metric_defs,
    collect_builtin_metrics, submit_judge_job,
)

ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
EVAL_ROLE_ARN = os.environ.get("BEDROCK_EVAL_ROLE_ARN", "")
s3 = boto3.client("s3")
bedrock = boto3.client("bedrock")


def _read_eco(eco_id):
    return yaml.safe_load(s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read())


def _run_outputs_by_case(run_results):
    out = {}
    for item in run_results or []:
        name = item.get("case_name")
        run = item.get("run") or {}
        o = run.get("output")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except json.JSONDecodeError:
                pass
        out.setdefault(name, []).append(o if o is not None else {})
    return out


def _bedrock_job_name(eco_id: str) -> str:
    """Bedrock CreateEvaluationJob jobName must match [a-z0-9](-*[a-z0-9]){0,62}
    (lowercase alnum + hyphens, start/end alnum, <=63 chars). Build a compliant,
    unique name — a digits-only timestamp avoids the 'T'/'Z' the ISO format adds."""
    suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    raw = re.sub(r"[^a-z0-9]+", "-", f"tessera-{eco_id[:40]}-{suffix}".lower()).strip("-")[:63].rstrip("-")
    if not raw or not raw[0].isalnum():
        raw = ("e" + raw)[:63].rstrip("-")
    return raw


def handler(event, context):
    eco_id = event["eco_id"]
    ev = config_from_dict(event["evaluation_config"])
    config = _read_eco(eco_id)
    cases = event["cases"]
    run_results = event.get("run_results", [])
    if isinstance(run_results, dict):
        run_results = run_results.get("body", run_results)

    judge_model = select_independent_judge(config, ev)   # raises if not independent
    dataset = build_byoi_dataset(ev, cases, _run_outputs_by_case(run_results))
    builtin = collect_builtin_metrics(ev)
    custom = build_custom_metric_defs(ev)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dataset_key = f"evaluations/{eco_id}/judge/{ts}/dataset.jsonl"
    s3.put_object(Bucket=RUNS_BUCKET, Key=dataset_key,
                  Body="\n".join(json.dumps(r, default=str) for r in dataset).encode(),
                  ContentType="application/jsonl")
    dataset_uri = f"s3://{RUNS_BUCKET}/{dataset_key}"
    output_uri = f"s3://{RUNS_BUCKET}/evaluations/{eco_id}/judge/{ts}/out/"

    result = {"judge_model": judge_model, "per_metric": {}, "dataset_uri": dataset_uri}

    if not EVAL_ROLE_ARN:
        # Fail loudly but non-fatally: record that the judge job could not be
        # submitted (missing role) so the aggregator/scorecard shows the gap
        # rather than silently omitting judge metrics.
        result["error"] = ("BEDROCK_EVAL_ROLE_ARN not configured — judge job not "
                           "submitted; judge metrics absent from this scorecard.")
        return result

    try:
        job_arn = submit_judge_job(
            bedrock_client=bedrock, job_name=_bedrock_job_name(eco_id),
            judge_model=judge_model, dataset_s3_uri=dataset_uri, output_s3_uri=output_uri,
            builtin_metrics=builtin, custom_metrics=custom, eval_role_arn=EVAL_ROLE_ARN)
    except Exception as e:
        # Judge is an OPTIONAL enrichment — a managed-eval submit failure must NOT
        # abort the whole eval. Record the gap (so the scorecard shows judge metrics
        # are absent) and let the SM proceed to aggregate: deterministic + trajectory
        # scores still produce a scorecard. Consistent with the missing-role path above.
        result["error"] = f"judge job submit failed: {type(e).__name__}: {str(e)[:300]}"
        result["status"] = "JUDGE_SUBMIT_FAILED"
        return result
    result["job_arn"] = job_arn
    result["output_uri"] = output_uri
    result["status"] = "SUBMITTED"
    # Note: polling to completion + parsing per-metric scores from the Bedrock
    # output is done by a follow-up poll (kept async so the SM is not blocked for
    # the full job duration). The scorecard records the job + judge model now;
    # scores backfill on completion.
    return result
