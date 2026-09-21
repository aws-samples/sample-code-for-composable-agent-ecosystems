# Troubleshooting

## `make package` takes a long time

Building the Lambda packages (currently ~46) plus the shared layer with Linux
(`manylinux2014_x86_64`) wheels can take several minutes on the first run. This is
expected; later runs reuse pip's cache. In CI it runs in the dedicated `build` stage and
its artifacts are reused downstream.

## `make package` fails: "Could not find a version that satisfies the requirement" / "No matching distribution"

`scripts/package-lambdas.sh` installs each Lambda's `requirements.txt` with
`--platform manylinux2014_x86_64 --python-version 3.12 --implementation cp
--only-binary=:all:` so the deployed artifacts contain Lambda-compatible Linux wheels. A
pinned version therefore only works if PyPI publishes a **`manylinux2014_x86_64` cp312
wheel** for it — a version that is source-only, or has wheels only for other platforms,
will fail here even though `pip install` works on your laptop.

This bites when bumping a native/binary dependency to a very new release before its
manylinux wheel is published. Example: `pillow==12.3.0` fails (only sdist / non-manylinux
wheels at time of writing) while `pillow==12.2.0` installs fine. Fix: pin the highest
version that has a `manylinux2014_x86_64` cp312 wheel. Check available wheels with:

```bash
pip3 index versions <pkg> --platform manylinux2014_x86_64 --python-version 3.12 \
  --implementation cp --only-binary=:all:
```

## `terraform init` fails asking for backend configuration

`init` needs the per-environment backend file:

```bash
source scripts/load-env.sh dev
cd infra && terraform init $TF_BACKEND      # = -backend-config=environments/dev/backend.hcl
```

Make sure the state bucket in `environments/<env>/backend.hcl` exists — see
[state backend](../deployment/state-backend.md).

## Lambdas fail at cold start with a native-extension error

If a handler crashes importing a compiled dependency (e.g. `cryptography`), the package
was likely built with host wheels instead of Lambda's Linux wheels. `make package`
already pins `--platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all:`.
Rebuild with `make package ENV=<env>` and re-apply.

## WebSocket features don't stream / "internal server error" on a WS route

The WebSocket handler and the agent Lambdas need the callback URL (`WS_ENDPOINT`) and an
invoke permission. These are wired in the `compute` module from the `api` module's
outputs. If you deployed the API and Lambdas out of order, re-run
`terraform apply $TF_VARS` (from `infra/`) so the permissions and env vars settle.

## Agent runtime not invoked / `agent_runtime_arn` empty / `InvokeAgentRuntime` 404

Symptom: agent chat or catalog **synthesis** fails, and the Lambda logs / Step Functions
show `An error occurred (404) ... InvokeAgentRuntime ... <UnknownOperationException/>`.
That 404 means the Lambda was invoked with an **empty** `agentRuntimeArn`.

Cause 1 — **the runtime ARN wasn't wired in.** On the first apply the runtime doesn't
exist yet. Run `make deploy-agent` (which saves the ARN to
`environments/<env>/agent.auto.tfvars`), then re-apply.

Cause 2 (most common) — **you applied without the `agent.auto.tfvars` var-file.**
Terraform auto-loads `*.auto.tfvars` only from `infra/`, **not** from the
`environments/<env>/` subdirectory where the file lives. The var-file is added to
`$TF_VARS` by `scripts/load-env.sh`. So you must:

```bash
source scripts/load-env.sh          # $TF_VARS now includes environments/<env>/agent.auto.tfvars
cd infra && terraform apply $TF_VARS && cd ..
```

A bare `terraform apply -var-file=environments/<env>/terraform.tfvars` (without the agent
var-file) resolves `agent_runtime_arn` to `""` → empty Lambda env → the 404 above. Verify
the wiring with:

```bash
aws lambda get-function-configuration --function-name <name_prefix>-agent-invoker \
  --query 'Environment.Variables.TESSERA_AGENT_RUNTIME_ARN' --output text
```

