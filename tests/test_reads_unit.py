"""Unit tests for reads.py pure functions.

Tests _parse_date, _parse_datetime, and _item_from_dict without any
database access -- these are pure dict-to-model transformations.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from things_mcp.models import ChecklistItem, DerivedList, StartFlag, ThingsItem
from things_mcp.reads import _item_from_dict, _parse_date, _parse_datetime


class TestParseDate:
    """Tests for _parse_date: ISO date string -> date | None."""

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_valid_iso_date(self):
        assert _parse_date("2026-04-06") == date(2026, 4, 6)

    def test_date_with_time_component(self):
        """things.py sometimes returns datetime strings; we take first 10 chars."""
        assert _parse_date("2026-04-06 14:30:00") == date(2026, 4, 6)

    def test_date_with_iso_datetime(self):
        assert _parse_date("2026-04-06T14:30:00") == date(2026, 4, 6)


class TestParseDatetime:
    """Tests for _parse_datetime: ISO datetime string -> datetime | None."""

    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_valid_iso_datetime(self):
        result = _parse_datetime("2026-04-06 14:30:00")
        assert result == datetime(2026, 4, 6, 14, 30, 0)

    def test_iso_datetime_with_t_separator(self):
        result = _parse_datetime("2026-04-06T14:30:00")
        assert result == datetime(2026, 4, 6, 14, 30, 0)


class TestItemFromDict:
    """Tests for _item_from_dict: raw things.py dict -> ThingsItem."""

    def test_valid_full_dict(self, sample_raw_dict: dict):
        item = _item_from_dict(sample_raw_dict)
        assert isinstance(item, ThingsItem)
        assert item.uuid == "A" * 22
        assert item.title == "Test task"
        assert item.type == "to-do"
        assert item.temporal_state.derived_list == "Anytime"

    def test_minimal_required_fields(self):
        """Only uuid, title, type are truly required by direct indexing."""
        raw = {"uuid": "B" * 22, "title": "Minimal", "type": "to-do"}
        item = _item_from_dict(raw)
        assert item.uuid == "B" * 22
        assert item.title == "Minimal"
        assert item.temporal_state.start == "Anytime"

    def test_missing_uuid_raises(self):
        raw = {"title": "No UUID", "type": "to-do"}
        with pytest.raises(ValueError, match="uuid"):
            _item_from_dict(raw)

    def test_missing_title_raises(self):
        raw = {"uuid": "C" * 22, "type": "to-do"}
        with pytest.raises(ValueError, match="title"):
            _item_from_dict(raw)

    def test_missing_type_raises(self):
        raw = {"uuid": "D" * 22, "title": "No type"}
        with pytest.raises(ValueError, match="type"):
            _item_from_dict(raw)

    def test_truncate_notes_true(self, sample_raw_dict: dict):
        sample_raw_dict["notes"] = "x" * 300
        item = _item_from_dict(sample_raw_dict, truncate_notes=True)
        assert len(item.notes) == 200

    def test_truncate_notes_false(self, sample_raw_dict: dict):
        sample_raw_dict["notes"] = "x" * 300
        item = _item_from_dict(sample_raw_dict, truncate_notes=False)
        assert len(item.notes) == 300

    def test_short_notes_not_truncated(self, sample_raw_dict: dict):
        sample_raw_dict["notes"] = "short"
        item = _item_from_dict(sample_raw_dict, truncate_notes=True)
        assert item.notes == "short"

    def test_checklist_mapping(self, sample_raw_dict: dict):
        sample_raw_dict["checklist"] = [
            {"title": "Step 1", "status": "completed"},
            {"title": "Step 2", "status": "incomplete"},
        ]
        item = _item_from_dict(sample_raw_dict)
        assert len(item.checklist) == 2
        assert item.checklist[0] == ChecklistItem(title="Step 1", completed=True)
        assert item.checklist[1] == ChecklistItem(title="Step 2", completed=False)

    def test_checklist_non_list_ignored(self, sample_raw_dict: dict):
        """If checklist is not a list (e.g. boolean 1 from things.py), treat as empty."""
        sample_raw_dict["checklist"] = 1
        item = _item_from_dict(sample_raw_dict)
        assert item.checklist == []

    def test_start_defaults_to_anytime(self):
        raw = {"uuid": "E" * 22, "title": "No start", "type": "to-do"}
        item = _item_from_dict(raw)
        assert item.temporal_state.start == "Anytime"

    def test_start_inbox(self, sample_raw_dict: dict):
        sample_raw_dict["start"] = "Inbox"
        item = _item_from_dict(sample_raw_dict)
        assert item.temporal_state.start == "Inbox"
        assert item.temporal_state.derived_list == "Inbox"

    def test_start_someday(self, sample_raw_dict: dict):
        sample_raw_dict["start"] = "Someday"
        item = _item_from_dict(sample_raw_dict)
        assert item.temporal_state.start == "Someday"
        assert item.temporal_state.derived_list == "Someday"

    def test_evening_flag_true(self, sample_raw_dict: dict):
        sample_raw_dict["evening"] = 1
        item = _item_from_dict(sample_raw_dict)
        assert item.temporal_state.evening is True

    def test_evening_flag_false(self, sample_raw_dict: dict):
        sample_raw_dict["evening"] = 0
        item = _item_from_dict(sample_raw_dict)
        assert item.temporal_state.evening is False

    def test_evening_flag_missing(self):
        raw = {"uuid": "F" * 22, "title": "No evening", "type": "to-do"}
        item = _item_from_dict(raw)
        assert item.temporal_state.evening is False

    def test_project_type(self, sample_raw_dict: dict):
        sample_raw_dict["type"] = "project"
        item = _item_from_dict(sample_raw_dict)
        assert item.type == "project"

    def test_context_fields(self, sample_raw_dict: dict):
        sample_raw_dict["project"] = "P" * 22
        sample_raw_dict["project_title"] = "My Project"
        sample_raw_dict["area"] = "A" * 22
        sample_raw_dict["area_title"] = "My Area"
        sample_raw_dict["heading_title"] = "My Heading"
        item = _item_from_dict(sample_raw_dict)
        assert item.context.project_uuid == "P" * 22
        assert item.context.project_title == "My Project"
        assert item.context.area_uuid == "A" * 22
        assert item.context.area_title == "My Area"
        assert item.context.heading_title == "My Heading"

    def test_deadline_parsed(self, sample_raw_dict: dict):
        sample_raw_dict["deadline"] = "2026-12-31"
        item = _item_from_dict(sample_raw_dict)
        assert item.deadline == date(2026, 12, 31)

    def test_timestamps_parsed(self, sample_raw_dict: dict):
        sample_raw_dict["created"] = "2026-04-06 10:00:00"
        sample_raw_dict["modified"] = "2026-04-06 11:00:00"
        sample_raw_dict["stop_date"] = "2026-04-06 12:00:00"
        item = _item_from_dict(sample_raw_dict)
        assert item.created == datetime(2026, 4, 6, 10, 0, 0)
        assert item.modified == datetime(2026, 4, 6, 11, 0, 0)
        assert item.completed_date == datetime(2026, 4, 6, 12, 0, 0)
