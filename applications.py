"""Application-record logic. The MCP tool layer (server.py) calls into this."""

from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from db import VALID_STATUSES, connect

USER_AGENT = "jobagent/0.1 (+https://github.com/koenrohrer/jobagent)"
FETCH_TIMEOUT = 10.0
LIST_LIMIT_CAP = 500


def add(url: str) -> dict:
    """Insert an application by URL. Dedupes; returns the row as a dict."""
    _validate_url(url)

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM applications WHERE url = ?", (url,)
        ).fetchone()
        if existing:
            return _row_to_dict(existing, deduped=True)

    title = _fetch_title(url)
    company = _guess_company(url)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (url, company, title) VALUES (?, ?, ?)",
            (url, company, title),
        )
        app_id = cur.lastrowid
        conn.execute(
            "INSERT INTO events (application_id, event_type, payload) "
            "VALUES (?, ?, ?)",
            (app_id, "added", url),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()

    return _row_to_dict(row, deduped=False)


def list_(status: str | None = None, limit: int = 50) -> list[dict]:
    """List applications most recently updated first, optionally filtered by status."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    capped = max(1, min(limit, LIST_LIMIT_CAP))

    sql = "SELECT * FROM applications"
    params: tuple = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params = params + (capped,)

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r, deduped=False) for r in rows]


def update_status(application_id: int, status: str, note: str | None = None) -> dict:
    """Change status, optionally appending a note. Records an event."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"No application with id {application_id}")

        if note:
            prior = existing["notes"]
            new_notes = f"{prior}\n{note}" if prior else note
        else:
            new_notes = existing["notes"]

        conn.execute(
            "UPDATE applications "
            "SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP, "
            "    last_touched_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (status, new_notes, application_id),
        )
        conn.execute(
            "INSERT INTO events (application_id, event_type, payload) "
            "VALUES (?, ?, ?)",
            (application_id, "status_changed", f"{existing['status']}->{status}"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()

    return _row_to_dict(row, deduped=False)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Not a valid http(s) URL: {url!r}")


def _fetch_title(url: str) -> str | None:
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return None


def _guess_company(url: str) -> str | None:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return None


def _row_to_dict(row: sqlite3.Row, deduped: bool) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "company": row["company"],
        "role": row["role"],
        "title": row["title"],
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deduped": deduped,
    }
