"""List all ecosystems from Amazon S3, filtered by Amazon Verified Permissions authorization."""
import os, sys, json, boto3, yaml
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import require_auth, is_admin

# Non-sensitive resource identifiers (not secrets — safe in env vars)
BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID", "")
# Amazon S3 client
s3 = boto3.client("s3")
# Amazon Verified Permissions (AVP) client
avp = boto3.client("verifiedpermissions") if POLICY_STORE_ID else None


def _get_yaml(key):
    """Fetch + parse one YAML object from the bucket. Returns None on any error.
    Used with a thread pool so the many per-object S3 GETs run concurrently."""
    try:
        return yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return None

def handler(event, context):
    try:
        user = require_auth(event)
    except PermissionError:
        return {"statusCode": 401, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps({"error": "Unauthorized"})}

    user_id = user["user_id"]
    user_groups = user.get("groups", [])
    is_admin_user = is_admin(user)

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="ecosystems/")
    # Domain id -> friendly name, so the ecosystems page can group/badge by DOMAIN
    # (replaces the old free-text `category`). Loaded once per request.
    #
    # Read the domain + ecosystem YAML objects CONCURRENTLY. This list previously read
    # every object with a sequential s3.get_object loop (~40 serial round-trips =>
    # 10-20s grid loads); a thread pool collapses that to roughly one round-trip.
    domain_keys = []
    try:
        dresp = s3.list_objects_v2(Bucket=BUCKET, Prefix="domains/")
        domain_keys = [o["Key"] for o in (dresp.get("Contents", []) or []) if o["Key"].endswith(".yaml") and "/" not in o["Key"][len("domains/"):]]
    except Exception:
        pass
    eco_keys = [o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith(".yaml") and "/_" not in o["Key"]]

    with ThreadPoolExecutor(max_workers=16) as pool:
        domain_configs = list(pool.map(_get_yaml, domain_keys))
        eco_pairs = list(zip(eco_keys, pool.map(_get_yaml, eco_keys)))

    dom_names = {}
    for dd in domain_configs:
        if isinstance(dd, dict) and dd.get("domain"):
            dom_names[dd["domain"]] = dd.get("name") or dd["domain"]

    all_ecosystems = []
    for key, config in eco_pairs:
        if not config or not isinstance(config, dict): continue
        eco = config.get("ecosystem") or {}
        if not isinstance(eco, dict): eco = {}
        eco_id = key.split("/")[-1].replace(".yaml", "")
        _domains = config.get("domains", []) or []
        _primary = dom_names.get(_domains[0], _domains[0]) if _domains else "Unassigned"
        all_ecosystems.append({
            "id": eco_id,
            "name": eco.get("name", ""),
            "description": eco.get("description", ""),
            "agents": len(config.get("agents", [])),
            "strategy": config.get("strategy", "orchestrated"),
            "inputs": eco.get("inputs", []),
            "owner": config.get("owner", ""),
            "platform": bool(config.get("platform", False)),
            # Domain replaces category: `domain` = primary domain's friendly name
            # (for grouping/badge), `domains` = all mounted domain ids.
            "domain": _primary,
            "domains": _domains,
            "tags": config.get("tags", []) or [],
        })

    # Admin sees everything
    if is_admin_user:
        return _ok(all_ecosystems)

    # Filter by AVP authorization (BatchIsAuthorized)
    if avp and POLICY_STORE_ID:
        visible = _filter_by_avp(all_ecosystems, user_id, user_groups)
    else:
        # Fallback: owner + platform only
        visible = [e for e in all_ecosystems if e["owner"] == user_id or e["platform"]]

    return _ok(visible)


def _filter_by_avp(ecosystems, user_id, user_groups):
    """Use BatchIsAuthorized to check view permission on each ecosystem."""
    visible = []
    batch_size = 30
    for i in range(0, len(ecosystems), batch_size):
        batch = ecosystems[i:i + batch_size]

        # Build shared entities context
        entity_list = [
            {
                "identifier": {"entityType": "Tessera::User", "entityId": user_id},
                "parents": [{"entityType": "Tessera::Group", "entityId": g} for g in user_groups],
            },
        ]
        for eco in batch:
            entity_list.append({
                "identifier": {"entityType": "Tessera::Ecosystem", "entityId": eco["id"]},
                "attributes": {"platform": {"boolean": eco["platform"]}},
            })

        # Build per-ecosystem authorization requests
        requests = []
        for eco in batch:
            requests.append({
                "principal": {"entityType": "Tessera::User", "entityId": user_id},
                "action": {"actionType": "Tessera::Action", "actionId": "view"},
                "resource": {"entityType": "Tessera::Ecosystem", "entityId": eco["id"]},
            })

        try:
            resp = avp.batch_is_authorized(
                policyStoreId=POLICY_STORE_ID,
                requests=requests,
                entities={"entityList": entity_list},
            )
            for j, result in enumerate(resp.get("results", [])):
                if result.get("decision") == "ALLOW":
                    visible.append(batch[j])
        except Exception as e:
            # Fallback on AVP error: show owner + platform
            print(f"[list-ecosystems] BatchIsAuthorized failed: {e}")
            for eco in batch:
                if eco["owner"] == user_id or eco["platform"]:
                    visible.append(eco)

    return visible


def _ok(data):
    return {"statusCode": 200, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps(data)}
