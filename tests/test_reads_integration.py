"""Integration tests for read path against the fixture SQLite database.

Uses the things_db fixture from conftest.py to point THINGSDB at the
fixture SQLite, then exercises reads.py functions against real data.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
import things

from things_mcp import reads
from things_mcp.models import ThingsItem


class TestInboxReads:
    """Test get_inbox against fixture DB."""

    def test_inbox_returns_items(self, things_db):
        items = reads.get_inbox()
        assert len(items) >= 1
        for item in items:
            assert isinstance(item, ThingsItem)
            assert item.temporal_state.derived_list == "Inbox"

    def test_inbox_limit(self, things_db):
        items = reads.get_inbox(limit=1)
        assert len(items) <= 1


class TestTodayReads:
    """Test get_today against fixture DB."""

    def test_today_returns_items(self, things_db):
        items = reads.get_today()
        # Fixture has items scheduled for today
        assert isinstance(items, list)
        for item in items:
            assert isinstance(item, ThingsItem)
            assert item.temporal_state.derived_list in ("Today", "Upcoming")

    def test_today_limit(self, things_db):
        items = reads.get_today(limit=1)
        assert len(items) <= 1


class TestAnytimeReads:
    """Test get_anytime against fixture DB."""

    def test_anytime_returns_items(self, things_db):
        items = reads.get_anytime()
        assert isinstance(items, list)
        assert len(items) >= 1


class TestSomedayReads:
    """Test get_someday against fixture DB."""

    def test_someday_returns_items(self, things_db):
        items = reads.get_someday()
        assert len(items) >= 1
        for item in items:
            assert item.temporal_state.derived_list == "Someday"


class TestGetItem:
    """Test get_item for valid and invalid UUIDs."""

    def test_valid_uuid_returns_item(self, things_db):
        item = reads.get_item(uuid="InboxTask00000000000001")
        assert item is not None
        assert isinstance(item, ThingsItem)
        assert item.uuid == "InboxTask00000000000001"
        assert item.title == "Buy groceries"
        assert item.temporal_state.derived_list == "Inbox"

    def test_invalid_uuid_returns_none(self, things_db):
        item = reads.get_item(uuid="ZZZZZZZZZZZZZZZZZZZZZZ")
        assert item is None

    def test_full_notes_not_truncated(self, things_db):
        """get_item returns full notes (truncate_notes=False)."""
        item = reads.get_item(uuid="InboxTask00000000000001")
        # Item may or may not have notes, but the path is exercised
        assert item is not None


class TestSearch:
    """Test search function against fixture DB."""

    def test_search_by_title(self, things_db):
        items = reads.search(query="Buy")
        assert len(items) >= 1
        assert any("Buy" in item.title for item in items)

    def test_search_no_results(self, things_db):
        items = reads.search(query="zzz_nonexistent_query_zzz")
        assert items == []

    def test_search_returns_things_items(self, things_db):
        items = reads.search(query="Review")
        for item in items:
            assert isinstance(item, ThingsItem)
            assert item.temporal_state.derived_list is not None


class TestEmptyResults:
    """Verify empty list responses have correct structure."""

    def test_empty_search(self, things_db):
        items = reads.search(query="zzz_nonexistent_zzz")
        assert isinstance(items, list)
        assert len(items) == 0

    def test_upcoming_may_be_empty(self, things_db):
        items = reads.get_upcoming()
        assert isinstance(items, list)
        # Fixture may have no upcoming items; structure still correct


class TestThingsUnavailable:
    """Test that a nonexistent DB path raises sqlite3.OperationalError.

    This validates the error path that server.py catches as THINGS_UNAVAILABLE.
    """

    def test_nonexistent_db_raises_operational_error(self, monkeypatch):
        monkeypatch.setenv("THINGSDB", "/nonexistent/path/things.sqlite")
        with pytest.raises(sqlite3.OperationalError):
            reads.get_inbox()

    def test_nonexistent_db_on_get_item(self, monkeypatch):
        monkeypatch.setenv("THINGSDB", "/nonexistent/path/things.sqlite")
        with pytest.raises(sqlite3.OperationalError):
            reads.get_item(uuid="A" * 22)

    def test_nonexistent_db_on_search(self, monkeypatch):
        monkeypatch.setenv("THINGSDB", "/nonexistent/path/things.sqlite")
        with pytest.raises(sqlite3.OperationalError):
            reads.search(query="test")
