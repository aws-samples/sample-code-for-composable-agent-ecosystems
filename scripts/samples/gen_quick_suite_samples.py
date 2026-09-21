#!/usr/bin/env python3
"""Generate the enterprise-risk-board 'Amazon Quick Suite adoption' sample as PDFs.

Scenario (fictional but realistic): Tasman National Bank Limited (TNB) - a top-tier,
ASX-listed, APRA-regulated Australian ADI (~41,000 staff, ~AUD 1.15tn assets, ~8.5m
customers) - seeks Technology & Operational Risk Committee endorsement to roll out
AMAZON QUICK SUITE (AWS's agentic AI-powered workspace: Quick Sight, Quick Flows,
Quick Automate, Quick Index, Quick Research + the 'Amazon Quick' agentic assistant)
enterprise-wide as the staff productivity + agentic-automation layer, connected to
Microsoft 365, ServiceNow, Confluence/Jira and a governed subset of the enterprise
data lake.

Unlike the deliberately-damning 'Aria' pack (a money-moving agent engineered to trip
every blocker -> NO-GO), this pack is 'a bit more real': a genuine, competently-run
enterprise SaaS/agentic adoption. TNB has done real work (AU-region tenancy, Entra ID
SSO/MFA, a DPIA, an AWS third-party assessment citing SOC 2 / ISO 27001 / IRAP, an
acceptable-use policy) - so several lenses should mark controls PRESENT. The residual
risks are the REALISTIC ones for this class of adoption, not contrived catastrophes:

  - APP 8 / cross-border: AU-region tenancy is set, but not ALL processing (certain
    model inference / Quick Research web retrieval) is yet confirmed in-country ->
    'reasonable steps' + disclosure to close.
  - Data oversharing: the per-user knowledge graph + broad connector indexing can
    surface documents a user technically can access but shouldn't (the classic
    Copilot-style over-permissioning risk) -> access review / least privilege before
    enterprise scale.
  - Agentic 'take actions': Phase 1 is read-only, but the roadmap enables write-
    actions (raise tickets, send email, update records) -> action-governance,
    guardrails and audit not yet defined for write scope.
  - Model governance: vendor-managed foundation models update frequently; no TNB-side
    material-change notification / validation; CPS 234 covers infra but AI-agent
    threats (indirect prompt injection via indexed content) only partially addressed.
  - CPS 230 third-party: AWS assessed, but Quick Suite not yet formally classified /
    registered as a material arrangement; exit / substitutability not documented.
  - Conduct: acceptable-use forbids using outputs for regulated advice/decisions
    without human review, but monitoring that at 41k scale is unproven.
  - FAR: COO sponsor + CRO named, but no single accountable person for AI/model
    governance of the tool.
  - Accounting (minor): SaaS + configuration cost treatment (AASB 138 / cloud
    arrangements) - being handled by Finance.

  Expected verdict: GO-WITH-CONDITIONS - an approvable adoption with a defined set of
  conditions precedent + owners; no single catastrophic BLOCKER. A realistic contrast
  to the Aria NO-GO that shows the board the platform's calibrated judgement.

Run:  python3 scripts/gen_quick_suite_samples.py
Deps: fpdf2
"""
import os
from fpdf import FPDF

ROOT = os.path.join(os.path.dirname(__file__), "..", "samples", "enterprise-risk-board",
                    "quick-suite-adoption")

_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u20ac": "EUR ", "\u00d7": "x", "\u2265": ">=", "\u2264": "<=",
    "\u2192": "->", "\u2194": "<->", "\u2022": "-", "\u2026": "...", "\u00a0": " ",
    "\u2248": "~", "\u00ae": "(R)", "\u2122": "(TM)",
}


def _s(t: str) -> str:
    for k, v in _SUBS.items():
        t = t.replace(k, v)
    return t.encode("latin-1", "replace").decode("latin-1")


