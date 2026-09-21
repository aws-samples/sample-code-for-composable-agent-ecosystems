# Local deployment (from your laptop)

You deploy the platform in two kinds of steps:

- **Terraform steps** — run `terraform` **directly** from the `infra/` directory
  (`init` / `plan` / `apply`). Running them directly keeps the output and any errors
  in plain view, which makes troubleshooting far easier than wrapping them in `make`.
- **Non-Terraform steps** — Lambda packaging, the AgentCore runtime, the frontend
  build/publish, seeding, and admin creation are `make` targets (thin wrappers around
  the scripts in `scripts/`).

## Prerequisites

- **Terraform >= 1.11** (S3-native state locking).
- **AWS CLI v2**, authenticated to your target account (SSO profile or exported creds).
- **Python 3.12** and pip (Lambda packaging). Install e.g. `brew install python@3.12`
  (macOS) or `pyenv install 3.12`.
- **Node.js 20+** and npm (frontend build). Install e.g. `nvm install 20` or `brew install node`.
- **`agentcore` CLI** — `pip install bedrock-agentcore-starter-toolkit` (agent runtime).
- **zip**, **jq**, **openssl** (used by the scripts).
- **Amazon Bedrock model access** enabled in your region for the models in `var.models`.

Your AWS principal needs permission to create the full resource set (Lambda, API
Gateway, DynamoDB, S3, CloudFront, WAF, Cognito, KMS, SQS, SNS, VPC, Step Functions,
EventBridge, IAM, Verified Permissions, Bedrock/AgentCore, Secrets Manager).

## One-time setup

