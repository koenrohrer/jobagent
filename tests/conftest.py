"""Shared fixtures: each test gets a fresh SQLite DB in a tmp dir."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Make the project root importable (db.py, applications.py live there).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Point JOBAGENT_DB at a tmp file, reload modules, init schema."""
    db_file = tmp_path / "jobagent.db"
    monkeypatch.setenv("JOBAGENT_DB", str(db_file))

    import db as db_module

    importlib.reload(db_module)
    import applications as apps_module

    importlib.reload(apps_module)

    db_module.init()
    return apps_module
