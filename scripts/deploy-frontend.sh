#!/usr/bin/env bash
# scripts/deploy-frontend.sh — build the Next.js frontend and publish it.
#
# 1. Generate .env.local from the DEPLOYED Terraform outputs (so the built site targets
#    the correct account's Cognito pool + APIs — Next.js inlines NEXT_PUBLIC_* at build).
# 2. Install node deps if node_modules is missing (npm ci when a lockfile exists).
# 3. next build (static export) → out/.
# 4. aws s3 sync out/ → frontend bucket.
# 5. Publish extensionless route aliases (static export writes foo.html; browsers request
#    /foo). 6. CloudFront invalidation.
#
# Requires Node.js + npm on PATH.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

FRONTEND_DIR="$APP_DIR/frontend"
[ -d "$FRONTEND_DIR" ] || die "frontend not found at $FRONTEND_DIR"

step "Resolving config from Terraform outputs"
API_URL=$(tf_output rest_api_url); API_URL="${API_URL%/}"
WS_URL=$(tf_output websocket_url)
POOL=$(tf_output cognito_user_pool_id)
CLIENT=$(tf_output cognito_client_id)
COGDOM=$(tf_output cognito_domain)
CATALOG_API=$(tf_output catalog_api_url)
BUCKET=$(tf_output frontend_bucket_name)
DIST_ID=$(tf_output cloudfront_distribution_id)
# Federated login (OIDC SSO) — drives whether the "Federated Login" button renders.
# Empty/false when federation is disabled, so the button is hidden.
FED_ENABLED=$(tf_output federated_login_enabled)
FED_PROVIDER=$(tf_output federated_provider_name)
# Self-registration — drives whether the login screen shows a "Create Account" tab.
SELF_SIGNUP=$(tf_output allow_self_signup)
[ -n "$POOL" ] && [ -n "$CLIENT" ] && [ -n "$API_URL" ] || die "could not resolve Cognito/API config from Terraform outputs — is the stack deployed?"

cat > "$FRONTEND_DIR/.env.local" <<ENVEOF
NEXT_PUBLIC_API_URL=$API_URL
NEXT_PUBLIC_WS_URL=$WS_URL
NEXT_PUBLIC_COGNITO_USER_POOL_ID=$POOL
NEXT_PUBLIC_COGNITO_CLIENT_ID=$CLIENT
NEXT_PUBLIC_CATALOG_API_URL=$CATALOG_API
NEXT_PUBLIC_COGNITO_DOMAIN=$COGDOM
NEXT_PUBLIC_FEDERATED_LOGIN_ENABLED=$FED_ENABLED
NEXT_PUBLIC_FEDERATED_PROVIDER_NAME=$FED_PROVIDER
NEXT_PUBLIC_ALLOW_SELF_SIGNUP=$SELF_SIGNUP
ENVEOF
ok "wrote .env.local (pool=$POOL, federated=${FED_ENABLED:-false}, self_signup=${SELF_SIGNUP:-false})"

step "Installing frontend dependencies"
# Install only when node_modules is missing (keeps repeat deploys fast). Prefer the
# reproducible `npm ci` when a lockfile is present; otherwise fall back to `npm install`.
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
    ( cd "$FRONTEND_DIR" && npm ci )
  else
    ( cd "$FRONTEND_DIR" && npm install )
  fi
else
  log "node_modules present — skipping install"
fi

step "Building frontend"
( cd "$FRONTEND_DIR" && npm run build )

step "Syncing to s3://$BUCKET"
awscli s3 sync "$FRONTEND_DIR/out/" "s3://$BUCKET" --delete --quiet

step "Publishing extensionless route aliases"
for f in "$FRONTEND_DIR"/out/*.html; do
  [ -e "$f" ] || continue
  base="$(basename "$f" .html)"
  case "$base" in index|404) continue ;; esac
  awscli s3 cp "$f" "s3://$BUCKET/$base" --content-type "text/html" --quiet
done

if [ -n "$DIST_ID" ]; then
  step "Invalidating CloudFront ($DIST_ID)"
  awscli cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --no-cli-pager >/dev/null
fi
ok "Frontend deployed."
