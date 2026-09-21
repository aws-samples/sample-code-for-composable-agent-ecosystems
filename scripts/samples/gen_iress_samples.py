#!/usr/bin/env python3
"""Generate a realistic, holistic client document pack for the iress-adviser-copilot
ecosystem — the fictional pre-retiree client John Doe.

Profile (kept CONSISTENT across every document):
  - John Andrew Doe, DOB 14/03/1965 (age 60), married to Jane Louise Doe (58).
  - 2 adult, financially-independent children (non-dependants).
  - Home: 42 Marina Crescent, Mosman NSW 2088 — value $2,000,000, OWNED OUTRIGHT.
  - Direct ASX share portfolio ~$200,000 (CommSec), CBA-heavy (concentration).
  - Super (AustralianSuper, Balanced) $685,000 accumulation; Jane $360,000.
  - Cash ~$157,400; salary $145,000 + SG; wants to retire ~65 on ~$85k p.a. (couple).

Produces (samples/iress-adviser-copilot/john-doe/):
  01-xplan-fact-find.pdf            — adviser fact find / Xplan-style export
  02-investment-portfolio-statement.pdf
  03-superannuation-member-statement.pdf
  04-bank-statement.pdf             — everyday account, ~2 months of transactions
  05-insurance-schedule.pdf         — life / TPD / income protection (in super)
  06-tax-notice-of-assessment.pdf   — ATO NoA 2024-25
  07-prior-record-of-advice.pdf     — a prior ROA (adds history + estate context)
  README.md                         — what the pack is + expected copilot behaviour

Run:  source .venv/bin/activate && python3 scripts/gen_iress_samples.py
Deps: fpdf2
"""
import os
from fpdf import FPDF

OUT = os.path.join(os.path.dirname(__file__), "..", "samples", "iress-adviser-copilot", "john-doe")

_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'", "\u201c": '"',
    "\u201d": '"', "\u20ac": "EUR ", "\u00d7": "x", "\u2265": ">=", "\u2264": "<=",
    "\u2192": "->", "\u2194": "<->", "\u2022": "-", "\u2026": "...", "\u00a0": " ",
    "\u00b7": "-",
}


def _s(t) -> str:
    t = str(t)
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
    pdf.set_text_color(30, 50, 90)
    pdf.multi_cell(0, 6, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1)


