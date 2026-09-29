---
sidebar_position: 3
---

# Agents & orchestration

An **agent** is a single role in an ecosystem. Agents don't run as separate services —
they all run on the same Bedrock AgentCore Runtime, configured per invocation from their
YAML definition. What makes each agent distinct is its **prompt**, its **tools**, its
**output_kind**, and its **place in the dependency graph**.

## Anatomy of an agent

```yaml
- id: advisory_synth            # snake_case, starts with a letter
  name: Advisory Synthesizer    # display name in the UI
  registry_id: tessera/advisory_synth
  swarm: synthesis              # grouping for parallel execution
  depends_on: [intake_agent]    # upstream agents that must finish first
  tools: [memory_read, code_interpreter, generateUI]
  tool_budget:
    web_search: 12              # cap tool calls
  output_kind: synthesizer      # the contract the runtime applies
  output_format:                # optional typed artifact
    type: markdown              # markdown | json | yaml | html
    save_as: advisory_report.md
  prompt: |
    Your specific contribution: reconcile the upstream findings into a single
    advisory report. Cite each upstream agent as [per <agent_id>: ...].
```

## output_kind

`output_kind` tells the runtime what contract to apply to an agent's output:

| `output_kind` | Role |
|---------------|------|
| `analyst` | Researches and produces findings for downstream agents. |
| `synthesizer` | Reconciles upstream findings into a canonical result. |
| `challenger` | Stress-tests / critiques the output (auto-injected when `quality.challenger`). |
| `reporter` | Assembles the final report (auto-injected when `report: true`). |
| `drafter` | Produces a draft document. |
| `creative` | Open-ended generation. |
| `chat` | Conversational. |
| `operator` | Takes actions / operates tools. |

## Tools

Agents call tools through the AgentCore Gateway (MCP). Common ones:

| Tool | Use |
|------|-----|
| `web_search` | External grounding / research. |
| `memory_read` / `memory_write` | Read and write shared memory namespaces. |
| `current_time` | Deterministic timestamp. |
| `code_interpreter` | Run code for validation, math, table checks. |
| `generateUI` | Emit generative-UI components to the frontend. |

Use `tool_budget` to cap calls to expensive tools (e.g. `web_search: 12`).

## The dependency graph (DAG)

`depends_on` defines the edges. The compiler turns the agent set into a directed acyclic
graph and then into a Step Functions state machine:

- **Leaf agents** (no `depends_on`) run first, in parallel.
- **Synthesizers** wait until all their dependencies complete, then reconcile.
- A **cycle** or a **missing dependency** makes the ecosystem fail to compile.

## Swarms & parallelism

A **swarm** is a named group of agents that can run together:

```yaml
swarms:
  research:  { agents: [intake_agent, market_agent], strategy: graph }
  synthesis: { agents: [advisory_synth], strategy: graph }
parallel_swarms: [research, synthesis]
```

`parallel_swarms` lets independent swarms execute concurrently, subject to the DAG edges
between their agents.

## Shared memory

Agents pass context through **memory namespaces**, not by stuffing everything into
prompts:

```yaml
memory:
  strategies: [semantic]     # + episodic for chronological recall
  shared_namespaces:
    - name: findings
      write: [all]           # or a list of agent ids
      read: [all]
```

An analyst writes findings to `findings`; a downstream synthesizer reads them. Namespaces
are isolated per ecosystem run.

## Grounding & citations

With `quality.grounding: true`, agent output is checked against:

- **Internal grounding** — a Bedrock Guardrail confirms claims are supported by the
  agents' own source outputs.
- **External grounding** — web search validates factual claims.

Synthesizers are expected to cite upstream agents (`[per <agent_id>: ...]`); uncited
standalone synthesis is rejected by the output contract.

:::tip
These quality gates check *this run's* output. To verify an ecosystem stays correct across
a prompt/model/tool change over time, see [Evaluation](evaluation.md).
:::
