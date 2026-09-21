"""Tool Catalog CRUD — wraps Amazon Bedrock Agent Runtime Gateway target + Identity credential provider APIs."""
import os, json, boto3

GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

client = boto3.client("bedrock-agentcore-control", region_name=REGION)

CORS = {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"), "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}


def handler(event, context):
    method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}
    tool_name = path_params.get("name")

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}
    if not GATEWAY_ID:
        return resp({"error": "GATEWAY_ID not configured"}, 500)

    try:
        if method == "GET" and not tool_name:
            return list_tools()
        elif method == "GET" and tool_name:
            return get_tool(tool_name)
        elif method == "POST":
            body = json.loads(event.get("body", "{}"))
            if tool_name == "test":
                return test_connection(body)
            return create_tool(body)
        elif method == "PUT" and tool_name:
            body = json.loads(event.get("body", "{}"))
            return update_tool(tool_name, body)
        elif method == "DELETE" and tool_name:
            return delete_tool(tool_name)
        else:
            return resp({"error": "Not found"}, 404)
    except Exception as e:
        return resp({"error": str(e)[:500]}, 500)


def test_connection(body):
    """Make a test HTTP request to verify the tool endpoint + credentials work."""
    import urllib.request, urllib.error
    url = body.get("url", "")
    headers = body.get("headers", {})
    if not url:
        return resp({"status": "error", "body": "No URL provided"}, 400)
    if not url.startswith(("https://", "http://")):  # nosec B310 — scheme validated
        return resp({"status": "error", "body": "Only http/https URLs are allowed"}, 400)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310
            status = str(r.status)
            response_body = r.read().decode("utf-8", errors="replace")[:500]
        return resp({"status": status, "body": response_body})
    except urllib.error.HTTPError as e:
        return resp({"status": str(e.code), "body": e.read().decode("utf-8", errors="replace")[:300]})
    except Exception as e:
        return resp({"status": "error", "body": str(e)[:300]})


