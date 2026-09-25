"""Read operations via things.py (SQLite queries).

All reads go through things.py which queries the Things 3 SQLite database
directly. This is the fastest possible read path (<10ms).

Every item returned includes a `derived_list` field computed by the
derivation module. Consumers should use `derived_list` for list-related
logic, never the raw `start` field.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

import things

from things_mcp import checklist as checklist_reader
from things_mcp import evening as evening_reader
from things_mcp import logbook as logbook_reader
from things_mcp import repeats
from things_mcp.derivation import derive_list
from things_mcp.models import AreaItem, ChecklistItem, ItemContext, TemporalState, ThingsItem


def _parse_date(val: str | None) -> date | None:
    """Parse ISO date string from things.py, handling None and time components."""
    if val is None:
        return None
    return date.fromisoformat(val[:10])


def _parse_datetime(val: str | None) -> datetime | None:
    """Parse ISO datetime string from things.py, handling None."""
    if val is None:
        return None
    return datetime.fromisoformat(val)


def _items_from_dicts(
    raw_items: list[dict], *, truncate_notes: bool = True
) -> list[ThingsItem]:
    """Map a list of things.py dicts, reading every evening flag in one query.

    Batched because the evening flag comes from the database rather than from
    the item dict (things.py does not expose it -- see evening.py), and a list
    view should not pay one round trip per row for it.
    """
    uuids = [r["uuid"] for r in raw_items if r.get("uuid")]
    flags = evening_reader.evening_flags(uuids)
    unlogged = logbook_reader.unlogged_flags(uuids)

    # List-view rows carry the TMTask.checklist column, a 0/1 flag, where a
    # get_item row carries the rows themselves. Treating the flag as "no
    # checklist" made every list view report [] for items that get_item showed
    # with rows (things-mcp#29). Resolve the flagged ones in one query.
    flagged = [
        r["uuid"]
        for r in raw_items
        if r.get("uuid") and r.get("checklist") and not isinstance(r.get("checklist"), list)
    ]
    if flagged:
        rows = checklist_reader.checklists_for(flagged)
        for r in raw_items:
            if r.get("uuid") in rows:
                r["checklist"] = rows[r["uuid"]]

    return [
        _item_from_dict(
            r,
            truncate_notes=truncate_notes,
            evening=flags.get(r.get("uuid")),
            unlogged=unlogged.get(r.get("uuid")),
        )
        for r in raw_items
    ]


_UNSET = object()


def _item_from_dict(
    raw: dict,
    *,
    truncate_notes: bool = True,
    evening: bool | None = _UNSET,
    unlogged: bool | None = _UNSET,
) -> ThingsItem:
    """Map a things.py dict to ThingsItem with nested TemporalState and ItemContext.

    Args:
        raw: Dict returned by things.py query functions.
        truncate_notes: If True, truncate notes to 200 chars (for list views).
        evening: Pre-fetched evening flag. Omit to look it up for this item;
            pass one from evening_flags() when mapping a list.
    """
    # Validate required fields — raise ValueError instead of KeyError
    uuid = raw.get("uuid")
    if uuid is None:
        raise ValueError("Missing required field 'uuid' in Things item data")
    title = raw.get("title")
    if title is None:
        raise ValueError("Missing required field 'title' in Things item data")
    item_type = raw.get("type")
    if item_type is None:
        raise ValueError("Missing required field 'type' in Things item data")

    start = raw.get("start", "Anytime")
    start_date = _parse_date(raw.get("start_date"))
    status = raw.get("status", "incomplete")

    # things.py has no evening flag to include -- it comes from the database
    # directly. None where it cannot be read, never False. See evening.py.
    if evening is _UNSET:
        evening = evening_reader.is_evening(uuid)
    if unlogged is _UNSET:
        unlogged = logbook_reader.is_unlogged(uuid)

    notes = raw.get("notes")
    if truncate_notes and notes and len(notes) > 200:
        notes = notes[:200]

    checklist_raw = raw.get("checklist", [])
    checklist = [
        ChecklistItem(
            title=ci["title"],
            completed=(ci.get("status") == "completed"),
        )
        for ci in checklist_raw
    ] if isinstance(checklist_raw, list) else []

    temporal_state = TemporalState(
        start=start,
        start_date=start_date,
        derived_list=derive_list(start, start_date, status=status, unlogged=unlogged),
        status=status,
        evening=evening,
    )

    context = ItemContext(
        project_uuid=raw.get("project"),
        project_title=raw.get("project_title"),
        area_uuid=raw.get("area"),
        area_title=raw.get("area_title"),
        heading_title=raw.get("heading_title"),
    )

    return ThingsItem(
        uuid=uuid,
        title=title,
        type=item_type,
        temporal_state=temporal_state,
        context=context,
        deadline=_parse_date(raw.get("deadline")),
        tags=raw.get("tags", []),
        notes=notes,
        checklist=checklist,
        created=_parse_datetime(raw.get("created")),
        modified=_parse_datetime(raw.get("modified")),
        completed_date=_parse_datetime(raw.get("stop_date")),
        today_index=raw.get("today_index"),
        index=raw.get("index"),
    )


def _drop_template_content(raw_items: list[dict]) -> list[dict]:
    """Remove to-dos that live inside a repeating project template.

    Things never lists them; they are the template's contents, edited only
    through the repeating project. things.py's own filter misses them because
    the rule sits on the project, not the to-do (things-mcp#26). Call before
    slicing to `limit`, so a page is not short by however many were dropped.
    """
    hidden = repeats.template_content([r["uuid"] for r in raw_items if r.get("uuid")])
    if not hidden:
        return raw_items
    return [r for r in raw_items if r.get("uuid") not in hidden]


def get_inbox(*, limit: int = 50) -> list[ThingsItem]:
    """Get unprocessed items from Inbox.

    These are items with start=Inbox (database value 0). They have not
    been triaged into Anytime/Someday yet.
    """
    raw_items = _drop_template_content(things.inbox())[:limit]
    return _items_from_dicts(raw_items)


def get_today(*, limit: int = 50) -> list[ThingsItem]:
    """Get items scheduled for today.

    Uses things.today() which handles the three-query union correctly:
    regular today tasks, unconfirmed scheduled tasks, and overdue deadline tasks.
    """
    raw_items = _drop_template_content(things.today())[:limit]
    return _items_from_dicts(raw_items)


def get_upcoming(*, limit: int = 50, days_ahead: int = 30) -> list[ThingsItem]:
    """Get items with future start dates.

    These are items where start_date > today. They will auto-promote to
    Today when their start_date arrives.
    """
    raw_items = _drop_template_content(things.upcoming())[:limit]
    return _items_from_dicts(raw_items)


def get_anytime(*, limit: int = 50) -> list[ThingsItem]:
    """Get active items with no specific start date.

    These are items where start=Anytime and start_date is null.
    This is the default state for processed items -- Anytime means
    "available for work whenever."
    """
    raw_items = _drop_template_content(things.anytime(start_date=False))[:limit]
    return _items_from_dicts(raw_items)


def get_someday(*, limit: int = 50) -> list[ThingsItem]:
    """Get parked items.

    These are items where start=Someday and start_date is null.
    Someday means "not now, maybe later."
    """
    raw_items = _drop_template_content(things.someday())[:limit]
    return _items_from_dicts(raw_items)


_PERIOD_DAYS = {"d": 1, "w": 7, "y": 365}


def _period_cutoff(period: str, *, today: date | None = None) -> date:
    """The earliest completion date a ``period`` reaches back to.

    ``"7d"`` on 2026-08-10 is 2026-08-03: that day and every day after it.
    So ``"1d"`` is yesterday and today, and ``"0d"`` is today only.
    """
    m = re.fullmatch(r"(\d+)([dwy])", period.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid period {period!r}: expected a count and a unit, e.g. '7d', '2w', '1y'"
        )
    count, unit = int(m.group(1)), m.group(2)
    return (today or date.today()) - timedelta(days=count * _PERIOD_DAYS[unit])


def get_logbook(*, limit: int = 50, period: str = "7d") -> list[ThingsItem]:
    """Get completed or canceled items.

    Returns completed or canceled items by completion date, including ones
    Things has not yet swept into the Logbook view. Those report the list they
    still show in (e.g. Today) as derived_list; status says they are done.

    ``period`` is measured against the COMPLETION date. things.py's own
    ``last=`` filter, which this used to pass through, limits by *creation*
    date -- "created within the last N days" -- so ``period="1d"`` returned
    only items both created and completed within a day, and a task finished
    this morning but created last month was invisible to it (things-mcp#24).
    """
    cutoff = _period_cutoff(period)
    raw_items = things.logbook(stop_date=f">={cutoff.isoformat()}")[:limit]
    return _items_from_dicts(raw_items)


def get_item(*, uuid: str) -> Optional[ThingsItem]:
    """Get a single item by UUID with full detail.

    Returns None if the item does not exist. Notes are returned in full
    (not truncated like list views). Checklist items are fetched separately.
    When the item is a project, its child to-dos are populated into
    ``item.items`` via a separate ``things.tasks(project=uuid)`` query
    (mirrors get_projects(include_items=True); child items also carry
    full-detail notes).
    """
    raw = things.get(uuid)
    if raw is None:
        return None

    # Fetch checklist items separately for complete detail
    try:
        checklist_items = things.checklist_items(uuid)
        if isinstance(checklist_items, list):
            raw["checklist"] = checklist_items
    except Exception:
        pass

    item = _item_from_dict(raw, truncate_notes=False)
    item.repeat = repeats.repeat_info(uuid)

    # If this is a project, populate its child tasks. Mirrors the
    # get_projects(include_items=True) pattern — queried separately by
    # project UUID to avoid the upstream things.py include_items bug.
    if item.type == "project":
        raw_children = things.tasks(project=uuid)
        item.items = _items_from_dicts(raw_children, truncate_notes=False)

    return item


def search(
    *,
    query: str,
    project_uuid: str | None = None,
    area: str | None = None,
    tag: str | None = None,
    start_date: str | None = None,
    deadline: str | None = None,
    include_completed: bool = False,
    limit: int = 50,
) -> list[ThingsItem]:
    """Search items by title, notes, and checklist-row text.

    Searches across all items regardless of list placement. things.py's
    search covers title, notes and area title; a to-do whose only match is
    inside one of its checklist rows is found through the checklist table and
    appended, subject to the same filters.
    """
    kwargs: dict = {}
    if project_uuid is not None:
        kwargs["project"] = project_uuid
    if area is not None:
        kwargs["area"] = area
    if tag is not None:
        kwargs["tag"] = tag
    if start_date is not None:
        kwargs["start_date"] = start_date
    if deadline is not None:
        kwargs["deadline"] = deadline
    if include_completed:
        kwargs["status"] = None  # None = any status in things.py
    raw_items = things.tasks(search_query=query, **kwargs)

    # Checklist rows are not in things.py's search columns, so a to-do whose
    # only match is in a row is looked up through the checklist table. The
    # extra hits still have to honour the caller's filters, and a by-uuid
    # fetch cannot do that: things.tasks(uuid=...) returns the row whatever
    # else is passed (verified 2026-09-10 -- a completed uuid comes back under
    # the default incomplete filter, and a wrong project= is ignored). So the
    # candidates are checked against the same filtered query minus the text
    # match. That query runs only when a checklist hit is not already in the
    # results, which is the uncommon case.
    seen = {r["uuid"] for r in raw_items if r.get("uuid")}
    extra = [u for u in checklist_reader.parents_matching(query) if u not in seen]
    if extra:
        wanted = set(extra)
        eligible = {
            r["uuid"]: r for r in things.tasks(**kwargs) if r.get("uuid") in wanted
        }
        raw_items.extend(eligible[u] for u in extra if u in eligible)

    return _items_from_dicts(_drop_template_content(raw_items)[:limit])


def get_projects(*, include_items: bool = False) -> list[ThingsItem]:
    """Get all projects with metadata.

    Projects are multi-step completable containers that live inside Areas.
    If include_items is True, also returns the to-dos within each project
    (queried separately by project UUID to avoid the upstream empty bug).
    """
    raw_projects = things.projects()
    projects = _items_from_dicts(raw_projects)
    if include_items:
        for proj in projects:
            raw_children = things.tasks(project=proj.uuid)
            proj.items = _items_from_dicts(raw_children)
    return projects


def _area_from_dict(raw: dict) -> AreaItem:
    """Map a things.py area dict to AreaItem (no temporal state)."""
    uuid = raw.get("uuid")
    if uuid is None:
        raise ValueError("Missing required field 'uuid' in Things area data")
    return AreaItem(
        uuid=uuid,
        title=raw.get("title", ""),
        tags=raw.get("tags", []),
    )


def get_areas(*, include_items: bool = False) -> list[AreaItem]:
    """Get all areas with metadata.

    Areas are ongoing, never-completed containers. They hold projects
    and loose to-dos.
    """
    raw_areas = things.areas()
    areas = [_area_from_dict(r) for r in raw_areas]
    if include_items:
        for area in areas:
            raw_children = things.tasks(area=area.uuid)
            area.items = _items_from_dicts(_drop_template_content(raw_children))
    return areas
