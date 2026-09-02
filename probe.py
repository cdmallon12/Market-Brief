"""Standalone API probes. Hits one endpoint, prints what came back, exits.

Nothing here touches the page. The point is to see a real response before any
fetcher is written against it, so parsing code is written against observed
JSON rather than documented JSON.

Usage:  python build.py --probe eia
Keys are read from the environment; none is ever written to disk or committed.
"""
from __future__ import annotations

import json
import os
import sys

import requests

TIMEOUT = 20
UA = {"User-Agent": "standpoint-brief-probe/1.0"}


def _show(label, url, params=None, redact=None):
    print(f"\n=== {label}")
    safe = dict(params or {})
    for k in (redact or []):
        if k in safe:
            safe[k] = "<redacted>"
    print(f"GET {url}")
    print(f"params: {safe}")
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"FAILED: {e}")
        return None
    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:500])
        return None
    try:
        data = r.json()
    except ValueError:
        print("not JSON. first 500 chars:")
        print(r.text[:500])
        return None
    print("--- parsed JSON (first 2500 chars) ---")
    print(json.dumps(data, indent=2)[:2500])
    return data


def probe_eia():
    key = os.environ.get("EIA_API_KEY")
    if not key:
        print("EIA_API_KEY is not set. Get a free key at https://www.eia.gov/opendata/register.php")
        print("Then:  export EIA_API_KEY=...   (macOS/Linux)")
        print("       $env:EIA_API_KEY='...'   (PowerShell)")
        return
    # Spot prices: WTI and Henry Hub, most recent observations.
    _show(
        "EIA · petroleum spot prices (daily)",
        "https://api.eia.gov/v2/petroleum/pri/spt/data/",
        {"api_key": key, "frequency": "daily", "data[0]": "value",
         "sort[0][column]": "period", "sort[0][direction]": "desc",
         "length": 5},
        redact=["api_key"],
    )
    _show(
        "EIA · natural gas spot price (daily)",
        "https://api.eia.gov/v2/natural-gas/pri/fut/data/",
        {"api_key": key, "frequency": "daily", "data[0]": "value",
         "sort[0][column]": "period", "sort[0][direction]": "desc",
         "length": 5},
        redact=["api_key"],
    )


def probe_finnhub():
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        print("FINNHUB_API_KEY is not set. Free key at https://finnhub.io/register")
        return
    import datetime as dt
    today = dt.date.today()
    _show(
        "Finnhub · earnings calendar (next 14 days)",
        "https://finnhub.io/api/v1/calendar/earnings",
        {"from": today.isoformat(),
         "to": (today + dt.timedelta(days=14)).isoformat(),
         "token": key},
        redact=["token"],
    )


def probe_census():
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        print("CENSUS_API_KEY is not set. Free key at https://api.census.gov/data/key_signup.html")
        return
    _show(
        "Census · construction spending time series",
        "https://api.census.gov/data/timeseries/eits/vip",
        {"get": "cell_value,time_slot_id,category_code,data_type_code",
         "time": "from 2025", "key": key},
        redact=["key"],
    )


PROBES = {"eia": probe_eia, "finnhub": probe_finnhub, "census": probe_census}


def run(name):
    fn = PROBES.get(name)
    if not fn:
        print(f"unknown probe '{name}'. options: {', '.join(sorted(PROBES))}")
        sys.exit(2)
    fn()
