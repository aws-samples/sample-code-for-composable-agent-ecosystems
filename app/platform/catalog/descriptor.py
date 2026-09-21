"""tessera_platform.catalog.descriptor — load + validate a domain's declarative ingest.

A domain's ingest lives in its descriptor's `ingest:` block (domains/<id>.yaml) or a
sibling domains/<id>/catalog.yaml. This turns the declaration into validated
SourceAdapters + refresh/synthesis settings. Pure/offline (from a dict) so it's testable.
"""
import os

from .adapter import SourceAdapter


class Descriptor:
    def __init__(self, domain, ingest, schema=None, model=None):
        self.domain = domain
        self.ingest = ingest or {}
        self.schema = schema
        self._model = model
        self.sources = list(self.ingest.get("sources") or [])
        self.refresh = self.ingest.get("refresh") or {}
        self.synthesis = self.ingest.get("synthesis", "none")
        # Synthesized types (emitted by the Synthesizer plugin, not by any source) + the
        # DECLARATIVE coverage gate for them. Generic: any synthesis domain declares
        # `synthesis: {plugin, produces: [...], coverage: {min_total, required_categories}}`.
        syn = self.synthesis if isinstance(self.synthesis, dict) else {}
        self.synthesis_produces = list(syn.get("produces") or [])
        self.coverage = dict(syn.get("coverage") or {})
        self._validate()

    # ── validation ──────────────────────────────────────────────────────────────
    def _validate(self):
        seen = set()
        for s in self.sources:
            name = s.get("name")
            if not name:
                raise ValueError(f"[{self.domain}] a source is missing 'name'")
            if name in seen:
                raise ValueError(f"[{self.domain}] duplicate source name {name!r}")
            seen.add(name)
            if not s.get("produces"):
                raise ValueError(f"[{self.domain}] source {name!r} declares no 'produces'")
            norm = s.get("normalize") or {}
            mode = norm.get("mode", "map")
            if mode not in ("map", "builder", "agent"):
                raise ValueError(f"[{self.domain}] source {name!r} has unknown normalize.mode {mode!r}")
            if mode in ("map", "agent") and not norm.get("entity_type"):
                raise ValueError(f"[{self.domain}] source {name!r} ({mode}) needs normalize.entity_type")
            if mode == "builder" and not norm.get("builder"):
                raise ValueError(f"[{self.domain}] source {name!r} (builder) needs normalize.builder")

    # ── adapters ──────────────────────────────────────────────────────────────────
    def adapters(self):
        return [SourceAdapter(s, schema=self.schema, model=self._model) for s in self.sources]

    def adapter(self, name):
        for s in self.sources:
            if s.get("name") == name:
                return SourceAdapter(s, schema=self.schema, model=self._model)
        raise KeyError(name)

    def source_names(self):
        return [s.get("name") for s in self.sources]

    def produced_types(self):
        """Union of every entity type any source produces (for snapshot/retire)."""
        out = []
        for s in self.sources:
            for t in (s.get("produces") or []):
                if t not in out:
                    out.append(t)
        return out

    def key_field(self, etype):
        """The entity key field for a type, from the records schema's x-tessera-keys
        ('{technique_id}' -> 'technique_id'). Falls back to a source's declared key_field."""
        import re
        keys = (self.schema or {}).get("x-tessera-keys", {}) if self.schema else {}
        m = re.findall(r"\{([^}]+)\}", keys.get(etype, ""))
        if m:
            return m[0]
        for s in self.sources:
            if etype in (s.get("produces") or []):
                kf = (s.get("normalize") or {}).get("key_field")
                if kf:
                    return kf
        return None

    def needs_ingest(self):
        return bool(self.sources)


# ── loading ────────────────────────────────────────────────────────────────────────
def _domains_dir():
    return os.path.join(os.path.dirname(__file__), "..", "..", "domains")


def load_schema(domain, domains_dir=None):
    import json
    domains_dir = domains_dir or _domains_dir()
    path = os.path.join(domains_dir, "schemas", f"{domain}.records.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load(domain, domains_dir=None, model=None):
    """Load a domain descriptor's ingest block + records schema -> Descriptor.

    In the deployed runtime, set TESSERA_DOMAINS_BUCKET to read domains/<id>.yaml +
    domains/schemas/<id>.records.json from S3. Otherwise reads the local filesystem
    (repo / tests). Looks for domains/<id>/catalog.yaml first, then an `ingest:` block
    in domains/<id>.yaml."""
    import os
    bucket = os.environ.get("TESSERA_DOMAINS_BUCKET")
    if bucket:
        return _load_s3(domain, bucket, model=model)
    import yaml
    domains_dir = domains_dir or _domains_dir()
    ingest = None
    sub = os.path.join(domains_dir, domain, "catalog.yaml")
    flat = os.path.join(domains_dir, f"{domain}.yaml")
    if os.path.exists(sub):
        with open(sub) as f:
            doc = yaml.safe_load(f) or {}
        ingest = doc.get("ingest") or doc  # allow the file to be the ingest block itself
    elif os.path.exists(flat):
        with open(flat) as f:
            doc = yaml.safe_load(f) or {}
        ingest = doc.get("ingest")
    if ingest is None:
        raise FileNotFoundError(f"no ingest declaration for domain {domain!r}")
    return Descriptor(domain, ingest, schema=load_schema(domain, domains_dir), model=model)


def _load_s3(domain, bucket, model=None):
    import json
    import yaml
    import boto3
    s3 = boto3.client("s3")

    def _get(key):
        try:
            return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception:
            return None

    body = _get(f"domains/{domain}/catalog.yaml") or _get(f"domains/{domain}.yaml")
    if body is None:
        raise FileNotFoundError(f"no descriptor in s3://{bucket}/domains/ for {domain!r}")
    doc = yaml.safe_load(body) or {}
    ingest = doc.get("ingest") or (doc if "sources" in doc else None)
    if ingest is None:
        raise FileNotFoundError(f"no ingest declaration for domain {domain!r}")
    schema_body = _get(f"domains/schemas/{domain}.records.json")
    schema = json.loads(schema_body) if schema_body else None
    return Descriptor(domain, ingest, schema=schema, model=model)


def from_dict(domain, ingest, schema=None, model=None):
    return Descriptor(domain, ingest, schema=schema, model=model)
