"""Legacy import shim — the risk synthesis logic now lives in ONE place:
`tessera_platform.catalog.synth_risk` (the shared, vendored plugin). This module
re-exports it verbatim so the legacy catalog-ingest handler (`import synth_plan`)
keeps working with zero drift — there is no copy-pasted synthesis code (AC #4).

The shared module is vendored into this Lambda by scripts/deploy-lambdas.sh (it copies
tessera_platform/catalog/*.py into every Lambda that imports tessera_platform).
"""
from tessera_platform.catalog.synth_risk import (  # noqa: F401
    cluster,
    cluster_agentic,
    cluster_scenarios,
    enumerate_slots,
    covered_cells,
    plan_slots,
    cluster_digest,
    plan,
    plan_synthesis,
)
