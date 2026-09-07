"""Evening-flag reads, straight from the Things database.

things.py does not expose Things' evening marker. It never has: its SELECT
does not include the `startBucket` column, so a raw item dict has no `evening`
key at all. Both TemporalState builders used to do::

    evening = bool(raw.get("evening", False))   # things.py may include it

-- a guess written as a fact. `raw` has no such key, so that expression is
`False` for every item that has ever passed through it. `evening` was not
unreliable; it was a constant.

That produced three dev-issue filings (things-mcp#9, #21, #23) reporting that
`when="evening"` writes "don't take". Two of the three repro items carry
`startBucket = 1` in the live database today, so the writes landed and only the
read was lying. The third report went further and concluded the write path was
broken, which sent the investigation at the wrong half of the system.

So this module reads the marker Things actually stores.

**Unknown is not False.** If the column or the database cannot be read, every
lookup returns `None`, never `False`. Returning `False` there would rebuild the
original bug exactly: a value that reads as "not in the evening" whether or not
anybody looked. `None` is visibly an absence of knowledge; `False` is a claim.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import things.database

# Things stores the Today sub-section in TMTask.startBucket: 0 is the main
# Today block, 1 is This Evening. Verified against the live database, where
# every one of the 196 startBucket=1 rows also carries a startDate, and the
# repro items from things-mcp#9 and #23 -- both confirmed in This Evening by
# screenshot at filing time -- are among them.
_EVENING_BUCKET = 1

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_has_column: bool | None = None


def database_path() -> str:
    """The database things.py itself would read.

    Resolved through things.py rather than rebuilt here, so this can never end
    up reading a different file than the one that produced the item dict --
    including under THINGSDB, which the tests set.
    """
    return (
        os.getenv(things.database.ENVIRONMENT_VARIABLE_WITH_FILEPATH)
        or things.database.DEFAULT_FILEPATH
    )


def _connect() -> sqlite3.Connection | None:
    """Open (or reuse) a read-only connection to the current database path.

    Reopens when the path changes, so a test that repoints THINGSDB is not
    served from a connection to the previous database.
    """
    global _conn, _conn_path, _has_column

    path = database_path()
    if _conn is not None and _conn_path == path:
        return _conn

    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None
    _conn_path = None
    _has_column = None

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(TMTask)")}
    except Exception:
        return None

    _conn = conn
    _conn_path = path
    _has_column = "startBucket" in columns
    return _conn


def reset_cache() -> None:
    """Drop the cached connection. For tests that swap databases."""
    global _conn, _conn_path, _has_column
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
        _conn_path = None
        _has_column = None


def evening_flags(uuids: list[str]) -> dict[str, bool | None]:
    """Map each uuid to its evening flag, or to None where it cannot be read.

    A uuid missing from the database maps to None as well: absent is not the
    same claim as "not in the evening".
    """
    if not uuids:
        return {}

    unknown: dict[str, bool | None] = {u: None for u in uuids}

    with _lock:
        conn = _connect()
        if conn is None or not _has_column:
            return unknown

        result = dict(unknown)
        try:
            # Chunked to stay well under SQLITE_MAX_VARIABLE_NUMBER.
            for i in range(0, len(uuids), 500):
                chunk = uuids[i : i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT uuid, startBucket FROM TMTask WHERE uuid IN ({placeholders})",
                    chunk,
                )
                for uuid, bucket in rows:
                    result[uuid] = bucket == _EVENING_BUCKET
        except Exception:
            return unknown

        return result


def is_evening(uuid: str) -> bool | None:
    """Evening flag for one item, or None if it cannot be read."""
    return evening_flags([uuid])[uuid]
