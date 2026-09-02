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
