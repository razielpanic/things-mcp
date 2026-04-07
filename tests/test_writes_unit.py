"""Unit tests for writes.py pure functions.

Tests _validate_uuid and _applescript_date_block without any subprocess
or AppleScript invocations -- these are pure validation/string functions.
"""

from __future__ import annotations

from datetime import date

import pytest

from things_mcp.writes import _applescript_date_block, _validate_uuid


class TestValidateUuid:
    """Tests for _validate_uuid: 22-char base62 validation."""

    def test_valid_22_char_base62(self):
        result = _validate_uuid("A" * 22)
        assert result == "A" * 22

    def test_valid_mixed_alphanumeric(self):
        uuid = "AbCdEfGh1234567890XyZz"
        assert _validate_uuid(uuid) == uuid

    def test_too_short(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 21)

    def test_too_long(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 23)

    def test_rfc_uuid_with_dashes(self):
        """RFC-style UUIDs with dashes are not valid Things UUIDs."""
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("550e8400-e29b-41d4-a716")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("")

    def test_special_chars(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 20 + "!@")

    def test_spaces(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 20 + "  ")

    def test_underscores(self):
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 20 + "__")


class TestApplescriptDateBlock:
    """Tests for _applescript_date_block: date -> AppleScript code block."""

    def test_valid_date_produces_script(self):
        result = _applescript_date_block("theDate", date(2026, 4, 6))
        assert "set theDate to current date" in result
        assert "set time of theDate to 0" in result

    def test_year_value_matches(self):
        result = _applescript_date_block("d", date(2026, 4, 6))
        assert "set year of d to 2026" in result

    def test_month_value_matches(self):
        result = _applescript_date_block("d", date(2026, 4, 6))
        assert "set month of d to 4" in result

    def test_day_value_matches(self):
        result = _applescript_date_block("d", date(2026, 4, 6))
        assert "set day of d to 6" in result

    def test_variable_name_used(self):
        result = _applescript_date_block("myVar", date(2026, 1, 1))
        assert "set myVar to current date" in result
        assert "set year of myVar to 2026" in result
        assert "set month of myVar to 1" in result
        assert "set day of myVar to 1" in result
        assert "set time of myVar to 0" in result

    def test_different_date(self):
        result = _applescript_date_block("x", date(2030, 12, 31))
        assert "set year of x to 2030" in result
        assert "set month of x to 12" in result
        assert "set day of x to 31" in result

    def test_result_is_multiline(self):
        result = _applescript_date_block("d", date(2026, 1, 1))
        lines = result.strip().split("\n")
        assert len(lines) == 5
