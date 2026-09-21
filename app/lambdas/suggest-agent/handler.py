"""AI generates agent config via Amazon Bedrock."""
import os, json, boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")

PROMPT_TEMPLATE = """Design a single agent for the Tessera platform ecosystem "{ecosystem_context}".

Description: {description}

The agent's prompt MUST include:
1. Clear role identity and expertise
2. Specific output format with section headers
3. Quality constraints (cite evidence, no speculation, be concise)
4. Instructions on how to use its tools effectively

Respond with ONLY valid JSON (no markdown fences) for one agent with these fields:
- id (snake_case identifier)
- name (display name)
- swarm (optional group name)
- tools (list of tool name strings)
- depends_on (list of upstream agent ids, can be empty)
- prompt (detailed system prompt, 4-6 sentences with output format and constraints)"""


def handler(event, context):
    body = json.loads(event["body"])
    description = body.get("description", "")
    ecosystem_context = body.get("ecosystem_context", "")
    if not description:
        return {"statusCode": 400, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps({"error": "description required"})}

    # Amazon Bedrock Runtime client
    client = boto3.client("bedrock-runtime", region_name=REGION)
    prompt = PROMPT_TEMPLATE.format(description=description, ecosystem_context=ecosystem_context)

    resp = client.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}),
    )
    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = "\n".join(text.split("\n")[:-1])

    try:
        agent = json.loads(text)
    except Exception:
        agent = None

    return {"statusCode": 200, "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")}, "body": json.dumps({"agent": agent})}
