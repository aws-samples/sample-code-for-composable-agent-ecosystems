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

variable "lambda_runtime" {
  type    = string
  default = "python3.12"
}
variable "log_retention_days" {
  type    = number
  default = 30
}

variable "lambda_max_memory" {
  description = "Upper bound (MB) clamped onto every Lambda's memory_size (account per-function memory quota)."
  type        = number
  default     = 10240
}

variable "lambdas_src_dir" {
  description = "Absolute path to app/lambdas (source)."
  type        = string
}
variable "build_dir" {
  description = "Absolute path to prebuilt Lambda packages (from scripts/package-lambdas.sh)."
  type        = string
}

variable "shared_layer_arn" {
  description = "Shared Lambda layer ARN (reused from the compute module)."
  type        = string
}

# Foundation
variable "lambda_env_key_arn" {
  type = string
}
variable "lambda_dlq_arn" {
  type = string
}
variable "vpc_private_subnet_ids" {
  type = list(string)
}
variable "lambda_security_group_id" {
  type = string
}
variable "ecosystems_bucket_name" {
  type = string
}
variable "ecosystems_bucket_arn" {
  type = string
}
variable "runs_bucket_name" {
  type = string
}
variable "runs_bucket_arn" {
  type = string
}

# Data
variable "catalog_refresh_table_name" {
  type = string
}
variable "catalog_refresh_table_arn" {
  type = string
}
variable "connections_table_name" {
  type = string
}
variable "connections_table_arn" {
  type = string
}
variable "regrounding_bus_name" {
  type = string
}
variable "regrounding_bus_arn" {
  type = string
}

# Identity
variable "cognito_user_pool_id" {
  type    = string
  default = ""
}
variable "cognito_client_id" {
  type    = string
  default = ""
}
variable "cognito_issuer_url" {
  type    = string
  default = ""
}
variable "policy_store_id" {
  type    = string
  default = ""
}

# AgentCore
variable "memory_id" {
  type    = string
  default = ""
}
variable "memory_arn" {
  type    = string
  default = ""
}

# API (for concierge-ws which attaches a route to the platform WebSocket API)
variable "websocket_api_id" {
  type    = string
  default = ""
}
variable "websocket_callback_url" {
  type    = string
  default = ""
}

# Config
variable "models" {
  type = map(string)
}
variable "allowed_origin" {
  description = "CORS allowed origin for the catalog HTTP API (CloudFront URL). '*' until frontend exists."
  type        = string
  default     = "*"
}
variable "default_catalog_domain" {
  type    = string
  default = "cyber-security-risk"
}
variable "enable_quorum" {
  type    = bool
  default = true
}

variable "logs_key_arn" {
  type        = string
  description = "KMS CMK ARN (logs key, with the CloudWatch Logs service grant) for encrypting API Gateway access-log groups."
}

variable "data_key_arn" {
  type        = string
  description = "KMS CMK ARN for data-at-rest (DynamoDB). Granted to Lambda roles that read/write the CMK-encrypted tables (e.g. catalog-refresh)."
}
