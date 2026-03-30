"""Write operations via AppleScript and Things URL scheme.

Write path strategy:
- AppleScript: Primary write path for scheduling, list moves, property updates.
  Use `schedule` command (not `set activation date`, which is broken).
- URL scheme: Required for checklist operations (AppleScript cannot touch checklists).
  Also used for `when=evening` (AppleScript has no evening slot).
  Requires auth token from ~/.things-auth for update/delete operations.

Security:
- User strings passed as osascript argv, never embedded in script source.
- UUIDs validated with regex before all AppleScript calls.
- Dates constructed via property setting, not string parsing (locale-safe).
- shell=True never used with subprocess.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from datetime import date
from typing import Optional

import things

from things_mcp.derivation import derive_list
from things_mcp.models import ErrorResponse, SuccessResponse, TemporalState

# Things 3 uses 22-char base62 identifiers (alphanumeric, no dashes)
_UUID_RE = re.compile(r"^[A-Za-z0-9]{22}$")

# Date pattern for YYYY-MM-DD when values
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_uuid(uuid: str) -> str:
    """Validate that a string is a valid Things UUID format.

    Args:
        uuid: The string to validate.

    Returns:
        The validated UUID string.

    Raises:
        ValueError: If the string does not match UUID format.
    """
    if not _UUID_RE.match(uuid):
        raise ValueError(f"Invalid UUID format: {uuid!r}")
    return uuid


def run_applescript(script: str, *args: str) -> str:
    """Execute an AppleScript via osascript stdin.

    User strings should be passed as args (accessed via `on run argv`
    in the script), never embedded in the script source.

    Args:
        script: The AppleScript source code.
        *args: Arguments passed to the script (available as argv).

    Returns:
        The stdout output from osascript, stripped.

    Raises:
        RuntimeError: If osascript returns a non-zero exit code.
    """
    result = subprocess.run(
        ["osascript", "-", *args],
        input=script,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def _applescript_date_block(var: str, d: date) -> str:
    """Construct a locale-safe AppleScript date via property setting.

    Args:
        var: The AppleScript variable name to assign the date to.
        d: The Python date to convert.

    Returns:
        AppleScript code block that sets the variable to the given date.
    """
    return (
        f"set {var} to current date\n"
        f"set year of {var} to {d.year}\n"
        f"set month of {var} to {d.month}\n"
        f"set day of {var} to {d.day}\n"
        f"set time of {var} to 0"
    )


def _read_temporal_state(uuid: str) -> TemporalState | None:
    """Re-read an item and build its TemporalState for write response feedback.

    Returns None if the item cannot be found (caller handles this).
    """
    raw = things.get(uuid)
    if raw is None:
        return None

    start = raw.get("start", "Anytime")
    start_date_str = raw.get("start_date")
    start_date = date.fromisoformat(start_date_str[:10]) if start_date_str else None
    status = raw.get("status", "incomplete")
    evening = bool(raw.get("evening", False))

    return TemporalState(
        start=start,
        start_date=start_date,
        derived_list=derive_list(start, start_date, status=status),
        status=status,
        evening=evening,
    )


def _verify_url_scheme_write(uuid: str, *, delay: float = 0.5) -> dict | None:
    """Wait for URL scheme to process, then re-read item from SQLite.

    Returns the raw dict from things.get(uuid), or None if not found.
    Used after fire-and-forget URL scheme operations.
    """
    time.sleep(delay)
    return things.get(uuid)


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
    - "evening" -> URL scheme with auth token (AppleScript has no evening slot)
    - "YYYY-MM-DD" -> schedule for that date
    - "anytime" -> move to list "Anytime" (clears start_date, sets start=Anytime)
    - "someday" -> move to list "Someday" (clears start_date, sets start=Someday)

    CRITICAL: "anytime" must map to move to list "Anytime", NOT Someday.
    """
    _validate_uuid(uuid)

    when_lower = when.lower().strip()

    if when_lower == "today":
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    schedule theToDo for (current date)
end tell
'''
        run_applescript(script)

    elif when_lower == "tomorrow":
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    schedule theToDo for ((current date) + 1 * days)
end tell
'''
        run_applescript(script)

    elif when_lower == "evening":
        token = things.token()
        if token is None:
            return ErrorResponse(
                error="NO_AUTH_TOKEN",
                message="Auth token not available. "
                "Enable Things URLs in Things > Settings > General.",
            )
        url = f"things:///update?id={uuid}&when=evening&auth-token={token}"
        subprocess.run(["open", url], capture_output=True, timeout=10)
        time.sleep(0.5)  # URL scheme is fire-and-forget; wait before verification

    elif when_lower == "anytime":
        # CRITICAL: move to list "Anytime", NOT "Someday"
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    move theToDo to list "Anytime"
end tell
'''
        run_applescript(script)

    elif when_lower == "someday":
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    move theToDo to list "Someday"
end tell
'''
        run_applescript(script)

    elif _DATE_RE.match(when_lower):
        target_date = date.fromisoformat(when_lower)
        date_block = _applescript_date_block("theDate", target_date)
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    {date_block}
    schedule theToDo for theDate
