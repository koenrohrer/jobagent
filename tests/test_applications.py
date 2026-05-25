"""End-to-end scenarios against the applications module + SQLite."""

from __future__ import annotations

import pytest


def _seed(apps, url, company="acme", title="Engineer"):
    """Insert directly to skip network in add()."""
    import db

    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (url, company, title) VALUES (?, ?, ?)",
            (url, company, title),
        )
        return cur.lastrowid


def test_list_empty(app_db):
    assert app_db.list_() == []


def test_list_returns_seeded_rows(app_db):
    _seed(app_db, "https://acme.com/jobs/1")
    _seed(app_db, "https://beta.com/jobs/2", company="beta")
    rows = app_db.list_()
    assert {r["url"] for r in rows} == {
        "https://acme.com/jobs/1",
        "https://beta.com/jobs/2",
    }


def test_list_filter_by_status(app_db):
    a = _seed(app_db, "https://acme.com/a")
    _seed(app_db, "https://acme.com/b")
    app_db.update_status(a, "applied")
    applied = app_db.list_(status="applied")
    assert [r["id"] for r in applied] == [a]
    interested = app_db.list_(status="interested")
    assert len(interested) == 1


def test_list_limit_is_capped(app_db):
    # Seed strictly more rows than the cap so the cap is structurally tested.
    cap = app_db.LIST_LIMIT_CAP
    for i in range(cap + 5):
        _seed(app_db, f"https://acme.com/{i}")
    assert len(app_db.list_(limit=2)) == 2
    assert len(app_db.list_(limit=10_000)) == cap


def test_list_rejects_unknown_status(app_db):
    with pytest.raises(ValueError):
        app_db.list_(status="bogus")


def test_update_status_changes_row(app_db):
    aid = _seed(app_db, "https://acme.com/x")
    row = app_db.update_status(aid, "applied")
    assert row["status"] == "applied"
    assert row["id"] == aid


def test_update_status_appends_note(app_db):
    aid = _seed(app_db, "https://acme.com/x")
    app_db.update_status(aid, "applied", note="cover letter v1")
    row = app_db.update_status(aid, "phone", note="recruiter call Tue")
    assert "cover letter v1" in row["notes"]
    assert "recruiter call Tue" in row["notes"]


def test_update_status_rejects_unknown(app_db):
    aid = _seed(app_db, "https://acme.com/x")
    with pytest.raises(ValueError):
        app_db.update_status(aid, "bogus")


def test_update_status_rejects_missing_id(app_db):
    with pytest.raises(ValueError):
        app_db.update_status(9999, "applied")


def test_update_status_records_event(app_db):
    import db

    aid = _seed(app_db, "https://acme.com/x")
    app_db.update_status(aid, "applied")
    with db.connect() as conn:
        events = conn.execute(
            "SELECT event_type, payload FROM events WHERE application_id = ?",
            (aid,),
        ).fetchall()
    types = [e["event_type"] for e in events]
    assert "status_changed" in types


def test_update_status_is_idempotent_when_unchanged(app_db):
    import db

    aid = _seed(app_db, "https://acme.com/x")
    app_db.update_status(aid, "applied")
    with db.connect() as conn:
        before = conn.execute(
            "SELECT updated_at, last_touched_at FROM applications WHERE id = ?",
            (aid,),
        ).fetchone()
        events_before = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE application_id = ?",
            (aid,),
        ).fetchone()["n"]

    # Re-call with the same status and no note — should be a no-op.
    app_db.update_status(aid, "applied")

    with db.connect() as conn:
        after = conn.execute(
            "SELECT updated_at, last_touched_at FROM applications WHERE id = ?",
            (aid,),
        ).fetchone()
        events_after = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE application_id = ?",
            (aid,),
        ).fetchone()["n"]

    assert before["updated_at"] == after["updated_at"]
    assert before["last_touched_at"] == after["last_touched_at"]
    assert events_before == events_after


def test_list_ordering_survives_same_second_updates(app_db):
    a = _seed(app_db, "https://acme.com/a")
    _seed(app_db, "https://acme.com/b")
    # Bump `a` after both are seeded; with millisecond timestamps, a should
    # come out first even though id is lower.
    app_db.update_status(a, "applied")
    rows = app_db.list_()
    assert rows[0]["id"] == a


def test_guess_company_ats_hosts(app_db):
    assert app_db._guess_company("https://jobs.lever.co/acme/abc-123") == "acme"
    assert app_db._guess_company("https://boards.greenhouse.io/acme/jobs/1") == "acme"
    assert app_db._guess_company("https://jobs.ashbyhq.com/acme/role") == "acme"


def test_guess_company_multipart_tld(app_db):
    assert app_db._guess_company("https://acme.co.uk/jobs/1") == "acme"
    assert app_db._guess_company("https://acme.com.au/jobs/1") == "acme"


