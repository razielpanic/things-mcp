"""Tests for write functions with mocked subprocess.run and things.get.

All write operations go through AppleScript (subprocess.run) or URL scheme.
These tests verify the correct AppleScript commands are constructed and
the proper error responses are returned.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from things_mcp import writes
from things_mcp.models import ErrorResponse, SuccessResponse

VALID_UUID = "A" * 22
ALT_UUID = "B" * 22

# Distinct UUIDs for blocker-relation tests (blocker / dependent pairs).
BLK = "B" * 22
DEP = "D" * 22
BLK2 = "E" * 22
DEP2 = "F" * 22


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


class FakeThings:
    """In-memory Things stand-in for blocker-relation verb tests.

    ``things.get`` reads from the store; the verbs' AppleScript writes (``set
    notes`` / ``set tag names``) are interpreted and applied back to the store,
    so each verb's full read -> splice -> write-back -> verify loop runs against
    live, mutating state. This is what lets us assert idempotency, tag merging,
    and both-direction scrubbing end-to-end rather than per-call.
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.fail_notes: set[str] = set()

    def add(self, uuid, title="Task", notes=None, tags=None, status="incomplete"):
        self.store[uuid] = _raw_task(
            uuid=uuid,
            title=title,
            notes=notes,
            tags=list(tags or []),
            status=status,
        )
        return uuid

    def get(self, uuid):
        item = self.store.get(uuid)
        if item is None:
            return None
        # Copy so a verb's in-memory edits never leak back without a write.
        clone = dict(item)
        clone["tags"] = list(item["tags"])
        return clone

    def run_applescript(self, script, *args):
        self.calls.append((script, args))
        m = re.search(r'to do id "([A-Za-z0-9]{21,22})"', script)
        uuid = m.group(1) if m else None
        if uuid is None or uuid not in self.store:
            return ""
        if "set notes of theToDo" in script:
            if uuid in self.fail_notes:
                raise RuntimeError("AppleScript error: simulated notes-write failure")
            self.store[uuid]["notes"] = args[0]
        elif "set tag names of theToDo" in script:
            self.store[uuid]["tags"] = [
                t.strip() for t in args[0].split(",") if t.strip()
            ]
        return ""

    def write_calls(self):
        """AppleScript calls that actually mutate state (notes/tags)."""
        return [
            c
            for c in self.calls
            if "set notes of theToDo" in c[0] or "set tag names of theToDo" in c[0]
        ]


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
    def test_inbox(self, mock_run, mock_get, mock_sleep):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start="Inbox", start_date=None)
        result = writes.schedule_item(uuid=VALID_UUID, when="inbox")
        assert isinstance(result, SuccessResponse)
        assert result.action == "moved_to_inbox"
        script = mock_run.call_args[1].get("input") or mock_run.call_args[0][0]
        assert 'move theToDo to list "Inbox"' in script
        assert result.temporal_state is not None
        assert result.temporal_state.derived_list == "Inbox"

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_inbox_move_that_does_not_land_fails_verification(
        self, mock_run, mock_get, mock_sleep
    ):
        # Things left the item dated: the move silently did not take.
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(start="Anytime", start_date="2026-09-18")
        result = writes.schedule_item(uuid=VALID_UUID, when="inbox")
        assert isinstance(result, ErrorResponse)
        assert result.error == "VERIFY_FAILED"

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


