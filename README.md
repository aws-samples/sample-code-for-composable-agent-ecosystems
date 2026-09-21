# Tessera

Composable agentic security platform — deployed to AWS with Terraform.

Tessera lets security teams orchestrate multi-agent workflows ("ecosystems") through
configuration rather than code. This repository contains the application code and a
config-driven, modular Terraform project that stands the whole platform up in your AWS
account, deployable both from a laptop and through GitLab CI/CD.

## Repository layout

```
├── app/          Application code
│   ├── lambdas/     control-plane + ingest Lambda handlers (+ shared modules)
│   ├── platform/    shared platform package (tessera_platform.*)
│   ├── agent/       AgentCore runtime container
│   ├── frontend/    Next.js UI (static export)
│   ├── content/     ecosystems, domains, catalog content, templates (seed data)
│   └── tests/       unit / integration / ground-truth tests
├── infra/        Terraform — root module + 9 child modules + per-environment config
├── scripts/      package-lambdas, deploy-agent, deploy-frontend, seed, create-admin
├── cicd/         GitLab CI/CD pipeline
├── docs/         documentation (start at docs/README.md)
└── Makefile      local + CI entrypoints (single source of truth)
```

## Quick start (local)

```bash
# 0. Prereqs: Terraform >= 1.11, AWS CLI v2, Python 3.12, Node 20+, jq, zip, openssl, and the
#    agentcore CLI (pip install bedrock-agentcore-starter-toolkit). You also need Amazon
#    Bedrock model access enabled in your region for the models in var.models.
#    Install help: Python 3.12 -> `brew install python@3.12`; Node 20+ -> `nvm install 20`.
#    Full prerequisites list: docs/deployment/local-deployment.md.
# 1. Authenticate to AWS.
export AWS_PROFILE=your-profile

# 2. Configure your deploy (REQUIRED — the repo ships placeholders, not real values):
#    a. Create your local env file, then set AWS_PROFILE / AWS_REGION / ENVIRONMENT in it.
cp .env.example .env                  # then edit .env — AWS_REGION here is your deploy region
#    b. Set the region in the environment's tfvars (Terraform reads it from var.region, NOT
#       from AWS_REGION, so it MUST be a real region or `terraform plan` fails). Replace
#       "<your aws region>" (e.g. us-east-1) in:
#         infra/environments/<env>/terraform.tfvars   (region = "...")
#       Do NOT hand-edit backend.hcl's region: `make bootstrap` (step 3) writes it from
#       AWS_REGION. Just make sure AWS_REGION in .env matches the tfvars region above, so the
#       state bucket and the deployed resources land in the same region.
#    See docs/deployment/local-deployment.md for the full list of values to set.

# 3. One-time: create the Terraform state bucket and wire it into every backend.hcl.
make bootstrap                    # or: make bootstrap BUCKET=my-tf-state-bucket

# 4. Store the web-search API key (REQUIRED because enable_websearch = true by default).
#    Terraform reads this Secrets Manager secret at PLAN time, so it must exist BEFORE
#    the first `terraform plan` in step 5 — otherwise plan fails with
#    "reading Secrets Manager Secret Version (...-websearch-api-key): couldn't find resource".
#    Get a free API key from Tavily (https://app.tavily.com/), then store it (input hidden):
make set-websearch-key ENV=dev
#    To ship WITHOUT web search instead, set enable_websearch = false in
#    infra/environments/<env>/terraform.tfvars and skip this step.

# 5. Package the Lambdas, then run Terraform DIRECTLY (easy to read + troubleshoot).
make package
source scripts/load-env.sh            # loads .env + exports AWS creds + $TF_BACKEND / $TF_VARS
cd infra
terraform init  $TF_BACKEND
terraform plan  $TF_VARS
terraform apply $TF_VARS
cd ..

# 6. Deploy the AgentCore runtime, then re-apply to wire its ARN.
make deploy-agent                     # saves the ARN to environments/<env>/agent.auto.tfvars
cd infra && terraform apply $TF_VARS && cd ..   # picks up the saved ARN automatically

# 7. Publish the frontend + seed content, then create the first admin.
make deploy-frontend
make seed
make create-admin EMAIL=you@example.com
terraform -chdir=infra output -raw frontend_url

# 8. (optional) Build + populate the catalog. SEPARATE + LONG-RUNNING (several minutes
#    per domain; synthesis-enabled domains invoke Bedrock agents per cluster). This runs
#    the per-domain build state machine (ingest -> synthesis -> validate) and PROMOTES the
#    new version when the coverage gate passes, so synthesized types (e.g. `risk`) appear.
#    Idempotent/re-runnable. Needs Bedrock model access enabled.
#    Default poll cap is 60m (POLL_TIMEOUT_MIN). Large default domains — notably
#    cyber-security-risk — can exceed it: the build then CONTINUES in AWS but is NOT
#    promoted by this invocation. Re-run `make ingest DOMAINS="<domain>"` to finish +
#    promote (idempotent), or raise the cap up front: `POLL_TIMEOUT_MIN=90 make ingest`.
make ingest                           # or: make ingest DOMAINS="cyber-security-risk"
```

