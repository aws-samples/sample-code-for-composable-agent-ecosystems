---
sidebar_position: 2
---

# Create a new domain

A **domain** defines a body of curated knowledge for agents to ground against. This guide
creates a new domain end to end. For the concepts, see [Domains](../core-concepts/domains.md).

## 1. Create the domain directory

Add a folder under `app/content/domains/` named for your domain, with a `catalog.yaml`:

```
app/content/domains/my-domain/catalog.yaml
```

## 2. Declare your sources

Each source names what it `produces` and how it's fetched. Use `static` when the knowledge
is authored/derived, or `http` when it comes from a URL.

```yaml
ingest:
  sources:
    - name: my-standard
      produces: [my-control]
      fetch:
        transport: static
        data:
          - control_id: mc-1
            name: Access control
            description: Restrict access to sensitive systems.
            frameworks: [my-framework]
          - control_id: mc-2
            name: Logging
            description: Record security-relevant events.
            frameworks: [my-framework]
```

## 3. Configure normalization

Have an agent normalize raw records into clean, keyed entities:

```yaml
      normalize:
        mode: agent
        entity_type: my-control
        key: "{control_id}"
        key_field: control_id
        guidance: >-
          Emit one my-control record per item, preserving every field verbatim
          (control_id, name, description, frameworks). Do not invent or drop items.
        ground: true
        provenance: { ingested_by: catalog-ingest, sources: [my-standard] }
```

- `entity_type` is the type agents will query.
- `key` / `key_field` give each entity a stable identity.
- `ground: true` grounds the normalization against the source so records aren't invented.

## 4. Set refresh + synthesis

```yaml
  refresh:
    schedule: "rate(90 days)"     # periodic re-ingest
    sanity_drop: 0.5              # reject a refresh that collapses the source
  synthesis: none                 # or a synthesis stage for derived entities
```

## 5. Ingest and promote

Publish content, then build the catalog for your domain:

```bash
make seed
make ingest DOMAINS="my-domain"
```

The build state machine ingests → (synthesizes) → validates → **promotes** the new version
if the coverage gate passes. See [Catalog](../core-concepts/catalog.md) for the full
lifecycle and timing caveats.

## 6. Use it in an ecosystem

Once promoted, agents can query your `entity_type` (e.g. via catalog lookup tools) and
cite it in their output. Point an assessor agent at your domain's entities in its prompt,
and it will ground its assessment against them.

:::tip
Model your first domain on a shipped one — `app/content/domains/responsible-ai/catalog.yaml`
is a clean `static` example, and the AU financial-services domains show `http` sources.
:::
