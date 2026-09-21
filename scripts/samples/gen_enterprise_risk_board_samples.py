#!/usr/bin/env python3
"""Generate the enterprise-risk-board sample document package as PDFs.

Scenario (fictional): Meridian Mutual Bank - an APRA-regulated ADI - seeks Risk &
Compliance Committee approval to deploy "Aria", an AGENTIC customer-servicing
assistant that reads customer PII and can AUTONOMOUSLY MOVE MONEY (fee reversals,
refunds, and payments/transfers up to a limit), built on a US-hosted third-party
foundation model.

These are LARGE, COMPLEX, MULTI-PAGE board-governance documents - the kind a Chief
Risk Officer actually receives - so the demo shows the platform ingesting real,
dense evidence, not one-pagers. The deliberately-planted signal is preserved EXACTLY
and buried inside realistic connective tissue (document control, glossaries, control
matrices, appendices) so the eco has to FIND it:

  STRONG, EVIDENCED controls (lenses mark PRESENT, cited to a doc):
    - CPS 234-aligned information security (auth, encryption, immutable logging)
    - A completed DPIA covering collection + security of personal information
    - Third-party due diligence performed (SOC 2 Type II sighted)

  DELIBERATE GAPS (the challenger + lenses catch these, grounded in the catalogs):
    - FCC (the standout BLOCKER): the agent can initiate payments/refunds but NO
      document describes AML/CTF transaction monitoring, an SMR pathway, or
      sanctions screening for Aria-initiated money movement.
    - Privacy APP 8: customer PII is disclosed to a US-hosted vendor, but the DPIA
      explicitly defers overseas-disclosure assessment "to a later phase".
    - Privacy APP 1: automated-decision-making transparency is out of scope.
    - Regulatory / 3rd-party: the US vendor is NOT yet classified as a CPS 230
      material service provider; no exit / business-continuity plan.
    - Model: reliance on the vendor's own evaluations; no Meridian-owned independent
      validation or drift monitoring.
    - Cyber (agentic): strong perimeter security, but prompt-injection / tool-
      permission abuse of the payment tool is not addressed.
    - Conduct (catalog-gap, reasoning-only): ~70% of interactions are fully
      automated with no routine human review - unfair-outcome / consumer exposure.
    - FAR accountability ORPHAN: the paper names a business owner (Head of Digital)
      and the CRO as accountable entity, but NO accountable person for MODEL RISK
      or FINANCIAL CRIME for this system.

  Expected verdict: GO-WITH-CONDITIONS (arguably NO-GO on the AML blocker), with
  named conditions precedent + owners and the orphan-accountability flag.

Run:  python3 scripts/gen_enterprise_risk_board_samples.py
Deps: fpdf2
"""
import os
from fpdf import FPDF
from fpdf.fonts import FontFace

ROOT = os.path.join(os.path.dirname(__file__), "..", "samples", "enterprise-risk-board",
                    "agentic-servicing-assistant")

_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u20ac": "EUR ", "\u00d7": "x", "\u2265": ">=", "\u2264": "<=",
    "\u2192": "->", "\u2194": "<->", "\u2022": "-", "\u2026": "...", "\u00a0": " ",
    "\u2248": "~", "\u00a3": "GBP ", "\u2122": "(TM)", "\u00ae": "(R)", "\u00b1": "+/-",
    "\u00bd": "1/2", "\u00e9": "e", "\u00a7": "S",
}

# Brand palette
NAVY = (32, 54, 92)
STEEL = (60, 80, 120)
GREY = (90, 90, 90)


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
            self.set_draw_color(210)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(140)
        cls = getattr(self, "_classification", "")
        self.cell(0, 6, _s(f"{getattr(self, '_docref', '')}   |   {cls}   |   page "
                           f"{self.page_no()}/{{nb}}"), align="C")
        self.set_text_color(0)


