#!/usr/bin/env bash
# scripts/load-env.sh — load .env and export deploy targeting into your CURRENT shell.
#
# SOURCE this (don't execute it) so the exports persist, then run terraform directly:
#
#   source scripts/load-env.sh              # loads .env, ENV defaults from it (or dev)
#   source scripts/load-env.sh staging      # override the environment
#
# After sourcing you get:
#   AWS_PROFILE, AWS_REGION, ENVIRONMENT exported
#   $TF_VARS      = -var-file=environments/<env>/terraform.tfvars   (run terraform from infra/)
#   $TF_BACKEND   = -backend-config=environments/<env>/backend.hcl
#
# Then, from the infra/ directory:
#   terraform init $TF_BACKEND
#   terraform plan $TF_VARS
#   terraform apply $TF_VARS

# Load .env from the repo root (one level up from scripts/).
_repo="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
if [ -f "$_repo/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$_repo/.env"
  set +a
fi

# Environment: arg 1 overrides ENVIRONMENT from .env, which overrides the default.
export ENVIRONMENT="${1:-${ENVIRONMENT:-dev}}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

# Terraform flag helpers (paths are relative to the infra/ directory).
export TF_VARS="-var-file=environments/${ENVIRONMENT}/terraform.tfvars"
export TF_BACKEND="-backend-config=environments/${ENVIRONMENT}/backend.hcl"

# Guard: the committed backend.hcl ships a placeholder bucket. `make bootstrap` fills it in
# (or set TF_STATE_BUCKET / edit the file). If it's still the placeholder, `terraform init`
# would fail against a non-existent bucket — warn with the fix instead of a cryptic S3 error.
_backend_hcl="$_repo/infra/environments/${ENVIRONMENT}/backend.hcl"
if [ -f "$_backend_hcl" ] && grep -q 'REPLACE_ME_TF_STATE_BUCKET' "$_backend_hcl"; then
  echo "WARNING: environments/${ENVIRONMENT}/backend.hcl still has the placeholder state bucket." >&2
  echo "         Run 'make bootstrap' (or 'make bootstrap BUCKET=<name>') before 'terraform init $TF_BACKEND'." >&2
fi

# If `make deploy-agent` has written the AgentCore Runtime ARN, load it too. Terraform
# only auto-loads *.auto.tfvars from the CWD (infra/), NOT from environments/<env>/, so we
# pass it explicitly here. Without this the agent-invoker Lambda gets an empty runtime ARN
# and every run fails with InvokeAgentRuntime 404 (UnknownOperationException).
if [ -f "$_repo/infra/environments/${ENVIRONMENT}/agent.auto.tfvars" ]; then
  export TF_VARS="$TF_VARS -var-file=environments/${ENVIRONMENT}/agent.auto.tfvars"
fi

echo "Loaded: ENVIRONMENT=$ENVIRONMENT  AWS_REGION=$AWS_REGION  AWS_PROFILE=${AWS_PROFILE:-(unset)}"
echo "Terraform flags (run from infra/):"
echo "  TF_BACKEND = $TF_BACKEND"
echo "  TF_VARS    = $TF_VARS"
