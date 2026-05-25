"""SQLite storage for jobagent. Schema + connection helper."""

from __future__ import annotations

import datetime
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "jobagent" / "jobagent.db"

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

# Millisecond-precision timestamp expression for schema defaults.
NOW_SQL = "strftime('%Y-%m-%d %H:%M:%f','now')"


def now_iso() -> str:
    """Microsecond-precision UTC timestamp string.

    Use this from Python instead of NOW_SQL so successive writes within the
    same millisecond still sort correctly. Format is lexicographically
    comparable with the schema's millisecond default ('YYYY-MM-DD HH:MM:SS.mmm'
    sorts before '...mmm000' so a Python-side write after a schema-default
    insert always orders later).
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )

_STATUS_CHECK = ", ".join(f"'{s}'" for s in sorted(VALID_STATUSES))

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    company         TEXT,
    role            TEXT,
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'interested'
                    CHECK (status IN ({_STATUS_CHECK})),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT ({NOW_SQL}),
    updated_at      TEXT NOT NULL DEFAULT ({NOW_SQL}),
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
    created_at      TEXT NOT NULL DEFAULT ({NOW_SQL})
);

CREATE INDEX IF NOT EXISTS idx_events_application
    ON events(application_id);
"""


def db_path() -> Path:
    raw = os.environ.get("JOBAGENT_DB")
    return Path(raw) if raw else DEFAULT_DB_PATH


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, rollback on error, always close."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        _migrate_status_check_constraint(conn)
        conn.executescript(SCHEMA)


def _migrate_status_check_constraint(conn: sqlite3.Connection) -> None:
    """Retrofit the status CHECK constraint onto pre-existing tables.

    CREATE TABLE IF NOT EXISTS won't add constraints to an existing table,
    and SQLite has no ALTER TABLE ADD CONSTRAINT. Detect a legacy schema
    and rebuild the table in place: create-new, copy, drop-old, rename.
    No-op when the table doesn't yet exist or already has the constraint.
    """
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
    ).fetchone()
    if sql_row is None:
        return
    existing_sql = sql_row["sql"] or ""
    if "CHECK" in existing_sql.upper():
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""
            CREATE TABLE applications_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                url             TEXT NOT NULL UNIQUE,
                company         TEXT,
                role            TEXT,
                title           TEXT,
                status          TEXT NOT NULL DEFAULT 'interested'
                                CHECK (status IN ({_STATUS_CHECK})),
                notes           TEXT,
                created_at      TEXT NOT NULL DEFAULT ({NOW_SQL}),
                updated_at      TEXT NOT NULL DEFAULT ({NOW_SQL}),
                last_touched_at TEXT
            );
            INSERT INTO applications_new
                SELECT id, url, company, role, title, status, notes,
                       created_at, updated_at, last_touched_at
                FROM applications;
            DROP TABLE applications;
            ALTER TABLE applications_new RENAME TO applications;
        """)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
