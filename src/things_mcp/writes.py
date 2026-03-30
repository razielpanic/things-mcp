"""Write operations via AppleScript and Things URL scheme.

Write path strategy:
- AppleScript: Primary write path for scheduling, list moves, property updates.
  Use `schedule` command (not `set activation date`, which is broken).
- URL scheme: Required for checklist operations (AppleScript cannot touch checklists).
  Also correct for `when=anytime` in `add` commands.
  Requires auth token from ~/.things-auth for update/delete operations.

STUB: This module contains function signatures and docstrings only.
Implementation will use subprocess for AppleScript and `open` for URL scheme.
"""

from __future__ import annotations

from typing import Optional

from things_mcp.models import ErrorResponse, SuccessResponse


def create_todo(
    *,
    title: str,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[list[str]] = None,
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
    heading: Optional[str] = None,
    checklist_items: Optional[list[str]] = None,
) -> SuccessResponse | ErrorResponse:
    """Create a new to-do in Things 3.

    Uses URL scheme if checklist_items are provided (only way to create
    checklists). Uses AppleScript otherwise for confirmation.

    The `when` parameter accepts: today, tomorrow, evening, anytime,
    someday, or YYYY-MM-DD. See models.WhenValue for semantics.
    """
    raise NotImplementedError("TODO: AppleScript make + schedule, or URL scheme")


def create_project(
    *,
    title: str,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[list[str]] = None,
    area_uuid: Optional[str] = None,
    todos: Optional[list[str]] = None,
) -> SuccessResponse | ErrorResponse:
    """Create a new project in Things 3.

    Uses URL scheme for bulk creation with initial to-dos.
    Never schedule a project to Today -- only tasks get Today.
    """
    raise NotImplementedError("TODO: URL scheme add-project")


def update_item(
    *,
    uuid: str,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[list[str]] = None,
    completed: Optional[bool] = None,
    canceled: Optional[bool] = None,
) -> SuccessResponse | ErrorResponse:
    """Update any field on an existing item.

    Uses AppleScript for property updates and scheduling.
    Requires auth token for URL scheme operations.

    Important: `completed` and `canceled` must be actual booleans.
    The current MCP's string coercion bug is not replicated here.
    """
    raise NotImplementedError("TODO: AppleScript set properties + schedule")


def schedule_item(
    *,
    uuid: str,
    when: str,
) -> SuccessResponse | ErrorResponse:
    """Set an item's start date or move it to a temporal list.

    This is the most important write operation. The `when` parameter
    maps to specific AppleScript commands:

    - "today" -> schedule for (current date)
    - "tomorrow" -> schedule for tomorrow
    - "evening" -> schedule for today, evening section
    - "YYYY-MM-DD" -> schedule for that date
    - "anytime" -> move to list "Anytime" (clears start_date, sets start=1)
    - "someday" -> move to list "Someday" (clears start_date, sets start=2)

    CRITICAL: "anytime" must NOT map to Someday. This was the root bug
    in the previous MCP server.
    """
    raise NotImplementedError("TODO: AppleScript schedule / move to list")


def move_to_context(
    *,
    uuid: str,
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
) -> SuccessResponse | ErrorResponse:
    """Move an item to a different project or area.

    This changes the structural context (where it lives), not the temporal
    placement (when to work on it). Use schedule_item for temporal moves.
    """
    raise NotImplementedError("TODO: AppleScript set project / move to area")


def delete_item(*, uuid: str) -> SuccessResponse | ErrorResponse:
    """Move an item to the trash.

    Requires auth token. Verifies deletion actually happened (the current
    MCP silently reports success even without auth).
    """
    raise NotImplementedError("TODO: URL scheme update with auth token check")
