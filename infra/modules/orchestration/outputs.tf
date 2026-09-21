output "catalog_api_url" {
  # Trailing slash is REQUIRED: the frontend builds catalog URLs by concatenating the base
  # with paths like "catalog/types" (no leading slash), so the base must end in "/". This
  # matches the CDK HttpApi.url the frontend was originally written against; the raw
  # api_endpoint has no trailing slash, which produced malformed URLs ("Failed to fetch").
  description = "Catalog HTTP API endpoint (with trailing slash)."
  value       = "${aws_apigatewayv2_api.catalog.api_endpoint}/"
}

output "quorum_run_function_name" {
  description = "Quorum runtime Lambda function name (empty if quorum disabled)."
  value       = var.enable_quorum ? aws_lambda_function.fn["quorum-run"].function_name : ""
}

output "catalog_refresh_state_machine_arn" {
  description = "cyber-security-risk refresh state machine ARN."
  value       = try(aws_sfn_state_machine.refresh["cyber-security-risk"].arn, "")
}
