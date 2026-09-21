# Web-search credential provider (AgentCore Identity Token Vault).
#
# NOTE: This is the MAIN platform stack's provider (the active plan path for a normal
# deploy). It reads the API key from Secrets Manager — set it with `make set-websearch-key`
# BEFORE the first `terraform plan` (the data source below reads at plan time). A separate,
# unrelated file `modules/eco-lite/credential-providers.tf` handles the exported-ecosystem
# ("eco-lite") deploy path and takes the key via a variable instead; it is NOT used here.
#
# Root cause of past outages: main.tf only NAMED the provider and wired it into the
# Lambdas (WEBSEARCH_CREDENTIAL_PROVIDER), but nothing provisioned it — creating the
# key was a manual out-of-band step that got skipped, silently breaking web_search and
# external-grounding. This makes it DECLARATIVE and idempotent: every apply (re)creates
# the provider from a Secrets Manager secret, so it can never be forgotten.
#
# Zero static secrets in repo/tfvars: the key lives ONLY in Secrets Manager (set once via
# `make set-websearch-key`), read here at apply time. The provider name matches
# local.websearch_provider_name exactly, so it can never drift from the Lambda env var.

data "aws_secretsmanager_secret_version" "websearch" {
  count     = var.enable_websearch ? 1 : 0
  secret_id = local.websearch_secret_id
}

resource "awscc_bedrockagentcore_api_key_credential_provider" "websearch" {
  count   = var.enable_websearch ? 1 : 0
  name    = local.websearch_provider_name
  api_key = data.aws_secretsmanager_secret_version.websearch[0].secret_string

  lifecycle {
    precondition {
      condition = (
        length(data.aws_secretsmanager_secret_version.websearch[0].secret_string) > 0 &&
        data.aws_secretsmanager_secret_version.websearch[0].secret_string != "REPLACE_ME_SEE_README" # pragma: allowlist secret — placeholder sentinel comparison, not a secret value
      )
      error_message = "enable_websearch=true but the web-search key in Secrets Manager secret '${local.websearch_secret_id}' is empty or a placeholder. Run `make set-websearch-key ENV=${var.environment}` to store the real key, then re-apply. To ship without web search, set enable_websearch=false."
    }
  }
}

output "websearch_provider_name" {
  description = "Name of the provisioned web-search credential provider (empty when disabled)."
  value       = var.enable_websearch ? local.websearch_provider_name : ""
}
