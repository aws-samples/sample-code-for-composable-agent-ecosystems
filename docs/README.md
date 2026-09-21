# Tessera documentation

Documentation for the Tessera platform and its Terraform infrastructure.

**How this is organized.** Each feature's end-to-end *procedure* lives on its **Operations**
page; **Configuration** pages are reference (what each variable means and its default);
**Deployment** pages cover standing the platform up; **Architecture** explains how it fits
together. Start with the task table below, or scan the sections — every link has a one-line
description of what it's for.

## Which doc do I need?

| I want to… | Read |
|---|---|
| Deploy from my laptop | [deployment/local-deployment.md](deployment/local-deployment.md) |
| Deploy through CI/CD | [deployment/cicd-deployment.md](deployment/cicd-deployment.md) |
| Set up remote Terraform state | [deployment/state-backend.md](deployment/state-backend.md) |
| Deploy / wire the AgentCore runtime | [deployment/agentcore-runtime.md](deployment/agentcore-runtime.md) |
| Look up what a variable does or its default | [configuration/variables.md](configuration/variables.md) |
| Understand dev vs staging vs prod config | [configuration/environments.md](configuration/environments.md) |
| Enable SSO / set the IdP client secret | [operations/federated-login.md](operations/federated-login.md) |
| Create the first admin or promote admins | [operations/admin-user-setup.md](operations/admin-user-setup.md) |
| Publish content + seed the catalog | [operations/seeding.md](operations/seeding.md) |
| Evaluate / gate an ecosystem's quality | [operations/evaluation-harness.md](operations/evaluation-harness.md) |
| Tear everything down / stop charges | [operations/cleanup.md](operations/cleanup.md) |
| Run the security scan / manage suppressions | [operations/security-scanning.md](operations/security-scanning.md) |
| Fix something that's broken | [operations/troubleshooting.md](operations/troubleshooting.md) |
| Understand the module layout | [architecture/terraform-architecture.md](architecture/terraform-architecture.md) |

## Architecture
- [Platform overview](architecture/overview.md) — what Tessera is and how it fits together.
- [Terraform architecture](architecture/terraform-architecture.md) — the module layout, providers, and dependency graph.

## Deployment
- [Local deployment](deployment/local-deployment.md) — deploy from your laptop.
- [CI/CD deployment](deployment/cicd-deployment.md) — deploy through GitLab CI/CD.
- [State backend](deployment/state-backend.md) — remote state (S3 with native locking) and one-time bootstrap.
- [AgentCore runtime](deployment/agentcore-runtime.md) — how the agent runtime is deployed and wired.

## Configuration
- [Variables](configuration/variables.md) — every input variable and its default.
- [Environments](configuration/environments.md) — dev / staging / prod differences.

## Operations
- [Admin user setup](operations/admin-user-setup.md) — create the first admin.
- [Federated login (OIDC SSO)](operations/federated-login.md) — optional SSO button; enable/disable via config.
- [Evaluation harness](operations/evaluation-harness.md) — offline capability eval, regression deploy-gate, and online monitoring for ecosystems.
- [Seeding](operations/seeding.md) — publish content + compile ecosystems (`make seed`), and the separate, long-running catalog ingestion/synthesis (`make ingest`).
- [Cleanup](operations/cleanup.md) — tear down and stop charges.
- [Security scanning](operations/security-scanning.md) — run ASH and how in-source suppressions (checkov skips, detect-secrets pragmas, `.grype.yaml`) work.
- [Troubleshooting](operations/troubleshooting.md) — common issues and fixes.

## Reference
- [Demo sample packages](reference/demo-samples/) — descriptions of the optional demo input document packs.