def list_tools():
    # Registered tools from Gateway
    result = client.list_gateway_targets(gatewayIdentifier=GATEWAY_ID)
    registered = [{
        "name": t.get("name", ""),
        "description": t.get("description", ""),
        "status": t.get("status", ""),
        "createdAt": str(t.get("createdAt", "")),
        "updatedAt": str(t.get("updatedAt", "")),
        "registered": True,
    } for t in result.get("items", [])]

    registered_names = {t["name"] for t in registered}

    # Scan ecosystem YAMLs for tool usage info
    import yaml as yamlmod
    s3 = boto3.client("s3")
    bucket = os.environ.get("ECOSYSTEMS_BUCKET", "")
    unregistered = {}
    tool_usage = {}  # name -> [eco/agent, ...]
    platform_tools = {"web_search", "generateUI", "memory_read", "memory_write", "memory_search", "calculator", "current_time", "http_request", "code_interpreter", "browser"}

    if bucket:
        try:
            objs = s3.list_objects_v2(Bucket=bucket, Prefix="ecosystems/")
            for obj in objs.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".yaml") or "-v1" in key:
                    continue
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                config = yamlmod.safe_load(body)
                eco_name = config.get("ecosystem", {}).get("name", key)
                for agent in config.get("agents", []):
                    for tool_name in agent.get("tools", []):
                        if tool_name in platform_tools:
                            continue
                        usage_key = f"{eco_name}/{agent['id']}"
                        tool_usage.setdefault(tool_name, []).append(usage_key)
                        if tool_name in registered_names:
                            continue
                        if tool_name not in unregistered:
                            unregistered[tool_name] = {"name": tool_name, "registered": False, "usedBy": [], "description": ""}
                        unregistered[tool_name]["usedBy"].append(usage_key)
                        # Extract purpose from agent prompt (first sentence mentioning the tool)
                        prompt = agent.get("prompt", "")
                        if not unregistered[tool_name]["description"] and tool_name in prompt:
                            for line in prompt.split("\n"):
                                if tool_name in line:
                                    unregistered[tool_name]["description"] = line.strip()[:120]
                                    break
        except Exception:
            pass

    # Add audit-based descriptions for known tools
    TOOL_PURPOSES = {
        "fair_value_model": ("DCF + comparable multiples weighted average valuation", "code_interpreter"),
        "scenario_simulator": ("Bull/base/bear probability-weighted return scenarios", "code_interpreter"),
        "var_calculator": ("Value-at-Risk (historical/parametric) at 95%/99%", "code_interpreter"),
        "correlation_calculator": ("Pearson correlation matrix from price series", "code_interpreter"),
        "drawdown_modeler": ("Max drawdown + recovery period from equity curve", "code_interpreter"),
        "position_sizer": ("Kelly criterion / risk-parity position sizing", "code_interpreter"),
        "historical_pattern_matcher": ("Pattern lookup + similarity scoring on price history", "code_interpreter"),
        "order_builder": ("Construct order spec (size, stops, take-profit targets)", "code_interpreter"),
        "execution_planner": ("TWAP/VWAP execution scheduling", "code_interpreter"),
        "historical_contrarian_scanner": ("Contrarian pattern lookup + similarity scoring", "code_interpreter"),
        "earnings_analyzer": ("Parse SEC EDGAR earnings filings", "http_request"),
        "balance_sheet_scanner": ("Parse SEC EDGAR balance sheet data", "http_request"),
        "guidance_parser": ("Parse SEC 8-K management guidance", "http_request"),
        "insider_activity_monitor": ("SEC EDGAR Form 4 insider transactions", "http_request"),
        "social_scanner": ("Social media sentiment scanning", "web_search"),
        "sector_rotation_tracker": ("Sector ETF rotation analysis (FRED + yfinance)", "http_request"),
        "rate_environment_analyzer": ("Interest rate environment from FRED", "http_request"),
        "contagion_mapper": ("Cross-asset correlation and contagion risk", "code_interpreter"),
        "counter_thesis_builder": ("Build contrarian thesis", "llm-only"),
        "market_data_api": ("Fetch stock prices (yfinance)", "http_request"),
        "probe_api": ("Simulated: probe agent API surface", "code_interpreter"),
        "map_trust_boundaries": ("Simulated: map trust boundaries", "code_interpreter"),
        "craft_injection": ("Simulated: craft prompt injection", "code_interpreter"),
        "attempt_exploit": ("Simulated: attempt exploit", "code_interpreter"),
        "lateral_move": ("Simulated: lateral movement", "code_interpreter"),
        "extract_data": ("Simulated: data extraction", "code_interpreter"),
        "monitor_behavior": ("Simulated: behavioral monitoring", "code_interpreter"),
        "score_deviation": ("Simulated: deviation scoring", "code_interpreter"),
        "revoke_tokens": ("Simulated: token revocation", "code_interpreter"),
        "isolate_agent": ("Simulated: agent isolation", "code_interpreter"),
        "block_memory": ("Simulated: memory blocking", "code_interpreter"),
        "update_guardrails": ("Simulated: guardrail update", "code_interpreter"),
        "patch_prompt": ("Simulated: prompt patching", "code_interpreter"),
        "deploy_countermeasure": ("Simulated: countermeasure deployment", "code_interpreter"),
        "alert_classifier": ("Classify alert type and severity", "llm-only"),
        "severity_scorer": ("Score incident severity (P1-P4)", "code_interpreter"),
        "blast_radius_mapper": ("Map affected systems from incident", "code_interpreter"),
        "log_analyzer": ("Analyze log entries for IOCs", "llm-only"),
        "ioc_extractor": ("Extract indicators of compromise", "code_interpreter"),
        "attack_path_tracer": ("Trace attack path through systems", "code_interpreter"),
        "timeline_builder": ("Build incident timeline", "code_interpreter"),
        "isolate_system": ("Simulated: system isolation", "code_interpreter"),
        "rotate_credentials": ("Simulated: credential rotation", "code_interpreter"),
        "block_ip": ("Simulated: IP blocking", "code_interpreter"),
        "revoke_sessions": ("Simulated: session revocation", "code_interpreter"),
        "draft_notification": ("Draft stakeholder notification", "llm-only"),
        "stakeholder_mapper": ("Identify notification stakeholders", "llm-only"),
        "regulatory_checker": ("Check regulatory disclosure requirements", "code_interpreter"),
        "backup_validator": ("Simulated: backup validation", "code_interpreter"),
        "restoration_planner": ("Plan recovery sequence", "llm-only"),
        "integrity_checker": ("Simulated: integrity check", "code_interpreter"),
        "root_cause_analyzer": ("Analyze root cause", "llm-only"),
        "gap_identifier": ("Identify security gaps", "llm-only"),
        "recommendation_generator": ("Generate remediation recommendations", "llm-only"),
    }
    FALLBACK_LABELS = {
        "code_interpreter": "Agent writes Python in code_interpreter",
        "http_request": "Agent calls API via http_request",
        "web_search": "Agent uses web_search",
        "llm-only": "Agent reasons without a tool",
    }
    for name, info in unregistered.items():
        if name in TOOL_PURPOSES:
            purpose, fallback_type = TOOL_PURPOSES[name]
            if not info["description"]:
                info["description"] = purpose
            info["fallback"] = FALLBACK_LABELS.get(fallback_type, "Agent improvises with platform tools")
        elif not info.get("fallback"):
            info["fallback"] = "Agent improvises with platform tools"

    # Add usedBy to registered tools
    for t in registered:
        t["usedBy"] = tool_usage.get(t["name"], [])

    return resp(registered + list(unregistered.values()))


