// terraform-tessera-eco :: variables
// The single-ecosystem ("eco-lite") deployment surface. Everything a customer must
// supply is a variable — nothing hard-coded to Tessera (spec §8.2). Values are rendered
// from the ecosystem's Bill of Materials by the export generator (spec §6).

// ── Identity ────────────────────────────────────────────────────────────────────────
variable "eco_id" {
  type        = string
  description = "Ecosystem id (used to name resources)."
}

variable "eco_version" {
  type        = string
  description = "Pinned, content-addressed ecosystem version being deployed."
}

variable "region" {
  type        = string
  description = "Target AWS region. Must support AgentCore (see spec §10 region gate)."
}

variable "tags" {
  type        = map(string)
  description = "Mandatory resource tags (customer tagging policy)."
  default     = {}
}

// ── Networking (D10: single 'standard' private profile) ───────────────────────────────
// Create-or-reference: set create_networking=true to have the module stand up a dedicated
// VPC (2 private + 2 public subnets, 1 NAT), or false to attach to an existing VPC.
variable "create_networking" {
  type        = bool
  default     = true
  description = "true → module creates a dedicated VPC; false → reference vpc_id/private_subnet_ids."
}

variable "vpc_cidr" {
  type        = string
  default     = "10.20.0.0/16"
  description = "CIDR for the created VPC (create_networking=true)."
}

variable "vpc_id" {
  type        = string
  default     = ""
  description = "Existing VPC id (required when create_networking=false)."
}

variable "private_subnet_ids" {
  type        = list(string)
  default     = []
  description = "Existing private subnets >=2 AZs (required when create_networking=false)."
}

variable "create_kms" {
  type        = bool
  default     = true
  description = "true → module creates a customer-managed KMS key; false → reference kms_key_arn."
}

variable "kms_key_arn" {
  type        = string
  default     = ""
  description = "Existing CMK ARN for encryption at rest (required when create_kms=false)."
}

// ── Orchestration ─────────────────────────────────────────────────────────────────────
variable "strategy" {
  type        = string
  description = "orchestrated | quorum | choreography (from the BoM)."
}

variable "state_machine_definition" {
  type        = string
  description = "Compiled Step Functions ASL (orchestrated ecos). Empty for quorum."
  default     = ""
}

// ── Runtime / models ──────────────────────────────────────────────────────────────────
variable "runtime_image_uri" {
  type        = string
  default     = ""
  description = "Pinned AgentCore runtime container image (customer ECR, by digest)."
}

variable "model_ids" {
  type        = map(string)
  description = "Model IDs by role, e.g. { synthesizer = \"...\", leaf = \"...\" }. Must exist in region."
}

// ── Lambda closure (spec §9) ──────────────────────────────────────────────────────────
variable "lambdas" {
  type = map(object({
    s3_key  = string # prebuilt, pinned artifact in artifacts_bucket (customer builds/scans)
    handler = optional(string, "handler.handler")
    timeout = optional(number, 120)
    memory  = optional(number, 512)
  }))
  description = "Run-plane Lambdas to provision, keyed by name (BoM lambda closure)."
}

variable "create_artifacts_bucket" {
  type        = bool
  default     = true
  description = "true → module creates the Lambda-artifacts bucket; false → reference artifacts_bucket."
}

variable "artifacts_bucket" {
  type        = string
  default     = ""
  description = "S3 bucket holding the prebuilt Lambda zips (required when create_artifacts_bucket=false)."
}

variable "create_ecr" {
  type        = bool
  default     = true
  description = "true → module creates an ECR repo for the runtime image (build-and-push script pushes to it)."
}

// ── web_search tool (spec §5.4 / §10): real credential provider, customer-supplied key ──
variable "provision_web_search" {
  type        = bool
  default     = false
  description = "Provision an AgentCore Identity API-key credential provider for web_search (set by the generator when the eco uses web_search)."
}

variable "websearch_api_key" {
  type        = string
  default     = "REPLACE_ME_SEE_README"
  sensitive   = true
  description = "PLACEHOLDER — the exporter/customer supplies their own web-search API key (see README). Not shipped by Tessera."
}

variable "websearch_api_url" {
  type        = string
  default     = "https://api.tavily.com/search"
  description = "Web-search API endpoint the runtime calls."
}

// ── Quality ───────────────────────────────────────────────────────────────────────────
variable "grounding_enabled" {
  type        = bool
  default     = false
  description = "Provision a Bedrock Guardrail for contextual grounding."
}

// ── Tools (spec §5.4) ─────────────────────────────────────────────────────────────────
variable "business_tools" {
  type        = list(string)
  description = "Business (Gateway-target) tool names to register. Demo backends for v1 (O1)."
  default     = []
}

variable "tool_secret_arns" {
  type        = map(string)
  description = "Customer-supplied secret ARNs for tool credential providers (name -> ARN)."
  default     = {}
  sensitive   = true
}

// ── Auth (D11: dual auth + role allow-list) ───────────────────────────────────────────
variable "federated_idp" {
  type = object({
    enabled       = bool
    provider_name = optional(string, "CorporateSSO")
    oidc_issuer   = optional(string, "")
    client_id     = optional(string, "")
    secret_arn    = optional(string, "") # never a literal secret — an ARN
  })
  description = "Optional federated (BYO-OIDC/SAML) IdP. Cognito direct login is always on."
  default     = { enabled = false }
}

variable "allowed_groups" {
  type        = list(string)
  description = "IdP/Cognito groups permitted to run this ecosystem (default restrictive)."
  default     = []
}

variable "allow_all_authenticated" {
  type        = bool
  default     = false
  description = "Explicit opt-in: allow ANY authenticated user (the 'after confirmation' switch)."
}

// ── Teardown / decommissioning (spec §14.1) ───────────────────────────────────────────
variable "force_destroy_data" {
  type        = bool
  default     = false
  description = "Allow `terraform destroy` to delete the (non-empty) runs bucket + data. Keep FALSE in prod (destroy then safely fails until data is exported/purged); set TRUE for demo/dev teardown."
}

variable "log_retention_days" {
  type        = number
  default     = 30
  description = "CloudWatch log retention for run-plane Lambdas (log groups are TF-managed so destroy removes them — no orphans)."
}

variable "cognito_deletion_protection" {
  type        = string
  default     = "INACTIVE"
  description = "ACTIVE in prod (blocks accidental pool deletion); INACTIVE lets teardown remove the pool."
}

variable "cloudfront_acm_certificate_arn" {
  type        = string
  default     = ""
  description = "Optional us-east-1 ACM certificate ARN for a custom domain on the eco frontend CloudFront distribution. When set (with cloudfront_aliases), CloudFront presents this cert and enforces TLSv1.2_2021; when empty, the default CloudFront certificate is used."
}

variable "cloudfront_aliases" {
  type        = list(string)
  default     = []
  description = "Optional custom domain name(s) (CNAMEs) for the eco frontend CloudFront distribution."
}
