---
sidebar_position: 6
---

# Evaluate an ecosystem

This guide shows how to add evaluation to an ecosystem: declare an `evaluation:` block,
attach a golden set, launch an eval, read the scorecard, and use the deploy gate. For the
concepts, see [Evaluation](../core-concepts/evaluation.md).

Evaluation is **opt-in** — an ecosystem with no `evaluation:` block is unaffected.

## 1. Add an `evaluation:` block

Declare metrics, thresholds, and a gate mode on the ecosystem:

```yaml
evaluation:
  golden_set: s3://<runs-bucket>/evaluations/<eco>/<version>/cases.jsonl
  replicates: 3            # runs per case → noise bands
  run_mode: headless       # gates warn (never pause) so unattended runs complete
  trajectory: false        # enable AgentCore trajectory eval
  metrics:
    - id: decision_acc
      field: decision       # an output field your ecosystem produces
      scorer: categorical
      cost_weights: { false_approve: 5, false_decline: 1 }
    - id: evidence_faithful
      field: "*"            # all cited claims
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

The keys are generic; `field` names and expected values are your ecosystem's own output
vocabulary. A malformed block **fails at compile time** when you save.

Saving the ecosystem compiles a dedicated **Eval State Machine** for it (separate from the
run state machine).

## 2. Attach a golden set

The golden set is per-ecosystem and versioned, stored in the runs bucket:
`s3://<runs-bucket>/evaluations/<eco>/<version>/cases.jsonl`. One JSON case per line:

```json
{ "name": "prime-clean-approve",
  "inputs":   { "applicant": "…matches the eco's declared inputs…" },
  "expected": { "decision": "APPROVE", "risk_tier": "Low", "should_abstain": false },
  "labels":   { "archetype": "happy_path", "reviewed": true } }
```

- `inputs` must match the ecosystem's declared [inputs](../core-concepts/ecosystems.md).
- `expected` is the known-correct answer the scorers compare against.
- `labels` are for organizing cases (archetype, review status).

You can attach/curate the golden set through the builder, or upload `cases.jsonl` to the
S3 path above.

## 3. Launch an eval

From the **Evaluation** dashboard in the UI, or via the API:

```bash
# Start the Eval State Machine (optionally scope replicates / cases)
POST /api/ecosystems/{id}/runs/_eval/launch
     { "replicates": 3, "max_cases": 50 }

# Check status + whether a scorecard is present
GET  /api/ecosystems/{id}/runs/_eval/status
```

The eval runs the ecosystem headlessly over every golden case × `replicates`, scores each
output, and writes a scorecard.

## 4. Read the scorecard

```bash
GET /api/ecosystems/{id}?artifact=scorecard   # → { scorecard, gate }
```

The scorecard reports, per metric:

- the **median ± noise band** across replicates,
- pass/fail against your `thresholds`,
- per-issue **miss-rate**,
- the overall **gate** decision.

A metric only counts as a **regression** if it moves beyond its noise band — this filters
out run-to-run variance.

## 5. Use the deploy gate

`gate` controls what a failing threshold (beyond its band) does at deploy:

| `gate` | Effect |
|--------|--------|
| `block` | Refuses the deploy — a genuine regression stops the change. |
| `warn` | Flags it, allows the deploy. |
| `off` | Informational only. |

Set `gate: block` for ecosystems where a regression must not ship (e.g. a decisioning
workflow under model-risk oversight).

## 6. (Optional) Turn on online monitoring

```yaml
  online:
    enabled: true
    baseline_sample_rate: 0.1
    always_eval_when: [gate_fired, human_override, { faithfulness_below: 0.8 }]
```

Online eval runs **asynchronously** on live production runs (zero added latency), using
reference-free scorers for drift detection. Runs it flags become candidate golden cases you
can promote into the golden set — closing the loop between production and your test set.

## Tips

- Start small: a handful of well-labelled golden cases covering the archetypes that matter
  (happy path, edge cases, cases that *should* abstain) beats a large noisy set.
- Use `replicates: 3+` so the noise band is meaningful.
- Keep `gate: warn` while you build confidence, then switch to `block` once thresholds are
  stable.
