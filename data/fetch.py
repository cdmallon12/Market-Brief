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


def _treasury_entries(ym):
    """Return the <entry> list from the Treasury par-yield feed for month YYYYMM, or []."""
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           "interest-rates/pages/xml?data=daily_treasury_yield_curve"
           f"&field_tdr_date_value_month={ym}")
    try:
        root = ET.fromstring(_get(url).content)
        return root.findall(".//a:entry", NS)
    except Exception as e:
        print(f"[warn] treasury {ym}: {e}")
        return []


def fetch_treasury_curve():
    """
    Return {'points': [{t,x,y,axis,mark}], 'date': 'Mon DD, YYYY',
            'y2': float, 'y10': float, 'y30': float, 'spread_2s10s_bps': int} or None.

    Queries the current month, then falls back to the previous month when the
    current one has no rows yet — e.g. on the 1st of a month before that day's
    curve posts after the close — so the latest available reading still shows
    instead of the page going stale at the month boundary.
    """
    today = dt.date.today()
    entries = _treasury_entries(today.strftime("%Y%m"))
    if not entries:
        prev_ym = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y%m")
        entries = _treasury_entries(prev_ym)
    if not entries:
        return None
    props = entries[-1].find(".//m:properties", NS)  # latest available date

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

    y3m = val("BC_3MONTH")
    y2 = val("BC_2YEAR")
    y5 = val("BC_5YEAR")
    y10 = val("BC_10YEAR")
    y30 = val("BC_30YEAR")

    def bps(a, b):
        return int(round((a - b) * 100)) if (a is not None and b is not None) else None

    return {"points": points, "date": date_str, "y2": y2, "y10": y10, "y30": y30,
            "spread_2s10s_bps": bps(y10, y2),
            "spread_3m10y_bps": bps(y10, y3m),
            "spread_5s30s_bps": bps(y30, y5),
            "spread_10s30s_bps": bps(y30, y10)}


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
    import calendar
    items = []
    for source, url in feeds:
        try:
            f = feedparser.parse(url)
            for e in f.entries[:6]:
                when, epoch = "", 0
                pp = getattr(e, "published_parsed", None)
                if pp:
                    # feedparser's published_parsed is UTC; label the date in
                    # Eastern so a late-evening ET article isn't stamped tomorrow.
                    epoch = calendar.timegm(pp)
                    try:
                        from zoneinfo import ZoneInfo
                        when = dt.datetime.fromtimestamp(epoch, ZoneInfo("America/New_York")).strftime("%b %d")
                    except Exception:
                        when = dt.datetime.utcfromtimestamp(epoch).strftime("%b %d")
                items.append({
                    "source": source,
                    "title": (e.get("title") or "").strip(),
                    "link": e.get("link") or "",
                    "when": when,
                    "_ts": epoch,
                })
        except Exception as ex:
            print(f"[warn] rss {source}: {ex}")
    # newest first
    items.sort(key=lambda x: x["_ts"] or 0, reverse=True)
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

    # --- Energy (the inflation driver) + headline PCE + mortgage rate --- #
    try:
        brent = _fred_obs("DCOILBRENTEU", key, limit=2)   # Brent crude, $/bbl (daily)
        if brent:
            out["brent"] = {"value": brent[0][1], "prev": brent[1][1] if len(brent) > 1 else None}
    except Exception as e:
        print(f"[warn] fred brent: {e}")
    try:
        wti = _fred_obs("DCOILWTICO", key, limit=2)       # WTI crude, $/bbl (daily)
        if wti:
            out["wti"] = {"value": wti[0][1], "prev": wti[1][1] if len(wti) > 1 else None}
    except Exception as e:
        print(f"[warn] fred wti: {e}")
    try:
        hpce = _fred_obs("PCEPI", key)                    # headline PCE price index (monthly) -> YoY
        hyoy = _yoy_from_index(hpce)
        if hyoy is not None:
            out["headline_pce"] = {"yoy": hyoy, "month": _month_label(hpce[0][0])}
    except Exception as e:
        print(f"[warn] fred headline pce: {e}")
    try:
        mort = _fred_obs("MORTGAGE30US", key, limit=2)    # Freddie Mac PMMS 30-yr (weekly, %)
        if mort:
            out["mortgage30"] = {"value": mort[0][1], "prev": mort[1][1] if len(mort) > 1 else None,
                                 "date": mort[0][0]}
    except Exception as e:
        print(f"[warn] fred mortgage30: {e}")

    return out or None


