# Ecosystem Evaluation Harness

The evaluation harness is an **offline capability-evaluation + regression deploy-gate +
online production-monitoring** layer for ecosystems. It complements the runtime quality
gates (output_kind / format / citation / grounding / challenger), which check *this* run's
output as it's produced. The harness answers a different question: **"is this ecosystem
correct, and does it stay correct after a prompt / model / tool change?"** — the artifact a
model-risk (MRM) function signs off on.

It is **eco-agnostic and opt-in**: the platform reads keys, the ecosystem author supplies
values. An ecosystem with no `evaluation:` block is completely unaffected.

## What it does

1. You declare an `evaluation:` block on an ecosystem (metrics, thresholds, gate mode) and
   attach a **golden set** — labelled test cases with known-correct answers.
2. Saving the ecosystem compiles a dedicated per-eco **Eval State Machine**
   (`<name_prefix>-<eco_id>-eval`) — separate from the run state machine.
3. Launching an eval (from the **Evaluation** dashboard or `POST
   /api/ecosystems/{id}/runs/_eval/launch`) runs the ecosystem headlessly over every golden
   case × `replicates`, scores the outputs, and writes a **scorecard**.
4. Scoring uses a fixed, domain-blind primitive set: `categorical` / `exact_match`,
   `numeric_tolerance`, `set_recall` / `coverage`, `citation_resolves`, `abstention`
   (deterministic, in-platform); `bedrock_judge` (Amazon Bedrock Evaluations, LLM-as-judge,
   independent model); and optional trajectory scoring (Amazon Bedrock AgentCore
   Evaluations).
5. Results are aggregated as **median ± noise band** over the replicates — a change is a
   regression only if it clears the band. The scorecard also reports per-issue miss-rate.
6. The **deploy gate** (`gate: block | warn | off`) decides what happens when a threshold
   fails beyond its band: `block` refuses the deploy, `warn` flags, `off` is informational.
7. **Online eval** (opt-in, `online.enabled`) runs on live production runs *asynchronously*
   (zero added latency), using reference-free scorers only (quality, never accuracy) for
   drift detection; flagged runs become candidate golden cases.

## The `evaluation:` block

```yaml
evaluation:
  golden_set: s3://<runs-bucket>/evaluations/<eco>/<version>/cases.jsonl  # optional; SME attaches via the builder
  replicates: 3            # runs per case → noise bands
  run_mode: headless       # gates warn (never pause) so unattended runs complete
  trajectory: false        # enable AgentCore trajectory eval
  metrics:
  - id: decision_acc
    field: decision         # an eco-declared output field
    scorer: categorical
    cost_weights: { false_approve: 5, false_decline: 1 }
  - id: evidence_faithful
    field: "*"              # all cited claims
    scorer: citation_resolves
  thresholds:
    decision_acc: 0.9
    evidence_faithful: 1.0
  gate: warn               # block | warn | off
  online:
    enabled: true
    baseline_sample_rate: 0.1
    always_eval_when: [gate_fired, human_override, { faithfulness_below: 0.8 }]
```

All keys are generic; the field names / expected values are the ecosystem's own output
vocabulary. A malformed block fails loudly at compile time.

## Golden set

Per-eco, versioned, in the runs bucket:
`s3://<runs-bucket>/evaluations/<eco>/<version>/cases.jsonl`. One JSON case per line:

```json
{ "name": "prime-clean-approve",
  "inputs":   { "applicant": "…matches the eco's declared inputs…" },
  "expected": { "decision": "APPROVE", "risk_tier": "Low", "should_abstain": false },
  "labels":   { "archetype": "happy_path", "reviewed": true } }
```

## AWS resources (Terraform `eval` module)

The harness is a separate Terraform module (`infra/modules/eval`) that creates:

- **5 Lambda functions** (deterministic names): `<name_prefix>-eval-runner`,
  `-eval-judge`, `-eval-aggregator`, `-eval-trajectory`, and `<name_prefix>-online-eval`.
  They share the platform Lambda layer (`tessera_platform.evaluation.*`).
- **`<name_prefix>-bedrock-eval` IAM role** — assumed by Bedrock to read datasets and
  invoke judge models.
- Per-Lambda IAM (S3 to the ecosystems + runs buckets); eval-judge additionally gets
  `bedrock:CreateEvaluationJob` + `iam:PassRole`; eval-trajectory gets AgentCore eval
  actions.
- **EventBridge rule** `<name_prefix>-online-eval` — fires online-eval when any run state
  machine SUCCEEDS.

The per-ecosystem **Eval State Machine is NOT created by Terraform** — it is compiled at
runtime by `save-ecosystem` (`tessera_platform.evaluation.eval_compiler`) when an ecosystem
declares an `evaluation:` block. The platform side (env vars on save/get/suggest-ecosystem,
and the Step Functions role's permission to invoke the eval Lambdas and drive
`startExecution.sync`) is wired in the compute module.

Storage lives in existing buckets (no new bucket): golden sets, scorecards
(`.../scorecards/<ts>.json` + `_latest.json`), judge datasets, online drift records, and
golden candidates all under `s3://<runs-bucket>/evaluations/<eco>/...`.

## Impact on ecosystems

- **Opt-in and additive** — no `evaluation:` block means no eval SM, no scorecard, no gate;
  existing runs are unchanged.
- The eval runs the ecosystem's **own** run state machine headlessly (gates set to `warn`
  so they don't pause) — the system under test is the real thing, not a special-cased copy.
- The only way it can block a deploy is if you explicitly set `gate: block` **and** a real
  regression (beyond the noise band) occurs.
- Online monitoring only activates with `online.enabled: true`, and is fully async off the
  completed run — **zero added latency** to live runs.

## Endpoints

The eval endpoints ride the existing `/api/ecosystems/...` proxy (no new API resources):

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/ecosystems/{id}?artifact=scorecard`  | latest `{scorecard, gate}` |
| GET  | `/api/ecosystems/{id}?artifact=golden_set` | curated golden set |
| POST | `/api/ecosystems/{id}/runs/_eval/launch`   | start the Eval SM (optional `{replicates, max_cases}`) |
| GET  | `/api/ecosystems/{id}/runs/_eval/status`   | eval-run status + scorecard presence |
| POST | `/api/ecosystems/suggest` `{mode:"eval_proposal"\|"eval_approve"}` | builder eval authoring |