It must print the runtime ARN, not an empty line. See
[AgentCore runtime](../deployment/agentcore-runtime.md).

## `agentcore` CLI not found

Install it: `pip install bedrock-agentcore-starter-toolkit`. It is required only for the
agent runtime step, not for `terraform apply`.

## "CloudWatch Logs role ARN must be set in account settings" (API Gateway stage)

A fresh account has no account-level API Gateway CloudWatch Logs role, which API Gateway
requires before a stage can enable access logging. The `api` module now provisions this
(`aws_api_gateway_account` + a role) and the stages depend on it, so a normal
`terraform apply` sets it automatically. Note it is an **account-wide** setting (one per
account/region), not Tessera-specific. If you hit this, just re-run `terraform apply` — the
account setting is applied before the stages.

## Memory: "Role does not have access to required SNS topics"

AgentCore validates, at `CreateMemory` time, that the memory execution role can access the
self-managed strategy's SNS topic. Two things that role needs (both now included):
its trust policy must be scoped with an `aws:SourceArn` condition for `memory/*`, and —
because the jobs topic is SSE-encrypted (`alias/aws/sns`) — it needs `kms:GenerateDataKey`
+ `kms:Decrypt` (granted via a `kms:ViaService = sns.<region>.amazonaws.com` condition). If
you see this after an older apply, re-run `terraform apply` to pick up the updated role.

**On a first deploy this can also be a transient IAM propagation race:** the role and its
policy are created in the same apply as the Memory resource, and IAM is eventually
consistent, so `CreateMemory` occasionally runs before the grant has propagated and fails
with this error. It is not a config problem — **simply re-run `terraform apply`** and it
succeeds. The same "re-apply resolves it" applies to occasional transient AWS API/DNS blips
during a large first apply (e.g. a one-off `no such host` for an AWS endpoint). Terraform is
idempotent, so re-applying only creates what's missing.

## CloudFront access logging errors

CloudFront writes logs to the shared access-logs bucket. If logging fails to initialize,
confirm the access-logs bucket has object ownership set to `BucketOwnerPreferred` (it is,
in the `foundation` module). If your account restricts ACLs org-wide, you may need to
switch CloudFront to a log-delivery-service bucket policy or disable `logging_config`.

## Provider version / AgentCore resource not found

AgentCore Memory / Gateway / Workload Identity are provided by the **awscc** provider
(`~> 1.20`, resolved to a recent version). If `terraform init` selects an older awscc
that lacks these resources, bump the constraint in `infra/versions.tf` and re-init.

## Lambda: "'MemorySize' value failed to satisfy constraint: ... less than or equal to 512"

This is an **AWS account quota**, not a code error: brand-new accounts cap per-function
Lambda memory at 512 MB. Several functions request more (1024–2048 MB). Two options:

- **Raise the quota (recommended):** Service Quotas → AWS Lambda → the function-memory
  quota → request an increase (up to 10240 MB). Then set `lambda_max_memory = 10240` in
  the environment's `terraform.tfvars` and re-apply.
- **Clamp to your current ceiling:** set `lambda_max_memory = 512` in
  `environments/<env>/terraform.tfvars`. Every function's `memory_size` is clamped with
  `min(requested, lambda_max_memory)`, so the apply succeeds immediately. Heavy functions
  (ingest, chat, concierge) will run with less memory and may be slower or time out on
  large workloads until you raise the quota.

## Bedrock access denied

Ensure Amazon Bedrock **model access** is enabled in your region for the model ids in
`var.models` (Claude, Titan embeddings, etc.). Without it, agent/synthesis calls fail
with access-denied.

## Catalog is empty after deploy

The platform deploys with an empty catalog. `make seed` publishes content + compiles
ecosystems but does **not** populate the catalog — run `make ingest` (or
`make ingest DOMAINS="cyber-security-risk"`) to build + synthesize + promote it. It is
long-running and re-runnable — see [seeding](seeding.md).
