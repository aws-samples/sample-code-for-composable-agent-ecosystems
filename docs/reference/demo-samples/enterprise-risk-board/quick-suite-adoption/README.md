# Sample — Enterprise adoption of Amazon Quick Suite (Tasman National Bank)

A **realistic, balanced** evidence pack for the `enterprise-risk-board` ecosystem — a
large Australian bank adopting an agentic AI workspace enterprise-wide. Deliberately
*less damning* than the `agentic-servicing-assistant` (Aria) pack: this is a
competently-run adoption that should resolve to **GO-WITH-CONDITIONS**, showing the
board the platform's *calibrated* judgement rather than a foregone NO-GO.

## Scenario (fictional)

**Tasman National Bank Limited (TNB)** — a top-tier, ASX-listed, APRA-regulated
Australian ADI (~41,000 staff, ~AUD 1.15tn assets, ~8.5m customers) — seeks
Technology & Operational Risk Committee endorsement to roll out **Amazon Quick Suite**
(AWS's agentic AI-powered workspace: Quick Sight / Quick Index / Quick Research /
Quick Flows / Quick Automate + the *Amazon Quick* agentic assistant) to all staff.

Phase 3 is **read-only**, connected to Microsoft 365, ServiceNow, Confluence/Jira and a
**governed subset** of the data lake — **not** core banking, payments, or raw customer
PII. Agentic **write-actions** are on the roadmap but **not** part of this decision.

> Product note: **Amazon Quick Suite** is a real AWS product (GA Oct 2025). If you
> intended a different product (e.g. a virtual-desktop / DaaS offering), regenerate with
> the details changed — the scenario framing is the only product-specific part.

## Documents in this pack

| # | Document | Feeds (lenses) |
|---|----------|----------------|
| 01 | Initiative brief (decision sought, scope, phasing) | intake, all |
| 02 | Solution architecture & data flow | cyber/tech, privacy, regulatory |
| 03 | Privacy Impact Assessment (APPs) | privacy (AU) |
| 04 | Information Security Assessment (CPS 234) | cyber/tech |
| 05 | Third-Party / Material Service Provider Assessment (CPS 230) | regulatory, cyber/tech |
| 06 | AI & Model Governance Assessment | regulatory/model, responsible-AI |
| 07 | Operational Resilience Assessment (CPS 230) | regulatory, cyber/tech |
| 08 | Conduct, People, Accountability & Legal Assessment | conduct, people, accounting/tax |

## How to run

In the frontend, open **Enterprise Risk Board** → **Run**, upload the 8 PDFs, and provide:

- **initiative**: "Enterprise-wide rollout of Amazon Quick Suite (AWS's agentic AI
  workspace) to ~41,000 staff at Tasman National Bank — Phase 3, read-only, connected to
  M365, ServiceNow, Confluence/Jira and a governed data-lake subset."
- **context**: "Top-tier APRA-regulated Australian ADI. Technology & Operational Risk
  Committee endorsement. Phase 1 (500) and Phase 2 (8,200) complete. Core banking,
  payments, customer PII systems and agentic write-actions are out of scope for this
  decision."
- **accountable_person** (optional): "Chief Operating Officer (sponsor); Chief Risk
  Officer (accountable entity)."
- **decision_sought** (optional): "Endorse Phase 3 enterprise rollout, subject to
  conditions."
- **supporting_documentation**: the 8 attached PDFs.

## Expected shape of the outcome (illustrative — not a script)

A **GO-WITH-CONDITIONS** memo is the likely calibrated outcome, with genuine controls
recognised and a defined set of conditions precedent — contrasting the Aria NO-GO.

**Controls the lenses should recognise as PRESENT (cited):**
- CPS 234-aligned security: Entra ID SSO/MFA, KMS encryption, audit logging, ACL-honouring connectors.
- A completed DPIA; AU-region (Sydney) tenancy.
- AWS third-party attestations sighted (SOC 2, ISO 27001, IRAP Protected, PCI DSS).
- Non-critical CPS 230 classification well-supported (productivity layer, not in the customer-service path).

**Realistic residual findings / conditions (grounded in the catalogs):**
- **Privacy (APP 8)**: AU tenancy set, but not all processing (model inference / Quick Research web) confirmed in-country → reasonable-steps + disclosure. *[privacy_data_lens]*
- **Data oversharing**: per-user knowledge graph + broad connector indexing surfaces latent over-permissioning → pre-scale access review / least privilege. *[cyber_tech_lens]*
- **Agentic write-actions**: read-only now; write-scope on roadmap → action-governance required before enabling. *[regulatory_lens, cyber_tech_lens]*
- **Model governance**: vendor-managed models update frequently; no TNB material-change notification / validation / model-risk tiering. *[regulatory_lens, responsible_ai_mapper]*
- **CPS 230 third-party**: AWS assessed but Quick Suite not yet formally classified/registered as a material arrangement; no documented exit. *[regulatory_lens]*
- **Conduct**: acceptable-use forbids regulated-decision use without review, but monitoring at 41k scale unproven → training + assurance sampling. *[conduct_lens]*
- **FAR accountability**: COO + CRO named, but no single accountable person for AI/model governance of the tool → assign + record. *[accountability_mapper, people_lens]*
- **Accounting (minor)**: SaaS/config cost treatment under AASB 138 / cloud-arrangement guidance — administrative. *[accounting_tax_lens]*
- **Responsible-AI**: transparency/fairness low-exposure in read-only Phase 3; reliability tied to the model-governance gaps. *[responsible_ai_mapper]*

## Regenerate

```bash
python3 scripts/gen_quick_suite_samples.py
```
