"""eval-runner Lambda (spec §5.1, §5.3).

Two modes, both driven by the Eval State Machine:

  mode=load  — read the eco's `evaluation:` block + golden set from S3, expand
               (case × replicate) work items for the Map, and emit the parsed
               evaluation_config so the SM is self-contained.
  mode=score — run the DETERMINISTIC scorer primitives (§4) over the headless
               run outputs collected by the Map.

Eco-AGNOSTIC: all field names / expected values come from the golden set and the
generic `evaluation:` block. Fails loudly (no silent fallback).
"""
import os

import boto3
import yaml

from tessera_platform.evaluation.config import parse_evaluation_block
from tessera_platform.evaluation.runner import (
    load_golden_cases, expand_work_items, score_deterministic, config_from_dict,
    resolve_load_plan,
)

ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
s3 = boto3.client("s3")


def _read_eco(eco_id):
    obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
    return yaml.safe_load(obj["Body"].read())


def _resolve_bucket_token(uri):
    """Substitute the __RUNS_BUCKET__ placeholder with the deployment's runs bucket.
    Keeps account-specific / squattable bucket names out of committed ecosystem YAML —
    the golden_set is stored as s3://__RUNS_BUCKET__/... and resolved here at run time."""
    if "__RUNS_BUCKET__" in uri:
        if not RUNS_BUCKET:
            raise ValueError("golden_set uses __RUNS_BUCKET__ but TESSERA_RUNS_BUCKET is unset")
        uri = uri.replace("__RUNS_BUCKET__", RUNS_BUCKET)
    return uri


def _parse_s3_uri(uri):
    uri = _resolve_bucket_token(uri)
    assert uri.startswith("s3://"), f"golden_set must be an s3:// URI, got {uri!r}"
    _, _, rest = uri.partition("s3://")
    bucket, _, key = rest.partition("/")
    return bucket, key


def _golden_set_version(uri):
    # s3://bucket/evaluations/<eco>/<version>/cases.jsonl -> <version>
    parts = uri.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else "unversioned"


def handler(event, context):
    mode = event.get("mode")
    eco_id = event["eco_id"]

    if mode == "load":
        config = _read_eco(eco_id)
        ev = parse_evaluation_block(config)     # raises on malformed -> loud
        if ev is None:
            raise ValueError(f"eco {eco_id!r} has no `evaluation:` block to run")
        if not ev.golden_set:
            raise ValueError(f"eco {eco_id!r} evaluation block declares no golden_set")
        bucket, key = _parse_s3_uri(ev.golden_set)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        cases = load_golden_cases(body)
        # Per-run Quick-run overrides (replicates / max_cases). Never mutates the
        # stored block — resolve_load_plan yields EFFECTIVE values for this run
        # only, and fails loudly on malformed values. Eco-agnostic.
        plan = resolve_load_plan(cases, ev.replicates, event.get("overrides") or {})
        return {
            "cases": plan["cases"],
            "work_items": plan["work_items"],
            "evaluation_config": config["evaluation"],
            "golden_set_version": _golden_set_version(ev.golden_set),
            "replicates": plan["replicates"],
            "case_count": plan["case_count"],
            "overrides_applied": plan["overrides_applied"],
        }

    if mode == "score":
        ev = config_from_dict(event["evaluation_config"])
        cases = event["cases"]
        run_results = _flatten_map_output(event.get("run_results", []))
        return score_deterministic(ev, cases, run_results)

    raise ValueError(f"eval-runner: unknown mode {mode!r} (expected load|score)")


def _flatten_map_output(map_output):
    """The Map emits, per item, the Iterator's terminal state output carrying the
    item Parameters (case_name/replicate) + run.{output|error}. Normalise to
    [{case_name, replicate, run:{output}}]."""
    flat = []
    for item in map_output or []:
        flat.append({
            "case_name": item.get("case_name"),
            "replicate": item.get("replicate", 0),
            "run": item.get("run") or {},
        })
    return flat
