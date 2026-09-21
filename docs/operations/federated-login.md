# Federated login (OIDC SSO)

Tessera can add a **Federated Login** button to the sign-in screen alongside the default
email/password form. It federates an external OIDC identity provider (for example Amazon
Federate) into the Cognito user pool. It is **off by default** and fully **config-gated**,
so it can be enabled for a demo and cleanly removed before publishing — no code deletion
required.

## Sign-in model (important)

The Cognito user pool uses **alias attributes**, not email-as-username:

```hcl
alias_attributes         = ["email", "preferred_username"]
auto_verified_attributes = ["email"]
```

This matters for federation. Amazon Federate's OIDC token carries **no `email` claim** —
its only identity claim is `sub`, which is the Amazon **alias** (e.g. `alias`). With an
email-username pool (`username_attributes = ["email"]`), Cognito derives the federated
username from `sub`, which is not a valid email, and sign-in fails with
`username attribute mapping required`. With **alias attributes**, the federated username is
an opaque, IdP-prefixed value (`AmazonFederate_<sub>`), and email/preferred_username are
merely optional sign-in aliases — so Federate's email-less identity is accepted directly,
with **no email-synthesis workaround needed**.

> `username_attributes` vs `alias_attributes` is fixed at pool creation and **cannot** be
> changed on an existing pool. Switching it forces the pool (and every resource keyed on
> its id — app clients, authorizers, the Verified Permissions identity source, etc.) to be
> recreated.

## Security posture (this deployment)

- **Self-registration is disabled.** `allow_self_signup = false` sets
  `admin_create_user_config.allow_admin_create_user_only = true`, so there is no
  "Create Account" path from the login screen. This was the original security requirement:
  federated SSO is the sign-in path; local accounts are admin-created only.
- **Federated users are non-admin by default; admins are promoted via the Admin Console.**
  The `federated-pretoken` trigger places every NEW federated (SSO) user into the
  least-privilege **`Users`** group (`federated_login.default_group = "Users"`). Admin rights
  come from membership of the **`admin`** group, which the Verified Permissions (Cedar) policy
  `admin_full_access` grants full access. An existing admin promotes a user by adding them to
  the `admin` group in the **Admin Console → Groups** tab (`groups` Lambda, admin-gated
  `POST /api/admin/groups/admin/members`). The trigger only assigns the default group to users
  with **zero real groups**, so a promotion is never overwritten on the user's next login.
  - **Seeded admin:** `you@example.com` is the bootstrap administrator. The `admin` group
    is created/seeded out-of-band (not via `default_group`), so changing `default_group` never
    touches it — the group is intentionally NOT Terraform-managed (removed from state) to
    prevent a `default_group` change from destroying it and its memberships.
  - To seed the first admin on a fresh pool, run `make create-admin EMAIL=<you>@amazon.com`
    (adds the `admin` group + the user), then promote others from the UI.

## What gets created when enabled

Setting `federated_login.enabled = true` turns on (all driven by the one config object):

- **OIDC identity provider** on the Cognito user pool (in the `identity` module).
- **Frontend app-client OAuth**: the existing SPA client gains the authorization-code
  flow, the federated provider in `supported_identity_providers`, and callback/logout
  URLs pointing at the CloudFront origin. The *same* client is used for native and
  federated sign-in, so every backend token verifier keeps working unchanged.
- **Two Cognito trigger Lambdas** (in the `auth-federated` module):
  - `federated-presignup` — links a federated login to an existing native account by
    verified email so the user's `sub` (and therefore all ownership + shares) is
    preserved; auto-confirms under admin-create-only.
  - `federated-pretoken` — places federated users into `default_group` (here, `Users`).
- The pool's `PreSignUp` / `PreTokenGeneration` triggers wired to those Lambdas.
- The `default_group` group (`create_users_group = true`).

When `enabled = false`, none of the above is created and the button is hidden.

## Enable it

1. **Register a Federate (OIDC) app** for Tessera and obtain its `client_id` and client
   secret. Store the secret in Secrets Manager and note the secret ARN.

