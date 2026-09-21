#!/usr/bin/env bash
# Post-deploy guard: assert the web-search credential provider actually exists in the
# target account. Converts the old silent gap (Lambdas point at a provider that was never
# created) into a loud, blocking failure. Run by `make verify-websearch` and CI.
set -euo pipefail

PROJECT="${PROJECT:-tessera}"
ENV="${ENV:-dev}"
REGION="${AWS_REGION:-us-east-1}"
NAME="${PROJECT}-${ENV}-websearch"

found="$(aws bedrock-agentcore-control list-api-key-credential-providers \
  --region "$REGION" \
  --query "credentialProviders[?name=='${NAME}'] | [0].name" \
  --output text 2>/dev/null || true)"

if [ "$found" = "$NAME" ]; then
  echo "OK: web-search credential provider '${NAME}' present."
  exit 0
fi
echo "FAIL: web-search credential provider '${NAME}' is MISSING." >&2
echo "      web_search and external-grounding will not work. Fix:" >&2
echo "        make set-websearch-key ENV=${ENV}   # store the key in Secrets Manager" >&2
echo "        terraform -chdir=infra apply ...     # provisions the provider" >&2
exit 1
