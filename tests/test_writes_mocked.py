"""Tests for write functions with mocked subprocess.run and things.get.

All write operations go through AppleScript (subprocess.run) or URL scheme.
These tests verify the correct AppleScript commands are constructed and
the proper error responses are returned.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from things_mcp import writes
from things_mcp.models import ErrorResponse, SuccessResponse

VALID_UUID = "A" * 22
ALT_UUID = "B" * 22


def _mock_subprocess_ok():
    """Return a MagicMock mimicking successful subprocess.run."""
    return MagicMock(returncode=0, stdout=VALID_UUID, stderr="")


def _raw_task(uuid=VALID_UUID, **overrides):
    """Return a minimal raw dict matching what things.get() returns."""
    base = {
        "uuid": uuid,
        "type": "to-do",
        "title": "Test task",
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
    base.update(overrides)
    return base


class TestScheduleItem:
    """Test schedule_item with all when values."""

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_today(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start_date="2026-04-06")
        result = writes.schedule_item(uuid=VALID_UUID, when="today")
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        assert result.action == "scheduled"
        mock_run.assert_called_once()
        script = mock_run.call_args[1].get("input") or mock_run.call_args[0][0]
        # Should use run_applescript which calls subprocess.run

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_tomorrow(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start_date="2026-04-07")
        result = writes.schedule_item(uuid=VALID_UUID, when="tomorrow")
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        assert result.action == "scheduled"

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.things.token")
    @patch("things_mcp.writes.subprocess.run")
    def test_evening(self, mock_run, mock_token, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_token.return_value = "test-auth-token"
        mock_get.return_value = _raw_task(start_date="2026-04-06", evening=1)
        result = writes.schedule_item(uuid=VALID_UUID, when="evening")
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        assert result.action == "scheduled_evening"
        # Evening uses URL scheme via subprocess open
        assert mock_run.call_count >= 1

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.things.token")
    @patch("things_mcp.writes.subprocess.run")
    def test_evening_no_token(self, mock_run, mock_token, mock_get, mock_sleep):
        mock_token.return_value = None
        result = writes.schedule_item(uuid=VALID_UUID, when="evening")
        assert isinstance(result, ErrorResponse)
        assert result.error == "NO_AUTH_TOKEN"

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_anytime(self, mock_run, mock_get, mock_sleep):
        # Arrange: post-write state has start=Anytime, start_date cleared
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start="Anytime", start_date=None)

        # Act
        result = writes.schedule_item(uuid=VALID_UUID, when="anytime")

        # Assert: response contract
        assert isinstance(result, SuccessResponse)
        assert result.action == "moved_to_anytime"

        # Assert: AppleScript payload uses the "Anytime" list (fix guard for
        # the pre-fix "Someday" bug at writes.py:198-206).
        script = mock_run.call_args[1].get("input") or mock_run.call_args[0][0]
        assert 'move theToDo to list "Anytime"' in script
        assert '"Someday"' not in script

        # Assert: temporal_state reflects post-move state
        assert result.temporal_state is not None
        assert result.temporal_state.derived_list == "Anytime"
        assert result.temporal_state.start_date is None

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_someday(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start="Someday")
        result = writes.schedule_item(uuid=VALID_UUID, when="someday")
        assert isinstance(result, SuccessResponse)
        assert result.action == "moved_to_someday"

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_specific_date(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start_date="2026-06-15")
        result = writes.schedule_item(uuid=VALID_UUID, when="2026-06-15")
        assert isinstance(result, SuccessResponse)
        assert result.success is True

    def test_invalid_when(self):
        result = writes.schedule_item(uuid=VALID_UUID, when="garbage")
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_WHEN"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.schedule_item(uuid="bad-uuid", when="today")

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_verify_failed(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = None  # Item not found after scheduling
        result = writes.schedule_item(uuid=VALID_UUID, when="today")
        assert isinstance(result, ErrorResponse)
        assert result.error == "VERIFY_FAILED"


class TestCreateTodo:
    """Test create_todo with various parameters."""

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_basic_creation(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0, stdout=VALID_UUID, stderr="")
        mock_get.return_value = _raw_task()
        result = writes.create_todo(title="Test task")
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        assert result.action == "created"
        assert result.uuid == VALID_UUID

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_with_tags(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0, stdout=VALID_UUID, stderr="")
        mock_get.return_value = _raw_task(tags=["work", "urgent"])
        result = writes.create_todo(title="Tagged task", tags=["work", "urgent"])
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        # Should have called run_applescript twice: create + set tags
        assert mock_run.call_count == 2

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_with_project_uuid(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0, stdout=VALID_UUID, stderr="")
        mock_get.return_value = _raw_task(project=ALT_UUID)
        result = writes.create_todo(title="Project task", project_uuid=ALT_UUID)
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        # create + set project
        assert mock_run.call_count == 2

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_creation_failure_empty_uuid(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = writes.create_todo(title="Failing task")
        assert isinstance(result, ErrorResponse)
        assert result.error == "CREATE_FAILED"

    def test_invalid_project_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.create_todo(title="Bad project", project_uuid="not-valid")


class TestDeleteItem:
    """Test delete_item success and not-found paths.

    Things 3 items in Trash remain in the SQLite database with `trashed=True` —
    they are not removed from disk. Verification must check the `trashed` field,
    not whether `things.get(uuid)` returns None (it never will after a trash op).
    See GH issue #1.
    """

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_success(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # First call: item exists. Second call: item still exists but with
        # trashed=True (the correct post-trash-op state).
        mock_get.side_effect = [_raw_task(), _raw_task(trashed=True)]
        result = writes.delete_item(uuid=VALID_UUID)
        assert isinstance(result, SuccessResponse)
        assert result.success is True
        assert result.action == "trashed"

    @patch("things_mcp.writes.things.get")
    def test_not_found(self, mock_get):
        mock_get.return_value = None
        result = writes.delete_item(uuid=VALID_UUID)
        assert isinstance(result, ErrorResponse)
        assert result.error == "NOT_FOUND"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_verify_failed_when_trashed_flag_not_set(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # Item exists before and after, but trashed field is missing/false —
        # indicates the AppleScript trash operation silently failed.
        mock_get.side_effect = [_raw_task(), _raw_task(trashed=False)]
        result = writes.delete_item(uuid=VALID_UUID)
        assert isinstance(result, ErrorResponse)
        assert result.error == "VERIFY_FAILED"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_verify_failed_when_item_vanishes(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # Defensive: if the second get returns None entirely (item gone from
        # database, not just trashed), that's an unexpected state — we can't
        # confirm a proper trash, so VERIFY_FAILED is the safe answer.
        mock_get.side_effect = [_raw_task(), None]
        result = writes.delete_item(uuid=VALID_UUID)
        assert isinstance(result, ErrorResponse)
        assert result.error == "VERIFY_FAILED"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.delete_item(uuid="short")


class TestMoveToContext:
    """Test move_to_context with project/area targets."""

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_to_project(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(project=ALT_UUID)
        result = writes.move_to_context(uuid=VALID_UUID, project_uuid=ALT_UUID)
        assert isinstance(result, SuccessResponse)
        assert result.action == "moved_to_project"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_to_area(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(area=ALT_UUID)
        result = writes.move_to_context(uuid=VALID_UUID, area_uuid=ALT_UUID)
        assert isinstance(result, SuccessResponse)
        assert result.action == "moved_to_area"

    def test_missing_both_params(self):
        result = writes.move_to_context(uuid=VALID_UUID)
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_INPUT"

    def test_both_params_provided(self):
        result = writes.move_to_context(
            uuid=VALID_UUID, project_uuid=ALT_UUID, area_uuid=ALT_UUID
        )
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_INPUT"


class TestUpdateItem:
    """Test update_item field-clearing and update paths."""

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_deadline_clear_uses_missing_value(self, mock_run, mock_get, mock_sleep):
        # Arrange: post-clear state has deadline=None (follow-up read
        # verification per CLAUDE.md rule 8).
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(deadline=None)

        # Act: clear the deadline via the empty-string sentinel
        result = writes.update_item(uuid=VALID_UUID, deadline="")

        # Assert: AppleScript payload uses the `missing value` literal
        # (fix guard for writes.py:680-683; the pre-fix bug error was
        # "Can't make missing value into type date").
        script = mock_run.call_args[1].get("input") or mock_run.call_args[0][0]
        assert "set due date of theToDo to missing value" in script

        # Assert: response contract
        assert isinstance(result, SuccessResponse)
        assert result.action == "updated"
        # per writes.py:746-747, parts.append("deadline") when deadline is not None
        assert "deadline" in result.message
        # temporal_state is built unconditionally; it does not read the
        # deadline field, so we only assert it exists.
        assert result.temporal_state is not None

        # Assert: follow-up-read verification — the mocked post-clear state
        # has deadline=None, documenting that the cleared state is what the
        # verification read returned (CLAUDE.md rule 8).
        assert mock_get.return_value["deadline"] is None