def _title(pdf, title, subtitle, classification):
    pdf.set_fill_color(*NAVY)
    pdf.rect(pdf.l_margin, pdf.get_y(), pdf.w - pdf.l_margin - pdf.r_margin, 1.6, style="F")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 19)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 8, _s(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 11.5)
    pdf.set_text_color(90)
    pdf.multi_cell(0, 6, _s(subtitle), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(150)
    pdf.multi_cell(0, 5, _s(classification), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(2)
    pdf.set_draw_color(200)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)


def _h(pdf, text):
    if pdf.get_y() > pdf.page_break_trigger - 24:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(0, 6.5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_draw_color(210)
    pdf.line(pdf.l_margin, pdf.get_y() + 0.5, pdf.w - pdf.r_margin, pdf.get_y() + 0.5)
    pdf.ln(2.5)


def _h3(pdf, text):
    if pdf.get_y() > pdf.page_break_trigger - 18:
        pdf.add_page()
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*STEEL)
    pdf.multi_cell(0, 5.5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(0.5)


def _p(pdf, text):
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.4)


def _bullets(pdf, items):
    pdf.set_font("Helvetica", "", 10)
    for it in items:
        y = pdf.get_y()
        if y > pdf.page_break_trigger - 8:
            pdf.add_page()
        pdf.set_x(pdf.l_margin + 2)
        pdf.multi_cell(0, 5, _s("-  " + it), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.4)


def _numbered(pdf, items):
    pdf.set_font("Helvetica", "", 10)
    for i, it in enumerate(items, 1):
        if pdf.get_y() > pdf.page_break_trigger - 8:
            pdf.add_page()
        pdf.set_x(pdf.l_margin + 2)
        pdf.multi_cell(0, 5, _s(f"{i}.  " + it), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.4)


def _note(pdf, text):
    if pdf.get_y() > pdf.page_break_trigger - 20:
        pdf.add_page()
    pdf.ln(1)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_fill_color(247, 244, 230)
    pdf.set_draw_color(200, 185, 120)
    pdf.set_text_color(70, 60, 20)
    pdf.multi_cell(0, 4.8, _s(text), border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1.6)


def _table(pdf, headers, rows, widths=None, fs=8.7, align="LEFT"):
    if pdf.get_y() > pdf.page_break_trigger - 22:
        pdf.add_page()
    pdf.set_font("Helvetica", "", fs)
    head = FontFace(emphasis="BOLD", color=255, fill_color=STEEL)
    with pdf.table(col_widths=widths, headings_style=head, line_height=fs * 0.52 + 1.6,
                   text_align=align, borders_layout="ALL", cell_fill_color=(244, 246, 250),
                   cell_fill_mode="ROWS") as t:
        hr = t.row()
        for h in headers:
            hr.cell(_s(str(h)))
        for row in rows:
            r = t.row()
            for c in row:
                r.cell(_s(str(c)))
    pdf.ln(2)


def _kvtable(pdf, pairs, widths=(1, 2.4), fs=9):
    if pdf.get_y() > pdf.page_break_trigger - 20:
        pdf.add_page()
    pdf.set_font("Helvetica", "", fs)
    bold = FontFace(emphasis="BOLD", fill_color=(238, 241, 247))
    with pdf.table(col_widths=widths, first_row_as_headings=False,
                   line_height=fs * 0.52 + 1.6, borders_layout="ALL") as t:
        for k, v in pairs:
            r = t.row()
            r.cell(_s(str(k)), style=bold)
            r.cell(_s(str(v)))
    pdf.ln(2)


def build(path, meta, docref, classification, title, subtitle, blocks, diagram=None):
    pdf = Doc()
    pdf._meta = meta
    pdf._docref = docref
    pdf._classification = classification
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(18, 14, 18)
    pdf.add_page()
    pdf.alias_nb_pages()
    _title(pdf, title, subtitle, classification)
    for b in blocks:
        kind = b[0]
        if kind == "h":
            _h(pdf, b[1])
        elif kind == "h3":
            _h3(pdf, b[1])
        elif kind == "p":
            _p(pdf, b[1])
        elif kind == "b":
            _bullets(pdf, b[1])
        elif kind == "num":
            _numbered(pdf, b[1])
        elif kind == "docctl":
            _kvtable(pdf, b[1])
        elif kind == "table":
            _table(pdf, b[1], b[2], b[3] if len(b) > 3 else None)
        elif kind == "note":
            _note(pdf, b[1])
        elif kind == "spacer":
            pdf.ln(b[1])
        elif kind == "pagebreak":
            pdf.add_page()
        elif kind == "diagram" and diagram:
            diagram(pdf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf.output(path)
    npages = pdf.page_no()
    print(f"wrote {os.path.relpath(path, os.path.join(os.path.dirname(__file__), '..'))}  ({npages} pages)")


def aria_architecture(pdf):
    """Box-and-arrow architecture for the Aria agentic servicing assistant."""
    if pdf.get_y() > pdf.page_break_trigger - 70:
        pdf.add_page()
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
    bw, bh = 40, 16
    gap = (W - 3 * bw) / 2
    xs = [x0 + i * (bw + gap) for i in range(3)]
    box(xs[0], row_y, bw, bh, "Customer\n(chat / app)")
    box(xs[1], row_y, bw, bh, "Aria Servicing Agent\norchestration + tools", fill=(225, 245, 235))
    box(xs[2], row_y, bw, bh, "Nimbus AI (foundation model)\nUS-hosted - customer data leaves AU", fill=(250, 232, 232))
    for i in range(2):
        arrow(xs[i] + bw, row_y + bh / 2, xs[i + 1], row_y + bh / 2)

    tools_y = row_y + bh + 12
    pdf.set_xy(x0, tools_y - 6)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.cell(0, 4, _s("Aria tools (act on the customer's account):"), new_x="LMARGIN", new_y="NEXT")
    tools = ["Read account + PII", "Reverse fee / refund <= $500", "Initiate payment / transfer <= $5,000", "Update contact details"]
    tw = (W - (len(tools) - 1) * 4) / len(tools)
    for i, t in enumerate(tools):
        fill = (250, 232, 232) if ("payment" in t or "refund" in t) else (240, 240, 240)
        box(x0 + i * (tw + 4), tools_y, tw, 14, t, fill=fill)
    arrow(xs[1] + bw / 2, row_y + bh, xs[1] + bw / 2, tools_y)
    pdf.set_y(tools_y + 18)
    pdf.ln(2)


def gen_01_board_paper():
    build(
        os.path.join(ROOT, "01-board-paper.pdf"),
        "Meridian Mutual Bank - Risk & Compliance Committee",
        "MMB-RCC-2026-06", "CONFIDENTIAL - Board Risk & Compliance Committee",
        "Risk & Compliance Committee Paper",
        "Agenda Item 6 - Production deployment of 'Aria', an agentic customer-servicing assistant",
        [
            ("docctl", [
                ("Paper reference", "MMB-RCC-2026-06 / Agenda Item 6"),
                ("Committee", "Board Risk & Compliance Committee (RCC)"),
                ("Meeting date", "21 July 2026"),
                ("Classification", "CONFIDENTIAL - Board and invitees only"),
                ("Author", "Head of Digital (Business Owner, Aria)"),
                ("Co-author", "Program Director, Customer Servicing Transformation"),
                ("Sponsor", "Chief Operating Officer"),
                ("Decision type", "Approval - production deployment"),
                ("Prepared", "9 July 2026"),
                ("Distribution", "RCC members; CRO; CCO; CFO; Company Secretary; RCC invitees"),
            ]),
            ("h", "Document control and version history"),
            ("table",
             ["Ver.", "Date", "Author", "Summary of change"],
             [["0.1", "12 Jun 2026", "Program Director", "Initial draft for management review"],
              ["0.4", "26 Jun 2026", "Head of Digital", "Incorporated pilot results; benefits case updated"],
              ["0.7", "3 Jul 2026", "Risk Business Partner", "Added risk summary and regulatory considerations"],
              ["0.9", "9 Jul 2026", "Head of Digital", "Executive Committee review comments incorporated"],
              ["1.0", "9 Jul 2026", "Head of Digital", "Released to RCC for the 21 July 2026 meeting"]],
             (0.7, 1.3, 2.0, 4.2)),

            ("h", "1. Decision sought"),
            ("p", "The Committee is asked to APPROVE the production deployment of Aria, an "
                  "agentic AI customer-servicing assistant, to all retail customers, "
                  "following a successful 3-month limited pilot. Management recommends "
                  "approval to proceed to full rollout in Q4 2026, subject to the standard "
                  "post-implementation review at 90 days."),
            ("p", "This is a decision paper (not a paper for noting). Management seeks a "
                  "resolution of the Committee in the terms set out in Section 12."),

            ("h", "2. Purpose and context"),
            ("p", "Customer servicing volumes have grown 14% year-on-year while cost-to-serve "
                  "has remained under pressure. The Bank's FY26-28 strategy commits to "
                  "'digital-first, human-when-it-matters' servicing. Aria is the flagship "
                  "initiative under that strategy and the first agentic AI system proposed "
                  "for production use at Meridian that can take actions on customer accounts "
                  "rather than only providing information."),
            ("p", "This paper consolidates the business case, the pilot outcomes, and the "
                  "supporting risk and control assessments (Attachments 02-07) so the "
                  "Committee can interrogate the proposal and exercise its challenge role "
                  "before a production decision."),

            ("h", "3. Background - what Aria is"),
            ("p", "Aria handles inbound customer servicing across web chat and the mobile "
                  "app. Beyond answering questions, Aria can ACT on the customer's account. "
                  "In its current design it can: reverse fees and issue refunds up to "
                  "AUD 500; initiate payments and transfers up to AUD 5,000; and update "
                  "customer contact details. In the pilot, approximately 70% of interactions "
                  "were handled end to end with no human involvement. Aria is built on the "
                  "Nimbus AI foundation model, a hosted third-party large language model. "
                  "The solution design is at Attachment 03."),
            ("h3", "3.1 Why agentic (rather than an information-only assistant)"),
            ("p", "An information-only assistant still requires a human agent to complete the "
                  "customer's request, retaining the cost and delay the initiative is meant "
                  "to remove. The benefits case depends materially on autonomous completion "
                  "of routine, low-value servicing actions."),

            ("h", "4. Pilot summary and results"),
            ("p", "A limited production pilot ran from 7 April 2026 to 30 June 2026, covering "
                  "~18,000 customers who opted in, across a constrained set of servicing "
                  "intents. Results against the pre-agreed success criteria:"),
            ("table",
             ["Metric", "Target", "Pilot actual", "Status"],
             [["Automated end-to-end resolution rate", ">= 60%", "70.4%", "Met"],
              ["Customer satisfaction (CSAT)", ">= 4.955", "4.6 / 5.0", "Met"],
              ["Average handling time (automated)", "< 3 min", "1 min 48 s", "Met"],
              ["Complaints attributable to Aria", "< 0.5%", "0.7%", "Slightly over"],
              ["Erroneous money-movement actions", "0", "3 (all remediated)", "Watch"],
              ["Escalation-to-human success", ">= 95%", "96.2%", "Met"]],
             (3.0, 1.4, 1.7, 1.1)),
            ("note", "Of the 3 erroneous money-movement actions, 2 were duplicate transfers "
                     "arising from a client retry, and 1 was a refund issued against the "
                     "wrong transaction. All were detected on customer contact and reversed. "
                     "Management assesses the control environment as adequate; the Committee "
                     "may wish to test this."),

            ("h", "5. Strategic alignment and expected benefits"),
            ("b", ["Around 40% reduction in contact-centre cost to serve for in-scope intents.",
                   "24/7 servicing and faster resolution for routine requests.",
                   "Deflection of an estimated 60% of Tier-1 servicing volume.",
                   "Redeployment of ~55 FTE from routine handling to complex and vulnerable-"
                   "customer support (subject to the People plan, Section 9)."]),
            ("h3", "5.1 Indicative financials (AUD, 3-year)"),
            ("table",
             ["Line", "Year 1", "Year 2", "Year 3"],
             [["Gross servicing cost saving", "2.1m", "4.8m", "5.3m"],
              ["Vendor / model API cost", "(0.9m)", "(1.3m)", "(1.4m)"],
              ["Run, support and assurance", "(0.6m)", "(0.7m)", "(0.7m)"],
              ["Net benefit", "0.6m", "2.8m", "3.2m"]],
             (3.0, 1.4, 1.4, 1.4)),
            ("p", "The benefits case assumes full rollout from Q4 2026 and is sensitive to "
                  "the automated-resolution rate; a 10 percentage-point reduction removes "
                  "approximately 30% of the Year-2 net benefit."),

            ("h", "6. Scope of this decision"),
            ("h3", "6.1 In scope"),
            ("b", ["Retail transaction, savings and personal-lending servicing intents.",
                   "Autonomous fee reversal / refund up to AUD 500.",
                   "Autonomous payment / transfer initiation up to AUD 5,000.",
                   "Contact-detail updates."]),
            ("h3", "6.2 Out of scope (this decision)"),
            ("b", ["Business and corporate customers.",
                   "Lending decisions, hardship determinations, and complaints handling.",
                   "Any increase to the money-movement thresholds (would return to Committee)."]),

            ("h", "7. Risk summary (management view)"),
            ("p", "Management's summary of the principal risks and its assessment of residual "
                  "risk after mitigation. Detailed assessments are at Attachments 04-07."),
            ("table",
             ["#", "Risk", "Inherent", "Mitigation (summary)", "Residual"],
             [["R1", "Privacy - handling of customer personal information", "High",
               "DPIA completed (Att. 04); encryption and access control", "Medium"],
              ["R2", "Information security of the platform", "High",
               "CPS 234-aligned controls (Att. 06)", "Low-Med"],
              ["R3", "Third-party / model vendor dependence", "High",
               "Vendor due diligence performed (Att. 07)", "Medium"],
              ["R4", "Model performance / accuracy", "Medium",
               "Vendor evaluations relied upon; pilot monitoring", "Medium"],
              ["R5", "Incorrect autonomous action", "Medium",
               "Value thresholds; policy checks; confirmation to customer", "Medium"],
              ["R6", "Customer / conduct outcomes", "Medium",
               "CSAT monitoring; escalation path", "Medium"]],
             (0.5, 2.3, 1.0, 3.0, 1.0)),
            ("note", "Management representation: the residual risk is considered acceptable "
                     "for production and the control environment adequate. The Committee's "
                     "challenge and decision are sought. (Note: this management summary does "
                     "not separately rate financial-crime risk arising from Aria-initiated "
                     "money movement; see Section 8 and Attachments.)"),

            ("h", "8. Regulatory considerations"),
            ("p", "Management has identified the following as the principal regulatory "
                  "instruments engaged by the initiative:"),
            ("table",
             ["Instrument", "Regulator", "Relevance to Aria"],
             [["Privacy Act 1988 / APPs", "OAIC", "Handling and disclosure of customer PI"],
              ["APRA CPS 234", "APRA", "Information security of the platform"],
              ["APRA CPS 230", "APRA", "Operational risk; material service providers"],
              ["APRA CPS 220", "APRA", "Risk management framework; model risk"],
              ["Financial Accountability Regime", "APRA/ASIC", "Accountable person obligations"],
              ["AML/CTF Act 2006", "AUSTRAC", "Applies to the Bank as a reporting entity"]],
             (1.7, 1.1, 3.4)),
            ("p", "The Bank maintains an AML/CTF program and transaction-monitoring and "
                  "suspicious-matter-reporting processes across its existing payment "
                  "channels (see Attachment 02). The application of these processes to "
                  "Aria-initiated transactions is a matter for the solution and control "
                  "documentation and is not separately addressed in this paper."),

            ("h", "9. People and change impact"),
            ("p", "Full rollout affects approximately 55 contact-centre roles. A consultation "
                  "and redeployment plan is being prepared with People & Culture; no "
                  "decision on roles is sought in this paper. Frontline staff will retain an "
                  "override capability for escalated interactions."),

            ("h", "10. Accountability"),
            ("p", "The accountable entity is Meridian Mutual Bank; the Chief Risk Officer is "
                  "the accountable executive for risk. The business owner for Aria is the "
                  "Head of Digital, who sponsors this paper. No separate accountable person "
                  "is nominated in this paper for model risk or for financial-crime risk "
                  "arising from Aria's payment capability."),

            ("h", "11. Implementation and rollout plan"),
            ("table",
             ["Stage", "Timing", "Description"],
             [["Approval", "Jul 2026", "RCC decision (this paper)"],
              ["Readiness", "Aug 2026", "Production hardening; runbooks; staff training"],
              ["Wave 1", "Sep 2026", "25% of eligible customers"],
              ["Wave 2", "Oct 2026", "60% of eligible customers"],
              ["Full rollout", "Q4 2026", "All eligible retail customers"],
              ["PIR", "Q1 2027", "90-day post-implementation review to RCC"]],
             (1.2, 1.1, 3.7)),

            ("h", "12. Recommendation and resolution sought"),
            ("p", "That the Board Risk & Compliance Committee RESOLVES to approve the "
                  "production deployment of Aria to all eligible retail customers, on the "
                  "terms described in this paper, with full rollout in Q4 2026 and a 90-day "
                  "post-implementation review to the Committee."),

            ("h", "Appendix A - Options considered"),
            ("table",
             ["Option", "Description", "Assessment"],
             [["A", "Do nothing / status quo", "Rejected - benefits case unmet; strategy commitment unmet"],
              ["B", "Information-only assistant (no actions)", "Rejected - retains cost; benefits case unmet"],
              ["C", "Agentic assistant with autonomous actions (recommended)", "Recommended - meets benefits case"],
              ["D", "Agentic assistant, human approves every action", "Deferred - erodes ~65% of benefit"]],
             (0.6, 3.0, 3.4)),
            ("h", "Appendix B - Attachments provided to the Committee"),
            ("num", ["02 - Company & AI Governance Profile",
                     "03 - Aria Solution Design (architecture, tools, autonomy, data flows)",
                     "04 - Data Protection Impact Assessment (DPIA)",
                     "05 - Nimbus AI Vendor Model Card",
                     "06 - Information Security Standard (Aria)",
                     "07 - Third-Party Risk Assessment (Nimbus AI)"]),
        ],
    )


def gen_02_company_profile():
    build(
        os.path.join(ROOT, "02-company-profile.pdf"),
        "Meridian Mutual Bank - Company & AI Governance Profile",
        "MMB-PROF-2026", "INTERNAL - provided for risk interrogation",
        "Meridian Mutual Bank",
        "Company & AI Governance Profile",
        [
            ("docctl", [
                ("Document", "Company & AI Governance Profile"),
                ("Owner", "Company Secretariat / Enterprise Risk"),
                ("Classification", "INTERNAL"),
                ("Version", "2026.2"),
                ("Last reviewed", "30 June 2026"),
                ("Purpose", "Baseline context for governance and risk assessments"),
            ]),

            ("h", "1. Organisation overview"),
            ("p", "Meridian Mutual Bank (MMB, 'the Bank') is an Australian authorised "
                  "deposit-taking institution (ADI). It is a customer-owned mutual, "
                  "established in 1968 from the merger of two regional credit "
                  "co-operatives, and is headquartered in Melbourne with a distribution "
                  "footprint across Victoria, New South Wales and South Australia."),
            ("table",
             ["Attribute", "Detail"],
             [["Legal form", "Mutual ADI (customer-owned)"],
              ["Members / customers", "~350,000"],
              ["Total assets", "~AUD 9.4 billion"],
              ["Employees", "~1,450 FTE"],
              ["Branches / service centres", "34"],
              ["Primary products", "Transaction & savings accounts; personal & home lending; payments; term deposits"],
              ["Geographic scope", "Australia only"]],
             (1.4, 4.6)),

            ("h", "2. Legal and regulatory status"),
            ("b", ["Authorised deposit-taking institution under the Banking Act 1959; "
                   "prudentially regulated by APRA.",
                   "Australian Financial Services and Australian Credit Licence holder; "
                   "regulated by ASIC for conduct and disclosure.",
                   "Reporting entity under the Anti-Money Laundering and Counter-Terrorism "
                   "Financing Act 2006; enrolled with AUSTRAC.",
                   "Accountable entity under the Financial Accountability Regime (FAR).",
                   "Subject to the Privacy Act 1988 (Cth) and the Australian Privacy "
                   "Principles; regulated by the OAIC."]),
            ("h3", "2.1 Prudential standards in scope (selected)"),
            ("table",
             ["Standard", "Title", "Applicability"],
             [["CPS 220", "Risk Management", "Enterprise RMF and model risk"],
              ["CPS 230", "Operational Risk Management", "Operational risk; service provider management; BCP"],
              ["CPS 234", "Information Security", "Information security capability and incident notification"],
              ["CPS 511", "Remuneration", "Remuneration and accountability"],
              ["CPG 235", "Managing Data Risk", "Data quality and lifecycle (guidance)"]],
             (0.9, 2.1, 3.0)),

            ("h", "3. Governance structure"),
            ("p", "The Board holds ultimate responsibility for the Bank's risk management. "
                  "It is supported by the Board Risk & Compliance Committee (RCC), the Board "
                  "Audit Committee, and the Board Remuneration & Nominations Committee. "
                  "Management operates a three-lines-of-defence model."),
            ("table",
             ["Line", "Function", "Responsibility"],
             [["1st", "Business units", "Own and manage risk in the activities they run"],
              ["2nd", "Risk & Compliance", "Set frameworks; oversee and challenge 1st line"],
              ["3rd", "Internal Audit", "Independent assurance to the Board Audit Committee"]],
             (0.8, 1.7, 3.5)),
            ("h3", "3.1 Executive risk committees"),
            ("b", ["Executive Risk Committee (ERC) - chaired by the CRO.",
                   "Operational Risk & Compliance Committee (ORCC).",
                   "Asset & Liability Committee (ALCO).",
                   "AI Governance Committee (see Section 4)."]),

            ("h", "4. AI governance"),
            ("p", "The Bank established an AI Governance Committee (AIGC) in 2025, chaired by "
                  "the Chief Risk Officer, with board-delegated oversight of AI systems. The "
                  "AIGC maintains an AI system inventory, approves AI systems into defined "
                  "risk tiers, and reports to the RCC."),
            ("b", ["An enterprise AI Policy sets principles for the responsible use of AI, "
                   "aligned to Australia's AI Ethics Principles.",
                   "A Model Risk Management framework (aligned to APRA CPS 220) applies to "
                   "material models across their lifecycle.",
                   "An AI risk-tiering standard classifies systems as low / medium / high / "
                   "unacceptable based on autonomy, impact and data sensitivity.",
                   "High-tier systems require independent validation prior to production."]),
            ("note", "Aria is classified by management as a high-tier AI system on the AI "
                     "inventory (autonomous action + customer PII + external model). The "
                     "independent-validation expectation for high-tier systems is addressed "
                     "- or not - in the attached model and control documentation."),

            ("h", "5. Risk management framework"),
            ("p", "The Bank operates an enterprise Risk Management Framework (RMF) approved "
                  "by the Board, with a Risk Appetite Statement (RAS) that sets qualitative "
                  "and quantitative appetite across financial and non-financial risk. The "
                  "non-financial risk taxonomy covers operational, compliance, conduct, "
                  "technology, cyber, data, privacy, third-party, financial-crime, model, "
                  "people and legal risk."),
            ("h3", "5.1 Risk appetite (selected non-financial statements)"),
            ("b", ["No appetite for breaches of privacy law or unauthorised disclosure of "
                   "customer information.",
                   "No appetite for systemic or deliberate breaches of AML/CTF obligations.",
                   "Low appetite for customer harm arising from automated decisions or "
                   "actions.",
                   "Low appetite for critical-service disruption arising from third-party "
                   "dependence."]),

            ("h", "6. Financial crime program"),
            ("p", "The Bank maintains an AML/CTF Program (Parts A and B) overseen by the "
                  "Money Laundering Reporting Officer (MLRO), who reports to the CRO. The "
                  "program includes an ML/TF risk assessment, an applicable customer "
                  "identification procedure (KYC/CDD), ongoing customer due diligence, "
                  "transaction monitoring, suspicious-matter reporting (SMR), threshold "
                  "transaction reporting (TTR), and sanctions screening."),
            ("p", "These controls currently operate across the Bank's ESTABLISHED payment "
                  "and onboarding channels (branch, online banking, card and direct-entry / "
                  "NPP rails). Monitoring rules and typologies are calibrated to those "
                  "channels and customer-initiated transactions."),
            ("note", "The extension of transaction monitoring, the SMR pathway, and "
                     "sanctions screening to money movement INITIATED BY Aria (an automated "
                     "agent) is a matter for the Aria solution and control documentation and "
                     "is not described in this profile. Reviewers should confirm coverage in "
                     "the attached documents."),

            ("h", "7. Data governance and privacy"),
            ("p", "A Chief Data Officer owns the data governance framework; a Privacy Office "
                  "(led by the Privacy Officer) owns privacy compliance, the APP program, "
                  "privacy impact assessments, and the Notifiable Data Breaches response. "
                  "Customer data is hosted primarily in Australian cloud regions; cross-"
                  "border arrangements require an APP 8 assessment."),

            ("h", "8. Third-party and outsourcing management"),
            ("p", "A Vendor Risk Management framework governs third-party arrangements. "
                  "Under APRA CPS 230, arrangements supporting critical operations must be "
                  "classified for materiality, recorded in a service-provider register, and "
                  "supported by tested business-continuity and exit arrangements. New "
                  "material arrangements require Executive Risk Committee endorsement."),

            ("h", "Glossary"),
            ("table",
             ["Term", "Meaning"],
             [["ADI", "Authorised Deposit-taking Institution"],
              ["AIGC", "AI Governance Committee"],
              ["APP", "Australian Privacy Principle"],
              ["CDD", "Customer Due Diligence"],
              ["FAR", "Financial Accountability Regime"],
              ["MLRO", "Money Laundering Reporting Officer"],
              ["RAS", "Risk Appetite Statement"],
              ["SMR", "Suspicious Matter Report"],
              ["TTR", "Threshold Transaction Report"]],
             (1.0, 5.0)),
        ],
    )


def gen_03_solution_design():
    build(
        os.path.join(ROOT, "03-solution-design.pdf"),
        "Aria Servicing Assistant v1.0 - Solution Design",
        "MMB-SD-ARIA-1.0", "INTERNAL - Technology & Digital",
        "Aria Servicing Assistant v1.0",
        "Solution Design - architecture, tools, autonomy and data flows",
        [
            ("docctl", [
                ("Document", "Aria Servicing Assistant - Solution Design"),
                ("Version", "1.0 (production candidate)"),
                ("Owner", "Solution Architecture, Technology & Digital"),
                ("Contributors", "Platform Engineering; Data; Security Architecture"),
                ("Classification", "INTERNAL"),
                ("Status", "Issued for RCC review"),
                ("Date", "5 July 2026"),
            ]),
            ("h", "1. Overview and objectives"),
            ("p", "Aria is an agentic assistant that resolves customer servicing requests "
                  "end to end. It interprets the request, retrieves account and customer "
                  "data, decides on an action, executes it using its tools, and confirms "
                  "with the customer. It currently handles approximately 180,000 "
                  "interactions per month across web chat and the mobile app."),
            ("b", ["Resolve routine servicing intents without human involvement where policy "
                   "permits.",
                   "Take actions on the customer's account (not merely provide information).",
                   "Escalate to a human agent when confidence is low or thresholds are "
                   "exceeded."]),
            ("h", "2. Scope"),
            ("h3", "2.1 In-scope servicing intents"),
            ("b", ["Balance, transaction and statement enquiries.",
                   "Fee reversal and refund (<= AUD 500).",
                   "Payment and transfer initiation (<= AUD 5,000).",
                   "Contact-detail updates (address, phone, email).",
                   "Card management (temporary lock/unlock, re-issue request)."]),
            ("h3", "2.2 Out of scope"),
            ("b", ["Lending, hardship and complaints decisions.",
                   "Account opening / closure.",
                   "Business and corporate customers."]),

            ("h", "3. Reference architecture"),
            ("diagram", None),
            ("p", "The customer interacts with Aria via the existing web-chat and mobile "
                  "channels. The Aria orchestration layer (hosted in Meridian's Australian "
                  "cloud region) manages the conversation, calls the Nimbus AI foundation "
                  "model for reasoning and language, and invokes the account tools through "
                  "an internal API gateway. Tool actions are executed against the core "
                  "banking and payments systems."),

            ("h", "4. Component inventory"),
            ("table",
             ["Component", "Role", "Hosting"],
             [["Channel front-ends", "Web chat, mobile app SDK", "AU (Meridian)"],
              ["Aria orchestrator", "Dialogue, planning, tool routing", "AU (Meridian cloud)"],
              ["Intent classifier", "Lightweight in-house intent routing", "AU (Meridian cloud)"],
              ["Nimbus AI model API", "Reasoning, language, tool/function calling", "US (vendor, us-east-1)"],
              ["Tool/API gateway", "Mediates tool calls; policy checks", "AU (Meridian)"],
              ["Core banking adapter", "Account read; contact update", "AU (Meridian)"],
              ["Payments adapter", "Fee reversal; refund; payment/transfer", "AU (Meridian)"],
              ["Conversation store", "Transcript + action log", "AU (Meridian)"]],
             (1.6, 2.7, 1.7)),

            ("h", "5. Models and hosting"),
            ("b", ["Reasoning / orchestration / natural language: the Nimbus AI foundation "
                   "model, accessed via the vendor's API hosted in the United States "
                   "(us-east-1). Customer data included in prompts (name, account details, "
                   "transaction history and free text) is transmitted to the US-hosted model "
                   "for processing.",
                   "No self-hosted or in-region model option is used in this release.",
                   "Intent routing uses a small in-house classifier hosted in Meridian's "
                   "Australian cloud region.",
                   "The vendor model is a managed service; the specific model version may be "
                   "updated by the vendor (see the vendor model card, Attachment 05)."]),
            ("note", "Data-residency note: prompts containing customer personal information "
                     "leave Australia and are processed in the United States by the model "
                     "vendor. The privacy treatment of this cross-border disclosure is dealt "
                     "with in the DPIA (Attachment 04)."),

            ("h", "6. Data flows"),
            ("num", ["The customer authenticates in the channel before any account action.",
                     "Aria assembles a prompt that may include the customer's name, account "
                     "identifiers, recent transactions and the free-text request, and sends "
                     "it to the Nimbus AI model API in the US.",
                     "The model returns a proposed response and, where applicable, a "
                     "structured tool call (e.g. 'refund transaction X, AUD 42.00').",
                     "The tool/API gateway applies business-policy checks (see Section 8) "
                     "and, if they pass and the value is within threshold, executes the "
                     "action against core banking / payments.",
                     "Aria confirms the outcome to the customer and writes an immutable "
                     "action-log entry."]),
            ("h3", "6.1 Data classification handled"),
            ("table",
             ["Data element", "Classification", "Crosses border to US model?"],
             [["Customer name / contact details", "Personal information", "Yes"],
              ["Account numbers / balances", "Sensitive / banking", "Yes"],
              ["Transaction history", "Sensitive / banking", "Yes"],
              ["Free-text customer message", "Personal information (may include more)", "Yes"],
              ["Authentication credentials", "Restricted", "No (handled in-channel)"]],
             (2.3, 1.9, 1.8)),

            ("h", "7. Tools (actions Aria can take on the account)"),
            ("table",
             ["Tool", "Action", "Limit", "Autonomy"],
             [["read_account", "Read balances, transactions, PII", "n/a", "Autonomous"],
              ["reverse_fee", "Reverse a fee / issue refund", "<= AUD 500", "Autonomous if checks pass"],
              ["initiate_payment", "Initiate payment / transfer", "<= AUD 5,000", "Autonomous if checks pass"],
              ["update_contact", "Update address / phone / email", "n/a", "Autonomous"],
              ["card_control", "Temporary lock / unlock / re-issue", "n/a", "Autonomous"],
              ["escalate", "Route to human agent", "n/a", "On low confidence / over-threshold"]],
             (1.6, 2.6, 1.2, 1.9)),

            ("h", "8. Policy and guardrail checks"),
            ("p", "Before executing a money-movement tool, the gateway applies a set of "
                  "BUSINESS-POLICY checks:"),
            ("b", ["The action value is within the tool threshold.",
                   "The customer is strongly authenticated for the session.",
                   "The destination is an existing payee or the customer's own linked "
                   "account (for transfers).",
                   "Per-session and per-day cumulative action limits are not exceeded.",
                   "A confirmation is shown to the customer before execution."]),
            ("note", "These are business-policy and payment-limit checks. They are not, and "
                     "are not intended to be, AML/CTF transaction-monitoring, suspicious-"
                     "matter detection, or sanctions-screening controls. Financial-crime "
                     "controls for Aria-initiated transactions are outside the scope of this "
                     "solution design."),

            ("h", "9. Autonomy and human control"),
            ("p", "Actions below the value thresholds are executed autonomously without "
                  "human review; approximately 70% of interactions complete with no human "
                  "involvement. Actions above threshold, and low-confidence interactions, "
                  "are routed to a human agent. There is no routine human review of Aria's "
                  "autonomous money-movement actions after the fact, beyond standard "
                  "exception reporting."),
            ("table",
             ["Scenario", "Handling"],
             [["Value within threshold, checks pass", "Executed autonomously"],
              ["Value over threshold", "Escalated to human agent"],
              ["Low model confidence", "Escalated to human agent"],
              ["Customer requests a human", "Escalated to human agent"],
              ["Executed autonomous action", "Logged; no routine post-action human review"]],
             (2.6, 3.4)),

            ("h", "10. Integration points"),
            ("table",
             ["System", "Interface", "Purpose"],
             [["Core banking", "Internal REST adapter", "Account read; contact update"],
              ["Payments / NPP", "Internal payments adapter", "Refund; payment/transfer"],
              ["CRM", "Event stream", "Interaction history"],
              ["IAM", "OIDC", "Customer & staff authentication"],
              ["Nimbus AI", "HTTPS API (US)", "Model reasoning / language"]],
             (1.3, 2.2, 2.5)),

            ("h", "11. Non-functional requirements"),
            ("table",
             ["NFR", "Target"],
             [["Availability", "99.9% (channel hours)"],
              ["Response latency (p95)", "< 4 s conversational turn"],
              ["Throughput", "300 concurrent conversations"],
              ["Scalability", "Auto-scale to 3x peak"],
              ["Recovery time objective (RTO)", "4 hours (Aria service)"],
              ["Recovery point objective (RPO)", "15 minutes (conversation/action log)"]],
             (2.4, 3.6)),

            ("h", "12. Logging and observability"),
            ("p", "Every conversation, model request and tool call is logged with a "
                  "correlation identifier to the conversation/action store, and forwarded to "
                  "the central security log platform. Immutable retention applies (see the "
                  "Information Security Standard, Attachment 06). Operational dashboards "
                  "track resolution rate, escalation rate, latency and error rate."),

            ("h", "13. Security architecture (summary)"),
            ("p", "Security controls for the platform (identity, encryption, logging, "
                  "network and incident management) are specified in the Information "
                  "Security Standard (Attachment 06). Threat modelling for AI/agent-specific "
                  "attack vectors (for example prompt injection via customer-supplied text, "
                  "or manipulation of a tool call) is PLANNED but not yet complete at the "
                  "date of this design; it is recorded as an open item in Section 15."),

            ("h", "14. Environments and deployment"),
            ("table",
             ["Environment", "Purpose", "Data"],
             [["Dev", "Development", "Synthetic"],
              ["Test", "Functional & integration test", "Masked"],
              ["Staging", "Pre-production / UAT", "Masked / limited"],
              ["Production", "Live customer servicing", "Live customer data"]],
             (1.4, 2.6, 2.0)),

            ("h", "15. Assumptions, constraints and open items"),
            ("b", ["Assumption: the vendor maintains the availability and safety posture "
                   "described in its model card (Attachment 05).",
                   "Constraint: no in-region model option is available from the vendor at "
                   "this release.",
                   "Open item: AI-specific threat modelling (prompt injection, tool-"
                   "permission abuse) not yet complete.",
                   "Open item: APP 8 overseas-disclosure treatment pending (see DPIA).",
                   "Open item: no Meridian-owned model validation or drift monitoring "
                   "established (see model card)."]),
        ],
        diagram=aria_architecture,
    )


def gen_04_dpia():
    build(
        os.path.join(ROOT, "04-dpia.pdf"),
        "Data Protection Impact Assessment - Aria",
        "MMB-DPIA-ARIA-2026", "CONFIDENTIAL - Privacy Office",
        "Data Protection Impact Assessment (DPIA)",
        "Aria Servicing Assistant v1.0 - Privacy Act 1988 (Cth) / Australian Privacy Principles",
        [
            ("docctl", [
                ("Assessment", "DPIA - Aria Servicing Assistant v1.0"),
                ("Framework", "Privacy Act 1988 (Cth); Australian Privacy Principles"),
                ("Owner", "Privacy Office"),
                ("Assessor", "Senior Privacy Analyst"),
                ("Reviewer", "Privacy Officer"),
                ("Classification", "CONFIDENTIAL"),
                ("Completed", "2 June 2026"),
                ("Status", "Completed for collection & security scope; open items deferred"),
            ]),
            ("h", "Sign-off"),
            ("table",
             ["Role", "Name / title", "Decision", "Date"],
             [["Assessor", "Senior Privacy Analyst", "Prepared", "28 May 2026"],
              ["Reviewer", "Privacy Officer", "Approved (with deferrals)", "2 Jun 2026"],
              ["Business owner", "Head of Digital", "Acknowledged", "3 Jun 2026"]],
             (1.4, 2.4, 1.5, 0.9)),

            ("h", "1. Purpose and scope"),
            ("p", "This DPIA assesses the privacy impacts of the Aria Servicing Assistant, "
                  "an agentic AI system that handles customer servicing and can take actions "
                  "on customer accounts. It is conducted under the Privacy Act 1988 (Cth) "
                  "and assesses the initiative against the Australian Privacy Principles "
                  "(APPs)."),
            ("p", "Scope for this version: the collection, use, storage and security of "
                  "personal information handled by Aria. Certain matters are expressly "
                  "deferred (see Section 9)."),

            ("h", "2. Methodology"),
            ("b", ["Systematic description of the processing (Section 3).",
                   "Personal-information inventory and data-flow mapping (Sections 4-5).",
                   "Assessment of necessity and proportionality (Section 6).",
                   "APP-by-APP compliance assessment (Section 7).",
                   "Privacy risk assessment and treatment (Section 8).",
                   "Identification of open / deferred items (Section 9)."]),

            ("h", "3. Description of the processing"),
            ("p", "Aria collects personal information directly from the customer during a "
                  "servicing interaction, retrieves further personal and account information "
                  "from Bank systems, and transmits relevant information to the Nimbus AI "
                  "foundation model (hosted in the United States) to generate responses and "
                  "propose actions. Where policy checks pass, Aria executes account actions "
                  "and records the outcome."),

            ("h", "4. Personal information inventory"),
            ("table",
             ["Data element", "APP category", "Source", "Purpose"],
             [["Name, contact details", "Personal information", "Customer / CRM", "Identify & service"],
              ["Account numbers, balances", "Personal (banking)", "Core banking", "Service request"],
              ["Transaction history", "Personal (banking)", "Core banking", "Answer & act"],
              ["Free-text messages", "Personal (may include sensitive)", "Customer", "Understand request"],
              ["Interaction transcript", "Personal information", "Aria", "Record & quality"],
              ["Action log", "Personal information", "Aria", "Audit & dispute"]],
             (1.9, 1.7, 1.4, 1.7)),
            ("p", "Aria is not designed to collect sensitive information (as defined in the "
                  "Act); however, customers may volunteer such information in free text. This "
                  "residual risk is addressed in Section 8."),

            ("h", "5. Data-flow mapping"),
            ("num", ["Customer -> channel (AU): the customer submits a request.",
                     "Channel -> Aria orchestrator (AU): the request is assembled.",
                     "Aria -> Nimbus AI model (US): a prompt containing personal information "
                     "is transmitted across the border to the United States for processing.",
                     "Nimbus AI -> Aria (AU): the model returns a response / proposed action.",
                     "Aria -> core banking / payments (AU): the action is executed.",
                     "Aria -> conversation & action store (AU): the interaction is recorded."]),
            ("note", "Step 3 is a cross-border disclosure of personal information to an "
                     "overseas recipient (the US-based model vendor). The APP 8 assessment "
                     "of this disclosure is DEFERRED (see Section 9.1)."),

            ("h", "6. Necessity and proportionality"),
            ("p", "The collection is assessed as reasonably necessary for the Bank's "
                  "servicing functions (APP 3). The volume of transaction data included in "
                  "prompts is limited to what is relevant to the request. Data-minimisation "
                  "measures (prompt scoping and redaction of unrelated fields) are applied "
                  "at the orchestration layer."),

            ("h", "7. APP-by-APP assessment"),
            ("table",
             ["APP", "Principle", "Assessment", "Status"],
             [["APP 1", "Open & transparent management of PI",
               "Privacy policy updated; BUT automated-decision-making transparency not assessed (see 9.2)", "Partial"],
              ["APP 2", "Anonymity & pseudonymity", "Not practicable for account servicing", "N/A"],
              ["APP 3", "Collection of solicited PI",
               "Collection limited to servicing need; data-minimisation applied", "Addressed"],
              ["APP 4", "Unsolicited PI", "Handling procedure exists for volunteered content", "Addressed"],
              ["APP 5", "Notice of collection",
               "Notice presented at the start of each session", "Addressed"],
              ["APP 6", "Use or disclosure", "Use limited to the servicing purpose", "Addressed"],
              ["APP 7", "Direct marketing", "Aria is not used for direct marketing", "N/A"],
              ["APP 8", "Cross-border disclosure",
               "PI disclosed to US-hosted model; APP 8 assessment DEFERRED (see 9.1)", "DEFERRED"],
              ["APP 9", "Gov related identifiers", "Not used as identifiers", "N/A"],
              ["APP 10", "Quality of PI", "Data sourced from systems of record", "Addressed"],
              ["APP 11", "Security of PI",
               "Encryption, access control, immutable logging (Att. 06)", "Addressed"],
              ["APP 12", "Access to PI", "BAU access request process applies", "Addressed"],
              ["APP 13", "Correction of PI", "BAU correction process applies", "Addressed"]],
             (0.7, 2.1, 3.1, 1.0)),

            ("h", "8. Privacy risk assessment"),
            ("table",
             ["#", "Risk", "Likelihood", "Impact", "Residual"],
             [["P1", "Unauthorised access to PI in transit/at rest", "Low", "High", "Low-Med"],
              ["P2", "Excess PI included in model prompts", "Medium", "Medium", "Medium"],
              ["P3", "Customer volunteers sensitive information", "Medium", "Medium", "Medium"],
              ["P4", "Cross-border disclosure without APP 8 basis", "Medium", "High", "Not yet rated (deferred)"],
              ["P5", "Lack of transparency re automated decisions", "Medium", "Medium", "Not yet rated (deferred)"]],
             (0.5, 2.6, 1.1, 0.9, 1.5)),

            ("h", "9. Open and deferred items"),
            ("h3", "9.1 Overseas disclosure (APP 8)"),
            ("p", "Aria transmits customer personal information to the Nimbus AI model hosted "
                  "in the United States. A full APP 8 cross-border disclosure assessment - "
                  "including whether the Bank has taken reasonable steps to ensure the "
                  "overseas recipient handles the information consistently with the APPs, or "
                  "whether an APP 8.2 exception applies - is DEFERRED to a later phase and is "
                  "NOT yet completed. The associated privacy risk (P4) is therefore not yet "
                  "rated."),
            ("h3", "9.2 Automated decision-making transparency (APP 1)"),
            ("p", "This DPIA does not assess the APP 1 transparency expectations for "
                  "automated decisions affecting individuals (including forthcoming automated-"
                  "decision-making transparency reforms). This is noted as OUT OF SCOPE for "
                  "this version and deferred to a later assessment."),

            ("h", "10. Notifiable Data Breaches readiness"),
            ("p", "Aria is within the scope of the Bank's NDB response plan. Eligible data "
                  "breaches involving Aria would be assessed and, where required, notified to "
                  "the OAIC and affected individuals under the NDB scheme."),

            ("h", "11. Residual privacy risk and recommendation"),
            ("p", "For the collection and security scope assessed, residual privacy risk is "
                  "assessed as MEDIUM and acceptable subject to the controls described. The "
                  "deferred overseas-disclosure (APP 8) and automated-decision transparency "
                  "(APP 1) items are NOT yet risk-rated and must be completed before those "
                  "aspects can be relied upon. The Privacy Officer approves this DPIA for the "
                  "assessed scope, with the open items recorded for completion."),

            ("h", "Appendix - Retention schedule (extract)"),
            ("table",
             ["Record", "Retention"],
             [["Conversation transcript", "24 months"],
              ["Action / tool-call log", "7 years"],
              ["Model request metadata", "24 months"],
              ["Consent / notice acceptance", "7 years"]],
             (2.6, 3.4)),
        ],
    )


def gen_05_model_card():
    build(
        os.path.join(ROOT, "05-nimbus-model-card.pdf"),
        "Nimbus AI - Model Card (vendor-supplied)",
        "NIMBUS-MC-2026", "COMMERCIAL IN CONFIDENCE - vendor document",
        "Nimbus AI Foundation Model - Model Card",
        "Vendor-supplied documentation (Nimbus AI Inc., USA) - with Meridian position appended",
        [
            ("docctl", [
                ("Model", "Nimbus AI foundation model (hosted API)"),
                ("Provider", "Nimbus AI Inc., United States"),
                ("Card version", "2026.2 (vendor)"),
                ("Deployment", "Hosted API, us-east-1"),
                ("Provided for", "Customer due diligence"),
                ("Meridian addendum", "Sections 10-11 (Meridian Model Risk position)"),
            ]),
            ("h", "1. Model overview"),
            ("p", "Nimbus AI is a general-purpose large language model provided as a hosted "
                  "API from data centres in the United States. This model card is supplied "
                  "by the vendor for customer due diligence and describes the model's "
                  "intended use, capabilities, evaluations and limitations."),
            ("h", "2. Intended use and out-of-scope use"),
            ("b", ["Intended: conversational assistance, summarisation, classification, and "
                   "tool/function calling within a customer-built application.",
                   "Out of scope (vendor position): use as a sole basis for legal, medical, "
                   "or safety-critical decisions without human oversight; the customer is "
                   "responsible for use-case controls."]),
            ("h", "3. Architecture and training data (high level)"),
            ("p", "The model is a transformer-based large language model trained by the "
                  "vendor on a broad corpus of licensed and publicly available text. "
                  "Training-data composition and model weights are proprietary and are not "
                  "disclosed to customers."),
            ("h", "4. Capabilities"),
            ("b", ["General natural-language understanding and generation.",
                   "Instruction following and multi-turn dialogue.",
                   "Structured tool / function calling with JSON output.",
                   "Configurable system prompts and response constraints."]),
            ("h", "5. Evaluation results (vendor-reported)"),
            ("table",
             ["Benchmark / test", "Vendor-reported result"],
             [["General knowledge & reasoning suite", "Competitive with peer models"],
              ["Instruction-following eval", "High adherence"],
              ["Safety refusal eval (harmful content)", "High refusal rate"],
              ["Tool-calling accuracy (vendor harness)", "Strong on well-formed schemas"],
              ["Hallucination rate (vendor eval)", "Reduced vs prior generation"]],
             (2.8, 3.2)),
            ("note", "All figures in Section 5 are the vendor's own reported results on the "
                     "vendor's own harnesses. They have not been independently reproduced by "
                     "Meridian (see Section 10)."),
            ("h", "6. Safety and guardrails (vendor)"),
            ("b", ["Content filters for categories including hate, self-harm and illegal "
                   "activity.",
                   "System-prompt controls and a moderation endpoint are available.",
                   "A published system card describes the vendor's safety approach."]),
            ("h", "7. Limitations and known issues"),
            ("b", ["The model can produce plausible but incorrect output (hallucination).",
                   "Performance may vary on domain-specific or Australian-context tasks.",
                   "The model is sensitive to prompt content and may follow instructions "
                   "embedded in user-supplied text.",
                   "Outputs are probabilistic and not guaranteed to be reproducible."]),
            ("h", "8. Hosting and data handling"),
            ("p", "The service is hosted in the United States (us-east-1). Data submitted "
                  "via the API is processed in that region. The vendor states that API data "
                  "is not used to train its foundation models by default, subject to the "
                  "terms of service."),
            ("h", "9. Versioning and change management"),
            ("p", "The vendor operates a managed service and may update the underlying model "
                  "version. Version pinning and advance change notification are available "
                  "only under specific enterprise terms and are NOT contractually guaranteed "
                  "under the current arrangement."),

            ("h", "10. Meridian Model Risk position - validation"),
            ("b", ["Independent validation by Meridian Model Risk Management: NOT PERFORMED. "
                   "Meridian relies on the vendor's own evaluations for this release.",
                   "No use-case-specific validation (servicing accuracy, harmful-action "
                   "rate, or money-movement error rate) has been conducted by Meridian for "
                   "Aria.",
                   "The high-tier independent-validation expectation in the Bank's Model "
                   "Risk framework is therefore not yet met for this system."]),
            ("h", "11. Meridian Model Risk position - monitoring"),
            ("b", ["Meridian-owned performance / drift monitoring for the Aria use case: "
                   "NOT ESTABLISHED. The model is a third-party black box and may be updated "
                   "by the vendor without Meridian-visible change control.",
                   "No production model-performance baseline or drift-alerting has been "
                   "implemented for Aria.",
                   "Version pinning / change notification from the vendor is not "
                   "contractually guaranteed (see Section 9)."]),
            ("note", "Net Meridian position: reliance on vendor evaluations; no Meridian-"
                     "owned independent validation and no drift monitoring for Aria at the "
                     "date of this card."),
        ],
    )


def gen_06_infosec_standard():
    build(
        os.path.join(ROOT, "06-information-security-standard.pdf"),
        "Information Security Standard - Aria",
        "MMB-ISS-ARIA-2026", "INTERNAL - Technology Risk",
        "Information Security Standard (extract)",
        "Applies to the Aria Servicing Assistant - aligned to APRA CPS 234",
        [
            ("docctl", [
                ("Standard", "Information Security Standard - Aria (extract)"),
                ("Aligned to", "APRA CPS 234; Group Information Security Policy"),
                ("Owner", "Chief Information Security Officer (CISO)"),
                ("Classification", "INTERNAL"),
                ("Version", "3.1"),
                ("Effective", "1 June 2026"),
                ("Next review", "1 June 2027"),
            ]),
            ("h", "1. Purpose and scope"),
            ("p", "This standard specifies the information-security controls that apply to "
                  "the Aria Servicing Assistant and its supporting components, in alignment "
                  "with APRA Prudential Standard CPS 234 (Information Security) and the "
                  "Group Information Security Policy. It addresses the confidentiality, "
                  "integrity and availability of the Aria platform and the information it "
                  "handles."),
            ("h", "2. Roles and responsibilities"),
            ("table",
             ["Role", "Responsibility"],
             [["CISO", "Owns this standard; reports security posture to the RCC"],
              ["Security Architecture", "Control design and assurance"],
              ["Platform Engineering", "Control implementation and operation"],
              ["Technology Risk (2nd line)", "Oversight and challenge"],
              ["Internal Audit (3rd line)", "Independent assurance"]],
             (2.0, 4.0)),
            ("h", "3. Information asset classification"),
            ("p", "Customer personal and banking information handled by Aria is classified "
                  "'Sensitive'. The conversation and action logs are classified 'Sensitive'. "
                  "Controls in this standard are calibrated to that classification."),

            ("h", "4. Identity and access management"),
            ("b", ["Customers are strongly authenticated before Aria performs any account "
                   "action, using the Bank's customer identity platform.",
                   "Staff and administrative access is role-based (RBAC) and enforced with "
                   "multi-factor authentication (MFA).",
                   "Privileged access is time-bound, logged, and subject to periodic "
                   "recertification.",
                   "Service-to-service calls use short-lived credentials issued by the "
                   "identity platform."]),
            ("h", "5. Encryption and key management"),
            ("b", ["Data is encrypted in transit using TLS 1.2 or higher for all internal "
                   "and external connections, including calls to the model API.",
                   "Data is encrypted at rest using AES-256.",
                   "Keys are managed in the Bank's key-management service with rotation and "
                   "access controls; keys are not exportable."]),
            ("h", "6. Logging, monitoring and audit"),
            ("b", ["Every Aria action, tool-call and model request is logged immutably with "
                   "a correlation identifier and retained for seven (7) years.",
                   "Logs are forwarded to the central SIEM; security use-cases and alerting "
                   "are configured.",
                   "Log integrity is protected (write-once storage; tamper-evident).",
                   "Access to logs is restricted and itself logged."]),
            ("h", "7. Network and perimeter security"),
            ("b", ["The Aria platform is deployed in segmented network zones with "
                   "default-deny controls between tiers.",
                   "Egress to the model API is restricted to allow-listed endpoints.",
                   "A web application firewall protects public channel endpoints."]),
            ("h", "8. Vulnerability and patch management"),
            ("b", ["Regular vulnerability scanning of platform components.",
                   "Risk-based patch SLAs (critical within defined windows).",
                   "Dependency and container image scanning in the build pipeline."]),
            ("h", "9. Secure software development"),
            ("b", ["Secure SDLC with peer review and mandatory security testing gates.",
                   "Secrets management via the vault; no secrets in code.",
                   "Separation of duties between build and production deployment."]),
            ("h", "10. Incident management and APRA notification"),
            ("p", "Security incidents affecting Aria are managed under the Group security "
                  "incident runbook. Material information-security incidents and control "
                  "weaknesses are notified to APRA within the timeframes required by "
                  "CPS 234. Incident post-mortems feed corrective actions."),
            ("h", "11. Security testing"),
            ("b", ["Independent penetration testing prior to production and at least "
                   "annually thereafter.",
                   "Remediation of findings tracked to closure by severity SLA."]),
            ("h", "12. Third-party / API security"),
            ("p", "Connections to the Nimbus AI model API use mutually authenticated, "
                  "encrypted channels to allow-listed endpoints. Vendor security posture is "
                  "assessed under the Third-Party Risk Assessment (Attachment 07)."),

            ("h", "13. CPS 234 control matrix (extract)"),
            ("table",
             ["CPS 234 area", "Control in this standard", "Status"],
             [["Roles & responsibilities", "Section 2", "In place"],
              ["Information asset classification", "Section 3", "In place"],
              ["Access control / identity", "Section 4", "In place"],
              ["Encryption", "Section 5", "In place"],
              ["Logging & monitoring", "Section 6", "In place"],
              ["Vulnerability & patching", "Section 8", "In place"],
              ["Incident mgmt & notification", "Section 10", "In place"],
              ["Testing & assurance", "Section 11", "In place"]],
             (2.4, 2.6, 1.0)),

            ("h", "14. Scope exclusions"),
            ("p", "This standard addresses the confidentiality, integrity and availability "
                  "of the Aria platform and its data. It does NOT, in this version, address "
                  "AI/agent-specific threats - for example prompt-injection via customer-"
                  "supplied text, tool-permission abuse, or manipulation of Aria into "
                  "executing an unintended payment or account action. Those threat classes "
                  "are noted for a future revision of this standard and are not covered by "
                  "the controls above."),
            ("note", "Reviewers should note that the controls in Sections 4-12 are "
                     "conventional (perimeter, identity, crypto, logging) and do not include "
                     "defences specific to an autonomous agent that can move money "
                     "(e.g. prompt-injection resistance, tool-call authorisation limits, or "
                     "an anti-manipulation control on the payment tool)."),
        ],
    )


def gen_07_tprm():
    build(
        os.path.join(ROOT, "07-third-party-risk-assessment.pdf"),
        "Third-Party Risk Assessment - Nimbus AI",
        "MMB-TPRM-NIMBUS-2026", "CONFIDENTIAL - Procurement & Vendor Risk",
        "Third-Party Risk Assessment",
        "Vendor: Nimbus AI Inc. (US) - foundation model service for Aria",
        [
            ("docctl", [
                ("Assessment", "Third-Party Risk Assessment - Nimbus AI Inc."),
                ("Service", "Hosted foundation model API (used by Aria)"),
                ("Owner", "Procurement & Vendor Risk"),
                ("Assessor", "Vendor Risk Analyst"),
                ("Classification", "CONFIDENTIAL"),
                ("Version", "1.2"),
                ("Date", "20 June 2026"),
            ]),
            ("h", "1. Purpose and scope"),
            ("p", "This assessment evaluates the risk of engaging Nimbus AI Inc. as the "
                  "foundation-model service provider for the Aria Servicing Assistant. It "
                  "covers entity-level due diligence and identifies open items requiring "
                  "resolution before or shortly after production."),
            ("h", "2. Vendor overview"),
            ("table",
             ["Attribute", "Detail"],
             [["Vendor", "Nimbus AI Inc."],
              ["Jurisdiction", "United States"],
              ["Service", "Hosted large language model API"],
              ["Data location", "United States (us-east-1)"],
              ["Role", "Sub-processor of customer personal information"],
              ["Criticality (proposed)", "Supports a customer-facing service"]],
             (1.8, 4.2)),
            ("h", "3. Service description"),
            ("p", "Nimbus AI provides the reasoning and natural-language capability for "
                  "Aria via a hosted API. Aria transmits prompts containing customer "
                  "personal and banking information to the service for processing and "
                  "receives responses and proposed tool calls."),
            ("h", "4. Due diligence performed"),
            ("b", ["Vendor security posture reviewed; a current SOC 2 Type II report was "
                   "sighted and reviewed with no material exceptions noted.",
                   "The vendor also holds ISO/IEC 27001 certification (certificate sighted).",
                   "Financial viability and reputational checks completed with no adverse "
                   "findings.",
                   "The contract includes confidentiality and data-handling clauses and a "
                   "data-processing addendum."]),
            ("table",
             ["Due-diligence item", "Evidence", "Outcome"],
             [["Security attestation", "SOC 2 Type II (sighted)", "Satisfactory"],
              ["Security certification", "ISO/IEC 27001 (sighted)", "Satisfactory"],
              ["Financial viability", "Financial review", "Satisfactory"],
              ["Reputational check", "Adverse-media screen", "No adverse findings"],
              ["Contract & DPA", "Executed agreement", "In place"]],
             (2.2, 2.4, 1.4)),
            ("h", "5. Domain assessment"),
            ("table",
             ["Domain", "Finding", "Rating"],
             [["Information security", "Attestations sighted; controls satisfactory", "Low-Med"],
              ["Data protection / offshoring", "Data processed in the US; see Section 6", "Open"],
              ["Regulatory classification (CPS 230)", "Materiality not assessed; see Section 7", "Open"],
              ["Resilience / continuity", "No exit or continuity plan; see Section 8", "Open"],
              ["Concentration", "Single-vendor dependency for all servicing", "Medium"]],
             (2.3, 2.7, 1.0)),
            ("h", "6. Data and offshoring"),
            ("p", "Customer personal and banking information is processed in the United "
                  "States. The cross-border data-handling and foreign lawful-access "
                  "implications are FLAGGED but NOT RESOLVED in this assessment; the privacy "
                  "treatment (APP 8) is deferred in the DPIA (Attachment 04). This item "
                  "remains open."),
            ("h", "7. CPS 230 material service provider classification"),
            ("p", "Whether Nimbus AI is a 'material service provider' under APRA CPS 230 has "
                  "NOT yet been assessed, and NO entry exists in the material service "
                  "provider register for this arrangement. As a result, the CPS 230 "
                  "obligations that attach to material arrangements (including formal "
                  "agreement requirements, monitoring, and notification to APRA) have not "
                  "been confirmed as met."),
            ("h", "8. Exit and business continuity"),
            ("p", "NO exit plan or substitutability assessment exists for the Nimbus AI "
                  "arrangement, and there is no tested fallback if the service is degraded, "
                  "unavailable or withdrawn. Because Aria depends on a single model vendor "
                  "for all servicing, this is a single-vendor dependency with no documented "
                  "continuity arrangement. This item remains open."),
            ("h", "9. Contractual review (summary)"),
            ("table",
             ["Clause", "Position"],
             [["Confidentiality", "Present"],
              ["Data-processing addendum", "Present"],
              ["Audit / assurance rights", "Limited (attestation reliance)"],
              ["Service levels / availability", "Standard vendor SLA"],
              ["Version pinning / change notice", "Not guaranteed under current terms"],
              ["Exit assistance", "Not addressed"]],
             (2.4, 3.6)),
            ("h", "10. Residual risk and recommendation"),
            ("p", "Vendor due diligence is SATISFACTORY at the entity level (Section 4). "
                  "However, arrangement-level items - offshoring/APP 8 (Section 6), CPS 230 "
                  "materiality classification and register entry (Section 7), and exit / "
                  "business-continuity for a single-vendor dependency (Section 8) - remain "
                  "OPEN and should be resolved. The assessment recommends these open items "
                  "be treated as conditions on the production decision."),
            ("h", "Appendix - Open items register"),
            ("table",
             ["#", "Open item", "Owner", "Status"],
             [["O1", "APP 8 / offshoring treatment", "Privacy Office", "Open"],
              ["O2", "CPS 230 materiality classification + register entry", "Vendor Risk", "Open"],
              ["O3", "Exit & business-continuity plan", "Vendor Risk / Technology", "Open"],
              ["O4", "Version pinning / change-notification terms", "Procurement", "Open"]],
             (0.5, 3.3, 1.4, 0.8)),
        ],
    )


def gen():
    gen_01_board_paper()
    gen_02_company_profile()
    gen_03_solution_design()
    gen_04_dpia()
    gen_05_model_card()
    gen_06_infosec_standard()
    gen_07_tprm()


if __name__ == "__main__":
    gen()
    print("done")
