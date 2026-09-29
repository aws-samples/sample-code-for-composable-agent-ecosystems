---
sidebar_position: 1
---

# Ecosystem YAML reference

The full field reference for a Tessera ecosystem (`ecosystem_yaml_v1`). See
[Ecosystems](../core-concepts/ecosystems.md) for the conceptual overview and
[Build an ecosystem](../guides/build-an-ecosystem.md) for a worked example.

## Top level

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ecosystem.name` | string | yes | Display name. |
| `ecosystem.description` | string | yes | One or two paragraphs describing the workflow. |
| `ecosystem.version` | string | yes | Semver, e.g. `"1.0"`. |
| `strategy` | enum | yes | `orchestrated` (DAG), `choreography`, or `quorum`. |
| `report` | bool | no | `true` auto-injects `report_agent` (default behaviour). |
| `quality.challenger` | bool | no | `true` auto-injects `challenger_agent`. |
| `quality.grounding` | bool | no | Enables internal + external grounding checks. |
| `entry_point` | agent id | yes | The agent the run starts on. |
| `inputs` | list | no | Operator-facing inputs (see below). |
| `memory` | object | no | Shared memory config (see below). |
| `agents` | list | yes | At least one agent (see below). |
| `swarms` | map | no | Named groups of agents. |
| `parallel_swarms` | list | no | Swarms that may run concurrently. |
| `triggers` | list | no | e.g. `- type: manual`. |

## `inputs[]`

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Referenced by agents / the launch form. |
| `type` | enum | `text`, `select`, or `file`. |
| `label` | string | Operator-facing label. |
| `required` | bool | Whether the field must be filled. |
| `default` | any | Optional default value. |

## `memory`

| Field | Type | Description |
|-------|------|-------------|
| `strategies` | list | `[semantic]`; add `episodic` for chronological recall. |
| `shared_namespaces[].name` | string | Namespace agents read/write. |
| `shared_namespaces[].write` | list | `[all]` or specific agent ids. |
| `shared_namespaces[].read` | list | `[all]` or specific agent ids. |

## `agents[]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | snake_case, starts with a letter. |
| `name` | string | yes | Display name. |
| `registry_id` | string | no | Conventionally `tessera/{id}`. |
| `swarm` | string | no | Swarm grouping for parallel execution. |
| `depends_on` | list | no | Upstream agent ids (defines the DAG). |
| `tools` | list | no | e.g. `web_search`, `memory_read`, `memory_write`, `current_time`, `code_interpreter`, `generateUI`. |
| `tool_budget` | map | no | Per-tool call caps, e.g. `web_search: 12`. |
| `output_kind` | enum | no | `analyst`, `synthesizer`, `challenger`, `reporter`, `drafter`, `creative`, `chat`, `operator`. |
| `output_format` | object | no | Typed artifact (see below). |
| `prompt` | string | yes | The agent's instructions (≥ ~50 chars). |

### `agents[].output_format`

| Field | Type | Description |
|-------|------|-------------|
| `type` | enum | `markdown` (default), `json`, `yaml`, `html`. |
| `file_extension` | string | e.g. `.yaml`. |
| `save_as` | string | Output filename. |
| `template_id` | string | Cross-reference a versioned template to inline into the prompt. |
| `schema_id` | string | JSON Schema the output must match. |

## `swarms`

```yaml
swarms:
  research:  { agents: [a, b], strategy: graph }
  synthesis: { agents: [c],    strategy: graph }
parallel_swarms: [research, synthesis]
```

## `evaluation` (optional)

An opt-in block that adds offline capability evaluation, a regression deploy-gate, and
online monitoring. Omit it entirely and the ecosystem is unaffected. See
[Evaluation](../core-concepts/evaluation.md) and
[Evaluate an ecosystem](../guides/evaluate-an-ecosystem.md).

```yaml
evaluation:
  golden_set: s3://<runs-bucket>/evaluations/<eco>/<version>/cases.jsonl
  replicates: 3
  run_mode: headless
  trajectory: false
  metrics:
    - id: decision_acc
      field: decision
      scorer: categorical
      cost_weights: { false_approve: 5, false_decline: 1 }
  thresholds:
    decision_acc: 0.9
  gate: warn
  online:
    enabled: true
    baseline_sample_rate: 0.1
    always_eval_when: [gate_fired, human_override, { faithfulness_below: 0.8 }]
```

| Field | Type | Description |
|-------|------|-------------|
| `golden_set` | string | S3 path to the versioned `cases.jsonl` golden set. |
| `replicates` | int | Runs per case, to form noise bands. |
| `run_mode` | enum | `headless` — gates warn (never pause) so unattended runs complete. |
| `trajectory` | bool | Enable AgentCore trajectory evaluation. |
| `metrics[].id` | string | Metric name. |
| `metrics[].field` | string | Output field to score (`"*"` = all cited claims). |
| `metrics[].scorer` | enum | `categorical`, `exact_match`, `numeric_tolerance`, `set_recall`, `coverage`, `citation_resolves`, `abstention`, `bedrock_judge`. |
| `metrics[].cost_weights` | map | Optional asymmetric error weights. |
| `thresholds` | map | Per-metric pass threshold. |
| `gate` | enum | `block`, `warn`, or `off`. |
| `online.enabled` | bool | Turn on async production-run monitoring. |
| `online.baseline_sample_rate` | number | Fraction of runs sampled for baseline drift. |
| `online.always_eval_when` | list | Conditions that force an online eval. |

## Auto-injected agents

When `report: true` and `quality.challenger: true`, the compiler adds `report_agent`
(`output_kind: reporter`) and `challenger_agent` (`output_kind: challenger`). **Do not**
define these in `agents[]`.

## Validation rules

- Unique snake_case `id`s starting with a letter; `prompt` ≥ ~50 chars.
- `depends_on` must form a DAG — no cycles, no missing references.
- Saving compiles the ecosystem; invalid graphs fail the save.

:::tip
The canonical template and JSON Schema live at
`app/content/templates/ecosystem_yaml_v1/` in the repository.
:::
