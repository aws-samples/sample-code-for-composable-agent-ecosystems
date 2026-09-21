"""Get one ecosystem config from Amazon S3.

Returns the RAW saved config (user agents only). Platform agents — the
Challenger, the final Report, and per-level checkpoints — are NOT injected here.
They are injected by the compiler at deploy time (save-ecosystem.compile_to_asl)
and mirrored for *display* by the frontend (dag-map + expandWithPlatformAgents).

Injecting them here too was a bug: the DAG rendered TWO Report (and Challenger)
nodes — once from this list and once from dag-map's own mirror — and the builder
editor showed the platform agents as if they were user-editable agents. The
display layer is now the single source of platform-agent rendering.
"""
import os, json, boto3, yaml

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")
# Amazon S3 client
s3 = boto3.client("s3")


def _latest_scorecard(eco_id):
    """Serve the latest evaluation scorecard for the eco (spec §9.1). Stored by
    the eval-aggregator at evaluations/<eco>/scorecards/_latest.json in the runs
    bucket ({scorecard, gate}). Returns None if none / not configured."""
    if not RUNS_BUCKET:
        return None
    try:
        raw = s3.get_object(Bucket=RUNS_BUCKET,
                            Key=f"evaluations/{eco_id}/scorecards/_latest.json")["Body"].read()
        return json.loads(raw)
    except Exception:
        return None


def _latest_golden_set(eco_id):
    """Serve the curated golden set for the eco (spec §5.3). The eval-approve
    step writes cases to evaluations/<eco>/<version>/cases.jsonl in the runs
    bucket (version = v<UTC-timestamp>Z). Returns {cases, version}, picking the
    lexicographically-latest version (timestamps sort correctly), or {cases:[]}
    if none / not configured."""
    if not RUNS_BUCKET:
        return {"cases": []}
    prefix = f"evaluations/{eco_id}/"
    try:
        # List the <version>/ sub-prefixes under the eco's evaluations folder.
        resp = s3.list_objects_v2(Bucket=RUNS_BUCKET, Prefix=prefix, Delimiter="/")
        versions = []
        for cp in resp.get("CommonPrefixes", []) or []:
            # e.g. "evaluations/<eco>/v20260101T000000Z/" -> "v20260101T000000Z"
            seg = cp["Prefix"][len(prefix):].strip("/")
            # Only version folders carry a cases.jsonl; skip scorecards/, judge/, etc.
            if seg and seg not in ("scorecards", "judge"):
                versions.append(seg)
        if not versions:
            return {"cases": []}
        version = sorted(versions)[-1]
        body = s3.get_object(Bucket=RUNS_BUCKET,
                             Key=f"{prefix}{version}/cases.jsonl")["Body"].read().decode("utf-8")
        cases = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return {"cases": cases, "version": version}
    except Exception:
        return {"cases": []}


def handler(event, context):
    eco_id = event["pathParameters"]["id"]
    headers = {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}

    # Evaluation scorecard artifact on the same route (no extra API GW resource):
    # GET /api/ecosystems/{id}?artifact=scorecard
    qs = (event.get("queryStringParameters") or {}) or {}
    if qs.get("artifact") == "scorecard":
        sc = _latest_scorecard(eco_id)
        if sc is None:
            return {"statusCode": 404, "headers": headers,
                    "body": json.dumps({"error": "no scorecard for this ecosystem"})}
        return {"statusCode": 200, "headers": headers, "body": json.dumps(sc, default=str)}

    # Curated golden set for the eval dashboard:
    # GET /api/ecosystems/{id}?artifact=golden_set  -> {cases:[...], version}
    if qs.get("artifact") == "golden_set":
        gs = _latest_golden_set(eco_id)
        return {"statusCode": 200, "headers": headers, "body": json.dumps(gs, default=str)}

    try:
        body = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()
        config = yaml.safe_load(body)
        # Attach the builder conversation transcript sidecar (if any) under an
        # underscore key so it rides along without polluting the eco config. Captured
        # transcripts are a plain list; reconstructed ones are {messages, reconstructed}.
        if isinstance(config, dict):
            try:
                raw = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/_transcripts/{eco_id}.builder.json")["Body"].read()
                data = json.loads(raw)
                if isinstance(data, list):
                    config["_build_transcript"] = {"messages": data, "reconstructed": False}
                elif isinstance(data, dict) and data.get("messages"):
                    config["_build_transcript"] = {"messages": data["messages"], "reconstructed": bool(data.get("reconstructed", True))}
            except Exception:
                pass
        return {"statusCode": 200, "headers": headers, "body": json.dumps(config, default=str)}
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Not found"})}
