# Standpoint Brief — build fix + freshness automation

## Read this first

The previous zip contained a `data/` folder holding only two files. Extracting
it **replaced** your `data/` folder instead of merging into it, which deleted
`data/fallback.json`. That is why the workflow build failed: `load_snapshot()`
had no error handling, so it died on the first line of `main()`.

**This zip contains the complete contents of every folder it touches**, so
replacing folders wholesale is safe this time. `data/fallback.json` is restored
byte-for-byte from the version in the repo before the deletion.

Files:

```
build.py                        (replaces existing)
data/fallback.json              (RESTORED — this is the fix for the failed build)
data/fetch.py                   (unchanged from your current version — included so
                                 a folder replace can't drop it again)
data/calendar.json              (unchanged from your current version — same reason)
.github/workflows/daily.yml     (replaces existing — one changed line, see #4)
```

`data/prose_state.json` is created automatically on the first build. Don't
create it by hand.

---

## 1. The build failure

`load_snapshot()` now exits with an actionable message naming the file and the
`git checkout` command to restore it, instead of a JSON traceback. A missing
snapshot still stops the build — the page genuinely cannot render without it —
but you'll know why in one line.

## 2. Automated: editorial prose staleness

The prose was the item on your manual list most likely to rot, because it sits
next to numbers that refresh every three hours and inherits their credibility.

`build.py` now hashes the editorial fields in each section (`markets`,
`economy`, `credit`, `news`, `cre`) and records in `data/prose_state.json` the
date each hash last *changed*. Every build prints an age line, and once a
section passes `PROSE_STALE_DAYS` (default 10) it emits a GitHub Actions
warning that surfaces on the run summary.

Editing the copy in `fallback.json` resets that section's clock by itself —
there's no date field to remember to bump. Tune the threshold at the top of the
freshness block.

This automates the *reminder*, not the writing. Writing the copy is still you.

## 3. Automated: calendar runway

`check_calendar_runway()` warns when `calendar.json` drops below
`CALENDAR_LOW_EVENTS` (default 8) future entries, and warns loudly if it has
none at all. You get told the calendar is running out months before the card
goes empty, rather than finding out from the page.

## 4. Fixed: the snapshot was never actually persisting

I got this wrong in my earlier note and want to correct it. The commit step ran
`git add docs/index.html docs/standpoint-signal.css` only — so the snapshot
`save_snapshot()` wrote each run was discarded with the runner. It was never
re-persisting stale prose the way I said, but the more serious consequence is
that `fallback.json` never refreshed at all: during an outage the page would
fall back to whatever you last committed by hand, however old.

The commit step now also stages `data/fallback.json` and
`data/prose_state.json`. The snapshot becomes genuinely rolling, and the
freshness clock survives between runs (it needs to persist to measure anything).

## 5. Not automated, and why

- **IMF global GDP.** Doable via the IMF DataMapper API, but it changes twice a
  year with the World Economic Outlook. Adding a new network dependency and
  failure path to a build I just broke is a bad trade for two edits a year.
  Worth revisiting once things are stable.
- **CME FedWatch odds** in the Fed Funds tile note (currently "~59% odds of a
  hold (late Aug)"). No keyless source I'd trust. This one goes stale fast and
  is worth either hand-updating or dropping from the tile.
- **BLS/BEA/Fed schedule ingestion.** Neither agency publishes a stable
  machine-readable calendar I could verify from here. The runway warning in #3
  is the reliable half of this.

---

## Verification

Tested against your real template and snapshot:

| Test | Result |
| --- | --- |
| Offline build | renders, exits 0 |
| Live build, every fetcher 403ing | renders from snapshot, exits 0 |
| Missing `fallback.json` | clear fatal message, no traceback |
| Missing `calendar.json` | warns, still renders |
| Prose backdated 14 days | warns, correct per-section ages |
| Prose edited | that section's clock resets, others keep counting |
| Calendar trimmed to 7 events | low-runway warning fires |
| `daily.yml` | parses, all six steps intact |

## After committing

Run **Actions → Build daily brief → Run workflow**. Expect these lines near the
end of the log:

```
[freshness] prose last rewritten: cre 0d · credit 0d · economy 0d · markets 0d · news 0d
[freshness] calendar: 29 future events, next 2026-09-04 Employment Situation
```

All sections will read `0d` on the first run — that's the clock starting, not a
claim the copy is fresh. The `markets` prose is genuinely from late August, so
expect its warning around ten days from now.
