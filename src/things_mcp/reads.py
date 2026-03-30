"""Read operations via things.py (SQLite queries).

All reads go through things.py which queries the Things 3 SQLite database
directly. This is the fastest possible read path (<10ms).

Every item returned includes a `derived_list` field computed by the
derivation module. Consumers should use `derived_list` for list-related
logic, never the raw `start` field.

STUB: This module contains function signatures and docstrings only.
Implementation will query things.py and enrich results with derived_list.
"""

from __future__ import annotations

from typing import Optional

from things_mcp.models import ThingsItem


def get_inbox(*, limit: int = 50) -> list[ThingsItem]:
    """Get unprocessed items from Inbox.

    These are items with start=Inbox (database value 0). They have not
    been triaged into Anytime/Someday yet.
    """
    raise NotImplementedError("TODO: Query things.todos(start='Inbox')")


def get_today(*, limit: int = 50) -> list[ThingsItem]:
    """Get items scheduled for today.

    These are items where start_date <= today, regardless of the start flag.
    An item with start=Someday but start_date=today IS in Today.
    """
    raise NotImplementedError("TODO: Query things.today()")


def get_upcoming(*, limit: int = 50, days_ahead: int = 30) -> list[ThingsItem]:
    """Get items with future start dates.

    These are items where start_date > today. They will auto-promote to
    Today when their start_date arrives.
    """
    raise NotImplementedError("TODO: Query things.upcoming()")


def get_anytime(*, limit: int = 50) -> list[ThingsItem]:
    """Get active items with no specific start date.

    These are items where start=Anytime and start_date is null.
    This is the default state for processed items -- Anytime means
    "available for work whenever."
    """
    raise NotImplementedError("TODO: Query things.anytime()")


def get_someday(*, limit: int = 50) -> list[ThingsItem]:
    """Get parked items.

    These are items where start=Someday and start_date is null.
    Someday means "not now, maybe later."
    """
    raise NotImplementedError("TODO: Query things.someday()")


def get_logbook(*, limit: int = 50, period: str = "7d") -> list[ThingsItem]:
    """Get completed or canceled items.

    Items are in Logbook when status is completed or canceled,
    regardless of start flag or start_date.
    """
    raise NotImplementedError("TODO: Query things.logbook()")


def get_item(*, uuid: str) -> Optional[ThingsItem]:
    """Get a single item by UUID with full detail.

    Returns None if the item does not exist. Notes are returned in full
    (not truncated like list views).
    """
    raise NotImplementedError("TODO: Query things.todos(uuid=uuid)")


def search(*, query: str, limit: int = 50) -> list[ThingsItem]:
    """Search items by title and notes text.

    Searches across all items regardless of list placement.
    """
    raise NotImplementedError("TODO: Query things.todos(search_query=query)")


def get_projects(*, include_items: bool = False) -> list[ThingsItem]:
    """Get all projects with metadata.

    Projects are multi-step completable containers that live inside Areas.
    If include_items is True, also returns the to-dos within each project
    (queried separately by project UUID to avoid the upstream empty bug).
    """
    raise NotImplementedError("TODO: Query things.projects()")


def get_areas(*, include_items: bool = False) -> list[dict]:
    """Get all areas with metadata.

    Areas are ongoing, never-completed containers. They hold projects
    and loose to-dos.
    """
    raise NotImplementedError("TODO: Query things.areas()")
