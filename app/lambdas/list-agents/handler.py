"""List all agents from all ecosystem YAMLs in S3."""
import os, json, boto3, yaml

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
# Amazon S3 client
s3 = boto3.client("s3")

def handler(event, context):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="ecosystems/")
    agents = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".yaml"): continue
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        config = yaml.safe_load(body)
        ecosystem_name = config.get("ecosystem", {}).get("name", key.split("/")[-1].replace(".yaml", ""))
        for agent in config.get("agents", []):
            agents.append({
                "agent_id": agent.get("id"),
                "name": agent.get("name", agent.get("id")),
                "ecosystem": ecosystem_name,
                "swarm": agent.get("swarm", ""),
                "tools": agent.get("tools", []),
                "depends_on": agent.get("depends_on", []),
                "status": "registered",
            })
    return {"statusCode": 200, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps(agents)}
