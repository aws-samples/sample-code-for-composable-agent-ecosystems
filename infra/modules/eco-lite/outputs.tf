// terraform-tessera-eco :: outputs

output "runs_bucket" {
  value       = aws_s3_bucket.runs.bucket
  description = "S3 bucket holding run transcripts/artifacts."
}

output "cognito_user_pool_id" {
  value       = aws_cognito_user_pool.eco.id
  description = "Cognito user pool (direct login always available)."
}

output "cognito_client_id" {
  value       = aws_cognito_user_pool_client.eco.id
  description = "App client id."
}

output "agent_runtime_id" {
  value       = awscc_bedrockagentcore_runtime.eco.id
  description = "AgentCore Runtime id."
}

output "gateway_id" {
  value       = awscc_bedrockagentcore_gateway.eco.id
  description = "AgentCore Gateway id (tool front door)."
}

output "state_machine_arn" {
  value       = try(aws_sfn_state_machine.eco[0].arn, "")
  description = "Step Functions state machine ARN (orchestrated ecos)."
}

output "lambda_function_names" {
  value       = { for k, f in aws_lambda_function.fn : k => f.function_name }
  description = "Provisioned run-plane Lambdas (the BoM closure)."
}

output "ecr_repository_url" {
  value       = try(aws_ecr_repository.runtime[0].repository_url, "")
  description = "ECR repo for the runtime image (create_ecr=true) — target for build-and-push-runtime.sh."
}

output "vpc_id" {
  value       = local.vpc_id
  description = "Resolved VPC id (created or referenced)."
}

output "kms_key_arn" {
  value       = local.kms_key_arn
  description = "Resolved KMS key ARN (created or referenced)."
}

output "rest_api_url" {
  value       = aws_apigatewayv2_api.rest.api_endpoint
  description = "REST API base URL → NEXT_PUBLIC_API_URL for the frontend build."
}

output "ws_url" {
  value       = "wss://${aws_apigatewayv2_api.ws.id}.execute-api.${var.region}.amazonaws.com/prod"
  description = "WebSocket URL → NEXT_PUBLIC_WS_URL for the frontend build."
}

output "cognito_domain" {
  value       = "${aws_cognito_user_pool_domain.eco.domain}.auth.${var.region}.amazoncognito.com"
  description = "Cognito hosted-UI domain → NEXT_PUBLIC_COGNITO_DOMAIN."
}

output "frontend_url" {
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
  description = "CloudFront URL of the single-ecosystem UI."
}
