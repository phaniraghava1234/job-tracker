"""
Daily digest — runs on GitHub Actions.
Reads Neon, sends HTML email via Gmail SMTP.
Environment: NEON_URL, GMAIL_FROM, GMAIL_APP_PASSWORD, GMAIL_TO
"""
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from sqlalchemy import create_engine, text

NEON_URL   = os.environ["NEON_URL"]
GMAIL_FROM = os.environ["GMAIL_FROM"]
GMAIL_PW   = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
GMAIL_TO   = os.environ["GMAIL_TO"]

# Two stale tiers for job applications
STALE_WARNING_DAYS  = 15   # yellow "chase this" band
STALE_CRITICAL_DAYS = 30   # red "you're losing this" band

# Statuses that mean the outreach is closed / dead — never nag about these
OUTREACH_TERMINAL = (
    "'Closed – no reply', 'Closed – converted', 'Replied', "
    "'Bounced', 'Wrong person', 'Ghosted'"
)

# Statuses that mean an application is still live in the pipeline
APP_ACTIVE = (
    "'Applied', 'Under review', 'HR screen', "
    "'Tech interview 1', 'Tech interview 2', 'Final round'"
)

engine = create_engine(NEON_URL)

SQL_OUTREACH_DUE = f"""
    SELECT id, person_name, company, role_title, first_contact_date,
           followup_1_sent, status, email
    FROM outreach
    WHERE follow_up_needed = TRUE
      AND status NOT IN ({OUTREACH_TERMINAL})
      AND (
        (followup_1_sent IS NULL
          AND (first_contact_date + INTERVAL '7 days')::date = CURRENT_DATE)
        OR
        (followup_1_sent IS NOT NULL AND followup_2_sent IS NULL
          AND (followup_1_sent + INTERVAL '7 days')::date = CURRENT_DATE)
      )
    ORDER BY first_contact_date
"""

SQL_OUTREACH_OVERDUE = f"""
    SELECT id, person_name, company, first_contact_date,
           (CURRENT_DATE - first_contact_date) AS days_since,
           status
    FROM outreach
    WHERE follow_up_needed = TRUE
      AND status NOT IN ({OUTREACH_TERMINAL})
      AND followup_1_sent IS NULL
      AND (CURRENT_DATE - first_contact_date) > 7
    ORDER BY first_contact_date
"""

SQL_APPS_STALE_WARNING = f"""
    SELECT id, job_title, company, status, last_status_change,
           (CURRENT_DATE - last_status_change) AS days_stale
    FROM applications
    WHERE follow_up_needed = TRUE
      AND status IN ({APP_ACTIVE})
      AND (CURRENT_DATE - last_status_change) BETWEEN :warn AND :crit - 1
    ORDER BY last_status_change
"""

SQL_APPS_STALE_CRITICAL = f"""
    SELECT id, job_title, company, status, last_status_change,
           (CURRENT_DATE - last_status_change) AS days_stale
    FROM applications
    WHERE follow_up_needed = TRUE
      AND status IN ({APP_ACTIVE})
      AND (CURRENT_DATE - last_status_change) >= :crit
    ORDER BY last_status_change
"""

SQL_WEEKLY_SUMMARY = """
    SELECT
      (SELECT COUNT(*) FROM applications
         WHERE date_applied >= CURRENT_DATE - INTERVAL '7 days')          AS apps_this_week,
      (SELECT COUNT(*) FROM outreach
         WHERE first_contact_date >= CURRENT_DATE - INTERVAL '7 days')    AS contacts_this_week,
      (SELECT COUNT(*) FROM outreach
         WHERE reply_received = TRUE
           AND reply_date >= CURRENT_DATE - INTERVAL '7 days')            AS replies_this_week,
      (SELECT COUNT(*) FROM applications
         WHERE status NOT IN ('Rejected', 'Withdrew',
                              'Not interested anymore', 'Ghosted', 'Offer')) AS active_pipeline
"""


def fetch_rows(sql, params=None):
    with engine.connect() as c:
        return list(c.execute(text(sql), params or {}).mappings())


def fetch_one(sql):
    with engine.connect() as c:
        return dict(c.execute(text(sql)).mappings().first())


def render_table(rows, columns):
    if not rows:
        return "<p style='color:#888'>— none —</p>"
    head = "".join(f"<th style='padding:6px 10px;text-align:left;border-bottom:2px solid #333'>{c}</th>"
                   for c in columns)
    body = ""
    for r in rows:
        cells = "".join(
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>"
            f"{r.get(c, '') if r.get(c) is not None else ''}</td>"
            for c in columns
        )
        body += f"<tr>{cells}</tr>"
    return (f"<table style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def build_html():
    due       = fetch_rows(SQL_OUTREACH_DUE)
    overdue_o = fetch_rows(SQL_OUTREACH_OVERDUE)
    warn_a    = fetch_rows(SQL_APPS_STALE_WARNING,
                           {"warn": STALE_WARNING_DAYS, "crit": STALE_CRITICAL_DAYS})
    crit_a    = fetch_rows(SQL_APPS_STALE_CRITICAL,
                           {"crit": STALE_CRITICAL_DAYS})
    summary   = fetch_one(SQL_WEEKLY_SUMMARY)

    today_str = date.today().strftime("%A, %d %B %Y")

    html = f"""
    <html><body style="font-family:sans-serif;max-width:800px;margin:auto;color:#222">
      <h2 style="border-bottom:3px solid #333;padding-bottom:6px">Job hunt digest — {today_str}</h2>

      <h3 style="color:#1a73e8">⏰ Follow-ups due today ({len(due)})</h3>
      {render_table(due, ['id','person_name','company','role_title','first_contact_date','followup_1_sent','status'])}

      <h3 style="color:#d93025">🚨 Outreach overdue — you missed a follow-up ({len(overdue_o)})</h3>
      {render_table(overdue_o, ['id','person_name','company','first_contact_date','days_since','status'])}

      <h3 style="color:#f9ab00">⚠️ Applications stale {STALE_WARNING_DAYS}–{STALE_CRITICAL_DAYS - 1} days ({len(warn_a)})</h3>
      {render_table(warn_a, ['id','job_title','company','status','last_status_change','days_stale'])}

      <h3 style="color:#d93025">🚨 Applications critically stale ≥{STALE_CRITICAL_DAYS} days ({len(crit_a)})</h3>
      {render_table(crit_a, ['id','job_title','company','status','last_status_change','days_stale'])}

      <h3 style="color:#137333">📊 This week</h3>
      <ul style="line-height:1.7">
        <li>Applications sent: <b>{summary['apps_this_week']}</b></li>
        <li>People contacted: <b>{summary['contacts_this_week']}</b></li>
        <li>Replies received: <b>{summary['replies_this_week']}</b></li>
        <li>Active pipeline: <b>{summary['active_pipeline']}</b></li>
      </ul>

      <p style="color:#888;font-size:12px;margin-top:30px">
        To silence a row: open the Streamlit app → toggle "Follow-up needed" off, or set status
        to a terminal value (Rejected, Ghosted, Withdrew, Bounced, Wrong person, Closed).<br>
        To mark a follow-up as sent: same app → Actions tab → "Mark follow-up sent today".
      </p>
    </body></html>
    """
    return html


def send_email(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Job hunt digest — {date.today().isoformat()}"
    msg["From"]    = GMAIL_FROM
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_FROM, GMAIL_PW)
        s.send_message(msg)


if __name__ == "__main__":
    send_email(build_html())
    print("Digest sent.")
