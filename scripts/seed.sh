#!/usr/bin/env bash
# scripts/seed.sh — publish platform CONTENT after infra is deployed.
#
#   1. Publish domain descriptors, schemas, and catalog-content source data to the
#      ecosystems bucket (the S3-backed ingest sources read the latter).
#   2. Upload + compile the platform and production ecosystem YAMLs (save-ecosystem
#      compiles each into a Step Functions state machine).
#   2b/2c. Publish export inputs + prebuilt deploy artifacts for single-eco exports.
#
# This does NOT run catalog ingestion — that is a SEPARATE, optional, LONG-RUNNING step:
# run `make ingest` (scripts/ingest.sh) AFTER this. Keeping them apart means a slow,
# externally-dependent ingest can't mask or block content setup, and re-running ingest
# doesn't rebuild the frontend export here.
#
# Content lives under app/content/. Fast, deterministic, and safely re-runnable.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

CONTENT="$APP_DIR/content"
ECO_BUCKET=$(tf_output ecosystems_bucket_name)
NAME_PREFIX=$(tf_output name_prefix)
[ -n "$ECO_BUCKET" ] || die "no ecosystems_bucket_name output — is the platform deployed?"

# ── 1. domains + catalog content ─────────────────────────────────────────────
step "Publishing domain descriptors + catalog content to s3://$ECO_BUCKET"
[ -d "$CONTENT/domains" ]         && { awscli s3 sync "$CONTENT/domains/"         "s3://$ECO_BUCKET/domains/"         --quiet; ok "domains published"; }
[ -d "$CONTENT/catalog-content" ] && { awscli s3 sync "$CONTENT/catalog-content/" "s3://$ECO_BUCKET/catalog-content/" --quiet; ok "catalog content published"; }

# ── 2. ecosystems (upload + compile via save-ecosystem) ──────────────────────
step "Seeding ecosystems"
SAVE_FN="${NAME_PREFIX}-save-ecosystem"

seed_ecosystem() {
  local yaml="$1" id
  id="$(basename "$yaml" .yaml)"
  # Upload the YAML to the ecosystems prefix.
  awscli s3 cp "$yaml" "s3://$ECO_BUCKET/ecosystems/$id.yaml" --quiet
  # Invoke save-ecosystem's deploy path to compile the ASL state machine.
  # NOTE: with --cli-binary-format raw-in-base64-out the CLI expects RAW JSON on --payload
  # and base64-encodes it itself; do NOT pre-encode with `| base64` (that double-encodes
  # and the invoke fails to parse the body).
  # The handler routes the deploy path off event["path"] (endswith "/deploy") and, on that
  # path, calls deploy_state_machine directly (no auth needed for compile). It must be the
  # "path" key — "resource" alone falls through to the auth-gated save and returns 401.
  awscli lambda invoke \
    --function-name "$SAVE_FN" \
    --payload "$(printf '{"path":"/api/ecosystems/%s/deploy","pathParameters":{"id":"%s"}}' "$id" "$id")" \
    --cli-binary-format raw-in-base64-out \
    /dev/null >/dev/null 2>&1 && ok "compiled $id" || warn "compile of $id reported issues (non-fatal)"
}

