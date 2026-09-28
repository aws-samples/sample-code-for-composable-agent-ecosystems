---
sidebar_position: 4
---

# Run and review

Once an ecosystem is compiled, operators launch runs from the web UI, watch them stream,
and review the report. This guide covers that loop.

## Launch a run

1. Open the frontend (`terraform -chdir=infra output -raw frontend_url`) and sign in.
2. Pick the ecosystem.
3. Fill in its **inputs** — the fields the ecosystem declared (`text`, `select`, `file`).
   Required inputs are marked; files are uploaded to the run.
4. Launch.

## Watch the live run map

The run streams in real time:

- **Agents** light up as they start, stream output, and complete.
- Agents in the same **swarm** run in parallel; **synthesizers** wait for their
  `depends_on` agents.
- **Tool calls** (web search, catalog lookups, `code_interpreter`) appear inline as they
  happen.
- If the ecosystem has a **human-in-the-loop gate**, the run pauses for a decision and
  surfaces it in the attention inbox.

## Read the report

When the run finishes, the **report agent** assembles the final deliverable. In the
report / artifact viewer you can:

- Read the structured report.
- See **grounding indicators** — which claims are backed by source outputs (internal
  grounding) or web search (external grounding).
- Inspect **citations** — synthesizers cite upstream agents as `[per <agent_id>: ...]`.
- Download typed artifacts an agent declared via `output_format` (e.g. a `.md` or `.json`
  file).

## Give feedback

The artifact viewer supports **inline section feedback**: select a passage and attach a
note or a score. This feedback is stored against the run and can inform iteration on the
ecosystem's prompts.

## Re-run and iterate

- **Re-run** the same ecosystem with different inputs to compare outcomes.
- If output isn't right, refine the relevant agent's **prompt** or **dependencies** (see
  [Build an ecosystem](build-an-ecosystem.md)), save to recompile, and run again.
- If agents lack grounding, populate or extend the [catalog](../core-concepts/catalog.md).

## Where runs are stored

Run artifacts (reports, uploaded files, generated outputs) are stored in S3 and listed in
the run history, along with cost and status. See
[Architecture](../core-concepts/architecture.md) for the full state picture.