Terraform is run **directly** (not wrapped by `make`) so its output and errors are easy
to see. `make` only handles the non-Terraform steps (state bucket, Lambda packaging,
agent, frontend, seed, catalog ingest, admin). See
[docs/deployment/local-deployment.md](docs/deployment/local-deployment.md) for the full
step-by-step.

### First-run defaults (things a fresh clone won't have)

The repo ships with safe, account-agnostic defaults. A new deployer supplies the
account-specific bits themselves:

- **Environment file** — only `.env.example` is committed. Copy it to `.env` (step 2a) and
  set `AWS_PROFILE` / `AWS_REGION` / `ENVIRONMENT`; `scripts/load-env.sh` sources it.
- **AWS region** — `environments/<env>/terraform.tfvars` ships a `"<your aws region>"`
  placeholder; set it (step 2b) — Terraform reads the region from `var.region` (the tfvars),
  not from `AWS_REGION`, so `terraform plan` fails until it is a real region. `backend.hcl`'s
  region is written by `make bootstrap` from `AWS_REGION` (don't hand-edit it); keep
  `AWS_REGION` and the tfvars region the same so state + resources share a region.
- **State bucket** — `environments/<env>/backend.hcl` ships a placeholder
  (`REPLACE_ME_TF_STATE_BUCKET`); `make bootstrap` (step 3) creates the bucket and fills it
  in. Running `terraform init` before bootstrap fails on purpose.
- **Web-search API key** — `enable_websearch = true` by default, and Terraform reads the
  key from Secrets Manager at plan time. Get a free [Tavily](https://app.tavily.com/) key
  and store it with `make set-websearch-key ENV=<env>` (step 4) BEFORE the first
  `terraform plan`, or plan fails on the missing secret. To ship without web search, set
  `enable_websearch = false` in `environments/<env>/terraform.tfvars`.
- **Federated SSO is OFF by default.** The sign-in screen shows email/password only. To add
  an OIDC SSO button, create a (gitignored) `environments/<env>/federated.auto.tfvars` with
  your IdP details — see [federated login](docs/operations/federated-login.md).
- **AgentCore runtime ARN** — empty until you run `make deploy-agent` (step 6), which writes
  a gitignored `environments/<env>/agent.auto.tfvars`.
- **Bedrock model access** — enable the models in `var.models` in your account/region first,
  or agent runs fail at invoke time.

## Highlights

- **Config-driven** — no account ids, emails, or model ids are hardcoded. The account is
  resolved at runtime; the region and everything else are variables set per environment.
- **Modular** — nine focused Terraform modules (foundation, data, identity, api, compute,
  orchestration, agentcore, frontend, auth-federated).
- **Providers** — `hashicorp/aws` for classic services and `hashicorp/awscc` for Amazon
  Bedrock AgentCore (Memory, Gateway, Workload Identity).
- **S3-native state locking** — Terraform >= 1.11 (`use_lockfile`), no DynamoDB lock
  table.
- **Environments** — `dev`, `staging`, `prod`, each with isolated state and config.
- **Local + CI parity** — GitLab CI runs the same `make` targets as a local deploy.

## Documentation

The full documentation index is **[docs/README.md](docs/README.md)** (this file, the
root `README.md`, is just the project entry point). Key pages:

- [Terraform architecture](docs/architecture/terraform-architecture.md)
- [Local deployment](docs/deployment/local-deployment.md) · [CI/CD deployment](docs/deployment/cicd-deployment.md)
- [State backend](docs/deployment/state-backend.md) · [AgentCore runtime](docs/deployment/agentcore-runtime.md)
- [Variables](docs/configuration/variables.md) · [Environments](docs/configuration/environments.md)
- [Admin setup](docs/operations/admin-user-setup.md) · [Seeding](docs/operations/seeding.md) · [Cleanup](docs/operations/cleanup.md) · [Security scanning](docs/operations/security-scanning.md) · [Troubleshooting](docs/operations/troubleshooting.md)