# Platform-internal ecosystems first, then production ecosystems. Samples are NOT seeded.
if [ -d "$CONTENT/ecosystems/_platform" ]; then
  for y in "$CONTENT/ecosystems/_platform"/*.yaml; do [ -e "$y" ] && seed_ecosystem "$y"; done
fi
for y in "$CONTENT/ecosystems"/*.yaml; do [ -e "$y" ] && seed_ecosystem "$y"; done

# ── 2b. export inputs (Option A) ─────────────────────────────────────────────
# The export-ecosystem Lambda has no repo tree, so it fetches the eco-lite Terraform
# module, the AgentCore runtime source, and the save-ecosystem ASL compiler from the
# ecosystems bucket under export-inputs/ and unpacks them at runtime. Each zip is
# ROOTED at its source dir (files at the archive root) because the generator copytree's
# the fetched dir directly. Published once here; re-runnable (overwrites in place).
step "Publishing export inputs to s3://$ECO_BUCKET/export-inputs/"
publish_input() {
  local src="$1" stem="$2" tmp
  [ -e "$src" ] || { warn "export input source missing: $src (skipping $stem)"; return; }
  tmp="$(mktemp -d)"
  if [ -d "$src" ]; then
    # Zip the directory CONTENTS at the archive root (exclude build cruft). Patterns
    # cover BOTH nested (*/foo/*) and top-level (foo/*) matches — the AgentCore build
    # dir (.bedrock_agentcore/, which holds a ~100MB dependencies.zip) and the local
    # build config sit at the runtime dir's ROOT, so top-level globs are required or
    # they bloat the input (Lambda /tmp is 512MB) and ship build artifacts into exports.
    ( cd "$src" && zip -qr "$tmp/$stem.zip" . \
        -x '*/__pycache__/*' '__pycache__/*' '*.pyc' \
           '*/.venv/*' '.venv/*' '*/.terraform/*' '.terraform/*' \
           '*.egg-info/*' '*/.bedrock_agentcore/*' '.bedrock_agentcore/*' \
           '.bedrock_agentcore.yaml' )
  else
    # Single file (e.g. save-ecosystem/handler.py) — zip it at the root by basename.
    ( cd "$(dirname "$src")" && zip -q "$tmp/$stem.zip" "$(basename "$src")" )
  fi
  awscli s3 cp "$tmp/$stem.zip" "s3://$ECO_BUCKET/export-inputs/$stem.zip" --quiet \
    && ok "published export-inputs/$stem.zip" || warn "publish of $stem.zip failed (non-fatal)"
  rm -rf "$tmp"
}
publish_input "$INFRA_DIR/modules/eco-lite"              "eco-lite"
publish_input "$APP_DIR/agent"                           "agent-runtime"
publish_input "$APP_DIR/lambdas/save-ecosystem/handler.py" "save-ecosystem"

# ── 2c. prebuilt deployment artifacts (Option A) ─────────────────────────────
# The export package must SHIP the run-plane Lambda zips, the shared layer, and the built
# single-eco frontend so it is directly deployable (the eco-lite module uploads them via
# aws_s3_object at apply). The Lambda can't run Docker/Node, so we build them HERE (dev
# machine) via `make package` (build/lambdas/*.zip + shared_layer.zip) + `next build`, and
# publish them to export-inputs/. The export-ecosystem Lambda fetches + lays them into each
# generated package. Re-runnable (overwrites in place).

# lambdas.zip — all per-lambda zips at the archive root (generate.py copies only the subset
# each eco's BoM ships). Excludes the shared layer (published separately as layer.zip).
if [ -d "$BUILD_DIR" ] && ls "$BUILD_DIR"/*.zip >/dev/null 2>&1; then
  _tmp="$(mktemp -d)"
  ( cd "$BUILD_DIR" && zip -qr "$_tmp/lambdas.zip" . -i '*.zip' -x 'shared_layer.zip' )
  awscli s3 cp "$_tmp/lambdas.zip" "s3://$ECO_BUCKET/export-inputs/lambdas.zip" --quiet \
    && ok "published export-inputs/lambdas.zip ($(ls "$BUILD_DIR"/*.zip | grep -v shared_layer | wc -l | tr -d ' ') lambdas)" \
    || warn "publish of lambdas.zip failed (non-fatal)"
  rm -rf "$_tmp"
else
  warn "no built lambda zips in $BUILD_DIR — run 'make package' first (skipping lambdas.zip/layer.zip)"
fi

# layer.zip — contains the shared layer as shared-layer.zip (generate.py lays it to
# layer/shared-layer.zip inside the package).
if [ -f "$BUILD_DIR/shared_layer.zip" ]; then
  _tmp="$(mktemp -d)"
  cp "$BUILD_DIR/shared_layer.zip" "$_tmp/shared-layer.zip"
  ( cd "$_tmp" && zip -q layer.zip shared-layer.zip )
  awscli s3 cp "$_tmp/layer.zip" "s3://$ECO_BUCKET/export-inputs/layer.zip" --quiet \
    && ok "published export-inputs/layer.zip" || warn "publish of layer.zip failed (non-fatal)"
  rm -rf "$_tmp"
else
  warn "no $BUILD_DIR/shared_layer.zip — run 'make package' first (skipping layer.zip)"
fi

# frontend.zip — the single-eco /solo static export, flattened so the /solo page serves at the
# site root (index.html) with a runtime-config <script> injected; the eco-lite frontend.tf
# uploads these files + renders tessera-config.js at deploy. Built here (needs Node). The build
# bakes NEXT_PUBLIC_SOLO_ECO="" so ONE build serves any eco (endpoints come from window config).
FE_DIR="$APP_DIR/frontend"
if command -v npx >/dev/null 2>&1 && [ -d "$FE_DIR" ]; then
  step "Building single-eco frontend export"
  _feout="$FE_DIR/out"
  # CRITICAL: the export build must NOT bake the platform's Cognito/API into the bundle — those
  # are the build-time fallbacks and the eco's real Cognito is injected at RUNTIME per deploy
  # (window.__TESSERA_SOLO__). Next inlines NEXT_PUBLIC_* from .env.local at build and it overrides
  # shell env, so we move any .env.local aside for the export build and supply BLANK Cognito/API
  # fallbacks. If the runtime config ever failed to load, the exported build would then fail auth
  # LOUDLY (empty pool) instead of silently authenticating against the platform pool. Restored after.
  _envlocal_bak=""
  if [ -f "$FE_DIR/.env.local" ]; then
    _envlocal_bak="$FE_DIR/.env.local.exportbak.$$"
    mv "$FE_DIR/.env.local" "$_envlocal_bak"
  fi
  cat > "$FE_DIR/.env.local" <<'FEENV'
NEXT_PUBLIC_SOLO_ECO=
NEXT_PUBLIC_API_URL=
NEXT_PUBLIC_WS_URL=
NEXT_PUBLIC_COGNITO_USER_POOL_ID=
NEXT_PUBLIC_COGNITO_CLIENT_ID=
NEXT_PUBLIC_COGNITO_DOMAIN=
NEXT_PUBLIC_CATALOG_API_URL=
NEXT_PUBLIC_FEDERATED_LOGIN_ENABLED=false
FEENV
  _restore_env() { rm -f "$FE_DIR/.env.local"; [ -n "$_envlocal_bak" ] && mv "$_envlocal_bak" "$FE_DIR/.env.local"; }
  if ( cd "$FE_DIR" && npx next build >/tmp/tessera-solo-fe-build.log 2>&1 ); then
    _stage="$(mktemp -d)"
    if [ -f "$_feout/solo.html" ]; then
      # /_next chunks + top-level static assets (not the other routes' HTML).
      cp -R "$_feout/_next" "$_stage/_next"
      for p in "$_feout"/*; do
        b="$(basename "$p")"
        case "$b" in *.html|*.txt) : ;; _next) : ;; *) [ -f "$p" ] && cp "$p" "$_stage/$b" ;; esac
      done
      # The /solo page becomes index.html with the runtime-config script injected first.
      python3 - "$_feout/solo.html" "$_stage/index.html" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
html = open(src, encoding="utf-8").read()
tag = '<script src="/tessera-config.js"></script>'
if tag not in html:
    html = html.replace("<head>", "<head>" + tag, 1) if "<head>" in html else tag + html
open(dst, "w", encoding="utf-8").write(html)
PY
      _tmp="$(mktemp -d)"
      ( cd "$_stage" && zip -qr "$_tmp/frontend.zip" . )
      awscli s3 cp "$_tmp/frontend.zip" "s3://$ECO_BUCKET/export-inputs/frontend.zip" --quiet \
        && ok "published export-inputs/frontend.zip" || warn "publish of frontend.zip failed (non-fatal)"
      rm -rf "$_tmp"
    else
      warn "next build produced no out/solo.html — skipping frontend.zip (check /tmp/tessera-solo-fe-build.log)"
    fi
    rm -rf "$_stage"
  else
    warn "frontend 'next build' failed — skipping frontend.zip (see /tmp/tessera-solo-fe-build.log)"
  fi
  _restore_env  # always restore the developer's .env.local (platform build config)
else
  warn "npx not found or frontend dir missing — skipping frontend.zip (exports will lack the UI)"
fi

ok "Content + ecosystems published. Next (optional): run 'make ingest' to populate the catalog."
