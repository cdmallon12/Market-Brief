#!/usr/bin/env python3
"""
Email digest for The Standpoint Brief.

Reads the freshly built snapshot (data/fallback.json) and emails a concise
morning summary: key rates & spreads, CRE credit signals, and the top CRE and
market headlines. Runs after build.py in the workflow.

It is fully optional and fails safe:
  - No SMTP credentials configured  -> prints a note and exits 0 (no email).
  - Send error                      -> prints the error and exits 0 (never
                                       breaks the page build/commit).

Gating (so you get ONE email per morning, not one every 3-hour build):
  - Manual "Run workflow" (GITHUB_EVENT_NAME=workflow_dispatch) always sends.
  - Otherwise, if DIGEST_HOUR_UTC is set (e.g. "11"), it only sends when the
    current UTC hour matches. Unset -> sends on every run.

Configure via repo secrets (Settings -> Secrets and variables -> Actions):
  DIGEST_TO     recipient(s), comma-separated        (required to send)
  SMTP_USER     SMTP username / sending address       (required to send)
  SMTP_PASS     SMTP password or app password         (required to send)
  DIGEST_FROM   From address        (optional; defaults to SMTP_USER)
  SMTP_HOST     SMTP server         (optional; default smtp.gmail.com)
  SMTP_PORT     SMTP port           (optional; default 465 = SSL, else STARTTLS)
  DIGEST_HOUR_UTC  morning hour gate (optional; the workflow sets "11")

Gmail: create an App Password (myaccount.google.com -> Security -> App
passwords) and use it as SMTP_PASS with SMTP_USER = your Gmail address.
"""

from __future__ import annotations
import datetime as dt
import json
import os
import pathlib
import smtplib
import ssl
import sys
from email.message import EmailMessage

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data" / "fallback.json"


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _tile(ctx, key, i):
    try:
        t = ctx[key][i]
        v = t.get("val", "")
        u = t.get("unit", "")
        return t.get("lbl", ""), f"{v}{u}"
    except Exception:
        return None, None


def _tval(ctx, key, i):
    """Raw 'val'+'unit' string for a tile, or '' if missing."""
    try:
        t = ctx[key][i]
        return f"{t.get('val','')}{t.get('unit','')}"
    except Exception:
        return ""


def build_summary(ctx):
    """A short, factual 'what to watch' blurb spanning the broad market and CRE."""
    try:
        spx = ctx["markets_tiles"][0]
        d = spx.get("delta", {})
        move = "up" if d.get("dir", 0) > 0 else ("down" if d.get("dir", 0) < 0 else "flat")
        spx_val, spx_chg = spx.get("val", ""), d.get("txt", "")
        vix = ctx["markets_tiles"][3].get("val", "")
        y10 = _tval(ctx, "cre_tiles", 0)
        mort = _tval(ctx, "cre_fin_tiles", 0)
        ig, hy = _tval(ctx, "credit_tiles", 0), _tval(ctx, "credit_tiles", 1)
        delq = _tval(ctx, "cre_fund_tiles", 0)
        sloos = ctx["cre_fund_tiles"][2].get("val", "")
        try:
            stance = "easing" if float(sloos) < 0 else ("tightening" if float(sloos) > 0 else "holding")
        except Exception:
            stance = "adjusting"
        # CMBS distress signal (overall + office) — a headline CRE-stress read.
        cmbs = ""
        try:
            crows = (ctx.get("cmbs_distress", {}) or {}).get("rows", [])
            overall = crows[0].get("val", "") if crows else ""
            office = next((r.get("val", "") for r in crows
                           if r.get("seg", "").lower().startswith("office")), "")
            if overall:
                cmbs = f"CMBS delinquency is {overall}" + (f", with office at {office}" if office else "") + "."
        except Exception:
            cmbs = ""
        cats = (ctx.get("markets", {}) or {}).get("catalysts", [])
        watch = f"{cats[0]['title']} ({cats[0]['when']})" if cats else ""

        s1 = (f"Broad market: the S&P 500 is {move} at {spx_val} "
              f"({spx_chg} vs. the prior close), the 10-year Treasury is {y10}, "
              f"and the VIX is {vix}.")
        s2 = (f"CRE: new debt is pricing near a {mort} 30-year mortgage, credit spreads are "
              f"{ig} investment-grade / {hy} high-yield, bank CRE delinquency is {delq}, "
              f"and banks are {stance} lending standards.")
        s3 = f"Distress: {cmbs}" if cmbs else ""
        s4 = f"Watch today: {watch}." if watch else ""
        return " ".join(x for x in (s1, s2, s3, s4) if x)
    except Exception:
        return ""


