# Platform overview

Tessera is a composable platform for building multi-agent workflows through
configuration rather than code. Teams orchestrate multi-agent workflows
("ecosystems") declaratively: ecosystems are declared as YAML, and agents emit
generative UI events consumed by a web frontend.

## High-level architecture

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
                                 ┌────────▼───────┐   Cognito M2M JWT
                                 │  AgentCore     │ ◀──────────────┐
                                 │  Runtime       │                │
                                 └────────┬───────┘   ┌────────────┴────────┐
                                          │           │ AgentCore Gateway   │
                                          │           │  (MCP tools)        │
                                 ┌────────▼───────┐   └─────────────────────┘
                                 │ AgentCore      │
                                 │  Memory        │  shared, namespace-isolated
                                 └────────────────┘
```

## Components

- **Frontend** — a Next.js single-page app served as a static export from S3 behind
  CloudFront (with WAF). It renders the conversational builder, run launcher, live run
  map, and admin console.
- **REST API + WebSocket API** (Amazon API Gateway) — the JWT-authorized control plane
  and the live run stream.
- **Control-plane Lambdas** — save/list/get ecosystems, the conversational builder
  (suggest), run history + cost, human-in-the-loop gate decisions, sharing/admin,
  uploads, and more.
- **Orchestration** — one AWS Step Functions state machine per ecosystem, compiled from
  the agent DAG at save time, plus the catalog ingest/build/refresh state machines.
- **Amazon Bedrock AgentCore** — a single agent Runtime container (invoked for every
  agent), a Gateway exposing MCP tools, a shared Memory, and a Workload Identity for
  Lambdas that read the credential vault.
- **Quality** — output/format/citation checks, internal grounding via a Bedrock
  Guardrail (contextual grounding), and external grounding via web search.
- **State** — Amazon S3 (ecosystems, run artifacts, frontend, access logs), Amazon
  DynamoDB (WebSocket connections, the human-attention inbox, catalog refresh state),
  AgentCore Memory, and a Bedrock Guardrail.
- **Security** — Amazon Cognito (auth), Amazon Verified Permissions with Cedar policies
  (per-ecosystem authorization), zero static secrets (credentials live in the AgentCore
  Identity token vault / Secrets Manager), a VPC for the Lambdas, WAF, and private S3.

## Principles

1. **Configuration over code** — ecosystems are YAML; agents emit generative UI.
2. **Agent-as-package** — a single AgentCore Runtime, configured per request.
3. **Config-driven infrastructure** — no account ids, emails, or model ids are
   hardcoded; everything is a Terraform variable or resolved at runtime.
4. **Deterministic where it matters** — catalog lookups are grounded against curated
   sources.
5. **Zero static secrets** — secrets never live in the repo or in environment files.
