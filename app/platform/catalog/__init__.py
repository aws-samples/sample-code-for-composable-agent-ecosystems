"""tessera_platform.catalog — the shared, domain-agnostic catalog ingestion runtime.

A new domain's ingest is DECLARATIVE (a descriptor + a records schema); this package
executes it. See docs/spec-declarative-ingest.md.

  engine.CatalogStore     — non-destructive incremental upsert into AgentCore Memory
  engine.content_hash/diff_plan — pure diff primitives (parity-pinned)
  mapping.Mapping         — deterministic `mode: map` normalizer (JMESPath + transforms)
  formats.BUILDERS        — shared standard-format builders (`mode: builder`)
  connectors.fetch        — declarative fetchers per transport
  normalize.AgentNormalizer — schema-driven agentic normalizer (`mode: agent`)
  adapter.SourceAdapter   — fetch + normalize seam
  descriptor.load / Descriptor — load + validate a domain's ingest block
  orchestrate             — generic ingest verbs + Lambda handler
  synthesis               — optional Synthesizer plugin contract
  store                   — refresh checkpoint/lock/manifest (DynamoDB)
"""
from .engine import CatalogStore, content_hash, diff_plan, diff_snapshots, slug, today_iso
from .mapping import Mapping
from .adapter import SourceAdapter
from . import formats, connectors, normalize, descriptor, orchestrate, synthesis, store

__all__ = [
    "CatalogStore", "content_hash", "diff_plan", "diff_snapshots", "slug", "today_iso",
    "Mapping", "SourceAdapter",
    "formats", "connectors", "normalize", "descriptor", "orchestrate", "synthesis", "store",
]
