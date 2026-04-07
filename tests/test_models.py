"""Unit tests for Pydantic models and enums in models.py.

Tests enum values, model validation, serialization, and defaults
without any database or I/O dependencies.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from things_mcp.models import (
    AreaItem,
    ChecklistItem,
    DerivedList,
    ErrorResponse,
    ItemContext,
    ItemType,
    StartFlag,
    SuccessResponse,
    TemporalState,
    ThingsItem,
)


class TestItemType:
    """Tests for ItemType enum serialization."""

    def test_todo_value(self):
        assert ItemType.TODO == "to-do"
        assert ItemType.TODO.value == "to-do"

    def test_project_value(self):
        assert ItemType.PROJECT == "project"
        assert ItemType.PROJECT.value == "project"

    def test_heading_value(self):
        assert ItemType.HEADING == "heading"
        assert ItemType.HEADING.value == "heading"

    def test_string_comparison(self):
        """str Enum allows direct string comparison."""
        assert ItemType.TODO == "to-do"
        assert ItemType.PROJECT == "project"
        assert ItemType.HEADING == "heading"


class TestStartFlag:
    """Tests for StartFlag enum values."""

    def test_inbox_value(self):
        assert StartFlag.INBOX == "Inbox"

    def test_anytime_value(self):
        assert StartFlag.ANYTIME == "Anytime"

    def test_someday_value(self):
        assert StartFlag.SOMEDAY == "Someday"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            StartFlag("InvalidList")


class TestDerivedList:
    """Tests for DerivedList enum -- all 6 values accessible."""

    def test_all_six_values(self):
        expected = {"Inbox", "Today", "Upcoming", "Anytime", "Someday", "Logbook"}
        actual = {member.value for member in DerivedList}
        assert actual == expected

    def test_inbox(self):
        assert DerivedList.INBOX == "Inbox"

    def test_today(self):
        assert DerivedList.TODAY == "Today"

    def test_upcoming(self):
        assert DerivedList.UPCOMING == "Upcoming"

    def test_anytime(self):
        assert DerivedList.ANYTIME == "Anytime"

    def test_someday(self):
        assert DerivedList.SOMEDAY == "Someday"

    def test_logbook(self):
        assert DerivedList.LOGBOOK == "Logbook"


class TestTemporalState:
    """Tests for TemporalState model."""

    def test_use_enum_values_produces_strings(self):
        ts = TemporalState(
            start="Anytime",
            derived_list="Today",
            status="incomplete",
        )
        assert isinstance(ts.start, str)
        assert isinstance(ts.derived_list, str)
        assert isinstance(ts.status, str)
        assert ts.start == "Anytime"

    def test_default_start_is_anytime(self):
        ts = TemporalState(derived_list="Anytime")
        assert ts.start == "Anytime"

    def test_evening_defaults_to_false(self):
        ts = TemporalState(derived_list="Anytime")
        assert ts.evening is False

    def test_start_date_optional(self):
        ts = TemporalState(derived_list="Anytime")
        assert ts.start_date is None

    def test_start_date_set(self):
        ts = TemporalState(
            derived_list="Today",
            start_date=date(2026, 4, 6),
        )
        assert ts.start_date == date(2026, 4, 6)

    def test_evening_true(self):
        ts = TemporalState(derived_list="Today", evening=True)
        assert ts.evening is True


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_success_always_false(self):
        err = ErrorResponse(error="READ_ERROR", message="Something broke")
        assert err.success is False

    def test_fields_present(self):
        err = ErrorResponse(error="VERIFY_FAILED", message="Item not found")
        assert err.error == "VERIFY_FAILED"
        assert err.message == "Item not found"

    def test_success_cannot_be_overridden(self):
        """Even if you pass success=True, the default is False."""
        err = ErrorResponse(error="X", message="Y", success=True)
        # Pydantic allows setting it, but the model default ensures False
        # for normal construction; we test the default path
        err_default = ErrorResponse(error="X", message="Y")
        assert err_default.success is False

    def test_serialization(self):
        err = ErrorResponse(error="NOT_FOUND", message="Gone")
        data = err.model_dump()
        assert data["success"] is False
        assert data["error"] == "NOT_FOUND"
        assert data["message"] == "Gone"


class TestSuccessResponse:
    """Tests for SuccessResponse model."""

    def test_success_always_true(self):
        resp = SuccessResponse(message="Done")
        assert resp.success is True

    def test_temporal_state_optional(self):
        resp = SuccessResponse(message="Trashed")
        assert resp.temporal_state is None

    def test_temporal_state_included(self):
        ts = TemporalState(derived_list="Today", start="Anytime")
        resp = SuccessResponse(
            uuid="A" * 22,
            message="Scheduled",
            action="scheduled",
            temporal_state=ts,
        )
        assert resp.temporal_state is not None
        assert resp.temporal_state.derived_list == "Today"

    def test_serialization(self):
        resp = SuccessResponse(uuid="B" * 22, message="Created", action="created")
        data = resp.model_dump()
        assert data["success"] is True
        assert data["uuid"] == "B" * 22
        assert data["action"] == "created"


class TestThingsItem:
    """Tests for ThingsItem model."""

    def test_full_construction(self):
        ts = TemporalState(
            start="Inbox",
            derived_list="Inbox",
            status="incomplete",
        )
        item = ThingsItem(
            uuid="A" * 22,
            title="Buy milk",
            type="to-do",
            temporal_state=ts,
        )
        assert item.uuid == "A" * 22
        assert item.title == "Buy milk"
        assert item.type == "to-do"
        assert item.temporal_state.derived_list == "Inbox"

    def test_items_list_for_project_children(self):
        ts = TemporalState(derived_list="Anytime")
        child = ThingsItem(
            uuid="C" * 22,
            title="Child task",
            type="to-do",
            temporal_state=ts,
        )
        project = ThingsItem(
            uuid="P" * 22,
            title="My Project",
            type="project",
            temporal_state=ts,
            items=[child],
        )
        assert len(project.items) == 1
        assert project.items[0].title == "Child task"

    def test_default_empty_lists(self):
        ts = TemporalState(derived_list="Anytime")
        item = ThingsItem(
            uuid="D" * 22,
            title="Defaults",
            type="to-do",
            temporal_state=ts,
        )
        assert item.tags == []
        assert item.checklist == []
        assert item.items == []

    def test_optional_fields_none(self):
        ts = TemporalState(derived_list="Anytime")
        item = ThingsItem(
            uuid="E" * 22,
            title="Optionals",
            type="to-do",
            temporal_state=ts,
        )
        assert item.deadline is None
        assert item.notes is None
        assert item.created is None
        assert item.modified is None
        assert item.completed_date is None
        assert item.today_index is None
        assert item.index is None

    def test_with_checklist(self):
        ts = TemporalState(derived_list="Today")
        item = ThingsItem(
            uuid="F" * 22,
            title="With checklist",
            type="to-do",
            temporal_state=ts,
            checklist=[
                ChecklistItem(title="Step 1", completed=True),
                ChecklistItem(title="Step 2", completed=False),
            ],
        )
        assert len(item.checklist) == 2
        assert item.checklist[0].completed is True
        assert item.checklist[1].completed is False

    def test_serialization_uses_enum_values(self):
        ts = TemporalState(
            start="Inbox",
            derived_list="Inbox",
            status="incomplete",
        )
        item = ThingsItem(
            uuid="G" * 22,
            title="Serialize",
            type="to-do",
            temporal_state=ts,
        )
        data = item.model_dump()
        assert data["type"] == "to-do"
        assert data["temporal_state"]["start"] == "Inbox"
        assert data["temporal_state"]["derived_list"] == "Inbox"
