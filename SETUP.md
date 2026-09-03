# Standpoint Brief — deterministic prose + API setup

## Part 1 — Commit this (do this first, no keys needed)

**Important:** last time, extracting a `data/` folder replaced yours and deleted
`fallback.json`. This zip again contains the *complete* contents of every folder
it touches, so folder-level replacement is safe. But confirm before you push:

```bash
git status
```

You should see modifications and two new files (`prose.py`, `probe.py`).
If you see `deleted: data/fallback.json`, stop and run
`git checkout -- data/fallback.json`.

Then:

```bash
python build.py --offline     # should print "[prose] deterministic: 20 fields written"
git add -A
git commit -m "Deterministic prose, callout removal, API probes"
git push
```

Then **Actions → Build daily brief → Run workflow**.

### One thing that did not land last time

`.github/workflows/daily.yml` was in the previous zip but never made it into the
repo — the commit step still stages only `docs/`. That means `fallback.json`
never refreshes and `prose_state.json` is never written. The corrected file is
in this zip again. Confirm after committing:

```bash
grep -A1 "git add" .github/workflows/daily.yml
```

It should list `data/fallback.json data/prose_state.json`.

---

## Part 2 — What changed

**`prose.py` (new).** Generates every editorial field from metrics already in
the build. Nothing forecasts; nothing introduces a figure not already rendered
on the page. If a metric is missing the field is **cleared**, not left holding
old copy — so a fetcher outage shortens the page rather than resurrecting
August's commentary.

**Deleted:** `economy.callout` and `cre.callout`. Both were forward-looking
commentary with no data behind them. The template already guarded them with
`{% if %}`, so no layout change was needed.

**Template:** the five `<h2>`/standfirst pairs are now wrapped in `{% if %}`,
so a cleared headline renders nothing instead of an empty heading.

**The daily gate** (`prose_due_today`) is wired but deliberately does **not**
apply to deterministic prose. Deterministic copy describes the current tiles,
so it must regenerate whenever they do — gating it daily would let the words
contradict the numbers. It activates only if `PROSE_MODE` is ever set to
`"llm"`, which is when paying six times a day would actually be wasteful.

---

## Part 3 — Adding the APIs

The loop for each source. Do them one at a time; do not register all three
keys at once.

### Step 1 — Register the key

| Source | Register at | Gives you |
| --- | --- | --- |
| EIA | https://www.eia.gov/opendata/register.php | WTI, Henry Hub, inventories |
| Finnhub | https://finnhub.io/register | earnings calendar |
| Census | https://api.census.gov/data/key_signup.html | construction spending, permits |

Start with **EIA**. It is a stable government API with no tier games, which
makes it the right one to prove the loop on.

### Step 2 — Probe it locally

Set the key in your shell only. Do not put it in a file, and do not commit it.

```bash
export EIA_API_KEY=your_key_here        # macOS / Linux
$env:EIA_API_KEY = "your_key_here"      # PowerShell

python build.py --probe eia
```

This hits the endpoint, prints the parsed JSON, and exits. It renders nothing
and writes nothing.

### Step 3 — Send me the output

Paste what it prints. The key is redacted in the output, but glance over it
before pasting. I write the fetcher against the JSON you actually received
rather than against documentation — this is the step that was missing when the
`chartPreviousClose` bug got in.

### Step 4 — Add the repo secret

**Settings → Secrets and variables → Actions → New repository secret.**
Name it exactly `EIA_API_KEY`. Then add it to the `Build the brief` step in
`daily.yml`, alongside the existing `FRED_API_KEY`:

```yaml
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          EIA_API_KEY: ${{ secrets.EIA_API_KEY }}
```

### Step 5 — Run and confirm

`workflow_dispatch`, then check the log for the new `[ok]` line. Every fetcher
is wrapped so a missing key or a dead endpoint is skipped with a warning and
the tile keeps its previous value — the build never fails on an API.

Then repeat for Finnhub, then Census.

