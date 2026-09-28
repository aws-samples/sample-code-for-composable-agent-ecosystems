---
sidebar_position: 6
---

# Catalog

The **catalog** is Tessera's grounded, versioned knowledge store. It's what agents read
from and cite instead of relying on the model's parametric memory. Domains
([Domains](domains.md)) define how knowledge is ingested; the catalog is the result.

## Why a catalog

- **Grounding** — agents cite curated entities, so claims are traceable to a source.
- **Versioning** — the catalog is promoted as versions; a run reads a known-good version.
- **Determinism where it matters** — regulatory lookups don't drift between runs.

## Catalog content

Curated source material lives under `app/content/catalog-content/` as JSON, for example:

| File | Source |
|------|--------|
| `acsc-e8-maturity.json` | ACSC Essential Eight maturity model |
| `apra-cps.json` | APRA prudential standards |
| `asic-conduct.json` | ASIC conduct guidance |
| `ato-tax-guidance.json` | ATO tax guidance |
| `austrac-fcc.json` | AUSTRAC financial-crime compliance |
| `nist-ai-rmf-playbook.json` | NIST AI Risk Management Framework |
| `scrutineers.json` | Scrutineer personas for [quorum](quorum.md) |

A domain's `catalog.yaml` ingests this content into typed, keyed entities.

## The build lifecycle

Populating the catalog runs a per-domain **build state machine**:

```
new_version → ingest_all → (synthesis domains) plan_synthesis
            → per-cluster synthesis → validate_version → PROMOTE
```

1. **Ingest** — each source is fetched (or read, for `static` sources) and normalized into
   entities.
2. **Synthesis** *(optional)* — for synthesis-enabled domains, agents derive higher-order
   entities (e.g. a `risk` type) from the ingested facts, cluster by cluster.
3. **Validate** — the new version is checked against a coverage gate.
4. **Promote** — if the gate passes, the new version becomes live; runs now see it.

## Running it: `make ingest`

```bash
# Build + populate all default domains (long-running)
make ingest

# Scope to specific domains
make ingest DOMAINS="cyber-security-risk"

# Raise the poll cap for large synthesis-heavy domains
POLL_TIMEOUT_MIN=90 make ingest
```

:::caution
`make ingest` is **separate from `make seed`** and can take several minutes to tens of
minutes per synthesis-enabled domain (it invokes Bedrock agents per cluster). It is
idempotent and safely re-runnable. Some default domains — notably `cyber-security-risk` —
can exceed the default 60-minute poll cap; the build then continues in AWS but is not
promoted by that invocation. Re-run `make ingest DOMAINS="<domain>"` to finish and promote,
or raise `POLL_TIMEOUT_MIN` up front. Enable Bedrock model access first, and run this
**after** `make seed`.
:::

## Catalog vs seed

| Step | What it does | Speed |
|------|--------------|-------|
| `make seed` | Publishes content + compiles ecosystems | Fast |
| `make ingest` | Builds, synthesizes, validates, and promotes the catalog | Long-running |

A freshly deployed platform has an **empty catalog** until you run `make ingest`.
Ecosystems still run without it, but agents that rely on catalog lookups will have less to
ground against.

See [Create a catalog](../guides/create-a-catalog.md) to add your own sources and content.
