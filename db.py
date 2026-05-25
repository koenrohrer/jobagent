"""SQLite storage for jobagent. Schema + connection helper."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "jobagent" / "jobagent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    company         TEXT,
    role            TEXT,
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'interested',
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_touched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_status
    ON applications(status);

CREATE INDEX IF NOT EXISTS idx_applications_updated
    ON applications(updated_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    payload         TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_application
    ON events(application_id);
"""

VALID_STATUSES = {
    "interested",
    "applied",
    "oa",
    "phone",
    "onsite",
    "offer",
    "rejected",
    "ghosted",
}


def db_path() -> Path:
    raw = os.environ.get("JOBAGENT_DB")
    return Path(raw) if raw else DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
