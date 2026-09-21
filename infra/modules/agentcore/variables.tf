variable "name_prefix" {
  type = string
}
variable "project" {
  type = string
}
variable "environment" {
  type = string
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "account_id" {
  type = string
}
variable "region" {
  type = string
}
variable "partition" {
  type = string
}

# Foundation
variable "memory_payloads_bucket_name" {
  type = string
}
variable "memory_payloads_bucket_arn" {
  type = string
}

# Identity — for the Gateway's CUSTOM_JWT authorizer and the M2M client.
variable "cognito_issuer_url" {
  type = string
}
variable "gateway_m2m_client_id" {
  type = string
}

variable "models" {
  type = map(string)
}

# Guardrail thresholds (config-driven).
variable "grounding_threshold" {
  type    = number
  default = 0.7
}
variable "relevance_threshold" {
  type    = number
  default = 0.7
}

variable "event_expiry_days" {
  description = "Days after which memory events expire."
  type        = number
  default     = 30
}