---

## Part 4 — Verification already done

| Test | Result |
| --- | --- |
| Offline build | 20 prose fields written, renders |
| Live build, every fetcher 403ing | renders, prose intact from snapshot metrics |
| Watchlist / tiles emptied | 11 written, 9 cleared, **no stale copy anywhere in output** |
| Empty `<h2>` check | zero |
| Missing `fallback.json` | clear fatal, no traceback |
| Missing `calendar.json` | warns, still renders |
| `--probe` with no key | prints the registration URL, exits cleanly |
| `--probe bogus` | lists valid options, exit 2 |
| `daily.yml` | parses, staging line correct |

A bug this caught: three card bodies are iterated by the template as lists of
paragraphs. Passing strings made Jinja iterate character by character and
render `3 o f 6 n a m e s`. Fixed and covered by a regression check.

---

## Part 5 — Unsourced claims removed in this pass

Every one of these was hand-written, presented as current, and had no mechanism
to update itself:

| Removed | Why |
| --- | --- |
| `economy_tiles[0].note` | CME FedWatch odds, dated "late Aug", no free source |
| `economy.body` paragraph | repeated the same FedWatch figure in prose |
| CME FedWatch in `sources` | attribution for data no longer used |
| `markets_tiles` notes ×3 | "Chip names have led recent swings", "Three-day win streak into Tuesday", "Calm — but coiled ahead of catalysts" |
| `cre_market.note` | "cap rates sit at multi-year highs… deals that clear today are value-add at a 5.75%+ going-in" — an unsourced market call with a specific number |
| trimmed `cre_tiles[1]` | "Policy-sensitive; curve positive but flat" → "Policy-sensitive short-end benchmark" |
| trimmed `cre_fin_tiles[0]` | "near a 12-month high" → "Freddie Mac Primary Mortgage Market Survey" |
| trimmed `cre_tiles[3]` | "Largest near-term maturity wave" → labelled as manually maintained |

### Bugs fixed alongside

**Core PCE note asserted its own conclusion.** `apply_fred` hardcoded
"still above the 2% goal" regardless of the print, so it would have stated a
falsehood the first time core PCE came in under 2. Now computed from the value.

**The Fed Funds pill was a hand-typed date.** "Sept 16 decision" had no way to
advance past its own meeting. Now derived from the next FOMC entry in
`calendar.json`, and cleared entirely when none is on file — it can't name a
meeting that already happened.

**Brent and WTI were frozen prices rendering as live data.** $88.50 and $80.50
were snapshot constants with no fetcher behind them. FRED carries both daily
(`DCOILBRENTEU`, `DCOILWTICO`) and your key is already configured, so this
needed no new credential. Each tile now carries its observation date, so a
stale print is visible rather than implied.

**IMF year followed UTC.** On Dec 31 a UTC runner would request the next year,
get nothing, and silently drop the tile for a day.

## Part 6 — Still hand-maintained

- **Calendar top-ups.** BEA past Oct 29 2026, BLS 2027. You get an Actions
  warning when fewer than 8 future events remain.
- **`cre_tiles[3]`, the $936B 2026 maturity figure.** Still a hardcoded number
  with no source or vintage. I labelled it rather than deleting it, since it is
  real content and the decision is yours. Either cite the source and date in
  the note or drop the tile — as it stands a reader has no way to tell how old
  it is.
- **`cre_market_tiles`** carry an `as_of` of "Aug 2026 · manually maintained
  from CBRE, CoStar & Trepp releases". The label is honest, which is why I left
  it, but those tiles age the same way everything else did.


---

# Update — earnings, retries, and the workflow (again)

## Commit checklist

**The workflow has failed to land twice.** Before anything else, after
committing:

```bash
grep -A1 "git add" .github/workflows/daily.yml
grep FINNHUB .github/workflows/daily.yml
```

