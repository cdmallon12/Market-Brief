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
import json
import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "index.html"


def load_snapshot():
    return json.loads((DATA / "fallback.json").read_text(encoding="utf-8"))


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

    if sofr is not None:
        ctx["cre_tiles"][2]["val"] = f"{sofr:.2f}"
        if "SOFR" in tick_by_name:
            tick_by_name["SOFR"]["vl"] = f"{sofr:.2f}%"


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
        tiles[2]["note"] = "Fed's preferred gauge — still above the 2% goal"
        if "Core PCE" in tick:
            tick["Core PCE"]["vl"] = f"{p['yoy']:.1f}%"

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
    today = dt.date.today()
    ctx["meta"]["date_line"] = today.strftime("%A · %b %d, %Y")
    ctx["meta"]["as_of"] = "Auto-compiled from public sources · " + today.strftime("%b %d, %Y")
    ctx["meta"]["compiled"] = "Compiled " + today.strftime("%b %d, %Y") + " · The Standpoint Brief"
    ctx["meta"]["generated_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gather_live(ctx, offline):
    if offline:
        print("[info] offline mode — snapshot only")
        return
    from data import fetch  # imported here so --offline needs no network libs at import

    try:
        idx = fetch.fetch_indices()
        if idx:
            apply_indices(ctx, idx)
            print(f"[ok] indices: {', '.join(idx)}")
    except Exception as e:
        print(f"[warn] indices step: {e}")

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
            print("[info] fred: no FRED_API_KEY set — macro tiles use snapshot values")
    except Exception as e:
        print(f"[warn] fred step: {e}")


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
    }
    html = tmpl.render(chart_data_json=json.dumps(chart_data), **ctx)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"[done] wrote {OUT.relative_to(ROOT)} ({len(html):,} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="build from snapshot only")
    ap.add_argument("--no-save", action="store_true", help="do not update fallback.json")
    args = ap.parse_args()

    ctx = load_snapshot()
    live = copy.deepcopy(ctx)
    gather_live(live, args.offline)
    refresh_dates(live)
    render(live)

    # Persist merged live data as the new snapshot so future outages degrade gracefully.
    if not args.offline and not args.no_save:
        save_snapshot(live)


if __name__ == "__main__":
    main()
