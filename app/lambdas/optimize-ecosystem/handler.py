"""POST /api/optimize-ecosystem — bundles target ecosystem telemetry then

Services: Amazon Bedrock, Amazon S3, AWS Step Functions.
launches the platform-optimizer Step Functions execution.

The platform-optimizer ecosystem (ecosystems/_platform/platform-optimizer.yaml)
expects a structured telemetry bundle in the input context describing the
target ecosystem's YAML and last N runs. Rather than have agents inside the
optimizer scrape SFN + S3 themselves (which would require IAM gymnastics
across the AgentCore Runtime), this Lambda pre-bundles everything with
proper credentials and passes it via the SFN execution input.

Request body:
  {
    "target_ecosystem_id": "fsi-anz-personas-painpoints-tessera",
    "max_runs": 5                 # optional, default 5
  }

Response:
  {
    "session_id": "platform-optimizer-<8hex>",
    "execution_arn": "arn:aws:states:...",
    "telemetry": { "runs_loaded": 5, "agents_in_target": 8 }
  }
"""
import json
import os
import re
import uuid
from typing import Any, Dict, List

import boto3
import yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]
STATE_MACHINE_PREFIX = os.environ["STATE_MACHINE_PREFIX"]
OPTIMIZER_ECO_ID = os.environ.get("OPTIMIZER_ECO_ID", "platform-optimizer")

# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
# AWS Step Functions client
sfn = boto3.client("stepfunctions", region_name=REGION)


# ---------------------------------------------------------------------------
# Telemetry bundling
# ---------------------------------------------------------------------------

def _load_target_yaml(target_eco_id: str) -> str:
    """Fetch the raw YAML text of the target ecosystem from S3."""
    key = f"ecosystems/{target_eco_id}.yaml"
    obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=key)
    return obj["Body"].read().decode("utf-8")


def _list_recent_executions(target_eco_id: str, max_runs: int) -> List[Dict[str, Any]]:
    """Return the most recent SFN executions for the target ecosystem.

    State machine naming convention: {prefix}-{ecosystem_id}.
    """
    sm_name = f"{STATE_MACHINE_PREFIX}-{target_eco_id}"
    sm_arn = f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:{sm_name}"

    try:
        resp = sfn.list_executions(
            stateMachineArn=sm_arn,
            maxResults=max(max_runs, 5),
        )
    except sfn.exceptions.StateMachineDoesNotExist:
        return []
    except Exception as e:
        print(f"optimize: list_executions failed for {sm_arn}: {e}")
        return []

    return resp.get("executions", [])[:max_runs]


