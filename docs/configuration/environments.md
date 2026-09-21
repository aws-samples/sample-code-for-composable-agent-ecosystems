# Environments

Three environments are provided: **dev**, **staging**, and **prod**. Each has its own
variable file and state, so they are fully isolated within an account (and can target
different accounts by authenticating to each).

```
infra/environments/
├── dev/      { terraform.tfvars, backend.hcl }
├── staging/  { terraform.tfvars, backend.hcl }
└── prod/     { terraform.tfvars, backend.hcl }
```

- `terraform.tfvars` — the input values for that environment (committed; no secrets or
  account-specific values).
- `backend.hcl` — the S3 state bucket + a per-environment state key. The `bucket` ships as
  a placeholder (`REPLACE_ME_TF_STATE_BUCKET`) and is filled in by `make bootstrap` before
  the first `init` (see [state backend](../deployment/state-backend.md)).

Two **optional, gitignored** override files may also appear in an environment directory.
Terraform auto-loads any `*.auto.tfvars`, so they merge in on a normal apply without being
named on the command line, and they never get committed (`*.auto.tfvars` is in
`.gitignore`):

- `agent.auto.tfvars` — the deployed AgentCore runtime ARN, written by
  `make deploy-agent` (see [AgentCore runtime](../deployment/agentcore-runtime.md)).
- `federated.auto.tfvars` — the real `federated_login` object (account-specific
  `client_id` / `client_secret_arn`) when SSO is enabled (see
  [federated login](../operations/federated-login.md)).

Select an environment with `source scripts/load-env.sh <env>` (which sets `$TF_BACKEND`
and `$TF_VARS`), then run terraform directly from `infra/`. Re-running
`terraform init $TF_BACKEND` points the backend at that environment's `backend.hcl`. The
`make` helper targets read `ENVIRONMENT` from `.env` (or `ENV=<env>` on the command line).

## Defaults per environment

| Setting | dev | staging | prod |
|---|---|---|---|
| `vpc_nat_gateways` | 1 | 1 | 2 (AZ-level HA) |
| `log_retention_days` | 30 | 30 | 90 |
| `enable_quorum` | true | true | true |
| `federated_login.enabled` | false | false | false (fill in real IdP to enable) |
| state key | `tessera/dev/…` | `tessera/staging/…` | `tessera/prod/…` |

Resource names are `tessera-<env>-*`, so all three can coexist in one account without
collision.

## Promoting a change

1. Merge to your default branch and let `dev` apply automatically (CI), or locally run
   the deploy steps with `source scripts/load-env.sh dev`.
2. Validate in `dev`.
3. Trigger the manual apply gate for `staging`, then `prod` (CI), or locally run the
   deploy steps with `source scripts/load-env.sh staging` / `prod`.

## Customizing an environment

Edit that environment's `terraform.tfvars` — for example, set real
`admin_notify_emails`, tune `models`, or raise `vpc_nat_gateways`. To enable
`federated_login` with your IdP's `client_id` + `client_secret_arn`, put the object in the
gitignored `federated.auto.tfvars` instead (keeps account-specific values out of git) — see
[federated login](../operations/federated-login.md).
