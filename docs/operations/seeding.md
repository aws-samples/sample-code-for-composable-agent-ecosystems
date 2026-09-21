# Seeding content

After the infrastructure is applied (see [local deployment](../deployment/local-deployment.md)),
`seed` publishes platform **content** and compiles ecosystems. It is fast, deterministic,
and safely re-runnable.

```bash
make seed ENV=dev
```

> `make seed` does **not** ingest the catalog. Catalog ingestion is a separate, optional,
> long-running step — see [Catalog ingestion](#catalog-ingestion-separate-step) below.
> The two are split so a slow, externally-dependent ingest can't block or mask content
> setup, and re-running ingest doesn't rebuild the frontend export.

## What `make seed` does

1. **Publishes domain descriptors + catalog content** — syncs `app/content/domains/` and
   `app/content/catalog-content/` to the ecosystems S3 bucket. The declarative ingest
   sources read the catalog content from S3.
2. **Seeds ecosystems** — uploads each platform (`_platform/`) and production ecosystem
   YAML and invokes `save-ecosystem` to compile it into a Step Functions state machine
   (this includes the synthesis ecosystems, e.g. `risk-synthesis`, that catalog ingestion
   later invokes). Sample ecosystems are **not** seeded.
3. **Publishes export inputs + prebuilt artifacts** — the eco-lite module, agent runtime,
   Lambda zips, shared layer, and single-eco frontend build used by ecosystem exports.

## Catalog ingestion (separate step)

Populating the catalog is a **separate** command because it is long-running and depends on
Bedrock + live external sources:

```bash
make ingest                                  # all default domains
make ingest DOMAINS="cyber-security-risk"    # scope to specific domains
POLL_TIMEOUT_MIN=90 make ingest              # extend the 60m wait cap (see note below)
```

> **The default poll cap is 60 minutes**, and some **default** synthesis-heavy domains —
> notably `cyber-security-risk` — routinely exceed it on a fresh account. When the cap is
> hit, the build **keeps running in AWS**; this `make ingest` invocation just stops waiting
> and does **not** promote that version. Either raise the cap up front
> (`POLL_TIMEOUT_MIN=90 make ingest`) or simply re-run `make ingest DOMAINS="<domain>"`
> afterward to finish and promote it (idempotent — see [Re-running](#re-running)).

`make ingest` runs each domain's **build state machine** end to end —
`new_version → ingest_all → (synthesis domains: plan_synthesis → per-cluster synthesis) →
validate_version` — then **promotes** the new version when the validation coverage gate
passes, so both the deterministically-ingested types AND the synthesized types (e.g.
`risk`) become live.

- **Long-running:** synthesis invokes Bedrock agents per cluster — expect several minutes
  to tens of minutes per synthesis-enabled domain.
- **Prerequisites:** run `make seed` first (it compiles the synthesis ecosystem SM and
  publishes the source content ingest reads), and enable **Bedrock model access** for the
  region.
- **Non-blocking failures:** some fetch sources pull live third-party/government data, so
  individual source failures (HTTP 403, timeouts) are expected; `make ingest` reports them
  and exits non-zero only on a real, fixable failure.

The platform stands up and passes basic checks with an empty catalog — ingestion only
populates catalog-backed features, so deferring it does not block the deploy.

## Re-running

Both steps are idempotent:
- `make seed` — ecosystem uploads overwrite in place and recompile. Re-run after changing
  content under `app/content/`. Because the underlying `aws s3` calls run with `--quiet`, a
  transient AWS/network blip can surface as a bare `make: *** [seed] Error 1` with little
  detail; `make seed` is safe to re-run and typically succeeds on a retry. For more detail
  on a persistent failure, run `bash scripts/seed.sh` directly (without the make wrapper).
- `make ingest` — builds a fresh version and promotes it on a passing coverage gate; the
  diff-upsert only writes real changes. Safely re-runnable (e.g. after enabling Bedrock
  access or when an external source recovers).
