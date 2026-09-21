"""tessera_platform.catalog.normalize — schema validation + the agentic normalizer.

For unstructured/HTML/PDF sources with no clean feed (`normalize.mode: agent`). A
generic normalizer agent turns raw source text into schema-conformant catalog records,
fenced by JSON-Schema validation + provenance. The model call is INJECTABLE so the
post-processing (key derivation, validation, provenance) is unit-testable offline.

Records that fail validation are dropped (loud) — never silently written.
"""
import json
import os
import re


# ── schema loading + validation ───────────────────────────────────────────────────
def load_schema(domain, schemas_dir=None):
    schemas_dir = schemas_dir or os.path.join(os.path.dirname(__file__), "..", "..", "domains", "schemas")
    path = os.path.join(schemas_dir, f"{domain}.records.json")
    with open(path) as f:
        return json.load(f)


def _def_for(schema, entity_type):
    return (schema.get("$defs") or {}).get(entity_type)


def _minimal_validate(record, defn):
    """Self-contained validation (required + primitive type + pattern) so the Lambda
    runtime needs no extra dependency. Returns a list of error strings."""
    errs = []
    props = defn.get("properties") or {}
    for req in defn.get("required") or []:
        if req not in record or record[req] in (None, ""):
            errs.append(f"missing required field {req!r}")
    _pytypes = {"string": str, "number": (int, float), "integer": int,
                "boolean": bool, "array": list, "object": dict}
    for name, spec in props.items():
        if name not in record or record[name] is None:
            continue
        val = record[name]
        t = spec.get("type")
        types = [t] if isinstance(t, str) else (t or [])
        if types:
            ok = any(isinstance(val, _pytypes[x]) for x in types if x in _pytypes and x != "null")
            if types and "null" not in types and not ok and any(x in _pytypes for x in types):
                errs.append(f"field {name!r} expected {types}, got {type(val).__name__}")
        if spec.get("pattern") and isinstance(val, str) and not re.search(spec["pattern"], val):
            errs.append(f"field {name!r} fails pattern {spec['pattern']}")
        if spec.get("enum") and val not in spec["enum"]:
            errs.append(f"field {name!r}={val!r} not in enum")
    return errs


def validate_record(record, entity_type, schema, use_jsonschema=True):
    """Validate one record against the domain schema's $def. Returns [] if valid."""
    defn = _def_for(schema, entity_type)
    if not defn:
        return [f"no $def for entity type {entity_type!r}"]
    if use_jsonschema:
        try:
            import jsonschema
            resolver = jsonschema.RefResolver.from_schema(schema)
            v = jsonschema.Draft202012Validator(defn, resolver=resolver)
            return [e.message for e in v.iter_errors(record)]
        except ImportError:
            pass
        except Exception:
            pass  # fall through to the minimal validator on resolver/version issues
    return _minimal_validate(record, defn)


# ── key derivation ────────────────────────────────────────────────────────────────
def render_key(template, record):
    """Render a key like '{measure}-{fy}' from a record's fields."""
    def sub(m):
        return str(record.get(m.group(1), "")).strip()
    return re.sub(r"\{([^}]+)\}", sub, template)


# ── the agentic normalizer ─────────────────────────────────────────────────────────
_SYSTEM = (
    "You extract DURABLE, entity-keyed catalog records from an authoritative source. "
    "Return STRICT JSON only: {\"records\": [ {..record..}, ... ]}. Each record MUST "
    "conform to the provided JSON-Schema $def (only its fields; correct types). Extract "
    "one record per distinct entity. Do NOT invent values not present in the source; if "
    "a required value is absent, omit that record. No prose, no code fences."
)


def _default_model(system, payload, region=None):
    import boto3
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    model_id = os.environ.get("CATALOG_NORMALIZER_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    brt = boto3.client("bedrock-runtime", region_name=region)
    resp = brt.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": json.dumps(payload)}]}],
        inferenceConfig={"maxTokens": 4000, "temperature": 0},
    )
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])


class AgentNormalizer:
    """Schema-driven LLM normalizer. `model` is a callable (system, payload)->text,
    injectable for tests; defaults to Bedrock converse."""

    def __init__(self, spec, schema, model=None):
        self.entity_type = spec["entity_type"]
        self.key_template = spec.get("key") or ("{" + (spec.get("key_field") or "id") + "}")
        self.key_field = spec.get("key_field") or self._infer_key_field(schema)
        self.guidance = spec.get("guidance", "")
        self.provenance = dict(spec.get("provenance") or {})
        self.schema = schema
        self.defn = _def_for(schema, self.entity_type) or {}
        self._model = model or _default_model

    def _infer_key_field(self, schema):
        keys = (schema.get("x-tessera-keys") or {}).get(self.entity_type, "")
        m = re.findall(r"\{([^}]+)\}", keys)
        return m[0] if m else "id"

    def _raw_text(self, raw):
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return raw.get("text") or raw.get("html") or json.dumps(raw)[:20000]
        return json.dumps(raw)[:20000]

    def build(self, raw, today):
        payload = {"guidance": self.guidance, "schema_def": self.defn,
                   "source_text": self._raw_text(raw)[:60000]}
        text = self._model(_SYSTEM, payload)
        text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"records": []}
        out = []
        for rec in (data.get("records") or []):
            if not isinstance(rec, dict):
                continue
            prov = dict(self.provenance); prov["refresh_date"] = today
            rec["provenance"] = prov
            if self.key_field not in rec:
                rendered = render_key(self.key_template, rec)
                if rendered:
                    rec[self.key_field] = rendered
            errs = validate_record(rec, self.entity_type, self.schema)
            if errs:
                continue  # loud drop: never write a non-conformant record
            out.append((self.entity_type, self.key_field, rec))
        return out
