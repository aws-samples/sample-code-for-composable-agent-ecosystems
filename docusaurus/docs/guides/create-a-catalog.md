---
sidebar_position: 3
---

# Create a catalog

The **catalog** is the grounded knowledge agents cite. Building it means adding curated
source content and running the ingest pipeline for the domains that consume it. For the
concepts and lifecycle, see [Catalog](../core-concepts/catalog.md).

## 1. Add curated source content

Curated material lives under `app/content/catalog-content/` as JSON. Add a file for your
source, for example `my-standard.json`, containing the reference records you want agents to
ground against.

Keep the content **curated and stable** — this is the deterministic layer. Regulatory
excerpts, framework controls, and reference taxonomies belong here; per-run conclusions do
not.

## 2. Point a domain's sources at it

In the consuming domain's `catalog.yaml`, add a source that reads your content and
normalizes it into a typed entity (see [Create a new domain](create-a-domain.md) for the
full source/normalize shape). For `http` sources, set the URL; for authored content, use
`transport: static` with inline `data`.

## 3. Choose whether to synthesize

- `synthesis: none` — the catalog holds exactly the ingested entities.
- A **synthesis** stage — agents derive higher-order entities (for example a `risk` type)
  from the ingested facts, cluster by cluster. Synthesis-enabled domains take longer to
  build because each cluster invokes a Bedrock agent.

## 4. Build and promote

```bash
make seed                                   # publish content first
make ingest DOMAINS="my-domain"             # build this domain's catalog
```

The build runs: `ingest → (synthesis) → validate → promote`. The new version goes live
only if it passes the **coverage gate**.

```bash
# Large, synthesis-heavy domains may need a longer poll cap
POLL_TIMEOUT_MIN=90 make ingest DOMAINS="my-domain"
```

:::caution
`make ingest` is long-running and idempotent. If a build exceeds the poll cap it continues
in AWS but isn't promoted by that invocation — re-run `make ingest DOMAINS="my-domain"` to
finish and promote. Enable Bedrock model access first.
:::

## 5. Verify agents can ground against it

Launch an ecosystem whose agents query your `entity_type` and confirm:

- Agent output **cites** the catalog entities.
- Grounding indicators in the report show claims are source-backed.

If the catalog is empty (you skipped `make ingest`), those agents still run but have less
to ground against — see the seed-vs-ingest comparison in
[Catalog](../core-concepts/catalog.md).
