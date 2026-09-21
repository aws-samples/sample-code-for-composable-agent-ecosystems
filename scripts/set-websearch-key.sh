#!/usr/bin/env bash
# Store / rotate the web-search (Tavily) API key in AWS Secrets Manager, out-of-band.
# Terraform (infra/credential-providers.tf) reads this secret and provisions the
# AgentCore Identity credential provider from it — so the key never lives in the repo,
# tfvars, or env, only in Secrets Manager. Idempotent: create-or-update.
#
# Usage:
#   make set-websearch-key ENV=dev        # prompts for the key (not echoed)
#   AWS_PROFILE=admin-audit make set-websearch-key ENV=dev
set -euo pipefail

PROJECT="${PROJECT:-tessera}"
ENV="${ENV:-dev}"
REGION="${AWS_REGION:-us-east-1}"
SECRET_NAME="${WEBSEARCH_SECRET_NAME:-${PROJECT}-${ENV}-websearch-api-key}"

printf 'Web-search API key for %s (input hidden): ' "$SECRET_NAME" >&2
read -rs KEY
echo >&2
if [ -z "${KEY}" ] || [ "${KEY}" = "REPLACE_ME_SEE_README" ]; then
  echo "ERROR: empty or placeholder key — aborting." >&2
  exit 1
fi

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" --secret-string "$KEY" --region "$REGION" >/dev/null
  echo "Updated secret $SECRET_NAME" >&2
else
  aws secretsmanager create-secret --name "$SECRET_NAME" \
    --description "Tessera web-search (Tavily) API key — read by Terraform to provision the AgentCore credential provider." \
    --secret-string "$KEY" --region "$REGION" >/dev/null
  echo "Created secret $SECRET_NAME" >&2
fi
unset KEY
echo "Done. Now run terraform apply (or the CI apply stage) to provision the credential provider." >&2
