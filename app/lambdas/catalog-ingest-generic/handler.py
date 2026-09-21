"""catalog-ingest-generic — the DECLARATIVE, domain-agnostic ingest Lambda.

A thin dispatcher: every domain runs the SAME code. It loads the domain's descriptor
(from S3 via TESSERA_DOMAINS_BUCKET, or local fs) and dispatches the action to the
shared orchestrator. A new domain needs NO new Lambda — just its descriptor + schema.

Event: {"action": "ingest_all"|"mark_source"|"mark_run"|"plan_synthesis"|"coverage",
        "domain": "<id>", ...}  (default action = ingest one source)
"""
from tessera_platform.catalog import orchestrate


def handler(event, context=None):
    return orchestrate.handler(event, context)
