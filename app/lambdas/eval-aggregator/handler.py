"""eval-aggregator Lambda (spec §6, §9).

Merges deterministic + judge + trajectory scores into a noise-aware Scorecard
(median ± band, per-issue miss-rate), applies the deploy gate, and writes the
scorecard artifact to S3 at evaluations/<eco>/scorecards/<timestamp>.json (read
by the deploy gate in save-ecosystem). Eco-AGNOSTIC.
"""
import datetime
import json
import os

import boto3

from tessera_platform.evaluation.runner import assemble_scorecard, config_from_dict

RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
s3 = boto3.client("s3")


def _load_baseline(eco_id, current_version):
    """Return the most-recent scorecard for a DIFFERENT version (the baseline to
    detect regression against), or None."""
    prefix = f"evaluations/{eco_id}/scorecards/"
    try:
        entries = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=RUNS_BUCKET, Prefix=prefix):
            for o in page.get("Contents", []):
                entries.append((o["LastModified"], o["Key"]))
        entries.sort(reverse=True)
        for _, key in entries:
            sc = json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read())
            if sc.get("lineage", {}).get("ecosystem_version") != current_version:
                return _rehydrate(sc)
    except Exception as e:
        print(f"[eval-aggregator] baseline load failed for {eco_id}: {e}")
    return None


def _rehydrate(sc_dict):
    from tessera_platform.evaluation.scorecard import Scorecard
    from tessera_platform.evaluation.aggregate import AggregatedMetric
    metrics = [AggregatedMetric(
        metric_id=m["metric_id"], scorer=m.get("scorer", ""), median=m["median"],
        band=m["band"], empirical_band=m.get("empirical_band", 0.0), n=m.get("n", 1),
        miss_rate=m.get("miss_rate", 0.0), threshold=m.get("threshold"))
        for m in sc_dict.get("metrics", [])]
    lin = sc_dict.get("lineage", {})
    return Scorecard(
        ecosystem_id=lin.get("ecosystem_id", ""), ecosystem_version=lin.get("ecosystem_version", ""),
        config_hash=lin.get("config_hash", ""), golden_set_version=lin.get("golden_set_version", ""),
        judge_model=lin.get("judge_model"), timestamp=lin.get("timestamp", ""),
        gate=sc_dict.get("gate", "warn"), metrics=metrics)


def handler(event, context):
    eco_id = event["eco_id"]
    ev = config_from_dict(event["evaluation_config"])
    deterministic = (event.get("deterministic") or {}).get("body") or event.get("deterministic") or {}
    judge = (event.get("judge") or {}).get("body") if event.get("judge") else None
    trajectory = (event.get("trajectory") or {}).get("body") if event.get("trajectory") else None

    ecosystem_version = event.get("ecosystem_version", "current")
    config_hash = event.get("config_hash", "")
    golden_set_version = event.get("golden_set_version", "")
    judge_model = event.get("judge_model") or (judge or {}).get("judge_model")

    baseline = _load_baseline(eco_id, ecosystem_version)

    result = assemble_scorecard(
        ev=ev, eco_id=eco_id, ecosystem_version=ecosystem_version,
        config_hash=config_hash, golden_set_version=golden_set_version,
        judge_model=judge_model, deterministic=deterministic, judge=judge,
        trajectory=trajectory, baseline_scorecard=baseline,
    )

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"evaluations/{eco_id}/scorecards/{ts}.json"
    s3.put_object(Bucket=RUNS_BUCKET, Key=key,
                  Body=json.dumps(result["scorecard"], default=str).encode(),
                  ContentType="application/json")
    # Also update a stable "latest" pointer for the UI.
    s3.put_object(Bucket=RUNS_BUCKET, Key=f"evaluations/{eco_id}/scorecards/_latest.json",
                  Body=json.dumps(result, default=str).encode(),
                  ContentType="application/json")
    result["scorecard_key"] = key
    return result