1. Copy `.env.example` to `.env` and set `AWS_PROFILE`, `AWS_REGION`, `ENVIRONMENT`.
2. **Authenticate** to AWS (e.g. `aws sso login --profile your-profile`, or ensure your
   profile's keys are valid). Verify with `aws sts get-caller-identity --profile your-profile`.
3. **Bootstrap the state bucket**: `make bootstrap` — creates the versioned, encrypted S3
   state bucket and writes its name into every `environments/<env>/backend.hcl`. This is
   **required before the first `terraform init`**: `backend.hcl` ships with a placeholder
   bucket (`REPLACE_ME_TF_STATE_BUCKET`), so `init` fails until bootstrap fills it in (or you
   set it yourself via `TF_STATE_BUCKET` / by editing the file). See
   [state backend](state-backend.md).

## Deploy — step by step

### 1. Package the Lambdas

```bash
make package
```

Builds `build/lambdas/shared_layer.zip` + one zip per Lambda (with Linux wheels).
Terraform consumes these on `apply`. First run takes a few minutes.

### 2. Load your environment + Terraform flags

`load-env.sh` reads `.env`, exports `AWS_PROFILE` / `AWS_REGION` / `ENVIRONMENT`, and sets
`$TF_BACKEND` and `$TF_VARS` so the terraform commands below are short and correct for the
chosen environment. **Source** it (don't execute it):

```bash
source scripts/load-env.sh          # uses ENVIRONMENT from .env (default: dev)
# or target another environment:
source scripts/load-env.sh staging
```

It prints the flags it set, e.g.:

```
TF_BACKEND = -backend-config=environments/dev/backend.hcl
TF_VARS    = -var-file=environments/dev/terraform.tfvars
```

### 3. Run Terraform directly

```bash
cd infra
terraform init  $TF_BACKEND
terraform plan  $TF_VARS            # review the plan
terraform apply $TF_VARS
cd ..
```

If you prefer not to source the helper, the equivalent explicit commands are:

```bash
cd infra
terraform init  -backend-config=environments/dev/backend.hcl
terraform plan  -var-file=environments/dev/terraform.tfvars
terraform apply -var-file=environments/dev/terraform.tfvars
cd ..
```

On the first apply, `agent_runtime_arn` is empty (the runtime doesn't exist yet); the
Lambdas point at a placeholder until step 4.

### 4. Deploy the AgentCore runtime, then re-apply to wire it

```bash
make deploy-agent
```

This deploys the runtime via the `agentcore` CLI and **saves the ARN for you** (no
copy/paste) to two places:

- `infra/environments/<env>/agent.auto.tfvars` — the runtime ARN, picked up on the next
  apply **when `$TF_VARS` includes this file** (which `load-env.sh` arranges — see the
  important note below).
- `.deploy-logs/agent-runtime-arn.env` — a source-able file exposing `$AGENT_RUNTIME_ARN`.

Then re-apply so the ARN is wired into the Lambdas:

```bash
source scripts/load-env.sh        # re-source so $TF_VARS includes agent.auto.tfvars
cd infra
terraform apply $TF_VARS          # agent.auto.tfvars supplies agent_runtime_arn
cd ..
```

> **IMPORTANT — you MUST pass `agent.auto.tfvars`, and `$TF_VARS` (from `load-env.sh`) does
> that for you.** Terraform only auto-loads `*.auto.tfvars` from its **working directory**
> (`infra/`). Our override files live in the **`environments/<env>/` subdirectory**, so
> Terraform does **not** auto-load them — instead `scripts/load-env.sh` appends
> `-var-file=environments/<env>/agent.auto.tfvars` (and `federated.auto.tfvars` if present)
> to `$TF_VARS`. So always run `terraform apply $TF_VARS` **after sourcing `load-env.sh`**.
> If you instead run a bare `terraform apply -var-file=environments/<env>/terraform.tfvars`
> (without the agent var-file), `agent_runtime_arn` resolves to `""`, the Lambdas get an
> **empty** runtime ARN, and every agent/synthesis run fails with
> `InvokeAgentRuntime` **404 (`UnknownOperationException`)**. This is the single most common
> deploy mistake — always use `$TF_VARS`.

Two override files are used this way (both gitignored, both account-specific):
`agent.auto.tfvars` (the runtime ARN, above) and, if you enable SSO,
`federated.auto.tfvars` (the real `federated_login` object with `client_id` /
`client_secret_arn` — see [federated login](../operations/federated-login.md)).

Or, if you prefer passing the ARN explicitly instead of via `$TF_VARS`:

```bash
source .deploy-logs/agent-runtime-arn.env         # sets $AGENT_RUNTIME_ARN
cd infra
terraform apply -var-file=environments/dev/terraform.tfvars -var agent_runtime_arn="$AGENT_RUNTIME_ARN"
cd ..
```

(See [AgentCore runtime](agentcore-runtime.md) for details.)

### 5. Publish the frontend + seed content

```bash
make deploy-frontend                # build the UI, sync to S3, invalidate CloudFront
make seed                           # publish domains/catalog + compile ecosystems (fast)
```

`make deploy-frontend` installs the frontend's npm dependencies automatically on the
first run (when `app/frontend/node_modules` is absent), so you don't need a separate
install step. If the build fails with `next: command not found`, that install step was
skipped — delete `app/frontend/node_modules` and re-run, or run `npm install` in
`app/frontend` manually.

`make seed` publishes content and compiles ecosystems only — it does **not** ingest the
catalog. Catalog ingestion is a separate, optional step (below), so a slow,
externally-dependent ingest can't block or mask content setup.

### 6. (optional) Build + populate the catalog

```bash
make ingest                         # all default domains
make ingest DOMAINS="cyber-security-risk"   # or scope to specific domains
```

> **This is long-running.** `make ingest` runs each domain's **build state machine**
> end to end — `new_version → ingest_all → (for synthesis domains) plan_synthesis →
> per-cluster synthesis → validate_version` — then **promotes** the new version when the
> coverage gate passes, so both ingested AND synthesized types (e.g. `risk`) become live.
> Synthesis invokes Bedrock agents per tactic-cluster, so expect **several minutes to tens
> of minutes per synthesis-enabled domain.** It is idempotent and safely re-runnable.
> Some fetch sources pull live third-party/government data, so individual source failures
> (HTTP 403, timeouts) are non-blocking. **Enable Bedrock model access** for the region
> first, and run this **after** `make seed` (which compiles the synthesis ecosystem SM
> and publishes the source content this reads). The wait cap defaults to **60 minutes**;
> some **default** synthesis-heavy domains (notably `cyber-security-risk`) can exceed it on
> a fresh account — the build keeps running in AWS but isn't promoted by that invocation, so
> either raise the cap (`POLL_TIMEOUT_MIN=90 make ingest`) or re-run `make ingest` afterward
> to finish + promote (idempotent).

### 7. First login

```bash
make create-admin EMAIL=you@example.com
terraform -chdir=infra output -raw frontend_url     # open this URL
```

See [admin user setup](../operations/admin-user-setup.md).

### Optional: federated login (OIDC SSO)

To add a "Federated Login" button next to email/password, enable it via config — see
[federated login](../operations/federated-login.md). It is off by default and removable
by flipping `federated_login.enabled` back to `false`.

## Reference: all commands

| Step | Command |
|---|---|
| State bucket (one-time) | `make bootstrap` |
| Package Lambdas | `make package` |
| Load env + TF flags | `source scripts/load-env.sh [env]` |
| Terraform init | `cd infra && terraform init $TF_BACKEND` |
| Terraform plan | `terraform plan $TF_VARS` |
| Terraform apply | `terraform apply $TF_VARS` |
| Show flags for an env | `make tf-flags ENV=dev` |
| Deploy agent runtime | `make deploy-agent` (saves the ARN to `agent.auto.tfvars`), then **re-`source scripts/load-env.sh`** and `terraform apply $TF_VARS` (so the ARN var-file is included) |
| Deploy frontend | `make deploy-frontend` |
| Seed content | `make seed` (publish + compile ecosystems; fast) |
| Ingest catalog (optional, long) | `make ingest` (or `make ingest DOMAINS="cyber-security-risk"`) |
| Create admin | `make create-admin EMAIL=you@example.com` |
| Show outputs | `terraform -chdir=infra output` |
| Destroy | `cd infra && terraform destroy $TF_VARS` |

## Notes

- Terraform is intentionally **not** wrapped by `make`, so `init` / `plan` / `apply`
  errors are shown directly by terraform. `make tf-flags ENV=<env>` prints the exact
  commands if you forget them.
- The AgentCore runtime is deployed by the `agentcore` CLI (the only non-Terraform
  provisioning step); everything else is Terraform-managed.
- Web-search features expect a credential provider in the AgentCore Identity token
  vault named `${project}-${environment}-websearch`; create it out of band (it is never
  stored in the repo or in environment variables).
