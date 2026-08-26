import os, datetime as dt
from data import fetch

class FakeResp:
    def __init__(self, js): self._js=js
    def raise_for_status(self): pass
    def json(self): return self._js

# Build realistic FRED-style responses per series_id, honoring the limit param.
def make_index(series_id, start, monthly_step, months=14):
    # newest-first list of observations, index rising by monthly_step each month
    obs=[]
    d=dt.date(2026,7,1)
    for i in range(months):
        val=start - i*monthly_step  # older = smaller
        obs.append({"date": (d.replace(day=1) - dt.timedelta(days=30*i)).isoformat(), "value": f"{val:.3f}"})
    return obs

SERIES = {
    "DFEDTARL":[{"date":"2026-08-25","value":"3.50"}],
    "DFEDTARU":[{"date":"2026-08-25","value":"3.75"}],
    # CPI index ~ so that 12-mo change ≈ 3.4%: latest 322.0, 12mo ago 311.4
    "CPIAUCSL":[{"date":f"2026-{7-(i//1):02d}-01" if i<7 else f"2025-{12-(i-7):02d}-01","value":f"{322.0 - i*0.9:.3f}"} for i in range(14)],
    "CPILFESL":[{"date":"2026-07-01","value":"330.0"}]+[{"date":"x","value":f"{330.0 - i*0.7:.3f}"} for i in range(1,14)],
    "PCEPILFE":[{"date":"2026-07-01","value":"128.0"}]+[{"date":"x","value":f"{128.0 - i*0.35:.3f}"} for i in range(1,14)],
    "A191RL1Q225SBEA":[{"date":"2026-04-01","value":"1.5"},{"date":"2026-01-01","value":"2.3"}],
}

def fake_get(url, params=None, **k):
    sid=params["series_id"]; lim=int(params.get("limit",14))
    return FakeResp({"observations": SERIES[sid][:lim]})

fetch._get = fake_get
os.environ["FRED_API_KEY"]="TESTKEY"
f = fetch.fetch_fred()
print("fed range:", f["fed_lower"], "-", f["fed_upper"])
print("cpi yoy: %.2f%% (prev %.2f) month=%s" % (f["cpi"]["yoy"], f["cpi"]["prev_yoy"], f["cpi"]["month"]))
print("core cpi yoy: %.2f%%" % f["core_cpi"]["yoy"])
print("core pce yoy: %.2f%% month=%s" % (f["core_pce"]["yoy"], f["core_pce"]["month"]))
print("gdp: %.1f%% (prev %.1f) quarter=%s" % (f["gdp"]["rate"], f["gdp"]["prev"], f["gdp"]["quarter"]))
assert f["fed_lower"]==3.50 and f["fed_upper"]==3.75
assert f["gdp"]["quarter"]=="Q2" and f["gdp"]["rate"]==1.5 and f["gdp"]["prev"]==2.3
assert f["cpi"]["yoy"]>0 and f["core_pce"]["yoy"]>0

# no key -> None
os.environ.pop("FRED_API_KEY")
assert fetch.fetch_fred() is None
print("\nno-key path returns None: OK")
print("FRED PARSER TESTS PASSED")
