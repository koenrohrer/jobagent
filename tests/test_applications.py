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
    for i in range(5):
        _seed(app_db, f"https://acme.com/{i}")
    assert len(app_db.list_(limit=2)) == 2
    # Cap at 500 should not blow up on huge values.
    assert len(app_db.list_(limit=10_000)) == 5


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