def build_email(ctx):
    date_line = ctx.get("meta", {}).get("date_line", "")
    quote_note = (ctx.get("markets", {}) or {}).get("quote_note", "")
    summary = build_summary(ctx)

    # Equity index snapshot (open / current vs prior close) from the market tiles.
    idx = []
    for i in range(4):
        try:
            t = ctx["markets_tiles"][i]
            d = t.get("delta", {})
            arrow = "▲" if d.get("dir", 0) > 0 else ("▼" if d.get("dir", 0) < 0 else "•")
            idx.append((t.get("lbl", ""), t.get("val", ""), arrow, d.get("txt", ""), d.get("dir", 0)))
        except Exception:
            pass

    # Key rates & signals — grouped: base rates, cost of new debt, credit
    # spreads, CRE fundamentals, policy, energy.
    rows = []
    for key, i in [("cre_tiles", 0), ("cre_tiles", 2), ("cre_fin_tiles", 0),
                   ("credit_tiles", 0), ("credit_tiles", 1), ("cre_fund_tiles", 0),
                   ("cre_fund_tiles", 2), ("economy_tiles", 0), ("energy_tiles", 0)]:
        lbl, val = _tile(ctx, key, i)
        if lbl and val:
            rows.append((lbl, val))

    # Catalysts (calendar-driven).
    cats = []
    for c in (ctx.get("markets", {}) or {}).get("catalysts", [])[:3]:
        cats.append((c.get("when", ""), c.get("title", ""), c.get("body", "")))

    def headlines(key, n=4):
        out = []
        for h in ctx.get(key, [])[:n]:
            title, link = h.get("title", ""), h.get("link", "")
            src = h.get("source", "") or h.get("tag", "")
            if title:
                out.append((title, link, src))
        return out

    cre_h, mkt_h = headlines("cre_headlines"), headlines("headlines")

    import re
    def strip_tags(s):
        return re.sub("<[^>]+>", "", s or "")

    # ---- plain text ----
    lines = [f"The Standpoint Brief — {date_line}", "=" * 44]
    if summary:
        lines += ["", strip_tags(summary)]
    if idx:
        lines += ["", "MARKETS AT THE OPEN"]
        lines += [f"  {l}: {v}  {a} {c} vs prior close" for l, v, a, c, _ in idx]
    if quote_note:
        lines.append(f"  ({quote_note})")
    lines += ["", "KEY RATES & SIGNALS"]
    lines += [f"  {l}: {v}" for l, v in rows]
    if cats:
        lines += ["", "ON THE CALENDAR"]
        lines += [f"  {w} — {t}: {strip_tags(b)}" for w, t, b in cats]
    if cre_h:
        lines += ["", "CRE HEADLINES"] + [f"  - {t}" + (f"  {l}" if l else "") for t, l, _ in cre_h]
    if mkt_h:
        lines += ["", "MARKET HEADLINES"] + [f"  - {t}" for t, _, _ in mkt_h]
    lines += ["", "Informational only — not investment advice.",
              "Full brief: https://cdmallon12.github.io/Market-Brief/"]
    text = "\n".join(lines)

    # ---- html ----
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def h3(t):
        return f"<h3 style='font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#445;margin:20px 0 6px;'>{t}</h3>"

    idx_html = ""
    if idx:
        cells = []
        for l, v, a, c, dr in idx:
            col = "#0a7d34" if dr > 0 else ("#bb392f" if dr < 0 else "#556")
            cells.append(
                f'<td style="padding:8px 14px 8px 0;">'
                f'<div style="font-size:11px;color:#889;text-transform:uppercase;letter-spacing:.04em;">{esc(l)}</div>'
                f'<div style="font-size:17px;font-weight:700;font-family:monospace;">{esc(v)}</div>'
                f'<div style="font-size:12px;font-weight:600;color:{col};font-family:monospace;">{a} {esc(c)}</div>'
                f'</td>')
        idx_html = (h3("Markets at the open")
                    + f'<table style="border-collapse:collapse;"><tr>{"".join(cells)}</tr></table>'
                    + (f'<div style="font-size:11px;color:#889;margin-top:4px;">{esc(quote_note)}</div>' if quote_note else ""))

    rate_html = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#556;">{esc(l)}</td>'
        f'<td style="padding:4px 0;font-weight:600;font-family:monospace;">{esc(v)}</td></tr>'
        for l, v in rows)

    cat_html = ""
    if cats:
        items = "".join(
            f'<div style="margin:8px 0;"><span style="display:inline-block;font-family:monospace;font-size:11px;color:#a9741f;border:1px solid #e3d6bd;border-radius:10px;padding:1px 8px;margin-right:8px;">{esc(w)}</span>'
            f'<strong>{esc(t)}</strong><div style="font-size:13px;color:#556;margin-top:2px;">{esc(strip_tags(b))}</div></div>'
            for w, t, b in cats)
        cat_html = h3("On the calendar") + items

    def hl_html(items):
        out = []
        for t, l, s in items:
            src = f' <span style="color:#889;font-size:12px;">· {esc(s)}</span>' if s else ""
            if l:
                out.append(f'<li style="margin:6px 0;"><a href="{esc(l)}" style="color:#0e4f6e;text-decoration:none;">{esc(t)}</a>{src}</li>')
            else:
                out.append(f'<li style="margin:6px 0;color:#223;">{esc(t)}{src}</li>')
        return "<ul style='padding-left:18px;margin:6px 0;'>" + "".join(out) + "</ul>" if out else ""

    summary_html = (f'<p style="font-size:14px;color:#223;margin:0 0 4px;background:#f2f5f8;border-left:3px solid #0e4f6e;padding:11px 14px;border-radius:0 6px 6px 0;">{summary}</p>'
                    if summary else "")

    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:600px;color:#223;line-height:1.5;">
  <div style="border-bottom:3px solid #0e4f6e;padding-bottom:8px;margin-bottom:14px;">
    <div style="font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:#a9741f;">Daily Markets &amp; Rates Brief</div>
    <div style="font-size:22px;font-weight:700;">The Standpoint Brief</div>
    <div style="font-size:13px;color:#667;font-family:monospace;">{esc(date_line)}</div>
  </div>
  {summary_html}
  {idx_html}
  {h3("Key rates &amp; signals")}
  <table style="font-size:14px;border-collapse:collapse;">{rate_html}</table>
  {cat_html}
  {h3("CRE headlines") + hl_html(cre_h) if cre_h else ""}
  {h3("Market headlines") + hl_html(mkt_h) if mkt_h else ""}
  <p style="margin-top:20px;">
    <a href="https://cdmallon12.github.io/Market-Brief/" style="background:#0e4f6e;color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;font-size:14px;">Open the full brief →</a>
  </p>
  <p style="font-size:11px;color:#889;margin-top:18px;border-top:1px solid #e1e0d9;padding-top:10px;">
    Informational only — not investment advice. Figures are point-in-time snapshots from public sources.
  </p>
