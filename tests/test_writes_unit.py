"""Unit tests for writes.py pure functions.

Tests _validate_uuid and _applescript_date_block without any subprocess
or AppleScript invocations -- these are pure validation/string functions.
"""

from __future__ import annotations

from datetime import date

import pytest

from things_mcp.writes import (
    _REL_GATED_BY,
    _applescript_date_block,
    _has_uuid,
    _merge_tag,
    _parse_relation_block,
    _relation_present,
    _render_relation_block,
    _splice_notes,
    _validate_uuid,
)

UUID_A = "A" * 22
UUID_B = "B" * 22
UUID_C = "C" * 22


class TestValidateUuid:
    """Tests for _validate_uuid: 21- or 22-char base62 validation."""

    def test_valid_22_char_base62(self):
        result = _validate_uuid("A" * 22)
        assert result == "A" * 22

    def test_valid_21_char_base62(self):
        # Real Things IDs are sometimes 21 chars when a leading base62 digit is
        # dropped — these are DB-real and must be writable (dev-issue 2026-04-20
        # / upstream #4). Verbatim IDs observed in the wild.
        for uuid in ("ufGzuLaRsMNZDPRPB3yPj", "eCNdD4xfM23J1nBop9ixv"):
            assert len(uuid) == 21
            assert _validate_uuid(uuid) == uuid

    def test_valid_mixed_alphanumeric(self):
        uuid = "AbCdEfGh1234567890XyZz"
        assert _validate_uuid(uuid) == uuid

    def test_too_short(self):
        # 20 chars is below the accepted 21/22 band.
        with pytest.raises(ValueError, match="Invalid UUID format"):
            _validate_uuid("A" * 20)

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
        # 6 lines: current date, day->1 (anti-rollover), year, month, real day, time
        assert len(lines) == 6

    def test_day_neutralized_before_month_to_avoid_rollover(self):
        # Regression for "explicit YYYY-MM-DD scheduled one month late": mutating
        # a date in place rolls the month forward when the current day-of-month
        # exceeds the target month's length. Setting day to 1 first neutralizes
        # this; the real day must be applied AFTER the month.
        result = _applescript_date_block("d", date(2026, 6, 1))
        lines = result.strip().split("\n")
        day1_idx = lines.index("set day of d to 1")
        month_idx = lines.index("set month of d to 6")
        assert day1_idx < month_idx, "day must be neutralized to 1 before month"
        assert month_idx < len(lines) - 1, "real day/time applied after month"

    def test_day_one_target_only_sets_day_once_after_month(self):
        # When the target day IS the 1st, the neutralizer and the real day are
        # both "set day to 1" -- assert the final day assignment follows month.
        result = _applescript_date_block("d", date(2026, 6, 1))
        lines = result.strip().split("\n")
        assert lines.index("set month of d to 6") < (
            len(lines) - 1 - lines[::-1].index("set day of d to 1")
        )


def _entry(title: str, uuid: str) -> str:
    """Render one managed entry (title line + deep-link line) like writes.py."""
    return f"{title}\nthings:///show?id={uuid}"


class TestMergeTag:
    """_merge_tag: add a tag without clobbering existing ones."""

    def test_adds_to_empty(self):
        assert _merge_tag([], "gated") == ["gated"]

    def test_preserves_existing(self):
        assert _merge_tag(["work", "home"], "gated") == ["work", "home", "gated"]

    def test_idempotent_when_present(self):
        assert _merge_tag(["work", "gated"], "gated") == ["work", "gated"]

    def test_does_not_mutate_input(self):
        original = ["work"]
        _merge_tag(original, "gated")
        assert original == ["work"]


class TestRenderRelationBlock:
    """_render_relation_block: label + a title line / deep-link line per entry."""

    def test_empty_entries_render_nothing(self):
        assert _render_relation_block(_REL_GATED_BY, []) == ""

    def test_single_entry(self):
        block = _render_relation_block(_REL_GATED_BY, [("Do first", UUID_A)])
        assert block == f"{_REL_GATED_BY}\n{_entry('Do first', UUID_A)}"

    def test_multiple_entries_title_then_url(self):
        block = _render_relation_block(
            _REL_GATED_BY, [("A", UUID_A), ("B", UUID_B)]
        )
        lines = block.split("\n")
        assert lines[0] == _REL_GATED_BY
        assert lines[1] == "A"
        assert lines[2] == f"things:///show?id={UUID_A}"
        assert lines[3] == "B"
        assert lines[4] == f"things:///show?id={UUID_B}"


