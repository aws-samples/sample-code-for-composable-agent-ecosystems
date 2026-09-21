# iress-adviser-copilot - sample client pack: John Doe

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
