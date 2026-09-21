"""Proof of the whole model: a NEW domain (au-financial-advice) ingests with ZERO code
— only domains/au-financial-advice.yaml + domains/schemas/au-financial-advice.records.json.
The generic runtime loads the descriptor, builds agentic adapters, and the schema drives
validation. No per-domain Python exists for this domain.
"""
import os

from tessera_platform.catalog import descriptor as desc_mod, normalize

_DOMAINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "content", "domains"))


def test_advice_domain_loads_from_declaration_only():
    d = desc_mod.load("au-financial-advice", domains_dir=_DOMAINS)
    assert d.needs_ingest()
    # The domain declares its ingest sources; assert the long-standing core sources
    # are present rather than pinning an exact set (the declaration grows over time —
    # it currently ships 15 sources, all agent-mode). This keeps the test about the
    # zero-code declarative model, not a frozen source list.
    names = set(d.source_names())
    assert {"ato-super-rates", "asic-code-of-ethics"} <= names
    # agent-mode adapters build without any domain code
    adapters = {a.name: a for a in d.adapters()}
    assert adapters["ato-super-rates"].mode == "agent"
    assert adapters["asic-code-of-ethics"].mode == "agent"
    assert d.schema is not None and "regulatory-figure" in d.schema["$defs"]


def test_advice_schema_validates_records():
    d = desc_mod.load("au-financial-advice", domains_dir=_DOMAINS)
    good = {"figure_key": "concessional-cap-2026-27", "measure": "concessional-cap",
            "fy": "2026-27", "value": "$32,500",
            "provenance": {"ingested_by": "catalog-ingest", "refresh_date": "2026-07-07"}}
    assert normalize.validate_record(good, "regulatory-figure", d.schema) == []
    bad = {"measure": "concessional-cap"}  # missing figure_key/fy/value/provenance
    assert normalize.validate_record(bad, "regulatory-figure", d.schema)


def test_advice_agent_adapter_end_to_end_with_fake_model():
    """The ato source, driven only by config, extracts + validates + keys records."""
    d = desc_mod.load("au-financial-advice", domains_dir=_DOMAINS)
    spec = next(s for s in d.sources if s["name"] == "ato-super-rates")

    def fake_model(system, payload):
        return ('{"records": [{"measure": "concessional-cap", "fy": "2026-27", "value": "$32,500", "unit": "AUD"},'
                ' {"measure": "super-guarantee-rate", "fy": "2026-27", "value": "12%", "unit": "percent"}]}')

    an = normalize.AgentNormalizer(spec["normalize"], d.schema, model=fake_model)
    out = an.build({"text": "ATO key rates page ..."}, "2026-07-07")
    keys = sorted(r["figure_key"] for _t, _kf, r in out)
    assert keys == ["concessional-cap-2026-27", "super-guarantee-rate-2026-27"]
    assert all(_t == "regulatory-figure" and _kf == "figure_key" for _t, _kf, _r in out)
