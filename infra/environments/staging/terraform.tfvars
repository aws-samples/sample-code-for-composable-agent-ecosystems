# ── staging environment configuration ────────────────────────────────────────

# SET THIS: replace "<your aws region>" with your target region (must match backend.hcl).
project     = "tessera"
environment = "staging"
region      = "<your aws region>"

vpc_max_azs      = 2
vpc_nat_gateways = 1

admin_notify_emails = []
attention_email     = ""

enable_quorum    = true
enable_websearch = true

federated_login = {
  enabled = false
}

agent_runtime_arn = ""

log_retention_days     = 30
default_catalog_domain = "cyber-security-risk"
