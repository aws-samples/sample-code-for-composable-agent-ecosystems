"""tessera_platform.catalog.mapping — the declarative `mode: map` normalizer.

Turns a raw fetched feed into `[(entity_type, key_field, record)]` from a pure,
declarative spec — no per-domain Python. A spec is:

    {
      "entity_type": "vulnerability",
      "key_field": "cve_or_ref",
      "select": "vulnerabilities[?cveID]",         # JMESPath -> item stream
                # or {"primitive": "oscal_controls"} for recursive standard formats
      "fields": {"cve_or_ref": "cveID", "name": "vulnerabilityName || ''", ...},
      "const":  {"framework": "nist-800-53-rev5"},  # literal fields
      "provenance": {"ingested_by": "catalog-ingest", "sources": ["cisa-kev"]},
      "limit": 0,
    }

Field expressions are JMESPath, extended with domain-agnostic TRANSFORM primitives
(upper/lower/truncate/eq/slug/oscal_statement/mitre_ext_*). `select` may name a
SELECTOR primitive for feeds whose item stream needs recursion (OSCAL) — the recursion
lives here as a shared platform primitive, never in a domain.

Determinism: identical input + spec => identical records (the parity contract vs the
hand-written build_* functions in lambdas/catalog-ingest/handler.py).
"""
import re
import jmespath
from jmespath import functions

from .engine import slug as _slug


# ── transform primitives (JMESPath custom functions) ─────────────────────────────
def _oscal_statement(control):
    """Concatenate an OSCAL control's statement prose (recursive parts). Mirrors
    lambdas/catalog-ingest/handler.py::_oscal_statement exactly."""
    out = []

    def walk(parts):
        for p in parts or []:
            if p.get("prose"):
                out.append(p["prose"])
            walk(p.get("parts"))

    for part in (control or {}).get("parts", []) or []:
        if part.get("name") == "statement":
            if part.get("prose"):
                out.append(part["prose"])
            walk(part.get("parts"))
    return " ".join(out)[:1200]


def _mitre_ext(obj):
    for ref in (obj or {}).get("external_references", []) or []:
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id"), ref.get("url")
    return None, None


class _CatalogFunctions(functions.Functions):
    @functions.signature({"types": []}, {"types": []})
    def _func_eq(self, a, b):
        return a == b

    @functions.signature({"types": []})
    def _func_lower(self, s):
        return "" if s is None else str(s).lower()

    @functions.signature({"types": []})
    def _func_upper(self, s):
        return "" if s is None else str(s).upper()

    @functions.signature({"types": []}, {"types": ["number"]})
    def _func_truncate(self, s, n):
        return ("" if s is None else str(s))[: int(n)]

    @functions.signature({"types": []})
    def _func_slug(self, s):
        return _slug(s if isinstance(s, str) else ("" if s is None else str(s)))

    @functions.signature({"types": []}, {"types": []})
    def _func_default(self, x, y):
        return y if x in (None, "", [], {}) else x

    @functions.signature({"types": ["object"]})
    def _func_oscal_statement(self, control):
        return _oscal_statement(control)

    @functions.signature({"types": ["object"]})
    def _func_mitre_ext_id(self, obj):
        return _mitre_ext(obj)[0]

    @functions.signature({"types": ["object"]})
    def _func_mitre_ext_url(self, obj):
        return _mitre_ext(obj)[1]


_OPTIONS = jmespath.Options(custom_functions=_CatalogFunctions())


# ── selector primitives (produce the item stream when JMESPath can't recurse) ────
def _sel_oscal_controls(root):
    """OSCAL catalog -> [{"control": <ctrl>, "family": <group title>}] flattening
    groups→controls→enhancements. Mirrors build_nist80053's walk (id-less controls
    skipped, enhancement inherits family, control emitted before its enhancements)."""
    cat = (root or {}).get("catalog") or root or {}
    out = []

    def emit(control, family):
        if not (control.get("id") or ""):
            return
        out.append({"control": control, "family": family})
        for enh in control.get("controls", []) or []:
            emit(enh, family)

    def walk_group(g):
        fam = g.get("title", "")
        for c in g.get("controls", []) or []:
            emit(c, fam)
        for sg in g.get("groups", []) or []:
            walk_group(sg)

    for g in cat.get("groups", []) or []:
        walk_group(g)
    return out


_SELECTORS = {"oscal_controls": _sel_oscal_controls}


# ── the mapping ──────────────────────────────────────────────────────────────────
class Mapping:
    """Compiled, reusable declarative normalizer for one source→type."""

    def __init__(self, spec):
        self.entity_type = spec["entity_type"]
        self.key_field = spec["key_field"]
        self.const = dict(spec.get("const") or {})
        self.provenance = dict(spec.get("provenance") or {})
        self.limit = int(spec.get("limit") or 0)
        sel = spec["select"]
        if isinstance(sel, dict):
            name = sel.get("primitive")
            if name not in _SELECTORS:
                raise ValueError(f"unknown selector primitive {name!r}; known: {sorted(_SELECTORS)}")
            self._selector = _SELECTORS[name]
            self._select_expr = None
        else:
            self._selector = None
            self._select_expr = jmespath.compile(sel)
        self._fields = {name: jmespath.compile(expr) for name, expr in (spec.get("fields") or {}).items()}

    @classmethod
    def from_spec(cls, spec):
        return cls(spec)

    def _items(self, raw):
        if self._selector is not None:
            return self._selector(raw) or []
        return self._select_expr.search(raw, options=_OPTIONS) or []

    def apply(self, raw, today):
        """raw feed -> [(entity_type, key_field, record)] with provenance stamped."""
        out = []
        for item in self._items(raw):
            rec = {name: parsed.search(item, options=_OPTIONS) for name, parsed in self._fields.items()}
            rec.update(self.const)
            prov = dict(self.provenance)
            prov["refresh_date"] = today
            rec["provenance"] = prov
            out.append((self.entity_type, self.key_field, rec))
            if self.limit and len(out) >= self.limit:
                break
        return out
