# Sample ecosystems — ⚠️ TEST / DEMO ONLY

> **Do NOT use these in production.** They are illustrative, experimental ecosystems kept
> as examples of what Tessera can express. They are **not production-hardened**, are **not**
> covered by the ground-truth tests (`tests/ground_truth/`), and may reference catalogs or
> domains that require ingestion to work at all. **No support, no guarantees.**

## What these are (and aren't)

- **Not shipped by the installer.** `scripts/install.sh` seeds only the production
  ecosystems in `ecosystems/` (+ `ecosystems/_platform/`). This `samples/` folder is
  deliberately excluded from the default deploy (the deploy scripts glob `ecosystems/*.yaml`
  flat, so subfolders are ignored).
- **Opt-in only.** To load them into a **non-production / lab** environment, run:
  ```bash
  bash scripts/deploy-samples.sh              # all samples
  bash scripts/deploy-samples.sh sale-ready   # a specific one
  ```
- **Dependencies are your problem.** Many samples expect a populated catalog and specific
  domains. If the relevant domain isn't ingested (`bash scripts/ingest.sh DOMAINS=…`),
  the ecosystem still *runs* but degrades (agents get no catalog grounding). Some samples
  were authored in the builder UI against domains that may not exist in this repo.

## Deploying the samples

Samples are **opt-in** and go to a **non-production / lab** environment only. The platform
must already be installed (`bash scripts/install.sh`) so the `save-ecosystem` compiler and
ecosystems bucket exist.

```bash
# deploy ALL samples
bash scripts/deploy-samples.sh

# deploy a specific sample (or several)
bash scripts/deploy-samples.sh architecture-review-board
bash scripts/deploy-samples.sh sale-ready trip-planner
```

Targeting follows the usual config (see `scripts/env.sh`): `AWS_PROFILE` (default
`admin-lab1`), `AWS_REGION` (`us-east-1`), `ENVIRONMENT` (`dev`). Example:

```bash
AWS_PROFILE=admin-lab1 ENVIRONMENT=dev bash scripts/deploy-samples.sh
```

**What it does:** uploads each `ecosystems/samples/<id>.yaml` to the ecosystems bucket and
compiles it to a Step Functions state machine (same path as production ecosystems, via
`scripts/deploy-ecosystems.sh`). After it finishes, the samples appear in the ecosystem grid
in the UI. It is idempotent — safe to re-run.

**Dependencies (important):** many samples need a populated catalog to work well. If the
required domain isn't ingested, the ecosystem still *runs* but degrades (agents get no
catalog grounding). Ingest what a sample needs first, e.g.:

```bash
# quorum ecosystems (e.g. architecture-review-board) draw personas from the scrutineer catalog
DOMAINS=scrutineer bash scripts/ingest.sh
# or ingest everything a set of samples might touch
DOMAINS=all bash scripts/ingest.sh
```

Some samples were authored in the builder UI against domains that may not exist in this repo —
those will list but have no catalog grounding.

**Removing them:** delete the compiled ecosystem from the platform via the UI (admin), or
re-run an install without samples (they are never seeded by `install.sh`).

## The samples

| Sample | What it demos | Notes |
|---|---|---|
| `sale-ready` | Property vendor pre-sale strategy pack | AU property; not security |
| `personal-banker-affordability` | Retail-banking affordability assessment | FSI demo |
| `tessera-concierge` | In-product concierge / help assistant | Platform helper |
| `stage` | Generic multi-agent presentation studio | Non-domain demo |
| `tessera-story` | Presentation decks *about* Tessera | Internal/marketing |
| `trip-planner` | Travel itinerary planner | Non-security demo |
| `au-fishing-spot-finder` | AU recreational fishing spots | Fun demo |
| `piano-band-songbook-builder` | Songbook / setlist builder | Fun demo |
| `consultant-client-meeting-prep` | Consultant meeting prep | Demo (UI-authored) |
| `advisor-client-conversation` | Adviser conversation prep | Demo (UI-authored) |
| `mission-news-brief` | News brief generator | Demo (UI-authored) |
| `commodity-trading-desk-v2` | Commodity trading desk (variant) | Overlaps the production `commodity-trading-ecosystem` |

## Promoting a sample to production

If a sample matures, move its YAML up to `ecosystems/`, add ground-truth coverage under
`tests/ground_truth/`, confirm its domain(s) are in `domains/` and ingested, and add it to
the ecosystem table in the top-level `README.md`.
