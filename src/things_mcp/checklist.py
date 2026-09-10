"""Checklist reads for list views and search, straight from the Things database.

things.py fetches checklist rows only when ``include_items=True``, and the
list functions this server wraps (``today``, ``upcoming``, ``anytime``, ...)
never pass it. What a list-view row carries instead is the bare ``checklist``
column of ``TMTask`` -- a 0/1 flag saying whether any rows exist. The mapper
used to treat that non-list value as "no checklist", so every list view
reported ``checklist: []`` for items that ``get_item`` showed with rows
(things-mcp#29). The OM read that as "nothing covers the pad test" and
proposed a duplicate of a task the user already had as a checklist row.

Two reads here, both single queries so a list view does not pay one round
trip per item:

- :func:`checklists_for` -- rows for a batch of to-do uuids.
- :func:`parents_matching` -- uuids of to-dos with a checklist row whose title
  contains a search string. things.py's search matches title, notes and area
  title only.

**Unknown is empty here, and that is a deliberate difference from evening.py.**
A checklist that cannot be read comes back as ``[]`` rather than ``None``
because the field's type is a list and every consumer iterates it. The cost
of that choice is that a read failure looks like an empty checklist, which is
exactly the bug this module fixes. The mitigation is that the batch read is
attempted only for rows whose ``checklist`` flag is truthy, so a silent
failure is confined to items that things.py itself says have rows.
"""

from __future__ import annotations

import sqlite3

from things_mcp.evening import database_path

# TMChecklistItem.status uses the same values as TMTask.status:
# 0 incomplete, 2 canceled, 3 completed. Mirrors things.database.STATUS_TO_FILTER.
_STATUS = {0: "incomplete", 2: "canceled", 3: "completed"}


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)


def checklists_for(uuids: list[str]) -> dict[str, list[dict]]:
    """Map each to-do uuid to its checklist rows, in checklist order.

    Rows are dicts shaped like ``things.checklist_items`` output -- ``title``
    and ``status`` -- so the existing mapper accepts them unchanged. A uuid
    with no rows is absent from the result.
    """
    if not uuids:
        return {}

    result: dict[str, list[dict]] = {}
    conn = None
    try:
        conn = _connect()
        # Chunked to stay well under SQLITE_MAX_VARIABLE_NUMBER.
        for i in range(0, len(uuids), 500):
            chunk = uuids[i : i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f'SELECT task, title, status FROM TMChecklistItem '
                f'WHERE task IN ({placeholders}) ORDER BY task, "index"',
                chunk,
            )
            for task, title, status in rows:
                result.setdefault(task, []).append(
                    {"title": title or "", "status": _STATUS.get(status, "incomplete")}
                )
        return result
    except Exception:
        return result
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def parents_matching(query: str) -> list[str]:
    """Uuids of to-dos that have a checklist row whose title contains ``query``.

    Case-insensitive substring match, the same shape as things.py's own
    title/notes search. Returns an empty list on any read failure.
    """
    if not query:
        return []

    conn = None
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT DISTINCT task FROM TMChecklistItem WHERE title LIKE ? ESCAPE '\\'",
            (
                "%"
                + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%",
            ),
        )
        return [task for (task,) in rows if task]
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
