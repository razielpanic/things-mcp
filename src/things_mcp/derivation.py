"""List derivation logic -- the core value of this MCP server.

Things 3's temporal lists (Inbox, Today, Upcoming, Anytime, Someday) are
computed views, not storage containers. An item's list membership is derived
from two fields:

1. `start` -- a sticky flag: Inbox (0), Anytime (1), or Someday (2)
2. `start_date` -- when to begin work (nullable date)

The `start` flag does NOT change when an item moves to Today or Upcoming.
Those transitions happen via `start_date`. This is the root cause of most
Things MCP bugs: treating `start` as "which list is this on."

Truth table:
    start=Inbox,   any start_date       -> Inbox
    start=*,       start_date <= today   -> Today
    start=*,       start_date > today    -> Upcoming
    start=Someday, no start_date         -> Someday
    start=Anytime, no start_date         -> Anytime

Completed/canceled items are in Logbook regardless of other fields.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from things_mcp.models import DerivedList, ItemStatus, StartFlag


def derive_list(
    start: str | StartFlag,
    start_date: Optional[date],
    today: Optional[date] = None,
    status: str | ItemStatus = ItemStatus.INCOMPLETE,
) -> DerivedList:
    """Compute the actual list an item appears in.

    Args:
        start: The sticky start flag ("Inbox", "Anytime", or "Someday").
        start_date: The item's start date, or None if unset.
        today: Reference date for today (defaults to date.today()).
        status: Item status. Completed/canceled items are always Logbook.

    Returns:
        The DerivedList value representing the item's actual list placement.
    """
    if today is None:
        today = date.today()

    # Normalize to enum values for comparison
    if isinstance(start, str):
        start = StartFlag(start)
    if isinstance(status, str):
        status = ItemStatus(status)

    # Completed or canceled -> Logbook
    if status in (ItemStatus.COMPLETED, ItemStatus.CANCELED):
        return DerivedList.LOGBOOK

    # Inbox is always Inbox regardless of start_date
    if start == StartFlag.INBOX:
        return DerivedList.INBOX

    # If start_date exists, it overrides the start flag for list placement
    if start_date is not None:
        if start_date <= today:
            return DerivedList.TODAY
        else:
            return DerivedList.UPCOMING

    # No start_date: fall back to the sticky flag
    if start == StartFlag.SOMEDAY:
        return DerivedList.SOMEDAY

    # Default: Anytime (start=Anytime, no start_date)
    return DerivedList.ANYTIME
