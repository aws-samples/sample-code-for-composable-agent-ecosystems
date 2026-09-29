---
sidebar_position: 7
---

# Evaluation

The **evaluation harness** answers a different question than the runtime quality gates.
Quality gates (output/format/citation/grounding/challenger — see
[Agents & orchestration](agents-and-orchestration.md)) check *this run's* output as it's
produced. Evaluation asks: **"is this ecosystem correct, and does it stay correct after a
prompt, model, or tool change?"** — the artifact a model-risk function signs off on.

It is **opt-in and additive**: an ecosystem with no `evaluation:` block is completely
unaffected — no eval state machine, no scorecard, no gate.

## Three modes

Evaluation is really three complementary capabilities:

| Mode | Question it answers | When it runs |
|------|--------------------|--------------|
| **Offline capability eval** | Is the ecosystem accurate against known-correct cases? | On demand, over a golden set. |
| **Regression deploy-gate** | Did a change make it worse? | At save/deploy, comparing to the last scorecard. |
| **Online monitoring** | Is live output drifting? | Async on production runs (zero added latency). |

## How offline evaluation works

1. You declare an `evaluation:` block on the ecosystem (metrics, thresholds, gate mode)
   and attach a **golden set** — labelled cases with known-correct answers.
2. Saving the ecosystem compiles a dedicated per-eco **Eval State Machine**
   (`<name_prefix>-<eco_id>-eval`), separate from the run state machine.
3. Launching an eval runs the ecosystem **headlessly** over every golden case ×
   `replicates`, scores the outputs, and writes a **scorecard**.
4. Results are aggregated as a **median ± noise band** across replicates — a change counts
   as a regression only if it clears the band. The scorecard also reports per-issue
   miss-rate.

The system under test is the **real** ecosystem — the eval runs its own run state machine
headlessly (gates set to `warn` so they don't pause), not a special-cased copy.

## Scorers

Scoring uses a fixed, domain-blind primitive set:

| Scorer | What it checks |
|--------|----------------|
| `categorical` / `exact_match` | The output field equals the expected value. |
| `numeric_tolerance` | A numeric field is within tolerance. |
| `set_recall` / `coverage` | Expected items are present. |
| `citation_resolves` | Cited claims resolve to real sources. |
| `abstention` | The ecosystem abstains when it should. |
| `bedrock_judge` | LLM-as-judge via **Amazon Bedrock Evaluations** (an independent model). |
| trajectory *(optional)* | Agent trajectory scoring via **Amazon Bedrock AgentCore Evaluations**. |

The deterministic scorers run in-platform; `bedrock_judge` and trajectory scoring use
Bedrock's evaluation services.

## The deploy gate

`gate: block | warn | off` decides what happens when a threshold fails **beyond its noise
band**:

- **`block`** — refuses the deploy (a real regression stops the change).
- **`warn`** — flags it but allows the deploy.
- **`off`** — informational only.

So the only way evaluation can block a deploy is if you explicitly set `gate: block` **and**
a genuine regression (beyond the band) occurs.

## Online monitoring

With `online.enabled: true`, evaluation also runs on live production runs
**asynchronously** — off the completed run, so it adds **zero latency**. It uses
reference-free scorers only (quality, never accuracy) for drift detection; flagged runs
become candidate golden cases you can promote into the golden set.

## Golden sets & scorecards

- **Golden set** — per-ecosystem, versioned, one JSON case per line
  (`inputs` / `expected` / `labels`). Stored in the runs bucket under
  `evaluations/<eco>/<version>/cases.jsonl`.
- **Scorecard** — the aggregated result of an eval run (per-metric median ± band, gate
  decision, miss-rates), stored alongside the golden set.

## Evaluation vs runtime quality gates

| | Runtime quality gates | Evaluation harness |
|---|---|---|
| **Scope** | A single live run | The ecosystem across many cases / over time |
| **When** | During the run | On demand, at deploy, or async post-run |
| **Answers** | "Is this output well-formed and grounded?" | "Is the ecosystem correct, and did a change regress it?" |
| **Blocks** | Can pause / flag a run | Can block a **deploy** (`gate: block`) |

## Next steps

- Author and run an evaluation: [Guides → Evaluate an ecosystem](../guides/evaluate-an-ecosystem.md)
- The `evaluation:` block fields: [Reference → Ecosystem YAML](../reference/ecosystem-yaml.md)
- The AWS services behind it: [Architecture](aws-architecture.md)

:::note
For the harness's AWS infrastructure detail (the eval Lambdas, IAM, EventBridge wiring),
see the repository's `docs/operations/evaluation-harness.md`.
:::
