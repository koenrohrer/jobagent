"""Cover the MCP tool wrappers in server.py."""

from __future__ import annotations


def _seed(url, company="acme", title="Engineer"):
    import db

    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO applications (url, company, title) VALUES (?, ?, ?)",
            (url, company, title),
        )
        return cur.lastrowid


def test_add_application_wraps_dict_in_model(app_db, monkeypatch):
    import server

    monkeypatch.setattr(
        server.applications,
        "add",
        lambda url: {
            "id": 1, "url": url, "company": "acme", "role": None,
            "title": "Engineer", "status": "interested", "notes": None,
            "created_at": "2026-01-01 00:00:00.000",
            "updated_at": "2026-01-01 00:00:00.000",
            "last_touched_at": None, "deduped": False,
        },
    )
    out = server.add_application("https://acme.com/jobs/1")
    assert out.url == "https://acme.com/jobs/1"
    assert out.deduped is False


def test_list_applications_returns_models(app_db):
    import server

    _seed("https://acme.com/a")
    _seed("https://acme.com/b")
    out = server.list_applications()
    assert {row.url for row in out} == {
        "https://acme.com/a",
        "https://acme.com/b",
    }


def test_update_status_returns_model(app_db):
    import server

    aid = _seed("https://acme.com/x")
    out = server.update_status(aid, "applied", note="cl v1")
    assert out.status == "applied"
    assert "cl v1" in (out.notes or "")


def test_main_initializes_db_and_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBAGENT_DB", str(tmp_path / "main.db"))
    import server

    called = []
    monkeypatch.setattr(server.mcp, "run", lambda: called.append("ran"))
    server.main()
    assert called == ["ran"]
    assert (tmp_path / "main.db").exists()
