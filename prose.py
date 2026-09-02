"""Deterministic editorial prose, derived only from metrics already on the page.

Every sentence this module produces is a description of a number that is
rendered elsewhere in the same build. Nothing here forecasts, infers intent,
or introduces a figure the page does not already show, which means the copy
cannot contradict the tiles and cannot go stale while the tiles stay live.

If an input is missing the clause that needs it is dropped rather than filled
in, so a failed fetcher shortens the prose instead of breaking it.
"""
from __future__ import annotations

import datetime as dt
import re

# Descriptive bands for a daily percentage move. Deliberately plain: these
# describe magnitude only and never imply cause or direction of travel.
_BANDS = [
    (0.10, "was little changed", "was little changed"),
    (0.50, "edged higher", "edged lower"),
    (1.50, "rose", "fell"),
    (float("inf"), "climbed", "dropped"),
]


def _num(val):
    """Pull a float out of a display string ('$3,119', '-0.03pp', '+47')."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(val).replace("−", "-"))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _signed(val):
    """Signed float from a display string, honouring a leading minus or '−'."""
    n = _num(val)
    if n is None:
        return None
    return -abs(n) if re.search(r"[-−]", str(val).split(".")[0]) else n


def _move(pct):
    """('rose', 0.42) for a signed percentage move."""
    if pct is None:
        return None, None
    mag = abs(pct)
    for limit, up, down in _BANDS:
        if mag < limit:
            return (up if pct >= 0 else down), mag
    return None, None


def _tile(tiles, label_startswith):
    for t in tiles or []:
        if str(t.get("lbl", "")).lower().startswith(label_startswith.lower()):
            return t
    return None


def _tile_delta(tile):
    """Signed daily change for a tile, from its delta block."""
    if not tile:
        return None
    d = tile.get("delta") or {}
    n = _num(d.get("txt"))
    if n is None:
        return None
    return n * (-1 if d.get("dir", 0) < 0 else 1)


def _ticker(ctx, name):
    for row in ctx.get("ticker") or []:
        if str(row.get("nm", "")).lower() == name.lower():
            return row
    return None


def _joinsent(parts):
    return " ".join(p for p in parts if p)


def _next_event(cal, today, contains=None):
    best = None
    for e in (cal or {}).get("events") or []:
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if d < today:
            continue
        if contains and contains.lower() not in str(e.get("title", "")).lower():
            continue
        if best is None or d < best[0]:
            best = (d, e)
    return best


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def _markets(ctx):
    tiles = ctx.get("markets_tiles") or []
    spx, ndx, dow, vix = (_tile(tiles, x) for x in ("S&P 500", "Nasdaq", "Dow", "VIX"))
    out = {}

    spx_chg = _tile_delta(spx)
    verb, mag = _move(spx_chg)
    if verb and spx:
        head = f"The S&P 500 {verb}"
        if mag is not None and mag >= 0.10:
            head += f" {mag:.2f}%"
        out["headline"] = head

    legs = []
    for tile, name in ((spx, "S&P 500"), (ndx, "Nasdaq"), (dow, "Dow")):
        chg = _tile_delta(tile)
        if tile and tile.get("val") and chg is not None:
            legs.append(f"{name} {tile['val']} ({chg:+.2f}%)")
    stand = []
    if legs:
        stand.append("At the last close: " + "; ".join(legs) + ".")
    if vix and vix.get("val"):
        vchg = _tile_delta(vix)
        tail = f" ({vchg:+.2f}%)" if vchg is not None else ""
        stand.append(f"VIX {vix['val']}{tail}.")
    if stand:
        out["standfirst"] = _joinsent(stand)

    # Breadth across the CRE-proxy watchlist — a real reading, not a narrative.
    rows = [r for r in (ctx.get("watchlist") or []) if _signed(r.get("chg")) is not None]
    if rows:
        ups = [r for r in rows if _signed(r["chg"]) > 0]
        best = max(rows, key=lambda r: _signed(r["chg"]))
        worst = min(rows, key=lambda r: _signed(r["chg"]))
        out["card1_title"] = "Breadth across the watchlist"
        out["card1_sub"] = "Real-estate proxies, latest close"
        # NOTE: the template iterates card bodies as a list of paragraphs
        # ({% for para in ... %}), so these must be lists, never strings.
        out["card1_body"] = [
            f"{len(ups)} of {len(rows)} names on the watchlist closed higher.",
            f"{best['code']} led at {_signed(best['chg']):+.2f}%; "
            f"{worst['code']} lagged at {_signed(worst['chg']):+.2f}%.",
        ]
    return out


def _economy(ctx, cal, today):
    tiles = ctx.get("economy_tiles") or []
    ff = _tile(tiles, "Fed Funds")
    cpi = _tile(tiles, "CPI")
    pce = _tile(tiles, "Core PCE")
    gdp = _tile(tiles, "U.S. GDP")
    world = _tile(tiles, "Global GDP")
    out = {}

    bits = []
    if ff and ff.get("val"):
        bits.append(f"the federal funds target at {ff['val']}{ff.get('unit', '')}")
    if cpi and cpi.get("val"):
        bits.append(f"headline CPI at {cpi['val']}% year over year")
    if pce and pce.get("val"):
        bits.append(f"core PCE at {pce['val']}%")
    if bits:
        out["headline"] = "Policy and prices: " + ", ".join(bits)

    stand = []
    if gdp and gdp.get("val"):
        stand.append(f"Real GDP was last reported at {gdp['val']}% annualized.")
    if world and world.get("val"):
        stand.append(f"The IMF puts world real growth at {world['val']}%.")
    nxt = _next_event(cal, today, contains="FOMC")
    if nxt:
        d, _ = nxt
        stand.append(f"The next FOMC decision is scheduled for {d.strftime('%B %-d')}.")
    if stand:
        out["standfirst"] = _joinsent(stand)

    para = []
    if cpi and pce:
        para.append(
            f"CPI stands at {cpi.get('val')}% and core PCE at {pce.get('val')}%, "
            f"against the Federal Reserve's stated 2% objective."
        )
    if ff:
        para.append(
            f"The target range has been {ff.get('val')}{ff.get('unit', '')} as of the "
            f"most recent decision."
        )
    if para:
        out["body"] = para
    out["callout"] = None          # removed: was forward-looking commentary
    return out


def _credit(ctx):
    tiles = ctx.get("credit_tiles") or []
    ig = _tile(tiles, "Investment-Grade")
    hy = _tile(tiles, "High-Yield")
    prem = _tile(tiles, "HY")
    sp = ctx.get("curve_spreads") or {}
    out = {}

    if ig and hy and ig.get("val") and hy.get("val"):
        out["headline"] = (
            f"Investment-grade OAS at {ig['val']}%, high-yield at {hy['val']}%"
        )
    stand = []
    if prem and prem.get("val"):
        stand.append(
            f"The high-yield premium over investment grade is {prem['val']} percentage points."
        )
    if sp.get("2s10s"):
        stand.append(f"The 2s10s Treasury spread is {sp['2s10s']} bps.")
    if stand:
        out["standfirst"] = _joinsent(stand)

    curve_bits = [f"{k} {v} bps" for k, v in
                  (("2s10s", sp.get("2s10s")), ("3m10y", sp.get("3m10y")),
                   ("5s30s", sp.get("5s30s")), ("10s30s", sp.get("10s30s"))) if v]
    if curve_bits:
        out["card_title"] = "Curve spreads"
        out["card_sub"] = "Current levels, basis points"
        out["card_body"] = ["Across the curve: " + "; ".join(curve_bits) + "."]
    return out


def _news(ctx):
    heads = ctx.get("headlines") or []
    cre_heads = ctx.get("cre_headlines") or []
    out = {}
    if heads or cre_heads:
        srcs = {h.get("src") for h in list(heads) + list(cre_heads) if h.get("src")}
        out["headline"] = "Latest headlines"
        parts = [f"{len(heads)} market and {len(cre_heads)} commercial real-estate headlines"]
        if srcs:
            parts.append(f"drawn from {len(srcs)} sources")
        out["standfirst"] = ", ".join(parts) + ", newest first."
    return out


def _cre(ctx):
    tiles = ctx.get("cre_tiles") or []
    fin = ctx.get("cre_fin_tiles") or []
    fund = ctx.get("cre_fund_tiles") or []
    t10 = _tile(tiles, "10-Yr")
    sofr = _tile(tiles, "SOFR")
    pmms = _tile(fin, "30-Yr Mortgage")
    agency = _tile(fin, "Illustrative Agency")
    delinq = _tile(fund, "CRE Loan Delinquency")
    stds = _tile(fund, "Bank Lending Standards")
    out = {}

    if t10 and sofr and t10.get("val") and sofr.get("val"):
        out["headline"] = (
            f"The 10-year at {t10['val']}%, overnight SOFR at {sofr['val']}%"
        )
    stand = []
    if agency and agency.get("val"):
        stand.append(f"An illustrative agency coupon prices at {agency['val']}%.")
    if pmms and pmms.get("val"):
        stand.append(f"The Freddie Mac 30-year survey rate is {pmms['val']}%.")
    if stand:
        out["standfirst"] = _joinsent(stand)

    body = []
    if delinq and delinq.get("val"):
        d = (delinq.get("delta") or {}).get("txt")
        body.append(
            f"Bank CRE loan delinquency was {delinq['val']}%"
            + (f", {d} against the prior quarter" if d else "")
            + "."
        )
    if stds and stds.get("val") is not None:
        n = _signed(stds.get("val"))
        if n is not None:
            direction = "easing" if n < 0 else "tightening"
            body.append(
                f"On net {abs(n):.1f}% of banks reported {direction} standards on CRE loans."
            )
    if body:
        out["card_title"] = "Credit conditions"
        out["card_sub"] = "Bank CRE lending, most recent survey"
        out["card_body"] = list(body)
    out["callout"] = None          # removed: was forward-looking commentary
    return out


# --------------------------------------------------------------------------

GENERATED = {
    "markets": ["headline", "standfirst", "card1_title", "card1_sub", "card1_body"],
    "economy": ["headline", "standfirst", "body", "callout"],
    "credit": ["headline", "standfirst", "card_title", "card_sub", "card_body"],
    "news": ["headline", "standfirst"],
    "cre": ["headline", "standfirst", "card_title", "card_sub", "card_body", "callout"],
}


def build_prose(ctx, cal, today):
    """Return {section: {field: text}}. A None value means delete the field."""
    return {
        "markets": _markets(ctx),
        "economy": _economy(ctx, cal, today),
        "credit": _credit(ctx),
        "news": _news(ctx),
        "cre": _cre(ctx),
    }


def apply_prose(ctx, cal, today):
    """Overwrite every field this module owns; clear the ones it cannot fill.

    The clearing half matters as much as the writing half. If a fetcher fails
    and a metric is missing, the field is REMOVED rather than left holding
    whatever the snapshot said. Otherwise an outage would quietly resurrect
    hand-written copy from months ago and present it as current — the exact
    failure this module exists to prevent.
    """
    written, removed = 0, 0
    generated = build_prose(ctx, cal, today)
    for section, owned in GENERATED.items():
        block = ctx.get(section)
        if not isinstance(block, dict):
            continue
        fields = generated.get(section) or {}
        for key in owned:
            val = fields.get(key)
            if val is None:
                if block.pop(key, None) is not None:
                    removed += 1
            else:
                block[key] = val
                written += 1
    print(f"[prose] deterministic: {written} fields written, {removed} cleared for lack of data")
    return written, removed
