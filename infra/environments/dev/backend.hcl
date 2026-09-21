# Backend configuration for the dev environment.
# Passed at init time:  terraform init -backend-config=environments/dev/backend.hcl
#
# The state bucket must exist before first init. Run `make bootstrap` FIRST — it creates
# the bucket and rewrites the `bucket` line below with your bucket name. Until then the
# placeholder is intentional so no account-specific value is committed. You can also set
# it manually or via TF_STATE_BUCKET. See docs/deployment/state-backend.md.
# S3-native locking (use_lockfile) is set in backend.tf — no DynamoDB.

bucket = "REPLACE_ME_TF_STATE_BUCKET"
key    = "tessera/dev/terraform.tfstate"
region = "<your aws region>"
