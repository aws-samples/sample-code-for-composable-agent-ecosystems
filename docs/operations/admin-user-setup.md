# Admin user setup

Self-registration is disabled by default (admin-create-only), so a freshly deployed
platform has no users and no `admin` group — nobody can sign in until you bootstrap the
first admin. This uses Cognito's IAM-authenticated admin APIs (never a public signup), so
only someone with AWS access can mint that first user. (Self-registration can be turned on
per environment — see [Optional: let users self-register](#optional-let-users-self-register-devdemo).)

## Create the first admin

```bash
make create-admin ENV=dev EMAIL=you@example.com
```

This:

1. Ensures the `admin` Cognito group exists (the app authorizes admins by membership).
2. Creates the user.
3. Adds the user to `admin`.

By default a **temporary password is emailed** to the address (Cognito email delivery
must work for it). On first sign-in the user is forced to set a new 12+ character
password and is prompted to enroll MFA (TOTP).

## When email delivery isn't configured

```bash
bash scripts/create-admin.sh you@example.com --show-temp
```

`--show-temp` generates a one-time temporary password and writes it to a `0600` file
under `.deploy-logs/` (gitignored) instead of emailing it. The password is still a
forced reset on first sign-in.

## After the first admin

Open the frontend (`terraform -chdir=infra output -raw frontend_url`), sign in, and create
all further users **in-app** from the admin console. Keep self-registration disabled for
staging/prod.

## Optional: let users self-register (dev/demo)

For a shared dev/demo where colleagues need their own accounts, you can enable a
"Create Account" option on the login screen instead of minting each user by hand. This is
config-driven and off by default.

In the environment's `terraform.tfvars`:

```hcl
allow_self_signup       = true    # shows "Create Account"; disables admin-create-only
signup_require_approval = false   # false = new users can sign in immediately
                                  # true  = new users are disabled until an admin enables them
```

Then apply and rebuild the frontend so the tab renders:

```bash
cd infra && terraform apply $TF_VARS && cd ..
make deploy-frontend   # derives NEXT_PUBLIC_ALLOW_SELF_SIGNUP from the Terraform output
```

Colleagues can now open the frontend URL, choose **Create Account**, register with an
email + password, and (with `signup_require_approval = false`) sign in right away.

**Security note.** With `allow_self_signup = true` and approval off, anyone who can reach
the URL can create a working account. That is acceptable for a throwaway dev/demo, but
**do not enable it for staging or prod**. To lock it back down, set `allow_self_signup =
false`, re-apply, and `make deploy-frontend`; existing self-registered users can be
deleted from the Cognito console if needed. If you want self-registration but with a
gate, set `signup_require_approval = true` — new users then stay disabled until an admin
enables them (admins are notified via the signup SNS topic; set `admin_notify_emails`).

## Prefer federated SSO for an organization

For an organization, enable federated (OIDC) SSO instead of Cognito-local users: set
`federated_login.enabled = true` with your IdP's `client_id` / `client_secret_arn` in the
environment's `terraform.tfvars`. Auth is then delegated to your corporate IdP (MFA and
lifecycle handled centrally). See [variables](../configuration/variables.md) and
[federated login](./federated-login.md).

### Federated users are non-admin by default

Federated (SSO) sign-ins are placed into the non-admin **`Users`** group
(`federated_login.default_group = "Users"`), so a new SSO user has no admin rights. An
existing admin **promotes** a user by adding them to the `admin` group in the
**Admin Console → Groups** tab (select `admin`, pick the user, Add). Group membership is
carried in the ID token, so a promoted (or demoted) user picks up the change on their next
sign-in. Because the `admin` group is seeded out-of-band (via `make create-admin`, above)
rather than by `default_group`, it is not destroyed if `default_group` ever changes.
