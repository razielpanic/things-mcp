"""Tests for the list derivation logic.

This is the most critical module in the server. Every case in the truth table
must be covered, because getting derivation wrong means the consumer (e.g. the
Office Manager) will make decisions based on incorrect list placement.
"""

from datetime import date, timedelta

import pytest

from things_mcp.derivation import derive_list
from things_mcp.models import DerivedList, ItemStatus, StartFlag


# Fixed reference date for deterministic tests
TODAY = date(2026, 3, 30)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
NEXT_WEEK = TODAY + timedelta(days=7)
LAST_WEEK = TODAY - timedelta(days=7)


class TestInboxDerivation:
    """Inbox items stay in Inbox regardless of start_date."""

    def test_inbox_no_date(self):
        assert derive_list("Inbox", None, TODAY) == DerivedList.INBOX

    def test_inbox_with_past_date(self):
        """Even with a start_date in the past, Inbox stays Inbox."""
        assert derive_list("Inbox", YESTERDAY, TODAY) == DerivedList.INBOX

    def test_inbox_with_today_date(self):
        assert derive_list("Inbox", TODAY, TODAY) == DerivedList.INBOX

    def test_inbox_with_future_date(self):
        assert derive_list("Inbox", TOMORROW, TODAY) == DerivedList.INBOX

    def test_inbox_enum(self):
        """Works with StartFlag enum too."""
        assert derive_list(StartFlag.INBOX, None, TODAY) == DerivedList.INBOX


class TestTodayDerivation:
    """Items with start_date <= today are in Today."""

    def test_anytime_with_today_date(self):
        result = derive_list("Anytime", TODAY, TODAY)
        assert result == DerivedList.TODAY

    def test_anytime_with_past_date(self):
        result = derive_list("Anytime", YESTERDAY, TODAY)
        assert result == DerivedList.TODAY

    def test_anytime_with_last_week(self):
        result = derive_list("Anytime", LAST_WEEK, TODAY)
        assert result == DerivedList.TODAY

    def test_someday_with_today_date(self):
        """Someday flag + today's start_date = Today, not Someday."""
        result = derive_list("Someday", TODAY, TODAY)
        assert result == DerivedList.TODAY

    def test_someday_with_past_date(self):
        """Someday flag + past start_date = Today, not Someday."""
        result = derive_list("Someday", LAST_WEEK, TODAY)
        assert result == DerivedList.TODAY


class TestUpcomingDerivation:
    """Items with start_date > today are in Upcoming."""

    def test_anytime_with_tomorrow(self):
        result = derive_list("Anytime", TOMORROW, TODAY)
        assert result == DerivedList.UPCOMING

    def test_anytime_with_next_week(self):
        result = derive_list("Anytime", NEXT_WEEK, TODAY)
        assert result == DerivedList.UPCOMING

    def test_someday_with_future_date(self):
        """Someday flag + future start_date = Upcoming, not Someday."""
        result = derive_list("Someday", TOMORROW, TODAY)
        assert result == DerivedList.UPCOMING


class TestAnytimeDerivation:
    """Items with start=Anytime and no start_date are in Anytime."""

    def test_anytime_no_date(self):
        result = derive_list("Anytime", None, TODAY)
        assert result == DerivedList.ANYTIME

    def test_anytime_enum_no_date(self):
        result = derive_list(StartFlag.ANYTIME, None, TODAY)
        assert result == DerivedList.ANYTIME


class TestSomedayDerivation:
    """Items with start=Someday and no start_date are in Someday."""

    def test_someday_no_date(self):
        result = derive_list("Someday", None, TODAY)
        assert result == DerivedList.SOMEDAY

    def test_someday_enum_no_date(self):
        result = derive_list(StartFlag.SOMEDAY, None, TODAY)
        assert result == DerivedList.SOMEDAY


class TestLogbookDerivation:
    """Completed or canceled items are always in Logbook."""

    def test_completed_overrides_today(self):
        result = derive_list("Anytime", TODAY, TODAY, status="completed")
        assert result == DerivedList.LOGBOOK

    def test_completed_overrides_anytime(self):
        result = derive_list("Anytime", None, TODAY, status="completed")
        assert result == DerivedList.LOGBOOK

    def test_canceled_overrides_someday(self):
        result = derive_list("Someday", None, TODAY, status="canceled")
        assert result == DerivedList.LOGBOOK

    def test_completed_overrides_inbox(self):
        result = derive_list("Inbox", None, TODAY, status="completed")
        assert result == DerivedList.LOGBOOK

    def test_completed_with_future_date(self):
        """Completed + future start_date = Logbook, not Upcoming."""
        result = derive_list("Anytime", TOMORROW, TODAY, status="completed")
        assert result == DerivedList.LOGBOOK

    def test_status_enum(self):
        result = derive_list("Anytime", None, TODAY, status=ItemStatus.COMPLETED)
        assert result == DerivedList.LOGBOOK


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_defaults_to_real_today(self):
        """When today is not specified, uses date.today()."""
        # This test just verifies no exception; actual list depends on real date
        result = derive_list("Anytime", None)
        assert result in DerivedList

    def test_start_date_exactly_today(self):
        """Boundary: start_date == today should be Today, not Upcoming."""
        result = derive_list("Anytime", TODAY, TODAY)
        assert result == DerivedList.TODAY

    def test_start_date_one_day_future(self):
        """Boundary: start_date == today+1 should be Upcoming, not Today."""
        result = derive_list("Anytime", TODAY + timedelta(days=1), TODAY)
        assert result == DerivedList.UPCOMING

    def test_very_old_start_date(self):
        """An item scheduled long ago is still in Today."""
        ancient = date(2020, 1, 1)
        result = derive_list("Anytime", ancient, TODAY)
        assert result == DerivedList.TODAY
