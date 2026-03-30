"""Things 3 MCP server -- tool definitions.

A thin MCP layer over Things 3 that exposes Cultured Code's actual data model.
Temporal lists (Today, Upcoming, Anytime, Someday) are computed views, not
containers. The ``start`` flag is sticky (Inbox/Anytime/Someday) and does NOT
change when an item moves to Today or Upcoming via ``start_date``. The
``derived_list`` field in every response is the source of truth for which list
an item currently appears in, computed from ``start`` + ``start_date`` +
``status``.

Read path: things.py (SQLite) -> derivation -> response
Write path: AppleScript (scheduling/moves) + URL scheme (checklists)
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from things_mcp import reads, writes
from things_mcp.models import ErrorResponse

mcp = FastMCP(
    "things-mcp",
    instructions=(
        "Things 3 MCP server exposing Cultured Code's actual data model. "
        "Key concept: temporal lists (Today, Upcoming, Anytime, Someday) are computed views, "
        "not containers. An item's list placement is derived from its `start` flag (sticky: "
        "Inbox/Anytime/Someday) and `start_date`. The `temporal_state` block in every response "
        "shows these derivation inputs alongside the computed `derived_list`. "
        "Use `derived_list` for all list-related logic."
    ),
)


# ---------------------------------------------------------------------------
# Read Tools (7)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_inbox(limit: int = 50) -> dict:
    """Get untriaged items from Inbox.

    Inbox holds items with start=Inbox that have not been processed. Items
    stay here regardless of start_date until explicitly triaged. Triage means
    setting start to Anytime (active) or Someday (deferred), or scheduling
    with a start_date.

    Returns items with derived_list="Inbox".
    """
    try:
        items = reads.get_inbox(limit=limit)
        return {
            "view": "Inbox",
            "description": "Untriaged items (start=Inbox). Triage by scheduling or moving to Anytime/Someday.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_today(limit: int = 50) -> dict:
    """Get items in the Today computed view.

    Today is a computed view, not a container: items appear here when
    start_date <= today. The sticky ``start`` flag does NOT change -- an item
    with start=Someday but start_date=today IS in Today. Evening items appear
    here with evening=true in temporal_state.

    Returns items with derived_list="Today".
    """
    try:
        items = reads.get_today(limit=limit)
        return {
            "view": "Today",
            "description": "Items with start_date <= today. Includes overdue items and items scheduled for this evening.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_upcoming(limit: int = 50, days_ahead: int = 30) -> dict:
    """Get items in the Upcoming computed view.

    Upcoming is a computed view: items appear here when start_date > today.
    Items auto-promote to Today when start_date arrives. The sticky ``start``
    flag remains unchanged during promotion.

    Returns items with derived_list="Upcoming".
    """
    try:
        items = reads.get_upcoming(limit=limit, days_ahead=days_ahead)
        return {
            "view": "Upcoming",
            "description": "Items with start_date > today. Auto-promote to Today when start_date arrives.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_anytime(limit: int = 50) -> dict:
    """Get items in the Anytime list.

    Anytime is the default state for processed items: start=Anytime with no
    start_date. "Anytime" means available for work with no temporal constraint.
    This is NOT a holding pen -- it is the active working set.

    Returns items with derived_list="Anytime".
    """
    try:
        items = reads.get_anytime(limit=limit)
        return {
            "view": "Anytime",
            "description": "Active items with no start_date (start=Anytime). Available for work anytime.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_someday(limit: int = 50) -> dict:
    """Get items in the Someday list.

    Someday items are intentionally deferred: start=Someday with no start_date.
    They never surface in daily views until explicitly rescheduled (given a
    start_date or moved to Anytime).

    Returns items with derived_list="Someday".
    """
    try:
        items = reads.get_someday(limit=limit)
        return {
            "view": "Someday",
            "description": "Deferred items (start=Someday, no start_date). Hidden from daily views until rescheduled.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_logbook(limit: int = 50, period: str = "7d") -> dict:
    """Get completed or canceled items from Logbook.

    Logbook contains items where status is completed or canceled. Status
    overrides all temporal placement -- start flag and start_date are preserved
    but do not affect visibility once an item enters Logbook.

    Args:
        limit: Max items to return.
        period: How far back to look (e.g. "7d", "30d", "1y").
    """
    try:
        items = reads.get_logbook(limit=limit, period=period)
        return {
            "view": "Logbook",
            "description": "Completed or canceled items. Status overrides all temporal placement.",
            "items": [item.model_dump() for item in items],
            "count": len(items),
        }
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def get_item(uuid: str) -> dict:
    """Get a single item by UUID with full detail.

    Returns the complete item including full notes (not truncated),
    checklist items, and all metadata. The temporal_state block shows all
    derivation inputs (start, start_date, derived_list, status, evening) so
    you can understand why the item is in its current computed view.
    """
    try:
        item = reads.get_item(uuid=uuid)
        if item is None:
            return ErrorResponse(error="NOT_FOUND", message=f"No item with UUID {uuid}").model_dump()
        return item.model_dump()
    except Exception as e:
        return ErrorResponse(error="READ_ERROR", message=str(e)).model_dump()


# ---------------------------------------------------------------------------
# Query Tools (3)
# ---------------------------------------------------------------------------


@mcp.tool()
async def search(query: str, limit: int = 50) -> dict:
    """Search items by title and notes text across all temporal lists and statuses.

    Results span every computed view (Inbox, Today, Upcoming, Anytime, Someday,
    Logbook). Each match includes temporal_state showing its current list
    placement derived from start + start_date + status.
    """
    # TODO: Wire up reads.search when implemented
    return {
        "view": "Search",
        "description": f"Items matching '{query}' across all lists and statuses.",
        "items": [],
        "count": 0,
        "error": "NOT_IMPLEMENTED",
        "message": "Stub -- reads.py not wired up yet",
    }


@mcp.tool()
async def get_projects(include_items: bool = False) -> dict:
    """Get all projects.

    Projects are structural containers (context axis), not temporal placements.
    A project has its own temporal_state -- it can be in Today, Upcoming, Anytime,
    or Someday independent of its to-dos' placements.

    Args:
        include_items: If true, also returns to-dos within each project.
            Projects are queried individually by UUID to avoid the upstream
            empty-response bug with bulk include.
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- reads.py not wired up yet"}


