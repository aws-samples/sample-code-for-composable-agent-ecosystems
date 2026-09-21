# ── prod environment configuration ───────────────────────────────────────────
# Production uses 2 NAT gateways for AZ-level HA. Set real notification emails.

# SET THIS: replace "<your aws region>" with your target region (must match backend.hcl).
project     = "tessera"
environment = "prod"
region      = "<your aws region>"

vpc_max_azs      = 2
vpc_nat_gateways = 2

admin_notify_emails = []
attention_email     = ""

enable_quorum    = true
enable_websearch = true

# For production, prefer federated SSO. Fill in real values and set enabled = true.
federated_login = {
  enabled           = false
  provider_name     = "AmazonFederate"
  issuer            = "https://idp.federate.amazon.com"
  client_id         = ""
  client_secret_arn = ""
}

agent_runtime_arn = ""

log_retention_days     = 90
default_catalog_domain = "cyber-security-risk"
