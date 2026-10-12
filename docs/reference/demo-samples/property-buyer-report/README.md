# Property Buyer Report — sample run

A sample for the **`property-buyer-report`** ecosystem
(`ecosystems/property-buyer-report.yaml`). Unlike the document-upload demos, this
ecosystem takes a **single text input — a property address** — and the agents research
public Australian sources themselves (council/state planning portals, ABS, listing
sites, state flood/bushfire maps, Land Titles authorities) to produce a buyer-oriented
due-diligence report as a self-contained **HTML artifact**.

Because the report is built from live public data about a *real* address, this sample
uses a **placeholder address** rather than naming a specific property — substitute your
own address of interest when you run it.

## How to run

In the frontend, open **Property Buyer Report** → **Run**, and provide:

- **address:** `<PROPERTY_ADDRESS>` — a full Australian residential address, e.g.
  `<unit/street number> <street name>, <suburb> <STATE> <postcode>`.

That single input is enough; the research agents fan out from the address. No file
uploads are required.

> Replace `<PROPERTY_ADDRESS>` with a real address when you run it. All example output
> described below is **illustrative only** — the actual report is generated live from
> public sources for the address you supply.

## The agents (how the report is assembled)

Five research agents run in parallel, a synthesizer reconciles them, and a drafter
renders the HTML:

| Agent | Contributes |
|-------|-------------|
| **Property Facts Researcher** | Lot/land/building area, year built, beds/baths/car, construction, zoning, council rates, recorded approvals, heritage overlay — from council / state planning portals (e.g. NSW ePlanning, VIC VicPlan, QLD DA Tracker). |
| **Neighborhood Analyst** | School catchments/ratings, transport, amenities, commute, and ABS suburb demographics (population, median age, income, SEIFA). |
| **Market & Comps Analyst** | Suburb medians, 12-month / 5-year growth, days-on-market, clearance rate, rental yield, and 3–6 recent comparable sales → an indicative value range. |
| **Risk Scanner** | Flood, bushfire-prone / BAL, coastal/erosion, contamination (state EPA registers), and local crime stats — each with severity, source, and date. |
| **Title & Legal Flags** | Relevant Land Titles authority (NSW LRS, VIC Landata, QLD Titles Registry), likely encumbrances/easements/covenants, strata/heritage/zoning overlays, and the official searches a conveyancer should order. |
| **Buyer Report Synthesizer** | Reconciles all findings, resolves conflicts, preserves citations, and structures the narrative. |
| **HTML Report Drafter** | Renders a single self-contained HTML file with a disclaimer. |

## Expected shape of the outcome (illustrative — not a script)

The downloadable **`property_buyer_report.html`** should contain, for the address you
supply:

- **Executive summary** (3–5 sentences) and a snapshot bar (beds / baths / car / land /
  indicative value range).
- **Property** — physical and administrative facts, with sources.
- **Neighborhood** — schools, transport, amenities, demographics.
- **Market & Comps** — medians, growth, a comparable-sales table, and an indicative
  value range with reasoning.
- **Risks** — a ranked register (flood / bushfire / contamination / crime), each cited
  and dated, with "no public evidence found" stated where that is the case.
- **Title & Legal** — likely encumbrances/overlays and the official searches a buyer's
  conveyancer should order (public vs paid-search clearly separated).
- **Pros**, **Cons**, **Open questions to ask**, and a **Next steps** checklist
  (building & pest inspection, conveyancer searches, strata report, etc.).
- **Sources / footnotes** — every claim cited with a URL.

Quality features are on: the report is **grounded** (claims cited, gaps flagged rather
than guessed) and **adversarially challenged** before it is finalized.

## Disclaimer

The report is an **informational summary only — not legal, financial, or valuation
advice**. Formal title searches, a conveyancer review, and building & pest inspections
are required before any purchase decision. Where a fact is not publicly available, the
agents state so rather than inferring it.
