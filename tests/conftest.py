"""Shared pytest fixtures for things-mcp tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_DB = FIXTURE_DIR / "things_fixture.sqlite"


@pytest.fixture()
def things_db(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point things.py at the fixture database via THINGSDB env var."""
    monkeypatch.setenv("THINGSDB", str(FIXTURE_DB))
    return FIXTURE_DB


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
        "tags": None,
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
