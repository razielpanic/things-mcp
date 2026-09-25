"""Repeat reads, straight from the Things database.

things.py does not expose Things' repeat columns (`rt1_*`) -- it uses them
only to filter templates out of list queries. So an instance of a repeating
to-do arrives with nothing naming the template that generates it, and the
template's own state (trashed, paused, next instance) is invisible.

That gap produced things-mcp#30: a repeat RP had trashed on 2026-06-01 was
reported to him as still live, because nothing in the payload connected the
instance he asked about to the trashed template behind it.

Two roles, both keyed off TMTask columns:

- template: carries `rt1_recurrenceRule`. Things hides it from every list; it
  is the thing the repeat UI edits.
- instance: carries `rt1_repeatingTemplate`, the template's uuid.

**Stopping or pausing a repeat is UI-only as of Things 3.24.** Checked on
2026-09-25 against every scripted surface: the AppleScript dictionary has no
repeat terms, `_private_experimental_ json` carries no repeat fields, the URL
scheme refuses `when`/`deadline` edits on repeating to-dos, and the Shortcuts
"Edit Items" action has no repeat parameter. The one intent that takes a
`recurrence` -- `TAIRemindersUpdateReminderIntent`, in ThingsCommon's
Metadata.appintents -- is `assistantOnly`, so Shortcuts cannot reach it. If a
future Things flips that, a stop-repeat verb becomes buildable.

Same contract as evening.py: a fresh read-only connection per call, and
**unknown is not "not repeating"**. If the columns cannot be read the result
is ``role="unknown"``, never None -- None is a claim that the item has no
repeat relation.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

from things_mcp.evening import database_path
from things_mcp.models import RepeatInfo

_REQUIRED_COLUMNS = {
    "rt1_repeatingTemplate",
    "rt1_recurrenceRule",
    "rt1_instanceCreationPaused",
    "rt1_nextInstanceStartDate",
}

_STATUS = {0: "incomplete", 2: "canceled", 3: "completed"}


def _unpack_date(value: Optional[int]) -> Optional[date]:
    """Decode Things' packed day integer: year<<16 | month<<12 | day<<7."""
    if not value:
        return None
    try:
        return date(value >> 16, (value >> 12) & 0xF, (value >> 7) & 0x1F)
    except ValueError:
        return None


def repeat_info(uuid: str) -> Optional[RepeatInfo]:
    """Repeat relation for one item, or None if it has none.

    Returns ``role="unknown"`` when the database or its repeat columns cannot
    be read, so a failed read never passes for "not repeating".
    """
    conn = None
    try:
        conn = sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(TMTask)")}
        if not _REQUIRED_COLUMNS <= columns:
            return RepeatInfo(role="unknown")

        row = conn.execute(
            "SELECT rt1_repeatingTemplate, rt1_recurrenceRule IS NOT NULL "
            "FROM TMTask WHERE uuid = ?",
            (uuid,),
        ).fetchone()
        if row is None:
            return RepeatInfo(role="unknown")
        template_uuid, is_template = row
        if is_template:
            role, template_uuid = "template", uuid
        elif template_uuid:
            role = "instance"
        else:
            return None

        tpl = conn.execute(
            "SELECT trashed, status, rt1_instanceCreationPaused, "
            "rt1_nextInstanceStartDate FROM TMTask WHERE uuid = ?",
            (template_uuid,),
        ).fetchone()
        if tpl is None:
            # Template row gone: emptied from Trash. The instance survives it.
            return RepeatInfo(role=role, template_uuid=template_uuid, template_found=False)
        trashed, status, paused, next_start = tpl
        return RepeatInfo(
            role=role,
            template_uuid=template_uuid,
            template_found=True,
            template_trashed=bool(trashed),
            template_status=_STATUS.get(status),
            template_paused=None if paused is None else bool(paused),
            next_instance_date=_unpack_date(next_start),
        )
    except Exception:
        return RepeatInfo(role="unknown")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