def test_guess_company_ignores_userinfo(app_db):
    # urlparse(...).netloc would include "user@" — .hostname must be used.
    assert app_db._guess_company("https://user@acme.com/jobs") == "acme"


def test_guess_company_plain_domain(app_db):
    assert app_db._guess_company("https://acme.com/jobs/1") == "acme"


def test_fetch_title_falls_back_on_whitespace_title(app_db, monkeypatch):
    html = "<html><title>   </title><body><h1>Senior Engineer</h1></body></html>"

    class _FakeResp:
        text = html
        def raise_for_status(self):  # noqa: D401
            return None

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def get(self, url):
            return _FakeResp()

    monkeypatch.setattr(app_db.httpx, "Client", _FakeClient)
    assert app_db._fetch_title("https://acme.com/jobs/1") == "Senior Engineer"


def test_pinned_transport_blocks_loopback_ip_literal(app_db):
    import httpx as _httpx

    transport = app_db._PinnedResolverTransport()
    for bad in ("http://127.0.0.1/x", "http://169.254.169.254/x", "http://10.0.0.1/x"):
        with pytest.raises(app_db._UnsafeURL):
            transport.handle_request(_httpx.Request("GET", bad))


def test_db_status_check_constraint(app_db):
    import db
    import sqlite3 as _sqlite3

    with pytest.raises(_sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO applications (url, status) VALUES (?, ?)",
                ("https://acme.com/bad", "withdrawn"),
            )


def test_status_literal_matches_valid_statuses():
    from typing import get_args

    import db as db_module
    import server

    assert set(get_args(server.Status)) == db_module.VALID_STATUSES


def test_row_dict_exposes_last_touched_at(app_db):
    aid = _seed(app_db, "https://acme.com/x")
    row = app_db.update_status(aid, "applied")
    assert "last_touched_at" in row
    assert row["last_touched_at"] is not None


def test_add_handles_invalid_url_from_httpx(app_db, monkeypatch):
    import httpx as _httpx

    def _raise_invalid(self, url):
        raise _httpx.InvalidURL("bad url")

    monkeypatch.setattr(_httpx.Client, "get", _raise_invalid)
    # _validate_url is lax; _fetch_title must swallow InvalidURL so add() succeeds.
    row = app_db.add("https://acme.com/has spaces")
    assert row["url"] == "https://acme.com/has spaces"
    assert row["title"] is None


def test_add_dedupes_existing_url(app_db):
    _seed(app_db, "https://acme.com/dupe", title="Original")
    row = app_db.add("https://acme.com/dupe")
    assert row["deduped"] is True
    assert row["title"] == "Original"


def test_add_race_returns_deduped_via_on_conflict(app_db, monkeypatch):
    """First SELECT misses; another writer inserts before our INSERT fires."""
    import db as _db

    def _fetch_then_insert(url):
        with _db.connect() as conn:
            conn.execute(
                "INSERT INTO applications (url, company, title) VALUES (?, ?, ?)",
                (url, "racer", "Racer Title"),
            )
        return "stolen-title"

    monkeypatch.setattr(app_db, "_fetch_title", _fetch_then_insert)
    row = app_db.add("https://acme.com/race")
    assert row["deduped"] is True
    assert row["title"] == "Racer Title"


def test_add_rejects_non_http_scheme(app_db):
    with pytest.raises(ValueError):
        app_db.add("ftp://acme.com/jobs/1")


def test_pinned_transport_rejects_missing_hostname(app_db):
    """A request whose URL has no host must be rejected by the transport."""
    import httpx as _httpx

    transport = app_db._PinnedResolverTransport()
    # httpx.Request needs a parseable URL; build one and zero out the host.
    req = _httpx.Request("GET", "http://example.com/")
    req.url = req.url.copy_with(host="")
    with pytest.raises(app_db._UnsafeURL):
        transport.handle_request(req)


def test_pinned_transport_rejects_resolution_failure(app_db, monkeypatch):
    import httpx as _httpx
    import socket as _socket

    def _boom(*a, **kw):
        raise _socket.gaierror("nope")

    monkeypatch.setattr(app_db.socket, "getaddrinfo", _boom)
    transport = app_db._PinnedResolverTransport()
    with pytest.raises(app_db._UnsafeURL):
        transport.handle_request(_httpx.Request("GET", "http://nope.example/"))


