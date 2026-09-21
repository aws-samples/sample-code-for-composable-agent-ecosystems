variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "project" {
  description = "Project name."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}

variable "account_id" {
  description = "AWS account id (runtime-resolved)."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "partition" {
  description = "AWS partition (e.g. aws)."
  type        = string
}

variable "lambda_runtime" {
  description = "Python runtime for all Lambda functions."
  type        = string
  default     = "python3.12"
}

variable "log_retention_days" {
  description = "CloudWatch log group retention (days)."
  type        = number
  default     = 30
}

variable "lambda_max_memory" {
  description = <<-EOT
    Upper bound (MB) clamped onto every Lambda's memory_size. Set to your account's
    per-function memory quota. New AWS accounts default to 512; request a Service Quotas
    increase (up to 10240) and raise this to let functions use their intended memory.
  EOT
  type        = number
  default     = 10240
}

# ── Paths ──
variable "lambdas_src_dir" {
  description = "Absolute path to the app/lambdas directory (Lambda source)."
  type        = string
}

variable "build_dir" {
  description = "Absolute path to the directory holding pre-built Lambda packages (from scripts/package-lambdas.sh). When a package zip exists here it is used; otherwise the raw source dir is zipped."
  type        = string
}

# ── Foundation inputs ──
variable "lambda_env_key_arn" {
  description = "KMS key ARN for Lambda env encryption."
  type        = string
}

variable "logs_key_arn" {
  description = "KMS key ARN for CloudWatch Logs encryption."
  type        = string
}

variable "lambda_dlq_arn" {
  description = "Shared Lambda DLQ ARN."
  type        = string
}

variable "vpc_private_subnet_ids" {
  description = "Private subnet ids for VPC-attached Lambdas."
  type        = list(string)
}

variable "lambda_security_group_id" {
  description = "Security group id for VPC-attached Lambdas."
  type        = string
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
variable "exports_bucket_name" {
  type = string
}
variable "exports_bucket_arn" {
  type = string
}

# ── Data inputs ──
variable "connections_table_name" {
  type = string
}
variable "connections_table_arn" {
  type = string
}
variable "attention_table_name" {
  type = string
}
variable "attention_table_arn" {
  type = string
}
variable "regrounding_bus_name" {
  type = string
}

# ── Identity inputs ──
variable "user_pool_id" {
  type = string
}
variable "user_pool_arn" {
  type = string
}
variable "policy_store_id" {
  type = string
}
variable "policy_store_arn" {
  type = string
}

# ── AgentCore inputs ──
variable "agent_runtime_arn" {
  description = "AgentCore Runtime ARN (may be empty on first apply)."
  type        = string
  default     = ""
}
variable "memory_id" {
  type    = string
  default = ""
}
variable "memory_arn" {
  type    = string
  default = ""
}
variable "guardrail_id" {
  type    = string
  default = ""
}

# ── Config-driven values (replacing original hardcoding) ──
variable "models" {
  description = "Bedrock model IDs used across Lambdas."
  type        = map(string)
}

variable "signup_require_approval" {
  description = "When true, a self-registered user is disabled until an admin enables them (and admins are notified via SNS). When false, new sign-ups are usable immediately. Only relevant when self-signup is enabled."
  type        = bool
  default     = false
}

variable "demo_open_access" {
  description = "When true, any authenticated user can view AND operate (list, view, resume, redrive, rate, guidance) runs of any ecosystem via runs-api — the owner check is relaxed. Mirrors the AVP demo policy for the ecosystems list. Dev/demo only; does NOT grant admin (user mgmt, ecosystem edit/delete stay admin/owner-only)."
  type        = bool
  default     = false
}

variable "admin_notify_emails" {
  description = "Emails subscribed to the signup-approval SNS topic."
  type        = list(string)
  default     = []
}

variable "attention_email" {
  description = "Optional email auto-subscribed to the attention SNS topic."
  type        = string
  default     = ""
}

variable "websearch_provider_name" {
  description = "AgentCore Identity web-search credential provider name."
  type        = string
}

variable "quorum_run_function_name" {
  description = "Deterministic name of the quorum-run Lambda (in the orchestration/quorum stack)."
  type        = string
}

variable "enable_quorum" {
  type    = bool
  default = true
}

variable "frontend_cors_origin" {
  description = "CloudFront origin used to restrict CORS on user-facing Lambdas. Empty on first apply (before CloudFront exists); wired from the frontend module afterwards."
  type        = string
  default     = "*"
}

# ── API inputs (api → compute direction: compute owns the functions, so it creates the
#    invoke permissions and receives the WebSocket callback URL) ──
variable "rest_api_id" {
  description = "REST API id (for Lambda invoke permissions)."
  type        = string
  default     = ""
}

variable "websocket_api_id" {
  description = "WebSocket API id (for the wildcard invoke permission)."
  type        = string
  default     = ""
}

variable "websocket_authorizer_id" {
  description = "WebSocket $connect authorizer id (for the ws-authorizer invoke permission source_arn)."
  type        = string
  default     = ""
}

variable "websocket_callback_url" {
  description = "WebSocket management (callback) endpoint set as WS_ENDPOINT on WS-pushing Lambdas."
  type        = string
  default     = ""
}

variable "rest_function_keys" {
  description = "Set of short function keys referenced by REST routes (granted API GW invoke)."
  type        = set(string)
  default     = []
}

variable "data_key_arn" {
  type        = string
  description = "KMS CMK ARN for data-at-rest (DynamoDB). Granted to Lambda roles that read/write the CMK-encrypted tables."
}
