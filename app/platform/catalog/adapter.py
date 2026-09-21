"""tessera_platform.catalog.adapter — the SourceAdapter contract.

One declarative source = fetch (via connectors) + normalize (map | builder | agent).
This is the single seam every domain source conforms to; the engine and orchestrator
never change per domain.

    adapter = SourceAdapter(source_spec, schema=<records schema>)
    raw     = adapter.fetch()                 # impure (network) — or None for static builders
    items   = adapter.build(raw, today)       # PURE for map/builder; agentic for agent
              # -> [(entity_type, key_field, record)]

source_spec (from a descriptor's ingest.sources[]):
    { name, produces: [...], fetch: {transport, ...},
      normalize: { mode: map|builder|agent, ... } }
"""
import jmespath

from . import connectors, formats
from .mapping import Mapping


class SourceAdapter:
    def __init__(self, source_spec, schema=None, model=None):
        self.spec = source_spec or {}
        self.name = self.spec.get("name")
        self.produces = self.spec.get("produces") or []
        self.fetch_cfg = self.spec.get("fetch") or {}
        self.norm = self.spec.get("normalize") or {}
        self.mode = self.norm.get("mode", "map")
        self.schema = schema
        self._model = model
        self._prepare()

    def _prepare(self):
        if self.mode == "map":
            self._mapping = Mapping.from_spec(self.norm)
        elif self.mode == "builder":
            bname = self.norm.get("builder")
            if bname not in formats.BUILDERS:
                raise ValueError(f"unknown builder {bname!r}; known: {sorted(formats.BUILDERS)}")
            self._builder = formats.BUILDERS[bname]
        elif self.mode == "agent":
            from .normalize import AgentNormalizer
            self._agent = AgentNormalizer(self.norm, self.schema, model=self._model)
        else:
            raise ValueError(f"unknown normalize mode {self.mode!r} for source {self.name!r}")

    @property
    def needs_fetch(self):
        if self.mode == "builder":
            return formats.BUILDERS[self.norm["builder"]]["needs_fetch"]
        return self.fetch_cfg.get("transport") not in (None, "static") or bool(self.fetch_cfg.get("data"))

    def fetch(self):
        """Run the declarative fetch (or return inline/static data). None if not needed."""
        if self.mode == "builder" and not formats.BUILDERS[self.norm["builder"]]["needs_fetch"]:
            return None
        if not self.fetch_cfg:
            return None
        return connectors.fetch(self.fetch_cfg)

    def build(self, raw, today):
        """Normalize the raw fetched object into (entity_type, key_field, record) tuples,
        then auto-stamp the canonical source `uri` (see _stamp_source_uri)."""
        if self.mode == "map":
            items = self._mapping.apply(raw, today)
        elif self.mode == "builder":
            b = self._builder
            arg = jmespath.search(b["input"], raw) if (b["input"] and raw is not None) else raw
            args = self.norm.get("args") or {}
            items = b["fn"](arg, today, **args)
        elif self.mode == "agent":
            items = self._agent.build(raw, today)
        else:
            raise ValueError(f"unknown mode {self.mode!r}")
        return self._stamp_source_uri(items)

    def _stamp_source_uri(self, items):
        """Durable deep-link foundation: for an http_html source (whose fetched `url` IS the
        human-readable page), stamp that url onto every produced record's canonical `uri`
        when the record doesn't already carry a deep one. So future re-ingests keep uri
        without a patch. Excludes API/http_json (the fetch url is an endpoint, not a page)
        and bare-domain urls (not a citable deep link)."""
        from urllib.parse import urlsplit
        url = self.fetch_cfg.get("url") or ""
        if self.fetch_cfg.get("transport") != "http_html":
            return items
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.netloc or len(p.path.strip("/")) == 0:
            return items  # not a deep page url
        for tup in items:
            rec = tup[2] if len(tup) >= 3 else None
            if isinstance(rec, dict):
                cur = urlsplit(str(rec.get("uri") or ""))
                if not (cur.scheme in ("http", "https") and cur.netloc and len(cur.path.strip("/")) > 0):
                    rec["uri"] = url
        return items

    def run(self, today):
        """Convenience: fetch + build."""
        return self.build(self.fetch(), today)