end tell
'''
        run_applescript(script)

    else:
        return ErrorResponse(
            error="INVALID_WHEN",
            message=f"Invalid when value: {when!r}. "
            "Expected: today, tomorrow, evening, anytime, someday, or YYYY-MM-DD.",
        )

    # Map when values to action strings
    when_to_action = {
        "today": "scheduled",
        "tomorrow": "scheduled",
        "evening": "scheduled_evening",
        "anytime": "moved_to_anytime",
        "someday": "moved_to_someday",
    }
    action = when_to_action.get(when_lower, "scheduled")

    # Verify write (CLAUDE.md rule 8)
    raw = things.get(uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Item {uuid} not found after scheduling.",
        )

    return SuccessResponse(
        uuid=uuid,
        message=f"Scheduled item for {when}.",
        action=action,
        temporal_state=_read_temporal_state(uuid),
    )


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

    Uses AppleScript with argv-based input sanitization for user strings.
    Checklist creation requires Phase 2 (URL scheme with auth token).

    The `when` parameter accepts: today, tomorrow, evening, anytime,
    someday, or YYYY-MM-DD. See models.WhenValue for semantics.
    """
    # Phase 2: checklist items require URL scheme
    if checklist_items:
        return ErrorResponse(
            error="NOT_IMPLEMENTED",
            message="Checklist creation requires URL scheme (Phase 2).",
        )

    # Validate UUIDs if provided
    if project_uuid is not None:
        _validate_uuid(project_uuid)
    if area_uuid is not None:
        _validate_uuid(area_uuid)

    # Create the to-do via AppleScript with argv for user strings
    script = '''
on run argv
    set theTitle to item 1 of argv
    set theNotes to item 2 of argv
    tell application "Things3"
        set newToDo to make new to do with properties {name:theTitle, notes:theNotes} at end of list "Inbox"
        return id of newToDo
    end tell
end run
'''
    new_uuid = run_applescript(script, title, notes or "")

    if not new_uuid:
        return ErrorResponse(
            error="CREATE_FAILED",
            message="AppleScript did not return a UUID for the new to-do.",
        )

    # Apply tags if provided
    if tags:
        tag_str = ", ".join(tags)
        tag_script = f'''
on run argv
    set theTags to item 1 of argv
    tell application "Things3"
        set theToDo to to do id "{new_uuid}"
        set tag names of theToDo to theTags
    end tell
end run
'''
        run_applescript(tag_script, tag_str)

    # Apply project assignment if provided
    if project_uuid is not None:
        project_script = f'''
tell application "Things3"
    set theToDo to to do id "{new_uuid}"
    set project of theToDo to project id "{project_uuid}"
end tell
'''
        run_applescript(project_script)

    # Apply area assignment if provided
    if area_uuid is not None:
        area_script = f'''
tell application "Things3"
    set theToDo to to do id "{new_uuid}"
    set area of theToDo to area id "{area_uuid}"
end tell
'''
        run_applescript(area_script)

    # Apply heading if provided (heading is within a project)
    if heading is not None and project_uuid is not None:
        heading_script = f'''
on run argv
    set theHeading to item 1 of argv
    tell application "Things3"
        set theToDo to to do id "{new_uuid}"
        move theToDo to beginning of to dos of project id "{project_uuid}" with heading theHeading
    end tell
end run
'''
        run_applescript(heading_script, heading)

    # Apply deadline if provided
    if deadline is not None:
        target_date = date.fromisoformat(deadline)
        date_block = _applescript_date_block("theDate", target_date)
        deadline_script = f'''
tell application "Things3"
    set theToDo to to do id "{new_uuid}"
    {date_block}
    set due date of theToDo to theDate
