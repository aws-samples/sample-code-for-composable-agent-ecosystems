# Terraform state backend

Tessera stores Terraform state remotely in **Amazon S3 with native state locking**. As
of Terraform 1.11, S3 can hold the lock itself via the `use_lockfile` argument — a lock
object written alongside the state file — so **no DynamoDB table is required**. The
DynamoDB locking arguments are deprecated in the S3 backend.

This is why the project pins `required_version = ">= 1.11.0"` (see `infra/versions.tf`).

## Backend configuration

The backend block in `infra/backend.tf` intentionally omits the bucket/key/region so
nothing is account-specific in source:

```hcl
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
```

The concrete values are supplied at init time from a per-environment file,
`infra/environments/<env>/backend.hcl`. This file is **committed with a placeholder bucket**
so nothing account-specific lands in git:

```hcl
bucket = "REPLACE_ME_TF_STATE_BUCKET"   # filled in by `make bootstrap`
key    = "tessera/dev/terraform.tfstate"
region = "us-east-1"
```

**Run `make bootstrap` before your first `init`** — it creates the bucket and rewrites the
`bucket` line in every `backend.hcl` with the real name (see [One-time
bootstrap](#one-time-bootstrap) below). Alternatively, set the name yourself by editing the
file or exporting `TF_STATE_BUCKET`. If you `init` while the placeholder is still in place,
Terraform fails against a non-existent bucket; `source scripts/load-env.sh <env>` also prints
a warning in that case.

You pass this at init time (run terraform directly from `infra/`):

```bash
cd infra
terraform init -backend-config=environments/dev/backend.hcl
```

`source scripts/load-env.sh dev` sets `$TF_BACKEND` to exactly this flag.

## One-time bootstrap

The state bucket must exist before the first `init`. The **bootstrap script** creates it
(versioned, encrypted, public-access-blocked, TLS-only) and writes the bucket name into
every `environments/<env>/backend.hcl` for you:

```bash
make bootstrap                          # prompts for a bucket name (suggests a unique default)
make bootstrap BUCKET=my-tf-state-bucket   # or pass one explicitly
```

It is idempotent — re-running reconciles settings and never deletes anything. It creates
**no DynamoDB table** (S3-native locking handles that). Each environment keeps a distinct
state `key` (`tessera/<env>/terraform.tfstate`), so `dev`/`staging`/`prod` never collide.

<details>
<summary>What the script does (equivalent manual commands)</summary>

```bash
BUCKET=your-terraform-state-bucket
REGION=us-east-1
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  $( [ "$REGION" = "us-east-1" ] || echo --create-bucket-configuration LocationConstraint="$REGION" )
aws s3api put-bucket-versioning  --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption  --bucket "$BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
# then set `bucket = "..."` in each environments/<env>/backend.hcl
```
</details>

## Per-environment isolation

Each environment has its own state key, so `dev`, `staging`, and `prod` never share
state. To switch environments, re-run `terraform init` with that environment's
`backend.hcl` (i.e. `source scripts/load-env.sh <env>` then
`terraform init $TF_BACKEND` from `infra/`).

## If you must run Terraform < 1.11

Native locking needs 1.11+. On older versions you would fall back to a DynamoDB lock
table (`dynamodb_table` in the backend). This project targets 1.11+, so the DynamoDB
fallback is intentionally not configured.
