---
sidebar_position: 5
---

# Domains

A **domain** is a knowledge scope. It defines *what* curated knowledge Tessera should
ingest and *how*, so that agents can reason against grounded, versioned facts instead of
guessing. A domain's output lands in the [catalog](catalog.md), which agents read from and
cite.

## What a domain contains

Each domain is a directory under `app/content/domains/<domain>/` with a `catalog.yaml`
that declares its ingest pipeline:

```yaml
ingest:
  sources:
    - name: au-ai-ethics-principles
      produces: [rai-principle]        # the entity type this source yields
      fetch:
        transport: static              # static | http
        data:                          # inline records for a static source
          - principle_id: fairness
            name: Fairness
            frameworks: [au-ai-ethics, nist-ai-rmf]
            # ...
      normalize:
        mode: agent                    # an agent normalizes records
        entity_type: rai-principle
        key: "{principle_id}"
        key_field: principle_id
        guidance: >-
          Emit one rai-principle record per item, preserving every field...
        ground: true
        provenance: {ingested_by: catalog-ingest, sources: [au-ai-ethics-principles]}

  refresh:
    schedule: "rate(90 days)"          # how often to re-ingest
    sanity_drop: 0.5                   # guard against a source collapsing
  synthesis: none                      # or a synthesis stage (see Catalog)
```

### Sources

A source describes where knowledge comes from and what it `produces`:

- **`transport: static`** — the records are authored inline in `catalog.yaml` (used when
  the knowledge is derived, e.g. a crosswalk that isn't on any single web page). Ingest is
  deterministic — no fetching.
- **`transport: http`** — the records are fetched from a URL.

### Normalize

`normalize.mode: agent` has an agent turn raw source records into clean, keyed entities of
`entity_type`, following `guidance`. `ground: true` grounds the normalization against the
source so records aren't invented. `key` / `key_field` give each entity a stable identity.

### Refresh

`refresh.schedule` (e.g. `rate(90 days)`) controls periodic re-ingest. `sanity_drop`
guards against a bad refresh — if a source suddenly yields far fewer records, the refresh
is rejected rather than silently shrinking the catalog.

## Shipped domains

Tessera ships domains covering Australian financial-services and AI-governance knowledge,
plus platform-support domains:

| Domain | Focus |
|--------|-------|
| `accounting-tax-au` | Accounting & tax guidance (AU) |
| `au-financial-advice` | Financial advice (AU) |
| `conduct-people-au` | Conduct & people obligations (AU) |
| `cyber-security-risk` | Cyber-security risk |
| `fcc-au` | Financial crime compliance (AU) |
| `financial-regulatory-risk` | Financial regulatory risk |
| `privacy-au` | Privacy (AU) |
| `responsible-ai` | Responsible-AI principles (cross-cutting) |
| `scrutineer` | Personas used by [quorum](quorum.md) deliberation |
| `tessera-self` | Knowledge about Tessera itself |
| `schemas` | Shared entity schemas |

## Domains vs the catalog

- A **domain** is the *recipe* — what to ingest and how.
- The **catalog** is the *result* — the grounded, versioned entities agents actually read.

Running a domain's pipeline (`make ingest`) populates the catalog. See
[Catalog](catalog.md) for that lifecycle, and
[Create a new domain](../guides/create-a-domain.md) to author one.