def get_tool(name):
    # Gateway uses targetId internally; look up by name from the list
    result = client.list_gateway_targets(gatewayIdentifier=GATEWAY_ID)
    for t in result.get("items", []):
        if t.get("name") == name:
            target_id = t.get("targetId", "")
            detail = client.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=target_id)
            return resp(detail, default_serializer=True)
    return resp({"error": f"Tool '{name}' not found"}, 404)


def create_tool(body):
    name = body.get("name", "")
    description = body.get("description", "")
    target_type = body.get("type", "LAMBDA")  # LAMBDA | MCP_SERVER
    config = body.get("config", {})
    # Security: API key transits via HTTPS request body, is immediately stored in
    # AgentCore Identity Token Vault (encrypted at rest), and is never logged or
    # persisted in this Lambda. The key is not returned in any API response.
    credential = body.get("credential")  # {type: "API_KEY", name, apiKey, headerName} or None

    # Validate credential format before processing
    if credential:
        if not isinstance(credential, dict) or credential.get("type") != "API_KEY":
            return resp({"error": "credential.type must be 'API_KEY'"}, 400)
        if not credential.get("apiKey") or not credential.get("headerName"):
            return resp({"error": "credential requires apiKey and headerName"}, 400)

    if not name:
        return resp({"error": "name is required"}, 400)

    # If credential provided, create/update the credential provider first
    cred_provider_arn = None
    if credential and credential.get("type") == "API_KEY" and credential.get("apiKey"):
        cred_provider_arn = _ensure_credential_provider(credential)
        # Explicitly clear the secret from memory after storing in vault
        credential["apiKey"] = None
        del body["credential"]

    # Build targetConfiguration.mcp
    mcp_config = {}
    if target_type == "LAMBDA":
        tool_schema = body.get("schema") or {"type": "object", "properties": {"input": {"type": "string", "description": "Tool input"}}}
        mcp_config["lambda"] = {
            "lambdaArn": config.get("lambdaArn", ""),
            "toolSchema": {"inlinePayload": [{"name": name, "description": description or name, "inputSchema": tool_schema}]},
        }
    elif target_type == "MCP_SERVER":
        mcp_config["mcpServer"] = {"endpoint": config.get("mcpServerUrl", "")}
    elif target_type == "OPEN_API":
        mcp_config["openApiSchema"] = {"inlinePayload": config.get("openApiSpec", "")}

    # Credential provider configuration
    cred_configs = []
    if cred_provider_arn:
        api_key_config = {
            "providerArn": cred_provider_arn,
            "credentialParameterName": credential.get("headerName", "X-API-Key"),
            "credentialLocation": "HEADER",
        }
        if credential.get("prefix"):
            api_key_config["credentialPrefix"] = credential["prefix"]
        cred_configs.append({
            "credentialProviderType": "API_KEY",
            "credentialProvider": {"apiKeyCredentialProvider": api_key_config}
        })
    elif target_type == "LAMBDA":
        cred_configs.append({"credentialProviderType": "GATEWAY_IAM_ROLE"})

    params = {
        "gatewayIdentifier": GATEWAY_ID,
        "name": name,
        "description": description or f"Tool: {name}",
        "targetConfiguration": {"mcp": mcp_config},
        "credentialProviderConfigurations": cred_configs,
    }

    result = client.create_gateway_target(**params)
    return resp({"created": True, "name": name, "targetId": result.get("targetId", ""), "credentialProvider": credential.get("name") if credential else None}, 201)


