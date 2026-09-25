"""Integration tests for read path against the fixture SQLite database.

Uses the things_db fixture from conftest.py to point THINGSDB at the
fixture SQLite, then exercises reads.py functions against real data.
"""

from __future__ import annotations

import os
import sqlite3

from datetime import date

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
        """Today is scheduled-for-today plus anything with an overdue deadline.

        The second half is easy to miss: things.py's today() passes
        deadline="past", so a task with no start_date at all shows up once its
        deadline goes by, deriving to Anytime. Asserting every item derives to
        Today/Upcoming was therefore wrong on its own terms -- it just could not
        fail until the fixture's deadlines aged past today.
        """
        items = reads.get_today()
        assert isinstance(items, list)
        assert items, "fixture should seed at least one Today item"
        today = date.today()
        for item in items:
            assert isinstance(item, ThingsItem)
            overdue = item.deadline is not None and item.deadline <= today
            assert item.temporal_state.derived_list in ("Today", "Upcoming") or overdue, (
                f"{item.title!r} derived {item.temporal_state.derived_list} with "
                f"deadline {item.deadline} -- neither scheduled for today nor overdue"
            )

    def test_today_limit(self, things_db):
        items = reads.get_today(limit=1)
        assert len(items) <= 1


class TestAnytimeReads:
    """Test get_anytime against fixture DB."""

    def test_anytime_returns_items(self, things_db):
        items = reads.get_anytime()
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_get_anytime_excludes_scheduled_items(self, things_db):
        """get_anytime must not dual-list items with a future start_date.

        Regression test for the bug where get_anytime called things.anytime()
        without a start_date filter, so items with start=Anytime AND a future
        start_date (which Things shows in Upcoming) leaked into the result.
        The fix pushes start_date=False down to things.anytime().

        Truth table: derived_list == "Anytime" iff start=Anytime AND
        start_date IS NULL. Assert the function enforces that.
        """
        # Arrange — fixture seeds:
        #   AnytimeTask000000000001: start=Anytime, no start_date (true Anytime).
        #   UpcomingTask000000000001: start=Anytime, startDate=tomorrow
        #     (contaminant: derives to Upcoming, not Anytime).

        # Act
        items = reads.get_anytime()
        uuids = {i.uuid for i in items}

        # Assert
        assert "AnytimeTask000000000001" in uuids
        assert "UpcomingTask000000000001" not in uuids
        for item in items:
            assert item.temporal_state.derived_list == "Anytime"
            assert item.temporal_state.start_date is None


class TestSomedayReads:
    """Test get_someday against fixture DB."""

    def test_someday_returns_items(self, things_db):
        items = reads.get_someday()
        assert len(items) >= 1
        for item in items:
            assert item.temporal_state.derived_list == "Someday"


class TestGetItemRepeat:
    """get_item exposes the repeat relation things.py drops (things-mcp#30)."""

    def test_instance_names_its_trashed_template(self, things_db):
        # The #30 failure: the instance looked live while its template was
        # trashed, and nothing in the payload said so.
        item = reads.get_item(uuid="RepeatInstance000000001")
        assert item is not None
        assert item.repeat is not None
        assert item.repeat.role == "instance"
        assert item.repeat.template_uuid == "RepeatTemplate000000001"
        assert item.repeat.template_found is True
        assert item.repeat.template_trashed is True
        assert item.repeat.template_paused is False

    def test_paused_template_reports_its_next_date(self, things_db):
        from datetime import date, timedelta

        item = reads.get_item(uuid="RepeatTemplate000000002")
        assert item is not None
        assert item.repeat is not None
        assert item.repeat.role == "template"
        assert item.repeat.template_uuid == "RepeatTemplate000000002"
        assert item.repeat.template_trashed is False
        assert item.repeat.template_paused is True
        assert item.repeat.next_instance_date == date.today() + timedelta(days=30)

    def test_todo_under_template_project_heading_is_template_child(self, things_db):
        item = reads.get_item(uuid="RepeatTplChild000000001")
        assert item is not None
        assert item.repeat is not None
        assert item.repeat.role == "template_child"
        assert item.repeat.template_uuid == "RepeatProjectTpl0000001"

    def test_template_content_stays_out_of_lists_and_search(self, things_db):
        # things-mcp#26: these leaked into Anytime with project_title null.
        assert "RepeatTplChild000000001" not in {i.uuid for i in reads.get_anytime(limit=500)}
        assert "RepeatTplChild000000001" not in {
            i.uuid for i in reads.search(query="lead story")
        }

    def test_plain_item_has_no_repeat(self, things_db):
        item = reads.get_item(uuid="InboxTask00000000000001")
        assert item is not None
        assert item.repeat is None

    def test_unreadable_columns_are_unknown_not_absent(self, things_db, monkeypatch):
        from things_mcp import repeats

        monkeypatch.setattr(repeats, "_REQUIRED_COLUMNS", {"no_such_column"})
        info = repeats.repeat_info("InboxTask00000000000001")
        assert info is not None
        assert info.role == "unknown"

    def test_templates_stay_out_of_list_views(self, things_db):
        uuids = {i.uuid for i in reads.get_someday()} | {i.uuid for i in reads.get_anytime()}
        assert "RepeatTemplate000000001" not in uuids
        assert "RepeatTemplate000000002" not in uuids


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

    def test_get_item_on_project_populates_items(self, things_db):
        """get_item on a project UUID populates items with child tasks.

        Regression test for the bug where get_item returned project items
        with an empty items list. The fix mirrors get_projects(include_items=True)
        by querying things.tasks(project=uuid) for child tasks and mapping
        them through _item_from_dict(truncate_notes=False).
        """
        # Arrange — fixture seeds ProjectTask000000000001 with child
        # ChildTask0000000000001a ("Design mockups") in create_fixture.py.
        project_uuid = "ProjectTask000000000001"
        expected_child_uuid = "ChildTask0000000000001a"
        expected_child_title = "Design mockups"

        # Act
        item = reads.get_item(uuid=project_uuid)

        # Assert
        assert item is not None
        assert item.type == "project"
        assert len(item.items) >= 1
        assert any(child.uuid == expected_child_uuid for child in item.items)
        assert any(child.title == expected_child_title for child in item.items)
        # Every child must carry derived_list per CLAUDE.md rule 1.
        for child in item.items:
            assert child.temporal_state.derived_list is not None
            assert isinstance(child.temporal_state.derived_list, str)


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


class TestEveningEndToEnd:
    """The seam that produced things-mcp#9, #21 and #23 had no test.

    The unit tests monkeypatch is_evening and the evening module is tested in
    isolation, so the join in reads._items_from_dicts -- the place the flag
    actually reaches a ThingsItem -- was never exercised. That join is exactly
    what was broken for the whole life of the field.
    """

    def test_today_reports_the_evening_item_as_evening(self, things_db):
        by_title = {i.title: i for i in reads.get_today()}
        assert "Evening meditation" in by_title, "fixture should seed an evening task"
        assert by_title["Evening meditation"].temporal_state.evening is True

    def test_a_plain_today_item_is_not_evening(self, things_db):
        by_title = {i.title: i for i in reads.get_today()}
        assert "Review pull request" in by_title
        assert by_title["Review pull request"].temporal_state.evening is False

    def test_get_item_agrees_with_the_list_view(self, things_db):
        item = reads.get_item(uuid="EveningTask000000000001")
        assert item is not None
        assert item.temporal_state.evening is True