def test_pinned_transport_pins_to_first_safe_ip(app_db, monkeypatch):
    """Public hostname: transport rewrites URL to literal IP, preserves Host
    and SNI extension, then calls super().handle_request. First IP is unsafe
    (covers the loop's skip branch); second is public.
    """
    import httpx as _httpx

    def _fake(host, *_a, **_kw):
        return [
            (0, 0, 0, "", ("10.0.0.1", 0)),
            (0, 0, 0, "", ("8.8.8.8", 0)),
        ]

    monkeypatch.setattr(app_db.socket, "getaddrinfo", _fake)

    captured = {}

    def _fake_super(self, req):
        captured["req"] = req
        return _httpx.Response(200, content=b"<html><title>OK</title></html>", request=req)

    monkeypatch.setattr(_httpx.HTTPTransport, "handle_request", _fake_super)

    transport = app_db._PinnedResolverTransport()
    resp = transport.handle_request(_httpx.Request("GET", "https://example.com:4443/x"))
    assert resp.status_code == 200

    pinned = captured["req"]
    assert pinned.url.host == "8.8.8.8"
    assert pinned.url.port == 4443
    assert pinned.headers["Host"] == "example.com:4443"
    assert pinned.extensions.get("sni_hostname") == "example.com"


def test_pinned_transport_pins_without_port(app_db, monkeypatch):
    """Host header drops the port when the URL has none (default port branch)."""
    import httpx as _httpx

    monkeypatch.setattr(
        app_db.socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", ("8.8.8.8", 0))],
    )

    captured = {}

    def _fake_super(self, req):
        captured["req"] = req
        return _httpx.Response(200, request=req)

    monkeypatch.setattr(_httpx.HTTPTransport, "handle_request", _fake_super)

    transport = app_db._PinnedResolverTransport()
    transport.handle_request(_httpx.Request("GET", "https://example.com/x"))
    assert captured["req"].headers["Host"] == "example.com"


def test_pinned_transport_allows_safe_ip_literal(app_db, monkeypatch):
    """An http://<public-IP>/ URL bypasses DNS but still hits super().handle_request."""
    import httpx as _httpx

    captured = {}

    def _fake_super(self, req):
        captured["req"] = req
        return _httpx.Response(200, request=req)

    monkeypatch.setattr(_httpx.HTTPTransport, "handle_request", _fake_super)
    transport = app_db._PinnedResolverTransport()
    transport.handle_request(_httpx.Request("GET", "http://8.8.8.8/x"))
    # No URL rewriting for IP literals; the original request is forwarded.
    assert captured["req"].url.host == "8.8.8.8"


def test_pinned_transport_raises_when_all_ips_unsafe(app_db, monkeypatch):
    import httpx as _httpx

    monkeypatch.setattr(
        app_db.socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", ("10.0.0.1", 0))],
    )
    transport = app_db._PinnedResolverTransport()
    with pytest.raises(app_db._UnsafeURL):
        transport.handle_request(_httpx.Request("GET", "https://rebind.example/x"))


def test_pick_safe_ip_handles_scoped_ipv6(app_db):
    """Zone-id suffix on IPv6 (fe80::1%eth0) must be stripped before parsing."""
    # fe80:: is link-local — should be rejected.
    with pytest.raises(app_db._UnsafeURL):
        app_db._pick_safe_ip([(0, 0, 0, "", ("fe80::1%eth0", 0))])


def test_fetch_title_logs_ssrf_block(app_db, monkeypatch, caplog):
    """_fetch_title swallows _UnsafeURL but logs a warning so blocks are visible."""
    import logging as _logging

    def _always_block(self, request):
        raise app_db._UnsafeURL("forced block")

    monkeypatch.setattr(
        app_db._PinnedResolverTransport, "handle_request", _always_block
    )
    with caplog.at_level(_logging.WARNING, logger=app_db.log.name):
        assert app_db._fetch_title("https://example.com/x") is None
    assert any("SSRF blocked" in r.message for r in caplog.records)


def _patch_fake_client(monkeypatch, app_db, html):
    class _Resp:
        text = html
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def get(self, url):
            return _Resp()

    monkeypatch.setattr(app_db.httpx, "Client", _Client)


def test_fetch_title_returns_clean_title(app_db, monkeypatch):
    _patch_fake_client(monkeypatch, app_db, "<html><title>Staff Engineer</title></html>")
    assert app_db._fetch_title("https://acme.com/jobs/1") == "Staff Engineer"


def test_fetch_title_uses_h1_when_no_title_tag(app_db, monkeypatch):
    _patch_fake_client(monkeypatch, app_db, "<html><body><h1>Lead Dev</h1></body></html>")
    assert app_db._fetch_title("https://acme.com/jobs/1") == "Lead Dev"


def test_fetch_title_returns_none_when_no_title_or_h1(app_db, monkeypatch):
    _patch_fake_client(monkeypatch, app_db, "<html><body><p>nothing</p></body></html>")
    assert app_db._fetch_title("https://acme.com/jobs/1") is None


def test_guess_company_returns_none_for_empty_host(app_db):
    assert app_db._guess_company("file:///etc/passwd") is None


