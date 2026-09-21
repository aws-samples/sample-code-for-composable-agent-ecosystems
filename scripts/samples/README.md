# Demo sample generators

These scripts generate the **demo input document packages** (fictional PDFs) used to
walk through specific ecosystems in the UI. The generated PDFs are **not** part of a
deployment — they are optional demo collateral you can regenerate on demand.

Each generator writes its output package under a `samples/<ecosystem>/` directory. See
the per-package descriptions in [`docs/reference/demo-samples/`](../../docs/reference/demo-samples/).

## Regenerating

```bash
pip install fpdf2
python3 scripts/samples/gen_conformity_samples.py          # AI Governance Conformity
python3 scripts/samples/gen_iress_samples.py               # Adviser copilot client pack
python3 scripts/samples/gen_enterprise_risk_board_samples.py
python3 scripts/samples/gen_debanking_samples.py
python3 scripts/samples/gen_quick_suite_samples.py
```

All persons, accounts, numbers and institutions in the generated documents are fictional
and for demonstration only.