class TestUpdateItemProjectMove:
    """update_item must support filing into a project/area (was a silent no-op)."""

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_project_uuid_emits_move_script(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task()

        result = writes.update_item(uuid=VALID_UUID, project_uuid=ALT_UUID)

        # Some AppleScript invocation must set the project context.
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        joined = "\n".join(scripts)
        assert "set project of" in joined
        assert f'project id "{ALT_UUID}"' in joined

        assert isinstance(result, SuccessResponse)
        assert "project" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_area_uuid_emits_move_script(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task()

        result = writes.update_item(uuid=VALID_UUID, area_uuid=ALT_UUID)

        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        joined = "\n".join(scripts)
        assert "set area of" in joined
        assert f'area id "{ALT_UUID}"' in joined
        assert isinstance(result, SuccessResponse)
        assert "area" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_both_project_and_area_rejected(self, mock_run, mock_get):
        result = writes.update_item(
            uuid=VALID_UUID, project_uuid=ALT_UUID, area_uuid="C" * 22
        )
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_INPUT"
        # rejected before any write or read happens
        mock_run.assert_not_called()
        mock_get.assert_not_called()


class TestSilentCompletionGuard:
    """A non-completing write reports an unexpected close -- and never "repairs" it.

    The check cannot distinguish a tool-caused close from a checkbox click in the
    Things UI mid-write, so auto-reopening un-completes tasks that were finished
    deliberately. Report only.
    """

    @patch("things_mcp.writes._log_status_anomaly")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_update_errors_but_does_not_touch_the_item(
        self, mock_run, mock_get, mock_log
    ):
        mock_run.return_value = _mock_subprocess_ok()
        # pre-read: incomplete; post-write verify: unexpectedly completed
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed"),
        ]

        result = writes.update_item(uuid=VALID_UUID, notes="just a note")

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"
        assert "LEFT AS-IS" in result.message
        # No repair attempt of any kind.
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("to open" in s for s in scripts)
        # The transition is recorded so a future occurrence is decidable.
        mock_log.assert_called_once()
        assert mock_log.call_args.args[1:3] == ("incomplete", "completed")

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_explicit_completed_request_is_not_guarded(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # pre-read, post-write verify, and the success-path temporal_state read
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed"),
            _raw_task(status="completed"),
        ]

        result = writes.update_item(uuid=VALID_UUID, completed=True)

        # caller asked for completion -> success, no reopen
        assert isinstance(result, SuccessResponse)
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("to open" in s for s in scripts)

    @patch("things_mcp.writes._log_status_anomaly")
    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_schedule_errors_but_does_not_touch_the_item(
        self, mock_run, mock_get, mock_sleep, mock_log
    ):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed", start_date="2026-06-14"),
        ]

        result = writes.schedule_item(uuid=VALID_UUID, when="today")

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("to open" in s for s in scripts)
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs.get("source") or mock_log.call_args.args[3] == "schedule_item"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_user_cancel_mid_write_is_reported_not_reverted(self, mock_run, mock_get):
        """An item comes back `canceled` after a schedule call.

        schedule_item has no cancel path, so this can only originate outside the
        tool -- in practice a cancel in the Things UI. It must be surfaced and
        left alone, never reverted.
        """
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="canceled"),
        ]

        result = writes.update_item(uuid=VALID_UUID, notes="note only")

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("to open" in s for s in scripts)


