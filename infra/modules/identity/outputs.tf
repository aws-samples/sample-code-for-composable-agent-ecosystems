# ── Cognito ──
output "user_pool_id" {
  description = "Cognito user pool id."
  value       = aws_cognito_user_pool.main.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.main.arn
}

output "user_pool_endpoint" {
  description = "Cognito user pool endpoint (issuer host)."
  value       = aws_cognito_user_pool.main.endpoint
}

output "frontend_client_id" {
  description = "Frontend (SPA) app client id."
  value       = aws_cognito_user_pool_client.frontend.id
}

output "cognito_domain_prefix" {
  description = "Cognito hosted-UI domain prefix."
  value       = aws_cognito_user_pool_domain.main.domain
}

output "cognito_domain_full" {
  description = "Full Cognito hosted-UI domain host."
  value       = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "gateway_m2m_client_id" {
  description = "Gateway M2M client id."
  value       = aws_cognito_user_pool_client.gateway_m2m.id
}

output "gateway_m2m_token_url" {
  description = "OAuth token endpoint for the M2M client."
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com/oauth2/token"
}

output "gateway_m2m_scope" {
  description = "OAuth scope for the M2M client."
  value       = "${var.project}-gateway/invoke"
}

output "gateway_secret_arn" {
  description = "Secrets Manager ARN holding the M2M client secret."
  value       = aws_secretsmanager_secret.gateway_m2m.arn
}

output "cognito_issuer_url" {
  description = "Cognito OIDC issuer URL."
  value       = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

# ── Verified Permissions ──
output "policy_store_id" {
  description = "Verified Permissions policy store id."
  value       = aws_verifiedpermissions_policy_store.main.policy_store_id
}

output "policy_store_arn" {
  description = "Verified Permissions policy store ARN."
  value       = aws_verifiedpermissions_policy_store.main.arn
}
