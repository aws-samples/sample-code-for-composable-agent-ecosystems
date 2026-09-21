// terraform-tessera-eco :: tool credential providers (AgentCore Identity)
//
// NOTE: This belongs to the exported-ecosystem ("eco-lite") deploy path only — a
// standalone single-ecosystem export, NOT the main platform stack. It takes the
// web-search key directly via var.websearch_api_key. The MAIN stack's provider lives in
// the root `infra/credential-providers.tf` (reads the key from Secrets Manager). The two
// files are separate on purpose; do not confuse them.
//
// REAL, eco-agnostic, NO stubs. Provisioned only when the eco's BoM declares the tool.
// The web-search API key is a CUSTOMER-SUPPLIED PLACEHOLDER (var.websearch_api_key,
// default "REPLACE_ME_SEE_README") — the exporter provides their own key per the README.
//
// Uses the AWS Cloud Control (awscc) resource — AgentCore Identity credential providers
// are modelled there, not in the classic aws provider. AgentCore stores the key in a
// managed Secrets Manager secret (api_key_secret_arn is the computed output).

resource "awscc_bedrockagentcore_api_key_credential_provider" "websearch" {
  count   = var.provision_web_search ? 1 : 0
  name    = replace("${local.name}-websearch", "-", "_")
  api_key = var.websearch_api_key
}
