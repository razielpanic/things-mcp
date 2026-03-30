"""Things 3 MCP server -- tool definitions.

A thin MCP layer over Things 3 that exposes Cultured Code's actual data model.
Every response includes `derived_list` -- the computed list an item appears in,
derived from `start` + `start_date`.

Read path: things.py (SQLite) -> derivation -> response
Write path: AppleScript (scheduling/moves) + URL scheme (checklists)

STUB: Tool definitions with correct signatures and docstrings.
Handlers call into reads.py and writes.py (also stubs).
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "things-mcp",
    description="Things 3 MCP server with correct data model semantics",
)


# ---------------------------------------------------------------------------
# Read Tools (7)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_inbox(limit: int = 50) -> dict:
    """Get unprocessed items from Inbox.

    Items in Inbox have not been triaged. They need to be moved to
    Anytime (active), Someday (parked), or scheduled for a specific date.

    Returns items with derived_list="Inbox".
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_today(limit: int = 50) -> dict:
    """Get items scheduled for today.

    An item is in Today when its start_date <= today, regardless of the
    sticky start flag. An item with start=Someday but start_date=today
    IS in Today -- the start_date overrides the flag for list placement.

    Returns items with derived_list="Today".
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_upcoming(limit: int = 50, days_ahead: int = 30) -> dict:
    """Get items with future start dates.

    Items in Upcoming have a start_date in the future. They auto-promote
    to Today when their start_date arrives.

    Returns items with derived_list="Upcoming".
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_anytime(limit: int = 50) -> dict:
    """Get active items with no specific start date.

    Anytime is the default state for processed items. It means "available
    for work whenever" -- it is NOT a list you explicitly move items into.
    An item is in Anytime when start=Anytime and start_date is null.

    Returns items with derived_list="Anytime".
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_someday(limit: int = 50) -> dict:
    """Get parked items.

    Someday items are intentionally deferred. They will not surface in
    daily views until explicitly rescheduled.

    Returns items with derived_list="Someday".
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_logbook(limit: int = 50, period: str = "7d") -> dict:
    """Get completed or canceled items.

    Items are in Logbook when their status is completed or canceled,
    regardless of start flag or start_date.

    Args:
        limit: Max items to return.
        period: How far back to look (e.g. "7d", "30d", "1y").
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_item(uuid: str) -> dict:
    """Get a single item by UUID with full detail.

    Returns the complete item including full notes (not truncated),
    checklist items, and all metadata. The derived_list field shows
    which list this item actually appears in.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


# ---------------------------------------------------------------------------
# Query Tools (3)
# ---------------------------------------------------------------------------


@mcp.tool()
async def search(query: str, limit: int = 50) -> dict:
    """Search items by title and notes text.

    Searches across all items regardless of list placement or status.
    Results include derived_list for each match.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_projects(include_items: bool = False) -> dict:
    """Get all projects with metadata.

    Projects are multi-step completable containers that live inside Areas.
    Each project's derived_list shows its temporal placement.

    Args:
        include_items: If true, also returns to-dos within each project.
            Projects are queried individually by UUID to avoid the upstream
            empty-response bug with bulk include.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_areas(include_items: bool = False) -> dict:
    """Get all areas with metadata.

    Areas are ongoing, never-completed structural containers.
    They hold projects and loose to-dos.

    Args:
        include_items: If true, also returns projects and loose to-dos.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


# ---------------------------------------------------------------------------
# Write Tools (6)
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_todo(
    title: str,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[str] = None,
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
    heading: Optional[str] = None,
    checklist_items: Optional[str] = None,
) -> dict:
    """Create a new to-do in Things 3.

    Args:
        title: The to-do title.
        notes: Body text / markdown notes.
        when: Scheduling -- "today", "tomorrow", "evening", "anytime",
            "someday", or "YYYY-MM-DD". None = Anytime (default state).
        deadline: Due date as "YYYY-MM-DD". Adds visual pressure but does
            NOT move the item out of its current list.
        tags: Comma-separated tag names.
        project_uuid: UUID of parent project.
        area_uuid: UUID of parent area (ignored if project_uuid is set).
        heading: Heading title within the project to place under.
        checklist_items: Newline-separated checklist items. Uses URL scheme
            when present (only way to create checklists).
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def create_project(
    title: str,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[str] = None,
    area_uuid: Optional[str] = None,
    todos: Optional[str] = None,
) -> dict:
    """Create a new project in Things 3.

    Important: Never schedule a project to Today. Only tasks get Today.
    A project scheduled to Today doubles up in the sidebar view.

    Args:
        title: Project title.
        notes: Project notes.
        when: Scheduling for the project.
        deadline: Project deadline.
        tags: Comma-separated tags.
        area_uuid: UUID of parent area.
        todos: Newline-separated initial to-do titles.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def update_item(
    uuid: str,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[str] = None,
    completed: Optional[bool] = None,
    canceled: Optional[bool] = None,
) -> dict:
    """Update any field on an existing item.

    Args:
        uuid: The item's UUID (required).
        title: New title.
        notes: New notes (replaces existing).
        when: Reschedule -- same values as create_todo.
        deadline: New deadline as "YYYY-MM-DD", or "" to clear.
        tags: New comma-separated tags (replaces existing).
        completed: Set to true to mark complete. Moves to Logbook.
        canceled: Set to true to cancel. Moves to Logbook.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def schedule_item(uuid: str, when: str) -> dict:
    """Set an item's temporal placement (which list it appears in).

    This is the most important write operation. Maps `when` to:
    - "today" -> AppleScript `schedule` for current date -> Today
    - "tomorrow" -> `schedule` for tomorrow -> Upcoming (promotes next day)
    - "evening" -> `schedule` for today evening -> This Evening
    - "YYYY-MM-DD" -> `schedule` for that date -> Today or Upcoming
    - "anytime" -> `move to list "Anytime"` -> clears start_date
    - "someday" -> `move to list "Someday"` -> parks the item

    CRITICAL: "anytime" clears the start_date and sets start=Anytime.
    It does NOT map to Someday. This was the root bug in the previous MCP.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def move_to_context(
    uuid: str,
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
) -> dict:
    """Move an item to a different project or area (structural context).

    This changes WHERE the item lives in the hierarchy, not WHEN to work
    on it. Use schedule_item for temporal changes.

    Args:
        uuid: The item to move.
        project_uuid: Target project UUID, or None.
        area_uuid: Target area UUID (used only if project_uuid is None).
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def delete_item(uuid: str) -> dict:
    """Move an item to the trash.

    Requires a valid auth token in ~/.things-auth. Will verify the token
    exists before attempting the operation, and confirm the item was
    actually trashed afterward.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
