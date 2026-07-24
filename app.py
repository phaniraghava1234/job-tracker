"""
Job Tracker — Streamlit app.
Deploys to Streamlit Community Cloud. Connects to Neon Postgres.
Secrets configured in Streamlit Cloud dashboard, NOT committed.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from sqlalchemy import text

st.set_page_config(page_title="Job Tracker", layout="wide", page_icon="📊")

# ---------- DB connection ----------
conn = st.connection("neon", type="sql")

def run_write(sql, params):
    with conn.session as s:
        s.execute(text(sql), params)
        s.commit()

# ---------- constants ----------
OUTREACH_SOURCES  = ["Cold outreach", "They reached out", "Alumni", "Referral", "Recruiter", "Other"]
CV_VERSIONS       = ["Cat 1 Design & Performance", "Cat 2 Methods & SciML", "Custom", "Other"]
OUTREACH_STATUSES = ["Active", "Waiting", "Replied", "Bounced", "Wrong person",
                    "Intro'd me", "Ghosted", "Closed – no reply", "Closed – converted"]
REPLY_TYPES       = ["Interested", "Not now", "Rejected", "Referred me", "Auto-reply", "No reply"]

APP_SOURCES  = ["LinkedIn", "Company site", "Referral", "Recruiter", "Apify pull", "Other"]
APP_STATUSES = ["Applied", "Under review", "HR screen", "Tech interview 1",
                "Tech interview 2", "Final round", "Offer", "Rejected",
                "Withdrew", "Not interested anymore", "Ghosted"]
COUNTRIES    = ["France", "Germany", "UK", "Netherlands", "Belgium", "Switzerland", "India", "Other"]

# ---------- diff helpers for st.data_editor ----------
def _norm(v):
    """Normalize a pandas cell value to Python/SQL-friendly form."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    # Convert pandas Timestamps to date for DATE columns
    if isinstance(v, pd.Timestamp):
        return v.date()
    return v

def _changed(old, new):
    o, n = _norm(old), _norm(new)
    if o is None and n is None:
        return False
    return o != n

# ---------- sidebar ----------
page = st.sidebar.radio("Navigation", ["Dashboard", "Outreach", "Applications"])
st.sidebar.markdown("---")
st.sidebar.caption(f"Today: {date.today().isoformat()}")

# ============================================================
# DASHBOARD
# ============================================================
if page == "Dashboard":
    st.title("📊 Dashboard")

    apps = conn.query("SELECT * FROM applications", ttl=60)
    outr = conn.query("SELECT * FROM outreach",     ttl=60)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total applications", len(apps))
    c2.metric("Active pipeline",
              int((~apps["status"].isin(["Rejected", "Withdrew",
                                         "Not interested anymore", "Ghosted"])).sum()) if len(apps) else 0)
    c3.metric("People contacted", len(outr))
    c4.metric("Reply rate",
              f"{outr['reply_received'].mean()*100:.0f}%" if len(outr) else "—")

    st.markdown("---")

    # Follow-ups due this week
    st.subheader("⏰ Follow-ups due within 7 days")
    today = date.today()
    week_out = today + timedelta(days=7)

    due_sql = """
        SELECT id, person_name, company, first_contact_date,
               (first_contact_date + INTERVAL '7 days')::date AS followup_1_due,
               followup_1_sent,
               (followup_1_sent + INTERVAL '7 days')::date  AS followup_2_due,
               followup_2_sent, status
        FROM outreach
        WHERE follow_up_needed = TRUE
          AND status NOT IN ('Closed – no reply', 'Closed – converted', 'Replied',
                             'Bounced', 'Wrong person', 'Ghosted')
          AND (
            (followup_1_sent IS NULL
              AND (first_contact_date + INTERVAL '7 days')::date <= :cutoff)
            OR
            (followup_1_sent IS NOT NULL AND followup_2_sent IS NULL
              AND (followup_1_sent + INTERVAL '7 days')::date <= :cutoff)
          )
        ORDER BY first_contact_date ASC
    """
    due = conn.query(due_sql, params={"cutoff": week_out}, ttl=0)
    if len(due):
        st.dataframe(due, hide_index=True, use_container_width=True)
    else:
        st.success("Nothing due this week. Nice.")

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Application funnel by status")
        if len(apps):
            funnel = apps["status"].value_counts().reindex(APP_STATUSES).fillna(0).reset_index()
            funnel.columns = ["status", "count"]
            fig = px.bar(funnel, x="count", y="status", orientation="h",
                         category_orders={"status": APP_STATUSES[::-1]})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No applications yet.")

    with col2:
        st.subheader("Applications by country")
        if len(apps):
            by_country = apps["country"].fillna("Unknown").value_counts().reset_index()
            by_country.columns = ["country", "count"]
            fig = px.pie(by_country, values="count", names="country")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No applications yet.")

    col3, col4 = st.columns(2)

    with col3:
        st.subheader("Weekly application volume (last 8 weeks)")
        if len(apps):
            apps["date_applied"] = pd.to_datetime(apps["date_applied"])
            weekly = (apps.set_index("date_applied")
                          .resample("W-MON")["id"].count()
                          .tail(8).reset_index())
            weekly.columns = ["week", "applications"]
            fig = px.line(weekly, x="week", y="applications", markers=True)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No applications yet.")

    with col4:
        st.subheader("Reply rate by CV version")
        if len(outr):
            by_cv = outr.groupby("cv_version").agg(
                sent=("id", "count"),
                replied=("reply_received", "sum")
            ).reset_index()
            by_cv["reply_rate_%"] = (by_cv["replied"] / by_cv["sent"] * 100).round(1)
            fig = px.bar(by_cv, x="cv_version", y="reply_rate_%",
                         hover_data=["sent", "replied"])
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No outreach data yet.")

