# ── dev environment configuration ────────────────────────────────────────────
# Account id is resolved at runtime from your AWS credentials; the region is named here
# and MUST match backend.hcl's region for the state bucket.
# SET THIS: replace "<your aws region>" with your target region, e.g. "us-east-1".

project     = "tessera"
environment = "dev"
region      = "<your aws region>"

# Networking — single NAT gateway is cheapest for a dev account.
vpc_max_azs      = 2
vpc_nat_gateways = 1

# Notifications — set to real addresses to receive signup-approval / attention emails.
admin_notify_emails = []
attention_email     = ""

# Feature flags
enable_quorum    = true
enable_websearch = true

# Self-registration DISABLED (admin-create-only) — remediates the security finding
# "Cognito User Pools with self-registration enabled". With this false, the user pool
# sets AllowAdminCreateUserOnly=true, so accounts can ONLY be created by an admin
# (make create-admin / admin console) — no unauthorized self-signup from the login screen.
allow_self_signup = false
# Demo open access (dev/demo): every authenticated user can see + run ALL ecosystems, so
# self-registered colleagues see the demo solutions in the picker. Keep false for prod.
demo_open_access = true
# false = new sign-ups can log in immediately (best for quick testing).
# true  = new sign-ups are disabled until an admin enables them (+ SNS notification).
signup_require_approval = false

# Federated (OIDC SSO) login. OFF in this committed file so a fresh clone deploys cleanly
# with only Cognito-local auth and no account-specific values in git.
#
# To ENABLE SSO, put the real object (client_id, client_secret_arn, etc.) in a LOCAL,
# gitignored override:  environments/dev/federated.auto.tfvars  (see the example already
# in this directory). Terraform auto-loads *.auto.tfvars and it takes precedence over this
# file. Because federated_login is a single object (Terraform does not merge objects across
# files), the override must contain the WHOLE object, not just the enabled flag.
#
# Full setup — the IdP secret, the Federate-side callback whitelist, and the enable/disable
# procedure — is documented in docs/operations/federated-login.md.
federated_login = {
  enabled = false
}

# AgentCore Runtime ARN — leave empty on the first apply; the deploy flow fills it in.
agent_runtime_arn = ""

log_retention_days     = 30
default_catalog_domain = "cyber-security-risk"

# Per-function Lambda memory ceiling. Brand-new AWS accounts cap this at 512 MB.
# Request a Service Quotas increase (AWS Lambda → function memory, up to 10240) and
# raise this to 10240 so heavier functions (ingest, chat, concierge) get full memory.
lambda_max_memory = 512
