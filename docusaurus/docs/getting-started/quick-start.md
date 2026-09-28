---
sidebar_position: 2
---

# Quick Start

This walkthrough assumes you've completed [Installation](installation.md) and have the
platform deployed with `make seed` run. You'll launch your first ecosystem run and read
the report.

## 1. Open the web UI

```bash
terraform -chdir=infra output -raw frontend_url
```

Open the printed URL and sign in with the admin account you created
(`make create-admin EMAIL=you@example.com`).

## 2. Pick an ecosystem

The home screen lists every compiled ecosystem. Each one is a multi-agent workflow with a
short description and a set of inputs. Pick one that matches what you want to produce — for
example a risk review, an advisory report, or a governance assessment.

## 3. Provide the mission and inputs

Every ecosystem declares its own **inputs**. Depending on the ecosystem you might supply:

- **text** — a mission statement, context, or goals
- **select** — a choice from a fixed list (e.g. the type of review)
- **file** — documents to analyze (statements, policies, specs)

Fill in the required inputs and launch the run.

## 4. Watch the run stream

As the run executes you'll see the **agent map** update in real time:

- Each agent lights up as it starts, streams its output, and completes.
- Agents in the same swarm run in parallel; synthesizers wait for their upstream
  dependencies.
- Tool calls (web search, catalog lookups, code interpreter) appear inline.

Behind the scenes, the WebSocket API started the ecosystem's Step Functions state machine,
and each agent ran on the Bedrock AgentCore Runtime. See
[Architecture](../core-concepts/architecture.md) for the full path.

## 5. Review the report

When the run finishes, the **report agent** assembles the final deliverable. You can:

- Read the structured report in the browser.
- See grounding indicators showing which claims are backed by sources.
- Leave inline feedback on sections (see [Run and review](../guides/run-and-review.md)).

## Next steps

- Understand the pieces: [Core Concepts → Architecture](../core-concepts/architecture.md)
- Author your own workflow: [Guides → Build an ecosystem](../guides/build-an-ecosystem.md)
- Ground agents in curated knowledge: [Core Concepts → Catalog](../core-concepts/catalog.md)
