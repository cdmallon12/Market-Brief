# Standpoint Brief — catalysts / date / delta fixes

Three files. Drop them in over the existing ones, keeping the same paths:

```
build.py                 (replaces existing)
data/fetch.py            (replaces existing)
data/calendar.json       (NEW — this file was missing from the repo)
```

Nothing in `templates/`, `docs/`, or `data/fallback.json` changes. No new
dependencies (`zoneinfo` is stdlib on Python 3.9+).

---

## 1. `data/calendar.json` was never committed

Root cause of the stale catalysts. `load_calendar()` returned `None` on the
missing file, `apply_calendar()` bailed out, and the card kept rendering the
seeded prose in `fallback.json` — which `save_snapshot()` then re-wrote after
every build, so it was pinned rather than decaying.

Seeded through **Dec 2026** for BLS (CPI, PPI, Employment Situation, JOLTS,
ECI), through **Oct 29 2026** for BEA (GDP + Personal Income/PCE), and through
**Dec 2027** for FOMC.

**Verification status**, so you know what to trust:

| Source | Verified through | Against |
| --- | --- | --- |
| BLS | Dec 15, 2026 | official BLS 2026 release calendar |
| BEA | Oct 29, 2026 | BEA release schedule |
| FOMC 2026 | Dec 9, 2026 | Fed calendar (confirmed) |
| FOMC 2027 | Dec 8, 2027 | Fed calendar (**tentative** — each date is confirmed at the preceding meeting) |

**Still to add:** BEA releases after Oct 29, 2026 (Q3 GDP second/third
estimates, October and November PCE) and all of BLS 2027. BEA publishes the
next year's schedule in the autumn; BLS publishes 2027 in late 2026. Sources
are listed in the `_sources` block inside the file.

`load_calendar()` now prints a warning instead of swallowing the error, so a
missing or malformed file shows up in the Actions log rather than silently
reverting the card.

## 2. Build dates ran on UTC

`dt.date.today()` on a GitHub runner is UTC, so any build after 8 PM ET
stamped the page with tomorrow. Meanwhile `_et_stamp()` already used Eastern
for the quote timestamp — which is why the header read Sep 02 above quotes
marked Sep 1.

Added `today_et()` and used it for the header block and for the date passed
into `apply_calendar()` (the calendar window had the same off-by-one).

Confirmed on a UTC box where `date -u` reads Sep 02 and ET reads Sep 01: the
page now stamps `Tuesday · Sep 01, 2026`.

## 3. "vs prior close" was actually year-over-year

`_yahoo_quote()` requests `range=1y&interval=1d`, then read
`meta.chartPreviousClose`. On a multi-day range Yahoo sets that field to the
close *before the requested window* — so with a 1-year range it was a
year-old price, and the daily change was silently a YoY change. That's the
+18.13% S&P figure, and the VIX reading down while it traded up on the day.

Prior close now comes from the second-to-last daily bar in the `close` array,
falling back to the meta field only if there aren't two bars. The watchlist
tiles use the same function, so they're fixed too.

---

## After committing

Run **Actions → Build daily brief → Run workflow** rather than waiting for
cron. The first successful run overwrites the stale catalysts inside
`fallback.json`, so the snapshot stops carrying August's prose.
