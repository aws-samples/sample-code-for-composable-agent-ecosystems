---
sidebar_position: 1
---

# Installation

Tessera deploys into your own AWS account with Terraform. This page covers the
prerequisites and the one-time setup to stand the platform up. For the full,
step-by-step infrastructure walkthrough, see the repository's
`docs/deployment/local-deployment.md`.

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
IAM, Verified Permissions, Bedrock/AgentCore, Secrets Manager).

## One-time setup

```bash
# 1. Authenticate to AWS
export AWS_PROFILE=your-profile

# 2. Create your env file and set AWS_PROFILE / AWS_REGION / ENVIRONMENT
cp .env.example .env

# 3. Set the region in the environment's tfvars (Terraform reads var.region, not AWS_REGION)
#    infra/environments/<env>/terraform.tfvars  ->  region = "us-east-1"

# 4. Create the Terraform state bucket and wire it into every backend.hcl
make bootstrap

# 5. Store the web-search API key (required — enable_websearch = true by default).
#    Get a free key from https://app.tavily.com/ then:
make set-websearch-key ENV=dev
```

:::tip
To ship **without** web search, set `enable_websearch = false` in
`infra/environments/<env>/terraform.tfvars` and skip step 5.
:::

## Deploy the platform

```bash
# Package the Lambdas, then run Terraform directly
make package
source scripts/load-env.sh          # exports AWS creds + $TF_BACKEND / $TF_VARS
cd infra
terraform init  $TF_BACKEND
terraform plan  $TF_VARS
terraform apply $TF_VARS
cd ..

# Deploy the AgentCore runtime, then re-apply to wire its ARN
make deploy-agent
cd infra && terraform apply $TF_VARS && cd ..

# Publish the frontend, seed content, and create the first admin
make deploy-frontend
make seed
make create-admin EMAIL=you@example.com

# Print the URL to open
terraform -chdir=infra output -raw frontend_url
```

Once `make seed` finishes, every bundled ecosystem is compiled and available in the UI.
Continue to the [Quick Start](quick-start.md) to launch your first run.

:::note
`make seed` publishes content and compiles ecosystems (fast). Populating the **catalog**
from domain sources is a separate, longer step — `make ingest` — covered under
[Catalog](../core-concepts/catalog.md).
:::
