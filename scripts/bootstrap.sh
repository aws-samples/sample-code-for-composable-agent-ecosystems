#!/usr/bin/env bash
# scripts/bootstrap.sh — one-time prerequisites for a fresh account.
#
# Creates the Terraform remote-state S3 bucket (versioned, encrypted, public access
# blocked, TLS-enforced) and writes its name into every environment's backend.hcl. With
# S3-native state locking (Terraform >= 1.11, use_lockfile) there is NO DynamoDB lock
# table to create — the bucket is the only prerequisite.
#
# Idempotent: re-running detects an existing bucket and only fixes settings / backend
# files as needed. It never deletes anything.
#
# Usage:
#   bash scripts/bootstrap.sh                                  # prompts for a bucket name
#   bash scripts/bootstrap.sh --bucket my-tf-state-bucket      # explicit name
#   TF_STATE_BUCKET=my-tf-state-bucket bash scripts/bootstrap.sh
#
# Config via env (see common.sh): AWS_REGION (default us-east-1), AWS_PROFILE (optional).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

BUCKET="${TF_STATE_BUCKET:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --bucket) shift; BUCKET="${1:-}" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

command -v aws >/dev/null 2>&1 || die "aws CLI not found (install AWS CLI v2)"

# Confirm credentials + resolve account (never hardcoded; used only for a friendly log).
ACCOUNT="$(awscli sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "AWS credentials invalid/expired. Authenticate first (e.g. aws sso login)."

if [ -z "$BUCKET" ]; then
  if [ -t 0 ]; then
    # Suggest a globally-unique default incorporating the account + region.
    SUGGEST="tessera-tfstate-${ACCOUNT}-${AWS_REGION}"
    read -r -p "Terraform state bucket name [$SUGGEST]: " BUCKET
    BUCKET="${BUCKET:-$SUGGEST}"
  else
    die "no bucket name — pass --bucket <name> or set TF_STATE_BUCKET"
  fi
fi

step "Bootstrapping Terraform state — bucket '$BUCKET' (account $ACCOUNT, region $AWS_REGION)"

# ── 1. Create the bucket (idempotent) ────────────────────────────────────────
if awscli s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  ok "bucket already exists — reconciling settings"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    awscli s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    awscli s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null
  fi
  ok "created bucket"
fi

# ── 2. Harden it (all idempotent) ─────────────────────────────────────────────
awscli s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled >/dev/null
ok "versioning enabled"

awscli s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' >/dev/null
ok "default encryption enabled"

awscli s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null
ok "public access blocked"

# Deny non-TLS access to the state bucket.
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*","Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}
JSON
)
awscli s3api put-bucket-policy --bucket "$BUCKET" --policy "$POLICY" >/dev/null
ok "TLS-only bucket policy applied"

# ── 3. Write the bucket name into every backend.hcl ──────────────────────────
step "Updating backend configuration files"
for env_dir in "$INFRA_DIR"/environments/*/; do
  hcl="$env_dir/backend.hcl"
  [ -f "$hcl" ] || continue
  # Replace the bucket line (whether it's the REPLACE_ placeholder or a prior value).
  tmp="$(mktemp)"
  sed -E "s|^([[:space:]]*bucket[[:space:]]*=[[:space:]]*).*|\\1\"${BUCKET}\"|" "$hcl" > "$tmp"
  # Also OWN the region: overwrite backend.hcl's region with the current AWS_REGION (from
  # .env or the shell). This is authoritative for the STATE BUCKET's region, so do NOT hand-
  # edit region in backend.hcl — set AWS_REGION instead. It must match `region` in the
  # matching terraform.tfvars (which you DO set by hand) so state + resources share a region.
  sed -E -i.bak "s|^([[:space:]]*region[[:space:]]*=[[:space:]]*).*|\\1\"${AWS_REGION}\"|" "$tmp" && rm -f "$tmp.bak"
  mv "$tmp" "$hcl"
  ok "set bucket in $(basename "$env_dir")/backend.hcl"
done

log ""
ok "Bootstrap complete."
log "State bucket:  $BUCKET"
log "Region:        $AWS_REGION   (written into every environments/*/backend.hcl)"
log "Next:          make package"
log "               source scripts/load-env.sh"
log "               cd infra && terraform init \$TF_BACKEND && terraform plan \$TF_VARS && terraform apply \$TF_VARS"
log "               (see README.md 'Quick start' / docs/deployment/local-deployment.md)"
