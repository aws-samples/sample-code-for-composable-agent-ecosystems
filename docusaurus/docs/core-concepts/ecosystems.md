---
sidebar_position: 2
---

# Ecosystems

An **ecosystem** is a multi-agent workflow declared in YAML. It's the central artifact you
author in Tessera: it names the agents, what each one does, how they depend on each other,
what inputs the operator supplies, and what the run produces.

When you save an ecosystem, Tessera **compiles** it into a single Step Functions state
machine from the agent dependency graph.

## Anatomy of an ecosystem

```yaml
ecosystem:
  name: Property Buyer Report
  description: Takes a property address, researches it, produces a due-diligence report.
  version: "1.0"

strategy: orchestrated      # orchestrated | choreography
report: true                # auto-injects a report_agent
quality:
  challenger: true          # auto-injects a challenger_agent
  grounding: true           # internal + external grounding checks

inputs:
  - id: address
    type: text
    label: Property Address
    required: true

entry_point: research_agent

memory:
  strategies: [semantic]    # add 'episodic' for chronological recall
  shared_namespaces:
    - name: findings
      write: [all]
      read: [all]

agents:
  - id: research_agent
    name: Property Research
    swarm: research
    tools: [web_search, memory_write, current_time]
    output_kind: analyst
    prompt: |
      Research the property from public sources and extract structured facts...

  - id: report_synth
    name: Report Synthesizer
    swarm: synthesis
    depends_on: [research_agent]
    tools: [memory_read, code_interpreter, generateUI]
    output_kind: synthesizer
    prompt: |
      Reconcile the upstream findings into a single buyer report...

swarms:
  research:  { agents: [research_agent], strategy: graph }
  synthesis: { agents: [report_synth], strategy: graph }
parallel_swarms: [research, synthesis]

triggers:
  - type: manual
```

See the [Ecosystem YAML reference](../reference/ecosystem-yaml.md) for every field.

## Top-level settings

| Field | Purpose |
|-------|---------|
| `ecosystem.name` / `description` / `version` | Identity shown in the UI. |
| `strategy` | `orchestrated` (DAG) or `choreography`. |
| `report` | When `true`, the compiler adds a **report_agent** that assembles the final deliverable. |
| `quality.challenger` | When `true`, adds a **challenger_agent** that stress-tests the output. |
| `quality.grounding` | Enables internal (Guardrail) + external (web search) grounding checks. |
| `inputs` | Operator-facing fields (`text`, `select`, `file`) shown in the launch UI. |
| `entry_point` | The agent the run starts on. |
| `memory` | Shared namespaces agents read/write to pass context. |

:::note
`report_agent` and `challenger_agent` are **auto-injected** by the compiler when
`report: true` and `quality.challenger: true`. Do not define them in `agents[]` yourself.
:::

## Strategies

- **orchestrated** — agents run as a directed acyclic graph. Leaf agents (no
  `depends_on`) run first, in parallel; synthesizers wait for their dependencies. This is
  the common case.
- **choreography** — looser, event-driven coordination (used by a subset of ecosystems).

## Where ecosystems live

Bundled ecosystems ship under `app/content/ecosystems/`. `make seed` publishes and
compiles them so they appear in the UI. You can also create and edit ecosystems directly
in the UI (including with the AI-assisted builder) — see
[Build an ecosystem](../guides/build-an-ecosystem.md).