# --------------------------------------------------------------------------- #
# IMF global growth (World Economic Outlook via the keyless DataMapper API)      #
# --------------------------------------------------------------------------- #
def fetch_imf_gdp():
    """World real-GDP growth (%) for the current and next year from the IMF WEO.

    Uses the free, keyless IMF DataMapper API (NGDP_RPCH = real GDP growth,
    WLD = world aggregate). Returns {'year','value','next_year','next_value'}
    or None. WEO revises only a few times a year, so this rarely changes — but
    keeping it live means the tile is never wrong at a refresh boundary.
    """
    url = "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/WLD"
    try:
        data = _get(url).json()
        series = data.get("values", {}).get("NGDP_RPCH", {}).get("WLD", {})
        if not series:
            return None
        this_year = dt.date.today().year

        def _v(y):
            raw = series.get(str(y))
            try:
                return round(float(raw), 1) if raw is not None else None
            except (TypeError, ValueError):
                return None

        cur = _v(this_year)
        if cur is None:                       # not yet published for this year
            return None
        return {"year": this_year, "value": cur,
                "next_year": this_year + 1, "next_value": _v(this_year + 1)}
    except Exception as e:
        print(f"[warn] imf gdp: {e}")
        return None


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
    ("vmrk.us", "VMRK", "Apartments (Vivmark Residential — ex-AvalonBay/EQR)"),
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


# --------------------------------------------------------------------------- #
# Intraday / market-open quotes (Yahoo Finance, keyless, ~15-min delayed)       #
# --------------------------------------------------------------------------- #
# Gives today's OPEN, the current (delayed) price, and change vs. the prior
# close — so the brief can show "how the market is trading today" in the
# morning, not just yesterday's close. Unofficial endpoint: if it fails, the
# caller falls back to Stooq end-of-day data.
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
# Our internal keys/codes -> Yahoo symbols
YAHOO_INDEX = {"spx": "^GSPC", "ndq": "^IXIC", "dji": "^DJI", "vix": "^VIX"}
YAHOO_WATCH = {"KRE": "KRE", "VNQ": "VNQ", "BXP": "BXP", "PLD": "PLD", "VMRK": "VMRK", "SPG": "SPG"}


def _yahoo_quote(sym):
    """
    Return {'price','prev_close','change_pct','open'(opt),'open_is_today','yoy_pct'(opt),
            'ts'(epoch, opt)} for one Yahoo symbol, or None on any failure.
    """
    url = YAHOO_CHART.format(sym=sym)
    r = _get(url, params={"range": "1y", "interval": "1d"})
    data = r.json()
    res = (((data or {}).get("chart") or {}).get("result") or [None])[0]
    if not res:
        return None
    meta = res.get("meta") or {}
    price = meta.get("regularMarketPrice")

    ts = res.get("timestamp") or []
    quote = (((res.get("indicators") or {}).get("quote") or [{}]))[0]
    opens = quote.get("open") or []
    closes = quote.get("close") or []

    # Prior close = the second-to-last daily bar. NOT meta.chartPreviousClose:
    # on a multi-day range Yahoo sets that to the close BEFORE the requested
    # window, so with range=1y it is a year-old price and the "day" change
    # silently becomes a year-over-year change. Fall back to the meta field
    # only if the bar history is too short to use.
    _closes = [c for c in closes if c is not None]
    if len(_closes) >= 2:
        prev = _closes[-2]
    else:
        prev = meta.get("chartPreviousClose", meta.get("previousClose"))

    if price is None or prev in (None, 0):
        return None
    out = {"price": float(price), "prev_close": float(prev),
           "change_pct": (float(price) - float(prev)) / float(prev) * 100,
           "ts": meta.get("regularMarketTime")}

    # Today's open: last bar's open, flagged whether that bar is actually today (ET).
    if opens and opens[-1] is not None and ts:
        try:
            from zoneinfo import ZoneInfo
            last_d = dt.datetime.fromtimestamp(ts[-1], ZoneInfo("America/New_York")).date()
            today_et = dt.datetime.now(ZoneInfo("America/New_York")).date()
            out["open"] = float(opens[-1])
            out["open_is_today"] = (last_d == today_et)
        except Exception:
            out["open"] = float(opens[-1])
            out["open_is_today"] = False

    # YoY from the earliest close in the ~1y window.
    firsts = _closes
    if firsts:
        base = firsts[0]
        if base:
            out["yoy_pct"] = (out["price"] - base) / base * 100
    return out


def fetch_quotes(mapping):
    """mapping: {key: yahoo_symbol} -> {key: quote_dict}. Missing keys omitted."""
    out = {}
    for key, sym in mapping.items():
        try:
            q = _yahoo_quote(sym)
            if q:
                out[key] = q
        except Exception as e:
            print(f"[warn] yahoo {sym}: {e}")
    return out


def fetch_index_quotes():
    return fetch_quotes(YAHOO_INDEX)


def fetch_watchlist_quotes():
    return fetch_quotes(YAHOO_WATCH)
