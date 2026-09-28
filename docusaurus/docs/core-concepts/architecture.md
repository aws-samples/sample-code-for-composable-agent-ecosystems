---
sidebar_position: 1
---

# Architecture

Tessera turns a YAML ecosystem into a running multi-agent workflow on AWS. This page
describes the pieces you interact with when using the platform. (For the Terraform module
layout and deployment internals, see the repository's `docs/architecture/`.)

## The run path

```
┌─────────────┐     WebSocket    ┌────────────────┐
│  Frontend   │ ◀───────────────▶│  WebSocket API │
│ (CloudFront)│                  │   handler      │
└─────────────┘                  │  (launches SFN)│
                                 └────────┬───────┘
                                          │ start run
                                 ┌────────▼───────┐
                                 │ Step Functions │  one state machine per ecosystem
                                 │  (agent DAG)   │  (compiled at save time)
                                 └────────┬───────┘
                                          │ invoke per agent
                                 ┌────────▼───────┐
                                 │ agent-invoker  │
                                 └────────┬───────┘
                                          │ invoke_agent_runtime
                                 ┌────────▼───────┐
                                 │  AgentCore     │  single container, invoked per agent
                                 │  Runtime       │  + Gateway (MCP tools) + Memory
                                 └────────────────┘
```

## Components

- **Frontend** — a Next.js single-page app served as a static export from S3 behind
  CloudFront (with WAF). It renders the ecosystem builder, run launcher, live run map, the
  report/artifact viewer, and the admin console.
- **REST + WebSocket APIs** (API Gateway) — the JWT-authorized control plane (save/list/get
  ecosystems, run history, sharing, uploads) and the live run stream.
- **Orchestration** — **one AWS Step Functions state machine per ecosystem**, compiled
  from the agent dependency graph when you save the ecosystem. The catalog build/ingest
  state machines live here too.
- **Amazon Bedrock AgentCore** — a single agent **Runtime** container invoked for every
  agent, a **Gateway** exposing MCP tools, and a shared, namespace-isolated **Memory**.
- **Quality** — output/format/citation checks, **internal grounding** via a Bedrock
  Guardrail (contextual grounding), and **external grounding** via web search.
- **State** — Amazon S3 (ecosystems, run artifacts, the frontend), DynamoDB (WebSocket
  connections, the human-attention inbox, catalog refresh state), AgentCore Memory, and a
  Bedrock Guardrail.
- **Security** — Amazon Cognito (auth), Amazon Verified Permissions with Cedar policies
  (per-ecosystem authorization), and zero static secrets (credentials live in the
  AgentCore Identity token vault / Secrets Manager).

## Design principles

1. **Configuration over code** — ecosystems are YAML; agents emit generative UI.
2. **Agent-as-package** — a single AgentCore Runtime, configured per request.
3. **Config-driven infrastructure** — no account ids, emails, or model ids are hardcoded.
4. **Deterministic where it matters** — catalog lookups are grounded against curated sources.
5. **Zero static secrets** — secrets never live in the repo or in environment files.

## What this means when you use Tessera

- Saving an ecosystem **compiles** it — the agent DAG becomes a state machine. If the DAG
  is invalid (a cycle, a missing dependency), the save fails fast.
- A run is **stateful and observable** — Step Functions tracks each agent; the UI streams
  progress live.
- Agents share context through **memory namespaces**, not by passing giant prompts around.
- Quality is **built in**, not bolted on — every run is checked before you see the report.

Next: [Ecosystems](ecosystems.md) — the YAML you actually author.
