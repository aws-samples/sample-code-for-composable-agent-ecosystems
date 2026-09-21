# ─────────────────────────────────────────────────────────────────────────────
# AgentCore Workload Identity + Gateway (AWSCC).
# ─────────────────────────────────────────────────────────────────────────────

# Workload identity for non-runtime callers (Lambdas) that read from the Token Vault.
resource "awscc_bedrockagentcore_workload_identity" "platform" {
  name = "${var.name_prefix}-platform"
}

# MCP Gateway with CUSTOM_JWT inbound auth (Cognito) and IAM outbound (invokes Lambda
# tool targets registered dynamically via the Tool Catalog).
resource "awscc_bedrockagentcore_gateway" "tools" {
  name          = "${var.name_prefix}-tools"
  description   = "Tessera tool catalog — MCP gateway"
  protocol_type = "MCP"
  role_arn      = aws_iam_role.gateway.arn

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration = {
    custom_jwt_authorizer = {
      discovery_url   = "${var.cognito_issuer_url}/.well-known/openid-configuration"
      allowed_clients = [var.gateway_m2m_client_id]
    }
  }
}
