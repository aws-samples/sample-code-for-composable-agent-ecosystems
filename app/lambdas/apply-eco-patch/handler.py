"""POST /api/apply-eco-patch — applies a patch_synthesizer's proposal to a
target ecosystem and writes a new version.

Versioning convention (matches existing repo pattern, e.g.
hedge-fund-trading-desk-v1.yaml + hedge-fund-trading-desk.yaml):
  - Bare ID (`<eco_id>.yaml`) holds the *latest* version, in S3.
  - Suffixed IDs (`<eco_id>-v<N>.yaml`) are version snapshots.

When a patch is approved this Lambda:
  1. Reads the optimizer run's patch_proposal from S3 (per the optimizer
     session_id) — the patch_synthesizer agent persists it there as part
     of its output.txt + tool_results.json + memory_write.
     For MVP we simply accept the new YAML inline in the request body
     (the frontend reads it from the patch surface and posts it back).
  2. Validates the YAML parses + has required fields + is NOT itself a
     platform eco.
  3. Computes next version number by scanning S3 for existing -vN suffixes
     of the same target_ecosystem_id.
  4. Snapshots the *current* bare YAML as <id>-v<current>.yaml.
  5. Writes the new YAML as the bare <id>.yaml.
  6. Triggers ASL recompile via the save-ecosystem Lambda's /deploy path.

Request body:
  {
    "target_ecosystem_id": "fsi-anz-personas-painpoints-tessera",
    "new_yaml": "ecosystem:\n  name: ...\n",         # full YAML text
    "approved_change_ids": ["C001","C003","C007"],   # for audit log
    "optimizer_session_id": "platform-optimizer-abc123",  # for audit log
    "applied_by": "operator"                          # optional
  }

Response:
  {
    "applied": true,
    "target_ecosystem_id": "fsi-anz-personas-painpoints-tessera",
    "snapshot_version": 2,            # the version the OLD YAML was archived to
    "deployed": true,
    "state_machine_arn": "arn:..."
  }
"""
import json
import os
import re
from typing import Dict, List, Tuple

import boto3
import yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
SAVE_ECOSYSTEM_FN = os.environ.get("SAVE_ECOSYSTEM_FN", "")

# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)


# ---------------------------------------------------------------------------
# Versioning helpers
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"-v(\d+)$")


