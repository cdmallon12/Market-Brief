# Manual data dependencies — The Standpoint Brief

_Last reviewed: Sep 1, 2026 (both optional-polish items now automated)_

This is the maintenance map for the brief. It separates what updates **itself** from
what a person has to update **by hand**, and — for each manual item — says where the
number comes from, how often it changes, and whether automation is realistically
available.

The design principle behind the site is that **the page always renders**: every value
lives in `data/fallback.json` as a last-known-good snapshot, and the live fetchers
overlay on top of it when they succeed. So a "manual" item is never broken — it just
shows the snapshot value until someone edits it.

---

## 1. What is already automated (no upkeep needed)

These refresh on every build from free, keyless (or free-key) sources. You never touch them.

| Data | Source | How it refreshes |
|---|---|---|
| S&P 500, Nasdaq, Dow, VIX | Yahoo Finance (intraday, ~15-min delay) → Stooq EOD fallback | Every build |
| Treasury yield curve (1M–30Y) + **all** curve spreads (2s10s, 3M-10Y, 5s30s, 10s30s) | U.S. Treasury par-yield feed | Every build |
| SOFR | NY Fed API | Every build |
| Fed funds target, CPI + core CPI, core PCE, **headline PCE**, real GDP | FRED API | Every build (needs `FRED_API_KEY`) |
| IG (BBB) & HY option-adjusted spreads | ICE BofA via FRED | Every build |
| CRE loan delinquency, bank CRE loans outstanding, SLOOS lending standards | FRED | Every build |
| **Brent & WTI crude** | EIA via FRED (`DCOILBRENTEU`, `DCOILWTICO`) | Every build |
| **30-yr mortgage rate** | Freddie Mac PMMS via FRED (`MORTGAGE30US`) | Every build |
| **Illustrative agency coupon** | Derived: live 10Y + 135–165 bp | Every build |
| **IMF global GDP growth** (current + next year) | IMF DataMapper API — WEO, keyless (`NGDP_RPCH`/`WLD`) | Every build |
| **Refinancing-gap "new loan" rate** | Derived: live 10Y + 150 bp | Every build |
| Watchlist (KRE, VNQ, BXP, PLD, VMRK, SPG) | Yahoo intraday → Stooq EOD fallback | Every build |
| Market & CRE headlines | RSS newswires | Every build |
| "Today's catalysts" card | `data/calendar.json` (date-matched) | Every build, from the calendar file |

Everything the two CLA briefs contributed on the **rates/energy/mortgage** side is now
in this automated tier — Brent, WTI, headline PCE, the mortgage rate, and the extra
curve spreads all pull live.

---

## 2. What is maintained by hand

Ordered roughly by how often it needs a look. Each item lives in `data/fallback.json`
unless noted; edit the value there and rebuild (or let the next scheduled build pick it up).

### Monthly

**CMBS distress & delinquency table** — `cmbs_distress`
- **Numbers:** overall / office / multifamily / lodging CMBS delinquency (Trepp), plus
  CRED iQ overall & office distress rates.
- **Source:** Trepp's monthly delinquency press release; CRED iQ's monthly distress blog post.
- **Cadence:** monthly, first week or two of the month for the prior month.
- **Automation:** **Not available cleanly.** Both are proprietary. Trepp publishes a
  monthly press release (scrapeable in principle, but the layout shifts and it brushes
  against terms-of-use); CRED iQ is a blog post with no API. Recommend keeping this
  manual — it's ~6 numbers once a month. Update `as_of` when you do.

### Quarterly

**Market fundamentals — cap rates, vacancy, supply** — `cre_market_tiles` + `cre_market.note`
- **Numbers:** multifamily cap rate (5.2%), all-property cap rate (6.3%), MF national
  vacancy (8.2%), 2026 MF deliveries (421K).
- **Source:** CBRE Cap Rate Survey / U.S. Real Estate Figures; CoStar multifamily reports.
- **Cadence:** quarterly.
- **Automation:** **Not available.** CBRE and CoStar are subscription/proprietary with no
  free API. This tier is inherently manual. Update the `note` and `as_of` line when the
  numbers move.

**MBA originations forecast** — `orig` chart (`y2025`, `y2026`) + the "+27%" figure in the CRE card
- **Source:** MBA CREF quarterly forecast press releases.
- **Cadence:** revised roughly quarterly.
- **Automation:** **Not available** (PDF/press-release only). Low churn — a quick edit a
  few times a year.

**2026 CRE maturities** — `cre_tiles[3]` ($936B) and the `cre.callout` ($1T/yr through 2030)
- **Source:** MSCI / MBA / Trepp maturity research.
- **Cadence:** annual research, occasionally revised.
- **Automation:** **Not available** (research reports). Rarely changes.

### Occasional / event-driven

**CME FedWatch odds** — the "~59% odds of a hold" note on `economy_tiles[0]` and in the economy body
- **Source:** CME FedWatch tool.
- **Cadence:** moves daily, but you only need to refresh it around FOMC-relevant news.
- **Automation:** **Effectively not available for free.** The probabilities are derived
  from Fed funds futures pricing; CME's tool has no free API, and recomputing it from raw
  futures is complex and error-prone. Keep manual; update near FOMC meetings.

**The refinancing-gap chart** — `gap` (`expiring` / `new`)
- **`new` is now automated** — it tracks the live agency-coupon midpoint (10Y + 150 bp),
  so the "new CRE loan" bar moves with rates on every build.
- **`expiring` stays manual/illustrative** (4.76) — it represents the average coupon on
  debt originated years ago, which no live feed provides. Edit occasionally if the
  vintage of maturing debt shifts. Note: the prose in the CRE card ("priced near ~6.24%")
  is illustrative and worded loosely, so a small chart/prose drift is expected and fine;
  refresh that sentence if rates move materially.

**Editorial narrative** — headlines, standfirsts, the "rotation" card, the energy/Hormuz
`energy.note`, the `cre.card_body` and callouts.
- These are written prose, not data. Inherently manual — refresh when the story changes.
- The `energy.note` in particular asserts a specific geopolitical cause (Hormuz); revisit
  it when the oil narrative shifts so it doesn't go stale against the live Brent/WTI numbers.

### Once a year

**Economic-release calendar** — `data/calendar.json`
- **Contents:** BLS (CPI/PCE), BEA (GDP), and FOMC meeting dates that drive the
  "Today's catalysts" card.
- **Cadence:** set it once a year from the published BLS/BEA/Fed schedules.
- **Automation:** **Possible but not worth it** — the agencies post annual schedules; a
  once-a-year manual refresh is simpler and less fragile than scraping three calendars.

---

## 3. Bottom line & suggested next steps

- **Nothing rate-, energy-, or mortgage-related is manual anymore** — the CLA-brief
  additions all landed in the automated tier.
- **The genuinely manual, genuinely un-automatable items are the proprietary CRE
  research feeds:** Trepp/CRED iQ (monthly), CBRE/CoStar (quarterly), MBA (quarterly).
  There is no free API for any of them; a paid data subscription would be the only path
  to automating these, and it likely isn't worth it for a handful of numbers.
- **Realistic cadence to keep the site current by hand:** ~10 minutes once a month
  (CMBS table), ~15 minutes once a quarter (cap rates / vacancy / MBA), plus a glance at
  the FedWatch odds and editorial narrative around FOMC dates.
- **The two optional-polish items are now done:** IMF global GDP pulls live from the IMF
  DataMapper API, and the refinancing-gap "new loan" rate tracks the live agency coupon.
  Nothing further is worth automating — what remains manual is manual because there is no
  free source for it, not for lack of wiring.
