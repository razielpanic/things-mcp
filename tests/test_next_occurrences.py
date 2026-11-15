"""repeats.next_occurrences: repeats Things shows in Upcoming before the to-do exists (things-mcp#34)."""

from __future__ import annotations

import sqlite3
from datetime import date

from things_mcp import evening, repeats


def _pack(d: date) -> int:
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


def _db(tmp_path, monkeypatch, rows):
    path = tmp_path / "repeats.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE TMTask (uuid TEXT PRIMARY KEY, title TEXT, type INTEGER, "
        "project TEXT, area TEXT, trashed INTEGER, status INTEGER, "
        "rt1_recurrenceRule BLOB, rt1_instanceCreationPaused INTEGER, "
        "rt1_nextInstanceStartDate INTEGER)"
    )
    conn.execute("CREATE TABLE TMArea (uuid TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO TMArea VALUES ('AREA', 'Biz Ops')")
    conn.executemany("INSERT INTO TMTask VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setenv("THINGSDB", str(path))
    evening.reset_cache()


def _tpl(uuid, title, when, *, trashed=0, status=0, paused=0, rule=b"r"):
    return (uuid, title, 0, None, "AREA", trashed, status, rule, paused, _pack(when) if when else None)


def test_live_template_in_window_is_listed(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [_tpl("T1", "Email Invoices", date(2026, 12, 4))])
    try:
        out = repeats.next_occurrences(until=date(2026, 12, 10))
        assert [r["title"] for r in out] == ["Email Invoices"]
        assert out[0]["next_occurrence"] == "2026-12-04"
        assert out[0]["area_title"] == "Biz Ops"
        assert out[0]["template_uuid"] == "T1"
    finally:
        evening.reset_cache()


def test_window_query_and_dead_repeats_filter(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        _tpl("T1", "Email Invoices", date(2026, 12, 4)),
        _tpl("T2", "Later thing", date(2027, 3, 1)),
        _tpl("T3", "Trashed repeat", date(2026, 12, 1), trashed=1),
        _tpl("T4", "Paused repeat", date(2026, 12, 1), paused=1),
        _tpl("T5", "Stopped repeat", date(2026, 12, 1), status=3),
        _tpl("T6", "Plain to-do", date(2026, 12, 1), rule=None),
    ])
    try:
        assert [r["template_uuid"] for r in repeats.next_occurrences(until=date(2026, 12, 31))] == ["T1"]
        assert [r["template_uuid"] for r in repeats.next_occurrences(query="INVOICES")] == ["T1"]
        assert [r["template_uuid"] for r in repeats.next_occurrences()] == ["T1", "T2"]
    finally:
        evening.reset_cache()


def test_unreadable_database_is_none_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("THINGSDB", str(tmp_path / "missing.sqlite"))
    evening.reset_cache()
    try:
        assert repeats.next_occurrences() is None
    finally:
        evening.reset_cache()
