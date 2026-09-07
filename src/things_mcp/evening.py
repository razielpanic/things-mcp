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

import things.database

# Things stores the Today sub-section in TMTask.startBucket: 0 is the main
# Today block, 1 is This Evening. Verified against the live database, where
# every one of the 196 startBucket=1 rows also carries a startDate, and the
# repro items from things-mcp#9 and #23 -- both confirmed in This Evening by
# screenshot at filing time -- are among them.
_EVENING_BUCKET = 1

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


def reset_cache() -> None:
    """No-op, kept so existing callers and tests keep working.

    There is no cache to reset any more -- see evening_flags.
    """


def evening_flags(uuids: list[str]) -> dict[str, bool | None]:
    """Map each uuid to its evening flag, or to None where it cannot be read.

    A uuid missing from the database maps to None as well: absent is not the
    same claim as "not in the evening".

    OPENS A FRESH CONNECTION PER CALL, deliberately. The first version cached
    one at module level, keyed on the path, and that bought three bugs for
    almost nothing:

      - It served a deleted inode. Replace the file at that path -- a Things
        Cloud full re-sync, a restore, a library switch -- and the cached
        connection reads the old file forever, while things.py (which connects
        per query) reads the new one. The item dict and its evening flag then
        come from different databases, and nothing in production ever
        invalidated the cache.
      - It was not thread-safe. sqlite3 defaults to check_same_thread=True, so
        a lock gives mutual exclusion but not cross-thread legality; the
        resulting ProgrammingError was swallowed and the connection never
        reset, so a second thread degraded to None permanently and silently --
        indistinguishable from "column missing".
      - It leaked a file descriptor per failed open, because sqlite3.connect is
        lazy and the PRAGMA that follows can raise after the fd exists.

    things.py opens a connection per query in roughly 50us. The cache was
    saving that and costing correctness, which is a bad trade in the one module
    whose entire premise is that a wrong answer is worse than no answer.
    """
    if not uuids:
        return {}

    unknown: dict[str, bool | None] = {u: None for u in uuids}

    conn = None
    try:
        conn = sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(TMTask)")}
        if "startBucket" not in columns:
            return unknown

        result = dict(unknown)
        # Chunked to stay well under SQLITE_MAX_VARIABLE_NUMBER.
        for i in range(0, len(uuids), 500):
            chunk = uuids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT uuid, startBucket FROM TMTask WHERE uuid IN ({placeholders})",
                chunk,
            )
            for uuid, bucket in rows:
                # startBucket is nullable in the live schema. NULL is an absent
                # value, so it reads as unknown -- calling it False would be the
                # module's own bug in miniature.
                result[uuid] = None if bucket is None else bucket == _EVENING_BUCKET
        return result
    except Exception:
        return unknown
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def is_evening(uuid: str) -> bool | None:
    """Evening flag for one item, or None if it cannot be read."""
    return evening_flags([uuid])[uuid]