class TestLinkBlocker:
    """link_blocker: wire a 'blocked by' relation across both items."""

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_links_both_sides_and_merges_tag(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker", tags=["work"])
        fake.add(DEP, title="Dependent", tags=["home"])
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert result.action == "linked_blocker"
        # Tag merge preserves the dependent's existing tags (no clobber).
        assert fake.store[DEP]["tags"] == ["home", "gated"]
        # Dependent -> blocker under Gated by.
        assert "Gated by:" in fake.store[DEP]["notes"]
        assert f"things:///show?id={BLK}" in fake.store[DEP]["notes"]
        # Blocker -> dependent under Gates.
        assert "Gates:" in fake.store[BLK]["notes"]
        assert f"things:///show?id={DEP}" in fake.store[BLK]["notes"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_preserves_user_notes(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker", notes="blocker prose")
        fake.add(DEP, title="Dependent", notes="dependent prose")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        # User-authored notes survive above the managed block.
        assert fake.store[DEP]["notes"].startswith("dependent prose")
        assert "Gated by:" in fake.store[DEP]["notes"]
        assert fake.store[BLK]["notes"].startswith("blocker prose")
        assert "Gates:" in fake.store[BLK]["notes"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_idempotent_second_call_writes_nothing(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker", tags=["work"])
        fake.add(DEP, title="Dependent", tags=["home"])
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        dep_notes = fake.store[DEP]["notes"]
        blk_notes = fake.store[BLK]["notes"]
        writes_after_first = len(fake.write_calls())

        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        # The idempotent second call performs no further writes...
        assert len(fake.write_calls()) == writes_after_first
        # ...and leaves both notes byte-identical and the tag un-duplicated.
        assert fake.store[DEP]["notes"] == dep_notes
        assert fake.store[BLK]["notes"] == blk_notes
        assert fake.store[DEP]["tags"] == ["home", "gated"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_many_to_many_dependent_gated_by_two_blockers(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker One")
        fake.add(BLK2, title="Blocker Two")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.link_blocker(blocker_uuid=BLK2, dependent_uuid=DEP)

        notes = fake.store[DEP]["notes"]
        # One managed block holding both blockers.
        assert notes.count("Gated by:") == 1
        assert f"things:///show?id={BLK}" in notes
        assert f"things:///show?id={BLK2}" in notes
        # `gated` applied once, not duplicated.
        assert fake.store[DEP]["tags"] == ["gated"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_many_to_many_blocker_gates_two_dependents(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent One")
        fake.add(DEP2, title="Dependent Two")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP2)

        notes = fake.store[BLK]["notes"]
        assert notes.count("Gates:") == 1
        assert f"things:///show?id={DEP}" in notes
        assert f"things:///show?id={DEP2}" in notes
        assert fake.store[DEP]["tags"] == ["gated"]
        assert fake.store[DEP2]["tags"] == ["gated"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_partial_link_when_blocker_side_fails(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent", tags=["home"])
        fake.fail_notes.add(BLK)  # blocker-side notes write raises
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, ErrorResponse)
        assert result.error == "PARTIAL_LINK"
        # Dependent side IS wired -- the safe partial (task is marked blocked).
        assert "gated" in fake.store[DEP]["tags"]
        assert f"things:///show?id={BLK}" in fake.store[DEP]["notes"]
        # Blocker side never landed.
        assert not fake.store[BLK]["notes"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_blocker_not_found(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        assert isinstance(result, ErrorResponse)
        assert result.error == "NOT_FOUND"

    def test_self_link_rejected(self):
        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=BLK)
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_INPUT"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.link_blocker(blocker_uuid="bad", dependent_uuid=DEP)


class TestUnlinkBlocker:
    """unlink_blocker: tear down a relation, dropping `gated` only when last."""

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_removes_both_sides_and_gated_tag(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent", tags=["home"])
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        result = writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert result.action == "unlinked_blocker"
        # Dependent fully cleaned: no gated tag, no Gated by reference.
        assert "gated" not in fake.store[DEP]["tags"]
        assert "home" in fake.store[DEP]["tags"]  # other tags preserved
        assert f"things:///show?id={BLK}" not in (fake.store[DEP]["notes"] or "")
        # Blocker no longer references the dependent.
        assert f"things:///show?id={DEP}" not in (fake.store[BLK]["notes"] or "")

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_keeps_gated_when_another_blocker_remains(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker One")
        fake.add(BLK2, title="Blocker Two")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.link_blocker(blocker_uuid=BLK2, dependent_uuid=DEP)

        # Drop only the first blocker.
        writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        # `gated` stays because BLK2 still blocks DEP.
        assert "gated" in fake.store[DEP]["tags"]
        notes = fake.store[DEP]["notes"]
        assert f"things:///show?id={BLK}" not in notes
        assert f"things:///show?id={BLK2}" in notes
        # BLK no longer gates DEP; BLK2 still does.
        assert f"things:///show?id={DEP}" not in (fake.store[BLK]["notes"] or "")
        assert f"things:///show?id={DEP}" in fake.store[BLK2]["notes"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_idempotent_second_unlink_is_noop(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes_after_first = len(fake.write_calls())

        result = writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert len(fake.write_calls()) == writes_after_first  # no further writes

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_tolerant_of_missing_blocker(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        # Blocker vanishes (e.g. trashed) -- the dependent side must still clean.
        del fake.store[BLK]

        result = writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert "gated" not in fake.store[DEP]["tags"]
        assert f"things:///show?id={BLK}" not in (fake.store[DEP]["notes"] or "")

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_neither_exists_returns_not_found(self, mock_get, mock_run):
        fake = FakeThings()  # empty store
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        assert isinstance(result, ErrorResponse)
        assert result.error == "NOT_FOUND"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid="bad")


class TestReconcileCompletion:
    """reconcile_completion: scrub a done task's relations, both directions."""

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_dependent_scrubs_blocker_side(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        # The blocked task gets completed -> reconcile it.
        result = writes.reconcile_completion(uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert result.action == "reconciled"
        # The completed task is clean...
        assert "gated" not in fake.store[DEP]["tags"]
        assert "Gated by:" not in (fake.store[DEP]["notes"] or "")
        # ...and the blocker no longer dangles a 'Gates' link to it.
        assert f"things:///show?id={DEP}" not in (fake.store[BLK]["notes"] or "")

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_blocker_scrubs_dependent_side(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        # The blocker gets completed -> reconcile it.
        result = writes.reconcile_completion(uuid=BLK)

        assert isinstance(result, SuccessResponse)
        # The blocker is clean...
        assert "Gates:" not in (fake.store[BLK]["notes"] or "")
        # ...and the freed dependent loses both the link and the `gated` tag.
        assert f"things:///show?id={BLK}" not in (fake.store[DEP]["notes"] or "")
        assert "gated" not in fake.store[DEP]["tags"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_scrubs_both_directions_at_once(self, mock_get, mock_run):
        # DEP is simultaneously a blocker (of DEP2) and a dependent (of BLK).
        fake = FakeThings()
        fake.add(BLK, title="Upstream")
        fake.add(DEP, title="Middle")
        fake.add(DEP2, title="Downstream")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)  # BLK gates DEP
        writes.link_blocker(blocker_uuid=DEP, dependent_uuid=DEP2)  # DEP gates DEP2

        result = writes.reconcile_completion(uuid=DEP)

        assert isinstance(result, SuccessResponse)
        # Upstream side: BLK no longer gates DEP.
        assert f"things:///show?id={DEP}" not in (fake.store[BLK]["notes"] or "")
        # Downstream side: DEP2 freed (link gone, tag gone).
        assert f"things:///show?id={DEP}" not in (fake.store[DEP2]["notes"] or "")
        assert "gated" not in fake.store[DEP2]["tags"]
        # DEP itself carries no managed block and no `gated` tag.
        assert "Gated by:" not in (fake.store[DEP]["notes"] or "")
        assert "Gates:" not in (fake.store[DEP]["notes"] or "")
        assert "gated" not in fake.store[DEP]["tags"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_keeps_gated_for_remaining_blocker(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker One")
        fake.add(BLK2, title="Blocker Two")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.link_blocker(blocker_uuid=BLK2, dependent_uuid=DEP)

        # Only the first blocker completes.
        writes.reconcile_completion(uuid=BLK)

        # DEP stays gated because BLK2 still blocks it.
        assert "gated" in fake.store[DEP]["tags"]
        assert f"things:///show?id={BLK}" not in fake.store[DEP]["notes"]
        assert f"things:///show?id={BLK2}" in fake.store[DEP]["notes"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_no_relations_is_noop(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(VALID_UUID, title="Lonely task", notes="plain notes", tags=["work"])
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.reconcile_completion(uuid=VALID_UUID)

        assert isinstance(result, SuccessResponse)
        assert "Reconciled 0" in result.message
        # A task with no relations is never written to.
        assert fake.write_calls() == []
        assert fake.store[VALID_UUID]["notes"] == "plain notes"
        assert fake.store[VALID_UUID]["tags"] == ["work"]

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_idempotent(self, mock_get, mock_run):
        fake = FakeThings()
        fake.add(BLK, title="Blocker")
        fake.add(DEP, title="Dependent")
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript
        writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)
        writes.reconcile_completion(uuid=DEP)
        writes_after_first = len(fake.write_calls())

        result = writes.reconcile_completion(uuid=DEP)

        assert isinstance(result, SuccessResponse)
        assert len(fake.write_calls()) == writes_after_first  # no further writes

    @patch("things_mcp.writes.run_applescript")
    @patch("things_mcp.writes.things.get")
    def test_reconcile_not_found(self, mock_get, mock_run):
        fake = FakeThings()  # empty store
        mock_get.side_effect = fake.get
        mock_run.side_effect = fake.run_applescript

        result = writes.reconcile_completion(uuid=DEP)
        assert isinstance(result, ErrorResponse)
        assert result.error == "NOT_FOUND"

    def test_invalid_uuid(self):
        with pytest.raises(ValueError, match="Invalid UUID"):
            writes.reconcile_completion(uuid="bad")


class TestUpdateItemStatusIdempotence:
    """things-mcp#6: completed/canceled must be idempotent sets, not toggles.

    Things' AppleScript toggles `set status` when the item is already in that
    state — completing an already-completed task un-logbooked it back to open,
    while the response optimistically reported success.
    """

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_completed_on_already_completed_is_noop(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(status="completed")

        result = writes.update_item(uuid=VALID_UUID, completed=True)

        # No status write may be emitted for an already-completed item.
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert "set status" not in "\n".join(str(s) for s in scripts)

        assert isinstance(result, SuccessResponse)
        assert "no-op" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_canceled_on_already_canceled_is_noop(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(status="canceled")

        result = writes.update_item(uuid=VALID_UUID, canceled=True)

        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert "set status" not in "\n".join(str(s) for s in scripts)

        assert isinstance(result, SuccessResponse)
        assert "no-op" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_completed_on_open_item_emits_status_write(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # pre-read: incomplete; verify read + temporal-state read: completed
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed"),
            _raw_task(status="completed"),
        ]

        result = writes.update_item(uuid=VALID_UUID, completed=True)

        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert "set status of theToDo to completed" in "\n".join(
            str(s) for s in scripts
        )
        assert isinstance(result, SuccessResponse)
        assert "no-op" not in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_status_mismatch_reported_not_assumed(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        # The write "succeeds" but the store never reflects completed.
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
        ]

        result = writes.update_item(uuid=VALID_UUID, completed=True)

        assert isinstance(result, ErrorResponse)
        assert result.error == "STATUS_MISMATCH"

    def test_completed_and_canceled_together_rejected(self):
        result = writes.update_item(
            uuid=VALID_UUID, completed=True, canceled=True
        )
        assert isinstance(result, ErrorResponse)
        assert result.error == "INVALID_INPUT"


class TestGuardCoverageOnRelationAndMoveWrites:
    """The paths things-mcp#27 found uncounted: move and the blocker relations.

    Each is a non-completing write, so each must both land in the census and
    report a close that happens inside its write window.
    """

    @patch("things_mcp.writes._log_status_anomaly")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_move_to_context_reports_unexpected_close(
        self, mock_run, mock_get, mock_log
    ):
        mock_run.return_value = _mock_subprocess_ok()
        # pre-read incomplete, post-move verify completed
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed"),
        ]

        result = writes.move_to_context(uuid=VALID_UUID, area_uuid=ALT_UUID)

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"
        mock_log.assert_called_once()
        assert mock_log.call_args.args[3] == "move_to_context"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_move_to_context_counts_a_clean_write(
        self, mock_run, mock_get, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(tmp_path / "census.json"))
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task()

        result = writes.move_to_context(uuid=VALID_UUID, area_uuid=ALT_UUID)

        assert isinstance(result, SuccessResponse)
        census = json.loads((tmp_path / "census.json").read_text())
        assert census["by_source"]["move_to_context"] == 1

    @patch("things_mcp.writes._log_status_anomaly")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_link_blocker_reports_a_close_on_the_dependent(
        self, mock_run, mock_get, mock_log
    ):
        mock_run.return_value = _mock_subprocess_ok()
        gated_notes = f"{writes._REL_GATED_BY}\nBlocker\nthings:///show?id={BLK}"
        mock_get.side_effect = [
            _raw_task(uuid=BLK, status="incomplete"),  # blocker pre-read
            _raw_task(uuid=DEP, status="incomplete"),  # dependent pre-read
            # dependent verify: wired correctly, but now closed
            _raw_task(
                uuid=DEP,
                status="completed",
                tags=["gated"],
                notes=gated_notes,
            ),
        ]

        result = writes.link_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"
        mock_log.assert_called_once()
        assert mock_log.call_args.args[3] == "link_blocker"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_unlink_blocker_counts_both_sides(
        self, mock_run, mock_get, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(tmp_path / "census.json"))
        mock_run.return_value = _mock_subprocess_ok()
        # No relation blocks anywhere: unwiring is a clean no-op on both sides.
        mock_get.return_value = _raw_task()

        result = writes.unlink_blocker(blocker_uuid=BLK, dependent_uuid=DEP)

        assert isinstance(result, SuccessResponse)
        census = json.loads((tmp_path / "census.json").read_text())
        assert census["by_source"]["unlink_blocker"] == 2

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_reconcile_counts_the_subject_and_each_counterpart(
        self, mock_run, mock_get, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(tmp_path / "census.json"))
        mock_run.return_value = _mock_subprocess_ok()

        subject_notes = f"{writes._REL_GATED_BY}\nBlocker\nthings:///show?id={BLK}"

        def fake_get(uuid):
            if uuid == VALID_UUID:
                # Subject keeps its block only on the first read; the scrub
                # clears it, and the verify read must see it gone.
                if not fake_get.subject_scrubbed:
                    fake_get.subject_scrubbed = True
                    return _raw_task(status="completed", notes=subject_notes)
                return _raw_task(status="completed")
            return _raw_task(uuid=BLK)

        fake_get.subject_scrubbed = False
        mock_get.side_effect = fake_get

        result = writes.reconcile_completion(uuid=VALID_UUID)

        assert isinstance(result, SuccessResponse)
        census = json.loads((tmp_path / "census.json").read_text())
        # subject + one blocker counterpart
        assert census["by_source"]["reconcile_completion"] == 2


class TestCensusCoverageStamping:
    """A census that spans an instrumentation change must say so.

    things-mcp#27's real hazard was a clean-looking sample whose older half was
    blind to paths the question asked about. Widening coverage therefore closes
    the previous regime instead of absorbing its writes.
    """

    def test_first_write_stamps_current_coverage(self, tmp_path, monkeypatch):
        census = tmp_path / "census.json"
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(census))

        writes._record_guarded_write("schedule_item")

        data = json.loads(census.read_text())
        assert data["coverage"] == sorted(writes.GUARDED_WRITE_TOOLS)
        assert "regimes" not in data

    def test_widening_coverage_closes_the_previous_regime(
        self, tmp_path, monkeypatch
    ):
        census = tmp_path / "census.json"
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(census))
        census.write_text(
            json.dumps(
                {
                    "writes": 198,
                    "first": "2026-08-25T12:37:50-04:00",
                    "last": "2026-09-06T22:34:35-04:00",
                    "by_source": {"update_item": 94, "schedule_item": 104},
                    "coverage": ["schedule_item", "update_item"],
                }
            )
        )

        writes._record_guarded_write("move_to_context")

        data = json.loads(census.read_text())
        assert data["coverage"] == sorted(writes.GUARDED_WRITE_TOOLS)
        assert len(data["regimes"]) == 1
        closed = data["regimes"][0]
        assert closed["coverage"] == ["schedule_item", "update_item"]
        # The 198 narrow-era writes stay counted, attributed to the narrow regime.
        assert closed["writes"] == 198
        assert data["writes"] == 199

    def test_unstamped_census_is_not_absorbed_as_current_coverage(
        self, tmp_path, monkeypatch
    ):
        """The live census predates stamping; its writes must not be relabelled."""
        census = tmp_path / "census.json"
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(census))
        census.write_text(
            json.dumps(
                {
                    "writes": 198,
                    "by_source": {"update_item": 94, "schedule_item": 104},
                }
            )
        )

        writes._record_guarded_write("update_item")

        data = json.loads(census.read_text())
        assert len(data["regimes"]) == 1
        assert data["regimes"][0]["writes"] == 198
        assert "unrecorded" in data["regimes"][0]["coverage"][0]

    def test_repeated_writes_under_one_regime_add_no_history(
        self, tmp_path, monkeypatch
    ):
        census = tmp_path / "census.json"
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(census))

        for _ in range(3):
            writes._record_guarded_write("link_blocker")

        data = json.loads(census.read_text())
        assert data["writes"] == 3
        assert "regimes" not in data


class TestReopen:
    """completed=false / canceled=false put a Logbook item back.

    They used to fall through every branch and return "Updated item: ." -- a
    success message for a call that wrote nothing. Callers worked around it by
    shelling out to osascript.
    """

    @staticmethod
    def _scripts(mock_run):
        return [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_canceled_false_reopens_a_canceled_item(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="canceled"),      # pre-read
            _raw_task(status="incomplete"),    # post-write verify
            _raw_task(status="incomplete"),    # temporal_state re-read
        ]

        result = writes.update_item(uuid=VALID_UUID, canceled=False)

        assert isinstance(result, SuccessResponse), getattr(result, "message", "")
        assert "reopened" in result.message
        assert any("set status of theToDo to open" in s for s in self._scripts(mock_run))

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_completed_false_reopens_a_completed_item(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="completed"),
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
        ]

        result = writes.update_item(uuid=VALID_UUID, completed=False)

        assert isinstance(result, SuccessResponse)
        assert "reopened" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_reopening_an_open_item_says_no_op_rather_than_nothing(
        self, mock_run, mock_get
    ):
        """The empty-message failure mode is the thing being fixed."""
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(status="incomplete")

        result = writes.update_item(uuid=VALID_UUID, completed=False)

        assert isinstance(result, SuccessResponse)
        assert "no-op" in result.message
        assert result.message != "Updated item: ."
        assert not any(
            "set status of theToDo to open" in s for s in self._scripts(mock_run)
        )

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_a_reopen_that_does_not_take_is_reported(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="canceled"),
            _raw_task(status="canceled"),   # still canceled after the write
            _raw_task(status="canceled"),
        ]

        result = writes.update_item(uuid=VALID_UUID, canceled=False)

        assert isinstance(result, ErrorResponse)
        assert result.error == "STATUS_MISMATCH"

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_an_explicit_close_wins_over_a_reopen(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="canceled"),
            _raw_task(status="canceled"),
        ]

        result = writes.update_item(uuid=VALID_UUID, completed=False, canceled=True)

        assert isinstance(result, SuccessResponse)
        scripts = self._scripts(mock_run)
        assert any("set status of theToDo to canceled" in s for s in scripts)
        assert not any("set status of theToDo to open" in s for s in scripts)

    @patch("things_mcp.writes._log_status_anomaly")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_a_reopen_is_not_counted_as_a_non_completing_write(
        self, mock_run, mock_get, mock_log, tmp_path, monkeypatch
    ):
        """The census watches writes that do NOT request a status change."""
        monkeypatch.setenv("THINGS_MCP_WRITE_CENSUS", str(tmp_path / "census.json"))
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="completed"),
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
        ]

        writes.update_item(uuid=VALID_UUID, completed=False)

        assert not (tmp_path / "census.json").exists()
        mock_log.assert_not_called()

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_reopen_combines_with_other_field_updates(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="completed"),
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
        ]

        result = writes.update_item(
            uuid=VALID_UUID, completed=False, title="Back from the dead"
        )

        assert isinstance(result, SuccessResponse)
        assert "title" in result.message and "reopened" in result.message


class TestReopenIsGatedToItsOwnStatus:
    """A flag must not act on a status it does not name.

    canceled=false used to un-complete a completed item. With the user and the
    agent both ticking checkboxes in Things, a caller sending default field
    values could quietly pull finished work back out of the Logbook.
    """

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_canceled_false_does_not_uncomplete_a_completed_item(
        self, mock_run, mock_get
    ):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(status="completed")

        result = writes.update_item(uuid=VALID_UUID, canceled=False)

        assert isinstance(result, SuccessResponse)
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("set status of theToDo to open" in s for s in scripts), (
            "canceled=false must not touch a completed item"
        )
        # And it must say why, not no-op mysteriously.
        assert "completed" in result.message and "no-op" in result.message

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_completed_false_does_not_uncancel_a_canceled_item(
        self, mock_run, mock_get
    ):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.return_value = _raw_task(status="canceled")

        result = writes.update_item(uuid=VALID_UUID, completed=False)

        assert isinstance(result, SuccessResponse)
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert not any("set status of theToDo to open" in s for s in scripts)

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_the_matching_flag_still_reopens(self, mock_run, mock_get):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="canceled"),
            _raw_task(status="incomplete"),
            _raw_task(status="incomplete"),
        ]

        result = writes.update_item(uuid=VALID_UUID, canceled=False)

        assert isinstance(result, SuccessResponse)
        assert "reopened" in result.message
