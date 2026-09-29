---
sidebar_position: 1
slug: /intro
---

# Introduction

**Tessera** is a composable agentic security platform. Security and risk teams
orchestrate multi-agent workflows — called **ecosystems** — through configuration rather
than code. You describe *what* agents should do and how their work fits together in YAML;
Tessera compiles that into a runnable pipeline, executes it on AWS, and streams a
generative UI back to a web frontend.

## Why Tessera?

- **Configuration over code** — an ecosystem is a YAML file. Add an agent, change a
  dependency, or swap an output format by editing config, not writing services.
- **Multi-agent orchestration** — agents run as a dependency graph across parallel
  swarms, each with a focused role, its own tools, and shared memory.
- **Grounded, not guessing** — domains ingest curated regulatory and reference sources
  into a versioned catalog that agents cite, backed by internal and external grounding
  checks.
- **Defensible by design** — built-in quality gates (output/format/citation checks, a
  challenger agent, and a quorum deliberation model) make outputs reviewable.
- **Runs on AWS** — Amazon Bedrock AgentCore for the agent runtime, Step Functions for
  orchestration, and a static web UI behind CloudFront.

## Key concepts

| Term | Meaning |
|------|---------|
| **Ecosystem** | A multi-agent workflow declared in YAML: agents, inputs, dependencies, and outputs. Compiled into one Step Functions state machine. |
| **Agent** | A single role in an ecosystem — a prompt, a set of tools, an `output_kind`, and its place in the dependency graph. |
| **Swarm** | A named group of agents that can run in parallel. |
| **Domain** | A knowledge scope (e.g. a regulatory area) that ingests curated sources into the catalog. |
| **Catalog** | The versioned, grounded knowledge store agents read from and cite. |
| **Quorum** | A deliberation model where a panel of scrutineer personas reach a verdict. |
| **Evaluation** | Offline capability eval + regression deploy-gate + online monitoring for an ecosystem. |
| **Mission** | The operator's request for a given run, supplied through the ecosystem's inputs. |
| **Report** | The final, structured deliverable produced by a run. |

## How a run works

1. An operator opens the web UI, picks an ecosystem, fills in its **inputs**, and launches.
2. The WebSocket API starts the ecosystem's **Step Functions** state machine (compiled
   from the agent DAG when the ecosystem was saved).
3. Each agent is invoked on the **Bedrock AgentCore Runtime**, using its tools and the
   shared memory. Agents emit generative-UI events that stream live to the browser.
4. Quality gates run — format/citation checks, grounding, and (optionally) a challenger.
5. A **report agent** assembles the final report, which the operator reviews and can give
   inline feedback on.

## Quick links

- [Installation](getting-started/installation.md) · [Quick Start](getting-started/quick-start.md)
- [How it works](core-concepts/architecture.md) · [Architecture](core-concepts/aws-architecture.md) · [Ecosystems](core-concepts/ecosystems.md)
- [Build an ecosystem](guides/build-an-ecosystem.md) · [Evaluate an ecosystem](guides/evaluate-an-ecosystem.md)
- [Ecosystem YAML reference](reference/ecosystem-yaml.md)
- [GitHub repository](https://github.com/aws-samples/sample-code-for-composable-agent-ecosystems)

:::note
This site focuses on **using** Tessera. For the infrastructure and Terraform detail
(modules, state backend, CI/CD), see the repository's top-level `docs/` directory.
:::
