# Root outputs. These mirror the values operators need after a deploy (frontend URL,
# API endpoints, Cognito ids, bucket names, AgentCore ids). Populated as modules land.

output "account_id" {
  description = "AWS account the stack is deployed into (resolved at runtime)."
  value       = local.account_id
}

output "region" {
  description = "AWS region the stack is deployed into."
  value       = local.region
}

output "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  value       = local.name_prefix
}

# Foundation outputs
output "vpc_id" {
  description = "Lambda VPC id."
  value       = module.foundation.vpc_id
}

output "ecosystems_bucket_name" {
  description = "S3 bucket holding ecosystem YAML, domain descriptors, and catalog content."
  value       = module.foundation.ecosystems_bucket_name
}

output "runs_bucket_name" {
  description = "S3 bucket holding run artifacts."
  value       = module.foundation.runs_bucket_name
}

# Identity outputs
output "cognito_user_pool_id" {
  description = "Cognito user pool id."
  value       = module.identity.user_pool_id
}

output "cognito_client_id" {
  description = "Cognito frontend app client id."
  value       = module.identity.frontend_client_id
}

output "cognito_domain" {
  description = "Cognito hosted-UI domain host."
  value       = module.identity.cognito_domain_full
}

output "allow_self_signup" {
  description = "Whether self-registration is enabled. Drives NEXT_PUBLIC_ALLOW_SELF_SIGNUP (login screen Create Account)."
  value       = var.allow_self_signup
}

output "federated_login_enabled" {
  description = "Whether federated (OIDC SSO) login is enabled. Drives NEXT_PUBLIC_FEDERATED_LOGIN_ENABLED."
  value       = var.federated_login.enabled
}

output "federated_provider_name" {
  description = "Cognito OIDC provider name for the federated login button. Empty when disabled."
  value       = var.federated_login.enabled ? var.federated_login.provider_name : ""
}

output "policy_store_id" {
  description = "Verified Permissions policy store id."
  value       = module.identity.policy_store_id
}

output "gateway_m2m_token_url" {
  description = "OAuth token endpoint for the Gateway M2M client."
  value       = module.identity.gateway_m2m_token_url
}

output "gateway_m2m_client_id" {
  description = "Cognito M2M client id the agent runtime uses to fetch a Gateway JWT."
  value       = module.identity.gateway_m2m_client_id
}

output "gateway_m2m_scope" {
  description = "OAuth scope for the Gateway M2M client_credentials grant."
  value       = module.identity.gateway_m2m_scope
}

output "gateway_secret_arn" {
  description = "Secrets Manager ARN holding the Gateway M2M client secret."
  value       = module.identity.gateway_secret_arn
}

output "data_key_arn" {
  description = "KMS CMK ARN for data-at-rest (encrypts the M2M secret, DynamoDB, etc.). Used to grant the out-of-band AgentCore runtime role kms:Decrypt on the secret."
  value       = module.foundation.data_key_arn
}

# API outputs
output "rest_api_url" {
  description = "REST API invoke URL."
  value       = module.api.rest_api_url
}

output "websocket_url" {
  description = "WebSocket API invoke URL."
  value       = module.api.websocket_url
}

output "catalog_api_url" {
  description = "Catalog HTTP API endpoint."
  value       = module.orchestration.catalog_api_url
}

# AgentCore outputs
output "memory_id" {
  description = "AgentCore Memory id."
  value       = module.agentcore.memory_id
}

output "gateway_url" {
  description = "AgentCore Gateway MCP URL."
  value       = module.agentcore.gateway_url
}

output "guardrail_id" {
  description = "Bedrock grounding guardrail id."
  value       = module.agentcore.guardrail_id
}

output "workload_identity_name" {
  description = "Platform workload identity name (for the web-search credential provider)."
  value       = module.agentcore.workload_identity_name
}

# Frontend outputs
output "frontend_url" {
  description = "Public frontend URL (CloudFront)."
  value       = module.frontend.frontend_url
}

output "frontend_bucket_name" {
  description = "Frontend site S3 bucket name."
  value       = module.frontend.frontend_bucket_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidation)."
  value       = module.frontend.distribution_id
}
