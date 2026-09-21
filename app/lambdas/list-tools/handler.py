"""List all tools — platform + ecosystem tools from S3."""
import os, json, boto3, yaml

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
# Amazon S3 client
s3 = boto3.client("s3")

def handler(event, context):
    tools = [
        {"name": "web_search", "description": "Search the web for current information", "type": "platform"},
        {"name": "generateUI", "description": "Render tables, charts, or cards", "type": "platform"},
    ]
    seen = {"web_search", "generateUI"}

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="ecosystems/")
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".yaml"): continue
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        config = yaml.safe_load(body)
        for agent in config.get("agents", []):
            for tool_name in agent.get("tools", []):
                if tool_name not in seen:
                    seen.add(tool_name)
                    tools.append({"name": tool_name, "description": "Ecosystem tool", "type": "ecosystem"})

    return {"statusCode": 200, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps(tools)}
