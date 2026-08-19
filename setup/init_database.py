import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / "database" / "jobs.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL,
    last_fetched_at TEXT,
    last_changed_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    company TEXT,
    title TEXT,
    location TEXT,
    date_posted TEXT,
    link TEXT,
    is_direct BOOLEAN,
    extracted_at TEXT,
    UNIQUE(company, title, link)
);

CREATE TABLE IF NOT EXISTS job_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    profile_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('compatible', 'incompatible', 'needs_direct_link', 'needs_details')),
    reason TEXT NOT NULL,
    description_checked_at TEXT,
    evaluated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'not_started',
    applied_at TEXT,
    resume_used TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);
"""


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    print(f"Database ready at: {DB_PATH}")


if __name__ == "__main__":
    main()
