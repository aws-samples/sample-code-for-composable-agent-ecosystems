<p align="center">
  <img src="docusaurus/static/img/tessera-mark.svg"
       alt="Tessera" width="96">
</p>

# Tessera

**Tessera** is a composable platform for building and running **multi-agent workflows** —
called *ecosystems* — through configuration rather than code. You describe the agents, how
their work fits together, what inputs they take, and what they produce in YAML; Tessera
compiles that into a runnable pipeline, executes it on AWS (Amazon Bedrock AgentCore +
Step Functions), and streams a live, generative UI to a web frontend.

📚 **[Documentation](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/)**
— introduction, getting started, core concepts, guides, and reference.

## What Tessera does

Ecosystems are **domain-agnostic** — the same engine powers a personal-finance advisor, a
commodity-trading desk, an enterprise risk board, an AI-governance conformity assessment, a
security incident war-room, a consultant's meeting prep, and even a trip planner. What ties
them together is *how* they run:

- **Configuration over code** — an ecosystem is a YAML file; agents emit generative UI.
- **Multi-agent orchestration** — agents run as a dependency graph across parallel swarms,
  each with a focused role, its own tools, and shared memory.
- **Grounded, not guessing** — domains ingest curated regulatory and reference sources into
  a versioned **catalog** that agents cite, backed by internal and external grounding checks.
- **Defensible by design** — built-in quality gates (format / citation / grounding, plus a
  challenger agent and a quorum deliberation model) and an opt-in **evaluation harness** for
  regression and model-risk sign-off.

That machinery makes Tessera especially strong for **regulated, high-stakes decision work
where outputs must hold up** — without being limited to it. See
[docs/architecture/overview.md](docs/architecture/overview.md) for the platform architecture.

## Repository layout

```
├── app/          Application code
│   ├── lambdas/     control-plane + ingest Lambda handlers (+ shared modules)
│   ├── platform/    shared platform package (tessera_platform.*)
│   ├── agent/       AgentCore runtime container
│   ├── frontend/    Next.js UI (static export)
│   ├── content/     ecosystems, domains, catalog content, templates (seed data)
│   └── tests/       unit / integration / ground-truth tests
├── infra/        Terraform — root module + child modules + per-environment config
├── scripts/      package-lambdas, deploy-agent, deploy-frontend, seed, create-admin
├── cicd/         GitLab CI/CD pipeline
├── docs/         in-repo documentation (start at docs/README.md)
├── docusaurus/   the documentation site published to GitHub Pages
└── Makefile      local + CI entrypoints (single source of truth)
```

## Prerequisites

- **Terraform** ≥ 1.11 (S3-native state locking)
- **AWS CLI v2**, authenticated to your target account
- **Python 3.12** and pip
- **Node.js 20+** and npm (Node 22 recommended)
- **jq**, **zip**, **openssl**
- The **`agentcore` CLI** — `pip install bedrock-agentcore-starter-toolkit`
- **Amazon Bedrock model access** enabled in your region for the models in `var.models`

Your AWS principal needs permission to create the full resource set (Lambda, API Gateway,
DynamoDB, S3, CloudFront, WAF, Cognito, KMS, SQS, SNS, VPC, Step Functions, EventBridge,
IAM, Verified Permissions, Bedrock/AgentCore, Secrets Manager). Full list:
[docs/deployment/local-deployment.md](docs/deployment/local-deployment.md).

## Deploy Tessera

Terraform is run **directly** from `infra/` (not wrapped by `make`) so its output and
errors are easy to read; `make` handles the surrounding steps.

```bash
# 1. Authenticate + configure
export AWS_PROFILE=your-profile
cp .env.example .env                    # set AWS_PROFILE / AWS_REGION / ENVIRONMENT
#    then set region = "..." in infra/environments/<env>/terraform.tfvars

# 2. One-time: create the Terraform state bucket
make bootstrap

# 3. Store the web-search API key (enable_websearch = true by default).
#    Get a free key from https://app.tavily.com/ :
make set-websearch-key ENV=dev          # or set enable_websearch = false to skip

# 4. Package the Lambdas, then apply Terraform
make package
source scripts/load-env.sh              # exports AWS creds + $TF_BACKEND / $TF_VARS
cd infra && terraform init $TF_BACKEND && terraform plan $TF_VARS && terraform apply $TF_VARS && cd ..

# 5. Deploy the AgentCore runtime, then re-apply to wire its ARN
make deploy-agent
cd infra && terraform apply $TF_VARS && cd ..

# 6. Publish the frontend, seed content, create the first admin
make deploy-frontend
make seed
make create-admin EMAIL=you@example.com
terraform -chdir=infra output -raw frontend_url    # open this URL
```

See the [local deployment guide](docs/deployment/local-deployment.md) for the full
step-by-step, first-run defaults, and troubleshooting.

## First run

1. Open the `frontend_url` printed above and sign in with the admin account.
2. Pick an ecosystem from the home screen and fill in its inputs (text / select / file).
3. Launch, and watch the agent map stream in real time.
4. Review the generated report and (optionally) give inline feedback.

Populate the **catalog** so grounded lookups have data. This step is **required** (separate, long-running):

```bash
make ingest                             # or: make ingest DOMAINS="cyber-security-risk"
```

## Where to go next

### Use Tessera

- [Introduction](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/intro)
  and [Quick Start](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/getting-started/quick-start)
- [Build an ecosystem](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/guides/build-an-ecosystem)
  · [Evaluate an ecosystem](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/guides/evaluate-an-ecosystem)
- [Ecosystem YAML reference](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/reference/ecosystem-yaml)

### Understand the platform

- [How it works](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/architecture)
  · [Architecture (AWS services)](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/aws-architecture)
- Core concepts: [Ecosystems](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/ecosystems),
  [Domains](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/domains),
  [Catalog](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/catalog),
  [Evaluation](https://aws-samples.github.io/sample-code-for-composable-agent-ecosystems/docs/core-concepts/evaluation)

### Deploy & operate (in-repo docs)

- [Local deployment](docs/deployment/local-deployment.md) · [CI/CD deployment](docs/deployment/cicd-deployment.md)
- [Terraform architecture](docs/architecture/terraform-architecture.md) · [State backend](docs/deployment/state-backend.md)
- [Variables](docs/configuration/variables.md) · [Environments](docs/configuration/environments.md)
- [Admin setup](docs/operations/admin-user-setup.md) · [Seeding](docs/operations/seeding.md) · [Security scanning](docs/operations/security-scanning.md) · [Troubleshooting](docs/operations/troubleshooting.md)

## Highlights

- **Config-driven** — no account ids, emails, or model ids are hardcoded; the account is
  resolved at runtime, everything else is a per-environment variable.
- **Modular Terraform** — focused child modules (foundation, data, identity, api, compute,
  orchestration, agentcore, frontend, auth-federated).
- **Environments** — `dev`, `staging`, `prod`, each with isolated state and config.
- **Zero static secrets** — credentials live in the AgentCore Identity token vault /
  Secrets Manager, never in the repo or environment files.
- **Local + CI parity** — GitLab CI runs the same `make` targets as a local deploy.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
