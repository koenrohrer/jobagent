"""Application-record logic. The MCP tool layer (server.py) calls into this."""

from __future__ import annotations

import ipaddress
import logging
import socket
import sqlite3
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from db import VALID_STATUSES, connect, now_iso

log = logging.getLogger(__name__)

USER_AGENT = "jobagent/0.1 (+https://github.com/koenrohrer/jobagent)"
FETCH_TIMEOUT = 10.0
LIST_LIMIT_CAP = 500

_ATS_PATH_COMPANY = {
    "jobs.lever.co",
    "boards.greenhouse.io",
    "boards-api.greenhouse.io",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
    "careers.smartrecruiters.com",
}

_TWO_LABEL_PUBLIC_SUFFIXES = {
    "co.uk", "ac.uk", "org.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.nz",
    "co.jp", "ne.jp", "or.jp",
    "co.in", "co.kr", "com.br", "com.mx", "com.sg",
}


def add(url: str) -> dict:
    """Insert an application by URL. Dedupes; returns the row + a deduped flag.

    Fast-path: if the URL is already tracked, return that row without a
    network fetch. Otherwise fetch the title, then INSERT … ON CONFLICT
    DO NOTHING so a concurrent insert by another caller still yields a
    deduped result instead of an IntegrityError.
    """
    _validate_url(url)

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM applications WHERE url = ?", (url,)
        ).fetchone()
    if existing is not None:
        return {**_row_to_dict(existing), "deduped": True}

    title = _fetch_title(url)
    company = _guess_company(url)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (url, company, title) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO NOTHING",
            (url, company, title),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT * FROM applications WHERE url = ?", (url,)
            ).fetchone()
            return {**_row_to_dict(row), "deduped": True}

        app_id = cur.lastrowid
        conn.execute(
            "INSERT INTO events (application_id, event_type, payload) "
            "VALUES (?, ?, ?)",
            (app_id, "added", url),
        )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()

    return {**_row_to_dict(row), "deduped": False}


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
    return [_row_to_dict(r) for r in rows]


def update_status(application_id: int, status: str, note: str | None = None) -> dict:
    """Change status, optionally appending a note. Records an event.

    No-op (no event, no timestamp bump) if status is already `status` and no
    note is given.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")

    with connect() as conn:
        existing = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"No application with id {application_id}")

        status_changed = existing["status"] != status

        if not status_changed and not note:
            return _row_to_dict(existing)

        if note:
            prior = existing["notes"]
            new_notes = f"{prior}\n{note}" if prior else note
        else:
            new_notes = existing["notes"]

        ts = now_iso()
        conn.execute(
            "UPDATE applications "
            "SET status = ?, notes = ?, updated_at = ?, last_touched_at = ? "
            "WHERE id = ?",
            (status, new_notes, ts, ts, application_id),
        )
        if status_changed:
            conn.execute(
                "INSERT INTO events (application_id, event_type, payload) "
                "VALUES (?, ?, ?)",
                (application_id, "status_changed", f"{existing['status']}->{status}"),
            )
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()

    return _row_to_dict(row)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Not a valid http(s) URL: {url!r}")


class _UnsafeURL(ValueError):
    """Raised when a request target resolves to a non-public IP."""


def _is_unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _pick_safe_ip(infos: list) -> str:
    """Return the first publicly-routable IP from a getaddrinfo result.

    Raises _UnsafeURL if no entry is safe.
    """
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
        if not _is_unsafe_ip(ip):
            return str(ip)
    raise _UnsafeURL("No publicly-routable IP for hostname")


class _PinnedResolverTransport(httpx.HTTPTransport):
    """httpx transport that resolves each request's hostname exactly once,
    validates the IP, then connects to the literal IP with Host: header
    and SNI preserved. Closes the DNS-rebinding TOCTOU window between
    validation and connect, and keeps redirects safe because httpx calls
    handle_request for every hop.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        hostname = request.url.host
        if not hostname:
            raise _UnsafeURL("Request URL missing hostname")
        # If the URL already targets a literal IP, validate it directly.
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if _is_unsafe_ip(literal):
                raise _UnsafeURL(f"Disallowed IP literal: {literal}")
            return super().handle_request(request)

        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror as e:
            raise _UnsafeURL(f"Cannot resolve hostname: {hostname}") from e

        safe_ip = _pick_safe_ip(infos)

        port = request.url.port
        host_header = f"{hostname}:{port}" if port else hostname
        pinned_url = request.url.copy_with(host=safe_ip)
        new_headers = httpx.Headers(request.headers)
        new_headers["Host"] = host_header
        new_request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=new_headers,
            content=request.read(),
            extensions={**request.extensions, "sni_hostname": hostname},
        )
        return super().handle_request(new_request)


def _fetch_title(url: str) -> str | None:
    try:
        with httpx.Client(
            transport=_PinnedResolverTransport(),
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except _UnsafeURL as e:
        log.warning("SSRF blocked while fetching %s: %s", url, e)
        return None
    except (httpx.HTTPError, httpx.InvalidURL):
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    if soup.title and soup.title.string:
        stripped = soup.title.string.strip()
        if stripped:
            return stripped
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return None


def _guess_company(url: str) -> str | None:
    """Pull a sensible 'company' label from the URL.

    Handles common ATS hosts (lever, greenhouse, ashby, workable, smart-
    recruiters) by reading the first path segment, multi-part public
    suffixes (.co.uk, .com.au, ...) so we don't return "co", and uses
    .hostname so userinfo (`user@host`) doesn't bleed into the label.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return None

    if host in _ATS_PATH_COMPANY:
        first = parsed.path.lstrip("/").split("/", 1)[0]
        return first or None

    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LABEL_PUBLIC_SUFFIXES:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return None


def _row_to_dict(row: sqlite3.Row) -> dict:
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
        "last_touched_at": row["last_touched_at"],
    }
