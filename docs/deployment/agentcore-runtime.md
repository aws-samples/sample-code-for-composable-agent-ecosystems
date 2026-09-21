# AgentCore runtime

The Amazon Bedrock AgentCore **Memory**, **Gateway**, and **Workload Identity** are
managed declaratively by Terraform (via the AWSCC provider, in the `agentcore` module).
The **Runtime** — the container that runs the agent — is different: it is built from the
agent source and deployed with the `agentcore` CLI, so it is deployed as a scripted step
rather than a Terraform resource. Its ARN is then fed back into Terraform.

## Why the runtime is a CLI step

The runtime packages the agent source (`app/agent/`) into a container, creates/updates
the runtime and its execution role, and returns a runtime ARN. That build-and-deploy is
owned by the `agentcore` toolchain, not by declarative IaC. Terraform still owns
everything the runtime depends on and everything that depends on it — only the container
lifecycle is delegated to the CLI.

## The three-step flow

1. **`terraform apply`** — creates Memory, Gateway, Workload Identity, the guardrail,
   Cognito, buckets, and the Lambdas. On the first apply, `var.agent_runtime_arn` is
   empty, so the Lambdas point at a placeholder.
2. **`make deploy-agent`** (runs `scripts/deploy-agent.sh`) — resolves the runtime config
   from Terraform outputs (gateway URL, memory id, buckets), runs `agentcore deploy`, then
   **saves the resulting ARN** so you don't have to copy it:
   - writes `agent_runtime_arn` to `infra/environments/<env>/agent.auto.tfvars`
     (included in `$TF_VARS` by `load-env.sh` — see the note below), and
   - writes `.deploy-logs/agent-runtime-arn.env` (a source-able `AGENT_RUNTIME_ARN=…`).
   It also prints `AGENT_RUNTIME_ARN=<arn>` on stdout for CI/scripting.
3. **`terraform apply $TF_VARS`** — re-apply (after re-sourcing `load-env.sh`) so the saved
   ARN is picked up, wiring `agent-invoker`, `websocket`, and `chat` to the real runtime
   (env var + IAM `bedrock-agentcore:InvokeAgentRuntime`).

## Running it

Terraform is run directly (see [local deployment](local-deployment.md)); only the agent
deploy itself is a `make` target.

```bash
source scripts/load-env.sh          # exports $TF_VARS etc.

# 1. Base infra (if not already applied):
cd infra && terraform apply $TF_VARS && cd ..

# 2. Deploy the runtime — saves the ARN to agent.auto.tfvars + .deploy-logs/…
make deploy-agent

# 3. Re-apply — $TF_VARS (from load-env.sh) includes agent.auto.tfvars, which supplies
#    agent_runtime_arn:
source scripts/load-env.sh                     # re-source so $TF_VARS includes agent.auto.tfvars
cd infra && terraform apply $TF_VARS && cd ..
```

> **Why sourcing `load-env.sh` matters here.** Terraform auto-loads `*.auto.tfvars` only
> from its working directory (`infra/`), **not** from the `environments/<env>/`
> subdirectory where `agent.auto.tfvars` lives. `load-env.sh` is what appends
> `-var-file=environments/<env>/agent.auto.tfvars` to `$TF_VARS`. If you run a bare
> `terraform apply -var-file=environments/<env>/terraform.tfvars` **without** that
> var-file, `agent_runtime_arn` is `""` and the agent Lambdas fail every invocation with
> `InvokeAgentRuntime` **404 (`UnknownOperationException`)**.

If you'd rather pass it explicitly instead of via `$TF_VARS`:

```bash
source .deploy-logs/agent-runtime-arn.env      # sets $AGENT_RUNTIME_ARN
cd infra
terraform apply -var-file=environments/dev/terraform.tfvars -var agent_runtime_arn="$AGENT_RUNTIME_ARN"
cd ..
```

## Prerequisites

- The `agentcore` CLI: `pip install bedrock-agentcore-starter-toolkit`.
- The infrastructure from step 1 must exist first (the deploy script reads Terraform
  outputs).
- On a fresh account (no `.bedrock_agentcore.yaml`), the script runs `agentcore configure`
  automatically to create it. It runs **fully non-interactive** (`--non-interactive`,
  with an empty request-header allowlist via `-rha ""`), so there are no prompts — this
  is safe to run locally or in CI. If you have previously seen a wizard prompt such as
  "Configure request header allowlist? (yes/no)" or a terminal "CPR" warning, that was
  the older interactive path; it no longer appears.
- Re-deploys are idempotent: `agentcore deploy` runs with `--auto-update-on-conflict`, so
  an existing runtime is updated in place instead of erroring.

## Persisting the ARN

`make deploy-agent` already persists the ARN to
`infra/environments/<env>/agent.auto.tfvars`, so every subsequent `terraform apply
$TF_VARS` keeps the wiring — **as long as you run apply with `$TF_VARS` from a sourced
`load-env.sh`** (which adds that var-file; Terraform will not auto-load it from the
`environments/<env>/` subdirectory on its own). That file is gitignored (`*.auto.tfvars`),
since the ARN is environment/account-specific. If you'd prefer it to load with no extra
var-file, move the value into `environments/<env>/terraform.tfvars` instead.
