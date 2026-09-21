#!/usr/bin/env python3
"""Generate the ai-governance-conformity sample document packages as PDFs.

Two packages, contrasting breadth:
  - au-loan-approval/  (BROAD): a company profile, a solution overview WITH an
    architecture diagram, and six supporting-evidence documents whose content
    EXPLICITLY evidences controls -- so the eco (with the evidence-discipline
    clause) marks many controls PRESENT and cites the document. Expected: AMBER.
  - eu-hiring-agent/   (THIN): a single solution brief that names the model and
    states, plainly, that no governance documentation exists. Expected: RED.

Run:  python3 scripts/gen_conformity_samples.py
Deps: fpdf2
"""
import os
from fpdf import FPDF

ROOT = os.path.join(os.path.dirname(__file__), "..", "samples", "ai-governance-conformity")
AU = os.path.join(ROOT, "au-loan-approval")
EU = os.path.join(ROOT, "eu-hiring-agent")

_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u20ac": "EUR ", "\u00d7": "x", "\u2265": ">=", "\u2264": "<=",
    "\u2192": "->", "\u2194": "<->", "\u2022": "-", "\u2026": "...", "\u00a0": " ",
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
        elif kind == "diagram" and diagram:
            diagram(pdf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf.output(path)
    print("wrote", os.path.relpath(path, os.path.join(os.path.dirname(__file__), "..")))


def au_architecture(pdf):
    """Box-and-arrow architecture diagram for the CreditFlow agent."""
    pdf.ln(2)
    x0, y0 = pdf.l_margin, pdf.get_y()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def box(x, y, w, h, label, fill=(235, 240, 250)):
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(90, 110, 150)
        pdf.rect(x, y, w, h, style="DF")
        pdf.set_xy(x, y + h / 2 - 4)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.multi_cell(w, 3.6, _s(label), align="C")

    def arrow(x1, y1, x2, y2):
        pdf.set_draw_color(120)
        pdf.line(x1, y1, x2, y2)

    row_y = y0 + 4
    bw, bh = 34, 16
    gap = (W - 4 * bw) / 3
    xs = [x0 + i * (bw + gap) for i in range(4)]
    box(xs[0], row_y, bw, bh, "Applicant\n(loan application)")
    box(xs[1], row_y, bw, bh, "CreditFlow Decisioning Agent\nLlama 3.1 70B + scorecard", fill=(225, 245, 235))
    box(xs[2], row_y, bw, bh, "Decision\nApprove / Decline / Refer")
    box(xs[3], row_y, bw, bh, "Credit Officer\nreviews ALL declines", fill=(250, 240, 225))
    for i in range(3):
        arrow(xs[i] + bw, row_y + bh / 2, xs[i + 1], row_y + bh / 2)

    tools_y = row_y + bh + 12
    pdf.set_xy(x0, tools_y - 6)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.cell(0, 4, _s("Agent tools (least-privilege, all calls logged):"), new_x="LMARGIN", new_y="NEXT")
    tools = ["Credit-bureau API", "Core-banking (read)", "Fraud check", "Document OCR", "Adverse-action letter"]
    tw = (W - (len(tools) - 1) * 4) / len(tools)
    for i, t in enumerate(tools):
        box(x0 + i * (tw + 4), tools_y, tw, 12, t, fill=(240, 240, 240))
    arrow(xs[1] + bw / 2, row_y + bh, xs[1] + bw / 2, tools_y)
    pdf.set_y(tools_y + 16)
    pdf.ln(2)


def gen_au():
    build(
        os.path.join(AU, "company-profile.pdf"),
        "CreditFlow Financial Group - Company Profile", "company-profile.pdf",
        "CreditFlow Financial Group", "Company & AI Governance Profile",
        "INTERNAL - provided for AI conformity assessment",
        [
            ("h", "Organisation"),
            ("p", "CreditFlow Financial Group is an Australian authorised deposit-taking "
                  "institution (ADI) regulated by APRA and ASIC, headquartered in Sydney, "
                  "with approximately 4,000 employees and 1.2 million retail customers. Its "
                  "principal activities are retail deposits and consumer lending (personal "
                  "loans and credit cards)."),
            ("h", "AI governance posture"),
            ("p", "An AI Governance Committee chaired by the Chief Risk Officer holds "
                  "board-delegated accountability for every AI system in production. The "
                  "committee maintains a documented AI policy, an AI system inventory, and "
                  "assigns a named accountable owner and a model risk rating to each system."),
            ("b", ["Accountability: the CRO is the accountable executive; each AI system has a named business owner.",
                   "Policies: an approved enterprise AI Policy and Responsible AI Standard apply to all AI systems.",
                   "Assurance: Internal Audit reviews high-materiality AI systems annually.",
                   "Jurisdictions: services offered in Australia only; no EU establishment or EU customers at this time."]),
            ("h", "Regulatory context"),
            ("p", "As an ADI, CreditFlow operates under APRA prudential standards (including "
                  "CPS 220 Risk Management and CPS 234 Information Security) and ASIC's "
                  "expectations for consumer credit. The Group aligns its AI governance to "
                  "Australia's Voluntary AI Safety Standard and is preparing for the proposed "
                  "Mandatory Guardrails for AI in high-risk settings."),
        ],
    )

    build(
        os.path.join(AU, "solution-overview.pdf"),
        "CreditFlow Decisioning Agent v2.0 - Solution Overview", "solution-overview.pdf",
        "CreditFlow Decisioning Agent v2.0", "Solution & Architecture Overview",
        "INTERNAL - provided for AI conformity assessment",
        [
            ("h", "Purpose"),
            ("p", "The CreditFlow Decisioning Agent is an agentic system that assesses "
                  "personal-loan and credit-card applications end to end: it gathers "
                  "applicant data, evaluates creditworthiness, and issues an approve, "
                  "decline, or refer decision. It processes roughly 120,000 applications "
                  "per month across digital and branch channels nationwide."),
            ("h", "Models and components"),
            ("b", ["Reasoning / orchestration and adverse-action letter drafting: Meta "
                   "Llama 3.1 70B-Instruct, self-hosted in CreditFlow's own VPC (model "
                   "repository meta-llama/Llama-3.1-70B-Instruct). No third-party model API "
                   "is used; the model runs on managed GPU infrastructure under CreditFlow's control.",
                   "Creditworthiness scoring: an in-house gradient-boosted scorecard trained "
                   "on CreditFlow's historical lending outcomes (2015-2025).",
                   "Tools: credit-bureau API, core-banking (read), fraud-check service, "
                   "document OCR, and adverse-action letter generation."]),
            ("h", "Autonomy and human control"),
            ("p", "The agent auto-approves applications up to AUD 50,000 where all policy and "
                  "fraud checks pass. Every decline and every referral requires human action: "
                  "the agent does not communicate a decline to a customer until a credit "
                  "officer has reviewed it (see the Human Oversight SOP)."),
            ("h", "Reference architecture"),
            ("diagram", None),
            ("p", "Deployment: Australia only. All model inference and applicant data remain "
                  "within CreditFlow's Australian cloud region."),
        ],
        diagram=au_architecture,
    )

    build(
        os.path.join(AU, "dpia.pdf"),
        "Data Protection Impact Assessment", "dpia.pdf",
        "Data Protection Impact Assessment (DPIA)", "CreditFlow Decisioning Agent v2.0",
        "CONFIDENTIAL - Privacy Office",
        [
            ("h", "Status"),
            ("p", "A Data Protection Impact Assessment was completed on 11 March 2026 in "
                  "accordance with the Privacy Act 1988 (Cth) and the Australian Privacy "
                  "Principles, and was reviewed and signed off by the CreditFlow Privacy "
                  "Officer. It is reviewed annually and on material change."),
            ("h", "Scope and findings"),
            ("b", ["Assesses automated decision-making in consumer lending and its effect on individuals.",
                   "Documents personal-information flows, purpose limitation, and data-minimisation measures.",
                   "Confirms a lawful basis for processing applicant data and for using historical lending outcomes as model training labels.",
                   "Records applicant recourse: individuals may request a review and an explanation of an adverse decision.",
                   "Residual privacy risk assessed as MEDIUM with agreed mitigations and a named owner."]),
            ("h", "Note"),
            ("p", "This DPIA covers privacy and data-protection risk. A separate fundamental-"
                  "rights assessment would be required only if the system were placed on the "
                  "EU market."),
        ],
    )

    build(
        os.path.join(AU, "model-risk-assessment.pdf"),
        "Model Risk Assessment", "model-risk-assessment.pdf",
        "Model Risk Assessment", "CreditFlow Decisioning Agent v2.0 (scorecard + LLM)",
        "CONFIDENTIAL - Model Risk Management",
        [
            ("h", "Risk management system"),
            ("p", "The system is governed by CreditFlow's Model Risk Management framework, "
                  "aligned to APRA CPS 220. A continuous, documented risk-management process "
                  "identifies, assesses, and mitigates model risks across the lifecycle, and "
                  "is re-run after every material change to the model or its data."),
            ("h", "Validation and monitoring"),
            ("b", ["The scorecard is independently validated at least annually by a team separate from development.",
                   "Performance, calibration, and input/population drift are monitored monthly against defined thresholds.",
                   "Accuracy and stability metrics are tracked per cohort and reported to the AI Governance Committee.",
                   "A documented fallback to manual underwriting is triggered automatically if drift exceeds the threshold."]),
            ("h", "Robustness and security"),
            ("p", "Adversarial and out-of-distribution testing is performed on the LLM "
                  "component, including prompt-injection tests on applicant-supplied "
                  "documents. The self-hosted model is version-pinned; no silent model swaps "
                  "occur. Security controls follow APRA CPS 234."),
        ],
    )

    build(
        os.path.join(AU, "bias-fairness-test-report.pdf"),
        "Bias & Fairness Test Report", "bias-fairness-test-report.pdf",
        "Bias & Fairness Test Report", "CreditFlow Decisioning Agent v2.0 - Q2 2026",
        "CONFIDENTIAL - Model Risk Management",
        [
            ("h", "Testing regime"),
            ("p", "Disparate-impact testing is performed quarterly across gender, age band, "
                  "and cultural/linguistic background using the four-fifths (80%) rule and "
                  "equal-opportunity difference. Testing is documented, repeatable, and owned "
                  "by Model Risk Management - it is not a one-off exercise."),
            ("h", "Most recent results (May 2026)"),
            ("b", ["Adverse-impact ratios ranged 0.86-0.97 across tested groups, above the 0.80 threshold.",
                   "Equal-opportunity difference within +/-0.04 for all protected groups tested.",
                   "Protected attributes are excluded from model features; known proxies (e.g. postcode) are monitored.",
                   "One borderline cohort (applicants aged 18-21) flagged for enhanced monitoring; remediation tracked to closure."]),
            ("h", "Governance"),
            ("p", "Results are reported to the AI Governance Committee. Any adverse-impact "
                  "ratio below 0.80 triggers mandatory remediation before continued use."),
        ],
    )

    build(
        os.path.join(AU, "human-oversight-sop.pdf"),
        "Human Oversight Standard Operating Procedure", "human-oversight-sop.pdf",
        "Human Oversight SOP", "CreditFlow Decisioning Agent v2.0",
        "INTERNAL - Lending Operations",
        [
            ("h", "Principle"),
            ("p", "No adverse credit decision is issued to a customer without a human "
                  "accountable owner. The system is designed so that a qualified person can "
                  "understand, override, and reverse its output."),
            ("h", "Mandatory review of declines"),
            ("b", ["Every declined application is independently reviewed by a qualified credit "
                   "officer within two (2) business days before the decline is communicated to the applicant.",
                   "Referrals are queued to a human underwriter; the agent never finalises a referral itself.",
                   "Auto-approvals up to AUD 50,000 are sampled at 10% for post-decision review.",
                   "Credit officers can override or reverse any agent output and must record a reason."]),
            ("h", "Anti-automation-bias measures"),
            ("b", ["The review UI surfaces the top decision factors and a confidence indicator, and withholds the agent's recommended action on low-confidence cases until the officer records an independent view.",
                   "Officers complete annual training on automation bias and on the limits of the model.",
                   "Review throughput is capacity-planned so reviewers are not overloaded."]),
        ],
    )

    build(
        os.path.join(AU, "data-governance-policy.pdf"),
        "Data Governance Policy", "data-governance-policy.pdf",
        "Data Governance Policy (extract)", "Applies to CreditFlow AI/ML systems",
        "INTERNAL - Data Office",
        [
            ("h", "Data quality and lineage"),
            ("p", "Training, validation, and test datasets are documented and version-"
                  "controlled. Training-data lineage is recorded in the enterprise feature "
                  "registry, including source, extraction date, and transformations, so any "
                  "model's data provenance can be reconstructed."),
            ("h", "Representativeness and bias examination"),
            ("b", ["Datasets are examined for representativeness and for possible bias before use in training.",
                   "Historical lending outcomes are used as labels under a documented lawful basis; known discriminatory patterns are tested for and mitigated.",
                   "Sensitive attributes are excluded from features and retained only for fairness monitoring."]),
            ("h", "Retention and consent"),
            ("p", "Personal data is retained per the Group Records Retention Schedule and the "
                  "Privacy Act; applicant consent and disclosures are captured at the point of "
                  "application."),
        ],
    )

    build(
        os.path.join(AU, "logging-monitoring-standard.pdf"),
        "Logging & Monitoring Standard", "logging-monitoring-standard.pdf",
        "Logging & Monitoring Standard", "CreditFlow Decisioning Agent v2.0",
        "INTERNAL - Technology Risk",
        [
            ("h", "Event logging"),
            ("p", "Every agent tool-call, model inference, decision, and human override is "
                  "logged immutably with a correlation id and retained for seven (7) years. "
                  "Logs are tamper-evident and access-controlled, and are sufficient to "
                  "reconstruct any individual decision after the fact."),
            ("h", "Monitoring and incident reporting"),
            ("b", ["Operational and model-performance metrics are monitored continuously with alerting to Technology Risk.",
                   "Post-market monitoring reviews are conducted quarterly by the AI Governance Committee.",
                   "Serious incidents are reported to the CRO and, where required, to regulators within 15 days.",
                   "A documented incident-response runbook covers model failure, drift breaches, and adversarial events."]),
        ],
    )


def gen_eu():
    build(
        os.path.join(EU, "solution-brief.pdf"),
        "TalentSift Copilot v3.2 - Solution Brief", "solution-brief.pdf",
        "TalentSift Copilot v3.2", "Solution Brief (for AI conformity review)",
        "COMMERCIAL IN CONFIDENCE",
        [
            ("h", "The company"),
            ("p", "TalentSift AB is a 40-person HR-technology SaaS vendor established in the "
                  "EU (Stockholm), serving approximately 400 enterprise customers across "
                  "Germany, France, the Netherlands, and Ireland. TalentSift is the provider "
                  "of the system; its enterprise customers are the deployers."),
            ("h", "What the product does"),
            ("p", "TalentSift Copilot is an agentic assistant that ingests job requisitions "
                  "and inbound CVs, autonomously screens and ranks candidates, drafts "
                  "shortlists with written justifications, and schedules interviews via "
                  "calendar and email tools with minimal recruiter intervention. It processes "
                  "about 50,000 applications per month."),
            ("h", "Models and autonomy"),
            ("b", ["Built on Anthropic Claude (accessed via Amazon Bedrock) for reasoning and "
                   "text generation, plus an in-house fine-tuned scoring model trained on "
                   "customers' historical hiring outcomes.",
                   "The agent auto-rejects candidates scoring below a threshold and auto-"
                   "advances the top N. In practice, approximately 85% of below-threshold "
                   "rejections are never individually reviewed by a recruiter.",
                   "Tools: CV parser, applicant-tracking-system read/write, calendar, and autonomous email send.",
                   "Deployment: EU (DE, FR, NL, IE)."]),
            ("h", "Existing documentation"),
            ("p", "The only existing governance documentation is a marketing 'AI fact sheet' "
                  "and a SOC 2 Type II report. There is no EU AI Act technical documentation, "
                  "no fundamental-rights impact assessment, no DPIA, no model card, no "
                  "documented bias/fairness testing beyond an undocumented 2024 spot-check, "
                  "no defined human-oversight procedure, and no logging specification."),
        ],
    )


if __name__ == "__main__":
    gen_au()
    gen_eu()
    print("done")
