"""Checklist rows in list views and search, and the logbook period (#29, #24).

Integration tests against the fixture database via the things_db fixture.
"""

from __future__ import annotations

from datetime import date

import pytest

from things_mcp import checklist, reads


class TestChecklistReader:
    def test_batch_returns_rows_in_index_order(self, things_db):
        rows = checklist.checklists_for(["TodayTask00000000000001", "ChecklistTask0000000001"])
        assert [r["title"] for r in rows["TodayTask00000000000001"]] == ["Check tests", "Review docs"]
        assert [r["status"] for r in rows["TodayTask00000000000001"]] == ["incomplete", "completed"]
        assert [r["status"] for r in rows["ChecklistTask0000000001"]] == ["incomplete", "canceled"]

    def test_uuid_without_rows_is_absent(self, things_db):
        rows = checklist.checklists_for(["InboxTask00000000000001"])
        assert rows == {}

    def test_empty_input(self, things_db):
        assert checklist.checklists_for([]) == {}

    def test_parents_matching_is_case_insensitive_substring(self, things_db):
        assert checklist.parents_matching("PAD ADAPTER") == ["ChecklistTask0000000001"]
        assert checklist.parents_matching("zzz_nothing_zzz") == []

    def test_parents_matching_escapes_like_wildcards(self, things_db):
        # A literal % or _ in the query must not turn into a wildcard.
        assert checklist.parents_matching("%") == []
        assert checklist.parents_matching("_") == []

    def test_unreadable_database_is_empty_not_an_error(self, monkeypatch):
        monkeypatch.setenv("THINGSDB", "/nonexistent/things.sqlite")
        assert checklist.checklists_for(["TodayTask00000000000001"]) == {}
        assert checklist.parents_matching("pad") == []


class TestListViewsCarryChecklist:
    """Regression for #29: only get_item used to carry checklist rows."""

    def test_today_has_rows(self, things_db):
        item = next(i for i in reads.get_today() if i.uuid == "TodayTask00000000000001")
        assert [c.title for c in item.checklist] == ["Check tests", "Review docs"]
        assert [c.completed for c in item.checklist] == [False, True]

    def test_anytime_has_rows(self, things_db):
        item = next(i for i in reads.get_anytime() if i.uuid == "ChecklistTask0000000001")
        assert [c.title for c in item.checklist] == [
            "If the drummer comes: test the pad adapter",
            "Ring out the mains",
        ]

    def test_list_view_matches_get_item(self, things_db):
        listed = next(i for i in reads.get_today() if i.uuid == "TodayTask00000000000001")
        single = reads.get_item(uuid="TodayTask00000000000001")
        assert listed.checklist == single.checklist

    def test_item_without_checklist_stays_empty(self, things_db):
        item = next(i for i in reads.get_inbox() if i.uuid == "InboxTask00000000000001")
        assert item.checklist == []


class TestSearchChecklistText:
    """Regression for #29: search must match checklist-row titles."""

    def test_search_finds_parent_by_row_text(self, things_db):
        items = reads.search(query="pad adapter")
        assert [i.uuid for i in items] == ["ChecklistTask0000000001"]
        # And the hit carries its rows, so the caller can see what matched.
        assert any("pad adapter" in c.title for c in items[0].checklist)

    def test_search_does_not_duplicate_title_hits(self, things_db):
        # "Review" matches TodayTask's title and one of its checklist rows.
        items = reads.search(query="Review")
        uuids = [i.uuid for i in items]
        assert uuids.count("TodayTask00000000000001") == 1

    def test_search_filters_apply_to_checklist_hits(self, things_db):
        # The parent is a loose to-do; constraining to the project excludes it.
        assert reads.search(query="pad adapter", project_uuid="ProjectTask000000000001") == []

    def test_search_limit_applies_across_both_sources(self, things_db):
        assert len(reads.search(query="e", limit=1)) == 1


class TestLogbookPeriod:
    """Regression for #24: period was filtering by creation date."""

    def test_1d_includes_item_completed_today_created_last_month(self, things_db):
        titles = [i.title for i in reads.get_logbook(period="1d")]
        assert "Return the library books" in titles

    def test_0d_is_today_only(self, things_db):
        titles = [i.title for i in reads.get_logbook(period="0d")]
        assert "Return the library books" in titles
        assert "Ship v1.0" in titles

    def test_cutoff_arithmetic(self):
        today = date(2026, 8, 10)
        assert reads._period_cutoff("7d", today=today) == date(2026, 8, 3)
        assert reads._period_cutoff("1d", today=today) == date(2026, 8, 9)
        assert reads._period_cutoff("0d", today=today) == today
        assert reads._period_cutoff("2w", today=today) == date(2026, 7, 27)
        assert reads._period_cutoff("1y", today=today) == date(2025, 8, 10)
        assert reads._period_cutoff(" 3D ", today=today) == date(2026, 8, 7)

    @pytest.mark.parametrize("bad", ["", "7", "d", "7m", "seven days", "-1d"])
    def test_invalid_period_is_a_value_error(self, bad):
        with pytest.raises(ValueError):
            reads._period_cutoff(bad, today=date(2026, 8, 10))
