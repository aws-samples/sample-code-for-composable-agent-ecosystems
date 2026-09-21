# AI Governance Conformity Desk — sample document packages

Realistic input packages for the **`ai-governance-conformity`** ecosystem
(`ecosystems/ai-governance-conformity.yaml`). All files are **PDFs** — the kind
of documents a product / governance / risk team actually hands over — and are
uploaded as **run attachments** for a single assessment run.

The ecosystem sources model cards for the *named* model itself (web / HF API)
and reads these supplied documents as **primary evidence**: a control is graded
PRESENT only if an attached document evidences it (quoted + cited), and ABSENT
only if no supplied document covers it.

## Two packages, contrasting breadth

### `au-loan-approval/` — BROAD (evidence-rich → expected AMBER)
A regulated Australian bank that plausibly has real governance artefacts. The
package makes many controls resolve **present, cited to a document**, and shows
evidence overriding what would otherwise be a gap (e.g. human oversight).

| File | Evidences |
|------|-----------|
| `company-profile.pdf` | The company; AI Governance Committee, CRO accountability, jurisdictions (AU only) |
| `solution-overview.pdf` | Solution + **architecture diagram**; names **Meta Llama 3.1 70B-Instruct (self-hosted)** + in-house scorecard; autonomy |
| `dpia.pdf` | Completed DPIA (Privacy Act), automated-decision-making, applicant recourse |
| `model-risk-assessment.pdf` | Risk-management system, validation, drift monitoring, fallback (APRA CPS 220) |
| `bias-fairness-test-report.pdf` | Quarterly disparate-impact testing (four-fifths rule), results, remediation |
| `human-oversight-sop.pdf` | **Every decline reviewed by a credit officer within 2 business days**; override; anti-automation-bias |
| `data-governance-policy.pdf` | Training-data lineage, representativeness/bias examination, retention, lawful basis |
| `logging-monitoring-standard.pdf` | Immutable 7-year logging; post-market monitoring; 15-day incident reporting |

### `eu-hiring-agent/` — THIN (no evidence → expected RED)
An EU HR-tech startup vendor pre-Aug-2. One brief, naming the model, that plainly
states no governance documentation exists → the eco finds real gaps.

| File | Contents |
|------|----------|
| `solution-brief.pdf` | Company paragraph; names **Anthropic Claude (via Amazon Bedrock)** + in-house scorer; autonomy (~85% of rejections never reviewed); explicit statement that only a marketing sheet + SOC 2 exist (no DPIA, model card, bias testing, oversight procedure, or logging) |

The contrast is the point: a mature bank (broad, evidence → AMBER, with residual
gaps) vs a startup vendor (thin, no evidence → RED).

## How to run

Open the Tessera frontend, choose **AI Governance Conformity Desk**, choose
**Run**, and **upload the PDFs from one package as run attachments**, then give a
one-line description (or paste the solution overview text) as the solution input.

- The ecosystem requires the `cyber-security-risk` catalog (EU AI Act, NIST AI
  RMF, NIST AI 600-1, OWASP, MITRE ATLAS, AI incidents, crosswalk).
- PDFs are text-extracted by `file-classify` (`<key>.extracted.txt`) and the
  **full text** is folded into the run so the mapper/harmonizer/grade agents can
  quote exact clauses — e.g. *[per doc: human-oversight-sop.pdf — "every declined
  application is independently reviewed … within two business days"]*.

## Regenerating

The PDFs are generated (content + the AU architecture diagram) by:

```bash
python3 scripts/gen_conformity_samples.py   # needs fpdf2
```

Edit the content in that script and re-run to regenerate.
