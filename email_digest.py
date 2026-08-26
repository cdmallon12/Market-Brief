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


def build_email(ctx):
    date_line = ctx.get("meta", {}).get("date_line", "")

    # Key numbers, pulled straight from the built tiles (already sourced).
    rows = []
    for key, i in [("cre_tiles", 0), ("cre_tiles", 2), ("credit_tiles", 0),
                   ("credit_tiles", 1), ("cre_fund_tiles", 0), ("cre_fund_tiles", 2),
                   ("economy_tiles", 0)]:
        lbl, val = _tile(ctx, key, i)
        if lbl and val:
            rows.append((lbl, val))

    def headlines(key, n=4):
        out = []
        for h in ctx.get(key, [])[:n]:
            title = h.get("title", "")
            link = h.get("link", "")
            src = h.get("source", "") or h.get("tag", "")
            if title:
                out.append((title, link, src))
        return out

    cre_h = headlines("cre_headlines")
    mkt_h = headlines("headlines")

    # ---- plain text ----
    lines = [f"The Standpoint Brief — {date_line}", "=" * 44, "", "KEY RATES & SIGNALS"]
    for lbl, val in rows:
        lines.append(f"  {lbl}: {val}")
    if cre_h:
        lines += ["", "CRE HEADLINES"]
        lines += [f"  - {t}" + (f"  {l}" if l else "") for t, l, _ in cre_h]
    if mkt_h:
        lines += ["", "MARKET HEADLINES"]
        lines += [f"  - {t}" for t, _, _ in mkt_h]
    lines += ["", "Informational only — not investment advice.",
              "Full brief: https://cdmallon12.github.io/Market-Brief/"]
    text = "\n".join(lines)

    # ---- html ----
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rate_html = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#556;">{esc(l)}</td>'
        f'<td style="padding:4px 0;font-weight:600;font-family:monospace;">{esc(v)}</td></tr>'
        for l, v in rows
    )

    def hl_html(items):
        out = []
        for t, l, s in items:
            src = f' <span style="color:#889;font-size:12px;">· {esc(s)}</span>' if s else ""
            if l:
                out.append(f'<li style="margin:6px 0;"><a href="{esc(l)}" style="color:#0e4f6e;text-decoration:none;">{esc(t)}</a>{src}</li>')
            else:
                out.append(f'<li style="margin:6px 0;color:#223;">{esc(t)}{src}</li>')
        return "<ul style='padding-left:18px;margin:6px 0;'>" + "".join(out) + "</ul>" if out else ""

    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:600px;color:#223;line-height:1.5;">
  <div style="border-bottom:3px solid #0e4f6e;padding-bottom:8px;margin-bottom:16px;">
    <div style="font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:#a9741f;">Daily Markets &amp; Rates Brief</div>
    <div style="font-size:22px;font-weight:700;">The Standpoint Brief</div>
    <div style="font-size:13px;color:#667;font-family:monospace;">{esc(date_line)}</div>
  </div>
  <h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#445;">Key rates &amp; signals</h3>
  <table style="font-size:14px;border-collapse:collapse;">{rate_html}</table>
  {"<h3 style='font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#445;'>CRE headlines</h3>" + hl_html(cre_h) if cre_h else ""}
  {"<h3 style='font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#445;'>Market headlines</h3>" + hl_html(mkt_h) if mkt_h else ""}
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

    # Gating: manual runs always send; scheduled runs honor DIGEST_HOUR_UTC.
    forced = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or os.environ.get("DIGEST_FORCE")
    hour_gate = os.environ.get("DIGEST_HOUR_UTC", "").strip()
    if not forced and hour_gate:
        try:
            if dt.datetime.utcnow().hour != int(hour_gate):
                print(f"[info] digest: not the scheduled hour ({hour_gate} UTC) — skipping")
                return
        except ValueError:
            pass  # bad gate value -> just send

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