# ============================================================
# OUTREACH
# ============================================================
elif page == "Outreach":
    st.title("👥 Outreach")
    tab_view, tab_add, tab_edit = st.tabs(["View & edit", "Add contact", "Actions (buttons)"])

    # ---------- Inline edit table ----------
    with tab_view:
        outr = conn.query("SELECT * FROM outreach ORDER BY first_contact_date DESC", ttl=30)
        c1, c2, c3 = st.columns(3)
        with c1:
            status_f = st.multiselect("Status", OUTREACH_STATUSES, default=OUTREACH_STATUSES)
        with c2:
            cv_f = st.multiselect("CV version", CV_VERSIONS, default=CV_VERSIONS)
        with c3:
            company_f = st.text_input("Company contains")

        view = outr[outr["status"].isin(status_f) & outr["cv_version"].isin(cv_f)]
        if company_f:
            view = view[view["company"].str.contains(company_f, case=False, na=False)]

        st.caption(f"{len(view)} of {len(outr)} rows — edit any cell then click 'Save changes'")

        if len(view):
            edited = st.data_editor(
                view,
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                disabled=["id", "created_at", "updated_at"],
                key="outr_editor",
                column_config={
                    "status":            st.column_config.SelectboxColumn("Status", options=OUTREACH_STATUSES, required=True),
                    "cv_version":        st.column_config.SelectboxColumn("CV version", options=CV_VERSIONS),
                    "source":            st.column_config.SelectboxColumn("Source", options=OUTREACH_SOURCES),
                    "reply_type":        st.column_config.SelectboxColumn("Reply type", options=REPLY_TYPES),
                    "first_contact_date": st.column_config.DateColumn("First contact"),
                    "reply_date":        st.column_config.DateColumn("Reply date"),
                    "followup_1_sent":   st.column_config.DateColumn("Follow-up 1 sent"),
                    "followup_2_sent":   st.column_config.DateColumn("Follow-up 2 sent"),
                    "reply_received":    st.column_config.CheckboxColumn("Reply received"),
                    "follow_up_needed":  st.column_config.CheckboxColumn("Follow-up needed"),
                    "linkedin_url":      st.column_config.LinkColumn("LinkedIn"),
                    "cv_custom_url":     st.column_config.LinkColumn("Custom CV URL"),
                    "portfolio_url":     st.column_config.LinkColumn("Portfolio"),
                },
            )

            if st.button("💾 Save changes", key="outr_save"):
                editable_cols = [c for c in view.columns if c not in ["id", "created_at", "updated_at"]]
                changes = 0
                for _, new_row in edited.iterrows():
                    old_row = view.loc[view["id"] == new_row["id"]].iloc[0]
                    diffs = {c: _norm(new_row[c]) for c in editable_cols if _changed(old_row[c], new_row[c])}
                    if not diffs:
                        continue
                    set_clauses = ", ".join(f"{c} = :{c}" for c in diffs)
                    params = dict(diffs)
                    params["id"] = int(new_row["id"])
                    run_write(f"UPDATE outreach SET {set_clauses}, updated_at = NOW() WHERE id = :id", params)
                    changes += 1

                if changes:
                    st.success(f"Saved {changes} row(s).")
                    st.rerun()
                else:
                    st.info("No changes to save.")
        else:
            st.info("No rows match the filters.")

    # ---------- Add contact form ----------
    with tab_add:
        with st.form("add_outreach", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                person_name  = st.text_input("Person name *")
                company      = st.text_input("Company")
                role_title   = st.text_input("Role / title")
                location     = st.text_input("Person's location (city, country)")
                email        = st.text_input("Email")
                linkedin_url = st.text_input("LinkedIn URL")
                source       = st.selectbox("Source", OUTREACH_SOURCES)
            with c2:
                first_contact_date = st.date_input("First contact date *", value=date.today())
                cv_version = st.selectbox("CV version used", CV_VERSIONS)
                cv_custom_url = ""
                if cv_version == "Custom":
                    cv_custom_url = st.text_input("Custom CV URL")
                portfolio_url = st.text_input("Portfolio link", value="https://phaniraghava1234.github.io")
                sent_from  = st.text_input("Sent from (your email)", value="phaniraghava1234@gmail.com")
                status     = st.selectbox("Status", OUTREACH_STATUSES)
                notes      = st.text_area("Notes")

            if st.form_submit_button("Add contact"):
                if not person_name:
                    st.error("Person name is required.")
                else:
                    run_write("""
                        INSERT INTO outreach
                            (person_name, company, role_title, location, email, linkedin_url,
                             source, first_contact_date, cv_version, cv_custom_url, portfolio_url,
                             sent_from, status, notes)
                        VALUES
                            (:person_name, :company, :role_title, :location, :email, :linkedin_url,
                             :source, :first_contact_date, :cv_version, :cv_custom_url, :portfolio_url,
                             :sent_from, :status, :notes)
                    """, {"person_name": person_name, "company": company, "role_title": role_title,
                          "location": location, "email": email, "linkedin_url": linkedin_url,
                          "source": source, "first_contact_date": first_contact_date,
                          "cv_version": cv_version, "cv_custom_url": cv_custom_url,
                          "portfolio_url": portfolio_url, "sent_from": sent_from,
                          "status": status, "notes": notes})
                    st.success(f"Added {person_name}.")

    # ---------- Structured action buttons ----------
    with tab_edit:
        outr = conn.query("SELECT id, person_name, company, status FROM outreach ORDER BY id DESC", ttl=0)
        if not len(outr):
            st.info("No contacts yet.")
        else:
            labels = [f"#{r.id} — {r.person_name} @ {r.company or '—'} ({r.status})" for r in outr.itertuples()]
            picked = st.selectbox("Pick contact", options=list(range(len(outr))), format_func=lambda i: labels[i])
            row = outr.iloc[picked]

            c1, c2, c3 = st.columns(3)
            if c1.button("Mark follow-up 1 sent today"):
                run_write("UPDATE outreach SET followup_1_sent = CURRENT_DATE, updated_at = NOW() WHERE id = :id",
                          {"id": int(row["id"])})
                st.success("Follow-up 1 timestamped.")
            if c2.button("Mark follow-up 2 sent today"):
                run_write("UPDATE outreach SET followup_2_sent = CURRENT_DATE, updated_at = NOW() WHERE id = :id",
                          {"id": int(row["id"])})
                st.success("Follow-up 2 timestamped.")
            if c3.button("Toggle follow-up needed"):
                run_write("UPDATE outreach SET follow_up_needed = NOT follow_up_needed, updated_at = NOW() WHERE id = :id",
                          {"id": int(row["id"])})
                st.success("Toggled.")

            st.markdown("**Log a reply**")
            with st.form("log_reply"):
                reply_type = st.selectbox("Reply type", REPLY_TYPES)
                new_status = st.selectbox("New status", OUTREACH_STATUSES, index=2)
                if st.form_submit_button("Log reply"):
                    run_write("""
                        UPDATE outreach
                        SET reply_received = TRUE,
                            reply_date     = CURRENT_DATE,
                            reply_type     = :reply_type,
                            status         = :new_status,
                            updated_at     = NOW()
                        WHERE id = :id
                    """, {"reply_type": reply_type, "new_status": new_status, "id": int(row["id"])})
                    st.success("Reply logged.")

# ============================================================
# APPLICATIONS
# ============================================================
elif page == "Applications":
    st.title("💼 Applications")
    tab_view, tab_add, tab_edit, tab_history = st.tabs(
        ["View & edit", "Add application", "Update status", "Status history"])

    # ---------- Inline edit table ----------
    with tab_view:
        apps = conn.query("SELECT * FROM applications ORDER BY date_applied DESC", ttl=30)
        c1, c2, c3 = st.columns(3)
        with c1:
            status_f = st.multiselect("Status", APP_STATUSES, default=APP_STATUSES)
        with c2:
            country_f = st.multiselect("Country", COUNTRIES, default=COUNTRIES)
        with c3:
            company_f = st.text_input("Company contains")

        view = apps[apps["status"].isin(status_f) & apps["country"].isin(country_f)]
        if company_f:
            view = view[view["company"].str.contains(company_f, case=False, na=False)]

        st.caption(f"{len(view)} of {len(apps)} rows — edit any cell then click 'Save changes'. "
                   "Editing status auto-updates 'last_status_change' and logs it in Status history.")

        if len(view):
            edited = st.data_editor(
                view,
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                disabled=["id", "created_at", "updated_at", "contact_person_id",
                          "last_status_change_reason"],
                key="apps_editor",
                column_config={
                    "status":              st.column_config.SelectboxColumn("Status", options=APP_STATUSES, required=True),
                    "cv_category":         st.column_config.SelectboxColumn("CV category", options=CV_VERSIONS),
                    "country":             st.column_config.SelectboxColumn("Country", options=COUNTRIES),
                    "source":              st.column_config.SelectboxColumn("Source", options=APP_SOURCES),
                    "date_applied":        st.column_config.DateColumn("Date applied"),
                    "last_status_change":  st.column_config.DateColumn("Last status change"),
                    "follow_up_needed":    st.column_config.CheckboxColumn("Follow-up needed"),
                    "cv_file_link":        st.column_config.LinkColumn("CV link"),
                    "cover_letter_link":   st.column_config.LinkColumn("Cover letter"),
                    "job_posting_url":     st.column_config.LinkColumn("Job posting"),
                    "portfolio_url":       st.column_config.LinkColumn("Portfolio"),
                    "cv_custom_url":       st.column_config.LinkColumn("Custom CV URL"),
                },
            )

            if st.button("💾 Save changes", key="apps_save"):
                editable_cols = [c for c in view.columns if c not in
                                 ["id", "created_at", "updated_at", "contact_person_id",
                                  "last_status_change_reason"]]
                changes = 0
                for _, new_row in edited.iterrows():
                    old_row = view.loc[view["id"] == new_row["id"]].iloc[0]
                    diffs = {c: _norm(new_row[c]) for c in editable_cols if _changed(old_row[c], new_row[c])}
                    if not diffs:
                        continue

                    aid = int(new_row["id"])
                    status_changed = "status" in diffs and diffs["status"] != _norm(old_row["status"])
                    date_manually_edited = "last_status_change" in diffs and not status_changed

                    if status_changed:
                        # Log the change and set last_status_change = today
                        run_write("""
                            INSERT INTO status_change_log
                                (application_id, changed_on, old_status, new_status, change_source)
                            VALUES (:aid, CURRENT_DATE, :old, :new, 'inline_edit')
                        """, {"aid": aid, "old": _norm(old_row["status"]), "new": diffs["status"]})
                        diffs["last_status_change"] = date.today()
                        diffs["last_status_change_reason"] = "Status changed (inline)"
                    elif date_manually_edited:
                        diffs["last_status_change_reason"] = "Manual date edit"

                    set_clauses = ", ".join(f"{c} = :{c}" for c in diffs)
                    params = dict(diffs)
                    params["id"] = aid
                    run_write(f"UPDATE applications SET {set_clauses}, updated_at = NOW() WHERE id = :id", params)
                    changes += 1

                if changes:
                    st.success(f"Saved {changes} row(s).")
                    st.rerun()
                else:
                    st.info("No changes to save.")
        else:
            st.info("No rows match the filters.")

    # ---------- Add application form (status now between country and CV category) ----------
    with tab_add:
        contacts = conn.query("SELECT id, person_name, company FROM outreach ORDER BY id DESC", ttl=30)
        with st.form("add_app", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                job_title       = st.text_input("Job title *")
                company         = st.text_input("Company *")
                job_id          = st.text_input("Job ID / req number")
                location        = st.text_input("Location")
                country         = st.selectbox("Country", COUNTRIES)
                status          = st.selectbox("Status", APP_STATUSES, index=0)
                job_posting_url = st.text_input("Job posting URL")
            with c2:
                date_applied      = st.date_input("Date applied *", value=date.today())
                cv_category       = st.selectbox("CV category", CV_VERSIONS)
                cv_custom_url = ""
                if cv_category == "Custom":
                    cv_custom_url = st.text_input("Custom CV URL")
                cv_file_link      = st.text_input("CV file (Google Drive link)")
                cover_letter_link = st.text_input("Cover letter (Google Drive link)")
                portfolio_url     = st.text_input("Portfolio link", value="https://phaniraghava1234.github.io")
                source            = st.selectbox("Source", APP_SOURCES)
                salary_range      = st.text_input("Salary range")

            contact_choices = ["— none —"] + [f"#{r.id} — {r.person_name} @ {r.company or '—'}"
                                              for r in contacts.itertuples()]
            contact_pick = st.selectbox("Linked contact (optional)", contact_choices)
            notes = st.text_area("Notes")

            if st.form_submit_button("Add application"):
                if not (job_title and company):
                    st.error("Job title and company are required.")
                else:
                    contact_person_id = None
                    if contact_pick != "— none —":
                        contact_person_id = int(contact_pick.split("—")[0].strip().lstrip("#"))
                    run_write("""
                        INSERT INTO applications
                            (job_title, company, job_id, location, country, status,
                             cv_category, cv_custom_url, cv_file_link, cover_letter_link,
                             portfolio_url, job_posting_url, date_applied, last_status_change,
                             last_status_change_reason, source, salary_range,
                             contact_person_id, notes)
                        VALUES
                            (:job_title, :company, :job_id, :location, :country, :status,
                             :cv_category, :cv_custom_url, :cv_file_link, :cover_letter_link,
                             :portfolio_url, :job_posting_url, :date_applied, :date_applied,
                             'Initial entry', :source, :salary_range,
                             :contact_person_id, :notes)
                    """, {"job_title": job_title, "company": company, "job_id": job_id,
                          "location": location, "country": country, "status": status,
                          "cv_category": cv_category, "cv_custom_url": cv_custom_url,
                          "cv_file_link": cv_file_link, "cover_letter_link": cover_letter_link,
                          "portfolio_url": portfolio_url, "job_posting_url": job_posting_url,
                          "date_applied": date_applied, "source": source,
                          "salary_range": salary_range,
                          "contact_person_id": contact_person_id, "notes": notes})
                    st.success(f"Added: {job_title} @ {company}")

    # ---------- Structured status update ----------
    with tab_edit:
        apps = conn.query("SELECT id, job_title, company, status FROM applications ORDER BY id DESC", ttl=0)
        if not len(apps):
            st.info("No applications yet.")
        else:
            labels = [f"#{r.id} — {r.job_title} @ {r.company} ({r.status})" for r in apps.itertuples()]
            picked = st.selectbox("Pick application", options=list(range(len(apps))), format_func=lambda i: labels[i])
            row = apps.iloc[picked]

            new_status = st.selectbox("New status", APP_STATUSES,
                                      index=APP_STATUSES.index(row["status"]) if row["status"] in APP_STATUSES else 0)
            c1, c2 = st.columns(2)
            if c1.button("Update status"):
                if new_status != row["status"]:
                    run_write("""
                        INSERT INTO status_change_log
                            (application_id, changed_on, old_status, new_status, change_source)
                        VALUES (:aid, CURRENT_DATE, :old, :new, 'status_update')
                    """, {"aid": int(row["id"]), "old": row["status"], "new": new_status})
                run_write("""
                    UPDATE applications
                    SET status = :s,
                        last_status_change = CURRENT_DATE,
                        last_status_change_reason = 'Status changed (Update tab)',
                        updated_at = NOW()
                    WHERE id = :id
                """, {"s": new_status, "id": int(row["id"])})
                st.success(f"Status → {new_status}")
            if c2.button("Toggle follow-up needed"):
                run_write("UPDATE applications SET follow_up_needed = NOT follow_up_needed, updated_at = NOW() WHERE id = :id",
                          {"id": int(row["id"])})
                st.success("Toggled.")

    # ---------- Status change history audit trail ----------
    with tab_history:
        st.caption("Every status change is logged here — inline edits, Update-tab actions, and initial entries.")
        history = conn.query("""
            SELECT l.id, l.application_id, a.job_title, a.company,
                   l.changed_on, l.old_status, l.new_status, l.change_source, l.logged_at
            FROM status_change_log l
            LEFT JOIN applications a ON a.id = l.application_id
            ORDER BY l.logged_at DESC
            LIMIT 500
        """, ttl=15)
        if len(history):
            st.dataframe(history, hide_index=True, use_container_width=True)
        else:
            st.info("No status changes logged yet.")
