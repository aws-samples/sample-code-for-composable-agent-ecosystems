# ─────────────────────────────────────────────────────────────────────────────
# AgentCore Runtime.
#
# The Runtime is a container-based agent deployed with the `agentcore` CLI (see
# scripts/deploy-agent.sh), which builds the source into a container, creates/updates
# the runtime + its execution role, and returns the runtime ARN. That ARN is fed back
# into the platform as var.agent_runtime_arn so the agent-invoker and related Lambdas
# can invoke it.
#
# Terraform does not build the container itself; the deploy flow (Makefile / CI) runs:
#   1. terraform apply                          (creates Memory/Gateway/Identity, etc.)
#   2. scripts/deploy-agent.sh                  (agentcore CLI → runtime ARN)
#   3. terraform apply -var agent_runtime_arn=… (wires the runtime into the Lambdas)
#
# This module simply surfaces the configured ARN (below, via outputs) — there is no
# Terraform-managed runtime resource, matching how the runtime lifecycle is owned by
# the agentcore toolchain rather than declarative IaC.
