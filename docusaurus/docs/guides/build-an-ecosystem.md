---
sidebar_position: 1
---

# Build an ecosystem

This guide walks through authoring a new ecosystem — the multi-agent workflow you launch
from the UI. You can build one two ways: with the **AI-assisted builder** in the UI, or by
**writing the YAML** directly. Both produce the same artifact.

## Option A — AI-assisted builder (fastest start)

1. In the UI, choose to create a new ecosystem and describe what you want in plain
   language (e.g. *"Review a vendor's security posture against the Essential Eight and
   produce a gap report"*).
2. The builder proposes an ecosystem: agents, dependencies, tools, and outputs.
3. Review and edit the proposal, then **save**. Saving compiles the agent graph into a
   Step Functions state machine.

The builder is a starting point — you'll almost always refine the agents' prompts and
dependencies afterward.

## Option B — Write the YAML

Start from the canonical template (`app/content/templates/ecosystem_yaml_v1/`) and fill it
in. A minimal but complete ecosystem:

```yaml
ecosystem:
  name: Vendor Security Review
  description: Assess a vendor against the Essential Eight and produce a gap report.
  version: "1.0"

strategy: orchestrated
report: true
quality:
  challenger: true
  grounding: true

inputs:
  - id: vendor_docs
    type: file
    label: Vendor security documentation
    required: true
  - id: scope
    type: text
    label: Scope / focus areas
    required: false

entry_point: intake

memory:
  strategies: [semantic]
  shared_namespaces:
    - name: findings
      write: [all]
      read: [all]

agents:
  - id: intake
    name: Intake
    swarm: research
    tools: [memory_write, current_time]
    output_kind: analyst
    prompt: |
      Extract the vendor's stated controls from the supplied documents. Persist
      structured findings to `findings`. Cite each control back to its document.

  - id: e8_assessor
    name: Essential Eight Assessor
    swarm: research
    tools: [web_search, memory_read, memory_write]
    tool_budget: { web_search: 8 }
    output_kind: analyst
    prompt: |
      Assess the extracted controls against the ACSC Essential Eight maturity model.
      Note gaps per control. Cite the catalog / sources for each expectation.

  - id: gap_report
    name: Gap Report Synthesizer
    swarm: synthesis
    depends_on: [intake, e8_assessor]
    tools: [memory_read, generateUI]
    output_kind: synthesizer
    output_format: { type: markdown, save_as: gap_report.md }
    prompt: |
      Reconcile intake + assessment into a single gap report. Cite each finding as
      [per <agent_id>: ...]. Do not re-research; reconcile and present.

swarms:
  research:  { agents: [intake, e8_assessor], strategy: graph }
  synthesis: { agents: [gap_report], strategy: graph }
parallel_swarms: [research, synthesis]

triggers:
  - type: manual
```

## Rules the compiler enforces

- Every agent has a unique snake_case `id` starting with a letter, and a `prompt` of at
  least ~50 characters.
- `depends_on` must form a **DAG** — no cycles, no references to missing agents.
- Do **not** define `report_agent` / `challenger_agent`; they're auto-injected by
  `report: true` and `quality.challenger: true`.

See the [Ecosystem YAML reference](../reference/ecosystem-yaml.md) for the full field list.

## Save, seed, run

- **In the UI**: save compiles it immediately.
- **From the repo**: put the YAML under `app/content/ecosystems/` and run `make seed` to
  publish and compile it.

Then launch it and review the output — see [Run and review](run-and-review.md).

## Tips

- Give each agent a **specific contribution** — what only it can produce. Avoid generic
  "do good work" prompts.
- Push shared context through **memory namespaces**, not giant prompts.
- Use `tool_budget` to cap expensive tools like `web_search`.
- Keep synthesizers as *reconcilers* — they cite upstream agents rather than re-researching.
