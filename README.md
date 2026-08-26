# The Standpoint Brief

A self-refreshing daily brief on **markets, the macro economy, and commercial
real estate** — a single static page you can bookmark. Python assembles the data
into an HTML page; a scheduled GitHub Action rebuilds it every weekday and
GitHub Pages serves it. No server to run, no bill to pay.

> **Important:** this page is for personal information only — **not investment
> advice**. Values are point-in-time snapshots from public sources and can lag
> or be revised.

---

## How it works

```
 GitHub Actions (cron, weekday mornings)
        │  runs
        ▼
   build.py ──reads──►  data/fallback.json   (last-known-good snapshot)
        │   overlays live data from keyless sources:
        │     • Stooq            → index levels (S&P, Nasdaq, Dow, VIX)
        │     • US Treasury XML  → the par yield curve
        │     • NY Fed API       → SOFR
        │     • RSS feeds        → market + CRE headlines
        │   renders
        ▼
   templates/brief.html.j2  ──►  docs/index.html   ──►  GitHub Pages
```

Every live value is **optional**. If a source is unreachable, the page falls
back to the snapshot, so a single outage never produces a broken page. After a
successful build, the merged data is written back to `data/fallback.json`, so the
snapshot always reflects the most recent good run.

You asked about "building an API" — worth clarifying the mental model: you don't
host an API here. GitHub Pages only serves static files, so instead the Action
acts as a scheduled robot that **consumes** existing public data APIs and bakes a
static page. That's what keeps the whole thing free and serverless.

---

## Repository layout

```
market-brief/
├── build.py                     # assembles data + renders the page
├── requirements.txt
├── data/
│   ├── fetch.py                 # keyless data fetchers (each fails safe)
│   └── fallback.json            # last-known-good snapshot (also the seed content)
├── templates/
│   └── brief.html.j2            # the page (Jinja2 template of the design)
├── docs/
│   └── index.html               # GENERATED output — what Pages serves
└── .github/workflows/daily.yml  # the daily cron build
```

---

## Run it locally

```bash
pip install -r requirements.txt

python build.py            # fetch live data, then build docs/index.html
python build.py --offline  # build from the snapshot only (no network)
python build.py --no-save  # build without overwriting fallback.json
```

Open `docs/index.html` in a browser. The dark theme is the default; a Theme
button toggles light/dark and remembers your choice.

---

## Publish on GitHub Pages (one-time setup)

1. Create a repo and push this folder to it:
   ```bash
   git init && git add . && git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/market-brief.git
   git push -u origin main
   ```
2. In the repo: **Settings → Pages**. Set **Source: Deploy from a branch**,
   **Branch: `main`**, **Folder: `/docs`**. Save.
3. Your page goes live at `https://<you>.github.io/market-brief/` within a minute.
4. **Settings → Actions → General →** scroll to *Workflow permissions* and select
   **Read and write permissions** (lets the Action commit the rebuilt page).

That's it. The Action runs every weekday morning and updates the page in place.
You can also trigger a build any time from the **Actions** tab → *Build daily
brief* → **Run workflow**.

### Changing the schedule
Edit the `cron` line in `.github/workflows/daily.yml`. Cron is in **UTC** and does
not shift with US daylight saving. `30 11 * * 1-5` ≈ 6:30 AM Central during
summer (CDT), 5:30 AM in winter (CST).

---

## Editing content

- **Numbers, charts, tickers** come from the fetchers — they refresh themselves.
- **Editorial prose** (the section intros, the narrative cards) lives in
  `data/fallback.json` under `markets`, `economy`, `cre`, etc. Edit there and
  rebuild. HTML in those fields (e.g. `<strong>`) is rendered as-is; headline
  titles pulled from RSS are auto-escaped for safety.
- **Design** (colors, type, layout) lives entirely in the `<style>` block of
  `templates/brief.html.j2`.

---

## Data sources (all keyless in the default build)

| Data | Source | Notes |
|---|---|---|
| Index levels | [Stooq](https://stooq.com) daily CSV | `^spx`, `^ndq`, `^dji`, `^vix`; daily close + computed YoY |
| Treasury yield curve | [US Treasury XML feed](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve) | official par yields, all tenors |
| SOFR | [NY Fed markets API](https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json) | official overnight rate |
| Market headlines | CNBC RSS | newest, de-duplicated |
| CRE headlines | Commercial Observer, CRE Daily, The Real Deal, GlobeSt RSS | newest, de-duplicated |

The **global GDP** figure (IMF) is a manually maintained value in
`data/fallback.json` — the IMF publishes it only a few times a year, so update it
when a new World Economic Outlook lands. Everything else on the macro tab is
automated once you add a FRED key (below).

### FRED macro data — already wired in, just add a key

The Fed funds target, CPI, core CPI, core PCE, and GDP tiles pull authoritative
numbers from **FRED** (the Federal Reserve's own data service) whenever a key is
present. The fetcher (`fetch_fred()` in `data/fetch.py`) and the tile wiring are
already built — you only supply the key:

1. Get a free key (instant): <https://fredaccount.stlouisfed.org/apikeys>.
2. Add it as a repo secret: **Settings → Secrets and variables → Actions → New
   repository secret**, name it exactly `FRED_API_KEY`, paste the key, save.
3. That's it. The workflow already passes the secret to the build. On the next
   run those tiles switch from snapshot values to live FRED data — the month/
   quarter labels update automatically too (e.g. "CPI Inflation · Aug").

**No key?** The build still works; the macro tiles just show their snapshot
values and the log prints `fred: no FRED_API_KEY set`.

**Run it locally with a key:**
```bash
FRED_API_KEY=your_key_here python build.py
```

Series used (for reference): `DFEDTARL`/`DFEDTARU` (fed funds range),
`CPIAUCSL` (CPI → YoY), `CPILFESL` (core CPI → YoY), `PCEPILFE` (core PCE → YoY),
`A191RL1Q225SBEA` (real GDP, % SAAR).

---

## Notes & caveats

- Stooq index symbols and availability can change; if an index stops updating,
  the tile falls back to the snapshot. `yfinance` is an alternative if you'd
  rather (add it to `requirements.txt` and swap the fetcher).
- The page embeds no third-party scripts (fonts load from Google Fonts). It is a
  single self-contained HTML file, safe to host anywhere static.
- Want email/push instead of a bookmark? The Action can be extended to send the
  built page or a summary — open an issue-to-self in the workflow, or ask Claude
  to add a notification step.
