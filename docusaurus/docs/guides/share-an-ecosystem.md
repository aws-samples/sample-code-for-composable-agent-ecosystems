---
sidebar_position: 5
---

# Share an ecosystem

Ecosystems are owned by the user who creates them, and access is governed per-ecosystem.
This guide covers sharing an ecosystem with other users and how authorization works.

## How authorization works

Tessera authorizes access with **Amazon Verified Permissions** using **Cedar** policies,
evaluated per ecosystem. Ownership and shares are keyed on the user's Cognito `sub`, so
sharing grants specific principals access without exposing everything.

- The **owner** has full control (edit, run, share, delete).
- **Shared** principals get the access level you grant (e.g. run / view).

## Share from the UI

1. Open the ecosystem.
2. Open its **sharing** dialog.
3. Add a principal (a user, and depending on setup a group) and choose the access level.
4. Save. The Cedar policy is created for that principal + ecosystem.

To revoke, remove the entry — the corresponding policy is deleted.

## Admin sharing

Admins can manage sharing across users from the admin console — listing shares by
principal and adding or removing access. This is useful for onboarding a team to a set of
ecosystems.

## Groups

Where group-based access is enabled, you can share with a Cognito **group** instead of
individual users, so membership changes don't require re-sharing each ecosystem.

## Notes

- Sharing controls **access to the ecosystem**, not to the underlying AWS resources —
  those remain governed by IAM and the platform's security model (see
  [Architecture](../core-concepts/architecture.md)).
- Federated SSO is off by default; when enabled, shared principals can be your IdP
  identities. See the repository's `docs/operations/federated-login.md`.
