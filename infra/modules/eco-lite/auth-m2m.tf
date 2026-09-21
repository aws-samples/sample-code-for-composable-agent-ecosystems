// terraform-tessera-eco :: AgentCore Gateway M2M auth (generic, all ecos)
//
// The runtime authenticates to its Gateway with an OAuth2 client_credentials (M2M) token
// minted from Cognito, validated by the Gateway's CUSTOM_JWT authorizer. This wiring is
// eco-agnostic: it lets the runtime DISCOVER whatever targets are registered on the Gateway
// (zero or many) without erroring. Secret is the raw M2M client secret (the runtime's
// _get_gateway_secret() uses the SecretString directly as the client_secret).

resource "aws_cognito_user_pool_domain" "eco" {
  domain       = substr(replace("${local.name}-${data.aws_caller_identity.current.account_id}", "_", "-"), 0, 63)
  user_pool_id = aws_cognito_user_pool.eco.id
}

resource "aws_cognito_resource_server" "gateway" {
  identifier   = "tessera-gateway"
  name         = "tessera-gateway"
  user_pool_id = aws_cognito_user_pool.eco.id
  scope {
    scope_name        = "invoke"
    scope_description = "Invoke the AgentCore Gateway (MCP tools)"
  }
}

resource "aws_cognito_user_pool_client" "m2m" {
  name                                 = "${local.name}-gateway-m2m"
  user_pool_id                         = aws_cognito_user_pool.eco.id
  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = ["tessera-gateway/invoke"]
  allowed_oauth_flows_user_pool_client = true
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  depends_on                           = [aws_cognito_resource_server.gateway]
}

resource "aws_secretsmanager_secret" "gateway_m2m" {
  #checkov:skip=CKV2_AWS_57:Holds a Cognito M2M app-client secret whose lifecycle is owned by the Cognito user-pool client, not an independently rotatable credential — Secrets Manager auto-rotation would desync it from Cognito. Rotation is handled by rotating the Cognito client. Accepted risk.
  name                    = "${local.name}-gateway-m2m"
  recovery_window_in_days = 0 // immediate delete for clean teardown
  kms_key_id              = local.kms_key_arn
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "gateway_m2m" {
  secret_id     = aws_secretsmanager_secret.gateway_m2m.id
  secret_string = aws_cognito_user_pool_client.m2m.client_secret // raw secret (runtime uses as-is)
}
