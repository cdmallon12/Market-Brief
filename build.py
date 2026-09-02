#!/usr/bin/env python3
"""
Build The Standpoint Brief.

Loads the last-known-good snapshot (data/fallback.json), overlays whatever the
keyless fetchers can retrieve today, renders templates/brief.html.j2, and writes
docs/index.html (the file GitHub Pages serves).

Design principle: the page ALWAYS renders. Every live value is optional; if a
source is down, the snapshot value shows instead. Run locally with:

    pip install -r requirements.txt
    python build.py            # build from snapshot + live sources
    python build.py --offline  # build from snapshot only (no network)
"""

from __future__ import annotations
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib

from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

import prose

ET = ZoneInfo("America/New_York")


def today_et():
    """The current date in US Eastern time.

    CI runners are UTC, so dt.date.today() rolls over at 8/7 PM ET and stamps
    the page with tomorrow's date. Every user-facing date is market-relative,
    so the whole build works off Eastern.
    """
    return dt.datetime.now(ET).date()


ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "index.html"


def load_snapshot():
    path = DATA / "fallback.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"[fatal] {path} is missing. It is the last-known-good snapshot that every\n"
            f"        section falls back to, so the page cannot render without it.\n"
            f"        Restore it from git: git checkout HEAD~1 -- data/fallback.json"
        )
    except json.JSONDecodeError as e:
        raise SystemExit(f"[fatal] {path} is not valid JSON: {e}")


def load_calendar():
    path = DATA / "calendar.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[warn] calendar: {path} not found - catalysts card will fall back to the snapshot")
        return None
    except json.JSONDecodeError as e:
        print(f"[warn] calendar: {path} is not valid JSON ({e}) - catalysts card will fall back to the snapshot")
        return None


