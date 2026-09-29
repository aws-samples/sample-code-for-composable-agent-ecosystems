---
sidebar_position: 4
---

# Quorum

Some decisions need more than a single agent's opinion. The **quorum** strategy empanels a
set of **scrutineer personas**, has them deliberate over the material, and produces a
single **defensible verdict** — useful for reviews, approvals, and assessments where the
*reasoning* matters as much as the answer.

## When to use quorum

Reach for a quorum ecosystem when you want:

- **Multiple perspectives** — several personas assess the same input through different
  lenses (e.g. conduct, privacy, regulatory, cyber).
- **A defensible outcome** — a recorded verdict with the deliberation behind it, not just
  a yes/no.
- **Panel-style review** — architecture review boards, risk boards, governance
  conformity checks.

For a straight pipeline (research → synthesize → report), use the standard
`orchestrated` strategy instead — see [Agents & orchestration](agents-and-orchestration.md).

## Shape of a quorum ecosystem

A quorum ecosystem uses `strategy: quorum` and a `quorum` block with three phases:

```yaml
strategy: quorum

quorum:
  empanelment:
    persona_source: /catalog/scrutineer/     # where the personas come from
    select:
      min: 3
      max: 6
      must_include: []                        # force specific personas
  deliberation:
    # how the panel discusses and reconciles
  verdict:
    # how the final decision is formed
```

### 1. Empanelment

The panel is assembled from a **persona source** — commonly the `scrutineer` domain in the
catalog. `select` controls how many personas sit on the panel (`min`/`max`) and lets you
force specific ones with `must_include`. Each persona carries its own perspective / lens.

### 2. Deliberation

The empanelled personas review the input and deliberate. Because personas are grounded in
catalog knowledge (see [Catalog](catalog.md)), their positions cite curated sources rather
than free-associating.

### 3. Verdict

The deliberation is reconciled into a single verdict — the run's defensible outcome —
which the report agent presents to the operator.

## Scrutineers

The **scrutineer** domain supplies the personas a quorum draws on. Its catalog content
(e.g. `scrutineers.json`, `scrutineers-architecture.json`) defines the lenses and roles a
panel can be assembled from. Adding or refining scrutineers is a
[catalog](catalog.md) authoring task.

## Relationship to quality gates

Quorum is a **deliberation model**, complementary to the standard quality gates. You can
still enable `report: true` (to assemble the verdict into a report) and grounding checks.
The challenger agent plays a similar "stress the conclusion" role in orchestrated
ecosystems that the deliberation phase plays here.