end tell
'''
        run_applescript(deadline_script)

    # Apply scheduling if when is provided
    if when is not None:
        schedule_result = schedule_item(uuid=new_uuid, when=when)
        if not schedule_result.success:
            return schedule_result

    # Verify creation (CLAUDE.md rule 8)
    raw = things.get(new_uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message="To-do not found after creation.",
        )

    return SuccessResponse(
        uuid=new_uuid,
        message=f"Created to-do: {title}",
        action="created",
        temporal_state=_read_temporal_state(new_uuid),
    )


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

    Without initial to-dos: AppleScript (synchronous, UUID returned).
    With initial to-dos: URL scheme add-project (fire-and-forget, title-based verification).
    Never schedule a project to Today -- only tasks get Today.
    """
    if area_uuid is not None:
        _validate_uuid(area_uuid)

    # Block Today scheduling for projects (CLAUDE.md rule 7)
    if when is not None and when.lower().strip() == "today":
        return ErrorResponse(
            error="INVALID_INPUT",
            message="Cannot schedule a project to Today. Only tasks get Today.",
        )

    if todos:
        # URL scheme path: add-project with to-dos JSON array
        params: dict[str, str] = {"title": title}
        if notes:
            params["notes"] = notes
        if tags:
            params["tags"] = ",".join(tags)
        if area_uuid:
            params["area-id"] = area_uuid
        if deadline:
            params["deadline"] = deadline
        if when:
            params["when"] = when
        params["to-dos"] = json.dumps([{"title": t} for t in todos])

        url = "things:///add-project?" + urllib.parse.urlencode(
            params, quote_via=urllib.parse.quote
        )
        subprocess.run(["open", url], capture_output=True, timeout=10)

        # URL scheme doesn't return UUID; title-based search after delay
        time.sleep(0.5)
        matches = things.tasks(search_query=title, type="project")
        if not matches:
            return ErrorResponse(
                error="VERIFY_FAILED",
                message="Project not found after creation via URL scheme.",
            )
        # Use the most recent match (last created)
        project = matches[-1]
        return SuccessResponse(
            uuid=project["uuid"],
            message=f"Created project: {title} (with {len(todos)} to-dos).",
            action="created",
            temporal_state=_read_temporal_state(project["uuid"]),
        )

    # AppleScript path: no todos, synchronous UUID return
    script = '''
on run argv
    set theTitle to item 1 of argv
    set theNotes to item 2 of argv
    tell application "Things3"
        set newProject to make new project with properties {name:theTitle, notes:theNotes}
        return id of newProject
    end tell
end run
'''
    new_uuid = run_applescript(script, title, notes or "")

    if not new_uuid:
        return ErrorResponse(
            error="CREATE_FAILED",
            message="AppleScript did not return a UUID for the new project.",
        )

    # Apply tags if provided
    if tags:
        tag_str = ", ".join(tags)
        tag_script = f'''
on run argv
    set theTags to item 1 of argv
    tell application "Things3"
        set theProject to to do id "{new_uuid}"
        set tag names of theProject to theTags
    end tell
end run
'''
        run_applescript(tag_script, tag_str)

    # Apply area assignment if provided
    if area_uuid is not None:
        area_script = f'''
tell application "Things3"
    set theProject to to do id "{new_uuid}"
    set area of theProject to area id "{area_uuid}"
end tell
'''
        run_applescript(area_script)

    # Apply deadline if provided
    if deadline is not None:
        target_date = date.fromisoformat(deadline)
        date_block = _applescript_date_block("theDate", target_date)
        deadline_script = f'''
tell application "Things3"
    set theProject to to do id "{new_uuid}"
    {date_block}
    set due date of theProject to theDate
