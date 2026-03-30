"""Read operations via things.py (SQLite queries).

All reads go through things.py which queries the Things 3 SQLite database
directly. This is the fastest possible read path (<10ms).

Every item returned includes a `derived_list` field computed by the
derivation module. Consumers should use `derived_list` for list-related
logic, never the raw `start` field.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import things

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


def _item_from_dict(raw: dict, *, truncate_notes: bool = True) -> ThingsItem:
    """Map a things.py dict to ThingsItem with nested TemporalState and ItemContext.

    Args:
        raw: Dict returned by things.py query functions.
        truncate_notes: If True, truncate notes to 200 chars (for list views).
    """
    start = raw.get("start", "Anytime")
    start_date = _parse_date(raw.get("start_date"))
    status = raw.get("status", "incomplete")

    # Evening detection: things.py may include an evening flag
    evening = bool(raw.get("evening", False))

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
        derived_list=derive_list(start, start_date, status=status),
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
        uuid=raw["uuid"],
        title=raw["title"],
        type=raw["type"],
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


def get_inbox(*, limit: int = 50) -> list[ThingsItem]:
    """Get unprocessed items from Inbox.

    These are items with start=Inbox (database value 0). They have not
    been triaged into Anytime/Someday yet.
    """
    raw_items = things.inbox()[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_today(*, limit: int = 50) -> list[ThingsItem]:
    """Get items scheduled for today.

    Uses things.today() which handles the three-query union correctly:
    regular today tasks, unconfirmed scheduled tasks, and overdue deadline tasks.
    """
    raw_items = things.today()[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_upcoming(*, limit: int = 50, days_ahead: int = 30) -> list[ThingsItem]:
    """Get items with future start dates.

    These are items where start_date > today. They will auto-promote to
    Today when their start_date arrives.
    """
    raw_items = things.upcoming()[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_anytime(*, limit: int = 50) -> list[ThingsItem]:
    """Get active items with no specific start date.

    These are items where start=Anytime and start_date is null.
    This is the default state for processed items -- Anytime means
    "available for work whenever."
    """
    raw_items = things.anytime()[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_someday(*, limit: int = 50) -> list[ThingsItem]:
    """Get parked items.

    These are items where start=Someday and start_date is null.
    Someday means "not now, maybe later."
    """
    raw_items = things.someday()[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_logbook(*, limit: int = 50, period: str = "7d") -> list[ThingsItem]:
    """Get completed or canceled items.

    Items are in Logbook when status is completed or canceled,
    regardless of start flag or start_date.
    """
    raw_items = things.logbook(last=period)[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_item(*, uuid: str) -> Optional[ThingsItem]:
    """Get a single item by UUID with full detail.

    Returns None if the item does not exist. Notes are returned in full
    (not truncated like list views). Checklist items are fetched separately.
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

    return _item_from_dict(raw, truncate_notes=False)


def search(
    *,
    query: str,
    project: str | None = None,
    area: str | None = None,
    tag: str | None = None,
    start_date: str | None = None,
    deadline: str | None = None,
    include_completed: bool = False,
    limit: int = 50,
) -> list[ThingsItem]:
    """Search items by title and notes text.

    Searches across all items regardless of list placement.
    """
    kwargs: dict = {}
    if project is not None:
        kwargs["project"] = project
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
    raw_items = things.tasks(search_query=query, **kwargs)[:limit]
    return [_item_from_dict(r) for r in raw_items]


def get_projects(*, include_items: bool = False) -> list[ThingsItem]:
    """Get all projects with metadata.

    Projects are multi-step completable containers that live inside Areas.
    If include_items is True, also returns the to-dos within each project
    (queried separately by project UUID to avoid the upstream empty bug).
    """
    raw_projects = things.projects()
    projects = [_item_from_dict(r) for r in raw_projects]
    if include_items:
        for proj in projects:
            raw_children = things.tasks(project=proj.uuid)
            proj.items = [_item_from_dict(r) for r in raw_children]
    return projects


def _area_from_dict(raw: dict) -> AreaItem:
    """Map a things.py area dict to AreaItem (no temporal state)."""
    return AreaItem(
        uuid=raw["uuid"],
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
            area.items = [_item_from_dict(r) for r in raw_children]
    return areas
