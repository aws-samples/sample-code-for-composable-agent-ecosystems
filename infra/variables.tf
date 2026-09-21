# ─────────────────────────────────────────────────────────────────────────────
# Core naming / targeting
# ─────────────────────────────────────────────────────────────────────────────

variable "project" {
  description = "Project name — used as the prefix for every resource name (e.g. <project>-<environment>-*)."
  type        = string
  default     = "tessera"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be lowercase alphanumeric/hyphen, starting with a letter, max 21 chars."
  }
}

variable "environment" {
  description = "Deployment environment. Drives resource names and per-env configuration."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "region" {
  description = "AWS region to deploy into. The account id is resolved at runtime from the active credentials."
  type        = string
  default     = "us-east-1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Networking
# ─────────────────────────────────────────────────────────────────────────────

variable "vpc_max_azs" {
  description = "Number of Availability Zones for the Lambda VPC."
  type        = number
  default     = 2
}

variable "vpc_nat_gateways" {
  description = "Number of NAT gateways for the private subnets (1 is cheapest; increase for HA)."
  type        = number
  default     = 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Bedrock model configuration (config-driven — no model IDs baked into code)
# ─────────────────────────────────────────────────────────────────────────────

variable "models" {
  description = "Bedrock model / inference-profile IDs used across the platform. Override per environment as models evolve."
  type = object({
    builder       = string # conversational ecosystem builder (highest leverage)
    leaf          = string # per-agent leaf model
    synthesizer   = string # catalog / risk synthesis
    classify      = string # file classification
    external_eval = string # external-grounding claim evaluation
    interactive   = string # interactive ecosystem serve engine
    concierge     = string # concierge websocket agent
    quorum        = string # quorum deliberation runtime
    embeddings    = string # memory embeddings model
  })
  default = {
    builder       = "us.anthropic.claude-opus-4-7"
    leaf          = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    synthesizer   = "us.anthropic.claude-opus-4-7"
    classify      = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    external_eval = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    interactive   = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    concierge     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    quorum        = "us.anthropic.claude-sonnet-5"
    embeddings    = "amazon.titan-embed-text-v2:0"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Notifications (replaces hardcoded email addresses from the original)
# ─────────────────────────────────────────────────────────────────────────────

variable "admin_notify_emails" {
  description = "Email addresses subscribed to the new-user signup-approval SNS topic. Empty = no subscriptions."
  type        = list(string)
  default     = []
}

variable "attention_email" {
  description = "Optional email auto-subscribed to the human-in-the-loop Attention SNS topic. Empty = none."
  type        = string
  default     = ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Feature flags
# ─────────────────────────────────────────────────────────────────────────────

variable "enable_quorum" {
  description = "Deploy the Quorum runtime and enable the quorum archetype on the control-plane Lambdas."
  type        = bool
  default     = true
}

variable "enable_websearch" {
  description = "Whether the web-search credential provider is expected to exist (AgentCore Identity Token Vault, created out-of-band)."
  type        = bool
  default     = true
}

variable "websearch_api_key_secret_name" {
  description = "Secrets Manager secret holding the raw web-search (Tavily) API key. Empty => defaults to '<project>-<env>-websearch-api-key'. Set the value out-of-band via 'make set-websearch-key' — never in tfvars."
  type        = string
  default     = ""
}

variable "allow_self_signup" {
  description = <<-EOT
    When true, the login screen shows a "Create Account" option and users can self-register
    (Cognito admin-create-only is disabled). Intended for dev/demo so colleagues can make
    their own test accounts. Keep false for staging/prod, where accounts are admin-created.
    Whether a self-registered user is usable immediately or must be approved by an admin is
    controlled separately by the post-confirmation trigger's SIGNUP_REQUIRE_APPROVAL env
    (default off = immediately usable).
  EOT
  type        = bool
  default     = false
}

variable "demo_open_access" {
  description = <<-EOT
    When true, all authenticated users can view and run EVERY ecosystem (via AVP policies),
    so self-registered colleagues immediately see the owner-scoped demo solutions in the
    picker. Dev/demo only — keep false for staging/prod, where per-owner and shared
    authorization must apply.
  EOT
  type        = bool
  default     = false
}

variable "signup_require_approval" {
  description = <<-EOT
    Only relevant when allow_self_signup = true. When true, a self-registered user is
    disabled until an admin enables the account (admins are notified via the signup SNS
    topic). When false (default), new sign-ups can log in immediately — convenient for
    quick colleague testing in dev.
  EOT
  type        = bool
  default     = false
}

variable "federated_login" {
  description = "Optional OIDC SSO (federated login) configuration. When enabled = false, only Cognito-local auth is used."
  type = object({
    enabled            = bool
    provider_name      = optional(string, "AmazonFederate")
    issuer             = optional(string, "https://idp.federate.amazon.com")
    client_id          = optional(string, "")
    client_secret_arn  = optional(string, "")
    default_group      = optional(string, "Users")
    create_users_group = optional(bool, false)
    # Domain for the synthesized email/name identity given to federated users (Amazon
    # Federate emits no email/name claim); used only as a UI display identity key.
    email_domain = optional(string, "amazon.com")
  })
  default = {
    enabled = false
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# AgentCore Runtime
# ─────────────────────────────────────────────────────────────────────────────

variable "agent_runtime_arn" {
  description = <<-EOT
    ARN of the Amazon Bedrock AgentCore Runtime. The runtime is deployed via the
    `agentcore` CLI (see scripts/deploy-agent.sh); its ARN is fed back here so the
    agent-invoker and related Lambdas can invoke it. Leave empty on the first apply —
    the deploy flow provisions the runtime, then re-applies with the resolved ARN.
  EOT
  type        = string
  default     = ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda defaults
# ─────────────────────────────────────────────────────────────────────────────

variable "lambda_runtime" {
  description = "Python runtime for all Lambda functions."
  type        = string
  default     = "python3.12"
}

variable "log_retention_days" {
  description = "Retention (days) for CloudWatch log groups managed by this project."
  type        = number
  default     = 30
}

variable "lambda_max_memory" {
  description = <<-EOT
    Upper bound (MB) applied to every Lambda's memory_size. Set this to your AWS account's
    per-function Lambda memory quota. Brand-new accounts default to 512 MB; request a
    Service Quotas increase (up to 10240) and raise this so functions get their intended
    memory. Leaving it at 10240 means "no clamp" for a quota-raised account.
  EOT
  type        = number
  default     = 10240
}

# ─────────────────────────────────────────────────────────────────────────────
# Catalog / ingestion
# ─────────────────────────────────────────────────────────────────────────────

variable "default_catalog_domain" {
  description = "Default catalog domain used by the ingest / catalog APIs."
  type        = string
  default     = "cyber-security-risk"
}
