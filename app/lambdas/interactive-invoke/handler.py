"""Interactive eco serve engine (spec-interactive-eco.md).

Runs an `strategy: interactive` ecosystem's multi-agent graph in dependency layers
(parallel within a layer), grounded in the eco's mounted catalog namespaces.

A multi-agent turn takes tens of seconds, which exceeds API Gateway's hard 30s
integration timeout. So the HTTP path is ASYNC: POST starts a job (returns a job_id
after firing an async self-invocation that does the work and writes the result to a
job store), and the client POSTs {mode:"poll"} until the answer is ready. Direct
(non-API-GW) invokes can still run synchronously.

Event modes:
  start (default): {eco_id, message, history?}          -> {job_id, status:"running"}
  poll:            {mode:"poll", job_id}                 -> {status, answer?, agents_run?}
  run (internal):  {mode:"run", job_id, eco_id, message, history}  (async self-invoke)
Env: TESSERA_MEMORY_ID, CATALOG_REFRESH_TABLE, ECOSYSTEMS_BUCKET, INTERACTIVE_MODEL_ID,
     INTERACTIVE_SELF_FN
"""
import os, re, json, time, uuid, concurrent.futures as cf
import boto3, yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL = os.environ.get("INTERACTIVE_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MEM = os.environ.get("TESSERA_MEMORY_ID", "")
ECOS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
JOB_TABLE = os.environ.get("CATALOG_REFRESH_TABLE", "tessera-dev-catalog-refresh")
SELF_FN = os.environ.get("INTERACTIVE_SELF_FN", "tessera-dev-interactive-invoke")
_s3 = boto3.client("s3", region_name=REGION)
_bed = boto3.client("bedrock-runtime", region_name=REGION)
_ac = boto3.client("bedrock-agentcore", region_name=REGION)
_lam = boto3.client("lambda", region_name=REGION)
_ddb = boto3.client("dynamodb", region_name=REGION)


def _layers(agents):
    by_id = {a["id"]: a for a in agents}
    done, layers, remaining = set(), [], list(agents)
    while remaining:
        layer = [a for a in remaining if all(d in done for d in (a.get("depends_on") or []))]
        if not layer:
            layer = remaining
        layers.append(layer)
        for a in layer:
            done.add(a["id"])
        remaining = [a for a in remaining if a["id"] not in done]
    return layers


def _grounding(eco):
    from tessera_platform.catalog import store as refresh
    doms = eco.get("domains") or []
    doms = doms if isinstance(doms, list) else [doms]
    version = refresh.get_active_version(doms[0]) if doms else None
    if not version:
        return {}
    out = {}
    for ns in (eco.get("memory", {}).get("shared_namespaces") or []):
        m = re.match(r"^/catalog/([a-z0-9-]+)/?$", ns.get("path", ""))
        if not m:
            continue
        t = m.group(1)
        recs, tok = [], None
        while len(recs) < 200:
            kw = {"memoryId": MEM, "namespacePath": f"/tessera/catalog/{version}/{t}", "maxResults": 100}
            if tok:
                kw["nextToken"] = tok
            r = _ac.list_memory_records(**kw)
            for x in r.get("memoryRecordSummaries") or []:
                try:
                    recs.append(json.loads((x.get("content") or {}).get("text", "{}") or "{}"))
                except Exception:
                    pass
            tok = r.get("nextToken")
            if not tok:
                break
        out[t] = recs
    return out


def _run_agent(a, context):
    r = _bed.converse(modelId=MODEL, system=[{"text": a.get("prompt", "")}],
                      messages=[{"role": "user", "content": [{"text": json.dumps(context)[:120000]}]}],
                      inferenceConfig={"maxTokens": 900, "temperature": 0})
    return a["id"], r["output"]["message"]["content"][0]["text"]


def _serve(eco_id, message, history):
    """Run the eco's graph synchronously and return (answer, agents_run)."""
    eco = yaml.safe_load(_s3.get_object(Bucket=ECOS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()) or {}
    if (eco.get("strategy") or "orchestrated") != "interactive":
        raise ValueError(f"{eco_id} is not an interactive eco")
    agents = eco.get("agents") or []
    shared = {"visitor_message": message, "history": history, "grounding": _grounding(eco)}
    outputs = {}
    for layer in _layers(agents):
        with cf.ThreadPoolExecutor(max_workers=min(4, len(layer))) as ex:
            ctx = {**shared, "upstream": outputs}
            for aid, out in ex.map(lambda a: _run_agent(a, ctx), layer):
                outputs[aid] = out
    return (outputs.get(agents[-1]["id"], "") if agents else ""), list(outputs.keys())


def _job_key(job_id):
    return {"pk": {"S": f"INTERACTIVE#{job_id}"}, "sk": {"S": "JOB"}}


def _ok(obj):
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(obj)}


def handler(event, context=None):
    body = event.get("body")
    if isinstance(body, str):
        try: event = {**event, **json.loads(body)}
        except Exception: pass
    mode = event.get("mode", "start")

    # ── poll: return the stored job result ─────────────────────────────────────
    if mode == "poll":
        job_id = str(event.get("job_id", ""))
        if not re.fullmatch(r"[a-f0-9-]{8,40}", job_id):
            return {"statusCode": 400, "body": json.dumps({"error": "invalid job_id"})}
        it = _ddb.get_item(TableName=JOB_TABLE, Key=_job_key(job_id)).get("Item")
        if not it:
            return _ok({"status": "unknown"})
        return _ok({"status": it.get("status", {}).get("S", "running"),
                    "answer": it.get("answer", {}).get("S", ""),
                    "agents_run": json.loads(it.get("agents_run", {}).get("S", "[]"))})

    eco_id = event.get("eco_id", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,120}", eco_id or ""):
        return {"statusCode": 400, "body": json.dumps({"error": "invalid eco_id"})}
    message = str(event.get("message", ""))[:8000]
    history = event.get("history") or []

    # ── run (internal async worker): do the work, store the result ─────────────
    if mode == "run":
        job_id = str(event.get("job_id", ""))
        try:
            answer, agents_run = _serve(eco_id, message, history)
            status, err = "done", ""
        except Exception as e:
            answer, agents_run, status, err = "", [], "error", str(e)[:500]
        _ddb.put_item(TableName=JOB_TABLE, Item={
            **_job_key(job_id), "status": {"S": status}, "answer": {"S": answer or err},
            "agents_run": {"S": json.dumps(agents_run)},
            "ttl": {"N": str(int(time.time()) + 3600)}})
        return {"statusCode": 200, "body": "{}"}

    # ── start: create a job, fire the async worker, return the id ──────────────
    job_id = uuid.uuid4().hex
    _ddb.put_item(TableName=JOB_TABLE, Item={
        **_job_key(job_id), "status": {"S": "running"},
        "ttl": {"N": str(int(time.time()) + 3600)}})
    _lam.invoke(FunctionName=SELF_FN, InvocationType="Event",
                Payload=json.dumps({"mode": "run", "job_id": job_id,
                                    "eco_id": eco_id, "message": message, "history": history}).encode())
    return _ok({"job_id": job_id, "status": "running"})
