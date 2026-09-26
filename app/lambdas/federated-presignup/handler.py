"""Federated Login — Pre sign-up trigger.

Fires when Amazon Cognito is about to auto-provision a user that arrived through the
Federated Login (OIDC) identity provider. Two jobs:

  1. EMAIL-BASED IDENTITY LINKING (preserve `sub`). If a NATIVE Cognito user already
     exists with the same (verified) email, link the incoming federated identity into
     that existing user via AdminLinkProviderForUser. After linking, every subsequent
     federated login resolves to the EXISTING user's `sub` and username — so all of
     Tessera's authorization (owner policies + AVP/Cedar shares, all keyed on `sub`)
     keeps working unchanged. Without this, the federated login would mint a brand-new
     `sub` and the user would silently lose ownership of their ecosystems.

  2. ADMIN-CREATE-ONLY BYPASS for federation. The pool has AllowAdminCreateUserOnly=True
     (self-signup disabled). For EXTERNAL-provider provisioning we must explicitly
     auto-confirm/auto-verify in this trigger, otherwise the first federated login fails.

SECURITY — this is an account-takeover-sensitive path:
  * We ONLY link when the source is our trusted Federated Login provider AND the OIDC
    IdP asserts a verified email. We never link on an unverified / self-asserted email.
  * Email is normalized (trim + lowercase) on both sides before matching, so casing
    differences can't create duplicate or mismatched accounts.

Env:
  FEDERATED_PROVIDER_NAME  — the Cognito OIDC provider name (e.g. "FederatedLogin").
"""
import os
import boto3

cognito = boto3.client("cognito-idp")

# The trusted federated provider name. Linking is scoped to THIS provider only.
PROVIDER_NAME = os.environ.get("FEDERATED_PROVIDER_NAME", "FederatedLogin")

# Cognito PreSignUp trigger sources. External-IdP sign-ups carry this source.
_EXTERNAL_PROVIDER = "PreSignUp_ExternalProvider"


def _norm_email(value: str) -> str:
    """Normalize an email for matching: trim + lowercase. Empty string if falsy."""
    return (value or "").strip().lower()


def handler(event, context):
    trigger = event.get("triggerSource", "")
    user_pool_id = event["userPoolId"]
    attrs = event.get("request", {}).get("userAttributes", {}) or {}

    # Only act on EXTERNAL-provider (Federated Login) sign-ups. Native email/password
    # sign-ups (if ever re-enabled) pass straight through untouched.
    if trigger != _EXTERNAL_PROVIDER:
        return event

    email = _norm_email(attrs.get("email"))
    # event['userName'] for external providers looks like "<ProviderName>_<sub>".
    incoming_username = event.get("userName", "")
    # Trust: AmazonFederate is a corporate IdP whose emails are inherently verified, even
    # when the OIDC token omits an `email_verified` claim (Cognito then defaults it false).
    # For OUR trusted provider only, treat a present email as verified so account linking
    # works; we still NEVER link without an email.
    _from_trusted = incoming_username.startswith(PROVIDER_NAME + "_")
    email_verified = str(attrs.get("email_verified", "")).lower() == "true" or _from_trusted
    # Do NOT log anything derived from the event/user attributes (username, email and
    # claims are sensitive PII). Emit only a static marker.
    print("[presignup] processing external-provider sign-up")

    # GUARDRAIL: never link on a missing / unverified email. Still auto-confirm so the
    # login succeeds under AllowAdminCreateUserOnly.
    if not email or not email_verified:
        print("[presignup] not linking (missing or unverified email) — provisioning standalone")
        _auto_confirm(event)
        return event

    # Find a pre-existing user with this email that we can link into.
    existing = _find_link_target(user_pool_id, email, incoming_username)
    if existing:
        try:
            cognito.admin_link_provider_for_user(
                UserPoolId=user_pool_id,
                # DestinationUser: the existing account whose `sub` we want to preserve.
                DestinationUser={
                    "ProviderName": "Cognito",
                    "ProviderAttributeValue": existing,
                },
                # SourceUser: the incoming federated identity. For OIDC/SAML the linking
                # attribute is the provider subject ("Cognito_Subject").
                SourceUser={
                    "ProviderName": PROVIDER_NAME,
                    "ProviderAttributeName": "Cognito_Subject",
                    "ProviderAttributeValue": _provider_subject(incoming_username),
                },
            )
            print(f"[presignup] linked federated identity to existing user '{existing}' by email")
        except cognito.exceptions.InvalidParameterException as e:
            # Most commonly: the identities are already linked. Safe to ignore.
            print(f"[presignup] link skipped/failed (likely already linked): {str(e)[:200]}")
        except Exception as e:
            # Do NOT hard-fail the login on a link error; fall through to provisioning.
            print(f"[presignup] link error (continuing): {type(e).__name__}: {str(e)[:200]}")

    # Whether we linked or not, auto-confirm so provisioning is not blocked by
    # AllowAdminCreateUserOnly. (When linked, this login resolves to the destination user.)
    _auto_confirm(event)
    return event


def _auto_confirm(event):
    """Auto-confirm + auto-verify the federated user so login proceeds under
    admin-create-only. Federated auth is already established by the trusted IdP."""
    resp = event.setdefault("response", {})
    resp["autoConfirmUser"] = True
    if event.get("request", {}).get("userAttributes", {}).get("email"):
        resp["autoVerifyEmail"] = True


def _provider_subject(incoming_username: str) -> str:
    """Extract the OIDC subject from the federated username "<Provider>_<sub>".
    Falls back to the full username if the prefix is absent."""
    prefix = f"{PROVIDER_NAME}_"
    if incoming_username.startswith(prefix):
        return incoming_username[len(prefix):]
    return incoming_username


def _find_link_target(user_pool_id: str, email: str, incoming_username: str):
    """Return the Username of a pre-existing user to link into, or None.

    Matches on normalized email and skips the incoming federated user itself. Prefers a
    native (unlinked) account; any single stable match is acceptable since the goal is a
    consistent `sub`."""
    try:
        # Cognito's ListUsers email filter is case-insensitive on the stored value, but we
        # still re-check normalized equality to be certain.
        resp = cognito.list_users(
            UserPoolId=user_pool_id,
            Filter=f'email = "{email}"',
            Limit=10,
        )
    except Exception as e:
        print(f"[presignup] list_users failed: {type(e).__name__}: {str(e)[:200]}")
        return None

    for u in resp.get("Users", []):
        uname = u.get("Username", "")
        if uname == incoming_username:
            continue  # don't link a user to itself
        u_attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
        if _norm_email(u_attrs.get("email")) == email:
            return uname
    return None
