---
sidebar_position: 3
---

# Glossary

| Term | Meaning |
|------|---------|
| **Ecosystem** | A multi-agent workflow declared in YAML. Compiled into one Step Functions state machine. |
| **Agent** | A single role in an ecosystem — a prompt, tools, an `output_kind`, and a place in the DAG. |
| **Swarm** | A named group of agents that can run in parallel. |
| **DAG** | The directed acyclic graph formed by agents' `depends_on` edges. |
| **output_kind** | The contract the runtime applies to an agent (`analyst`, `synthesizer`, `challenger`, `reporter`, `drafter`, `creative`, `chat`, `operator`). |
| **output_format** | An optional typed artifact an agent produces (`markdown`/`json`/`yaml`/`html`). |
| **Memory namespace** | A shared store agents read/write to pass context within a run. |
| **Report agent** | Auto-injected agent (when `report: true`) that assembles the final report. |
| **Challenger agent** | Auto-injected agent (when `quality.challenger: true`) that stress-tests the output. |
| **Grounding** | Checking claims against sources — internal (Bedrock Guardrail) and external (web search). |
| **Domain** | A knowledge scope defining what to ingest and how. |
| **Catalog** | The grounded, versioned knowledge store agents cite. |
| **Ingest** | The build pipeline that populates the catalog (`make ingest`). |
| **Synthesis** | A catalog stage where agents derive higher-order entities from ingested facts. |
| **Quorum** | A deliberation model where a panel of scrutineer personas reach a verdict. |
| **Scrutineer** | A persona (with a lens) used in quorum deliberation. |
| **Mission** | The operator's request for a run, supplied via the ecosystem's inputs. |
| **Report** | The final structured deliverable of a run. |
| **AgentCore Runtime** | The single Bedrock AgentCore container that runs every agent. |
| **AgentCore Gateway** | Exposes MCP tools to agents. |
| **AgentCore Memory** | The shared, namespace-isolated memory store. |
| **Seed** | `make seed` — publish content + compile ecosystems (fast). |
| **Evaluation harness** | Offline capability eval + regression deploy-gate + online monitoring for an ecosystem. |
| **Golden set** | Versioned, labelled test cases (`cases.jsonl`) with known-correct answers. |
| **Scorecard** | The aggregated result of an eval run (per-metric median ± band, gate decision, miss-rates). |
| **Noise band** | The variance envelope across replicates; a change is a regression only if it clears the band. |
| **Deploy gate** | `gate: block \| warn \| off` — what a failing eval threshold does at deploy. |
| **Online eval** | Async, zero-latency evaluation of live production runs for drift detection. |
| **Eval State Machine** | The per-ecosystem Step Functions machine compiled when an ecosystem declares `evaluation:`. |
| **Bedrock Evaluations** | LLM-as-judge scoring service used by the `bedrock_judge` scorer. |
| **Step Functions** | AWS orchestration; one state machine per ecosystem (plus catalog and eval machines). |
| **Verified Permissions** | AWS authorization service; per-ecosystem access via Cedar policies. |

## See also

- [Introduction](../intro.md)
- [Ecosystem YAML reference](ecosystem-yaml.md)
- [CLI & make targets](cli-and-make.md)
- [GitHub repository](https://github.com/aws-samples/sample-code-for-composable-agent-ecosystems)
