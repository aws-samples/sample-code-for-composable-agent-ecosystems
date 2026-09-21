# Enterprise Risk Board — sample document package

A realistic input package for the **`enterprise-risk-board`** ecosystem
(`ecosystems/enterprise-risk-board.yaml`). All files are **PDFs** — the kind of
board-committee papers a Chief Risk Officer actually receives — uploaded as **run
attachments** for a single interrogation run.

The eco reads these as **primary evidence**: a control is graded PRESENT only if an
attached document evidences it (quoted + cited), and UNMET only if no supplied
document covers it. It grounds its findings in the mounted catalogs
(`financial-regulatory-risk`, `cyber-security-risk`, `privacy-au`, `fcc-au`) and
adversarially challenges the assessment before forming a verdict.

## Scenario — `agentic-servicing-assistant/` (an agentic system that moves money)

**Meridian Mutual Bank** (fictional APRA-regulated ADI and AML/CTF reporting entity)
seeks Risk & Compliance Committee approval to deploy **"Aria"**, an agentic customer-
servicing assistant that reads customer PII and can **autonomously reverse fees /
issue refunds (≤ $500) and initiate payments / transfers (≤ $5,000)** — built on a
**US-hosted** third-party foundation model, with **~70% of interactions fully
automated** and no routine human review.

This package is **deliberately crafted** so the eco has real signal — it shows the
platform's power, not a rubber stamp. It mixes genuinely strong, evidenced controls
with pointed, realistic gaps:

| File | What it evidences / plants |
|------|-----------------------------|
| `01-board-paper.pdf` | The **decision sought** + context + autonomy; names the Head of Digital as owner and CRO as accountable entity — but **no accountable person for model risk or financial crime** |
| `02-company-profile.pdf` | APRA ADI + AML/CTF reporting entity; FAR applies; CPS 220/230/234 in scope; an MLRO + AML program *on existing channels* |
| `03-solution-design.pdf` | Architecture + tools; **customer PII sent to a US-hosted model**; money-movement tools; ~70% autonomous |
| `04-dpia.pdf` | **STRONG** on collection + security (APP 3/5/11); **GAP**: APP 8 overseas disclosure **deferred**, automated-decision transparency (APP 1) out of scope |
| `05-nimbus-model-card.pdf` | Vendor card; **GAP**: no Meridian-owned independent validation or drift monitoring (third-party black box) |
| `06-information-security-standard.pdf` | **STRONG** CPS 234 controls (auth, encryption, immutable logging); **GAP**: silent on prompt-injection / tool-permission abuse of the payment tool |
| `07-third-party-risk-assessment.pdf` | Vendor due diligence done (SOC 2 sighted); **GAP**: CPS 230 materiality **not assessed**, **no exit/continuity plan**, data-offshoring unresolved |

> **The deliberate absence is the point:** there is **no financial-crime / AML control
> document** for Aria. A money-moving agent with no transaction-monitoring, SMR
> pathway, or sanctions screening described anywhere is the standout blocker the
> `financial_crime_lens` and the challenger are meant to catch.

## How to run

Open the Tessera frontend, choose **Enterprise Risk Board**, choose **Run**, upload
**all seven PDFs** as run attachments, and fill the inputs:

- **initiative:** *"Approve production deployment of 'Aria', an agentic customer-
  servicing assistant that reads customer PII and can autonomously reverse fees /
  refund up to $500 and initiate payments / transfers up to $5,000, built on a
  US-hosted third-party foundation model."*
- **context:** *"APRA-regulated mutual bank (ADI) and AML/CTF reporting entity;
  retail customers; ~70% of interactions fully automated; single US-hosted model
  vendor; full national rollout sought Q4 2026."*
- **accountable_person (optional):** *"CRO (accountable entity); Head of Digital
  (business owner)"*
- **decision_sought (optional):** *"approve for production"*

## What the eco should surface (from THIS package)

- **Privacy (grounded in `privacy-au`)** — collection/security PRESENT and cited to the
  DPIA; **BLOCKER: APP 8 overseas disclosure** to the US-hosted model is deferred,
  not assessed; automated-decision transparency (APP 1) not covered.
- **Financial crime (grounded in `fcc-au`)** — **BLOCKER: no AML/CTF control for Aria-
  initiated money movement** — no transaction monitoring, SMR pathway, or sanctions
  screening in any supplied document, despite payment/refund tools (cite
  `aml-obligation` records; `financial-crime-typology` for the mule/structuring
  exposure a money-moving bot enables).
- **Regulatory / third-party (grounded in `financial-regulatory-risk`)** — **CPS 230**
  material-service-provider classification and exit/continuity plan **missing**;
  single-vendor dependency.
- **Model** — reliance on vendor evaluations; **no Meridian-owned independent
  validation or drift monitoring** (CPS 220).
- **Cyber (grounded in `cyber-security-risk`)** — perimeter security PRESENT and cited;
  **agentic-threat gap**: prompt-injection / tool-permission abuse of the payment
  tool unaddressed (cite `attack-technique` / `security-control`).
- **Conduct / People (catalog-gap — reasoning only, honestly marked)** — ~70%
  autonomous money movement with no routine human review → unfair-outcome / consumer
  exposure; no accountable person for model or financial-crime risk.
- **FAR accountability map** — most risks map to a role, but **model risk and
  financial-crime risk are ORPHANS** (no accountable person) — a FAR "no gaps" flag.
- **Verdict:** **GO-WITH-CONDITIONS** (defensibly arguable as NO-GO on the AML
  blocker), with named conditions precedent + owners and the orphan-accountability
  flag — delivered as the board-ready **Risk Interrogation & Go/No-Go Memo** (HTML).

The demo lands because the memo is **cited** (APP 8, CPS 230, CPS 234, AML/CTF
obligations, ATT&CK/agentic threats), **adversarially challenged** (the money-moving-
with-no-AML blocker), **accountability-mapped** (the orphan), and **defensible** —
not a numeric score, and not a chatbot opinion.

## Regenerating

The PDFs are generated by:

```bash
python3 scripts/gen_enterprise_risk_board_samples.py   # needs fpdf2
```

Edit the content in that script and re-run to regenerate.

> All persons, accounts, numbers, institutions, and the "Nimbus AI" vendor are
> fictional and for demonstration only.
