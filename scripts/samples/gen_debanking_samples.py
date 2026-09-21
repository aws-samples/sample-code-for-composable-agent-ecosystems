#!/usr/bin/env python3
"""Generate the REMITTANCE/DCE DE-BANKING sample document package as PDFs.

Scenario (fictional): Coral Coast Bank Limited (CCB) - an ASX-listed, APRA-regulated
mid-tier regional ADI and AUSTRAC reporting entity - seeks Board Risk & Compliance
Committee approval to EXIT (offboard and close the accounts of) its entire remittance-
service-provider (RSP) and digital-currency-exchange (DCE) business-banking segment
(~850 accounts), citing elevated ML/TF risk and the cost of the Tranche-2 compliance
uplift. This is a NON-TECH board decision for the enterprise-risk-board eco.

The pack is deliberately CRAFTED so the eco has real signal - two regulators pulling
opposite ways (manage-your-risk vs don't-wholesale-debank) - not a rubber stamp:

  STRONG, EVIDENCED (lenses mark PRESENT, cited to a doc):
    - A genuine, documented segment ML/TF risk assessment (proper AML posture).
    - The segment customers are AUSTRAC-registered; CCB already runs transaction
      monitoring + SMR capability (i.e. it CAN manage risk).

  DELIBERATE GAPS / TENSION (challenger + lenses catch, grounded in the catalogs):
    - Blanket, sector-wide exit with NO per-customer risk differentiation - contrary
      to the risk-based approach + AUSTRAC de-banking guidance + CoFR policy.
    - The reversal: de-banking pushes flows into informal / unregistered / offshore
      channels (hawala, unregistered remittance, unregulated exchanges), REDUCING
      visibility and increasing systemic ML/TF risk (grounded in the alternative-
      remittance-abuse + cryptocurrency-laundering typologies).
    - Cost is the real driver, framed as risk.
    - AUSTRAC asked CCB to STRENGTHEN its risk-based systems; CCB reinterprets this
      as a mandate to EXIT.
    - Financial inclusion + indirect discrimination (migrant remittance corridors) -
      flagged by internal legal but deferred; no assessment done.
    - Transparency failure: 30-day notice, generic reasons, no review/appeal, no exit
      assistance - contrary to CoFR de-banking expectations.
    - FAR accountability ORPHAN: MLRO owns the AML outcome; nobody owns the customer-
      fairness / de-banking-conduct outcome.

  Expected verdict: GO-WITH-CONDITIONS (arguably NO-GO on the blanket exit as framed).

Run:  python3 scripts/gen_debanking_samples.py
Deps: fpdf2 (reuses the shared PDF helper library in gen_enterprise_risk_board_samples)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_enterprise_risk_board_samples import build, _s, NAVY, STEEL  # shared helpers

ROOT = os.path.join(os.path.dirname(__file__), "..", "samples", "enterprise-risk-board",
                    "remittance-dce-debanking")


def debanking_flow(pdf):
    """Simple before/after flow: regulated (visible) vs post-exit informal (invisible)."""
    if pdf.get_y() > pdf.page_break_trigger - 62:
        pdf.add_page()
    pdf.ln(2)
    x0, y0 = pdf.l_margin, pdf.get_y()
    W = pdf.w - pdf.l_margin - pdf.r_margin

    def box(x, y, w, h, label, fill):
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(90, 110, 150)
        pdf.rect(x, y, w, h, style="DF")
        pdf.set_xy(x, y + h / 2 - 5)
        pdf.set_font("Helvetica", "", 7.3)
        pdf.multi_cell(w, 3.4, _s(label), align="C")

    def arrow(x1, y1, x2, y2):
        pdf.set_draw_color(120)
        pdf.line(x1, y1, x2, y2)

    bw, bh = 44, 18
    gap = (W - 3 * bw) / 2
    xs = [x0 + i * (bw + gap) for i in range(3)]
    # Row 1 - today (visible)
    pdf.set_xy(x0, y0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, _s("TODAY - flows inside the regulated channel (visible to AUSTRAC):"),
             new_x="LMARGIN", new_y="NEXT")
    r1 = pdf.get_y() + 1
    box(xs[0], r1, bw, bh, "RSP / DCE\ncustomers (~850)", (235, 240, 250))
    box(xs[1], r1, bw, bh, "Coral Coast Bank\nTM + SMR + IFTI reporting", (225, 245, 235))
    box(xs[2], r1, bw, bh, "AUSTRAC\nvisibility of flows", (225, 245, 235))
    arrow(xs[0] + bw, r1 + bh / 2, xs[1], r1 + bh / 2)
    arrow(xs[1] + bw, r1 + bh / 2, xs[2], r1 + bh / 2)
    # Row 2 - after blanket exit (invisible)
    pdf.set_xy(x0, r1 + bh + 6)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 5, _s("AFTER A BLANKET EXIT - flows move OUTSIDE the regulated channel:"),
             new_x="LMARGIN", new_y="NEXT")
    r2 = pdf.get_y() + 1
    box(xs[0], r2, bw, bh, "Same customers +\ntheir end-users", (235, 240, 250))
    box(xs[1], r2, bw, bh, "Informal / unregistered\nremittance (hawala),\noffshore & unregulated\nexchanges", (250, 232, 232))
    box(xs[2], r2, bw, bh, "REDUCED visibility\n(no monitoring, no SMR)", (250, 232, 232))
    arrow(xs[0] + bw, r2 + bh / 2, xs[1], r2 + bh / 2)
    arrow(xs[1] + bw, r2 + bh / 2, xs[2], r2 + bh / 2)
    pdf.set_y(r2 + bh + 4)
    pdf.ln(2)


def gen_01_board_paper():
    build(
        os.path.join(ROOT, "01-board-paper.pdf"),
        "Coral Coast Bank - Board Risk & Compliance Committee",
        "CCB-RCC-2026-08", "CONFIDENTIAL - Board Risk & Compliance Committee",
        "Board Risk & Compliance Committee Paper",
        "Agenda Item 5 - Proposed exit of the remittance & digital-currency-exchange customer segment",
        [
            ("docctl", [
                ("Paper reference", "CCB-RCC-2026-08 / Agenda Item 5"),
                ("Committee", "Board Risk & Compliance Committee (RCC)"),
                ("Meeting date", "18 August 2026"),
                ("Classification", "CONFIDENTIAL - Board and invitees only"),
                ("Author", "Head of Business Banking (segment owner)"),
                ("Co-author", "Chief Risk Officer's office; Financial Crime (MLRO)"),
                ("Sponsor", "Chief Operating Officer"),
                ("Decision type", "Approval - customer-segment exit"),
                ("Distribution", "RCC members; CRO; CCO; CFO; MLRO; Company Secretary"),
            ]),
            ("h", "Document control and version history"),
            ("table",
             ["Ver.", "Date", "Author", "Summary of change"],
             [["0.2", "20 Jul 2026", "Business Banking", "Initial draft - segment exit proposal"],
              ["0.5", "31 Jul 2026", "Financial Crime", "Added ML/TF risk assessment summary"],
              ["0.7", "7 Aug 2026", "Finance", "Added cost and revenue analysis"],
              ["0.9", "12 Aug 2026", "CRO office", "Risk summary and regulatory considerations"],
              ["1.0", "12 Aug 2026", "Business Banking", "Released to RCC for 18 August 2026"]],
             (0.7, 1.3, 2.0, 4.2)),

            ("h", "1. Decision sought"),
            ("p", "The Committee is asked to APPROVE the exit of Coral Coast Bank's entire "
                  "remittance-service-provider (RSP) and digital-currency-exchange (DCE) "
                  "business-banking customer segment - approximately 850 accounts - by "
                  "offboarding and closing those accounts on 30 days' written notice, "
                  "commencing September 2026. Management recommends approval."),
            ("p", "This is a decision paper. Management seeks a resolution of the Committee "
                  "in the terms set out in Section 11."),

            ("h", "2. Purpose and context"),
            ("p", "Australia's AML/CTF regime was significantly expanded by the Tranche-2 "
                  "reforms, with the new AML/CTF Rules applying to existing reporting "
                  "entities from 31 March 2026 and the broader regime commencing 1 July "
                  "2026. Against that backdrop, and following heightened regulator focus on "
                  "the money-transfer and digital-currency sectors, management has reviewed "
                  "the economics and risk of continuing to bank the RSP/DCE segment."),
            ("p", "The segment sits within the Business Banking division. It comprises "
                  "independent money-transfer operators (many serving remittance corridors "
                  "to the Pacific, South and South-East Asia and Africa) and a small number "
                  "of AUSTRAC-registered digital-currency exchanges."),

            ("h", "3. Background - the segment"),
            ("table",
             ["Sub-segment", "Accounts", "Description"],
             [["Independent remittance dealers (RSPs)", "~760",
               "AUSTRAC-registered money-transfer businesses; corridor-focused"],
              ["Digital-currency exchanges (DCEs)", "~90",
               "AUSTRAC-registered exchanges; fiat on/off-ramp"],
              ["Total", "~850", "Business Banking transaction & FX accounts"]],
             (2.0, 1.0, 3.6)),

            ("h", "4. Rationale for the proposed exit"),
            ("p", "Management's rationale rests on two drivers: (a) the elevated inherent "
                  "ML/TF risk of the segment; and (b) the cost of the enhanced monitoring "
                  "and due diligence required to bank the segment to the Tranche-2 standard."),
            ("table",
             ["Driver", "Management view", "Weight"],
             [["ML/TF risk", "Segment is high inherent risk (see Attachment 02)", "Secondary"],
              ["Compliance cost", "Tranche-2 monitoring uplift is disproportionate to segment revenue (Attachment 03)", "PRIMARY"],
              ["Regulator focus", "Heightened AUSTRAC scrutiny of the sectors (Attachment 04)", "Contributing"]],
             (1.3, 3.7, 1.0)),
            ("note", "Management candidly notes that the PRIMARY driver is the cost of the "
                     "compliance uplift relative to the segment's revenue contribution; the "
                     "risk and regulator-focus drivers are contributing factors. The exit is "
                     "presented as a commercial de-risking decision."),

            ("h", "5. Financial impact"),
            ("table",
             ["Line (AUD, annual)", "Amount"],
             [["Segment revenue (FX margin, fees)", "~2.4m"],
              ["Current cost to serve + monitoring", "~1.6m"],
              ["Incremental Tranche-2 monitoring uplift (segment)", "~2.1m"],
              ["Net position if retained and uplifted", "~(1.3m) loss"],
              ["Net position if exited", "~+1.1m (cost avoided)"]],
             (3.6, 2.4)),

            ("h", "6. Scope of this decision"),
            ("h3", "6.1 In scope"),
            ("b", ["All ~850 RSP and DCE business-banking accounts.",
                   "Offboarding on 30 days' written notice.",
                   "Cessation of new onboarding for these sectors."]),
            ("h3", "6.2 Out of scope"),
            ("b", ["Retail customers who send remittances (unaffected).",
                   "Any differentiated / risk-based retention of lower-risk customers "
                   "within the segment (management proposes a uniform exit)."]),

            ("h", "7. Risk summary (management view)"),
            ("table",
             ["#", "Risk", "Inherent", "Mitigation (mgmt)", "Residual"],
             [["R1", "Continuing to bank high ML/TF-risk customers", "High",
               "Exit removes the exposure", "Low"],
              ["R2", "Compliance-cost / disproportionate spend", "High",
               "Exit avoids the uplift", "Low"],
              ["R3", "Customer / relationship impact", "Medium",
               "30-day notice; referrals to other providers", "Medium"],
              ["R4", "Reputational impact of exit", "Medium",
               "Standard external communications", "Medium"]],
             (0.5, 2.6, 1.0, 2.4, 0.9)),
            ("note", "This management summary does not separately assess financial-inclusion "
                     "or discrimination impacts of exiting corridor-focused remittance "
                     "customers, nor the AML/CTF consequence of flows migrating outside the "
                     "regulated channel. The Committee's challenge is invited."),

            ("h", "8. Regulatory considerations"),
            ("p", "Management considers the exit consistent with the Bank's AML/CTF "
                  "obligations and risk appetite. AUSTRAC has increased its scrutiny of the "
                  "remittance and digital-currency sectors and has engaged with CCB about "
                  "strengthening its systems for the segment (Attachment 04); management "
                  "reads this engagement as supporting a decision to exit."),
            ("p", "Management is aware of guidance from AUSTRAC and the Council of Financial "
                  "Regulators discouraging wholesale 'de-banking' and encouraging a risk-"
                  "based approach with transparency and notice. Internal Legal has provided "
                  "advice on this and on anti-discrimination considerations (Attachment 06); "
                  "management notes these points for consideration but recommends proceeding "
                  "with the exit on commercial grounds."),

            ("h", "9. Customer impact"),
            ("p", "Approximately 850 business customers would be offboarded. Each would "
                  "receive 30 days' written notice and a standard letter stating that the "
                  "Bank has made a commercial decision to cease providing services to the "
                  "sector. No individual reasons, review mechanism, or transition assistance "
                  "are proposed."),

            ("h", "10. Accountability"),
            ("p", "The accountable entity is Coral Coast Bank Limited; the Chief Risk Officer "
                  "is the accountable executive for risk, and the Money Laundering Reporting "
                  "Officer owns the financial-crime program. The business owner for the "
                  "segment and this proposal is the Head of Business Banking. No separate "
                  "accountable person is nominated for the fair-treatment / customer-outcome "
                  "(conduct) consequences of the exit."),

            ("h", "11. Recommendation and resolution sought"),
            ("p", "That the Board Risk & Compliance Committee RESOLVES to approve the exit of "
                  "the RSP and DCE customer segment on the terms described in this paper, "
                  "with offboarding to commence in September 2026 on 30 days' notice."),

            ("h", "Appendix A - Options considered"),
            ("table",
             ["Option", "Description", "Management assessment"],
             [["A", "Retain and uplift monitoring to Tranche-2 standard (risk-based)",
               "Rejected - net annual loss ~1.3m; monitoring cost disproportionate"],
              ["B", "Risk-based, per-customer assessment; retain manageable customers, exit the rest",
               "Not pursued - considered operationally complex"],
              ["C", "Blanket exit of the whole segment (recommended)",
               "Recommended - simplest; avoids the uplift cost"],
              ["D", "Do nothing", "Rejected - does not meet Tranche-2 expectations"]],
             (0.6, 3.0, 3.4)),
            ("h", "Appendix B - Attachments"),
            ("num", ["02 - Segment ML/TF Risk Assessment",
                     "03 - Cost & Program Impact Analysis",
                     "04 - AUSTRAC Engagement Summary",
                     "05 - Customer & Community Impact Profile",
                     "06 - Legal Advice Extract (de-banking & discrimination)",
                     "07 - Customer Offboarding & Communications Plan",
                     "08 - AML/CTF Program Extract (segment controls)"]),
        ],
    )


def gen_02_segment_risk_assessment():
    build(
        os.path.join(ROOT, "02-segment-mltf-risk-assessment.pdf"),
        "Coral Coast Bank - Segment ML/TF Risk Assessment",
        "CCB-FC-RA-2026", "CONFIDENTIAL - Financial Crime",
        "Segment ML/TF Risk Assessment",
        "Remittance-service-provider (RSP) and digital-currency-exchange (DCE) customer segment",
        [
            ("docctl", [
                ("Document", "Segment ML/TF Risk Assessment - RSP/DCE"),
                ("Owner", "Money Laundering Reporting Officer (MLRO)"),
                ("Framework", "AML/CTF Act 2006; risk-based approach"),
                ("Classification", "CONFIDENTIAL"),
                ("Version", "2.0"),
                ("Date", "31 July 2026"),
            ]),
            ("h", "1. Purpose and scope"),
            ("p", "This assessment evaluates the money-laundering and terrorism-financing "
                  "(ML/TF) risk of Coral Coast Bank's RSP and DCE business-banking segment, "
                  "in line with the risk-based approach expected under the AML/CTF Act and "
                  "AUSTRAC guidance. It informs the exit proposal at Agenda Item 5."),
            ("h", "2. Methodology"),
            ("p", "Inherent risk was assessed across the four AUSTRAC risk factors - customer "
                  "types, products/services, delivery channels, and jurisdictions - and then "
                  "considered against existing controls to arrive at residual risk. The "
                  "assessment draws on the Bank's transaction-monitoring data and known "
                  "sector typologies."),
            ("h", "3. Segment overview"),
            ("p", "The segment comprises ~760 AUSTRAC-registered independent remittance "
                  "dealers and ~90 AUSTRAC-registered digital-currency exchanges. Remittance "
                  "customers are predominantly corridor-focused (Pacific, South/South-East "
                  "Asia, Africa) and are, for many end-users, the primary means of sending "
                  "money to family overseas."),
            ("h", "4. Inherent risk assessment"),
            ("table",
             ["Risk factor", "Assessment", "Basis / typology"],
             [["Customer type", "High", "Cash-intensive; RSPs and DCEs are higher-risk designated services"],
              ["Products / services", "High", "Cross-border value transfer; fiat-crypto conversion"],
              ["Delivery channel", "Medium-High", "Non-face-to-face; agent networks"],
              ["Jurisdictions", "High", "Exposure to higher-risk corridors and offshore counterparties"]],
             (1.4, 1.1, 3.5)),
            ("p", "Relevant ML/TF typologies for the segment include cuckoo smurfing and "
                  "alternative-remittance (hawala) abuse in the money-transfer sub-segment, "
                  "and digital-currency laundering (mixers, chain-hopping, unregistered "
                  "exchanges) in the DCE sub-segment, as well as third-party money-mule "
                  "activity. International funds transfer instruction (IFTI) reporting is a "
                  "core obligation for this segment."),
            ("note", "The inherent ML/TF risk of the segment is genuinely HIGH and is well "
                     "evidenced. That is not in dispute. The question for the Committee is "
                     "whether that risk is best MANAGED or whether the segment should be "
                     "EXITED wholesale."),

            ("h", "5. Existing controls"),
            ("p", "Coral Coast Bank already applies the following controls to the segment: "
                  "customer identification and enhanced due diligence at onboarding; ongoing "
                  "customer due diligence; automated transaction monitoring with sector-"
                  "tuned rules; suspicious-matter reporting (SMR); IFTI reporting; and "
                  "sanctions screening. The Bank therefore HAS an operating capability to "
                  "monitor and report on this segment."),
            ("table",
             ["Control", "Status for the segment"],
             [["Customer identification / KYC", "In place"],
              ["Enhanced due diligence (higher-risk)", "In place"],
              ["Ongoing customer due diligence / monitoring", "In place (sector-tuned rules)"],
              ["Suspicious matter reporting (SMR)", "In place"],
              ["IFTI reporting", "In place"],
              ["Sanctions screening", "In place"]],
             (2.8, 3.2)),

            ("h", "6. Residual risk and recommendation"),
            ("p", "After controls, the segment is assessed as retaining a HIGH residual ML/TF "
                  "risk at the SEGMENT level. On that basis this assessment supports exiting "
                  "the segment."),
            ("note", "Limitation of this assessment: residual risk was assessed at the "
                     "SEGMENT level, treating all ~850 customers as a single high-risk group. "
                     "It does NOT differentiate risk at the individual-customer level and "
                     "does NOT identify the sub-set of customers whose risk could be managed "
                     "within appetite (e.g. long-tenured, low-volume, fully-compliant "
                     "registered dealers). No manageable-cohort analysis was performed."),

            ("h", "7. Consequence of exit for financial-crime visibility"),
            ("p", "Where regulated banking is withdrawn from an entire sector, the underlying "
                  "demand for value transfer does not disappear. Flows are likely to migrate "
                  "to informal, unregistered or offshore channels - including unregistered "
                  "remittance (hawala) arrangements and unregulated exchanges - that the Bank "
                  "and AUSTRAC cannot see or monitor. A blanket exit may therefore REDUCE the "
                  "visibility of these flows to the financial system, even as it removes the "
                  "risk from the Bank's own books."),
            ("diagram", None),
        ],
        diagram=debanking_flow,
    )


def gen_03_cost_impact():
    build(
        os.path.join(ROOT, "03-cost-and-program-impact.pdf"),
        "Coral Coast Bank - Cost & Program Impact Analysis",
        "CCB-FIN-CIA-2026", "CONFIDENTIAL - Finance / Business Banking",
        "Cost & Program Impact Analysis",
        "Tranche-2 compliance uplift vs segment economics - RSP/DCE segment",
        [
            ("docctl", [
                ("Document", "Cost & Program Impact Analysis - RSP/DCE exit"),
                ("Owner", "Finance Business Partner, Business Banking"),
                ("Classification", "CONFIDENTIAL"),
                ("Version", "1.1"),
                ("Date", "7 August 2026"),
            ]),
            ("h", "1. Purpose"),
            ("p", "This analysis quantifies the cost of maintaining and uplifting the "
                  "financial-crime control environment for the RSP/DCE segment to the "
                  "Tranche-2 standard, and compares it with the segment's revenue "
                  "contribution, to inform the exit decision."),
            ("h", "2. Tranche-2 compliance uplift (segment attributable)"),
            ("table",
             ["Cost item (AUD, annual unless noted)", "Estimate"],
             [["Enhanced monitoring tooling / rules tuning (segment share)", "0.7m"],
              ["Additional financial-crime analysts (6 FTE)", "0.9m"],
              ["Independent review, assurance and reporting", "0.3m"],
              ["One-off remediation / systems (Year 1)", "0.6m (one-off)"],
              ["Total incremental uplift (steady state)", "~2.1m p.a."]],
             (3.8, 2.2)),
            ("h", "3. Segment economics"),
            ("table",
             ["Line (AUD, annual)", "Amount"],
             [["FX margin", "1.5m"],
              ["Account & transaction fees", "0.6m"],
              ["Other (ancillary)", "0.3m"],
              ["Total segment revenue", "~2.4m"],
              ["Current cost to serve + monitoring", "~1.6m"],
              ["Current contribution margin", "~0.8m"]],
             (3.8, 2.2)),
            ("h", "4. Net economics"),
            ("p", "Applying the incremental Tranche-2 uplift (~2.1m p.a.) against the current "
                  "contribution margin (~0.8m) produces a net annual LOSS of approximately "
                  "1.3m if the segment is retained and uplifted. Exiting the segment avoids "
                  "the uplift and yields an approximate net improvement of ~1.1m p.a. once "
                  "run-down is complete."),
            ("table",
             ["Scenario", "Net annual position (AUD)"],
             [["Retain + uplift to Tranche-2 standard", "~(1.3m) loss"],
              ["Risk-based partial retention (indicative)", "~(0.2m) to break-even"],
              ["Blanket exit", "~+1.1m (cost avoided)"]],
             (3.4, 2.6)),
            ("note", "The dominant variable in the decision is the COST of the compliance "
                     "uplift, not a change in the segment's inherent risk (which is long-"
                     "standing). On these figures, the exit is primarily a cost-avoidance "
                     "decision. A risk-based partial retention (retaining lower-risk, lower-"
                     "cost-to-monitor customers) was estimated to be close to break-even but "
                     "was not developed in detail."),
            ("h", "5. Assumptions"),
            ("b", ["Segment-attributable costs allocated on a transaction-volume basis.",
                   "Uplift estimate based on vendor quotes and current analyst cost.",
                   "Revenue held flat; no growth assumed.",
                   "Partial-retention scenario is indicative only and not fully modelled."]),
        ],
    )


def gen_04_austrac_engagement():
    build(
        os.path.join(ROOT, "04-austrac-engagement-summary.pdf"),
        "Coral Coast Bank - AUSTRAC Engagement Summary",
        "CCB-FC-REG-2026", "CONFIDENTIAL - Financial Crime / Regulatory Affairs",
        "AUSTRAC Engagement Summary",
        "Record of engagement with AUSTRAC regarding the RSP/DCE segment",
        [
            ("docctl", [
                ("Document", "AUSTRAC Engagement Summary - RSP/DCE segment"),
                ("Owner", "Head of Regulatory Affairs; MLRO"),
                ("Classification", "CONFIDENTIAL"),
                ("Version", "1.0"),
                ("Date", "5 August 2026"),
            ]),
            ("h", "1. Purpose"),
            ("p", "This summary records the Bank's engagement with AUSTRAC concerning the "
                  "remittance and digital-currency segment, to inform the Committee's "
                  "consideration of the proposed exit."),
            ("h", "2. Chronology of engagement"),
            ("table",
             ["Date", "Event", "AUSTRAC's stated position"],
             [["Nov 2025", "Industry engagement on Tranche-2 uplift",
               "Reporting entities to strengthen risk-based systems and controls"],
              ["Mar 2026", "Review of CCB's segment monitoring",
               "Identified areas to STRENGTHEN monitoring rules and EDD for the segment"],
              ["May 2026", "Follow-up correspondence",
               "Requested a remediation plan; expected the Bank to MANAGE the risk"],
              ["Jul 2026", "Meeting with CCB Financial Crime",
               "Reiterated risk-based expectations; did not direct the Bank to exit"]],
             (0.9, 2.1, 3.0)),
            ("h", "3. AUSTRAC's position (as recorded)"),
            ("p", "Across the engagement, AUSTRAC's consistent position was that Coral Coast "
                  "Bank should STRENGTHEN its risk-based systems, monitoring rules and "
                  "enhanced due diligence for the segment, and remediate identified gaps. "
                  "AUSTRAC did not direct, request or require the Bank to exit the segment. "
                  "AUSTRAC's published guidance discourages wholesale de-banking and "
                  "encourages entities to manage risk on a risk basis."),
            ("note", "Management interpretation (for the Committee's awareness): management "
                     "has interpreted AUSTRAC's heightened scrutiny as supporting a decision "
                     "to EXIT the segment. On the record summarised above, AUSTRAC asked the "
                     "Bank to MANAGE and STRENGTHEN - not to exit. The Committee may wish to "
                     "test whether exit is a proportionate response to what AUSTRAC actually "
                     "requested."),
            ("h", "4. Relevant enforcement context"),
            ("p", "Management has cited landmark AUSTRAC enforcement outcomes (including the "
                  "2018 and 2020 major-bank civil penalties) as evidence of the cost of "
                  "getting financial-crime compliance wrong. Those matters concerned failures "
                  "to MONITOR and REPORT (for example IFTI-reporting and transaction-"
                  "monitoring failures) - i.e. failures to manage risk that was being "
                  "banked - rather than a failure to exit customers. They support investment "
                  "in effective monitoring at least as much as they support exit."),
        ],
    )


def gen_05_community_impact():
    build(
        os.path.join(ROOT, "05-customer-community-impact.pdf"),
        "Coral Coast Bank - Customer & Community Impact Profile",
        "CCB-BB-CIP-2026", "INTERNAL - Business Banking",
        "Customer & Community Impact Profile",
        "Who the RSP/DCE segment serves - provided for the exit decision",
        [
            ("docctl", [
                ("Document", "Customer & Community Impact Profile - RSP/DCE"),
                ("Owner", "Head of Business Banking"),
                ("Classification", "INTERNAL"),
                ("Version", "1.0"),
                ("Date", "6 August 2026"),
            ]),
            ("h", "1. Purpose"),
            ("p", "This profile describes the customers in the RSP/DCE segment and the "
                  "end-communities they serve, as factual context for the exit decision. It "
                  "does not itself assess fairness, inclusion or discrimination impacts (see "
                  "Section 5)."),
            ("h", "2. Who the remittance customers are"),
            ("p", "The ~760 remittance-dealer customers are predominantly small, independent, "
                  "AUSTRAC-registered money-transfer businesses. Many are operated by, and "
                  "serve, specific migrant and diaspora communities, and specialise in "
                  "particular corridors."),
            ("table",
             ["Corridor served", "Approx. dealers", "End-community"],
             [["Pacific (Fiji, Samoa, Tonga, PNG)", "~210", "Pacific-Islander diaspora; family support"],
              ["South Asia (India, Pakistan, Sri Lanka, Bangladesh)", "~230", "South-Asian diaspora"],
              ["South-East Asia (Philippines, Vietnam)", "~170", "South-East-Asian diaspora"],
              ["Africa (Somalia, Ethiopia, Nigeria)", "~90", "African diaspora; some fragile states"],
              ["Other / mixed", "~60", "Various"]],
             (2.4, 1.1, 2.5)),
            ("h", "3. Why this matters to end-users"),
            ("b", ["For many diaspora families, these dealers are the primary or only "
                   "practical means of sending money to relatives overseas.",
                   "Several corridors (e.g. Pacific, parts of Africa) are already served by "
                   "few providers; losing bank access can remove a corridor entirely.",
                   "Remittances to these regions are a significant source of household income "
                   "and are the subject of national and international financial-inclusion "
                   "commitments (e.g. reducing the cost of remittances)."]),
            ("h", "4. The digital-currency customers"),
            ("p", "The ~90 DCE customers are AUSTRAC-registered exchanges providing fiat "
                  "on/off-ramp services. They range from small startups to established "
                  "operators with mature compliance functions."),
            ("h", "5. Impact assessment status"),
            ("note", "No financial-inclusion impact assessment and no anti-discrimination "
                     "impact assessment have been undertaken for the proposed exit. Because "
                     "the affected customers and end-communities are strongly correlated with "
                     "particular national-origin and ethnic groups, an exit that is not "
                     "individually risk-assessed may have an indirect discriminatory effect "
                     "and a disproportionate financial-inclusion impact. These matters are "
                     "flagged in the Legal Advice Extract (Attachment 06) and have not been "
                     "assessed in this profile."),
        ],
    )


def gen_06_legal_advice():
    build(
        os.path.join(ROOT, "06-legal-advice-extract.pdf"),
        "Coral Coast Bank - Legal Advice Extract",
        "CCB-LGL-2026-114", "PRIVILEGED & CONFIDENTIAL - Legal",
        "Legal Advice Extract",
        "De-banking, notice, and anti-discrimination considerations - RSP/DCE exit",
        [
            ("docctl", [
                ("Document", "Legal Advice Extract - proposed RSP/DCE exit"),
                ("From", "General Counsel's office"),
                ("Privilege", "Legal professional privilege asserted"),
                ("Classification", "PRIVILEGED & CONFIDENTIAL"),
                ("Version", "Extract for RCC"),
                ("Date", "8 August 2026"),
            ]),
            ("h", "1. Scope of this advice"),
            ("p", "This extract summarises legal considerations relevant to the proposed exit "
                  "of the RSP/DCE segment. It is provided to assist the Committee and is not "
                  "a complete statement of the advice."),
            ("h", "2. De-banking guidance (AUSTRAC and CoFR)"),
            ("p", "AUSTRAC guidance and the Council of Financial Regulators' work on "
                  "de-banking discourage wholesale, sector-wide account closures and "
                  "encourage a risk-based approach. Recommended practice, where a bank does "
                  "decide to close accounts, includes giving clear written reasons, "
                  "reasonable prior notice, a fair and transparent process, and a mechanism "
                  "for the decision to be reviewed. A blanket exit on 30 days' notice with a "
                  "generic reason and no review mechanism is not aligned with this guidance."),
            ("h", "3. Contractual and notice considerations"),
            ("b", ["Account terms permit closure on notice, but unfair-contract-term and "
                   "general fairness considerations apply, particularly for small-business "
                   "customers.",
                   "30 days may be insufficient for customers to establish alternative "
                   "banking, especially where few providers serve a corridor.",
                   "A generic 'commercial decision' reason may not satisfy fairness "
                   "expectations for affected small businesses."]),
            ("h", "4. Anti-discrimination considerations"),
            ("p", "Because the affected customers and their end-communities correlate "
                  "strongly with particular national-origin and ethnic groups, a uniform "
                  "exit that is not individually risk-justified could give rise to a claim of "
                  "INDIRECT discrimination under the Racial Discrimination Act 1975 (Cth) - "
                  "that is, a condition or practice that is the same for everyone but has a "
                  "disproportionate adverse effect on people of a particular national origin "
                  "or ethnicity, and which may not be reasonable in the circumstances. A "
                  "documented, individualised risk basis for each closure materially reduces "
                  "this exposure."),
            ("h", "5. Recommendations"),
            ("num", ["Adopt a risk-based, individually-assessed approach rather than a "
                     "blanket exit; retain customers whose risk can be managed within "
                     "appetite.",
                     "If closing accounts, provide clear individual reasons, extended notice "
                     "(e.g. 90 days), and a review mechanism.",
                     "Undertake a financial-inclusion and anti-discrimination impact "
                     "assessment before proceeding.",
                     "Document the risk basis for each closure decision."]),
            ("note", "Status: these recommendations are NOTED by management and marked 'to be "
                     "considered' but have not been adopted in the current proposal, which "
                     "recommends a blanket exit on 30 days' notice (see Attachment 01). The "
                     "divergence between this advice and the proposal is unresolved at the "
                     "date of this paper."),
        ],
    )


def gen_07_offboarding_plan():
    build(
        os.path.join(ROOT, "07-offboarding-communications-plan.pdf"),
        "Coral Coast Bank - Offboarding & Communications Plan",
        "CCB-BB-OCP-2026", "INTERNAL - Business Banking / Communications",
        "Customer Offboarding & Communications Plan",
        "Operational plan for the proposed RSP/DCE segment exit",
        [
            ("docctl", [
                ("Document", "Offboarding & Communications Plan - RSP/DCE"),
                ("Owner", "Business Banking Operations; Corporate Affairs"),
                ("Classification", "INTERNAL"),
                ("Version", "0.9 (draft for RCC)"),
                ("Date", "10 August 2026"),
            ]),
            ("h", "1. Overview"),
            ("p", "This plan describes how the segment exit would be executed if approved. It "
                  "covers notice, customer communications, and account run-down."),
            ("h", "2. Notice and timeline"),
            ("table",
             ["Stage", "Timing", "Action"],
             [["T-0", "Sep 2026", "Approval; issue closure notices"],
              ["T+0 to T+30", "Sep-Oct 2026", "30-day notice period; accounts remain open"],
              ["T+30", "Oct 2026", "Accounts closed; balances returned"],
              ["T+30+", "Nov 2026", "Residual transaction handling; complaints"]],
             (0.9, 1.5, 3.6)),
            ("h", "3. Customer communication"),
            ("b", ["A standard closure letter will be sent to all ~850 customers.",
                   "The stated reason will be that the Bank has 'made a commercial decision "
                   "to cease providing banking services to this sector'.",
                   "No individual, customer-specific reasons will be provided.",
                   "No review or appeal mechanism is offered.",
                   "No transition assistance or referral to alternative providers is "
                   "included in the current plan."]),
            ("note", "The communications approach (uniform generic reason, 30-day notice, no "
                     "review mechanism, no transition support) does not reflect the de-"
                     "banking transparency practices recommended by AUSTRAC/CoFR or the "
                     "recommendations in the Legal Advice Extract (Attachment 06)."),
            ("h", "4. Complaints handling"),
            ("p", "Complaints will be handled through the Bank's standard internal dispute "
                  "resolution process. Given the volume and the community sensitivity, an "
                  "elevated complaint rate and possible external-dispute-resolution (AFCA) "
                  "referrals and media interest should be expected. No segment-specific "
                  "complaints or vulnerable-customer protocol is included in this draft."),
            ("h", "5. Operational risks"),
            ("table",
             ["Risk", "Note"],
             [["Customer hardship", "Short notice; corridors with few alternative providers"],
              ["Complaint / AFCA volume", "No dedicated handling in current plan"],
              ["Reputational / media", "Community and advocacy attention likely"],
              ["Regulator interest", "Possible AUSTRAC/ASIC interest in the exit approach"]],
             (2.0, 4.0)),
        ],
    )


def gen_08_amlctf_program():
    build(
        os.path.join(ROOT, "08-amlctf-program-extract.pdf"),
        "Coral Coast Bank - AML/CTF Program Extract",
        "CCB-FC-PROG-2026", "CONFIDENTIAL - Financial Crime",
        "AML/CTF Program Extract",
        "Segment control capability - extract provided for the exit decision",
        [
            ("docctl", [
                ("Document", "AML/CTF Program Extract (Part A) - segment controls"),
                ("Owner", "Money Laundering Reporting Officer (MLRO)"),
                ("Classification", "CONFIDENTIAL"),
                ("Version", "Extract"),
                ("Date", "4 August 2026"),
            ]),
            ("h", "1. Purpose"),
            ("p", "This extract summarises the Bank's existing AML/CTF control capability as "
                  "it applies to the RSP/DCE segment, to inform whether the segment's risk "
                  "can be MANAGED as an alternative to exit."),
            ("h", "2. Program components (Part A)"),
            ("table",
             ["Component", "In place?", "Note"],
             [["Board / senior-management oversight", "Yes", "AML/CTF governance via MLRO to CRO"],
              ["ML/TF risk assessment", "Yes", "Segment assessed (Attachment 02)"],
              ["Applicable customer identification (KYC)", "Yes", "At onboarding"],
              ["Enhanced due diligence (higher-risk)", "Yes", "Applied to RSP/DCE"],
              ["Ongoing customer due diligence", "Yes", "Periodic review by risk rating"],
              ["Transaction monitoring", "Yes", "Automated; sector-tuned rules"],
              ["Suspicious matter reporting (SMR)", "Yes", "Established pathway"],
              ["IFTI / threshold reporting", "Yes", "Automated lodgement"],
              ["Sanctions screening", "Yes", "Screened against DFAT Consolidated List"],
              ["Independent review", "Yes", "Periodic; last completed 2025"],
              ["Employee due diligence & training", "Yes", "Role-based"]],
             (2.3, 0.9, 2.8)),
            ("h", "3. What an uplift would involve"),
            ("p", "Meeting the Tranche-2 standard for the segment would involve tuning "
                  "monitoring rules to updated typologies, increasing analyst capacity, "
                  "strengthening EDD for the highest-risk customers, and enhancing "
                  "independent assurance. These are extensions of an EXISTING and operating "
                  "capability, not the establishment of a new one."),
            ("note", "The Bank already operates a full AML/CTF control set for the segment, "
                     "including transaction monitoring, an SMR pathway, IFTI reporting and "
                     "sanctions screening. This supports the view that the segment's risk is "
                     "capable of being MANAGED (at a cost), and is relevant to whether a "
                     "blanket exit is a proportionate response.")
        ],
    )


def gen():
    gen_01_board_paper()
    gen_02_segment_risk_assessment()
    gen_03_cost_impact()
    gen_04_austrac_engagement()
    gen_05_community_impact()
    gen_06_legal_advice()
    gen_07_offboarding_plan()
    gen_08_amlctf_program()


if __name__ == "__main__":
    gen()
    print("done")
