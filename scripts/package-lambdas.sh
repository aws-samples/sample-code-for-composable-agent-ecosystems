#!/usr/bin/env bash
# scripts/package-lambdas.sh — build Lambda deployment packages + the shared layer.
#
# Produces, under build/lambdas/:
#   • shared_layer.zip   — the shared modules (auth.py, pricing.py, tessera_platform.*)
#                          laid out under python/, from app/lambdas/shared_layer_manifest.txt.
#   • <name>.zip         — one per Lambda source dir, containing its own .py files plus
#                          any pip dependencies (built for the Lambda runtime: Linux
#                          x86_64 wheels), so native deps (cryptography, etc.) work.
#
# Terraform consumes these prebuilt zips (see the compute / orchestration modules). Run
# this before `terraform apply`. Idempotent — safe to re-run.
#
# Requires: python3.12 (or the LAMBDA_PY_VERSION you set) + pip.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

# VERBOSE=1 streams full pip output for every package (default: quiet, but a FAILING
# package always shows its full pip output + the exact command). ONLY="name1 name2"
# builds just those Lambda dirs (handy for iterating on one failing package).
LAMBDA_PY_VERSION="${LAMBDA_PY_VERSION:-3.12}"
LAMBDA_PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"
VERBOSE="${VERBOSE:-0}"
ONLY="${ONLY:-}"
LAMBDAS_DIR="$APP_DIR/lambdas"
MANIFEST="$LAMBDAS_DIR/shared_layer_manifest.txt"

command -v python3 >/dev/null 2>&1 || die "python3 not found"
command -v pip3    >/dev/null 2>&1 || die "pip3 not found"

log "python: $(python3 --version 2>&1) | pip: $(pip3 --version 2>&1 | awk '{print $1, $2}')"
log "target runtime: python${LAMBDA_PY_VERSION} / ${LAMBDA_PLATFORM}"

mkdir -p "$BUILD_DIR"

# ── Shared layer ─────────────────────────────────────────────────────────────
step "Building shared layer from manifest"
LAYER_STAGE="$(mktemp -d)"
LAYER_PY="$LAYER_STAGE/python"
mkdir -p "$LAYER_PY"

# The application package `tessera_platform` was reorganised to app/platform/, but the
# Python import name is still `tessera_platform`. Map manifest entries accordingly.
while IFS= read -r line; do
  entry="$(echo "$line" | sed 's/#.*//' | xargs || true)"
  [ -z "$entry" ] && continue
  if [[ "$entry" == *.py ]]; then
    # Top-level module (e.g. auth.py) — lives in app/lambdas/.
    cp "$LAMBDAS_DIR/$entry" "$LAYER_PY/$entry"
  else
    # Package subdir, e.g. tessera_platform/catalog → app/platform/catalog.
    pkg="${entry%%/*}"; sub="${entry#*/}"
    src="$APP_DIR/platform/$sub"
    [ -d "$src" ] || die "shared layer source missing: $src (manifest entry '$entry')"
    dst="$LAYER_PY/$pkg/$sub"
    mkdir -p "$dst"
    : > "$LAYER_PY/$pkg/__init__.py"
    # Copy the whole subpackage tree (py files + nested packages).
    ( cd "$src" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
        mkdir -p "$dst/$(dirname "$f")"; cp "$f" "$dst/$f"
      done )
  fi
done < "$MANIFEST"

( cd "$LAYER_STAGE" && zip -qr "$BUILD_DIR/shared_layer.zip" python )
rm -rf "$LAYER_STAGE"
ok "shared_layer.zip built"

# ── Per-Lambda packages ──────────────────────────────────────────────────────
step "Building per-Lambda packages"

# Count the packages we'll build (for progress like [3/38]).
total=0
for dir in "$LAMBDAS_DIR"/*/; do
  n="$(basename "$dir")"
  [ -f "$dir/handler.py" ] || continue
  [ -n "$ONLY" ] && ! grep -qw "$n" <<<"$ONLY" && continue
  total=$((total + 1))
done

built=0; idx=0
for dir in "$LAMBDAS_DIR"/*/; do
  name="$(basename "$dir")"
  [ -f "$dir/handler.py" ] || continue
  # If ONLY is set, skip packages not listed.
  if [ -n "$ONLY" ] && ! grep -qw "$name" <<<"$ONLY"; then continue; fi

  idx=$((idx + 1))
  has_deps="no"; [ -s "$dir/requirements.txt" ] && has_deps="yes"
  log "  [$idx/$total] $name (deps=$has_deps)"

  stage="$(mktemp -d)"
  # The Lambda's own source files (top-level .py + any nested package dirs it owns).
  ( cd "$dir" && find . -name '*.py' -print0 | while IFS= read -r -d '' f; do
      mkdir -p "$stage/$(dirname "$f")"; cp "$f" "$stage/$f"
    done )

  # Dependencies (Linux x86_64 wheels) if the Lambda declares any.
  if [ "$has_deps" = "yes" ]; then
    # Build the pip command once so we can print it verbatim on failure.
    pip_cmd=(pip3 install -r "$dir/requirements.txt" -t "$stage"
             --platform "$LAMBDA_PLATFORM" --python-version "$LAMBDA_PY_VERSION"
             --implementation cp --only-binary=:all: --upgrade)
    [ "$VERBOSE" = "1" ] || pip_cmd+=(-q)

    if [ "$VERBOSE" = "1" ]; then
      # Stream everything live.
      if ! "${pip_cmd[@]}"; then
        rm -rf "$stage"
        err "pip install FAILED for '$name'"
        err "requirements.txt:"; sed 's/^/      /' "$dir/requirements.txt" >&2
        err "command: ${pip_cmd[*]}"
        die "fix the dependency above (or run: VERBOSE=1 ONLY=\"$name\" bash scripts/package-lambdas.sh)"
      fi
    else
      # Quiet by default, but capture output so a FAILURE prints the real error.
      pip_log="$(mktemp)"
      if ! "${pip_cmd[@]}" >"$pip_log" 2>&1; then
        err "pip install FAILED for '$name' — full pip output:"
        sed 's/^/      /' "$pip_log" >&2
        err "requirements.txt:"; sed 's/^/      /' "$dir/requirements.txt" >&2
        err "command: ${pip_cmd[*]}"
        rm -rf "$stage" "$pip_log"
        die "fix the dependency above (or re-run just this one: VERBOSE=1 ONLY=\"$name\" bash scripts/package-lambdas.sh)"
      fi
      rm -f "$pip_log"
    fi
  fi

  ( cd "$stage" && zip -qr "$BUILD_DIR/$name.zip" . )
  rm -rf "$stage"
  ok "  [$idx/$total] $name.zip"
  built=$((built + 1))
done
ok "built $built Lambda package(s) into $BUILD_DIR"
