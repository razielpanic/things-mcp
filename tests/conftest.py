"""Shared pytest fixtures for things-mcp tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

sys.path.insert(0, str(FIXTURE_DIR))
import create_fixture  # noqa: E402


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> Path:
    """Build a fresh fixture database for this test session.

    Built rather than read, because every date the generator writes is relative
    to the moment of generation. A committed .sqlite freezes those dates and
    then rots: DeadlineTask is `today + 14 days`, and once that passes,
    things.today() begins returning it (things.py's today includes
    `deadline="past"`) with no start_date. That is exactly how
    test_today_returns_items came to fail on clean HEAD while nothing in the
    code had changed -- and regenerating by hand only resets the timer.

    Building per session also keeps the generator honest: it is now the only
    way the suite gets a database, so a schema column added by hand to the
    committed file (which is how TMAreaTag, TMTag.shortcut and three
    TMChecklistItem columns came to exist only there) fails immediately
    instead of lying dormant until someone regenerates.
    """
    return Path(
        create_fixture.build(
            str(tmp_path_factory.mktemp("things") / "things_fixture.sqlite"),
            quiet=True,
        )
    )


@pytest.fixture()
def things_db(monkeypatch: pytest.MonkeyPatch, fixture_db: Path) -> Path:
    """Point things.py at the session's freshly built fixture database."""
    monkeypatch.setenv("THINGSDB", str(fixture_db))
    return fixture_db


@pytest.fixture()
def sample_raw_dict() -> dict:
    """Return a minimal valid things.py raw dict for _item_from_dict tests."""
    return {
        "uuid": "A" * 22,
        "title": "Test task",
        "type": "to-do",
        "status": "incomplete",
        "start": "Anytime",
        "start_date": None,
        "deadline": None,
        "notes": None,
        "tags": [],
        "project": None,
        "project_title": None,
        "area": None,
        "area_title": None,
        "heading_title": None,
        "checklist": [],
        "created": None,
        "modified": None,
        "stop_date": None,
        "today_index": None,
        "index": 0,
        "evening": 0,
    }


@pytest.fixture()
def valid_uuid() -> str:
    """Return a 22-char base62 test UUID."""
    return "A" * 22


@pytest.fixture(autouse=True)
def _isolate_anomaly_log(tmp_path, monkeypatch):
    """Never let a test append to the real status-anomaly log.

    That log is diagnostic evidence for unexplained status changes. A test run
    writing fake UUIDs into it destroys exactly what it is for.
    """
    monkeypatch.setenv("THINGS_MCP_ANOMALY_LOG", str(tmp_path / "anomalies.jsonl"))
    monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(tmp_path / "census.json"))


@pytest.fixture(autouse=True)
def _never_touch_the_real_database(fixture_db, monkeypatch):
    """Point every test at the fixture database, mocked ones included.

    The `things_db` fixture only covers tests that ask for it. Reads that do
    not go through things.py -- evening.py queries TMTask.startBucket directly
    -- would otherwise fall back to the default path and open the developer's
    real Things database. Read-only, but the promise in CONTRIBUTING.md is that
    pytest does not touch it at all, and a promise that holds only for the
    tests that remembered to opt in is not a promise.
    """
    from things_mcp import evening

    monkeypatch.setenv("THINGSDB", str(fixture_db))
    evening.reset_cache()
    yield
    evening.reset_cache()
