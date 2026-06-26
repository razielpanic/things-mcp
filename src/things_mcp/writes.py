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

# Things 3 uses base62 identifiers (alphanumeric, no dashes). They are usually
# 22 chars, but when the high-order base62 digit is zero it gets dropped, so
# real IDs surfaced by the read tools are sometimes 21 chars (e.g. the GTD area
# 'eCNdD4xfM23J1nBop9ixv' and tasks like 'ufGzuLaRsMNZDPRPB3yPj'). Accept both
# lengths — rejecting 21-char IDs made DB-real items unreachable by write ops
# (dev-issue 2026-04-20 / upstream #4). The injection guard is the [A-Za-z0-9]
# character class (no quotes/specials reach AppleScript); length is only a
# sanity bound.
_UUID_RE = re.compile(r"^[A-Za-z0-9]{21,22}$")

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
    try:
        result = subprocess.run(
            ["osascript", "-", *args],
            input=script,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Things 3 is not responding (timeout after 10s)")
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
    # Set day to 1 BEFORE setting month/year. Mutating an AppleScript date in
    # place rolls the month forward when the current day-of-month exceeds the
    # target month's length (e.g. run on the 31st, "set month to 6" overflows
    # June -> July, landing the date a month late). The 1st is valid in every
    # month, so neutralizing the day first makes the month assignment safe; the
    # real day is applied last.
    return (
        f"set {var} to current date\n"
        f"set day of {var} to 1\n"
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


def _reopen_if_unexpectedly_closed(
    uuid: str, pre_status: str | None, post_status: str | None
) -> ErrorResponse | None:
    """Guard against silent completion: restore an item the write closed by accident.

    No write here (scheduling, notes, retitle, move, deadline) should ever flip an
    open item to completed/canceled — only an explicit completed/canceled request
    may. If a write does so anyway, reopen the item and return an error so the
    caller learns of the anomaly instead of silently logbooking an active task.

    Returns an ErrorResponse describing the recovery, or None if status is fine.
    """
    if post_status in ("completed", "canceled") and pre_status not in (
        "completed",
        "canceled",
    ):
        try:
            run_applescript(
                f'tell application "Things3" to set status of (to do id "{uuid}") to open'
            )
        except RuntimeError:
            pass
        return ErrorResponse(
            error="UNEXPECTED_STATUS_CHANGE",
            message=(
                f"Write unexpectedly set status to {post_status!r} without a "
                "completed/canceled request; the item was restored to open. "
                "Re-check the item — no other field change is guaranteed."
            ),
        )
    return None


def _verify_url_scheme_write(uuid: str, *, delay: float = 0.5) -> dict | None:
    """Wait for URL scheme to process, then re-read item from SQLite.

    Returns the raw dict from things.get(uuid), or None if not found.
    Used after fire-and-forget URL scheme operations.
    """
    time.sleep(delay)
    return things.get(uuid)


# ---------------------------------------------------------------------------
# Blocker-relation helpers (gated / gates)
#
# Things has no native task-to-task relation. We synthesize "B blocks D" with a
# tag + bidirectional deep links in notes:
#   - the dependent D gets the `gated` tag and a `**Gated by:**` block listing
#     each blocker as a named link `[title](things:///show?id=uuid)`
#   - the blocker B gets a reciprocal `**Gates:**` block listing each dependent
#
# Two correctness traps drive the read->splice->write-back design:
#   - `set tag names` REPLACES the whole tag set, so adding `gated` must merge
#     into the existing tags (never set just {"gated"}).
#   - `set notes` REPLACES the whole notes body, so updating a managed block
#     must parse the current notes, splice, and write the whole string back.
# Both operations are kept idempotent by keying entries on uuid.
# ---------------------------------------------------------------------------

_GATED_TAG = "gated"

# The bold labels ARE the section structure: Things' notes render a tiny
# Markdown subset (bold/italic/strikethrough/named links) -- no headings, no
# horizontal rules -- so these labels are how a managed block is located.
_REL_GATED_BY = "**Gated by:**"
_REL_GATES = "**Gates:**"

# One managed entry: a named link to a Things item. The greedy title group
# backtracks to the final `](things:///show?id=` so a title containing brackets
# or parens still parses; the base62 uuid class bounds the id.
_REL_ENTRY_RE = re.compile(
    r"^\[(?P<title>.*)\]\(things:///show\?id=(?P<uuid>[A-Za-z0-9]{21,22})\)$"
)


def _set_notes(uuid: str, notes: str) -> None:
    """Set an item's notes via AppleScript (REPLACES the whole body).

    The value is passed via argv, never embedded in the script source.
    """
    script = f'''
on run argv
    set theNotes to item 1 of argv
    tell application "Things3"
        set theToDo to to do id "{uuid}"
        set notes of theToDo to theNotes
    end tell
end run
'''
    run_applescript(script, notes)


def _set_tag_names(uuid: str, tags: list[str]) -> None:
    """Set an item's full tag list via AppleScript (REPLACES all tags).

    Things' `set tag names` takes a comma-separated string and replaces the
    item's entire tag set, so callers must pass the already-merged list. An
    empty list clears all tags (`set tag names to ""`).
    """
    script = f'''
on run argv
    set theTags to item 1 of argv
    tell application "Things3"
        set theToDo to to do id "{uuid}"
        set tag names of theToDo to theTags
    end tell
end run
'''
    run_applescript(script, ", ".join(tags))


def _merge_tag(existing: list[str], tag: str) -> list[str]:
    """Return existing tags plus `tag`, order-preserving and deduped."""
    if tag in existing:
        return list(existing)
    return [*existing, tag]


def _drop_tag(existing: list[str], tag: str) -> list[str]:
    """Return existing tags minus `tag`, order-preserving."""
    return [t for t in existing if t != tag]


def _render_relation_block(label: str, entries: list[tuple[str, str]]) -> str:
    """Render a managed relation block as `label` + one named link per entry.

    `entries` is a list of (title, uuid). Returns "" when there are no entries
    (the caller drops the block entirely rather than leaving an empty label).
    """
    if not entries:
        return ""
    lines = [label]
    lines.extend(f"[{title}](things:///show?id={uuid})" for title, uuid in entries)
    return "\n".join(lines)


def _parse_relation_block(
    notes: str | None, label: str
) -> tuple[str, list[tuple[str, str]]]:
    """Split notes into (text_without_block, entries) for the given `label`.

    Locates the `label` line, consumes the contiguous following lines that are
    managed entries, and returns the notes with that block (and the single
    blank separator line preceding it, if any) removed, plus the parsed entries
    as (title, uuid) pairs in document order. If the label is absent, returns
    (notes, []). Inverse of _render_relation_block + _splice_notes.
    """
    if not notes:
        return "", []
    lines = notes.split("\n")
    out: list[str] = []
    entries: list[tuple[str, str]] = []
    i = 0
    n = len(lines)
    found = False
    while i < n:
        if not found and lines[i].strip() == label:
            found = True
            # Drop the one blank separator line we own above the block.
            if out and out[-1].strip() == "":
                out.pop()
            i += 1
            while i < n:
                m = _REL_ENTRY_RE.match(lines[i].strip())
                if m is None:
                    break
                entries.append((m.group("title"), m.group("uuid")))
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), entries


def _splice_notes(text: str, block: str) -> str:
    """Append a managed `block` to user `text`, normalized to the end.

    One blank line separates user text from the block. An empty block returns
    the user text alone (with trailing whitespace stripped). Idempotent given a
    `text` already stripped of the block (as _parse_relation_block returns).
    """
    text = (text or "").rstrip()
    if not block:
        return text
    if text:
        return f"{text}\n\n{block}"
    return block


def _has_uuid(entries: list[tuple[str, str]], uuid: str) -> bool:
    """True if any (title, uuid) entry matches `uuid`."""
    return any(u == uuid for _, u in entries)


def _relation_present(notes: str | None, label: str, uuid: str) -> bool:
    """True if the managed block for `label` references `uuid`."""
    _, entries = _parse_relation_block(notes, label)
    return _has_uuid(entries, uuid)


def _partial_link_message(
    blocker_uuid: str, dependent_uuid: str, exc: Exception | None = None
) -> str:
    """Error text for a half-wired link (dependent side done, blocker side not)."""
    detail = f": {exc}" if exc is not None else ""
    return (
        f"Dependent {dependent_uuid} is wired (gated tag + 'Gated by' link), but "
        f"the blocker side ({blocker_uuid} 'Gates' link) did not land{detail}. "
        "Re-run link_blocker to complete -- it is idempotent."
    )


def _unwire_gates_side(blocker_uuid: str, dependent_uuid: str) -> bool:
    """Remove the dependent from the blocker's `**Gates:**` block.

    Re-reads the blocker, drops the dependent's entry (collapsing the block if
    it empties), and writes the spliced notes back. Returns False if the blocker
    no longer exists (nothing to unwire), True otherwise. Idempotent: a blocker
    that never referenced the dependent is left untouched.
    """
    blocker = things.get(blocker_uuid)
    if blocker is None:
        return False
    notes = blocker.get("notes") or ""
    text, entries = _parse_relation_block(notes, _REL_GATES)
    remaining = [(t, u) for (t, u) in entries if u != dependent_uuid]
    if len(remaining) != len(entries):
        _set_notes(
            blocker_uuid, _splice_notes(text, _render_relation_block(_REL_GATES, remaining))
        )
    return True


def _unwire_gated_by_side(dependent_uuid: str, blocker_uuid: str) -> bool:
    """Remove the blocker from the dependent's `**Gated by:**` block.

    Re-reads the dependent, drops the blocker's entry (collapsing the block if
    it empties), and -- only when no blockers remain -- drops the `gated` tag
    (merge-aware: the dependent's other tags are preserved). Returns False if
    the dependent no longer exists, True otherwise. Idempotent.
    """
    dependent = things.get(dependent_uuid)
    if dependent is None:
        return False
    notes = dependent.get("notes") or ""
    text, entries = _parse_relation_block(notes, _REL_GATED_BY)
    remaining = [(t, u) for (t, u) in entries if u != blocker_uuid]
    if len(remaining) != len(entries):
        _set_notes(
            dependent_uuid,
            _splice_notes(text, _render_relation_block(_REL_GATED_BY, remaining)),
        )
    # Drop `gated` only when the last blocker is gone.
    if not remaining:
        tags = dependent.get("tags") or []
        if _GATED_TAG in tags:
            _set_tag_names(dependent_uuid, _drop_tag(tags, _GATED_TAG))
    return True


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

    # Capture prior status for the silent-completion guard (scheduling must
    # never change completion status).
    _pre = things.get(uuid)
    pre_status = _pre.get("status") if isinstance(_pre, dict) else None

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
        # URL scheme is fire-and-forget; wait before verification
        if _verify_url_scheme_write(uuid) is None:
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=f"Item {uuid} not found after scheduling.",
            )

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
        try:
            target_date = date.fromisoformat(when_lower)
        except ValueError:
            return ErrorResponse(
                error="INVALID_DATE",
                message=f"Invalid date: {when_lower!r}. Expected a valid YYYY-MM-DD date.",
            )
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

    guard = _reopen_if_unexpectedly_closed(
        uuid, pre_status, raw.get("status") if isinstance(raw, dict) else None
    )
    if guard is not None:
        return guard

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

    Without checklist_items: AppleScript (synchronous, UUID returned).
    With checklist_items: things:///json endpoint (single-call create with
    checklist embedded, title-based verification — no auth token required).

    The `when` parameter accepts: today, tomorrow, evening, anytime,
    someday, or YYYY-MM-DD. See models.WhenValue for semantics.
    """
    # Validate UUIDs if provided
    if project_uuid is not None:
        _validate_uuid(project_uuid)
    if area_uuid is not None:
        _validate_uuid(area_uuid)

    # Validate deadline early so both paths share the same error response
    if deadline is not None:
        try:
            date.fromisoformat(deadline)
        except ValueError:
            return ErrorResponse(
                error="INVALID_DATE",
                message=f"Invalid deadline: {deadline!r}. Expected a valid YYYY-MM-DD date.",
            )

    if checklist_items:
        # JSON endpoint: reliable single-call path for todo + checklist.
        # Avoids the AppleScript create -> trash -> json recreate cycle
        # (AppleScript cannot attach checklists, and the URL scheme update
        # endpoint would require an auth token).
        attrs: dict = {"title": title}
        if notes:
            attrs["notes"] = notes
        if when:
            attrs["when"] = when
        if deadline:
            attrs["deadline"] = deadline
        if tags:
            attrs["tags"] = tags
        if project_uuid:
            attrs["list-id"] = project_uuid
        elif area_uuid:
            attrs["list-id"] = area_uuid
        if heading:
            attrs["heading"] = heading
        attrs["checklist-items"] = [
            {"type": "checklist-item", "attributes": {"title": item}}
            for item in checklist_items
        ]

        data = [{"type": "to-do", "attributes": attrs}]
        data_json = json.dumps(data, separators=(",", ":"))
        encoded = urllib.parse.quote(data_json, safe="")
        url = f"things:///json?data={encoded}"
        subprocess.run(
            ["osascript", "-e", f'open location "{url}"'],
            capture_output=True,
            timeout=10,
        )

        # URL scheme doesn't return UUID; title-based search after delay
        time.sleep(0.5)
        matches = things.tasks(search_query=title)
        json_todo = next(
            (m for m in matches if m.get("title") == title),
            None,
        )
        if not json_todo:
            return ErrorResponse(
                error="VERIFY_FAILED",
                message="To-do not found after creation via URL scheme.",
            )

        new_uuid = json_todo["uuid"]

        # Verify checklist actually landed (URL scheme is fire-and-forget)
        full = things.tasks(uuid=new_uuid, include_items=True)
        has_checklist = (
            isinstance(full, dict) and bool(full.get("checklist"))
        )
        checklist_warning = (
            "" if has_checklist
            else " Warning: checklist items may not have been added."
        )

        return SuccessResponse(
            uuid=new_uuid,
            message=(
                f"Created to-do: {title} "
                f"(with {len(checklist_items)} checklist items)."
                f"{checklist_warning}"
            ),
            action="created",
            temporal_state=_read_temporal_state(new_uuid),
        )

    # AppleScript path: no checklist, synchronous UUID return
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

    # Apply deadline if provided (already validated above)
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
    With initial to-dos: URL scheme things:///json endpoint (fire-and-forget, title-based verification, no auth token required).
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
        # JSON endpoint: reliable path for project + initial todos
        attrs: dict = {"title": title}
        if notes:
            attrs["notes"] = notes
        if tags:
            attrs["tags"] = tags
        if area_uuid:
            attrs["area-id"] = area_uuid
        if deadline:
            attrs["deadline"] = deadline
        if when:
            attrs["when"] = when
        attrs["items"] = [{"type": "to-do", "attributes": {"title": t}} for t in todos]
        data = [{"type": "project", "attributes": attrs}]
        data_json = json.dumps(data, separators=(",", ":"))
        encoded = urllib.parse.quote(data_json, safe="")
        url = f"things:///json?data={encoded}"
        subprocess.run(
            ["osascript", "-e", f'open location "{url}"'],
            capture_output=True,
            timeout=10,
        )

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
        try:
            target_date = date.fromisoformat(deadline)
        except ValueError:
            return ErrorResponse(
                error="INVALID_DATE",
                message=f"Invalid deadline: {deadline!r}. Expected a valid YYYY-MM-DD date.",
            )
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
    project_uuid: Optional[str] = None,
    area_uuid: Optional[str] = None,
) -> SuccessResponse | ErrorResponse:
    """Update any field on an existing item.

    Uses AppleScript for property updates and scheduling.
    All user-supplied strings passed via argv, never embedded in script.

    Important: `completed` and `canceled` must be actual booleans.

    `project_uuid` / `area_uuid` move the item's structural context (same
    operation as move_to_context). Provide at most one.
    """
    _validate_uuid(uuid)

    if project_uuid is not None and area_uuid is not None:
        return ErrorResponse(
            error="INVALID_INPUT",
            message="Provide project_uuid or area_uuid, not both.",
        )
    if project_uuid is not None:
        _validate_uuid(project_uuid)
    if area_uuid is not None:
        _validate_uuid(area_uuid)

    # Capture prior status for the silent-completion guard below.
    _pre = things.get(uuid)
    pre_status = _pre.get("status") if isinstance(_pre, dict) else None

    # Handle scheduling separately via schedule_item (reuse logic)
    if when is not None:
        schedule_result = schedule_item(uuid=uuid, when=when)
        if not schedule_result.success:
            return schedule_result

    # Structural move to a project or area. update_item previously had no
    # project_uuid/area_uuid params, so callers asking it to file a task into a
    # project got a silent no-op. Delegate to the same AppleScript that
    # move_to_context uses (which works correctly).
    if project_uuid is not None:
        run_applescript(
            f'tell application "Things3" to set project of '
            f'(to do id "{uuid}") to project id "{project_uuid}"'
        )
    elif area_uuid is not None:
        run_applescript(
            f'tell application "Things3" to set area of '
            f'(to do id "{uuid}") to area id "{area_uuid}"'
        )

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
            try:
                target_date = date.fromisoformat(deadline)
            except ValueError:
                return ErrorResponse(
                    error="INVALID_DATE",
                    message=f"Invalid deadline: {deadline!r}. Expected a valid YYYY-MM-DD date.",
                )
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
    if project_uuid is not None:
        parts.append("project")
    if area_uuid is not None:
        parts.append("area")

    # Guard: a routine update (notes/title/tags/deadline/reschedule/move) must
    # never silently complete or cancel an open item. If it did, reopen and
    # report rather than logbooking an active task.
    if completed is not True and canceled is not True:
        guard = _reopen_if_unexpectedly_closed(
            uuid, pre_status, raw.get("status") if isinstance(raw, dict) else None
        )
        if guard is not None:
            return guard

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
    """Move an item to the trash via AppleScript.

    Uses AppleScript `move to list "Trash"` (no auth token needed).
    Verifies deletion by confirming the item's `trashed` field is True.
    Items in Trash remain in the SQLite database — they are not deleted
    from disk — so existence is not a valid trash check.
    """
    _validate_uuid(uuid)

    # Verify item exists before trashing
    raw = things.get(uuid)
    if raw is None:
        return ErrorResponse(error="NOT_FOUND", message=f"Item {uuid} not found.")

    script = f'''
tell application "Things3"
    move (to do id "{uuid}") to list "Trash"
end tell
'''
    run_applescript(script)

    # Verify: Things 3 items in Trash remain in the SQLite database with
    # trashed=True. Checking `raw_after is not None` would always fail the
    # verification because trashed items still exist. Check the `trashed`
    # field instead.
    raw_after = things.get(uuid)
    if raw_after is None or not (
        isinstance(raw_after, dict) and raw_after.get("trashed")
    ):
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Item {uuid} was not moved to trash.",
        )

    return SuccessResponse(
        uuid=uuid,
        message="Item moved to Trash.",
        action="trashed",
        temporal_state=None,
    )


def link_blocker(
    *,
    blocker_uuid: str,
    dependent_uuid: str,
) -> SuccessResponse | ErrorResponse:
    """Wire a 'blocked by' relation: blocker_uuid blocks dependent_uuid.

    Atomically, on success:
      1. Merge the `gated` tag into the dependent's existing tags.
      2. Ensure the dependent's `**Gated by:**` block links to the blocker.
      3. Ensure the blocker's `**Gates:**` block links to the dependent.

    Idempotent (a second identical call writes nothing) and many-to-many (a
    dependent may be gated by several blockers; a blocker's `**Gates:**` grows).
    The dependent side is wired and verified first, then the blocker side: if
    the blocker side fails, the dependent is left correctly marked blocked and a
    PARTIAL_LINK error is returned (re-running completes it).
    """
    _validate_uuid(blocker_uuid)
    _validate_uuid(dependent_uuid)
    if blocker_uuid == dependent_uuid:
        return ErrorResponse(
            error="INVALID_INPUT",
            message="A task cannot block itself.",
        )

    blocker = things.get(blocker_uuid)
    if blocker is None:
        return ErrorResponse(
            error="NOT_FOUND", message=f"Blocker {blocker_uuid} not found."
        )
    dependent = things.get(dependent_uuid)
    if dependent is None:
        return ErrorResponse(
            error="NOT_FOUND", message=f"Dependent {dependent_uuid} not found."
        )

    blocker_title = blocker.get("title") or ""
    dependent_title = dependent.get("title") or ""

    # ---- Side 1: dependent gets `gated` + a 'Gated by' link to the blocker ----
    dep_tags = dependent.get("tags") or []
    merged_tags = _merge_tag(dep_tags, _GATED_TAG)
    if merged_tags != dep_tags:
        _set_tag_names(dependent_uuid, merged_tags)

    dep_notes = dependent.get("notes") or ""
    dep_text, dep_entries = _parse_relation_block(dep_notes, _REL_GATED_BY)
    if not _has_uuid(dep_entries, blocker_uuid):
        dep_entries.append((blocker_title, blocker_uuid))
        new_dep_notes = _splice_notes(
            dep_text, _render_relation_block(_REL_GATED_BY, dep_entries)
        )
        if new_dep_notes != dep_notes:
            _set_notes(dependent_uuid, new_dep_notes)

    # Verify side 1 (CLAUDE.md rule 8) before touching the blocker side.
    dep_after = things.get(dependent_uuid)
    if (
        dep_after is None
        or _GATED_TAG not in (dep_after.get("tags") or [])
        or not _relation_present(dep_after.get("notes"), _REL_GATED_BY, blocker_uuid)
    ):
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=(
                f"Failed to wire the dependent side of "
                f"{blocker_uuid} -> {dependent_uuid}."
            ),
        )

    # ---- Side 2: blocker gets a 'Gates' link to the dependent ----
    blk_notes = blocker.get("notes") or ""
    blk_text, blk_entries = _parse_relation_block(blk_notes, _REL_GATES)
    try:
        if not _has_uuid(blk_entries, dependent_uuid):
            blk_entries.append((dependent_title, dependent_uuid))
            new_blk_notes = _splice_notes(
                blk_text, _render_relation_block(_REL_GATES, blk_entries)
            )
            if new_blk_notes != blk_notes:
                _set_notes(blocker_uuid, new_blk_notes)
    except RuntimeError as exc:
        return ErrorResponse(
            error="PARTIAL_LINK",
            message=_partial_link_message(blocker_uuid, dependent_uuid, exc),
        )

    # Verify side 2 (CLAUDE.md rule 8).
    blk_after = things.get(blocker_uuid)
    if blk_after is None or not _relation_present(
        blk_after.get("notes"), _REL_GATES, dependent_uuid
    ):
        return ErrorResponse(
            error="PARTIAL_LINK",
            message=_partial_link_message(blocker_uuid, dependent_uuid),
        )

    return SuccessResponse(
        uuid=dependent_uuid,
        message=(
            f"Linked: {dependent_title!r} is gated by {blocker_title!r}."
        ),
        action="linked_blocker",
        temporal_state=_read_temporal_state(dependent_uuid),
    )


def unlink_blocker(
    *,
    blocker_uuid: str,
    dependent_uuid: str,
) -> SuccessResponse | ErrorResponse:
    """Remove a 'blocked by' relation: blocker_uuid no longer blocks dependent.

    The inverse of link_blocker, for manual/explicit resolution:
      1. Drop the dependent from the blocker's `**Gates:**` block.
      2. Drop the blocker from the dependent's `**Gated by:**` block.
      3. Drop the `gated` tag from the dependent ONLY if it has no remaining
         blockers (merge-aware: other tags are preserved).

    Idempotent and tolerant: an item that no longer exists has its side skipped
    (its counterpart is still cleaned), so this also unwinds a half-broken link.
    """
    _validate_uuid(blocker_uuid)
    _validate_uuid(dependent_uuid)

    blocker_exists = things.get(blocker_uuid) is not None
    dependent_exists = things.get(dependent_uuid) is not None
    if not blocker_exists and not dependent_exists:
        return ErrorResponse(
            error="NOT_FOUND",
            message=f"Neither {blocker_uuid} nor {dependent_uuid} exists.",
        )

    _unwire_gates_side(blocker_uuid, dependent_uuid)
    _unwire_gated_by_side(dependent_uuid, blocker_uuid)

    # Verify the unwiring (CLAUDE.md rule 8): neither side may still reference
    # the other, and a now-blocker-less dependent must have shed `gated`.
    blk_after = things.get(blocker_uuid)
    if blk_after is not None and _relation_present(
        blk_after.get("notes"), _REL_GATES, dependent_uuid
    ):
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Blocker {blocker_uuid} still gates {dependent_uuid} after unlink.",
        )

    dep_after = things.get(dependent_uuid)
    if dep_after is not None:
        if _relation_present(dep_after.get("notes"), _REL_GATED_BY, blocker_uuid):
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=(
                    f"Dependent {dependent_uuid} is still gated by "
                    f"{blocker_uuid} after unlink."
                ),
            )
        _, dep_blockers = _parse_relation_block(dep_after.get("notes"), _REL_GATED_BY)
        if not dep_blockers and _GATED_TAG in (dep_after.get("tags") or []):
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=(
                    f"Dependent {dependent_uuid} kept the `gated` tag despite "
                    "having no remaining blockers."
                ),
            )

    return SuccessResponse(
        uuid=dependent_uuid,
        message=f"Unlinked: {dependent_uuid} no longer gated by {blocker_uuid}.",
        action="unlinked_blocker",
        temporal_state=_read_temporal_state(dependent_uuid),
    )


def reconcile_completion(*, uuid: str) -> SuccessResponse | ErrorResponse:
    """Scrub every blocker relation a just-completed/canceled task is part of.

    Things has no event hooks, so relation cleanup is caller-triggered: invoke
    this when marking a task done. It scrubs both directions:
      - As a dependent: for each blocker in its `**Gated by:**` block, remove
        this task from that blocker's `**Gates:**` block (and clear its own
        `**Gated by:**` + `gated` tag).
      - As a blocker: for each task in its `**Gates:**` block, remove this task
        from that dependent's `**Gated by:**` block, dropping the dependent's
        `gated` tag if this was its last blocker (and clear its own `**Gates:**`).

    Idempotent and safe on a task with no relations (a no-op). Verifies via
    things.get that no dangling reference to this task survives (CLAUDE.md
    rule 8).
    """
    _validate_uuid(uuid)

    item = things.get(uuid)
    if item is None:
        return ErrorResponse(error="NOT_FOUND", message=f"Item {uuid} not found.")

    notes = item.get("notes") or ""
    _, blockers = _parse_relation_block(notes, _REL_GATED_BY)  # uuid as dependent
    _, dependents = _parse_relation_block(notes, _REL_GATES)  # uuid as blocker

    # As a dependent: detach uuid from each blocker, both directions.
    for _title, blocker_uuid in blockers:
        _unwire_gates_side(blocker_uuid, uuid)
        _unwire_gated_by_side(uuid, blocker_uuid)
    # As a blocker: detach uuid from each dependent, both directions.
    for _title, dependent_uuid in dependents:
        _unwire_gated_by_side(dependent_uuid, uuid)
        _unwire_gates_side(uuid, dependent_uuid)

    # Verify (CLAUDE.md rule 8): uuid carries no managed block, and no
    # counterpart still references it.
    after = things.get(uuid)
    if after is not None:
        after_notes = after.get("notes") or ""
        if _parse_relation_block(after_notes, _REL_GATED_BY)[1] or _parse_relation_block(
            after_notes, _REL_GATES
        )[1]:
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=f"Item {uuid} still carries a managed relation block after reconcile.",
            )
    for _title, blocker_uuid in blockers:
        counterpart = things.get(blocker_uuid)
        if counterpart is not None and _relation_present(
            counterpart.get("notes"), _REL_GATES, uuid
        ):
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=f"Blocker {blocker_uuid} still references {uuid} after reconcile.",
            )
    for _title, dependent_uuid in dependents:
        counterpart = things.get(dependent_uuid)
        if counterpart is not None and _relation_present(
            counterpart.get("notes"), _REL_GATED_BY, uuid
        ):
            return ErrorResponse(
                error="VERIFY_FAILED",
                message=f"Dependent {dependent_uuid} still references {uuid} after reconcile.",
            )

    count = len(blockers) + len(dependents)
    return SuccessResponse(
        uuid=uuid,
        message=f"Reconciled {count} blocker relation(s) for {uuid}.",
        action="reconciled",
        temporal_state=_read_temporal_state(uuid),
    )
