-- Job Tracker schema. Run once against Neon:
--   psql "postgresql://user:pass@ep-xxx.neon.tech/dbname" -f schema.sql

CREATE TABLE IF NOT EXISTS outreach (
    id                  SERIAL PRIMARY KEY,
    person_name         TEXT NOT NULL,
    company             TEXT,
    role_title          TEXT,
    email               TEXT,
    linkedin_url        TEXT,
    source              TEXT,
    first_contact_date  DATE NOT NULL,
    cv_version          TEXT,
    sent_from           TEXT,
    reply_received      BOOLEAN DEFAULT FALSE,
    reply_date          DATE,
    reply_type          TEXT,
    followup_1_sent     DATE,
    followup_2_sent     DATE,
    status              TEXT DEFAULT 'Active',
    follow_up_needed    BOOLEAN DEFAULT TRUE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outreach_first_contact ON outreach(first_contact_date);
CREATE INDEX IF NOT EXISTS idx_outreach_status         ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_outreach_follow_needed  ON outreach(follow_up_needed);

CREATE TABLE IF NOT EXISTS applications (
    id                  SERIAL PRIMARY KEY,
    job_title           TEXT NOT NULL,
    company             TEXT NOT NULL,
    job_id              TEXT,
    location            TEXT,
    country             TEXT,
    cv_category         TEXT,
    cv_file_link        TEXT,
    cover_letter_link   TEXT,
    job_posting_url     TEXT,
    date_applied        DATE NOT NULL,
    source              TEXT,
    status              TEXT DEFAULT 'Applied',
    last_status_change  DATE DEFAULT CURRENT_DATE,
    salary_range        TEXT,
    contact_person_id   INT REFERENCES outreach(id) ON DELETE SET NULL,
    follow_up_needed    BOOLEAN DEFAULT TRUE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_apps_date_applied  ON applications(date_applied);
CREATE INDEX IF NOT EXISTS idx_apps_status         ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_last_change    ON applications(last_status_change);
