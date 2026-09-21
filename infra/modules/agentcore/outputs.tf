output "memory_id" {
  description = "AgentCore Memory id."
  value       = awscc_bedrockagentcore_memory.main.memory_id
}

output "memory_arn" {
  description = "AgentCore Memory ARN."
  value       = awscc_bedrockagentcore_memory.main.memory_arn
}

output "memory_name" {
  value = awscc_bedrockagentcore_memory.main.name
}

output "guardrail_id" {
  description = "Bedrock grounding guardrail id."
  value       = aws_bedrock_guardrail.grounding.guardrail_id
}

output "workload_identity_arn" {
  value = awscc_bedrockagentcore_workload_identity.platform.workload_identity_arn
}

output "workload_identity_name" {
  value = "${var.name_prefix}-platform"
}

output "gateway_id" {
  value = awscc_bedrockagentcore_gateway.tools.gateway_identifier
}

output "gateway_url" {
  value = awscc_bedrockagentcore_gateway.tools.gateway_url
}

output "gateway_arn" {
  value = awscc_bedrockagentcore_gateway.tools.gateway_arn
}

output "memory_execution_role_arn" {
  value = aws_iam_role.memory_execution.arn
}

output "gateway_role_arn" {
  value = aws_iam_role.gateway.arn
}
