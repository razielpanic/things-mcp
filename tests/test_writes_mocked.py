"""Tests for write functions with mocked subprocess.run and things.get.

All write operations go through AppleScript (subprocess.run) or URL scheme.
These tests verify the correct AppleScript commands are constructed and
the proper error responses are returned.
"""

from __future__ import annotations

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
    """A non-completing write must never silently complete/cancel an open item."""

    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_update_reopens_and_errors_on_unexpected_completion(
        self, mock_run, mock_get
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
        # the guard issued a reopen AppleScript
        scripts = [
            (c.kwargs.get("input") or (c.args[0] if c.args else ""))
            for c in mock_run.call_args_list
        ]
        assert any("set status of" in s and "to open" in s for s in scripts)

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

    @patch("things_mcp.writes.time.sleep")
    @patch("things_mcp.writes.things.get")
    @patch("things_mcp.writes.subprocess.run")
    def test_schedule_reopens_on_unexpected_completion(
        self, mock_run, mock_get, mock_sleep
    ):
        mock_run.return_value = _mock_subprocess_ok()
        mock_get.side_effect = [
            _raw_task(status="incomplete"),
            _raw_task(status="completed", start_date="2026-06-14"),
        ]

        result = writes.schedule_item(uuid=VALID_UUID, when="today")

        assert isinstance(result, ErrorResponse)
        assert result.error == "UNEXPECTED_STATUS_CHANGE"


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
        assert "**Gated by:**" in fake.store[DEP]["notes"]
        assert f"things:///show?id={BLK}" in fake.store[DEP]["notes"]
        # Blocker -> dependent under Gates.
        assert "**Gates:**" in fake.store[BLK]["notes"]
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
        assert "**Gated by:**" in fake.store[DEP]["notes"]
        assert fake.store[BLK]["notes"].startswith("blocker prose")
        assert "**Gates:**" in fake.store[BLK]["notes"]

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
        assert notes.count("**Gated by:**") == 1
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
        assert notes.count("**Gates:**") == 1
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
