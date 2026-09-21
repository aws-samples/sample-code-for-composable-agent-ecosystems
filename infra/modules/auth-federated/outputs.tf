output "enabled" {
  description = "Whether federated login is enabled."
  value       = var.config.enabled
}

output "provider_name" {
  description = "OIDC provider name (empty when disabled)."
  value       = var.config.enabled ? var.config.provider_name : ""
}

output "presignup_arn" {
  value = try(aws_lambda_function.trigger["federated-presignup"].arn, "")
}

output "pretoken_arn" {
  value = try(aws_lambda_function.trigger["federated-pretoken"].arn, "")
}