The first must show `data/fallback.json data/prose_state.json`; the second must
show the `FINNHUB_API_KEY` line. If either is missing, the zip's copy of
`daily.yml` did not replace yours.

Add `FINNHUB_API_KEY` under **Settings → Secrets and variables → Actions**
before dispatching, or the card stays macro-only (which is a clean fallback,
not an error).

## Earnings on the catalysts card

`fetch_earnings()` filters the Finnhub feed to `bellwethers` in
`calendar.json` plus your six watchlist tickers. The probe returned 271 rows
over a fortnight, so filtering is the entire design — unfiltered it would bury
the macro releases.

The bellwether list lives in `calendar.json` as plain symbols. It is a list,
not a claim: it can go incomplete, but unlike the prose we removed it cannot go
false. Edit it without touching code.

Timing: only 79 of 271 rows carried an `hour`. Where the feed says `bmo`/`amc`
the body reads "before open" / "after close"; where it is blank the row states
the date only. A blank is never read as after-close.

Macro and earnings now merge into one chronological stream, capped at
`CATALYST_MAX` (5) rows, with macro sorting ahead of earnings on a shared date.

## Treasury timeout

`_get()` now retries three times with backoff on timeouts, connection errors
and 5xx — but never on 4xx, since a 403 will not improve by asking again and
retrying just delays the fallback. Treasury gets a 40s timeout of its own; it
is reliably the slowest endpoint you hit.

If that warning still appears on most runs rather than occasionally, it is
rate limiting rather than slowness, and we should cache the last good curve
instead.

## Bugs caught while building this

- **Undefined name.** First draft called `YAHOO_WATCH_SYMBOLS`, which does not
  exist; it is `YAHOO_WATCH` in `fetch.py`. Would have crashed every live build.
- **Wrong sort order.** Earnings were appended after macro rather than merged,
  so today's after-close earnings rendered *below* next week's CPI. Now sorted
  by date.
- **Unbounded card.** Three macro plus three earnings gave six rows with no
  cap. Now five.
- **API key in the log.** `requests` puts the full URL in its error message,
  query string included, so a 4xx would have printed your Finnhub key. Actions
  masks registered secrets but local runs do not. The key is now scrubbed from
  the message regardless.

## Verified

| Test | Result |
| --- | --- |
| No key | `fetch_earnings` returns None, card macro-only |
| Key set, no matches | card macro-only |
| Today amc pair + blank hour + future bmo | correct grouping, chronological |
| Past-dated row | dropped |
| Malformed rows (no date, no symbol) | skipped, rest survive |
| 4xx | fails in 0.0s — no retry |
| Key in error message | `<redacted>` |
| Missing snapshot / calendar | fatal / warns, as before |
| Offline + live builds | render, prose intact |


---

# Hotfix — exit 128 on the commit step

## What happened

Two bugs of mine, chained.

`check_prose_age()` returned early when every section is machine-generated —
which is now always — and that early return sat *before* the line that writes
`data/prose_state.json`. So the file was never created.

The commit step then ran `git add data/prose_state.json` on a path that did not
exist. `git add` treats a missing pathspec as fatal, which fails the whole job
with exit 128. The build itself had already succeeded; only the push failed.

## Fixed

- `check_prose_age()` now always writes `prose_state.json`, carrying the
  `_generated` marker the daily gate will read.
- The commit step stages only files that exist, so a missing one is skipped
  rather than fatal.

The staging loop uses `if [ -f "$f" ]; then ... fi` rather than
`[ -f "$f" ] && git add "$f"`. That is not style. Actions runs `run:` blocks
under `bash -e`, and the `&&` form returns 1 when the file is absent, which
would fail the step for a different reason — verified both forms under
`bash -e`: the `&&` version exits 1, the `if` version exits 0.

## If it fails again

Exit code tells you where to look. **128 is git** — permissions, a rejected
push, or a bad path in the commit step; the page built fine. **1 is Python** —
read the traceback in the `Build the brief` step. Anything else, send me the
step name and the last few lines.
