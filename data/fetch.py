"""
Keyless data fetchers for The Standpoint Brief.

Every fetcher is defensive: on any network/parse error it returns None (or an
empty list), and build.py falls back to the last-known-good snapshot in
fallback.json. A single dead source therefore never breaks the daily build.

Sources used here need NO API key:
  - Indices .......... Stooq daily CSV        (https://stooq.com)
  - Treasury curve ... US Treasury XML feed   (home.treasury.gov)
  - SOFR / EFFR ...... NY Fed markets API      (markets.newyorkfed.org)
  - Headlines ........ RSS feeds via feedparser

To add richer macro data (CPI, PCE, GDP, Fed funds target), sign up for a free
FRED key and implement fetch_fred() — see README "Upgrading to FRED".
"""

from __future__ import annotations
import csv
import io
import os
import datetime as dt
import xml.etree.ElementTree as ET

import requests
try:
    import feedparser
except Exception:  # feedparser optional; headlines just fall back if missing
    feedparser = None

UA = {"User-Agent": "standpoint-brief/1.0 (+https://github.com/)"}
TIMEOUT = 20


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------- #
# Indices (Stooq)                                                             #
# --------------------------------------------------------------------------- #
STOOQ_SYMBOLS = {
    "spx": "^spx",   # S&P 500
    "ndq": "^ndq",   # Nasdaq Composite
    "dji": "^dji",   # Dow Jones Industrial Average
    "vix": "^vix",   # CBOE Volatility Index
}


def _stooq_series(symbol):
    """Return [(date_str, close_float), ...] ascending, or [] on failure."""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    txt = _get(url).text.strip()
    rows = list(csv.DictReader(io.StringIO(txt)))
    out = []
    for r in rows:
        c = r.get("Close")
        if c in (None, "", "N/A"):
            continue
        try:
            out.append((r["Date"], float(c)))
        except ValueError:
            continue
    return out


def _yoy(series):
    """Percent change vs the close nearest to one year before the last date."""
    if len(series) < 30:
        return None
    try:
        last_date = dt.date.fromisoformat(series[-1][0])
    except Exception:
        return None
    target = last_date - dt.timedelta(days=365)
    # walk backward to the first row on/before target
    prior = None
    for d, c in series:
        try:
            dd = dt.date.fromisoformat(d)
        except Exception:
            continue
        if dd <= target:
            prior = c
        else:
            break
    if not prior:
        return None
    return (series[-1][1] - prior) / prior * 100


def fetch_indices():
    """{'spx': {'value','change_pct','yoy_pct','date'}, ...} — missing keys omitted."""
    out = {}
    for key, sym in STOOQ_SYMBOLS.items():
        try:
            s = _stooq_series(sym)
            if len(s) < 2:
                continue
            last, prev = s[-1][1], s[-2][1]
            chg = (last - prev) / prev * 100 if prev else 0.0
            out[key] = {"value": last, "change_pct": chg, "yoy_pct": _yoy(s), "date": s[-1][0]}
        except Exception as e:
            print(f"[warn] indices {sym}: {e}")
    return out


# --------------------------------------------------------------------------- #
# Treasury par yield curve (official XML feed)                                #
# --------------------------------------------------------------------------- #
# Map the feed's element suffixes to (label, x-years, show-axis-tick, show-value-label)
CURVE_FIELDS = [
    ("BC_1MONTH",  "1M",  1 / 12, True,  True),
    ("BC_3MONTH",  "3M",  0.25,   False, False),
    ("BC_6MONTH",  "6M",  0.5,    False, False),
    ("BC_1YEAR",   "1Y",  1,      False, False),
    ("BC_2YEAR",   "2Y",  2,      True,  True),
    ("BC_3YEAR",   "3Y",  3,      False, False),
    ("BC_5YEAR",   "5Y",  5,      True,  False),
    ("BC_7YEAR",   "7Y",  7,      False, False),
    ("BC_10YEAR",  "10Y", 10,     True,  True),
    ("BC_20YEAR",  "20Y", 20,     True,  False),
    ("BC_30YEAR",  "30Y", 30,     True,  True),
]
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
}


def fetch_treasury_curve():
    """
    Return {'points': [{t,x,y,axis,mark}], 'date': 'Mon DD, YYYY',
            'y2': float, 'y10': float, 'y30': float, 'spread_2s10s_bps': int} or None.
    """
    ym = dt.date.today().strftime("%Y%m")
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           "interest-rates/pages/xml?data=daily_treasury_yield_curve"
           f"&field_tdr_date_value_month={ym}")
    try:
        root = ET.fromstring(_get(url).content)
    except Exception as e:
        print(f"[warn] treasury: {e}")
        return None

    entries = root.findall(".//a:entry", NS)
    if not entries:
        return None
    props = entries[-1].find(".//m:properties", NS)  # latest date in month

    def val(suffix):
        el = props.find(f"d:{suffix}", NS)
        if el is None or not (el.text and el.text.strip()):
            return None
        return float(el.text)

    date_el = props.find("d:NEW_DATE", NS)
    date_str = ""
    if date_el is not None and date_el.text:
        try:
            date_str = dt.datetime.fromisoformat(date_el.text.replace("Z", "")).strftime("%b %d, %Y")
        except Exception:
            date_str = date_el.text[:10]

    points = []
    for suffix, label, x, axis, mark in CURVE_FIELDS:
        y = val(suffix)
        if y is None:
            continue
        points.append({"t": label, "x": round(x, 3), "y": y, "axis": axis, "mark": mark})
    if len(points) < 4:
        return None

    y2 = val("BC_2YEAR")
    y10 = val("BC_10YEAR")
    y30 = val("BC_30YEAR")
    spread = int(round((y10 - y2) * 100)) if (y10 and y2) else None
    return {"points": points, "date": date_str, "y2": y2, "y10": y10, "y30": y30,
            "spread_2s10s_bps": spread}


