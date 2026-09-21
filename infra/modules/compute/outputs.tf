# Maps of function name (short key, e.g. "list-ecosystems") → attributes, for the API
# and other modules to consume.
output "function_arns" {
  description = "Map of short name → Lambda function ARN."
  value       = { for k, fn in aws_lambda_function.this : k => fn.arn }
}

output "function_invoke_arns" {
  description = "Map of short name → Lambda invoke ARN (for API Gateway integrations)."
  value       = { for k, fn in aws_lambda_function.this : k => fn.invoke_arn }
}

output "function_names" {
  description = "Map of short name → full Lambda function name."
  value       = { for k, fn in aws_lambda_function.this : k => fn.function_name }
}

output "post_confirmation_arn" {
  description = "Post-confirmation trigger Lambda ARN (for the Cognito pool trigger)."
  value       = try(aws_lambda_function.this["post-confirmation"].arn, "")
}

output "shared_layer_arn" {
  description = "Shared Lambda layer ARN."
  value       = aws_lambda_layer_version.shared.arn
}

output "attention_topic_arn" {
  description = "Attention SNS topic ARN."
  value       = aws_sns_topic.attention.arn
}

output "signup_topic_arn" {
  description = "Signup-approval SNS topic ARN."
  value       = aws_sns_topic.signup_approvals.arn
}

output "sfn_invoke_role_arn" {
  description = "IAM role ARN that Step Functions assumes to invoke the agent-invoker / grounding Lambdas."
  value       = aws_iam_role.sfn.arn
}

output "agent_invoker_arn" {
  value = try(aws_lambda_function.this["agent-invoker"].arn, "")
}
output "grounding_check_arn" {
  value = try(aws_lambda_function.this["grounding-check"].arn, "")
}
output "external_grounding_arn" {
  value = try(aws_lambda_function.this["external-grounding"].arn, "")
}
