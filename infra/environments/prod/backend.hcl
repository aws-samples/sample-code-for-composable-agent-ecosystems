# Backend configuration for the prod environment.
# Passed at init time:  terraform init -backend-config=environments/prod/backend.hcl
# Run `make bootstrap` first — it rewrites the `bucket` line below with your bucket name.
# The placeholder is intentional (no account-specific value committed). You can also set it
# manually or via TF_STATE_BUCKET. See docs/deployment/state-backend.md.

bucket = "REPLACE_ME_TF_STATE_BUCKET"
key    = "tessera/prod/terraform.tfstate"
region = "<your aws region>"
