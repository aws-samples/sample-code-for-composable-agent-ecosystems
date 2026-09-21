"""Federated Login — Pre Token Generation trigger.

Two jobs, both for federated (SSO) users, done here because Pre Token Generation runs on
every sign-in and can both persist profile state AND shape the very first token:

1. GROUP: place every federated user into the configured default group. For this
   deployment that group is the non-admin `Users` group — federated users are NOT admins by
   default; an existing admin promotes selected users to the `admin` group via the Admin
   Console (see infra/environments/dev/terraform.tfvars → federated_login.default_group).

2. IDENTITY: give the user a human-friendly identity for the UI. Amazon Federate's OIDC
   token has NO `email` / `name` claim — the only identity is the alias in `sub`
   (e.g. "alias"), and with the alias-attributes pool the Cognito username is the opaque
   `AmazonFederate_alias`. Left alone, the ID token carries no email/name, so the app's
   profile menu shows "?" and an empty "signed in as". We synthesize a stable identity from
   the alias — email = <alias>@<domain>, name = <alias> — persist it on the profile, and
   inject it into this token so the UI has something to show immediately.

Idempotency: the group is only assigned to users with ZERO groups (never downgrades a
user assigned earlier or by an admin). Profile attributes are only written when missing or
changed, so steady-state logins do no writes.

Env:
  DEFAULT_GROUP          — group every new federated user is placed into (default "Users").
  FEDERATED_EMAIL_DOMAIN — domain for the synthesized email (default "amazon.com").
"""
import os
import re
import boto3

cognito = boto3.client("cognito-idp")

DEFAULT_GROUP = os.environ.get("DEFAULT_GROUP", "Users")
EMAIL_DOMAIN = os.environ.get("FEDERATED_EMAIL_DOMAIN", "amazon.com")

# LDAP-style alias sanitizer so we never emit a malformed email local-part.
_ALIAS_OK = re.compile(r"[^a-zA-Z0-9._-]")


def _derive_alias(event: dict) -> str:
    """Best-effort extraction of the Amazon alias for a federated user.

    Primary source: the `identities` user attribute (userId = raw IdP subject). Fallback:
    strip the "<ProviderName>_" prefix Cognito prepends to the federated username.
    """
    attrs = (event.get("request", {}) or {}).get("userAttributes", {}) or {}
    identities = attrs.get("identities")
    if identities:
        try:
            import json
            parsed = json.loads(identities) if isinstance(identities, str) else identities
            if isinstance(parsed, list) and parsed:
                uid = parsed[0].get("userId")
                if uid:
                    return str(uid)
        except Exception:
            pass
    # Fallback: userName is like "AmazonFederate_alias" → take the part after the last "_".
    username = event.get("userName", "") or ""
    if "_" in username:
        return username.rsplit("_", 1)[-1]
    return username


def _is_internal_idp_group(g: str, user_pool_id: str) -> bool:
    """Cognito auto-creates a per-IdP group named "<poolId>_<providerName>" (e.g.
    "us-east-1_ezN75iP5p_AmazonFederate") and auto-adds every federated user to it. That
    group is an internal artifact, NOT a real authorization group — it must be ignored when
    deciding whether a user "already has groups", or the default-group assignment below
    would be skipped for every federated user (they always carry this one)."""
    return g == user_pool_id or g.startswith(f"{user_pool_id}_")


def _real_groups(current_groups: list, user_pool_id: str) -> list:
    return [g for g in (current_groups or []) if not _is_internal_idp_group(g, user_pool_id)]


def _ensure_default_group(user_pool_id: str, username: str, real_groups: list) -> None:
    """Ensure the user is in DEFAULT_GROUP. We only skip if they already have a REAL group
    (so an admin who later moves a user to a different group isn't overridden). Adding to a
    group the user is already in is a harmless no-op, so this is safe + idempotent."""
    if real_groups:
        return  # already has a real group (assigned on a prior login or by an admin).
    try:
        cognito.admin_add_user_to_group(UserPoolId=user_pool_id, Username=username, GroupName=DEFAULT_GROUP)
        print(f"[pretoken] added '{username}' to group '{DEFAULT_GROUP}'")
    except Exception as e:
        print(f"[pretoken] admin_add_user_to_group failed (continuing): {type(e).__name__}: {str(e)[:200]}")


def _persist_identity(user_pool_id: str, username: str, attrs: dict, email: str, name: str) -> None:
    """Persist email/name on the profile only if missing or changed (avoids needless writes)."""
    to_write = []
    if (attrs.get("email") or "") != email:
        to_write.append({"Name": "email", "Value": email})
    if (attrs.get("name") or "") != name:
        to_write.append({"Name": "name", "Value": name})
    if not to_write:
        return
    try:
        cognito.admin_update_user_attributes(UserPoolId=user_pool_id, Username=username, UserAttributes=to_write)
        print(f"[pretoken] persisted identity for '{username}': email={email!r} name={name!r}")
    except Exception as e:
        # Non-fatal: the token still carries the claims below; a later login retries the write.
        print(f"[pretoken] admin_update_user_attributes failed (continuing): {type(e).__name__}: {str(e)[:200]}")


def handler(event, context):
    user_pool_id = event["userPoolId"]
    username = event.get("userName", "")
    req = event.get("request", {}) or {}
    attrs = req.get("userAttributes", {}) or {}

    current = (req.get("groupConfiguration", {}) or {}).get("groupsToOverride") or []
    real = _real_groups(current, user_pool_id)
    _ensure_default_group(user_pool_id, username, real)

    # Synthesize a stable, human-friendly identity from the Federate alias.
    alias = _ALIAS_OK.sub("", _derive_alias(event)).lower()
    existing_email = attrs.get("email") or ""
    email = existing_email if "@" in existing_email else (f"{alias}@{EMAIL_DOMAIN}" if alias else "")
    name = attrs.get("name") or alias

    if alias:
        _persist_identity(user_pool_id, username, attrs, email, name)

    # Shape THIS token so the UI has identity + group immediately (before the persisted
    # writes above surface on the next login).
    resp = event.setdefault("response", {})
    claims = {}
    if email:
        claims["email"] = email
        claims["email_verified"] = "true"  # trusted corporate IdP
    if name:
        claims["name"] = name

    # Groups in THIS token: the user's real groups (excluding the internal IdP group) plus
    # DEFAULT_GROUP, so the very first session already reflects admin without waiting for the
    # persisted membership to surface on the next login. Dedupe, preserve order.
    token_groups = list(dict.fromkeys([*real, DEFAULT_GROUP]))
    resp["claimsOverrideDetails"] = {
        "claimsToAddOrOverride": claims,
        "groupOverrideDetails": {
            "groupsToOverride": token_groups,
        },
    }
    return event