class Doc(FPDF):
    def header(self):
        if getattr(self, "_meta", None):
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120)
            self.cell(0, 6, _s(self._meta), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(140)
        self.cell(0, 6, f"{_s(getattr(self, '_docref', ''))}  -  page {self.page_no()}", align="C")
        self.set_text_color(0)


def _title(pdf, title, subtitle, classification):
    pdf.set_font("Helvetica", "B", 17)
    pdf.multi_cell(0, 8, _s(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90)
    pdf.multi_cell(0, 6, _s(subtitle), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150)
    pdf.cell(0, 5, _s(classification), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(3)
    pdf.set_draw_color(200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)


def _h(pdf, text):
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 12)
    pdf.multi_cell(0, 6, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _p(pdf, text):
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _bullets(pdf, items):
    pdf.set_font("Helvetica", "", 10)
    for it in items:
        pdf.multi_cell(0, 5, _s("- " + it), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _kv(pdf, pairs):
    for k, v in pairs:
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, _s(f"{k}: ") + "", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_x(pdf.l_margin + 4)
        pdf.multi_cell(0, 5, _s(v), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
    pdf.ln(1)


def build(path, meta, docref, title, subtitle, classification, blocks, diagram=None):
    pdf = Doc()
    pdf._meta = meta
    pdf._docref = docref
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    _title(pdf, title, subtitle, classification)
    for b in blocks:
        kind = b[0]
        if kind == "h":
            _h(pdf, b[1])
        elif kind == "p":
            _p(pdf, b[1])
        elif kind == "b":
            _bullets(pdf, b[1])
        elif kind == "kv":
            _kv(pdf, b[1])
        elif kind == "diagram" and diagram:
            diagram(pdf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf.output(path)
    print("wrote", os.path.relpath(path, os.path.join(os.path.dirname(__file__), "..")))


def quick_suite_architecture(pdf):
    """Box-and-arrow architecture for the Quick Suite enterprise rollout."""
    pdf.ln(2)
    x0, y0 = pdf.l_margin, pdf.get_y()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def box(x, y, w, h, label, fill=(235, 240, 250)):
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(90, 110, 150)
        pdf.rect(x, y, w, h, style="DF")
        pdf.set_xy(x, y + h / 2 - 5)
        pdf.set_font("Helvetica", "", 7.3)
        pdf.multi_cell(w, 3.5, _s(label), align="C")

    def arrow(x1, y1, x2, y2):
        pdf.set_draw_color(120)
        pdf.line(x1, y1, x2, y2)

    row_y = y0 + 4
    bw, bh = 40, 16
    gap = (W - 3 * bw) / 2
    xs = [x0 + i * (bw + gap) for i in range(3)]
    box(xs[0], row_y, bw, bh, "~41,000 staff\n(browser + desktop)")
    box(xs[1], row_y, bw, bh, "Amazon Quick Suite\ntenancy - AWS ap-southeast-2\n(Sydney)", fill=(225, 245, 235))
    box(xs[2], row_y, bw, bh, "Foundation models +\nQuick Research web\n(region TBC - APP 8)", fill=(250, 240, 225))
    for i in range(2):
        arrow(xs[i] + bw, row_y + bh / 2, xs[i + 1], row_y + bh / 2)

    conn_y = row_y + bh + 13
    pdf.set_xy(x0, conn_y - 6)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.cell(0, 4, _s("Quick Index connectors (honour source ACLs) + agentic actions:"), new_x="LMARGIN", new_y="NEXT")
    conns = ["Microsoft 365\n(SharePoint/Teams/Outlook)", "ServiceNow", "Confluence / Jira",
             "Enterprise data lake\n(governed subset)", "Write-actions\n(Phase 3 - roadmap)"]
    tw = (W - (len(conns) - 1) * 3) / len(conns)
    for i, t in enumerate(conns):
        fill = (250, 232, 232) if "Write-actions" in t else (240, 240, 240)
        box(x0 + i * (tw + 3), conn_y, tw, 16, t, fill=fill)
    arrow(xs[1] + bw / 2, row_y + bh, xs[1] + bw / 2, conn_y)
    pdf.set_y(conn_y + 20)
    pdf.ln(2)


def gen():
    CLASS = "CONFIDENTIAL - TNB Technology & Operational Risk Committee - 22 July 2026"
    META = "Tasman National Bank - Technology & Operational Risk Committee"

    # 01 - Initiative brief -------------------------------------------------------
    build(
        os.path.join(ROOT, "01-initiative-brief.pdf"), META, "01-initiative-brief.pdf",
        "Committee Paper - Enterprise Adoption of Amazon Quick Suite",
        "Agenda item 4: Endorsement to proceed to enterprise rollout (Phase 3)",
        CLASS,
        [
            ("h", "1. Decision sought"),
            ("p", "The Committee is asked to ENDORSE the enterprise-wide rollout of Amazon Quick Suite "
                  "to all ~41,000 TNB staff (Phase 3), following successful Phase 1 pilot (500 users) and "
                  "Phase 2 (8,200 corporate-function users). Endorsement is sought subject to any conditions "
                  "the Committee and the second line consider necessary."),
            ("h", "2. Background and business case"),
            ("p", "Amazon Quick Suite is AWS's agentic AI-powered workspace (general availability October 2025). "
                  "It combines five capabilities - Quick Sight (analytics/visualisation), Quick Index (data "
                  "discovery across connected sources), Quick Research (deep research incl. external web), Quick "
                  "Flows and Quick Automate (workflow/process automation) - and an agentic assistant ('Amazon "
                  "Quick') that answers questions and can take actions across connected applications. It builds a "
                  "per-user knowledge graph over the sources each user is permitted to see."),
            ("kv", [
                ("Sponsor", "Chief Operating Officer (business owner)"),
                ("Delivery", "Group Technology - Workplace & AI Platforms"),
                ("Risk owner", "Chief Risk Officer (accountable entity for the arrangement)"),
                ("Vendor", "Amazon Web Services (AWS) - Amazon Quick Suite (SaaS)"),
                ("Population", "~41,000 employees + ~3,000 contractors at full scale"),
            ]),
            ("h", "3. Expected benefits"),
            ("b", ["Estimated 3.5-5.0% productivity uplift in knowledge-work roles (internal pilot telemetry + "
                   "AWS benchmarks) via faster search, drafting and analysis.",
                   "Consolidation of several point BI/automation tools onto one governed platform.",
                   "Faster analyst self-service reporting (Quick Sight) reducing central-BI backlog."]),
            ("h", "4. Scope and phasing"),
            ("b", ["Phase 1 (complete): 500 pilot users in Technology and Finance; read-only connectors.",
                   "Phase 2 (complete): ~8,200 corporate-function users (Finance, HR, Legal, Risk, Ops).",
                   "Phase 3 (this decision): enterprise rollout to all staff; connectors as per the solution "
                   "architecture (M365, ServiceNow, Confluence/Jira, governed data-lake subset).",
                   "OUT OF SCOPE (Phase 3): connection to core banking, the payments switch, or customer PII "
                   "systems; enabling agentic WRITE-actions (raise tickets / send email / update records) - both "
                   "are on the future roadmap and are NOT part of this endorsement."]),
            ("h", "5. Financials (indicative)"),
            ("p", "Year-1 total cost of ownership ~AUD 14.2m (subscription + integration + change/enablement). "
                  "Treated as an operating expense; Finance is confirming SaaS configuration-cost treatment under "
                  "AASB 138 and the cloud-computing-arrangement guidance."),
            ("h", "6. Recommendation"),
            ("p", "Group Technology recommends the Committee endorse Phase 3, subject to the second-line risk "
                  "assessment and any conditions precedent the Committee sets."),
        ],
    )

    # 02 - Solution architecture --------------------------------------------------
    build(
        os.path.join(ROOT, "02-solution-architecture.pdf"), META, "02-solution-architecture.pdf",
        "Solution Architecture and Data Flow - Amazon Quick Suite",
        "How Quick Suite is deployed, what it connects to, and where data flows",
        CLASS,
        [
            ("h", "1. Deployment model"),
            ("p", "Amazon Quick Suite is consumed as a multi-tenant SaaS. TNB's tenancy is provisioned in the "
                  "AWS Asia Pacific (Sydney) Region (ap-southeast-2). Data at rest is encrypted with AWS KMS; "
                  "data in transit uses TLS 1.2+. Users access via browser and a desktop client, authenticated "
                  "through TNB single sign-on."),
            ("diagram", None),
            ("h", "2. Identity and access"),
            ("b", ["Single sign-on via Microsoft Entra ID (SAML/OIDC), MFA enforced, conditional-access policies "
                   "(managed-device + geo).",
                   "User lifecycle via SCIM - joiners/movers/leavers deprovisioned automatically.",
                   "Quick Index honours the access controls of each connected source: a user only sees indexed "
                   "content they are already permitted to open in the source system."]),
            ("h", "3. Connected sources (Phase 3)"),
            ("b", ["Microsoft 365 - SharePoint Online, Teams, Outlook/Exchange (per-user mailbox and files).",
                   "ServiceNow - IT and HR service records (role-scoped).",
                   "Confluence / Jira - engineering and project knowledge.",
                   "Enterprise data lake - a GOVERNED SUBSET only (curated, de-identified analytics marts); raw "
                   "customer PII datasets are NOT connected in Phase 3.",
                   "Quick Research may retrieve from the public web for research tasks."]),
            ("h", "4. Data flow and residency"),
            ("b", ["Indexed content and per-user knowledge-graph state are stored in the Sydney-region tenancy.",
                   "Model inference for some Quick capabilities may be served from AWS foundation-model endpoints "
                   "whose regional placement is being confirmed with AWS; Quick Research web retrieval egresses to "
                   "the public internet. Confirming that ALL processing of TNB content remains in-country (or "
                   "documenting the residual) is an OPEN item - see the Privacy Impact Assessment.",
                   "Prompts, retrieved context and outputs are logged to TNB's tenancy for audit."]),
            ("h", "5. Agentic actions"),
            ("p", "Phase 3 is READ-ONLY (answer, analyse, draft). The product roadmap supports WRITE-actions "
                  "(create ServiceNow tickets, send email on the user's behalf, update records). Write-actions are "
                  "NOT enabled in this endorsement and would require a separate action-governance design (scopes, "
                  "approvals, audit, blast-radius limits)."),
        ],
        diagram=quick_suite_architecture,
    )

    # 03 - Privacy impact assessment ----------------------------------------------
    build(
        os.path.join(ROOT, "03-privacy-impact-assessment.pdf"), META, "03-privacy-impact-assessment.pdf",
        "Privacy Impact Assessment (PIA/DPIA) - Amazon Quick Suite",
        "Assessment under the Privacy Act 1988 (Cth) and the Australian Privacy Principles",
        CLASS,
        [
            ("h", "1. Scope and personal information involved"),
            ("p", "Quick Suite indexes staff-facing content. Personal information in scope is primarily EMPLOYEE "
                  "personal information (emails, HR/ServiceNow records) and any personal information incidentally "
                  "present in corporate documents. Raw CUSTOMER PII datasets are excluded from Phase 3 connectors; "
                  "however customer personal information may appear inside emails, documents and service records."),
            ("h", "2. APP assessment (summary)"),
            ("b", ["APP 1 (open and transparent) - privacy policy and internal collection notice being updated to "
                   "describe AI-assisted processing.",
                   "APP 3 / APP 6 (collection, use and disclosure) - use is limited to internal productivity; "
                   "acceptable-use policy prohibits entering third-party or customer PII beyond what the connected "
                   "sources already hold.",
                   "APP 8 (cross-border disclosure) - AU-region tenancy is provisioned, BUT not all processing "
                   "(certain model inference; Quick Research web retrieval) is yet confirmed in-country. Where any "
                   "overseas processing occurs, TNB must take reasonable steps under APP 8.1 and update disclosures. "
                   "THIS IS THE PRINCIPAL OPEN PRIVACY ITEM.",
                   "APP 11 (security) - see the Information Security Assessment.",
                   "Automated decision-making transparency (APP amendments, commencing Dec 2026) - Quick Suite is "
                   "decision-SUPPORT, not automated decisioning, in Phase 3; must remain so under acceptable use."]),
            ("h", "3. Knowledge-graph and over-collection risk"),
            ("p", "The per-user knowledge graph learns from the content each user can access. Because Quick Index "
                  "honours source ACLs, the privacy exposure equals the user's EXISTING access. Where source "
                  "permissions are overly broad (stale SharePoint sharing, open Confluence spaces), Quick can make "
                  "that latent over-permissioning easy to surface. A pre-scale access review is recommended (see "
                  "the Information Security Assessment)."),
            ("h", "4. Residual privacy risk"),
            ("p", "MODERATE, reducible to LOW with: (a) APP 8 confirmation/disclosure; (b) completion of the "
                  "collection-notice / privacy-policy update; (c) the pre-scale access review. No high-risk "
                  "processing of customer PII is introduced in Phase 3."),
        ],
    )

    # 04 - Information security assessment -----------------------------------------
    build(
        os.path.join(ROOT, "04-information-security-assessment.pdf"), META, "04-information-security-assessment.pdf",
        "Information Security Assessment - Amazon Quick Suite",
        "Assessed against TNB Information Security Standard (aligned to APRA CPS 234)",
        CLASS,
        [
            ("h", "1. Controls assessed as PRESENT"),
            ("b", ["Identity: Entra ID SSO, MFA, conditional access, SCIM deprovisioning.",
                   "Encryption: AES-256 at rest (AWS KMS), TLS 1.2+ in transit.",
                   "Tenant isolation and AWS platform controls evidenced by third-party attestations (see the "
                   "Third-Party assessment).",
                   "Audit logging: prompts, retrieved context and outputs logged to the TNB tenancy and exported "
                   "to the SIEM.",
                   "Connector permission model honours source-system ACLs."]),
            ("h", "2. AI/agent-specific threats (partially addressed)"),
            ("b", ["Indirect prompt injection: malicious instructions embedded in indexed content (a document, "
                   "email or web page retrieved by Quick Research) could manipulate the assistant. Mitigations are "
                   "PARTIAL; a testing regime for injection resistance is not yet defined.",
                   "Data exfiltration via outputs: DLP is applied to connectors, but egress via generated outputs "
                   "and Quick Research prompts needs explicit monitoring.",
                   "Over-permissioned surfacing: see access-review recommendation below."]),
            ("h", "3. Recommended pre-scale actions"),
            ("b", ["Complete an access-review / least-privilege remediation of the most-shared SharePoint and "
                   "Confluence content BEFORE enterprise scale (reduces over-surfacing).",
                   "Define an AI-agent threat-testing regime (prompt-injection, tool/permission abuse) and add it "
                   "to the annual CPS 234 control-testing program.",
                   "Confirm SIEM coverage of Quick Suite audit events and set detections for anomalous bulk "
                   "retrieval."]),
            ("h", "4. Overall"),
            ("p", "The infrastructure and identity posture is STRONG and CPS 234-aligned. The residual is "
                  "concentrated in AI-agent-specific threats and pre-scale access hygiene - addressable via the "
                  "recommended actions rather than architectural change."),
        ],
    )

    # 05 - Third-party / material service provider assessment ----------------------
    build(
        os.path.join(ROOT, "05-third-party-material-service-provider-assessment.pdf"), META,
        "05-third-party-msp-assessment.pdf",
        "Third-Party / Material Service Provider Assessment - AWS (Amazon Quick Suite)",
        "Assessed against TNB Outsourcing & Third-Party policy (aligned to APRA CPS 230)",
        CLASS,
        [
            ("h", "1. Provider and attestations sighted"),
            ("p", "Provider: Amazon Web Services. TNB has an existing enterprise relationship with AWS. The "
                  "following current attestations were sighted for the underlying platform:"),
            ("b", ["SOC 1 / SOC 2 Type II report",
                   "ISO/IEC 27001, 27017, 27018 certifications",
                   "IRAP assessment (PROTECTED) for in-scope AWS services",
                   "PCI DSS attestation (platform)"]),
            ("h", "2. CPS 230 classification (OPEN)"),
            ("p", "AWS is an existing material service provider for other TNB workloads. Amazon Quick Suite as a "
                  "specific arrangement has NOT YET been formally classified and entered on the material service "
                  "provider register for this use, and a service-specific exit / substitutability assessment has "
                  "not been documented. This is an administrative-but-required CPS 230 step."),
            ("h", "3. Concentration and substitutability"),
            ("p", "Quick Suite deepens TNB's reliance on AWS across compute, data and now workplace productivity. "
                  "Because Phase 3 is a productivity/analytics layer (NOT a critical banking operation - see the "
                  "Operational Resilience assessment), the substitutability bar is lower than for a critical "
                  "operation, but an exit path (data export of knowledge-graph state and content, revert to prior "
                  "tooling) should be documented."),
            ("h", "4. Residual"),
            ("p", "MODERATE-LOW, subject to formal material-arrangement classification/registration and a "
                  "documented exit plan. No adverse findings on AWS platform controls."),
        ],
    )

    # 06 - AI / model governance assessment ---------------------------------------
    build(
        os.path.join(ROOT, "06-ai-model-governance-assessment.pdf"), META, "06-ai-model-governance-assessment.pdf",
        "AI and Model Governance Assessment - Amazon Quick Suite",
        "Model risk, agentic-action governance, human oversight and Responsible-AI alignment",
        CLASS,
        [
            ("h", "1. Nature of the AI"),
            ("p", "Quick Suite is built on vendor-managed foundation models plus retrieval over connected sources. "
                  "TNB does not train or own the models. Outputs are probabilistic and can be incorrect "
                  "('hallucination'). In Phase 3 the system is decision-SUPPORT for staff, not an automated "
                  "decision-maker, and does not act on customer accounts."),
            ("h", "2. Model governance (gaps)"),
            ("b", ["Vendor-managed models update frequently; there is currently NO TNB-side material-change "
                   "notification or re-validation trigger when the vendor changes an underlying model.",
                   "No TNB-owned evaluation of output quality/accuracy for TNB's high-value use cases; reliance is "
                   "on vendor evaluations and pilot user feedback.",
                   "Model-risk tiering: Quick Suite should be tiered under the TNB model-risk policy; a tiering "
                   "decision is outstanding."]),
            ("h", "3. Agentic-action governance"),
            ("p", "Phase 3 is read-only, which materially limits action risk. If WRITE-actions are enabled later, "
                  "an action-governance framework (explicit scopes, human approval for material actions, full audit, "
                  "blast-radius/rate limits, reversibility) MUST be designed and separately approved before enabling."),
            ("h", "4. Human oversight and acceptable use"),
            ("p", "The acceptable-use policy requires staff to treat outputs as drafts, verify facts, and NOT use "
                  "Quick Suite outputs as the basis for regulated financial advice or customer decisions without "
                  "human review. Enforcement/monitoring of this at 41k scale is unproven (see Conduct)."),
            ("h", "5. Responsible-AI alignment (self-assessment)"),
            ("b", ["Transparency: staff are informed AI is in use; customer-facing automated-decision use is "
                   "excluded in Phase 3.",
                   "Fairness: no customer-outcome decisioning in scope, so disparate-impact exposure is low in "
                   "Phase 3; would need testing if scope expands.",
                   "Reliability/safety: dependent on the model-governance gaps above.",
                   "Accountability: see the FAR mapping in the Conduct/People/Legal assessment."]),
        ],
    )

    # 07 - Operational resilience assessment --------------------------------------
    build(
        os.path.join(ROOT, "07-operational-resilience-assessment.pdf"), META, "07-operational-resilience-assessment.pdf",
        "Operational Resilience Assessment - Amazon Quick Suite",
        "Criticality, tolerance and continuity (aligned to APRA CPS 230)",
        CLASS,
        [
            ("h", "1. Criticality determination"),
            ("p", "Quick Suite is a staff PRODUCTIVITY and analytics layer. It does NOT process customer "
                  "transactions and is not in the delivery path of any critical banking operation (payments, "
                  "deposits, lending origination, markets). On the CPS 230 test it is assessed as NOT a critical "
                  "operation; an outage degrades staff productivity but does not disrupt services to customers."),
            ("h", "2. Availability and continuity"),
            ("b", ["Service availability is per the AWS Quick Suite SLA; TNB has no independent HA requirement "
                   "given non-critical status.",
                   "Continuity for staff: on outage, staff revert to prior tools (native M365 search, existing BI). "
                   "This fallback should be documented.",
                   "Data durability: knowledge-graph and content indices are recoverable from source systems (the "
                   "sources remain the systems of record)."]),
            ("h", "3. Dependency and tolerance"),
            ("p", "As a non-critical operation, a formal disruption tolerance is not mandated, but the productivity "
                  "dependency at 41k scale warrants a documented (light) continuity note and inclusion in the "
                  "technology service catalogue."),
            ("h", "4. Residual"),
            ("p", "LOW. Non-critical classification is well-supported by the scope. Document the staff-fallback "
                  "and register the service."),
        ],
    )

    # 08 - Conduct / people / legal assessment ------------------------------------
    build(
        os.path.join(ROOT, "08-conduct-people-legal-assessment.pdf"), META, "08-conduct-people-legal-assessment.pdf",
        "Conduct, People, Accountability and Legal Assessment - Amazon Quick Suite",
        "Conduct and consumer-outcome risk, workforce, FAR accountability, records and accounting",
        CLASS,
        [
            ("h", "1. Conduct and consumer outcomes"),
            ("p", "Quick Suite does not interact with customers in Phase 3. The conduct risk is INDIRECT: staff "
                  "could rely on an incorrect AI output when drafting customer communications or making internal "
                  "assessments. The acceptable-use policy addresses this, but monitoring adherence across ~41,000 "
                  "staff is unproven. Recommended: mandatory enablement training + periodic assurance sampling of "
                  "AI-assisted customer communications."),
            ("h", "2. Workforce and change"),
            ("b", ["Change/enablement program in place; role-based training modules built.",
                   "No workforce-reduction is predicated on this rollout in the current business case; productivity "
                   "gains are to be reinvested (per the sponsor).",
                   "Consultation obligations (where applicable) are being managed by People & Culture."]),
            ("h", "3. Accountability (FAR)"),
            ("p", "The COO is the business sponsor and the CRO is the accountable entity for the arrangement. "
                  "However, no SINGLE accountable person has been explicitly assigned for the AI/MODEL GOVERNANCE "
                  "of the tool across the enterprise (as distinct from the platform delivery). Recommended: assign "
                  "an accountable person for AI/model governance under the Financial Accountability Regime and "
                  "record it in the relevant accountability statement/map."),
            ("h", "4. Records management"),
            ("p", "AI-assisted drafts and prompts/outputs that relate to regulated matters must be captured under "
                  "TNB's records-management and CPS 220 record-keeping obligations. Retention and discoverability "
                  "of Quick Suite content for regulated communications is to be confirmed with Records & Legal."),
            ("h", "5. Accounting and tax (note)"),
            ("p", "Finance is confirming treatment of the SaaS subscription and configuration/integration costs "
                  "under AASB 138 (Intangibles) and the cloud-computing-arrangement guidance (customarily expensed "
                  "unless a separable intangible/software asset arises). No material tax issue identified; GST on "
                  "the imported service to be handled per standard practice. This is administrative."),
            ("h", "6. Summary of conditions recommended by first line"),
            ("b", ["Confirm APP 8 data-residency position and update disclosures.",
                   "Complete pre-scale access-review / least-privilege remediation.",
                   "Formally classify/register the AWS Quick Suite arrangement (CPS 230) + document exit.",
                   "Define model-material-change notification + model-risk tiering.",
                   "Do NOT enable agentic write-actions without a separate action-governance approval.",
                   "Assign a FAR-accountable person for AI/model governance.",
                   "Stand up conduct assurance sampling + mandatory enablement training."]),
        ],
    )


if __name__ == "__main__":
    gen()
    print("\nDone. Sample package written to samples/enterprise-risk-board/quick-suite-adoption/")