def _list_existing_versions(eco_id: str) -> List[int]:
    """Return all version numbers N for which <eco_id>-v<N>.yaml exists in S3."""
    versions: List[int] = []
    paginator = s3.get_paginator("list_objects_v2")
    # Check both legacy location and new _versions/ prefix
    for prefix in [f"ecosystems/{eco_id}-v", f"ecosystems/_versions/{eco_id}-v"]:
        for page in paginator.paginate(Bucket=ECOSYSTEMS_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if not key.endswith(".yaml"):
                    continue
                base = key.rsplit("/", 1)[-1].replace(".yaml", "")
                tail = base[len(eco_id):]
                m = _VERSION_RE.match(tail)
                if m:
                    versions.append(int(m.group(1)))
    return sorted(versions)


def _next_snapshot_version(eco_id: str) -> int:
    """Determine the version number to assign when archiving the current
    bare YAML before overwriting it.

    If no snapshots exist yet, the current YAML becomes v1. Subsequent
    applies create v2, v3, etc.
    """
    existing = _list_existing_versions(eco_id)
    return (max(existing) if existing else 0) + 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_new_yaml(new_yaml: str, target_eco_id: str) -> Tuple[bool, str, dict]:
    """Parse + sanity-check the new YAML.

    Returns (is_valid, error_message, parsed_config).
    """
    try:
        cfg = yaml.safe_load(new_yaml)
    except yaml.YAMLError as e:
        return False, f"YAML parse error: {e}", {}

    if not isinstance(cfg, dict):
        return False, "Top-level YAML must be a mapping.", {}

    if cfg.get("platform"):
        return False, (
            "Refusing to apply: new YAML has platform:true. Platform "
            "ecosystems must be edited in repo, not via the optimizer."
        ), cfg

    eco = cfg.get("ecosystem") or {}
    if not eco.get("name"):
        return False, "Missing ecosystem.name", cfg

    agents = cfg.get("agents") or []
    if not isinstance(agents, list) or len(agents) == 0:
        return False, "agents[] must be non-empty", cfg

    return True, "", cfg


# ---------------------------------------------------------------------------
# S3 operations
# ---------------------------------------------------------------------------

def _read_current_yaml(eco_id: str) -> str:
    obj = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")
    return obj["Body"].read().decode("utf-8")


def _write_yaml(key: str, body: str) -> None:
    s3.put_object(
        Bucket=ECOSYSTEMS_BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="text/yaml",
    )


def _trigger_asl_recompile(eco_id: str) -> Dict:
    """Invoke save-ecosystem's /deploy path so the new YAML's ASL is built."""
    if not SAVE_ECOSYSTEM_FN:
        return {"error": "SAVE_ECOSYSTEM_FN env not configured"}

    payload = {
        "pathParameters": {"id": eco_id},
        "path": f"/api/ecosystems/{eco_id}/deploy",
        "httpMethod": "POST",
        "body": "{}",
    }

    try:
        resp = lambda_client.invoke(
            FunctionName=SAVE_ECOSYSTEM_FN,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        out = json.loads(resp["Payload"].read())
        body = json.loads(out.get("body", "{}"))
        return body
    except Exception as e:
        return {"error": f"Recompile invoke failed: {e}"}


def _write_audit_record(
    target_eco_id: str,
    snapshot_version: int,
    approved_change_ids: List[str],
    optimizer_session_id: str,
    applied_by: str,
) -> None:
    """Write a small audit record alongside the snapshot so we can later
    answer 'which optimizer run produced v3 of this eco?'.
    """
    record = {
        "target_eco_id": target_eco_id,
        "snapshot_version": snapshot_version,
        "approved_change_ids": approved_change_ids,
        "optimizer_session_id": optimizer_session_id,
        "applied_by": applied_by,
    }
    key = f"ecosystems/_audit/{target_eco_id}-v{snapshot_version}.json"
    s3.put_object(
        Bucket=ECOSYSTEMS_BUCKET,
        Key=key,
        Body=json.dumps(record, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _response(status: int, body: Dict) -> Dict:
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
        req = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    target_eco_id = (req.get("target_ecosystem_id") or "").strip()
    new_yaml = req.get("new_yaml") or ""
    approved_ids = req.get("approved_change_ids") or []
    optimizer_session_id = req.get("optimizer_session_id") or ""
    applied_by = req.get("applied_by") or "operator"

    if not target_eco_id:
        return _response(400, {"error": "target_ecosystem_id is required"})
    if not new_yaml:
        return _response(400, {"error": "new_yaml is required"})

    # Validate the proposed YAML
    ok, err, cfg = _validate_new_yaml(new_yaml, target_eco_id)
    if not ok:
        return _response(400, {"error": err})

    # Refuse to apply onto a platform ecosystem (read-only)
    try:
        current = _read_current_yaml(target_eco_id)
        current_cfg = yaml.safe_load(current) or {}
        if current_cfg.get("platform"):
            return _response(403, {
                "error": "Cannot apply patches to a platform ecosystem.",
                "detail": (
                    f"'{target_eco_id}' is platform-managed. Edit the source "
                    "in ecosystems/_platform/ and redeploy."
                ),
            })
    except s3.exceptions.NoSuchKey:
        return _response(404, {"error": f"Target ecosystem not found: {target_eco_id}"})
    except Exception as e:
        return _response(500, {"error": f"Failed to read current YAML: {e}"})

    # 1. Snapshot current YAML to _versions/ prefix (keeps ecosystems/ clean)
    snapshot_version = _next_snapshot_version(target_eco_id)
    snapshot_key = f"ecosystems/_versions/{target_eco_id}-v{snapshot_version}.yaml"
    try:
        _write_yaml(snapshot_key, current)
    except Exception as e:
        return _response(500, {"error": f"Failed to snapshot current YAML: {e}"})

    # 2. Write new YAML as the bare (latest)
    try:
        _write_yaml(f"ecosystems/{target_eco_id}.yaml", new_yaml)
    except Exception as e:
        # Roll back snapshot? Snapshot is just a copy of the OLD content,
        # leaving it is fine — the new YAML write is what failed.
        return _response(500, {"error": f"Failed to write new YAML: {e}"})

    # 3. Audit record
    try:
        _write_audit_record(
            target_eco_id, snapshot_version, approved_ids,
            optimizer_session_id, applied_by,
        )
    except Exception:
        # Non-fatal — proceed with deploy.
        pass

    # 4. Trigger ASL recompile
    deploy_result = _trigger_asl_recompile(target_eco_id)

    return _response(200, {
        "applied": True,
        "target_ecosystem_id": target_eco_id,
        "snapshot_version": snapshot_version,
        "snapshot_key": snapshot_key,
        "approved_change_ids": approved_ids,
        "deployed": bool(deploy_result.get("deployed")),
        "state_machine_arn": deploy_result.get("state_machine_arn", ""),
        "deploy_warnings": deploy_result.get("warnings", []),
        "deploy_error": deploy_result.get("error"),
    })