def save_snapshot(ctx):
    """Persist the merged context so a future outage falls back to today's data."""
    (DATA / "fallback.json").write_text(
        json.dumps(ctx, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def fmt_num(v, dec=2):
    return f"{v:,.{dec}f}"


def signed_pct(v, dec=2):
    return f"{'+' if v >= 0 else '-'}{abs(v):.{dec}f}%"


def dir_of(v):
    return 1 if v > 0 else (-1 if v < 0 else 0)


# --------------------------------------------------------------------------- #
INDEX_META = {
    "spx": {"nm": "S&P 500", "tile": 0, "dec": 2},
    "ndq": {"nm": "Nasdaq", "tile": 1, "dec": 0},
    "dji": {"nm": "Dow", "tile": 2, "dec": 0},
    "vix": {"nm": "VIX", "tile": 3, "dec": 2},
}


def apply_indices(ctx, idx):
    """Update ticker + markets tiles from live index data."""
    tick_by_name = {t["nm"]: t for t in ctx["ticker"]}
    for key, d in idx.items():
        meta = INDEX_META.get(key)
        if not meta:
            continue
        val = fmt_num(d["value"], meta["dec"])
        chg = d["change_pct"]
        # ticker
        t = tick_by_name.get(meta["nm"])
        if t:
            t["vl"] = val
            t["ch"] = signed_pct(chg)
            t["dir"] = dir_of(chg)
        # tile
        tile = ctx["markets_tiles"][meta["tile"]]
        tile["val"] = val
        tile["tone"] = "pos" if chg > 0 else ("neg" if chg < 0 else "")
        tile["delta"] = {"dir": dir_of(chg), "txt": f"{abs(chg):.2f}%", "note": "day"}
        # S&P year-over-year note, computed keyless from history
        if key == "spx" and d.get("yoy_pct") is not None:
            yoy = d["yoy_pct"]
            cls = "up" if yoy >= 0 else "down"
            tile["note"] = f'<span class="{cls} mono">{signed_pct(yoy, 1)}</span> vs. one year ago'


def apply_rates(ctx, curve, sofr):
    """Update ticker + CRE tiles + curve chart from Treasury/SOFR data."""
    tick_by_name = {t["nm"]: t for t in ctx["ticker"]}

    if curve and curve.get("points"):
        ctx["curve"] = [
            {"t": p["t"], "x": p["x"], "y": p["y"], "axis": p["axis"], "mark": p["mark"]}
            for p in curve["points"]
        ]
        if curve.get("date"):
            ctx["cre"]["curve_date"] = curve["date"]
        y2, y10, y30 = curve.get("y2"), curve.get("y10"), curve.get("y30")
        spread = curve.get("spread_2s10s_bps")
        if y10 is not None:
            ctx["cre_tiles"][0]["val"] = f"{y10:.2f}"
            if spread is not None:
                sign = "+" if spread >= 0 else ""
                ctx["cre_tiles"][0]["note"] = (
                    f'2s10s curve spread: <span class="mono">{sign}{spread} bps</span>'
                )
            if "10Y UST" in tick_by_name:
                tick_by_name["10Y UST"]["vl"] = f"{y10:.2f}%"
        if y2 is not None:
            ctx["cre_tiles"][1]["val"] = f"{y2:.2f}"
            if "2Y UST" in tick_by_name:
                tick_by_name["2Y UST"]["vl"] = f"{y2:.2f}%"
        if y30 is not None and "30Y UST" in tick_by_name:
            tick_by_name["30Y UST"]["vl"] = f"{y30:.2f}%"

        # Full curve-spread readout (CRE tab "Financing & spreads")
        def _sp(key):
            v = curve.get(key)
            return f"+{v}" if (v is not None and v >= 0) else (str(v) if v is not None else "—")
        ctx["curve_spreads"] = {
            "2s10s": _sp("spread_2s10s_bps"), "3m10y": _sp("spread_3m10y_bps"),
            "5s30s": _sp("spread_5s30s_bps"), "10s30s": _sp("spread_10s30s_bps"),
        }
        # Illustrative agency coupon = 10Y + ~135–165 bp (cre_fin_tiles[1])
        fin = ctx.get("cre_fin_tiles")
        if fin and len(fin) > 1 and y10 is not None:
            fin[1]["val"] = f"{y10 + 1.35:.2f}–{y10 + 1.65:.2f}"
            fin[1]["note"] = f'10Y ({y10:.2f}%) + 135–165 bp agency spread'

        # Refinancing-gap chart: the "new CRE loan" bar tracks the live agency-
        # coupon midpoint (10Y + ~150 bp). "expiring" stays the illustrative
        # snapshot — the average coupon on debt originated years ago, which no
        # live feed provides.
        if y10 is not None and isinstance(ctx.get("gap"), dict):
            ctx["gap"]["new"] = round(y10 + 1.5, 2)

    if sofr is not None:
        ctx["cre_tiles"][2]["val"] = f"{sofr:.2f}"
        if "SOFR" in tick_by_name:
            tick_by_name["SOFR"]["vl"] = f"{sofr:.2f}%"


def _month_label_safe(iso):
    try:
        d = dt.date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    return f"{d.strftime('%b')} {d.day}"


def apply_fred(ctx, f):
    """Map authoritative FRED macro values onto the economy tiles + ticker."""
    if not f:
        return
    tick = {t["nm"]: t for t in ctx["ticker"]}
    tiles = ctx["economy_tiles"]

    # Fed funds target range (tile 0) — keep the FedWatch odds note as-is.
    if "fed_lower" in f and "fed_upper" in f:
        lo, up = f["fed_lower"], f["fed_upper"]
        tiles[0]["val"] = f"{lo:.2f}"
        tiles[0]["unit"] = f"-{up:.2f}%"
        if "Fed Funds" in tick:
            tick["Fed Funds"]["vl"] = f"{lo:.2f}-{up:.2f}%"

    # CPI (tile 1) — headline YoY, month label, neutral MoM delta, core CPI note.
    if "cpi" in f:
        c = f["cpi"]
        tiles[1]["val"] = f"{c['yoy']:.1f}"
        tiles[1]["unit"] = "%"
        if c.get("month"):
            tiles[1]["lbl"] = f"CPI Inflation · {c['month']}"
        if c.get("prev_yoy") is not None:
            chg = c["yoy"] - c["prev_yoy"]
            tiles[1]["delta"] = {"dir": 0, "txt": f"{chg:+.1f}pp", "note": "vs prior mo"}
        else:
            tiles[1].pop("delta", None)
        if "core_cpi" in f:
            tiles[1]["note"] = f'Core CPI: <span class="mono">{f["core_cpi"]["yoy"]:.1f}%</span>'
        if "CPI" in tick:
            tick["CPI"]["vl"] = f"{c['yoy']:.1f}%"

    # Core PCE (tile 2) — the Fed's preferred gauge, now an actual print.
    if "core_pce" in f:
        p = f["core_pce"]
        tiles[2]["val"] = f"{p['yoy']:.1f}"
        tiles[2]["unit"] = "%"
        tiles[2]["lbl"] = f"Core PCE · {p['month']}" if p.get("month") else "Core PCE"
        # Was hardcoded as "still above the 2% goal" regardless of the print,
        # so it would have stated a falsehood the moment core PCE dipped under 2.
        yoy = p["yoy"]
        rel = "above" if yoy > 2.0 else ("below" if yoy < 2.0 else "at")
        tiles[2]["note"] = f"Fed's preferred gauge — {rel} the 2% goal"
        if "Core PCE" in tick:
            tick["Core PCE"]["vl"] = f"{p['yoy']:.1f}%"

    # Energy tiles 0/1 — live crude benchmarks. Previously frozen snapshot
    # prices that read as current; now stamped with the observation date so a
    # stale print is visible rather than implied.
    et = ctx.get("energy_tiles") or []
    for idx, name, label in ((0, "brent", "Brent Crude"), (1, "wti", "WTI Crude")):
        if name in f and idx < len(et):
            obs = f[name]
            et[idx]["lbl"] = label
            et[idx]["val"] = f"${obs['value']:,.2f}"
            month = _month_label_safe(obs.get("date"))
            base = ("Global benchmark — the swing factor in headline inflation"
                    if name == "brent" else "U.S. benchmark")
            et[idx]["note"] = f"{base} · spot as of {month}" if month else base

    # Real GDP (tile 3) — SAAR, with a signed delta vs the prior quarter.
    if "gdp" in f:
        g = f["gdp"]
        tiles[3]["val"] = f"{g['rate']:.1f}"
        tiles[3]["unit"] = "%"
        q = g.get("quarter") or ""
        tiles[3]["lbl"] = f"U.S. GDP · {q} adv." if q else "U.S. GDP"
        tiles[3]["note"] = "Annualized real growth (SAAR)"
        if g.get("prev") is not None:
            chg = g["rate"] - g["prev"]
            tiles[3]["tone"] = "pos" if chg > 0 else ("neg" if chg < 0 else "")
            tiles[3]["delta"] = {"dir": dir_of(chg), "txt": f"{chg:+.1f}pp", "note": "vs prior qtr"}
        # keep the S&P-style ticker entry current
        for key, t in tick.items():
            if key.startswith("US GDP"):
                t["nm"] = f"US GDP {q}".strip()
                t["vl"] = f"{g['rate']:.1f}%"
                break

    # --- Credit & Spreads tab tiles (spreads never carry misleading up/down
    #     color — a widening spread is bad, so we label "wider/tighter" instead) ---
    ct = ctx.get("credit_tiles")
    if ct:
        if "bbb_oas" in f:
            b = f["bbb_oas"]
            ct[0]["val"], ct[0]["unit"] = f"{b['value']:.2f}", "%"
            if b.get("prev") is not None:
                d = b["value"] - b["prev"]
                word = "wider" if d > 0 else ("tighter" if d < 0 else "flat")
                ct[0]["delta"] = {"dir": 0, "txt": f"{d:+.2f}pp", "note": word}
            if "IG OAS" in tick:
                tick["IG OAS"]["vl"] = f"{b['value']:.2f}%"
        if "hy_oas" in f:
            h = f["hy_oas"]
            ct[1]["val"], ct[1]["unit"] = f"{h['value']:.2f}", "%"
            if h.get("prev") is not None:
                d = h["value"] - h["prev"]
                word = "wider" if d > 0 else ("tighter" if d < 0 else "flat")
                ct[1]["delta"] = {"dir": 0, "txt": f"{d:+.2f}pp", "note": word}
            if "HY OAS" in tick:
                tick["HY OAS"]["vl"] = f"{h['value']:.2f}%"
        if "bbb_oas" in f and "hy_oas" in f:
            gap = f["hy_oas"]["value"] - f["bbb_oas"]["value"]
            ct[2]["val"], ct[2]["unit"] = f"{gap:.2f}", "pp"
            ctx["credit_chart"] = {"ig": f["bbb_oas"]["value"], "hy": f["hy_oas"]["value"]}

    # --- CRE credit-fundamentals tiles (in the CRE tab) ---
    cf = ctx.get("cre_fund_tiles")
    if cf:
        if "cre_delinq" in f:
            dq = f["cre_delinq"]
            cf[0]["val"], cf[0]["unit"] = f"{dq['value']:.2f}", "%"
            q = dq.get("quarter") or ""
            cf[0]["lbl"] = f"CRE Loan Delinquency · {q}" if q else "CRE Loan Delinquency"
            if dq.get("prev") is not None:
                d = dq["value"] - dq["prev"]
                cf[0]["delta"] = {"dir": 0, "txt": f"{d:+.2f}pp", "note": "vs prior Q"}
                cf[0]["tone"] = "neg" if d > 0 else ("pos" if d < 0 else "")
        if "cre_loans" in f:
            cl = f["cre_loans"]
            cf[1]["val"], cf[1]["unit"] = f"${cl['value']:,.0f}", "B"
            m = cl.get("month") or ""
            cf[1]["lbl"] = f"Bank CRE Loans · {m}" if m else "Bank CRE Loans"
            if cl.get("yoy") is not None:
                cf[1]["note"] = f'<span class="mono">{cl["yoy"]:+.1f}%</span> outstanding vs. one year ago'
        if "sloos_cre" in f:
            s = f["sloos_cre"]
            v = s["value"]
            cf[2]["val"], cf[2]["unit"] = f"{v:+.1f}", ""
            q = s.get("quarter") or ""
            cf[2]["lbl"] = f"Bank Lending Standards · {q}" if q else "Bank Lending Standards"
            if v > 0:
                cf[2]["note"] = "Net share of banks <strong>tightening</strong> CRE standards"
                cf[2]["tone"] = "neg"
            elif v < 0:
                cf[2]["note"] = "Net share of banks <strong>easing</strong> CRE standards"
                cf[2]["tone"] = "pos"
            else:
                cf[2]["note"] = "Standards unchanged on net"

    # --- Energy tiles (Global Economy tab): Brent, WTI, headline PCE ---
    et = ctx.get("energy_tiles")
    if et:
        if "brent" in f and len(et) > 0:
            b = f["brent"]
            et[0]["val"], et[0]["unit"] = f"${b['value']:,.2f}", ""
            if b.get("prev") is not None:
                d = b["value"] - b["prev"]
                et[0]["delta"] = {"dir": dir_of(d), "txt": f"{d:+.2f}", "note": "d/d"}
                et[0]["tone"] = ""
        if "wti" in f and len(et) > 1:
            w = f["wti"]
            et[1]["val"], et[1]["unit"] = f"${w['value']:,.2f}", ""
            if w.get("prev") is not None:
                d = w["value"] - w["prev"]
                et[1]["delta"] = {"dir": dir_of(d), "txt": f"{d:+.2f}", "note": "d/d"}
        if "headline_pce" in f and len(et) > 2:
            h = f["headline_pce"]
            et[2]["val"], et[2]["unit"] = f"{h['yoy']:.1f}", "%"
            m = h.get("month") or ""
            et[2]["lbl"] = f"Headline PCE · {m}" if m else "Headline PCE"
            core = f.get("core_pce", {}).get("yoy")
            if core is not None:
                gap = h["yoy"] - core
                et[2]["note"] = f'{gap:+.1f}pp above core PCE — the energy pass-through'
            if "Headline PCE" in tick:
                tick["Headline PCE"]["vl"] = f"{h['yoy']:.1f}%"

    # --- CRE financing tile: 30-yr mortgage (Freddie PMMS) ---
    fin = ctx.get("cre_fin_tiles")
    if fin and "mortgage30" in f and len(fin) > 0:
        m = f["mortgage30"]
        fin[0]["val"], fin[0]["unit"] = f"{m['value']:.2f}", "%"
        if m.get("prev") is not None:
            d = m["value"] - m["prev"]
            fin[0]["delta"] = {"dir": 0, "txt": f"{d:+.2f}pp", "note": "wk/wk"}


def apply_imf(ctx, imf):
    """Update the IMF global-GDP tile (economy_tiles[4]) from live WEO data."""
    if not imf:
        return
    tiles = ctx.get("economy_tiles")
    if not tiles or len(tiles) < 5:
        return
    t = tiles[4]
    t["val"] = f"{imf['value']:.1f}"
    t["unit"] = "%"
    t["lbl"] = "Global GDP · IMF"
    if imf.get("next_value") is not None:
        t["note"] = f"{imf['year']} · {imf['next_value']:.1f}% projected in {imf['next_year']}"
    else:
        t["note"] = f"{imf['year']} · IMF World Economic Outlook"


def apply_watchlist(ctx, wl):
    """Update the CRE watchlist rows from live Stooq end-of-day closes."""
    if not wl:
        return
    for row in ctx.get("watchlist", []):
        d = wl.get(row["code"])
        if not d:
            continue
        row["price"] = f"{d['value']:,.2f}"
        chg = d["change_pct"]
        row["chg"] = f"{chg:+.2f}%"
        row["dir"] = dir_of(chg)


def _et_stamp(ts):
    """Format an epoch as '1:23 PM ET, Aug 26' in US/Eastern; '' on failure."""
    try:
        from zoneinfo import ZoneInfo
        d = dt.datetime.fromtimestamp(ts, ZoneInfo("America/New_York"))
        hr = d.strftime("%I").lstrip("0") or "12"
        return f"{hr}:{d.strftime('%M %p')} ET, {d.strftime('%b')} {d.day}"
    except Exception:
        return ""


def apply_index_quotes(ctx, q):
    """Intraday/open quotes (Yahoo): current delayed price + change vs prior close."""
    tick = {t["nm"]: t for t in ctx["ticker"]}
    for key, d in q.items():
        meta = INDEX_META.get(key)
        if not meta:
            continue
        price, chg = d["price"], d["change_pct"]
        val = fmt_num(price, meta["dec"])
        tile = ctx["markets_tiles"][meta["tile"]]
        tile["val"] = val
        tile["tone"] = "pos" if chg > 0 else ("neg" if chg < 0 else "")
        tile["delta"] = {"dir": dir_of(chg), "txt": f"{abs(chg):.2f}%", "note": "vs prior close"}
        parts = []
        if d.get("open") is not None and d.get("open_is_today"):
            parts.append(f'Open <span class="mono">{fmt_num(d["open"], meta["dec"])}</span>')
        else:
            parts.append(f'Prior close <span class="mono">{fmt_num(d["prev_close"], meta["dec"])}</span>')
        if key == "spx" and d.get("yoy_pct") is not None:
            yoy = d["yoy_pct"]
            cls = "up" if yoy >= 0 else "down"
            parts.append(f'<span class="{cls} mono">{signed_pct(yoy, 1)}</span> YoY')
        tile["note"] = " · ".join(parts)
        t = tick.get(meta["nm"])
        if t:
            t["vl"], t["ch"], t["dir"] = val, signed_pct(chg), dir_of(chg)
    # data-freshness stamp for the markets tab — distinguish a live (post-open)
    # snapshot from a pre-open/closed one so the prior close doesn't read as stale.
    stamps = [d.get("ts") for d in q.values() if d.get("ts")]
    opened_today = any(d.get("open_is_today") for d in q.values())
    if stamps:
        et = _et_stamp(max(stamps))
        if et:
            if opened_today:
                ctx["markets"]["quote_note"] = f"Quotes ~15-min delayed · as of {et}"
            else:
                ctx["markets"]["quote_note"] = f"Markets closed — showing the prior session's close (last update {et})"


def apply_watchlist_quotes(ctx, q):
    """Intraday quotes (Yahoo) for the CRE watchlist: price + change vs prior close."""
    if not q:
        return
    for row in ctx.get("watchlist", []):
        d = q.get(row["code"])
        if not d:
            continue
        row["price"] = f"{d['price']:,.2f}"
        row["chg"] = f"{d['change_pct']:+.2f}%"
        row["dir"] = dir_of(d["change_pct"])
    ctx["watchlist_note"] = "Prices ~15-min delayed (Yahoo Finance) · change vs. prior close."


def _rel_day(target, today):
    delta = (target - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{target.strftime('%a')} · {target.strftime('%b')} {target.day}"


def apply_fomc_pill(ctx, cal, today):
    """Set the Fed Funds tile pill from the next FOMC date in the calendar.

    Replaces a hand-typed "Sept 16 decision" that had no mechanism to advance
    past its own meeting. Cleared when no future FOMC date is on file, so it
    can never name a meeting that has already happened.
    """
    tiles = ctx.get("economy_tiles") or []
    if not tiles:
        return
    tile = tiles[0]
    best = None
    for e in (cal or {}).get("events") or []:
        if "fomc" not in str(e.get("title", "")).lower():
            continue
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if d >= today and (best is None or d < best):
            best = d
    if best:
        tile["pill"] = {"cls": "watch", "txt": f"{best.strftime('%b %-d')} decision"}
    else:
        tile.pop("pill", None)


def apply_calendar(ctx, cal, today):
    """Populate the 'catalysts' card from the economic-release calendar by date."""
    if not cal:
        return
    events = cal.get("events", [])

    def dof(e):
        try:
            return dt.date.fromisoformat(e["date"])
        except Exception:
            return None

    dated = [(dof(e), e) for e in events if dof(e)]
    todays = [e for d, e in dated if d == today]
    upcoming = sorted([(d, e) for d, e in dated if d > today], key=lambda x: x[0])

    items = []
    for e in todays:
        items.append({"when": f"{e['time']} · Today", "title": e["title"], "body": e["detail"] + "."})
    for d, e in upcoming[: (2 if todays else 3)]:
        items.append({"when": _rel_day(d, today), "title": e["title"], "body": e["detail"] + "."})

    if items:
        ctx["markets"]["catalysts"] = items
        if todays:
            ctx["markets"]["card2_title"] = "On the calendar today"
            ctx["markets"]["card2_sub"] = "Scheduled economic releases (ET)"
        else:
            ctx["markets"]["card2_title"] = "Coming up"
            ctx["markets"]["card2_sub"] = "Next scheduled economic releases (ET)"


def apply_market_headlines(ctx, items):
    """Replace the weekly timeline with live market headlines when available."""
    if not items:
        return
    ctx["headlines"] = [
        {"when": it.get("when", ""), "title": it["title"], "blurb": "",
         "kind": "", "tag": it.get("source", ""), "link": it.get("link", "")}
        for it in items if it.get("title")
    ]
    ctx["news"]["standfirst"] = (
        "The latest market-moving headlines, refreshed automatically from major "
        "financial newswires. Click any item to read the full story."
    )


def apply_cre_headlines(ctx, items):
    if items:
        ctx["cre_headlines"] = [
            {"source": it.get("source", ""), "title": it["title"],
             "when": it.get("when", ""), "link": it.get("link", "")}
            for it in items if it.get("title")
        ]


def refresh_dates(ctx):
    today = today_et()
    ctx["meta"]["date_line"] = today.strftime("%A · %b %d, %Y")
    ctx["meta"]["as_of"] = "Auto-compiled from public sources · " + today.strftime("%b %d, %Y")
    ctx["meta"]["compiled"] = "Compiled " + today.strftime("%b %d, %Y") + " · The Standpoint Brief"
    ctx["meta"]["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_live(ctx, offline):
    if offline:
        print("[info] offline mode — snapshot only")
        return
    from data import fetch  # imported here so --offline needs no network libs at import

    # Indices: Yahoo intraday (open + delayed price) primary; Stooq EOD fallback.
    try:
        iq = fetch.fetch_index_quotes()
        if not iq:
            raise RuntimeError("no Yahoo index data")
        apply_index_quotes(ctx, iq)
        print(f"[ok] index quotes · Yahoo intraday: {', '.join(iq)}")
    except Exception as e:
        print(f"[warn] yahoo indices ({e}) — falling back to Stooq EOD")
        try:
            idx = fetch.fetch_indices()
            if idx:
                apply_indices(ctx, idx)
                print(f"[ok] indices · Stooq EOD: {', '.join(idx)}")
        except Exception as e2:
            print(f"[warn] indices step: {e2}")

    curve = sofr = None
    try:
        curve = fetch.fetch_treasury_curve()
        print("[ok] treasury curve" if curve else "[warn] treasury curve: no data")
    except Exception as e:
        print(f"[warn] treasury step: {e}")
    try:
        sofr = fetch.fetch_sofr()
        print(f"[ok] sofr: {sofr}" if sofr else "[warn] sofr: no data")
    except Exception as e:
        print(f"[warn] sofr step: {e}")
    apply_rates(ctx, curve, sofr)

    try:
        mh = fetch.fetch_market_headlines()
        apply_market_headlines(ctx, mh)
        print(f"[ok] market headlines: {len(mh)}")
    except Exception as e:
        print(f"[warn] market headlines: {e}")
    try:
        ch = fetch.fetch_cre_headlines()
        apply_cre_headlines(ctx, ch)
        print(f"[ok] cre headlines: {len(ch)}")
    except Exception as e:
        print(f"[warn] cre headlines: {e}")

    try:
        fred = fetch.fetch_fred()
        if fred:
            apply_fred(ctx, fred)
            print(f"[ok] fred macro: {', '.join(fred)}")
        else:
            print("[info] fred: no FRED_API_KEY set — macro/credit tiles use snapshot values")
    except Exception as e:
        print(f"[warn] fred step: {e}")

    try:
        imf = fetch.fetch_imf_gdp()
        if imf:
            apply_imf(ctx, imf)
            print(f"[ok] imf global gdp: {imf['value']}% ({imf['year']})")
        else:
            print("[info] imf gdp: no data — global-GDP tile uses snapshot value")
    except Exception as e:
        print(f"[warn] imf step: {e}")

    # Watchlist: Yahoo intraday primary; Stooq EOD fallback.
    try:
        wq = fetch.fetch_watchlist_quotes()
        if not wq:
            raise RuntimeError("no Yahoo watchlist data")
        apply_watchlist_quotes(ctx, wq)
        print(f"[ok] watchlist quotes · Yahoo intraday: {', '.join(wq)}")
    except Exception as e:
        print(f"[warn] yahoo watchlist ({e}) — falling back to Stooq EOD")
        try:
            wl = fetch.fetch_watchlist()
            if wl:
                apply_watchlist(ctx, wl)
                print(f"[ok] watchlist · Stooq EOD: {', '.join(wl)}")
        except Exception as e2:
            print(f"[warn] watchlist step: {e2}")


# --- Freshness checks -------------------------------------------------------
# The numbers on this page refresh themselves; the editorial prose and the
# release calendar do not. These checks turn both into something that reports
# itself in the build log instead of quietly rotting on the page.

PROSE_FIELDS = {
    "markets": ["headline", "standfirst", "card1_title", "card1_sub", "card1_body"],
    "economy": ["headline", "standfirst", "callout", "body"],
    "credit": ["headline", "standfirst", "card_title", "card_sub", "card_body"],
    "news": ["headline", "standfirst"],
    "cre": ["headline", "standfirst", "card_title", "card_sub", "card_body", "callout"],
}
PROSE_MODE = "deterministic"   # "deterministic" | "llm" (not implemented)
PROSE_STALE_DAYS = 10      # nag once a section's prose is older than this
CALENDAR_LOW_EVENTS = 8    # nag once the calendar has fewer future events left


def _ci_warn(msg):
    """Print a GitHub Actions annotation in CI, a plain warning locally."""
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"[warn] {msg}")


def _prose_digest(ctx, section):
    block = ctx.get(section) or {}
    parts = []
    for key in PROSE_FIELDS[section]:
        val = block.get(key)
        if isinstance(val, (list, tuple)):
            val = " ".join(str(x) for x in val)
        parts.append(f"{key}={val if val is not None else ''}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def prose_due_today(today, force=False):
    """True when a cost-bearing prose generator should run this build.

    Only meaningful for PROSE_MODE == "llm": it holds generation to once per
    day so an every-three-hours build does not pay six times for copy that
    moves once. Records the date in prose_state.json alongside the freshness
    clock.
    """
    if force:
        return True
    path = DATA / "prose_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return True
    return state.get("_generated") != today.isoformat()


def check_prose_age(ctx, today):
    """Track when each editorial block last CHANGED and warn once it goes stale.

    Hashes the prose fields and records the date the hash last moved in
    data/prose_state.json. Editing the copy in fallback.json resets the clock
    automatically; nothing to remember to bump by hand.
    """
    path = DATA / "prose_state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    hand_written = {s: f for s, f in PROSE_FIELDS.items() if s not in prose.GENERATED}
    if not hand_written:
        print("[freshness] prose: all sections generated from live metrics")
        return {}

    ages = {}
    for section in hand_written:
        digest = _prose_digest(ctx, section)
        rec = state.get(section) or {}
        try:
            unchanged = rec.get("digest") == digest
            written = dt.date.fromisoformat(rec["updated"]) if unchanged else today
        except (KeyError, ValueError, TypeError):
            written = today
        state[section] = {"digest": digest, "updated": written.isoformat()}
        ages[section] = (today - written).days

    try:
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[warn] prose age: could not write {path} ({e})")

    summary = " · ".join(f"{k} {v}d" for k, v in sorted(ages.items()))
    print(f"[freshness] prose last rewritten: {summary}")
    stale = sorted(k for k, v in ages.items() if v >= PROSE_STALE_DAYS)
    if stale:
        _ci_warn(
            "Editorial prose is "
            + ", ".join(f"{k} {ages[k]} days old" for k in stale)
            + ". The numbers around it are current; the words are not. "
              "Rewrite in data/fallback.json."
        )
    return ages


def check_calendar_runway(cal, today):
    """Warn before the catalysts calendar runs out of future events."""
    events = (cal or {}).get("events") or []
    future = []
    for e in events:
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if d >= today:
            future.append((d, e.get("title", "?")))
    future.sort()

    if not future:
        _ci_warn(
            "The catalysts calendar has no events on or after today. The card has "
            "nothing to show. Top up data/calendar.json (sources are in its _sources block)."
        )
        return 0

    nxt, title = future[0]
    print(f"[freshness] calendar: {len(future)} future events, next {nxt.isoformat()} {title}")
    if len(future) < CALENDAR_LOW_EVENTS:
        _ci_warn(
            f"The catalysts calendar has only {len(future)} future events left "
            f"(through {future[-1][0].isoformat()}). Top up data/calendar.json."
        )
    return len(future)


def render(ctx):
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("brief.html.j2")
    chart_data = {
        "ticker": ctx["ticker"],
        "curve": ctx["curve"],
        "gap": ctx["gap"],
        "orig": ctx["orig"],
        "credit": ctx.get("credit_chart", {}),
    }
    html = tmpl.render(chart_data_json=json.dumps(chart_data), **ctx)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[done] wrote {OUT.relative_to(ROOT)} ({len(html):,} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="build from snapshot only")
    ap.add_argument("--no-save", action="store_true", help="do not update fallback.json")
    ap.add_argument("--force-prose", action="store_true",
                    help="regenerate prose even if the daily gate says it already ran")
    ap.add_argument("--probe", metavar="SOURCE",
                    help="hit one API and print the parsed result, then exit "
                         "(eia | finnhub | census). Renders nothing.")
    args = ap.parse_args()

    if args.probe:
        import probe
        probe.run(args.probe)
        return

    today = today_et()
    ctx = load_snapshot()
    live = copy.deepcopy(ctx)
    gather_live(live, args.offline)
    refresh_dates(live)

    cal = load_calendar()
    apply_calendar(live, cal, today)  # dynamic catalysts (date-based, no network)
    apply_fomc_pill(live, cal, today)

    # Editorial prose, derived from the metrics already merged into `live`.
    # Deterministic output is idempotent and must track the tiles, so it is
    # regenerated every build. The daily gate below exists for a future
    # cost-bearing generator, where regenerating six times a day would be
    # wasteful — it deliberately does not apply to the deterministic path.
    if PROSE_MODE == "deterministic":
        prose.apply_prose(live, cal, today)
    elif not prose_due_today(today, args.force_prose):
        print("[prose] already generated today — skipping")

    render(live)

    check_prose_age(live, today)
    check_calendar_runway(cal, today)

    # Persist merged live data as the new snapshot so future outages degrade gracefully.
    if not args.offline and not args.no_save:
        save_snapshot(live)


if __name__ == "__main__":
    main()
