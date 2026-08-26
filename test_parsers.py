"""Offline tests: feed each parser a realistic sample payload via a fake _get."""
import types, datetime as dt
from data import fetch

class FakeResp:
    def __init__(self, text=None, content=None, js=None):
        self.text=text; self.content=content; self._js=js
    def raise_for_status(self): pass
    def json(self): return self._js

# 1) Stooq CSV -> indices + YoY
today = dt.date.today()
rows = ["Date,Open,High,Low,Close,Volume"]
for i in range(400):  # ~400 daily rows so YoY (365d) resolves
    d = today - dt.timedelta(days=400-i)
    close = 6000 + i*4  # steadily rising
    rows.append(f"{d.isoformat()},0,0,0,{close},0")
csv_text = "\n".join(rows)
fetch._get = lambda url, **k: FakeResp(text=csv_text)
idx = fetch.fetch_indices()
spx = idx["spx"]
assert spx["value"] == 6000+399*4, spx
assert abs(spx["change_pct"] - ((6000+399*4)-(6000+398*4))/(6000+398*4)*100) < 1e-6
assert spx["yoy_pct"] is not None and spx["yoy_pct"] > 0
print("1) indices OK  value=%.0f change=%.3f%% yoy=%.1f%%" % (spx["value"], spx["change_pct"], spx["yoy_pct"]))

# 2) Treasury XML -> curve
xml = '''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
<entry><content type="application/xml"><m:properties>
<d:NEW_DATE>2026-08-25T00:00:00</d:NEW_DATE>
<d:BC_1MONTH>3.79</d:BC_1MONTH><d:BC_3MONTH>3.9</d:BC_3MONTH><d:BC_6MONTH>3.98</d:BC_6MONTH>
<d:BC_1YEAR>4.04</d:BC_1YEAR><d:BC_2YEAR>4.24</d:BC_2YEAR><d:BC_3YEAR>4.31</d:BC_3YEAR>
<d:BC_5YEAR>4.41</d:BC_5YEAR><d:BC_7YEAR>4.55</d:BC_7YEAR><d:BC_10YEAR>4.70</d:BC_10YEAR>
<d:BC_20YEAR>5.21</d:BC_20YEAR><d:BC_30YEAR>5.23</d:BC_30YEAR>
</m:properties></content></entry></feed>'''
fetch._get = lambda url, **k: FakeResp(content=xml.encode())
c = fetch.fetch_treasury_curve()
assert c["y10"]==4.70 and c["y2"]==4.24 and c["y30"]==5.23, c
assert c["spread_2s10s_bps"]==46, c["spread_2s10s_bps"]
assert c["date"]=="Aug 25, 2026", c["date"]
assert len(c["points"])==11 and c["points"][0]["t"]=="1M", len(c["points"])
print("2) treasury OK  10Y=%.2f 2s10s=%dbps date=%s pts=%d" % (c["y10"], c["spread_2s10s_bps"], c["date"], len(c["points"])))

# 3) NY Fed SOFR JSON
fetch._get = lambda url, **k: FakeResp(js={"refRates":[{"percentRate":3.65}]})
assert fetch.fetch_sofr()==3.65
print("3) sofr OK  3.65")

# 4) RSS via feedparser (parse a raw RSS string directly)
import feedparser
rss = '''<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Debt Maturities Rise Amid 2026 CRE Pressure</title><link>https://ex.com/a</link><pubDate>Tue, 25 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>Office Values Stabilize in Q3</title><link>https://ex.com/b</link><pubDate>Mon, 24 Aug 2026 10:00:00 GMT</pubDate></item>
</channel></rss>'''
orig_parse = feedparser.parse
feedparser.parse = lambda url: orig_parse(rss)
items = fetch.fetch_cre_headlines(limit=5)
feedparser.parse = orig_parse
assert len(items)>=2 and items[0]["title"], items
assert items[0]["link"].startswith("http") and items[0]["when"], items[0]
print("4) rss OK  top='%s' (%s, %s)" % (items[0]["title"][:40], items[0]["source"], items[0]["when"]))

print("\nALL PARSER TESTS PASSED")
