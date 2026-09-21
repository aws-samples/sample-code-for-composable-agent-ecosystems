"""Gate cleanup — clears orphaned human-gate state when a run reaches a terminal state.

Triggered by an EventBridge rule on "Step Functions Execution Status Change" for
terminal statuses (ABORTED / FAILED / TIMED_OUT / SUCCEEDED). This is the
authoritative, path-independent cleanup: it fires no matter HOW a run ended —
operator Abort, out-of-band stop (console/CLI), a sibling-agent failure that kills
the run, or a gate-pause timeout — so a paused run can never leave a phantom
"awaiting" gate in the Attention inbox.

What it clears for the terminated execution (session_id == execution name):
  1. Attention table: every `awaiting` item for the session → status `resolved`.
  2. Runs bucket: leftover gate-coordination files (gate_decision.json /
     gate_payload.json) for the session — dead once the run is terminal.

Defensive: never raises; logs and returns counts.
"""
import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
ATTENTION_TABLE = os.environ["ATTENTION_TABLE"]
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")

ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(ATTENTION_TABLE)
s3 = boto3.client("s3", region_name=REGION)

TERMINAL = {"ABORTED", "FAILED", "TIMED_OUT", "SUCCEEDED"}


def _clear_attention(session_id: str) -> int:
    """Mark every still-`awaiting` Attention item for this session as resolved.
    Scan-by-session is fine here — this fires once per run at terminal time."""
    cleared = 0
    try:
        kwargs = {
            "FilterExpression": "session_id = :sid AND #s = :aw",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {":sid": session_id, ":aw": "awaiting"},
        }
        while True:
            resp = table.scan(**kwargs)
            for it in resp.get("Items", []):
                try:
                    table.update_item(
                        Key={"ownerId": it["ownerId"], "itemId": it["itemId"]},
                        UpdateExpression="SET #s = :r",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":r": "resolved"},
                    )
                    cleared += 1
                except Exception as e:
                    print(f"[gate-cleanup] update failed for {it.get('itemId')}: {e}")
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:
        print(f"[gate-cleanup] attention scan failed for {session_id}: {e}")
    return cleared


def _delete_gate_files(session_id: str) -> int:
    """Delete leftover gate_decision.json / gate_payload.json for the session."""
    if not RUNS_BUCKET:
        return 0
    deleted = 0
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=RUNS_BUCKET, Prefix=f"runs/{session_id}/"):
            for o in page.get("Contents", []):
                k = o["Key"]
                if k.endswith(("gate_decision.json", "gate_payload.json")):
                    try:
                        s3.delete_object(Bucket=RUNS_BUCKET, Key=k)
                        deleted += 1
                    except Exception as e:
                        print(f"[gate-cleanup] delete failed for {k}: {e}")
    except Exception as e:
        print(f"[gate-cleanup] s3 list failed for {session_id}: {e}")
    return deleted


def handler(event, context):
    # EventBridge "Step Functions Execution Status Change" detail carries the
    # execution `name` (== our session_id) and `status`.
    detail = event.get("detail", {}) or {}
    status = detail.get("status", "")
    session_id = detail.get("name", "")
    if status not in TERMINAL or not session_id:
        return {"ok": True, "skipped": True, "status": status, "session_id": session_id}

    cleared = _clear_attention(session_id)
    deleted = _delete_gate_files(session_id)
    print(f"[gate-cleanup] session={session_id} status={status} attention_resolved={cleared} gate_files_deleted={deleted}")
    return {"ok": True, "session_id": session_id, "status": status,
            "attention_resolved": cleared, "gate_files_deleted": deleted}
