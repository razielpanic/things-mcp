"""Tests for the evening flag, which comes from the database, not things.py.

The bug these cover: `evening` was `bool(raw.get("evening", False))` against a
dict things.py has never put an "evening" key in, so the field was a constant
False for every item, forever. Three dev issues (things-mcp#9, #21, #23) were
filed against `when="evening"` writes that had in fact landed, and #23 went on
to conclude the write path was broken.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import things

from things_mcp import evening

FIXTURE_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURE_DIR))
import create_fixture  # noqa: E402

EVENING_UUID = "EveningTask000000000001"
TODAY_UUID = "TodayTask00000000000001"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = create_fixture.build(str(tmp_path / "f.sqlite"), quiet=True)
    monkeypatch.setenv("THINGSDB", path)
    evening.reset_cache()
    yield Path(path)
    evening.reset_cache()


class TestPremise:
    """The facts the fix rests on, asserted rather than assumed."""

    def test_things_py_emits_no_evening_key(self, db):
        """The original `raw.get("evening")` had nothing to read.

        If things.py ever starts exposing it, this fails and the indirection in
        evening.py can be reconsidered -- which is the point of pinning it.
        """
        raw = things.get(EVENING_UUID)
        assert raw is not None
        assert "evening" not in raw

    def test_things_schema_uses_startbucket(self, db):
        cols = {
            r[1]
            for r in sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
                "PRAGMA table_info(TMTask)"
            )
        }
        assert "startBucket" in cols
        assert "evening" not in cols


class TestEveningFlags:
    def test_evening_item_reads_true(self, db):
        assert evening.is_evening(EVENING_UUID) is True

    def test_plain_today_item_reads_false(self, db):
        assert evening.is_evening(TODAY_UUID) is False

    def test_missing_item_reads_none_not_false(self, db):
        """Absent is not a claim that the item is out of the evening."""
        assert evening.is_evening("Z" * 22) is None

    def test_batch_covers_every_uuid_asked_for(self, db):
        flags = evening.evening_flags([EVENING_UUID, TODAY_UUID, "Z" * 22])
        assert flags == {EVENING_UUID: True, TODAY_UUID: False, "Z" * 22: None}

    def test_empty_batch(self, db):
        assert evening.evening_flags([]) == {}


class TestDegradation:
    """Unknown must never come back as False -- that is the original bug."""

    def test_missing_column_yields_none_for_all(self, tmp_path, monkeypatch):
        path = tmp_path / "no_bucket.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE TMTask (uuid TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO TMTask VALUES ('abc', 'x')")
        conn.commit()
        conn.close()

        monkeypatch.setenv("THINGSDB", str(path))
        evening.reset_cache()
        try:
            assert evening.is_evening("abc") is None
        finally:
            evening.reset_cache()

    def test_unreadable_database_yields_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("THINGSDB", str(tmp_path / "does_not_exist.sqlite"))
        evening.reset_cache()
        try:
            assert evening.is_evening("abc") is None
        finally:
            evening.reset_cache()

    def test_switching_database_is_not_served_from_a_stale_connection(
        self, db, tmp_path, monkeypatch
    ):
        assert evening.is_evening(EVENING_UUID) is True
        monkeypatch.setenv("THINGSDB", str(tmp_path / "gone.sqlite"))
        assert evening.is_evening(EVENING_UUID) is None


class TestPathResolution:
    def test_honours_thingsdb(self, db):
        assert evening.database_path() == str(db)

    def test_falls_back_to_things_py_default(self, monkeypatch):
        monkeypatch.delenv("THINGSDB", raising=False)
        import things.database

        assert evening.database_path() == things.database.DEFAULT_FILEPATH
