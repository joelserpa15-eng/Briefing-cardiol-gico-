#!/usr/bin/env python3
"""
Briefing Cardiológico Semanal — Email Notifier
===============================================
Generates an HTML email with the week's cardiology highlights and sends it
via Gmail SMTP or any SMTP server.

Environment variables (set as GitHub Actions secrets):
    MAIL_TO       Recipient address  (e.g. joelserpa15@gmail.com)
    MAIL_FROM     Sender address     (e.g. briefing.cardiologico@gmail.com)
    MAIL_PASSWORD Gmail App Password (Settings → Security → App passwords)
    PAGES_URL     GitHub Pages URL   (e.g. https://joelserpa15-eng.github.io/Briefing-cardiol-gico-/)
"""

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
MAIL_TO       = os.environ.get("MAIL_TO",       "joelserpa15@gmail.com")
MAIL_FROM     = os.environ.get("MAIL_FROM",     "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
PAGES_URL     = os.environ.get("PAGES_URL",     "https://joelserpa15-eng.github.io/Briefing-cardiol-gico-/")
SMTP_HOST     = os.environ.get("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))

# ── Evidence badge colors ───────────────────────────────────────────────────
EV_COLORS = {
    1: {"bg": "#fef9c3", "border": "#fbbf24", "text": "#92400e"},   # meta-analysis
    2: {"bg": "#dbeafe", "border": "#60a5fa", "text": "#1e3a8a"},   # systematic review
    3: {"bg": "#dcfce7", "border": "#4ade80", "text": "#14532d"},   # RCT
    4: {"bg": "#fff7ed", "border": "#fb923c", "text": "#7c2d12"},   # cohort
    5: {"bg": "#f3f4f6", "border": "#9ca3af", "text": "#374151"},   # case-control
}

def load_articles():
    data_path = Path(__file__).parent.parent / "data" / "articles.json"
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)

def ev_badge(rank, label):
    c = EV_COLORS.get(rank, EV_COLORS[5])
    return (
        f'<span style="display:inline-block;padding:2px 9px;border-radius:10px;'
        f'font-size:11px;font-weight:700;'
        f'background:{c["bg"]};border:1px solid {c["border"]};color:{c["text"]}">'
        f'{label}</span>'
    )

def subspecialty_section(sub):
    color  = sub["color"]
    name   = sub["name"]
    arts   = sub["articles"]
    n      = len(arts)

    rows = ""
    for art in arts:
        badge  = ev_badge(art["evidenceRank"], art["evidenceLevel"])
        rows  += f"""
        <tr>
          <td style="padding:14px 20px;border-bottom:1px solid #f1f5f9">
            <div style="margin-bottom:6px">{badge}
              <span style="font-size:11px;color:#718096;margin-left:8px;font-style:italic">
                {art["journal"]} · {art["year"]}
              </span>
            </div>
            <div style="font-size:14px;font-weight:700;color:#1a202c;margin-bottom:4px;line-height:1.4">
              {art["title"]}
            </div>
            <div style="font-size:12px;color:#718096;margin-bottom:8px">
              {art["authors"]}
            </div>
            <div style="font-size:13px;color:#2d3748;background:#f7fafc;
                        border-left:3px solid {color};padding:8px 12px;border-radius:0 6px 6px 0;
                        margin-bottom:10px;line-height:1.5">
              <strong style="font-size:11px;color:{color};text-transform:uppercase;
                             letter-spacing:.4px;display:block;margin-bottom:3px">
                Conclusión clave
              </strong>
              {art["keyFindings"]}
            </div>
            <a href="{art["url"]}" style="display:inline-block;padding:6px 14px;
               background:{color};color:#fff;text-decoration:none;border-radius:6px;
               font-size:12px;font-weight:700">
              Leer artículo completo →
            </a>
          </td>
        </tr>"""

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
           style="margin-bottom:24px;border:1px solid #e2e6ea;border-radius:12px;
                  overflow:hidden;border-collapse:separate;border-spacing:0">
      <tr>
        <td style="background:{color};padding:14px 20px">
          <span style="font-size:16px;font-weight:800;color:#fff">{name}</span>
          <span style="font-size:12px;color:rgba(255,255,255,.75);margin-left:10px">
            {n} artículo{'s' if n != 1 else ''}
          </span>
        </td>
      </tr>
      {rows}
    </table>"""

def build_html(data):
    week_label   = data.get("weekLabel", "")
    last_updated = data.get("lastUpdated", "")
    subs         = data.get("subspecialties", [])
    stats        = data.get("stats", {})

    total  = stats.get("total", sum(len(s["articles"]) for s in subs))
    n_meta = stats.get("metaAnalysis", 0)
    n_rct  = stats.get("rct", 0)
    n_subs = len(subs)

    if last_updated:
        dt  = datetime.strptime(last_updated[:19], "%Y-%m-%dT%H:%M:%S")
        upd = dt.strftime("%d/%m/%Y %H:%M UTC")
    else:
        upd = "—"

    sections = "".join(subspecialty_section(s) for s in subs)

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8f9fb;font-family:'Segoe UI',system-ui,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;padding:24px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1a202c,#2d3748);border-radius:16px 16px 0 0;padding:32px 32px 24px">
    <div style="display:inline-flex;align-items:center;gap:12px;margin-bottom:16px">
      <div style="width:44px;height:44px;background:linear-gradient(135deg,#c0392b,#e74c3c);
                  border-radius:12px;display:inline-flex;align-items:center;
                  justify-content:center;font-size:22px;vertical-align:middle">❤️</div>
      <span style="font-size:20px;font-weight:800;color:#fff;vertical-align:middle;margin-left:8px">
        Briefing Cardiológico Semanal
      </span>
    </div>
    <div style="font-size:14px;color:#a0aec0;margin-bottom:20px">
      Semana del {week_label} · Actualizado: {upd}
    </div>
    <!-- Stats -->
    <table cellpadding="0" cellspacing="0">
      <tr>
        {''.join(f"""
        <td style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);
                   border-radius:10px;padding:12px 20px;text-align:center;min-width:100px;
                   margin-right:12px">
          <div style="font-size:26px;font-weight:800;color:#fff;line-height:1">{val}</div>
          <div style="font-size:11px;color:#a0aec0;text-transform:uppercase;
                      letter-spacing:.5px;margin-top:4px">{lbl}</div>
        </td>
        <td width="12"></td>""" for val, lbl in [
            (total, "Artículos"),
            (n_subs, "Subespecialidades"),
            (n_meta, "Meta-análisis"),
            (n_rct, "Ensayos clínicos"),
        ])}
      </tr>
    </table>
  </td></tr>

  <!-- CTA -->
  <tr><td style="background:#fff;padding:24px 32px;border-bottom:2px solid #e2e6ea;text-align:center">
    <p style="margin:0 0 16px;font-size:15px;color:#4a5568">
      Accede a la plataforma completa con todos los resúmenes, abstract expandible y filtros por nivel de evidencia:
    </p>
    <a href="{PAGES_URL}"
       style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#c0392b,#e74c3c);
              color:#fff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:700;
              letter-spacing:-.2px">
      Ver Briefing Completo →
    </a>
  </td></tr>

  <!-- Articles -->
  <tr><td style="background:#f8f9fb;padding:24px 24px 0">
    {sections}
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#1a202c;border-radius:0 0 16px 16px;padding:20px 32px;text-align:center">
    <p style="margin:0 0 6px;color:#a0aec0;font-size:12px;font-weight:700">
      Briefing Cardiológico Semanal
    </p>
    <p style="margin:0;color:#4a5568;font-size:11px;line-height:1.7">
      Artículos recuperados de PubMed (NCBI) y Europe PMC.<br>
      Ordenados por nivel de evidencia y factor de impacto de la revista.<br>
      Los resúmenes son orientativos — consulta siempre el artículo original.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

def send(html, week_label):
    if not MAIL_FROM or not MAIL_PASSWORD:
        print("  [WARN] MAIL_FROM or MAIL_PASSWORD not set — skipping email.", file=sys.stderr)
        # Save HTML for debugging
        out = Path(__file__).parent.parent / "data" / "email_preview.html"
        out.write_text(html, encoding="utf-8")
        print(f"  Email HTML saved to {out}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Briefing Cardiológico — Semana del {week_label}"
    msg["From"]    = f"Briefing Cardiológico <{MAIL_FROM}>"
    msg["To"]      = MAIL_TO
    msg.attach(MIMEText(html, "html", "utf-8"))

    print(f"  Sending email to {MAIL_TO} via {SMTP_HOST}:{SMTP_PORT}…")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(MAIL_FROM, MAIL_PASSWORD)
        server.sendmail(MAIL_FROM, MAIL_TO, msg.as_string())
    print("  Email sent successfully.")

def main():
    print("=== Briefing Cardiológico — Email Sender ===")
    data = load_articles()
    week_label = data.get("weekLabel", "")
    html = build_html(data)
    send(html, week_label)
    print("=== Done ===")

if __name__ == "__main__":
    main()