def update_tool(name, body):
    # Look up targetId by name and fetch current config
    result = client.list_gateway_targets(gatewayIdentifier=GATEWAY_ID)
    target = None
    for t in result.get("items", []):
        if t.get("name") == name:
            target = t
            break
    if not target:
        return resp({"error": f"Tool '{name}' not found"}, 404)

    target_id = target["targetId"]
    existing = client.get_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=target_id)

    # If rotating key, update the credential provider
    credential = body.get("credential")
    if credential and credential.get("type") == "API_KEY" and credential.get("apiKey"):
        _ensure_credential_provider(credential)

    # Build update params — update_gateway_target requires name + targetConfiguration + credentialProviderConfigurations
    params = {
        "gatewayIdentifier": GATEWAY_ID,
        "targetId": target_id,
        "name": name,
        "description": body.get("description") or existing.get("description", ""),
        "targetConfiguration": existing.get("targetConfiguration", {}),
        "credentialProviderConfigurations": existing.get("credentialProviderConfigurations", []),
    }

    client.update_gateway_target(**params)
    return resp({"updated": True, "name": name})


def delete_tool(name):
    # Look up targetId by name
    result = client.list_gateway_targets(gatewayIdentifier=GATEWAY_ID)
    for t in result.get("items", []):
        if t.get("name") == name:
            client.delete_gateway_target(gatewayIdentifier=GATEWAY_ID, targetId=t["targetId"])
            return resp({"deleted": True, "name": name})
    return resp({"error": f"Tool '{name}' not found"}, 404)


def _ensure_credential_provider(credential):
    """Create or update an API key credential provider in AgentCore Identity Token Vault.
    Returns the provider ARN."""
    provider_name = credential.get("name", "")
    api_key = credential.get("apiKey", "")
    if not provider_name or not api_key:
        raise ValueError("credential.name and credential.apiKey are required")

    # Try update first (idempotent); if not found, create
    try:
        client.update_api_key_credential_provider(name=provider_name, apiKey=api_key)
    except Exception as e:
        if "not found" in str(e).lower() or "ResourceNotFound" in str(type(e).__name__):
            client.create_api_key_credential_provider(name=provider_name, apiKey=api_key)
        else:
            raise

    # Get the ARN
    info = client.get_api_key_credential_provider(name=provider_name)
    arn = info.get("credentialProviderArn", "")
    if not arn:
        # Construct from convention if API didn't return it
        import os
        account = os.environ.get("AWS_ACCOUNT_ID", "123456789012")
        arn = f"arn:aws:bedrock-agentcore:{REGION}:{account}:token-vault/default/apikeycredentialprovider/{provider_name}"
    return arn


def resp(data, status=200, default_serializer=False):
    body = json.dumps(data, default=str) if default_serializer else json.dumps(data)
    return {"statusCode": status, "headers": CORS, "body": body}