def test_guess_company_returns_none_for_single_label_host(app_db):
    assert app_db._guess_company("http://localhost/jobs") is None


def test_update_status_with_note_only_skips_event(app_db):
    """Status unchanged + note appended: row updates, no status_changed event."""
    import db

    aid = _seed(app_db, "https://acme.com/x")
    app_db.update_status(aid, "applied")
    with db.connect() as conn:
        events_before = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE application_id = ?",
            (aid,),
        ).fetchone()["n"]

    row = app_db.update_status(aid, "applied", note="added a note")
    assert "added a note" in row["notes"]

    with db.connect() as conn:
        events_after = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE application_id = ?",
            (aid,),
        ).fetchone()["n"]
    assert events_after == events_before


# --- Real-HTTP integration test for the pinned-resolver transport ---------


def test_pinned_transport_real_http_roundtrip(app_db, monkeypatch):
    """End-to-end: real httpx client → _PinnedResolverTransport → real HTTP
    server. Catches drift in httpx's URL/SNI/Host contract that pure mocks
    miss. We point a fake hostname at 127.0.0.1 via getaddrinfo + allow
    loopback in the classifier for the duration of this test only.
    """
    import http.server
    import threading

    received_host_headers: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            received_host_headers.append(self.headers.get("Host", ""))
            body = b"<html><title>real</title></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a, **_kw):  # silence stderr noise
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Map our fake hostname to loopback. Pass through for everything else
        # so httpx's own getaddrinfo call (on the rewritten 127.0.0.1 URL)
        # still gets back a usable socket family/type/proto tuple.
        real_getaddrinfo = app_db.socket.getaddrinfo

        def _fake_getaddrinfo(host, *a, **kw):
            if host == "test.local":
                return real_getaddrinfo("127.0.0.1", *a, **kw)
            return real_getaddrinfo(host, *a, **kw)

        monkeypatch.setattr(app_db.socket, "getaddrinfo", _fake_getaddrinfo)
        # Permit loopback for this test only — production callers still get
        # the strict classifier because monkeypatch unwinds on teardown.
        monkeypatch.setattr(app_db, "_is_unsafe_ip", lambda _ip: False)

        title = app_db._fetch_title(f"http://test.local:{port}/jobs/1")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert title == "real"
    # The server received Host: test.local:<port>, NOT Host: 127.0.0.1:<port> —
    # confirms the transport's hostname-preserving rewrite is wired correctly
    # through real httpx.
    assert received_host_headers == [f"test.local:{port}"]


# --- CHECK-constraint migration -------------------------------------------


_LEGACY_SCHEMA = """
CREATE TABLE applications (
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
"""


def test_migration_is_noop_when_table_absent(tmp_path, monkeypatch):
    """Fresh DB with no applications table: migration must return without error."""
    monkeypatch.setenv("JOBAGENT_DB", str(tmp_path / "fresh.db"))
    import importlib
    import db as db_module
    importlib.reload(db_module)
    with db_module.connect() as conn:
        db_module._migrate_status_check_constraint(conn)
    # Then a normal init() should succeed and create the table with CHECK.
    db_module.init()
    with db_module.connect() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()["sql"]
    assert "CHECK" in sql.upper()


def test_migration_is_noop_when_constraint_present(app_db):
    """Calling init() twice doesn't recreate the (already-correct) table."""
    import db
    db.init()  # second invocation; existing table already has CHECK
    with db.connect() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()["sql"]
    assert "CHECK" in sql.upper()


def test_migration_retrofits_legacy_table(tmp_path, monkeypatch):
    """Old schema (no CHECK) → init() rebuilds with CHECK, preserves rows."""
    db_file = tmp_path / "legacy.db"
    monkeypatch.setenv("JOBAGENT_DB", str(db_file))
    import importlib
    import sqlite3 as _sqlite3
    import db as db_module
    importlib.reload(db_module)

    # Hand-craft a legacy applications table (no CHECK) + one row.
    with db_module.connect() as conn:
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO applications (url, status) VALUES (?, ?)",
            ("https://acme.com/legacy", "applied"),
        )

    db_module.init()  # triggers the migration

    with db_module.connect() as conn:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()["sql"]
        assert "CHECK" in sql.upper()
        # Original row preserved.
        row = conn.execute(
            "SELECT url, status FROM applications WHERE url = ?",
            ("https://acme.com/legacy",),
        ).fetchone()
        assert row["status"] == "applied"

    # CHECK is now enforced on new writes.
    with pytest.raises(_sqlite3.IntegrityError):
        with db_module.connect() as conn:
            conn.execute(
                "INSERT INTO applications (url, status) VALUES (?, ?)",
                ("https://acme.com/bad", "withdrawn"),
            )
