"""Whether a completed item has actually been moved to the Logbook yet.

Completing a to-do does not put it in the Logbook. Things leaves it checked
off in its original list (Today, Anytime, a project) until the next logbook
sweep, per Settings > General > "Move completed items to Logbook". Deriving
Logbook from status alone told callers an item was in the Logbook while RP
was looking at it, checked, in Today (things-mcp#8).

The sweep leaves one mark in the database: TMSettings.manualLogDate, the time
it last ran. A completed or canceled item whose stopDate is later than that
has not been swept. Verified live on 2026-09-25 (Things 3.24,
logInterval=4): all three items completed after manualLogDate were still
showing in their original lists, and none of the 12,584 before it were.

**Only for logInterval values that have been verified.** The "Immediately"
setting may never advance manualLogDate, and then this rule would call every
completion unlogged. So an unverified interval falls back to the old answer
(Logbook), the same as a failed read. Add a value to _VERIFIED_INTERVALS only
after checking it against the Things UI.

Same contract as evening.py: a fresh read-only connection per call, and
unknown maps to None, never to a claim.
"""

from __future__ import annotations

import sqlite3

from things_mcp.evening import database_path

# logInterval values where "stopDate > manualLogDate" was checked against the
# UI. 4 is RP's setting; which menu label it is has not been established.
_VERIFIED_INTERVALS = {4}


def unlogged_flags(uuids: list[str]) -> dict[str, bool | None]:
    """Map each uuid to True if completed/canceled but not yet swept to the
    Logbook, False if swept or not closed, None if it cannot be told."""
    if not uuids:
        return {}
    unknown: dict[str, bool | None] = {u: None for u in uuids}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)
        row = conn.execute("SELECT logInterval, manualLogDate FROM TMSettings LIMIT 1").fetchone()
        if row is None or row[0] not in _VERIFIED_INTERVALS or row[1] is None:
            return unknown
        swept_at = row[1]
        result = dict(unknown)
        for i in range(0, len(uuids), 500):
            chunk = uuids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT uuid, status, stopDate FROM TMTask WHERE uuid IN ({placeholders})",
                chunk,
            )
            for uuid, status, stop in rows:
                if status not in (2, 3):
                    result[uuid] = False
                elif stop is not None:
                    result[uuid] = stop > swept_at
        return result
    except Exception:
        return unknown
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def is_unlogged(uuid: str) -> bool | None:
    return unlogged_flags([uuid])[uuid]
