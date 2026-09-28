---
sidebar_position: 2
---

# CLI & make targets

Tessera's operational entry points are `make` targets (the non-Terraform steps) plus
Terraform run directly from `infra/`. This is the command reference; see
[Installation](../getting-started/installation.md) for the end-to-end sequence.

## make targets

| Target | What it does |
|--------|--------------|
| `make bootstrap` | One-time: create the Terraform state bucket and write `backend.hcl` (optional `BUCKET=name`). |
| `make package` | Build the Lambda packages + shared layer. |
| `make deploy-agent` | Deploy the AgentCore runtime; saves the ARN to `agent.auto.tfvars`. |
| `make deploy-frontend` | Build and publish the frontend (reads Terraform outputs). |
| `make seed` | Publish content + compile ecosystems (fast). Does **not** ingest the catalog. |
| `make ingest` | Populate the catalog from domain sources (long-running). `DOMAINS="a b"` to scope. |
| `make create-admin EMAIL=you@example.com` | Bootstrap the first admin user. |
| `make set-websearch-key ENV=dev` | Store/rotate the web-search API key in Secrets Manager. |
| `make verify-websearch ENV=dev` | Assert the web-search credential provider exists. |
| `make test` | Run unit + integration tests (the CI gate). |

## Terraform (run directly from `infra/`)

Terraform is intentionally **not** wrapped by `make`, so its output and errors are visible.

```bash
source scripts/load-env.sh          # exports AWS creds + $TF_BACKEND / $TF_VARS
cd infra
terraform init  $TF_BACKEND
terraform plan  $TF_VARS
terraform apply $TF_VARS
cd ..
```

Print the frontend URL:

```bash
terraform -chdir=infra output -raw frontend_url
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `AWS_PROFILE` | The AWS profile to deploy with. |
| `AWS_REGION` | Deploy region (kept in sync with the tfvars `region`). |
| `ENVIRONMENT` | `dev` / `staging` / `prod`. |
| `POLL_TIMEOUT_MIN` | Raise the `make ingest` poll cap for large domains (default 60). |
| `DOMAINS` | Scope `make ingest` to specific domains. |

`.env` (copied from `.env.example`) holds `AWS_PROFILE` / `AWS_REGION` / `ENVIRONMENT`;
`scripts/load-env.sh` sources it and sets the Terraform flags.

:::note
For the full CLI/CI details and the Terraform module layout, see the repository's
`docs/deployment/` and `docs/configuration/`.
:::