</div>"""
    return text, html


def main():
    to = os.environ.get("DIGEST_TO", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    pw = os.environ.get("SMTP_PASS", "").strip()
    if not (to and user and pw):
        print("[info] digest: SMTP not configured (need DIGEST_TO, SMTP_USER, SMTP_PASS) — skipping")
        return

    # Gating (robust to GitHub's scheduled-run delays): a manual run always sends;
    # a scheduled run sends only when the cron that TRIGGERED it is the designated
    # email cron. GitHub sets github.event.schedule to that cron string regardless
    # of how late the run actually fires, so a delayed build still emails exactly
    # once — unlike checking the wall-clock hour, which drops the email if the run
    # slips into the next hour.
    event = os.environ.get("GITHUB_EVENT_NAME", "").strip()
    forced = event == "workflow_dispatch" or os.environ.get("DIGEST_FORCE")
    email_cron = os.environ.get("DIGEST_CRON", "50 13 * * 1-5").strip()
    trigger = os.environ.get("SCHEDULE_TRIGGER", "").strip()
    if not forced:
        if event == "schedule":
            if trigger != email_cron:
                print(f"[info] digest: build cron ({trigger or 'n/a'}) is not the email cron ({email_cron}) — skipping")
                return
        elif event:
            print(f"[info] digest: event '{event}' is neither a manual run nor the email schedule — skipping")
            return
        # empty event (e.g. local run) with creds set: fall through and send

    ctx = load()
    text, html = build_email(ctx)

    msg = EmailMessage()
    msg["Subject"] = f"Standpoint Brief — {ctx.get('meta', {}).get('date_line', 'daily')}"
    msg["From"] = os.environ.get("DIGEST_FROM", "").strip() or user
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT", "465").strip() or "465")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
                s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pw)
                s.send_message(msg)
        print(f"[ok] digest emailed to {to}")
    except Exception as e:
        print(f"[warn] digest send failed (non-fatal): {e}")
        # exit 0 anyway so the page build/commit is never blocked
        return


if __name__ == "__main__":
    sys.exit(main())
