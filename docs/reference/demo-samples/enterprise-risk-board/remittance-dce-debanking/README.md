# Sample — Remittance / DCE de-banking decision (Coral Coast Bank)

A **non-tech, board-level** evidence pack for the `enterprise-risk-board` ecosystem
(`ecosystems/enterprise-risk-board.yaml`). Unlike the `agentic-servicing-assistant`
(Aria) pack — which interrogates an *AI deployment* — this pack interrogates a
**business decision**: whether a bank should exit ("de-bank") an entire higher-risk
customer segment. It shows the platform is initiative-agnostic and needs **no change**
to reason about a strategic/regulatory decision rather than a technology change.

All files are **PDFs** — the kind of committee papers a CRO actually receives —
uploaded as **run attachments** for a single interrogation run.

## Scenario (fictional)

**Coral Coast Bank Limited (CCB)** — a fictional ASX-listed, APRA-regulated mid-tier
regional ADI (~AUD 34bn assets, HQ Gold Coast) and **AUSTRAC reporting entity** — seeks
Board Risk & Compliance Committee approval to **exit its entire remittance-service-
provider (RSP) and digital-currency-exchange (DCE) business-banking segment** (~850
accounts) on **30 days' notice**, citing elevated ML/TF risk and the cost of the
**Tranche-2** compliance uplift (the reforms commenced 1 July 2026).

This is **deliberately crafted** so the eco has real signal — a genuine *tension between
two regulators* (AUSTRAC "manage your ML/TF risk" vs the AUSTRAC/CoFR de-banking
guidance "don't wholesale de-bank"), not a rubber stamp.

## Documents in this pack

| # | Document | What it evidences / plants |
|---|----------|-----------------------------|
| 01 | `01-board-paper.pdf` | Decision sought = **blanket** exit; **cost is the primary driver** (framed as risk); AUSTRAC pressure reframed as a mandate to exit; **no accountable person for the customer-fairness outcome** (the FAR orphan) |
| 02 | `02-segment-mltf-risk-assessment.pdf` | **STRONG**: a genuine, evidenced ML/TF risk assessment. **GAP**: assessed at *segment* level only — **no per-customer differentiation**, no manageable-cohort analysis. Includes the **visibility-loss** flow (de-banking pushes flows to hawala / unregulated channels) |
| 03 | `03-cost-and-program-impact.pdf` | Confirms **cost avoidance** is the real driver; a risk-based partial-retention option is ~break-even but **not developed** |
| 04 | `04-austrac-engagement-summary.pdf` | AUSTRAC asked CCB to **STRENGTHEN / MANAGE** — **did not direct exit**; management reinterprets scrutiny as supporting exit; notes Westpac/CBA were *monitoring/reporting* failures (support manage-properly, not exit) |
| 05 | `05-customer-community-impact.pdf` | Segment serves **migrant remittance corridors** (Pacific, S/SE Asia, Africa). **GAP**: no financial-inclusion or anti-discrimination assessment done |
| 06 | `06-legal-advice-extract.pdf` | Internal legal **flags** AUSTRAC/CoFR de-banking guidance, notice/unfair-contract, and **indirect discrimination (Racial Discrimination Act 1975)** — recommends risk-based + transparency — but marked **"noted, to be considered"** |
| 07 | `07-offboarding-communications-plan.pdf` | **30-day notice, generic reason, no review/appeal, no transition assistance** — contrary to de-banking transparency expectations |
| 08 | `08-amlctf-program-extract.pdf` | The bank **already runs** TM + SMR + IFTI + sanctions screening → the segment risk **can be managed**; a blanket exit may be **disproportionate** |

## How to run

In the frontend, open **Enterprise Risk Board** → **Run**, upload the 8 PDFs, and provide:

- **initiative**: "Approve the exit (offboarding and account closure) of Coral Coast
  Bank's entire remittance-service-provider (RSP) and digital-currency-exchange (DCE)
  customer segment — ~850 accounts — on 30 days' notice, citing elevated ML/TF risk and
  the cost of the Tranche-2 compliance uplift."
- **context**: "APRA-regulated mid-tier regional ADI and AUSTRAC reporting entity;
  decision follows the 1 July 2026 Tranche-2 commencement and heightened AUSTRAC
  scrutiny; segment includes AUSTRAC-registered remittance dealers serving migrant
  corridors and registered DCEs; blanket exit proposed with 30-day notice."
- **accountable_person** (optional): "CRO (accountable entity); Head of Business Banking
  (segment owner); MLRO (financial-crime program)."
- **decision_sought** (optional): "approve segment exit."
- **supporting_documentation**: the 8 attached PDFs.

## What the eco should surface (from THIS package)

- **Financial crime (grounded in `fcc-au`)** — the segment's ML/TF risk is real (cite
  `aml-obligation` records incl. `ongoing-customer-due-diligence`, `suspicious-matter-
  report-smr`, `international-funds-transfer-ifti`, `amlctf-reform-tranche-2`;
  typologies `alternative-remittance-abuse`, `cryptocurrency-laundering`). **But** the
  reversal: a **blanket exit** contradicts the risk-based approach and the de-banking
  guidance (`ml-tf-risk-assessment`, `debanking-risk-based-approach`) and **reduces
  visibility** of flows (grounded in the hawala/DCE typologies). Enforcement
  (`austrac-2020-westpac` — IFTI) shows the cost of *not monitoring*, i.e. supports
  managing risk, not exiting.
- **Regulatory (grounded in `financial-regulatory-risk` + `fcc-au`/CoFR)** —
  proportionality; the `cofr-debanking-policy` transparency/notice/review expectations;
  the AUSTRAC-said-manage-not-exit misinterpretation.
- **Conduct (grounded in `conduct-people-au`)** — fair treatment (`efficiently-honestly-
  fairly-s912a`), dispute-resolution (`rg-271-internal-dispute-resolution`), notice; and
  **indirect discrimination** against national-origin/ethnic groups (grounded in the
  **Racial Discrimination Act 1975**, `legal-instrument`).
- **Financial inclusion** — disproportionate impact on diaspora corridors (partly
  reasoning-only / `[catalog-gap]`, honestly marked).
- **FAR accountability map** — the AML outcome is owned (MLRO), but the **customer-
  fairness / de-banking-conduct outcome is an ORPHAN** (no accountable person).
- **Verdict** — **GO-WITH-CONDITIONS** (defensibly arguable as **NO-GO** on the blanket
  exit as framed): replace the blanket exit with a **risk-based, per-customer
  assessment + EDD**, retain manageable customers; **transparent reasons + ≥90-day
  notice + review mechanism**; a **financial-inclusion / anti-discrimination
  assessment**; **assign a FAR accountable person** for the fairness outcome; and
  **engage AUSTRAC** on the risk-management approach rather than presenting exit as the
  only option.

## Grounding added for this sample

Two curated `fcc-au` guidance records (`debanking-risk-based-approach`,
`cofr-debanking-policy`) and the live addition of the **Racial Discrimination Act 1975**
(`C2004A00274`) to the `conduct-people-au` legal-instrument OData source. Publish with
`bash scripts/deploy-domains.sh` so the ingest Lambda picks them up.

## Regenerating

```bash
python3 scripts/gen_debanking_samples.py   # needs fpdf2; reuses the shared PDF helpers
```

> All persons, accounts, numbers, and "Coral Coast Bank" are fictional and for
> demonstration only.
