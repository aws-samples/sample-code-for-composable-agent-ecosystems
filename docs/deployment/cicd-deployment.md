# CI/CD deployment (GitLab)

The pipeline in [`cicd/.gitlab-ci.yml`](../../cicd/.gitlab-ci.yml) mirrors the local
deploy: it runs Terraform **directly** (`terraform init` / `plan` / `apply` against the
per-environment backend + tfvars) and uses the same `make` targets for the non-Terraform
steps (packaging, agent, frontend, seed). The differences from local are AWS
authentication (OIDC role assumption instead of a local profile), remote state, and a
manual approval gate before applying to staging/prod.

## Pipeline stages

```
validate → build → plan → apply → agent → frontend → seed
```

- **validate** — `terraform fmt -check`, `terraform validate`, and advisory security
  scans (`checkov`, `bandit`).
- **build** — `make package` (Lambda zips + shared layer) and the Next.js build;
  artifacts are passed to later stages.
- **plan** — `terraform plan` for the target environment, artifacted as `tfplan`.
- **apply** — `dev` applies automatically; `staging`/`prod` require a **manual** trigger
  (`when: manual`, protected).
- **agent** / **frontend** / **seed** / **ingest** — call `make deploy-agent` /
  `make deploy-frontend` / `make seed` / `make ingest`. `seed` publishes content +
  compiles ecosystems (fast). `ingest` is SEPARATE and builds + synthesizes + promotes the
  catalog (long-running: Bedrock synthesis per cluster). The agent and ingest jobs are
  **manual** (they call external CLIs / long synthesis) and non-blocking.

> **No interactive prompts in CI.** `make deploy-agent` invokes `agentcore configure`
> with `--non-interactive` (and an empty request-header allowlist), so it never asks
> questions such as "Configure request header allowlist?" and never needs a TTY. The
> pipeline runs it unattended.

## AWS authentication (OIDC — no static keys)

Jobs assume an AWS role via GitLab's OIDC id token (`sts:AssumeRoleWithWebIdentity`).
There are no static AWS keys in the pipeline.

1. In AWS, create an IAM OIDC identity provider for GitLab (`https://gitlab.com`) and a
   role whose trust policy allows your project/branch to assume it.
2. In GitLab, set the CI/CD variable **`TF_ROLE_ARN`** (protected + masked) to that
   role's ARN.
3. The `.aws_oidc` job template requests an id token (`aud: https://gitlab.com`), writes
   it to a file, points `AWS_WEB_IDENTITY_TOKEN_FILE` at it, and runs
   `aws sts get-caller-identity` to confirm.

## Configuration

- **`ENV`** — pipeline variable selecting the environment (`dev` | `staging` | `prod`).
  Wire it per branch or trigger as suits your branching model.
- **`AWS_REGION`** — defaults to `us-east-1`.
- Remote state is configured per environment via `infra/environments/<env>/backend.hcl`
  (see [state backend](state-backend.md)). The state bucket must already exist.

## Protected branches / environments

Point production deploys at a protected branch and mark `TF_ROLE_ARN` (and any prod
credentials) as protected so only pipelines on that branch can assume the role. The
manual gate on `apply:gated` gives a human checkpoint before any staging/prod change.

## Tooling image

The jobs use `hashicorp/terraform:1.11` and install `aws-cli`, Python, Node, `make`,
`zip`, and `openssl` in `before_script`. Swap in your team's prebuilt toolchain image if
you maintain one.