end tell
'''
        run_applescript(deadline_script)

    # Apply scheduling if when is provided
    if when is not None:
        schedule_result = schedule_item(uuid=new_uuid, when=when)
        if not schedule_result.success:
            return schedule_result

    # Verify creation (CLAUDE.md rule 8)
    raw = things.get(new_uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message="Project not found after creation.",
        )

    return SuccessResponse(
        uuid=new_uuid,
        message=f"Created project: {title}",
        action="created",
        temporal_state=_read_temporal_state(new_uuid),
    )


def update_item(
    *,
    uuid: str,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    when: Optional[str] = None,
    deadline: Optional[str] = None,
    tags: Optional[str] = None,
    completed: Optional[bool] = None,
    canceled: Optional[bool] = None,
) -> SuccessResponse | ErrorResponse:
    """Update any field on an existing item.

    Uses AppleScript for property updates and scheduling.
    All user-supplied strings passed via argv, never embedded in script.

    Important: `completed` and `canceled` must be actual booleans.
    """
    _validate_uuid(uuid)

    # Handle scheduling separately via schedule_item (reuse logic)
    if when is not None:
        schedule_result = schedule_item(uuid=uuid, when=when)
        if not schedule_result.success:
            return schedule_result

    # Build argv list and script dynamically based on provided fields
    argv_items: list[str] = []
    script_lines: list[str] = []
    argv_index = 1

    if title is not None:
        argv_items.append(title)
        script_lines.append(f"set name of theToDo to item {argv_index} of argv")
        argv_index += 1

    if notes is not None:
        argv_items.append(notes)
        script_lines.append(f"set notes of theToDo to item {argv_index} of argv")
        argv_index += 1

    if tags is not None:
        argv_items.append(tags)
        script_lines.append(f"set tag names of theToDo to item {argv_index} of argv")
        argv_index += 1

    if completed is True:
        script_lines.append("set status of theToDo to completed")

    if canceled is True:
        script_lines.append("set status of theToDo to canceled")

    # Handle deadline: date string sets it, empty string clears it
    if deadline is not None:
        if deadline == "":
            script_lines.append("set due date of theToDo to missing value")
        else:
            target_date = date.fromisoformat(deadline)
            date_block = _applescript_date_block("theDate", target_date)
            # Date block goes before the tell block in the script
            script_lines.append(f"DATEBLOCK:{date_block}")
            script_lines.append("set due date of theToDo to theDate")

    # If there are property updates to make, execute them
    if script_lines:
        # Separate date blocks from tell-block lines
        date_blocks: list[str] = []
        tell_lines: list[str] = []
        for line in script_lines:
            if line.startswith("DATEBLOCK:"):
                date_blocks.append(line[len("DATEBLOCK:"):])
            else:
                tell_lines.append(line)

        date_block_str = "\n".join(date_blocks)
        tell_body = "\n    ".join(tell_lines)

        if argv_items:
            script = f'''
on run argv
    {date_block_str}
    tell application "Things3"
        set theToDo to to do id "{uuid}"
        {tell_body}
    end tell
end run
'''
        else:
            script = f'''
{date_block_str}
tell application "Things3"
    set theToDo to to do id "{uuid}"
    {tell_body}
end tell
'''
        run_applescript(script, *argv_items)

    # Verify write (CLAUDE.md rule 8)
    raw = things.get(uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Item {uuid} not found after update.",
        )

    parts = []
    if title is not None:
        parts.append("title")
    if notes is not None:
        parts.append("notes")
    if when is not None:
        parts.append(f"when={when}")
    if deadline is not None:
        parts.append("deadline")
    if tags is not None:
        parts.append("tags")
    if completed is True:
        parts.append("completed")
    if canceled is True:
        parts.append("canceled")

    return SuccessResponse(
        uuid=uuid,
        message=f"Updated item: {', '.join(parts)}.",
        action="updated",
        temporal_state=_read_temporal_state(uuid),
    )


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
    _validate_uuid(uuid)

    if project_uuid is None and area_uuid is None:
        return ErrorResponse(
            error="INVALID_INPUT",
            message="Provide either project_uuid or area_uuid.",
        )

    if project_uuid is not None and area_uuid is not None:
        return ErrorResponse(
            error="INVALID_INPUT",
            message="Provide project_uuid or area_uuid, not both.",
        )

    if project_uuid is not None:
        _validate_uuid(project_uuid)
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    set project of theToDo to project id "{project_uuid}"
end tell
'''
        action = "moved_to_project"
    else:
        _validate_uuid(area_uuid)
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    set area of theToDo to area id "{area_uuid}"
end tell
'''
        action = "moved_to_area"

    run_applescript(script)

    # Verify write (CLAUDE.md rule 8) -- AppleScript is synchronous, no delay
    raw = things.get(uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message="Item not found after move.",
        )

    return SuccessResponse(
        uuid=uuid,
        message=f"Item {action.replace('_', ' ')}.",
        action=action,
        temporal_state=_read_temporal_state(uuid),
    )


def delete_item(*, uuid: str) -> SuccessResponse | ErrorResponse:
    """Move an item to the trash.

    Requires auth token. Verifies deletion actually happened (the current
    MCP silently reports success even without auth).
    """
    raise NotImplementedError("TODO: URL scheme update with auth token check")
