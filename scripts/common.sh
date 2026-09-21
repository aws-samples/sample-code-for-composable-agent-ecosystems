#!/usr/bin/env bash
# scripts/common.sh — shared config + helpers, sourced by every script.
#
# Targeting is controlled entirely by environment variables (no hardcoded account/
# profile). The AWS account id is resolved at runtime; nothing here is account-specific.
#
#   AWS_PROFILE   (optional)  AWS CLI profile to use
#   AWS_REGION    (default us-east-1)
#   ENVIRONMENT   (default dev)   -> tfvars/backend under infra/environments/<env>

set -Eeuo pipefail

export AWS_REGION="${AWS_REGION:-us-east-1}"
export ENVIRONMENT="${ENVIRONMENT:-dev}"

# Repo layout (this file lives in scripts/).
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
INFRA_DIR="$ROOT_DIR/infra"
APP_DIR="$ROOT_DIR/app"
BUILD_DIR="$ROOT_DIR/build/lambdas"
export ROOT_DIR INFRA_DIR APP_DIR BUILD_DIR

# Optional AWS profile flag (empty when AWS_PROFILE is unset).
AWS_PROFILE_ARG=()
[ -n "${AWS_PROFILE:-}" ] && AWS_PROFILE_ARG=(--profile "$AWS_PROFILE")
export AWS_PROFILE_ARG

# Colour helpers (only if a TTY).
if [ -t 1 ]; then
  _C_STEP=$'\033[1;36m'; _C_OK=$'\033[1;32m'; _C_WARN=$'\033[1;33m'; _C_ERR=$'\033[1;31m'; _C_OFF=$'\033[0m'
else
  _C_STEP=; _C_OK=; _C_WARN=; _C_ERR=; _C_OFF=
fi
log()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$_C_STEP" "$*" "$_C_OFF"; }
ok()   { printf '%s  ✓ %s%s\n' "$_C_OK" "$*" "$_C_OFF"; }
warn() { printf '%s  ! %s%s\n' "$_C_WARN" "$*" "$_C_OFF"; }
err()  { printf '%s  ✗ %s%s\n' "$_C_ERR" "$*" "$_C_OFF" >&2; }
die()  { err "$*"; exit 1; }

# Read a Terraform output value (empty on failure).
tf_output() {
  terraform -chdir="$INFRA_DIR" output -raw "$1" 2>/dev/null || true
}

# aws wrapper that injects region + optional profile.
awscli() {
  aws "$@" --region "$AWS_REGION" "${AWS_PROFILE_ARG[@]}"
}
