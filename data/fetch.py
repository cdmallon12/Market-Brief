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