class TestParseRelationBlock:
    """_parse_relation_block: locate label, consume (title, deep-link) pairs."""

    def test_absent_label_returns_notes_unchanged(self):
        text, entries = _parse_relation_block("just user notes", _REL_GATED_BY)
        assert text == "just user notes"
        assert entries == []

    def test_none_notes(self):
        text, entries = _parse_relation_block(None, _REL_GATED_BY)
        assert text == ""
        assert entries == []

    def test_extracts_entries_and_strips_block(self):
        notes = (
            f"user text\n\n{_REL_GATED_BY}\n"
            f"{_entry('First', UUID_A)}\n{_entry('Second', UUID_B)}"
        )
        text, entries = _parse_relation_block(notes, _REL_GATED_BY)
        assert text == "user text"
        assert entries == [("First", UUID_A), ("Second", UUID_B)]

    def test_title_with_brackets_and_parens(self):
        # The title is its own plain line, so brackets/parens (which would have
        # tripped a markdown-link parser) are stored and read back verbatim.
        title = "Fix [URGENT] thing (re: prod)"
        notes = f"{_REL_GATED_BY}\n{_entry(title, UUID_A)}"
        _text, entries = _parse_relation_block(notes, _REL_GATED_BY)
        assert entries == [(title, UUID_A)]

    def test_block_stops_at_non_entry_line(self):
        notes = (
            f"{_REL_GATED_BY}\n{_entry('A', UUID_A)}\n"
            "trailing user prose that is not a link"
        )
        text, entries = _parse_relation_block(notes, _REL_GATED_BY)
        assert entries == [("A", UUID_A)]
        assert "trailing user prose" in text

    def test_only_targets_its_own_label(self):
        # A Gates block must be invisible to a Gated by parse and vice versa.
        from things_mcp.writes import _REL_GATES

        notes = (
            f"{_REL_GATES}\n{_entry('Dep', UUID_C)}\n\n"
            f"{_REL_GATED_BY}\n{_entry('Blk', UUID_A)}"
        )
        _text, gated_by = _parse_relation_block(notes, _REL_GATED_BY)
        assert gated_by == [("Blk", UUID_A)]


class TestSpliceNotes:
    """_splice_notes: re-attach a block to user text, normalized to the end."""

    def test_empty_block_returns_trimmed_text(self):
        assert _splice_notes("user text\n\n", "") == "user text"

    def test_appends_with_blank_separator(self):
        block = _render_relation_block(_REL_GATED_BY, [("A", UUID_A)])
        assert _splice_notes("user text", block) == f"user text\n\n{block}"

    def test_block_only_when_no_user_text(self):
        block = _render_relation_block(_REL_GATED_BY, [("A", UUID_A)])
        assert _splice_notes("", block) == block

    def test_parse_then_splice_is_idempotent(self):
        # The core idempotency guarantee: rendering a parsed-out block back on
        # produces byte-identical notes on every subsequent pass.
        notes = (
            f"user text\n\n{_REL_GATED_BY}\n"
            f"{_entry('A', UUID_A)}\n{_entry('B', UUID_B)}"
        )
        text, entries = _parse_relation_block(notes, _REL_GATED_BY)
        rebuilt = _splice_notes(text, _render_relation_block(_REL_GATED_BY, entries))
        assert rebuilt == notes
        # And a second round-trip is stable too.
        text2, entries2 = _parse_relation_block(rebuilt, _REL_GATED_BY)
        rebuilt2 = _splice_notes(
            text2, _render_relation_block(_REL_GATED_BY, entries2)
        )
        assert rebuilt2 == notes


class TestRelationPresent:
    """_has_uuid / _relation_present: membership by uuid."""

    def test_has_uuid(self):
        entries = [("A", UUID_A), ("B", UUID_B)]
        assert _has_uuid(entries, UUID_A)
        assert not _has_uuid(entries, UUID_C)

    def test_relation_present(self):
        notes = f"{_REL_GATED_BY}\n{_entry('A', UUID_A)}"
        assert _relation_present(notes, _REL_GATED_BY, UUID_A)
        assert not _relation_present(notes, _REL_GATED_BY, UUID_B)

    def test_relation_present_on_none_notes(self):
        assert not _relation_present(None, _REL_GATED_BY, UUID_A)
