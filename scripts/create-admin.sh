#!/usr/bin/env bash
# scripts/create-admin.sh — bootstrap the FIRST admin user.
#
# Self-registration is disabled (admin-create-only), so a fresh install has no users and
# no `admin` group. This uses Cognito's IAM-authenticated admin APIs (never public signup)
# to: (1) ensure the `admin` group exists, (2) create the user, (3) add them to `admin`.
# Subsequent users are created in-app from the admin console.
#
# NOTE: the pool uses email as an ALIAS attribute (modules/identity/cognito.tf:
# alias_attributes = ["email", ...]), so the immutable Cognito username CANNOT be in email
# format. We derive a non-email username from the email and set the email as a verified
# attribute, so sign-in by email (alias) still works.
#
#   bash scripts/create-admin.sh you@example.com              # emails a temp password
#   bash scripts/create-admin.sh you@example.com --show-temp  # writes temp pw to a 0600 file
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

EMAIL=""; SHOW_TEMP=0
for a in "$@"; do
  case "$a" in
    --show-temp) SHOW_TEMP=1 ;;
    -*) die "unknown flag: $a" ;;
    *) EMAIL="$a" ;;
  esac
done

POOL=$(tf_output cognito_user_pool_id)
[ -n "$POOL" ] || die "no cognito_user_pool_id output — is the platform deployed?"
[ -n "$EMAIL" ] || { [ -t 0 ] && read -r -p "Admin email: " EMAIL || die "email required as arg1"; }
[ -n "$EMAIL" ] || die "no email provided"

GROUP=admin
step "Bootstrapping admin '$EMAIL' in pool $POOL"

# 1. ensure the admin group exists
if awscli cognito-idp get-group --user-pool-id "$POOL" --group-name "$GROUP" >/dev/null 2>&1; then
  ok "group '$GROUP' exists"
else
  awscli cognito-idp create-group --user-pool-id "$POOL" --group-name "$GROUP" \
    --description "Tessera administrators" >/dev/null
  ok "created group '$GROUP'"
fi

# Derive a non-email username from the email (email is an alias attribute, so the username
# itself may not be email-format). Replacing '@' with '.' yields a stable, non-email value
# (e.g. you@example.com -> you.example.com). The email is set as a verified attribute below,
# so it remains a sign-in alias.
USERNAME="$(printf '%s' "$EMAIL" | tr '@' '.')"

# 2. create the user (idempotent). admin-get-user resolves the email ALIAS, so a
# previously-created user (whatever its stored username) is detected here — and we reuse
# that stored username for the group step.
if EXISTING_USERNAME="$(awscli cognito-idp admin-get-user --user-pool-id "$POOL" --username "$EMAIL" --query Username --output text 2>/dev/null)"; then
  warn "user '$EMAIL' already exists (username '$EXISTING_USERNAME') — ensuring admin membership only"
  USERNAME="$EXISTING_USERNAME"
elif [ "$SHOW_TEMP" -eq 1 ]; then
  TMP_PW="Tmp-$(openssl rand -hex 10)-7A!"
  awscli cognito-idp admin-create-user --user-pool-id "$POOL" --username "$USERNAME" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --temporary-password "$TMP_PW" --message-action SUPPRESS >/dev/null
  CREDS="$ROOT_DIR/.deploy-logs/first-admin-${EMAIL//[^A-Za-z0-9]/_}.txt"
  mkdir -p "$ROOT_DIR/.deploy-logs"
  ( umask 077; printf 'pool: %s\nusername: %s\nemail (sign-in alias): %s\ntemporary_password: %s\nnote: one-time; set a new password on first sign-in.\n' "$POOL" "$USERNAME" "$EMAIL" "$TMP_PW" > "$CREDS" )
  chmod 600 "$CREDS"; unset TMP_PW
  ok "created user '$USERNAME' (temp password written to $CREDS — 0600, gitignored)"
else
  awscli cognito-idp admin-create-user --user-pool-id "$POOL" --username "$USERNAME" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --desired-delivery-mediums EMAIL >/dev/null
  ok "created user '$USERNAME' — a temporary password was EMAILED (forced reset on first sign-in)"
fi

# 3. add to admin group (use the resolved/created username, not the email alias)
awscli cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$USERNAME" \
  --group-name "$GROUP" >/dev/null
ok "added '$EMAIL' to '$GROUP'"

log ""
ok "Admin bootstrapped. Open the frontend URL, sign in with the email + temporary password,"
log "   set a new 12+ char password, and enroll MFA (TOTP) when prompted."
