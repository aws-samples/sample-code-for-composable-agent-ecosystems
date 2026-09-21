# Remote state backend: S3 with native state locking.
#
# `use_lockfile = true` enables S3-native locking (Terraform >= 1.11) — a lock object
# is written alongside the state file in the same bucket. No DynamoDB table is required.
#
# The bucket / key / region are intentionally left OUT of this block so nothing is
# hardcoded. They are supplied at init time via a per-environment backend config file:
#
#   terraform init -backend-config=environments/dev/backend.hcl
#
# See docs/deployment/state-backend.md for one-time bootstrap of the state bucket.
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