def _safe_get_object_text(bucket: str, key: str, max_bytes: int = 4096) -> str:
    """Read up to max_bytes from S3 as text; truncate gracefully."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read(max_bytes + 1)
        text = body.decode("utf-8", errors="replace")
        if len(body) > max_bytes:
            text = text[:max_bytes] + "...[truncated]"
        return text
    except s3.exceptions.NoSuchKey:
        return ""
    except Exception as e:
        return f"[read-error: {e}]"


def _safe_get_object_size(bucket: str, key: str) -> int:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return int(head.get("ContentLength", 0))
    except Exception:
        return 0


def _list_run_agents(session_id: str) -> List[str]:
    """Discover which agents emitted output for this run by listing the
    run's S3 prefix. Each agent has its own subdirectory.
    """
    prefix = f"runs/{session_id}/"
    agent_ids = []
    paginator = s3.get_paginator("list_objects_v2")
    seen = set()
    for page in paginator.paginate(Bucket=RUNS_BUCKET, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []) or []:
            sub = cp.get("Prefix", "")
            # sub looks like "runs/<session>/<agent_id>/"
            agent_id = sub[len(prefix):].rstrip("/")
            if agent_id and agent_id not in seen:
                seen.add(agent_id)
                agent_ids.append(agent_id)
    return sorted(agent_ids)


def _count_tool_calls_by_name(tool_results_text: str) -> Dict[str, int]:
    """Quick-and-dirty count of tool calls by name from tool_results.json.
    The file is a JSON list with each entry having a 'name' field.
    """
    counts: Dict[str, int] = {}
    try:
        arr = json.loads(tool_results_text)
        if isinstance(arr, list):
            for call in arr:
                name = call.get("name") or call.get("tool") or "?"
                counts[name] = counts.get(name, 0) + 1
    except Exception:
        pass
    return counts


def _agent_telemetry(session_id: str, agent_id: str) -> Dict[str, Any]:
    """Build a per-agent telemetry record from S3 outputs."""
    out_key = f"runs/{session_id}/{agent_id}/output.txt"
    tool_key = f"runs/{session_id}/{agent_id}/tool_results.json"

    output_text = _safe_get_object_text(RUNS_BUCKET, out_key, max_bytes=2000)
    output_size = _safe_get_object_size(RUNS_BUCKET, out_key)
    tool_text = _safe_get_object_text(RUNS_BUCKET, tool_key, max_bytes=200000)
    tool_size = _safe_get_object_size(RUNS_BUCKET, tool_key)
    tool_counts = _count_tool_calls_by_name(tool_text)

    has_findings = bool(re.search(r"##\s+STRUCTURED FINDINGS", output_text))

    return {
        "agent_id": agent_id,
        "output_size_bytes": output_size,
        "output_text_excerpt": output_text[:1500],   # short for token budget
        "has_structured_findings": has_findings,
        "memory_read_count": tool_counts.get("memory_read", 0),
        "memory_write_count": tool_counts.get("memory_write", 0),
        "code_interpreter_count": tool_counts.get("code_interpreter", 0),
        "generate_ui_count": tool_counts.get("generateUI", 0),
        "web_search_count": tool_counts.get("web_search", 0),
        "http_request_count": tool_counts.get("http_request", 0),
        "tool_results_size_bytes": tool_size,
    }


def _bundle_run(execution: Dict[str, Any]) -> Dict[str, Any]:
    """For a single execution, fetch SFN status + per-agent S3 outputs."""
    session_id = execution.get("name", "")
    started = execution.get("startDate")
    ended = execution.get("stopDate")
    duration = None
    if started and ended:
        duration = int((ended - started).total_seconds())

    agents_in_run = _list_run_agents(session_id)
    per_agent = [_agent_telemetry(session_id, aid) for aid in agents_in_run]

    # Snippet of the challenger's findings if present
    challenger_excerpt = ""
    if "challenger_agent" in agents_in_run:
        challenger_excerpt = _safe_get_object_text(
            RUNS_BUCKET,
            f"runs/{session_id}/challenger_agent/output.txt",
            max_bytes=2000,
        )

    return {
        "session_id": session_id,
        "status": execution.get("status"),
        "started_at": started.isoformat() if started else None,
        "ended_at": ended.isoformat() if ended else None,
        "duration_seconds": duration,
        "agents": per_agent,
        "challenger_findings_excerpt": challenger_excerpt,
    }


# ---------------------------------------------------------------------------
# Mission text (telemetry serialised for the optimizer's input_context)
# ---------------------------------------------------------------------------

def _build_mission(target_eco_id: str, target_yaml: str, runs: List[Dict[str, Any]]) -> str:
    """Compose the mission/input_context that the optimizer sees.

    Cap each section so we stay within the 32K input-token budget. The
    telemetry_loader agent's STEP 1 parses this JSON.
    """
    bundle = {
        "target_ecosystem_id": target_eco_id,
        "target_yaml": target_yaml[:30000],   # ~7K tokens
        "runs": runs,
        "catalog_optimization_patterns": [],   # populated later (5d.10)
    }
    serialized = json.dumps(bundle, default=str)

    # Hard cap: optimizer's first agent has 32K input + tool-results context.
    # Leave ~16K headroom for downstream. Bundle should fit in ~50K bytes.
    if len(serialized) > 80000:
        # Shed run output_text_excerpts first (least critical).
        for r in bundle["runs"]:
            for a in r.get("agents", []):
                a["output_text_excerpt"] = a.get("output_text_excerpt", "")[:300]
        serialized = json.dumps(bundle, default=str)

    return (
        f"Optimize ecosystem `{target_eco_id}` based on its last "
        f"{len(runs)} runs.\n\n"
        f"--- TELEMETRY BUNDLE (JSON) ---\n{serialized}\n--- END BUNDLE ---"
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    target_eco_id = (body.get("target_ecosystem_id") or "").strip()
    if not target_eco_id:
        return _response(400, {"error": "target_ecosystem_id is required"})

    try:
        max_runs = int(body.get("max_runs") or 5)
    except (TypeError, ValueError):
        max_runs = 5
    max_runs = max(1, min(max_runs, 20))   # clamp

    # 1. Load target YAML
    try:
        target_yaml = _load_target_yaml(target_eco_id)
    except s3.exceptions.NoSuchKey:
        return _response(404, {"error": f"Ecosystem not found: {target_eco_id}"})
    except Exception as e:
        return _response(500, {"error": f"Failed to load target YAML: {e}"})

    # 2. Block optimization OF the optimizer (avoid recursion / shooting
    # ourselves in the foot). Operators should edit the YAML in repo.
    try:
        target_cfg = yaml.safe_load(target_yaml) or {}
        if target_cfg.get("platform"):
            return _response(403, {
                "error": "Cannot optimize a platform ecosystem.",
                "detail": (
                    f"'{target_eco_id}' is platform-managed. Edit the source "
                    "YAML in ecosystems/_platform/ and redeploy."
                ),
            })
    except Exception:
        pass

    # 3. Bundle telemetry
    executions = _list_recent_executions(target_eco_id, max_runs)
    if len(executions) < 1:
        return _response(400, {
            "error": "No run history.",
            "detail": (
                f"Found 0 prior runs for '{target_eco_id}'. "
                "The optimizer needs at least one completed run to analyse."
            ),
            "runs_found": len(executions),
        })

    runs_telemetry = [_bundle_run(ex) for ex in executions]

    mission = _build_mission(target_eco_id, target_yaml, runs_telemetry)

    # 4. Start the platform-optimizer SFN execution
    optimizer_sm_arn = (
        f"arn:aws:states:{REGION}:{ACCOUNT_ID}:stateMachine:"
        f"{STATE_MACHINE_PREFIX}-{OPTIMIZER_ECO_ID}"
    )
    session_id = f"{OPTIMIZER_ECO_ID}-{uuid.uuid4().hex[:8]}"

    try:
        run_resp = sfn.start_execution(
            stateMachineArn=optimizer_sm_arn,
            name=session_id,
            input=json.dumps({
                "mission": mission,
                "session_id": session_id,
                "dependency_context": "",
                "input_context": (
                    f"target_ecosystem_id={target_eco_id}\n"
                    f"max_runs={max_runs}\n"
                    f"runs_loaded={len(runs_telemetry)}"
                ),
                "results": {},
            }),
        )
    except sfn.exceptions.StateMachineDoesNotExist:
        return _response(503, {
            "error": "Platform optimizer not deployed.",
            "detail": (
                "The platform-optimizer state machine doesn't exist. Run "
                "scripts/deploy-platform-ecosystems.sh to deploy it."
            ),
        })
    except Exception as e:
        return _response(500, {"error": f"Failed to start optimizer: {e}"})

    return _response(200, {
        "session_id": session_id,
        "execution_arn": run_resp.get("executionArn"),
        "ecosystem_id": OPTIMIZER_ECO_ID,
        "target_ecosystem_id": target_eco_id,
        "telemetry": {
            "runs_loaded": len(runs_telemetry),
            "agents_in_target": sum(len(r.get("agents", [])) for r in runs_telemetry),
            "max_runs_requested": max_runs,
        },
    })
