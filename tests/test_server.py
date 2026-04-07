"""Tests for async server tool handlers with mocked reads/writes.

All server handlers are async and wrap reads/writes with error handling.
These tests verify correct delegation and error response generation.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from things_mcp.models import (
    AreaItem,
    ErrorResponse,
    SuccessResponse,
    TemporalState,
    ThingsItem,
)
from things_mcp.server import (
    create_todo,
    delete_item,
    get_anytime,
    get_areas,
    get_inbox,
    get_item,
    get_logbook,
    get_projects,
    get_someday,
    get_today,
    get_upcoming,
    schedule_item,
    search,
)


def _make_item(uuid="A" * 22, title="Test", derived_list="Inbox") -> ThingsItem:
    """Build a minimal ThingsItem for mock returns."""
    return ThingsItem(
        uuid=uuid,
        title=title,
        type="to-do",
        temporal_state=TemporalState(
            start="Anytime",
            start_date=None,
            derived_list=derived_list,
            status="incomplete",
            evening=False,
        ),
    )


def _make_area(uuid="A" * 22, title="Test Area") -> AreaItem:
    return AreaItem(uuid=uuid, title=title)


class TestReadHandlers:
    """Test read tool handlers return correct structure."""

    @patch("things_mcp.server.reads.get_inbox")
    async def test_get_inbox_returns_items(self, mock_fn):
        mock_fn.return_value = [_make_item(derived_list="Inbox")]
        result = await get_inbox()
        assert result["view"] == "Inbox"
        assert result["count"] == 1
        assert len(result["items"]) == 1

    @patch("things_mcp.server.reads.get_inbox")
    async def test_get_inbox_empty(self, mock_fn):
        mock_fn.return_value = []
        result = await get_inbox()
        assert result["count"] == 0
        assert result["items"] == []

    @patch("things_mcp.server.reads.get_item")
    async def test_get_item_not_found(self, mock_fn):
        mock_fn.return_value = None
        result = await get_item(uuid="Z" * 22)
        assert result["error"] == "NOT_FOUND"
        assert result["success"] is False

    @patch("things_mcp.server.reads.get_item")
    async def test_get_item_found(self, mock_fn):
        item = _make_item()
        mock_fn.return_value = item
        result = await get_item(uuid="A" * 22)
        assert result["uuid"] == "A" * 22
        assert "temporal_state" in result

    @patch("things_mcp.server.reads.get_today")
    async def test_get_today(self, mock_fn):
        mock_fn.return_value = [_make_item(derived_list="Today")]
        result = await get_today()
        assert result["view"] == "Today"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.get_anytime")
    async def test_get_anytime(self, mock_fn):
        mock_fn.return_value = [_make_item(derived_list="Anytime")]
        result = await get_anytime()
        assert result["view"] == "Anytime"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.get_someday")
    async def test_get_someday(self, mock_fn):
        mock_fn.return_value = [_make_item(derived_list="Someday")]
        result = await get_someday()
        assert result["view"] == "Someday"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.get_upcoming")
    async def test_get_upcoming(self, mock_fn):
        mock_fn.return_value = []
        result = await get_upcoming()
        assert result["view"] == "Upcoming"
        assert result["count"] == 0

    @patch("things_mcp.server.reads.get_logbook")
    async def test_get_logbook(self, mock_fn):
        mock_fn.return_value = [_make_item(derived_list="Logbook")]
        result = await get_logbook()
        assert result["view"] == "Logbook"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.search")
    async def test_search(self, mock_fn):
        mock_fn.return_value = [_make_item()]
        result = await search(query="test")
        assert result["view"] == "Search"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.get_projects")
    async def test_get_projects(self, mock_fn):
        mock_fn.return_value = [_make_item(title="My Project")]
        result = await get_projects()
        assert result["view"] == "Projects"
        assert result["count"] == 1

    @patch("things_mcp.server.reads.get_areas")
    async def test_get_areas(self, mock_fn):
        mock_fn.return_value = [_make_area()]
        result = await get_areas()
        assert result["view"] == "Areas"
        assert result["count"] == 1


class TestWriteHandlers:
    """Test write tool handlers delegate correctly."""

    @patch("things_mcp.server.writes.create_todo")
    async def test_create_todo_success(self, mock_fn):
        mock_fn.return_value = SuccessResponse(
            uuid="A" * 22, message="Created", action="created"
        )
        result = await create_todo(title="New task")
        assert result["success"] is True
        assert result["action"] == "created"
        mock_fn.assert_called_once()

    @patch("things_mcp.server.writes.schedule_item")
    async def test_schedule_item_success(self, mock_fn):
        mock_fn.return_value = SuccessResponse(
            uuid="A" * 22, message="Scheduled", action="scheduled"
        )
        result = await schedule_item(uuid="A" * 22, when="today")
        assert result["success"] is True
        mock_fn.assert_called_once_with(uuid="A" * 22, when="today")

    @patch("things_mcp.server.writes.delete_item")
    async def test_delete_item_success(self, mock_fn):
        mock_fn.return_value = SuccessResponse(
            uuid="A" * 22, message="Trashed", action="trashed"
        )
        result = await delete_item(uuid="A" * 22)
        assert result["success"] is True
        mock_fn.assert_called_once_with(uuid="A" * 22)


class TestErrorHandling:
    """Test server error handling catches exceptions correctly."""

    @patch("things_mcp.server.reads.get_inbox")
    async def test_sqlite_error_returns_things_unavailable(self, mock_fn):
        mock_fn.side_effect = sqlite3.OperationalError("unable to open database file")
        result = await get_inbox()
        assert result["error"] == "THINGS_UNAVAILABLE"
        assert result["success"] is False

    @patch("things_mcp.server.reads.get_inbox")
    async def test_generic_exception_returns_read_error(self, mock_fn):
        mock_fn.side_effect = RuntimeError("unexpected failure")
        result = await get_inbox()
        assert result["error"] == "READ_ERROR"
        assert "unexpected failure" in result["message"]

    @patch("things_mcp.server.reads.get_item")
    async def test_sqlite_error_on_get_item(self, mock_fn):
        mock_fn.side_effect = sqlite3.OperationalError("database locked")
        result = await get_item(uuid="A" * 22)
        assert result["error"] == "THINGS_UNAVAILABLE"

    @patch("things_mcp.server.reads.search")
    async def test_sqlite_error_on_search(self, mock_fn):
        mock_fn.side_effect = sqlite3.OperationalError("unable to open database file")
        result = await search(query="test")
        assert result["error"] == "THINGS_UNAVAILABLE"

    @patch("things_mcp.server.writes.create_todo")
    async def test_write_exception_returns_write_error(self, mock_fn):
        mock_fn.side_effect = RuntimeError("AppleScript failed")
        result = await create_todo(title="Fail")
        assert result["error"] == "WRITE_ERROR"
        assert "AppleScript failed" in result["message"]

    @patch("things_mcp.server.writes.schedule_item")
    async def test_write_sqlite_error(self, mock_fn):
        mock_fn.side_effect = sqlite3.OperationalError("unable to open database file")
        result = await schedule_item(uuid="A" * 22, when="today")
        assert result["error"] == "THINGS_UNAVAILABLE"

    @patch("things_mcp.server.writes.delete_item")
    async def test_delete_exception_returns_write_error(self, mock_fn):
        mock_fn.side_effect = RuntimeError("Things 3 is not responding")
        result = await delete_item(uuid="A" * 22)
        assert result["error"] == "WRITE_ERROR"