2. **Set the config in a local, gitignored override.** The committed
   `infra/environments/<env>/terraform.tfvars` ships `federated_login = { enabled = false }`
   so a fresh clone deploys cleanly with no account-specific values in git. To turn SSO on,
   put the **whole** object in `infra/environments/<env>/federated.auto.tfvars` — this matches
   `*.auto.tfvars` in `.gitignore`, so the real `client_id` / `client_secret_arn` never get
   committed:

   ```hcl
   # infra/environments/<env>/federated.auto.tfvars  (gitignored)
   federated_login = {
     enabled            = true
     provider_name      = "AmazonFederate"
     issuer             = "https://idp-integ.federate.amazon.com"  # PROD: https://idp.federate.amazon.com
     client_id          = "<your Federate OIDC client id>"
     client_secret_arn  = "<Secrets Manager ARN of the client secret>"
     default_group      = "Users"   # federated users default to non-admin; promote to admin via the UI
     create_users_group = true      # creates default_group if the pool doesn't have it
   }
   ```

   > **Set the whole object, not just `enabled`.** `federated_login` is a single object
   > variable and Terraform does **not** merge objects across files — the auto-loaded
   > `federated.auto.tfvars` (higher precedence) replaces the committed value entirely. Any
   > field you omit here falls back to the variable's own default, not to what's in
   > `terraform.tfvars`.

   Leaving `client_secret_arn` empty provisions the IdP with a `REPLACE_ME` secret — the
   button appears but sign-in fails until a real secret is supplied. Prefer the ARN over a
   plaintext secret so it never lands in Terraform state.

3. **Apply Terraform** (from `infra/`, after `make package` so the trigger Lambda zips
   exist). Terraform **auto-loads** every `*.auto.tfvars`, so both `federated.auto.tfvars`
   and `agent.auto.tfvars` are picked up without naming them — you only pass the base file:

   ```bash
   terraform apply -var-file=environments/dev/terraform.tfvars
   # federated.auto.tfvars + agent.auto.tfvars are loaded automatically
   ```

4. **Whitelist the callback on the Federate side** (this cannot be done in Terraform). The
   Federate app must allow this pool's hosted-UI callback:

   ```
   https://<cognito_domain>/oauth2/idpresponse
   ```

   Get `<cognito_domain>` with `terraform -chdir=infra output -raw cognito_domain`. The
   domain is stable across pool recreations (it is suffixed with the account id, not the
   pool id), so a pool recreate does **not** require re-whitelisting.

5. **Rebuild + publish the frontend** so the button renders and the new pool/client ids are
   baked into the bundle:

   ```bash
   make deploy-frontend
   ```

   `deploy-frontend.sh` reads the Cognito ids + `federated_login_enabled` /
   `federated_provider_name` from Terraform outputs and writes the `NEXT_PUBLIC_*` env vars
   automatically — no manual env editing. **Always run this after a pool recreate**, or the
   frontend will keep pointing at the old (deleted) pool/client id.

## Disable it (before publishing)

1. Turn it off. Either delete `environments/<env>/federated.auto.tfvars`, or set
   `federated_login = { enabled = false }` in it. (The committed `terraform.tfvars` is
   already `enabled = false`, so removing the override alone disables SSO.)
2. `terraform apply -var-file=environments/dev/terraform.tfvars` — this destroys the IdP +
   trigger Lambdas and reverts the app client to Cognito-local (SRP only).
3. `make deploy-frontend` — the button disappears (the env var becomes `false`).

Native email/password login is unaffected in both states. Note that disabling federated
login does **not** re-enable self-registration; that is controlled separately by
`allow_self_signup`.

## Notes

- **Account linking is by verified email.** A federated login for `alice@corp.com` links
  into an existing native `alice@corp.com` so ownership/permissions carry over. The
  pre-token trigger only assigns `default_group` to users who currently have **zero**
  groups, so it never downgrades a user who was already assigned (idempotent per user).
- **Attribute mapping.** The OIDC provider mapping is `{ email = "email", username = "sub" }`.
  Cognito locks the `username` mapping to `sub` (it rejects mapping `username` to `email`
  or `preferred_username`). Because the pool uses alias attributes, the `sub`-derived
  opaque username is accepted; `email = "email"` simply maps nothing for Amazon Federate
  (no email claim) and correctly populates the email alias for IdPs that do emit one.

## Productization note (customer / public release)

There is **no internal-only workaround left to strip** — the earlier Inbound Federation
email-synthesis Lambda (`app/lambdas/federated-inbound/`, `scripts/wire-inbound-federation.sh`,
the `terraform_data.inbound_federation_trigger` block, and the `email_domain` config field)
has been removed. Federation now works through the standard PreSignUp + PreTokenGeneration
triggers on an alias-attributes pool.

For a real customer IdP that emits a proper `email` claim, no change is required: the
`email = "email"` mapping populates the email alias, and account linking by verified email
works as intended. Before a public release, review only the deployment-specific policy
choices in this doc — principally **`default_group`** (federated users default to the
non-admin `Users` group; admins are promoted via the Admin Console) and
**`demo_open_access`** — and tighten them to match the target environment.
