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

variable "lambdas_src_dir" {
  type = string
}
variable "build_dir" {
  type = string
}

variable "user_pool_id" {
  description = "Existing Cognito user pool id to federate into."
  type        = string
}

variable "lambda_env_key_arn" {
  description = "KMS key ARN for Lambda environment variable encryption (CKV_AWS_173)."
  type        = string
}

variable "lambda_dlq_arn" {
  description = "Shared Lambda dead-letter queue ARN (CKV_AWS_116)."
  type        = string
}

# Federated login config (from the root federated_login object).
variable "config" {
  description = "OIDC SSO configuration."
  type = object({
    enabled            = bool
    provider_name      = optional(string, "AmazonFederate")
    issuer             = optional(string, "https://idp.federate.amazon.com")
    client_id          = optional(string, "")
    client_secret_arn  = optional(string, "")
    default_group      = optional(string, "Users")
    create_users_group = optional(bool, false)
    # Domain for the synthesized email/name identity the pre-token trigger gives federated
    # users (Amazon Federate emits no email/name claim). Only an identity key for the UI —
    # federated users authenticate via Federate, not via this address.
    email_domain = optional(string, "amazon.com")
  })
}
