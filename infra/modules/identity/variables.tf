variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "project" {
  description = "Project name (used for resource-server identifier and client names)."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "account_id" {
  description = "AWS account id (resolved at runtime by the root module)."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "partition" {
  description = "AWS partition (aws, aws-cn, aws-us-gov)."
  type        = string
  default     = "aws"
}

variable "post_confirmation_lambda_arn" {
  description = "ARN of the post-confirmation trigger Lambda (from the compute module). Empty = no trigger wired."
  type        = string
  default     = ""
}

variable "allow_self_signup" {
  description = "When true, users can self-register from the login screen (admin-create-only is disabled). Intended for dev/demo; keep false for staging/prod."
  type        = bool
  default     = false
}

variable "demo_open_access" {
  description = <<-EOT
    When true, adds AVP policies granting EVERY authenticated user view + run on ALL
    ecosystems (not just platform ones). This makes owner-scoped demo solutions visible
    and runnable to self-registered colleagues without mislabeling them platform:true
    (which would hide them from the solution picker). Dev/demo only — do NOT enable for
    staging/prod, where per-owner/shared authorization must hold.
  EOT
  type        = bool
  default     = false
}

variable "frontend_url" {
  description = "Public frontend URL (CloudFront). Used as the OAuth callback/logout base when federated login is enabled."
  type        = string
  default     = ""
}

# Federated login config — same object shape as the root federated_login variable.
# When enabled, the frontend app client is configured for the hosted-UI OAuth code flow
# with the federated IdP, and the pool's PreSignUp / PreTokenGeneration triggers are wired
# to the (deterministically named) federated trigger Lambdas from the auth-federated module.
variable "federated_login" {
  description = "OIDC SSO configuration. enabled=false keeps the app client Cognito-local (SRP only)."
  type = object({
    enabled            = bool
    provider_name      = optional(string, "AmazonFederate")
    issuer             = optional(string, "https://idp.federate.amazon.com")
    client_id          = optional(string, "")
    client_secret_arn  = optional(string, "")
    default_group      = optional(string, "Users")
    create_users_group = optional(bool, false)
  })
  default = {
    enabled = false
  }
}

variable "data_key_arn" {
  type        = string
  description = "KMS CMK ARN for encrypting the gateway M2M Secrets Manager secret."
}