@mcp.tool()
async def get_areas(include_items: bool = False) -> dict:
    """Get all areas.

    Areas are permanent structural containers that never complete. They hold
    projects and loose to-dos. Areas have no temporal placement -- they exist
    on the structural axis only.

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

    Creates in Inbox (start=Inbox) by default. Use ``when`` to set initial
    temporal placement: a date sets start_date (placing in Today or Upcoming),
    "anytime" sets start=Anytime, "someday" sets start=Someday. Response
    includes temporal_state showing the resulting computed view.

    Args:
        title: The to-do title.
        notes: Body text / markdown notes.
        when: Scheduling -- "today", "tomorrow", "evening", "anytime",
            "someday", or "YYYY-MM-DD". None = Inbox (default).
        deadline: Due date as "YYYY-MM-DD". Adds visual pressure but does
            NOT move the item out of its current list.
        tags: Comma-separated tag names.
        project_uuid: UUID of parent project (structural context).
        area_uuid: UUID of parent area (structural context, ignored if
            project_uuid is set).
        heading: Heading title within the project to place under.
        checklist_items: Newline-separated checklist items. Uses URL scheme
            when present (only way to create checklists).
    """
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        cl_list = [c.strip() for c in checklist_items.split("\n") if c.strip()] if checklist_items else None
        result = writes.create_todo(
            title=title,
            notes=notes,
            when=when,
            deadline=deadline,
            tags=tag_list,
            project_uuid=project_uuid,
            area_uuid=area_uuid,
            heading=heading,
            checklist_items=cl_list,
        )
        return result.model_dump()
    except Exception as e:
        return ErrorResponse(error="WRITE_ERROR", message=str(e)).model_dump()


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
    """Create a new structural container (project) in Things 3.

    Projects are completable containers on the structural axis. Never schedule
    a project to Today -- projects in Today cause sidebar duplication. Use
    "anytime" or a future date. Response includes temporal_state showing the
    project's computed view placement.

    Args:
        title: Project title.
        notes: Project notes.
        when: Scheduling -- "anytime", "someday", or "YYYY-MM-DD". Never
            use "today" for projects.
        deadline: Project deadline.
        tags: Comma-separated tags.
        area_uuid: UUID of parent area (structural context).
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
    """Update properties on an existing item.

    The ``when`` parameter triggers a temporal state transition -- see
    schedule_item for the full when-to-state mapping. Response includes the
    new temporal_state showing the transition result.

    Args:
        uuid: The item's UUID (required).
        title: New title.
        notes: New notes (replaces existing).
        when: Reschedule -- triggers state transition (see schedule_item).
        deadline: New deadline as "YYYY-MM-DD", or "" to clear.
        tags: New comma-separated tags (replaces existing).
        completed: Set to true to mark complete. Status -> completed, item
            moves to Logbook (overrides temporal placement).
        canceled: Set to true to cancel. Status -> canceled, item moves to
            Logbook (overrides temporal placement).
    """
    try:
        result = writes.update_item(
            uuid=uuid,
            title=title,
            notes=notes,
            when=when,
            deadline=deadline,
            tags=tags,
            completed=completed,
            canceled=canceled,
        )
        return result.model_dump()
    except Exception as e:
        return ErrorResponse(error="WRITE_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def schedule_item(uuid: str, when: str) -> dict:
    """The core temporal operation: change which computed view an item appears in.

    Maps ``when`` values to state transitions on start + start_date:
    - "today" -> sets start_date=today -> derived_list=Today
    - "tomorrow" -> sets start_date=tomorrow -> derived_list=Upcoming (auto-promotes)
    - "evening" -> sets start_date=today + evening flag -> derived_list=Today, evening=true
    - "YYYY-MM-DD" -> sets start_date to that date -> Today or Upcoming
    - "anytime" -> clears start_date, sets start=Anytime (CRITICAL: not Someday)
    - "someday" -> clears start_date, sets start=Someday

    Response includes updated temporal_state showing the state transition result.
    """
    try:
        result = writes.schedule_item(uuid=uuid, when=when)
        return result.model_dump()
    except Exception as e:
        return ErrorResponse(error="WRITE_ERROR", message=str(e)).model_dump()


@mcp.tool()
async def move_to_context(
    uuid: str,
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
) -> dict:
    """Move an item to a different project or area (structural context change).

    This changes structural placement (which project/area), NOT temporal
    placement. An item's computed view membership (Today, Upcoming, etc.) is
    unaffected by moving between projects or areas. Use schedule_item for
    temporal changes.

    Args:
        uuid: The item to move.
        project_uuid: Target project UUID, or None.
        area_uuid: Target area UUID (used only if project_uuid is None).
    """
    return {"error": "NOT_IMPLEMENTED", "message": "Stub -- writes.py not wired up yet"}


@mcp.tool()
async def delete_item(uuid: str) -> dict:
    """Move an item to Trash, making it invisible to all computed views.

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