def _p(pdf, text):
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _note(pdf, text):
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(110)
    pdf.multi_cell(0, 5, _s(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1)


def _bullets(pdf, items):
    pdf.set_font("Helvetica", "", 10)
    for it in items:
        pdf.multi_cell(0, 5, _s("- " + it), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _kv(pdf, pairs, label_w=58):
    for k, v in pairs:
        y = pdf.get_y()
        x = pdf.l_margin
        pdf.set_xy(x, y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 5, _s(k))
        pdf.set_xy(x + label_w, y)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _s(v), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _table(pdf, headers, rows, widths, aligns=None):
    aligns = aligns or ["L"] * len(headers)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_fill_color(230, 236, 246)
    pdf.set_draw_color(180)
    for i, hdr in enumerate(headers):
        pdf.cell(widths[i], 6, _s(hdr), border=1, align=aligns[i], fill=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 8.5)
    fill = False
    for row in rows:
        # page-break guard
        if pdf.get_y() > pdf.h - 22:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_fill_color(230, 236, 246)
            for i, hdr in enumerate(headers):
                pdf.cell(widths[i], 6, _s(hdr), border=1, align=aligns[i], fill=True)
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 8.5)
        pdf.set_fill_color(247, 249, 252)
        for i, cell in enumerate(row):
            pdf.cell(widths[i], 5.6, _s(cell), border="LR", align=aligns[i], fill=fill)
        pdf.ln(5.6)
        fill = not fill
    pdf.set_draw_color(180)
    pdf.cell(sum(widths), 0, "", border="T")
    pdf.ln(3)


def build(path, meta, docref, title, subtitle, classification, blocks):
    pdf = Doc()
    pdf._meta = meta
    pdf._docref = docref
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    _title(pdf, title, subtitle, classification)
    for b in blocks:
        k = b[0]
        if k == "h":
            _h(pdf, b[1])
        elif k == "p":
            _p(pdf, b[1])
        elif k == "note":
            _note(pdf, b[1])
        elif k == "b":
            _bullets(pdf, b[1])
        elif k == "kv":
            _kv(pdf, b[1])
        elif k == "table":
            _table(pdf, b[1], b[2], b[3], b[4] if len(b) > 4 else None)
        elif k == "sp":
            pdf.ln(b[1] if len(b) > 1 else 2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf.output(path)
    print("wrote", os.path.relpath(path, os.path.join(os.path.dirname(__file__), "..")))


# ── 01. Xplan-style client fact find ──────────────────────────────────────────
def fact_find():
    build(
        os.path.join(OUT, "01-xplan-fact-find.pdf"),
        "Harbourline Financial Advice - Client Fact Find (Xplan export)", "01-xplan-fact-find.pdf",
        "Client Fact Find & Adviser Data Export",
        "Prepared for adviser meeting preparation - John & Jane Doe",
        "PRIVATE & CONFIDENTIAL - contains personal financial information",
        [
            ("h", "1. Personal details"),
            ("kv", [
                ("Client 1", "John Andrew Doe"),
                ("Date of birth", "14/03/1965  (age 60)"),
                ("Marital status", "Married to Jane Louise Doe (DOB 22/09/1966, age 58)"),
                ("Residency", "Australian resident for tax purposes"),
                ("Address", "42 Marina Crescent, Mosman NSW 2088"),
                ("Contact", "john.doe@example.com  -  0412 555 018"),
                ("Health / smoker", "Good health, non-smoker"),
                ("Client reference", "HFA-0472  -  Adviser: Sarah Nguyen (AR 1002345)"),
                ("Last review", "20/11/2024"),
                ("Review cadence", "Annual (currently overdue)"),
            ]),
            ("h", "2. Dependants"),
            ("b", [
                "Emily Doe, age 28 - financially independent, lives interstate (non-dependant).",
                "Thomas Doe, age 25 - financially independent, recently purchased first home (non-dependant).",
                "No dependent children or persons with special needs.",
            ]),
            ("h", "3. Employment & income (household)"),
            ("kv", [
                ("John - occupation", "Operations Manager, Harbourline Logistics Pty Ltd (PAYG, full-time)"),
                ("John - gross salary", "$145,000 p.a. + Superannuation Guarantee"),
                ("John - salary sacrifice", "$10,000 p.a. to superannuation"),
                ("Jane - occupation", "Bookkeeper (part-time, 3 days/week)"),
                ("Jane - gross salary", "$48,000 p.a. + Superannuation Guarantee"),
                ("Investment income", "~$8,900 p.a. fully-franked dividends (John's portfolio)"),
                ("Household gross income", "~$202,000 p.a."),
            ]),
            ("h", "4. Goals & objectives"),
            ("b", [
                "Transition to part-time work at age 62; fully retire at 65.",
                "Target retirement income ~$85,000 p.a. (couple, today's dollars), indexed.",
                "Travel: 6-8 weeks overseas per year in early retirement.",
                "Assist each adult child with ~$50,000 toward a home deposit within 5 years.",
                "Ensure Jane is financially secure and estate passes tax-effectively to children.",
                "Reduce reliance on a single dominant share holding (concerned about concentration).",
            ]),
            ("h", "5. Risk profile"),
            ("kv", [
                ("Assessed profile", "Balanced to Growth (7 / 10)"),
                ("Questionnaire date", "20/11/2024"),
                ("Notes", "Comfortable with market volatility on the long-term portion; wants a cash "
                          "buffer of ~2 years' expenses as retirement approaches."),
            ]),
            ("h", "6. Assets & liabilities summary"),
            ("table",
             ["Item", "Owner", "Value (A$)", "Notes"],
             [
                 ["Principal residence - 42 Marina Cres, Mosman", "Joint", "2,000,000", "Owned outright, no mortgage"],
                 ["ASX share portfolio (CommSec)", "John", "199,575", "See portfolio statement"],
                 ["Superannuation - AustralianSuper", "John", "685,000", "Accumulation, Balanced option"],
                 ["Superannuation - Rest Super", "Jane", "360,000", "Accumulation"],
                 ["Cash - ING Savings Maximiser", "Joint", "95,000", "Emergency / buffer"],
                 ["Cash - Term deposit (CBA, 6mo)", "Joint", "50,000", "Matures 09/2026"],
                 ["Cash - Everyday transaction acct", "Joint", "12,400", "Day-to-day"],
                 ["Motor vehicles (2)", "Joint", "62,000", "Personal use"],
                 ["Total assets", "", "3,463,975", "No liabilities"],
             ],
             [92, 16, 26, 46], ["L", "L", "R", "L"]),
            ("kv", [("Total liabilities", "$0 - the family is debt-free (home and cars owned outright).")]),
            ("h", "7. Superannuation detail (John)"),
            ("kv", [
                ("Fund / option", "AustralianSuper - Balanced (MySuper)"),
                ("Member number", "AS-338 201 774"),
                ("Balance", "$685,000 (100% preserved, accumulation phase)"),
                ("Preservation status", "Preservation age reached (born 1965 -> preservation age 60)"),
                ("Concessional contributions YTD", "$26,675 (SG $16,675 + salary sacrifice $10,000)"),
                ("Non-concessional YTD", "$0"),
                ("Beneficiary nomination", "NON-BINDING - Jane Doe 100% (lodged 2019, not reconfirmed)"),
                ("Insurance in super", "Life $250,000 / TPD $150,000 / Income Protection $6,000 p.m."),
            ]),
            ("h", "8. Estate planning"),
            ("b", [
                "Will: dated 06/2015 (not reviewed since) - mirror wills, estate to spouse then children equally.",
                "Enduring Power of Attorney: in place for both (2015).",
                "Enduring Guardianship / Advance Care Directive: not in place.",
                "Superannuation death benefit nomination is non-binding and 6 years old.",
                "No testamentary trust; no binding financial agreement.",
            ]),
            ("h", "9. Adviser notes"),
            ("note", "Client raised concentration risk in CBA and a large idle cash balance. Approaching "
                     "preservation age - transition-to-retirement and contribution strategies to be explored. "
                     "Review is ~20 months since last completed and several items (beneficiary nomination, will, "
                     "risk profile) are stale. This document is a data capture for meeting prep, not advice."),
        ],
    )


# ── 02. Investment portfolio statement ────────────────────────────────────────
def portfolio():
    holdings = [
        ["CBA", "Commonwealth Bank of Aust.", "Aust. Equity", "350", "128.50", "44,975", "22.5"],
        ["VAS", "Vanguard Aust. Shares ETF", "Aust. Equity ETF", "380", "99.50", "37,810", "18.9"],
        ["BHP", "BHP Group Ltd", "Aust. Equity", "600", "44.20", "26,520", "13.3"],
        ["CSL", "CSL Limited", "Aust. Equity", "90", "290.00", "26,100", "13.1"],
        ["WES", "Wesfarmers Ltd", "Aust. Equity", "300", "68.00", "20,400", "10.2"],
        ["TLS", "Telstra Group Ltd", "Aust. Equity", "4,000", "3.95", "15,800", "7.9"],
        ["VGS", "Vanguard Global Shares ETF", "Intl Equity ETF", "120", "124.00", "14,880", "7.5"],
        ["NAB", "National Australia Bank", "Aust. Equity", "350", "37.40", "13,090", "6.6"],
    ]
    build(
        os.path.join(OUT, "02-investment-portfolio-statement.pdf"),
        "CommSec - Portfolio Holdings Statement", "02-investment-portfolio-statement.pdf",
        "Investment Portfolio Statement",
        "Account: John A Doe - CommSec Trading A/C 5029-8871  -  as at 30/06/2025",
        "Statement generated for financial advice review",
        [
            ("kv", [
                ("Account holder", "Mr John Andrew Doe"),
                ("Account number", "CommSec 5029-8871 (individual, non-super)"),
                ("Portfolio valuation date", "30 June 2025"),
                ("Total portfolio value", "$199,575"),
                ("Number of holdings", "8"),
                ("Est. gross annual income", "$8,900 (dividends), ~$3,050 franking credits attached"),
            ]),
            ("h", "Holdings"),
            ("table",
             ["Code", "Security", "Asset class", "Units", "Price $", "Value $", "Wt %"],
             holdings,
             [16, 50, 34, 20, 20, 26, 14], ["L", "L", "L", "R", "R", "R", "R"]),
            ("h", "Concentration & composition (informational)"),
            ("b", [
                "Single largest holding: CBA at 22.5% of portfolio (well above a typical 10% single-name guideline).",
                "Financials sector (CBA + NAB) ~= 29.1% of the portfolio.",
                "Direct Australian equities ~= 73.6%; ETFs (VAS + VGS) ~= 26.4%.",
                "International exposure limited to VGS (~7.5%); portfolio is heavily domestic.",
                "All holdings are listed, liquid ASX securities. No fixed income or unlisted assets.",
            ]),
            ("note", "Prices are indicative closing prices on the valuation date. This statement lists holdings "
                     "only and does not constitute financial product advice."),
        ],
    )


# ── 03. Superannuation member statement ───────────────────────────────────────
def super_stmt():
    build(
        os.path.join(OUT, "03-superannuation-member-statement.pdf"),
        "AustralianSuper - Annual Member Statement", "03-superannuation-member-statement.pdf",
        "Superannuation Member Statement",
        "Member: John A Doe  -  Statement period 01/07/2024 to 30/06/2025",
        "Superannuation account summary",
        [
            ("kv", [
                ("Member name", "Mr John Andrew Doe"),
                ("Member number", "AS-338 201 774"),
                ("Date of birth", "14/03/1965"),
                ("Product", "AustralianSuper - Accumulation"),
                ("Investment option", "Balanced (MySuper) - 100%"),
                ("Preservation age", "60 (reached) - condition of release for TTR available"),
            ]),
            ("h", "Account movement (2024-25)"),
            ("table",
             ["Item", "Amount (A$)"],
             [
                 ["Opening balance (01/07/2024)", "628,400"],
                 ["Employer SG contributions", "16,675"],
                 ["Salary sacrifice (concessional)", "10,000"],
                 ["Personal (non-concessional) contributions", "0"],
                 ["Contributions tax (15%)", "-4,001"],
                 ["Net investment earnings", "37,126"],
                 ["Fees & insurance premiums", "-3,200"],
                 ["Closing balance (30/06/2025)", "685,000"],
             ],
             [120, 40], ["L", "R"]),
            ("h", "Preservation components"),
            ("kv", [
                ("Preserved", "$685,000 (100%)"),
                ("Restricted non-preserved", "$0"),
                ("Unrestricted non-preserved", "$0"),
                ("Tax components", "Taxable (taxed) $631,300 / Tax-free $53,700"),
            ]),
            ("h", "Concessional contributions cap usage"),
            ("kv", [
                ("Concessional contributions this year", "$26,675 (SG + salary sacrifice)"),
                ("Note", "Member should confirm the current-year concessional cap and any unused "
                         "carry-forward cap space before making additional contributions."),
            ]),
            ("h", "Insurance cover held in this account"),
            ("table",
             ["Cover type", "Sum insured / benefit", "Waiting / term"],
             [
                 ["Death (Life)", "$250,000", "n/a"],
                 ["Total & Permanent Disability", "$150,000", "n/a"],
                 ["Income Protection", "$6,000 per month", "90-day wait, benefit to age 65"],
             ],
             [55, 70, 55], ["L", "L", "L"]),
            ("h", "Beneficiary nomination"),
            ("kv", [
                ("Type", "Non-binding (preferred) nomination"),
                ("Nominated", "Jane Louise Doe - 100%"),
                ("Lodged", "2019 (non-lapsing status not applicable to non-binding nominations)"),
            ]),
            ("note", "Investment earnings are net of investment fees and taxes within the fund. Past performance "
                     "is not indicative of future performance."),
        ],
    )


# ── 04. Bank statement (everyday transaction account) ─────────────────────────
def bank_stmt():
    tx = [
        ["02/05/2025", "Opening balance", "", "", "9,120.44"],
        ["06/05/2025", "Salary - Harbourline Logistics", "", "4,190.00", "13,310.44"],
        ["07/05/2025", "Woolworths Mosman", "214.60", "", "13,095.84"],
        ["09/05/2025", "Origin Energy - electricity", "268.15", "", "12,827.69"],
        ["10/05/2025", "Transfer to ING Savings Maximiser", "1,500.00", "", "11,327.69"],
        ["12/05/2025", "Mosman Council - rates", "742.00", "", "10,585.69"],
        ["15/05/2025", "Salary - Jane (bookkeeping)", "", "1,420.00", "12,005.69"],
        ["16/05/2025", "Dan Murphy's", "96.40", "", "11,909.29"],
        ["18/05/2025", "Qantas - flights (holiday)", "1,880.00", "", "10,029.29"],
        ["20/05/2025", "Salary - Harbourline Logistics", "", "4,190.00", "14,219.29"],
        ["22/05/2025", "Bupa - private health insurance", "398.70", "", "13,820.59"],
        ["24/05/2025", "Coles Neutral Bay", "188.25", "", "13,632.34"],
        ["27/05/2025", "Restaurant - The Bathers Pavilion", "246.00", "", "13,386.34"],
        ["30/05/2025", "Ampol fuel", "112.80", "", "13,273.54"],
        ["02/06/2025", "CBA dividend (DRP cash residual)", "", "1,120.00", "14,393.54"],
        ["03/06/2025", "Salary - Harbourline Logistics", "", "4,190.00", "18,583.54"],
        ["05/06/2025", "Transfer to ING Savings Maximiser", "3,000.00", "", "15,583.54"],
        ["07/06/2025", "Woolworths Mosman", "231.10", "", "15,352.44"],
        ["10/06/2025", "AGL gas", "88.40", "", "15,264.04"],
        ["12/06/2025", "Private health / misc direct debits", "512.30", "", "14,751.74"],
        ["15/06/2025", "Salary - Jane (bookkeeping)", "", "1,420.00", "16,171.74"],
        ["17/06/2025", "Amazon AU", "164.99", "", "16,006.75"],
        ["20/06/2025", "Salary - Harbourline Logistics", "", "4,190.00", "20,196.75"],
        ["24/06/2025", "Transfer to ING Savings Maximiser", "2,000.00", "", "18,196.75"],
        ["27/06/2025", "Groceries / dining / fuel (sundry)", "1,410.00", "", "16,786.75"],
        ["30/06/2025", "Closing balance", "", "", "16,786.75"],
    ]
    build(
        os.path.join(OUT, "04-bank-statement.pdf"),
        "Commonwealth Bank - Smart Access Statement", "04-bank-statement.pdf",
        "Everyday Transaction Account Statement",
        "Account: J A & J L Doe - BSB 062-000 Acc 1122 3344  -  02/05/2025 to 30/06/2025",
        "Two-month transaction history",
        [
            ("kv", [
                ("Account name", "Mr J A Doe & Mrs J L Doe (joint)"),
                ("Account", "CBA Smart Access - BSB 062-000  Acc 1122 3344"),
                ("Statement period", "2 May 2025 - 30 June 2025"),
                ("Opening / closing balance", "$9,120.44  ->  $16,786.75"),
            ]),
            ("h", "Transactions"),
            ("table",
             ["Date", "Description", "Debit $", "Credit $", "Balance $"],
             tx,
             [22, 84, 22, 22, 30], ["L", "L", "R", "R", "R"]),
            ("h", "Two-month summary"),
            ("kv", [
                ("Total credits (salary + income)", "$26,330.00 over ~2 months (~$13,165 / month net)"),
                ("Total regular savings transfers", "$6,500 to ING Savings Maximiser"),
                ("Estimated monthly living expenses", "~$4,600 (ex-savings, ex-holiday)"),
                ("Estimated monthly surplus", "~$2,300 - $2,800 after savings transfers"),
                ("Observation", "Household runs a consistent surplus; a large one-off holiday outflow "
                                "($1,880) appears in May."),
            ]),
            ("note", "Balances are indicative for advice review. Not all direct debits are itemised."),
        ],
    )


# ── 05. Insurance schedule ────────────────────────────────────────────────────
def insurance():
    build(
        os.path.join(OUT, "05-insurance-schedule.pdf"),
        "AustralianSuper Insurance - Policy Schedule", "05-insurance-schedule.pdf",
        "Personal Insurance Schedule",
        "Life insured: John A Doe  -  cover held within superannuation",
        "Insurance cover summary",
        [
            ("kv", [
                ("Life insured", "Mr John Andrew Doe (DOB 14/03/1965)"),
                ("Held within", "AustralianSuper (member AS-338 201 774)"),
                ("Cover structure", "Group insurance inside superannuation"),
                ("Premium source", "Deducted from super account balance"),
            ]),
            ("h", "Cover held"),
            ("table",
             ["Benefit", "Sum insured", "Premium (p.a.)", "Notes"],
             [
                 ["Death (Life)", "$250,000", "$540", "Lump sum on death/terminal illness"],
                 ["Total & Permanent Disability", "$150,000", "$690", "Own-occupation not held; any-occupation"],
                 ["Income Protection", "$6,000 / month", "$1,970", "90-day wait, benefit to age 65, 75% of income"],
             ],
             [46, 34, 34, 66], ["L", "L", "R", "L"]),
            ("h", "Adequacy context (for adviser review, not advice)"),
            ("b", [
                "No outstanding mortgage or personal debt; adult children are financially independent.",
                "Income protection benefit ceases at age 65, aligned to stated retirement age.",
                "Spouse (Jane) holds separate default cover within Rest Super (details not provided).",
                "No standalone (ordinary) life, trauma, or TPD cover held outside super.",
                "Existing Death/TPD sums were set ~2016 and have not been reviewed against current needs.",
            ]),
            ("note", "Group insurance terms, definitions and exclusions are set by the fund's policy. "
                     "Confirm current definitions and any pre-existing exclusions before relying on cover."),
        ],
    )


# ── 06. ATO Notice of Assessment ──────────────────────────────────────────────
def tax_noa():
    build(
        os.path.join(OUT, "06-tax-notice-of-assessment.pdf"),
        "Australian Taxation Office - Notice of Assessment", "06-tax-notice-of-assessment.pdf",
        "Notice of Assessment",
        "Taxpayer: John Andrew Doe  -  Income year ended 30 June 2025",
        "ATO assessment summary (income tax)",
        [
            ("kv", [
                ("Taxpayer", "Mr John Andrew Doe"),
                ("Tax file number", "xxx xxx 018 (masked)"),
                ("Income year", "1 July 2024 - 30 June 2025"),
                ("Lodgment", "Lodged via registered tax agent"),
            ]),
            ("h", "Assessment"),
            ("table",
             ["Item", "Amount (A$)"],
             [
                 ["Gross salary & wages (Harbourline Logistics)", "145,000"],
                 ["Reportable employer super (salary sacrifice)", "10,000"],
                 ["Gross dividends (unfranked + franked)", "8,900"],
                 ["Franking (imputation) credits", "3,050"],
                 ["Total assessable income", "156,950"],
                 ["Less work-related & other deductions", "-2,400"],
                 ["Taxable income", "154,550"],
                 ["Income tax on taxable income", "40,183"],
                 ["Medicare levy (2%)", "3,091"],
                 ["Less PAYG withheld", "-44,600"],
                 ["Less franking credits", "-3,050"],
                 ["Estimated refund", "4,376"],
             ],
             [120, 40], ["L", "R"]),
            ("note", "Figures are illustrative for advice review and rounded. Salary-sacrifice amounts are "
                     "reportable employer super contributions and count toward the concessional cap."),
        ],
    )


# ── 07. Prior Record of Advice ────────────────────────────────────────────────
def prior_roa():
    build(
        os.path.join(OUT, "07-prior-record-of-advice.pdf"),
        "Harbourline Financial Advice - Record of Advice (prior)", "07-prior-record-of-advice.pdf",
        "Record of Advice (previous engagement)",
        "Client: John & Jane Doe  -  dated 15/03/2023  -  Adviser: Sarah Nguyen",
        "Historical advice document - provided as background",
        [
            ("h", "Scope of prior advice (2023)"),
            ("b", [
                "Confirmed John's risk profile as Balanced to Growth (7/10) following questionnaire.",
                "Recommended commencing $10,000 p.a. salary sacrifice into AustralianSuper.",
                "Recommended consolidating a small legacy super account into AustralianSuper (completed).",
                "Retained existing insurance inside super pending a later needs review.",
                "Recommended establishing a 12-month expense cash buffer (since built up in ING account).",
            ]),
            ("h", "Matters explicitly deferred at the time"),
            ("b", [
                "Share portfolio concentration (CBA) - client wished to defer for capital-gains reasons.",
                "Transition-to-retirement strategy - to be revisited when John neared preservation age (60).",
                "Updating the superannuation death benefit nomination to a binding nomination.",
                "Estate documents (will/EPOA) review - last completed 2015.",
            ]),
            ("h", "Best interests & compliance record (2023)"),
            ("kv", [
                ("Basis of advice", "Best Interests Duty safe-harbour steps documented (s961B)"),
                ("Standing disclaimer", "Prior advice was personal advice under an SoA/RoA; superseded by any "
                                        "new advice. Provided here only as client history."),
            ]),
            ("note", "This is a historical record supplied for context. It is not current advice and several "
                     "recommendations were expressly deferred to a future review - now due."),
        ],
    )


def readme():
    txt = """# iress-adviser-copilot - sample client pack: John Doe

A realistic, **holistic** document pack for a fictional pre-retiree client, used to
demo the `iress-adviser-copilot` ecosystem end to end. Every figure is internally
consistent across the documents so the copilot's analysis, next-best-actions,
conversation script and HTML advice pack are coherent.

## The client (fictional)
- **John Andrew Doe**, age **60**, married to Jane (58); two adult, independent children.
- **Home**: 42 Marina Crescent, Mosman NSW - **$2,000,000, owned outright** (no debt).
- **Share portfolio**: ~**$200,000** direct ASX (CommSec), **CBA-heavy (22.5%)**.
- **Super**: AustralianSuper Balanced **$685,000** (John), Rest Super $360,000 (Jane).
- **Cash**: ~**$157,400** (ING savings, term deposit, everyday account).
- **Income**: salary **$145,000** + SG + $10k salary sacrifice; wants to retire ~**65** on ~**$85k p.a.** (couple).

## Documents
| File | What it is |
|---|---|
| `01-xplan-fact-find.pdf` | Adviser fact find / Xplan-style export - profile, goals, risk, assets, super, estate |
| `02-investment-portfolio-statement.pdf` | CommSec holdings statement (8 ASX holdings, CBA concentration) |
| `03-superannuation-member-statement.pdf` | AustralianSuper statement - balance, contributions, insurance, beneficiary |
| `04-bank-statement.pdf` | CBA everyday account, ~2 months - salary, expenses, savings transfers |
| `05-insurance-schedule.pdf` | Life / TPD / Income Protection held inside super |
| `06-tax-notice-of-assessment.pdf` | ATO Notice of Assessment 2024-25 |
| `07-prior-record-of-advice.pdf` | A 2023 Record of Advice (history + deferred items now due) |

## What the copilot should surface (from THIS data)
- **Concentration risk**: CBA 22.5% (financials sector ~29%); heavy domestic tilt, limited global.
- **Cash drag**: ~$157k idle cash - large relative to near-term needs.
- **Preservation age reached (60)**: transition-to-retirement and contribution-timing discussion.
- **Concessional cap headroom**: $26,675 contributed - confirm current cap / carry-forward (catalog `regulatory-figure`).
- **Stale items**: review ~20 months overdue; **non-binding** super beneficiary (2019); **will 2015**; no ACD/guardianship.
- **Insurance review**: no mortgage + independent kids - Death/TPD sums set ~2016, IP to age 65; check adequacy vs need.
- **Estate/next-gen**: goal to gift each child ~$50k; binding nomination + will update.
- Each Next-Best-Action should map to a **BID safe-harbour step** and **Code of Ethics** standard, framed as
  "points for discussion" (not personal advice), with information gaps listed - and any statutory dollar figure
  cited from the catalog `regulatory-figure` records (or flagged as a gap if the catalog does not yet hold it).

> All persons, accounts, numbers and institutions are fictional and for demonstration only.
"""
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "README.md"), "w") as f:
        f.write(txt)
    print("wrote", os.path.relpath(os.path.join(OUT, "README.md"), os.path.join(os.path.dirname(__file__), "..")))


if __name__ == "__main__":
    fact_find()
    portfolio()
    super_stmt()
    bank_stmt()
    insurance()
    tax_noa()
    prior_roa()
    readme()
    print("\nJohn Doe client pack generated in", os.path.relpath(OUT, os.path.join(os.path.dirname(__file__), "..")))
