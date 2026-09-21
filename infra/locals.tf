locals {
  # Runtime-resolved account/region/partition — never hardcoded.
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  # Canonical name prefix used by every resource: e.g. "tessera-dev".
  name_prefix = "${var.project}-${var.environment}"

  # Web-search (AgentCore Identity) credential provider name — referenced BOTH by the
  # compute module (WEBSEARCH_CREDENTIAL_PROVIDER env var) and by the credential-provider
  # resource that provisions it, so the two can never drift.
  websearch_provider_name = "${local.name_prefix}-websearch"

  # Secrets Manager secret that holds the raw web-search API key. Created/rotated
  # out-of-band (make set-websearch-key) so the value never lives in the repo or tfvars.
  websearch_secret_id = coalesce(var.websearch_api_key_secret_name, "${local.name_prefix}-websearch-api-key")

  # Absolute path to the application source tree (app/) relative to the infra root.
  # Lambda packaging, the agent build, and content seeding all resolve from here.
  app_dir = abspath("${path.root}/../app")

  # Tags applied everywhere (in addition to provider default_tags).
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