# --------------------------------------------------------------------------- #
# SOFR + Effective Fed Funds (NY Fed markets API)                             #
# --------------------------------------------------------------------------- #
def _nyfed_rate(path):
    url = f"https://markets.newyorkfed.org/api/rates/{path}/last/1.json"
    data = _get(url).json()
    refs = data.get("refRates") or []
    if not refs:
        return None
    return float(refs[0]["percentRate"])


def fetch_sofr():
    try:
        return _nyfed_rate("secured/sofr")
    except Exception as e:
        print(f"[warn] sofr: {e}")
        return None


def fetch_effr():
    """Effective Federal Funds Rate (a keyless proxy for the policy rate)."""
    try:
        return _nyfed_rate("unsecured/effr")
    except Exception as e:
        print(f"[warn] effr: {e}")
        return None


# --------------------------------------------------------------------------- #
# Headlines via RSS                                                           #
# --------------------------------------------------------------------------- #
MARKET_FEEDS = [
    ("CNBC Markets",  "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Finance",  "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
]
CRE_FEEDS = [
    ("Commercial Observer", "https://commercialobserver.com/feed/"),
    ("CRE Daily",           "https://www.credaily.com/feed/"),
    ("The Real Deal",       "https://therealdeal.com/feed/"),
    ("GlobeSt",             "https://www.globest.com/feed/"),
]


def _rss_items(feeds, limit):
    if feedparser is None:
        return []
    items = []
    for source, url in feeds:
        try:
            f = feedparser.parse(url)
            for e in f.entries[:6]:
                when = ""
                if getattr(e, "published_parsed", None):
                    when = dt.datetime(*e.published_parsed[:6]).strftime("%b %d")
                items.append({
                    "source": source,
                    "title": (e.get("title") or "").strip(),
                    "link": e.get("link") or "",
                    "when": when,
                    "_ts": getattr(e, "published_parsed", None),
                })
        except Exception as ex:
            print(f"[warn] rss {source}: {ex}")
    # newest first when timestamps exist
    items.sort(key=lambda x: x["_ts"] or dt.datetime.min.timetuple(), reverse=True)
    for it in items:
        it.pop("_ts", None)
    # de-dup by title
    seen, deduped = set(), []
    for it in items:
        k = it["title"].lower()
        if k and k not in seen:
            seen.add(k)
            deduped.append(it)
    return deduped[:limit]


def fetch_market_headlines(limit=6):
    return _rss_items(MARKET_FEEDS, limit)


def fetch_cre_headlines(limit=6):
    return _rss_items(CRE_FEEDS, limit)


# --------------------------------------------------------------------------- #
# FRED — authoritative macro data (needs a free API key in FRED_API_KEY)       #
# --------------------------------------------------------------------------- #
# Get a key at https://fredaccount.stlouisfed.org/apikeys and add it as the
# repo secret FRED_API_KEY. Without a key, fetch_fred() returns None and the
# macro tiles fall back to their snapshot values.
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_obs(series_id, key, limit=14):
    """Return [(date, value_float), ...] newest-first, skipping missing points."""
    params = {
        "series_id": series_id, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": limit,
    }
    data = _get(FRED_BASE, params=params).json()
    out = []
    for o in data.get("observations", []):
        v = o.get("value")
        if v in (None, "", "."):
            continue
        try:
            out.append((o["date"], float(v)))
        except ValueError:
            continue
    return out


def _yoy_from_index(obs):
    """YoY % for a monthly index series: latest vs the reading 12 months back."""
    if len(obs) >= 13:
        return (obs[0][1] - obs[12][1]) / obs[12][1] * 100
    return None


def _month_label(date_str):
    try:
        return dt.date.fromisoformat(date_str).strftime("%b")
    except Exception:
        return ""


def _quarter_label(date_str):
    try:
        d = dt.date.fromisoformat(date_str)
        return f"Q{(d.month - 1) // 3 + 1}"
    except Exception:
        return ""


def fetch_fred():
    """
    Authoritative macro values from FRED. Returns a dict (missing keys omitted)
    or None if no API key is configured. Series:
      DFEDTARL/U      fed funds target range (lower/upper)
      CPIAUCSL        headline CPI index  -> YoY
      CPILFESL        core CPI index      -> YoY
      PCEPILFE        core PCE index      -> YoY
      A191RL1Q225SBEA real GDP, % change SAAR (already a rate)
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return None

    out = {}
    try:
        lo = _fred_obs("DFEDTARL", key, limit=1)
        up = _fred_obs("DFEDTARU", key, limit=1)
        if lo and up:
            out["fed_lower"], out["fed_upper"] = lo[0][1], up[0][1]
    except Exception as e:
        print(f"[warn] fred fed funds: {e}")

    try:
        cpi = _fred_obs("CPIAUCSL", key)
        cur, prev = _yoy_from_index(cpi), _yoy_from_index(cpi[1:]) if len(cpi) > 13 else None
        if cur is not None:
            out["cpi"] = {"yoy": cur, "prev_yoy": prev, "month": _month_label(cpi[0][0])}
    except Exception as e:
        print(f"[warn] fred cpi: {e}")

    try:
        core = _fred_obs("CPILFESL", key)
        cyoy = _yoy_from_index(core)
        if cyoy is not None:
            out["core_cpi"] = {"yoy": cyoy}
    except Exception as e:
        print(f"[warn] fred core cpi: {e}")

    try:
        pce = _fred_obs("PCEPILFE", key)
        pyoy = _yoy_from_index(pce)
        if pyoy is not None:
            out["core_pce"] = {"yoy": pyoy, "month": _month_label(pce[0][0])}
    except Exception as e:
        print(f"[warn] fred core pce: {e}")

    try:
        gdp = _fred_obs("A191RL1Q225SBEA", key, limit=2)
        if gdp:
            out["gdp"] = {
                "rate": gdp[0][1],
                "prev": gdp[1][1] if len(gdp) > 1 else None,
                "quarter": _quarter_label(gdp[0][0]),
            }
    except Exception as e:
        print(f"[warn] fred gdp: {e}")

    # --- Credit spreads (ICE BofA option-adjusted spreads, via FRED; daily) --- #
    try:
        bbb = _fred_obs("BAMLC0A4CBBB", key, limit=2)   # investment-grade (BBB) OAS
        if bbb:
            out["bbb_oas"] = {"value": bbb[0][1], "prev": bbb[1][1] if len(bbb) > 1 else None}
    except Exception as e:
        print(f"[warn] fred bbb oas: {e}")
    try:
        hy = _fred_obs("BAMLH0A0HYM2", key, limit=2)    # high-yield OAS
        if hy:
            out["hy_oas"] = {"value": hy[0][1], "prev": hy[1][1] if len(hy) > 1 else None}
    except Exception as e:
        print(f"[warn] fred hy oas: {e}")

    # --- CRE credit fundamentals (all FRED) --- #
    try:
        dq = _fred_obs("DRCRELEXFACBS", key, limit=2)   # CRE delinquency rate, banks (quarterly)
        if dq:
            out["cre_delinq"] = {"value": dq[0][1],
                                 "prev": dq[1][1] if len(dq) > 1 else None,
                                 "quarter": _quarter_label(dq[0][0])}
    except Exception as e:
        print(f"[warn] fred cre delinquency: {e}")
    try:
        loans = _fred_obs("CREACBM027NBOG", key)        # CRE loans outstanding, banks ($B, monthly)
        if loans:
            out["cre_loans"] = {"value": loans[0][1],
                                "yoy": _yoy_from_index(loans),
                                "month": _month_label(loans[0][0])}
    except Exception as e:
        print(f"[warn] fred cre loans: {e}")
    try:
        sloos = _fred_obs("SUBLPDRCSN", key, limit=1)   # SLOOS CRE (nonfarm nonres) net % tightening
        if sloos:
            out["sloos_cre"] = {"value": sloos[0][1], "quarter": _quarter_label(sloos[0][0])}
    except Exception as e:
        print(f"[warn] fred sloos cre: {e}")

    return out or None


# --------------------------------------------------------------------------- #
# Watchlist — public-market CRE proxies (Stooq, keyless)                        #
# --------------------------------------------------------------------------- #
# Public equities price CRE stress in real time and often lead the private
# market. KRE (regional banks) gauges lender stress; the REITs cover the major
# property types.
WATCHLIST = [
    ("kre.us", "KRE", "Regional banks — hold most CRE debt"),
    ("vnq.us", "VNQ", "Broad U.S. REITs"),
    ("bxp.us", "BXP", "Office (Boston Properties)"),
    ("pld.us", "PLD", "Industrial / logistics (Prologis)"),
    ("avb.us", "AVB", "Apartments (AvalonBay)"),
    ("spg.us", "SPG", "Retail malls (Simon)"),
]


def fetch_watchlist():
    """{'KRE': {'value','change_pct','yoy_pct','date'}, ...} — missing omitted."""
    out = {}
    for sym, code, _desc in WATCHLIST:
        try:
            s = _stooq_series(sym)
            if len(s) < 2:
                continue
            last, prev = s[-1][1], s[-2][1]
            chg = (last - prev) / prev * 100 if prev else 0.0
            out[code] = {"value": last, "change_pct": chg, "yoy_pct": _yoy(s), "date": s[-1][0]}
        except Exception as e:
            print(f"[warn] watchlist {sym}: {e}")
    return out
