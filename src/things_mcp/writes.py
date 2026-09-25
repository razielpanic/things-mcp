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

import fcntl
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import things

from things_mcp import evening as evening_reader
from things_mcp import logbook as logbook_reader
from things_mcp import repeats
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
        stderr = result.stderr.strip()
        if "(301)" in stderr:
            raise RuntimeError(
                "Things refused to move this item (AppleScript error 301). "
                "Known cause: repeat-template content; check get_item's repeat "
                f"block. Raw: {stderr}"
            )
        raise RuntimeError(f"AppleScript error: {stderr}")
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
    # From the database, not from raw -- things.py carries no evening key, so
    # the old raw.get("evening", False) was a constant False. See evening.py.
    evening = evening_reader.is_evening(uuid)

    return TemporalState(
        start=start,
        start_date=start_date,
        derived_list=derive_list(
            start, start_date, status=status, unlogged=logbook_reader.is_unlogged(uuid)
        ),
        status=status,
        evening=evening,
    )


_DEFAULT_ANOMALY_LOG = Path.home() / ".things-mcp" / "status-anomalies.jsonl"


def anomaly_log_path() -> Path:
    """Where status anomalies are recorded.

    Resolved per call, not at import, and overridable via
    THINGS_MCP_ANOMALY_LOG -- the test suite points it at a temp file. Without
    that, running the tests appends fake UUIDs to the real diagnostic log and
    quietly destroys the only evidence the log exists to collect.
    """
    override = os.environ.get("THINGS_MCP_ANOMALY_LOG")
    return Path(override) if override else _DEFAULT_ANOMALY_LOG


def _log_status_anomaly(
    uuid: str, pre_status: str | None, post_status: str | None, source: str
) -> None:
    """Append one status anomaly to a durable JSONL log.

    Exists because an unexplained status change cannot be diagnosed after the
    fact: restoring the affected item overwrites its completed_date, and nothing
    else records the transition. A dated pre/post pair per event makes the next
    occurrence decidable instead of a suspicion. Best-effort -- a logging failure
    must never break a write.
    """
    try:
        log_path = anomaly_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": datetime.now().astimezone().isoformat(),
                        "uuid": uuid,
                        "source": source,
                        "pre_status": pre_status,
                        "post_status": post_status,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def census_path() -> Path:
    """Companion counter to the anomaly log: how many guarded writes have run.

    An empty anomaly log is not evidence on its own -- it reads the same whether
    the fault is gone or the tool simply went unused. Without a denominator there
    is no condition under which an open "does this still happen?" question can
    ever be closed, so it stays open forever and clutters the tracker.

    Overridable via THINGS_MCP_WRITE_CENSUS for the same reason as the log.
    """
    override = os.environ.get("THINGS_MCP_WRITE_CENSUS")
    return Path(override) if override else (Path.home() / ".things-mcp" / "write-census.json")


def _record_guarded_write(source: str) -> None:
    """Count one write that passed through the close check.

    Locked and written atomically. The first version was an unlocked
    read-modify-write ending in `write_text`, which truncates before it writes:
    a second process reading inside that window got partial JSON, the
    `except: data = {}` swallowed the error, and the census silently RESET to
    one write with `first` stamped to now -- erasing months of accumulation and
    the regime history with it, leaving no trace that it had happened. Measured
    at 4 processes x 400 writes reporting 15. Two MCP server processes run
    concurrently on this machine, so the window is real, and a crash mid-write
    did the same thing.

    Best-effort by design: a census that cannot be written must never break a
    Things write. But it must not silently destroy itself either.
    """
    try:
        p = census_path()
        p.parent.mkdir(parents=True, exist_ok=True)

        lock_path = p.with_suffix(p.suffix + ".lock")
        with open(lock_path, "a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                # json.loads succeeds on `null` and `[]`; the .get() below would
                # then raise AttributeError into the outer handler and the file
                # would never be rewritten, freezing the count forever.
                if not isinstance(data, dict):
                    data = {}

                now = datetime.now().astimezone().isoformat()
                data["writes"] = int(data.get("writes", 0)) + 1
                data.setdefault("first", now)
                data["last"] = now
                by = data.setdefault("by_source", {})
                if not isinstance(by, dict):
                    by = data["by_source"] = {}
                by[source] = int(by.get(source, 0)) + 1

                # Which write paths this count covers. Without it a census
                # spanning an instrumentation change reads as one clean sample
                # when it is really two, and the older half was blind to paths
                # the question asks about.
                coverage = sorted(GUARDED_WRITE_TOOLS)
                regimes = data.get("regimes")
                if not isinstance(regimes, list):
                    regimes = []
                prior = data.get("coverage")
                if prior is None and int(data.get("writes", 1)) > 1:
                    prior = ["(unrecorded — census predates coverage stamping)"]
                if prior is not None and prior != coverage:
                    # This regime's OWN count, not the running total. The first
                    # version stored `writes - 1`, which is right only for the
                    # first rollover; every later one over-credited the closing
                    # regime with every write that preceded it. That inflates a
                    # narrow, half-blind regime into looking well-observed --
                    # the direction that makes a bogus CLOSE more likely, which
                    # is the exact failure the counter exists to prevent.
                    already = sum(
                        int(r.get("writes", 0))
                        for r in regimes
                        if isinstance(r, dict)
                    )
                    regimes.append(
                        {
                            "coverage": prior,
                            "writes": max(int(data["writes"]) - 1 - already, 0),
                            "until": now,
                        }
                    )
                    data["regimes"] = regimes
                data["coverage"] = coverage

                # Atomic: write beside the target, then rename over it. A reader
                # sees the old file or the new one, never a truncated one.
                tmp = p.with_suffix(p.suffix + ".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, p)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def _check_unexpected_close(
    uuid: str,
    pre_status: str | None,
    post_status: str | None,
    source: str = "write",
    already_written: str | None = None,
) -> ErrorResponse | None:
    """Report — do not repair — an item that closed during a non-closing write.

    No write here (scheduling, notes, retitle, move, deadline) requests a status
    change, so seeing one means something else closed the item inside the write
    window.

    This used to reopen the item automatically. That is wrong whenever the
    Things UI is in use alongside the MCP: the check cannot distinguish a
    deliberate checkbox click from a tool-caused change, so auto-reopening
    silently un-completes tasks that were finished on purpose — a guard against
    data loss that causes data loss.

    So: leave the item exactly as found, record the transition, and tell the
    caller. A wrongly-closed item that is visible beats a deliberately-closed
    item silently reopened. Recovery is one click; an unnoticed reopen is a task
    that quietly returns from the dead.

    Attribution caution: a hit here does NOT establish that this tool closed the
    item. `schedule_item` has no cancellation path at all, so a `canceled` result
    cannot originate here. Treat every hit as unattributed until the anomaly log
    and the item's own completed_date say otherwise.

    Returns an ErrorResponse describing what was seen, or None if status is fine.
    """
    if post_status in ("completed", "canceled") and pre_status not in (
        "completed",
        "canceled",
    ):
        _record_guarded_write(source)
        _log_status_anomaly(uuid, pre_status, post_status, source)
        return ErrorResponse(
            error="UNEXPECTED_STATUS_CHANGE",
            message=(
                f"Item status went {pre_status!r} -> {post_status!r} during a write "
                "that did not request it. "
                + (
                    f"{already_written} "
                    if already_written
                    else "The item was LEFT AS-IS, not modified. "
                )
                + "The most common cause is a concurrent edit in the Things UI. "
                "Check the item and set it how you want it; other field changes "
                f"from this call are not guaranteed. Logged to {anomaly_log_path()}."
            ),
        )
    _record_guarded_write(source)
    return None


# Every write tool the server exposes must either route through
# _check_unexpected_close -- so it lands in the census and any close during the
# write is logged -- or declare below why it cannot.
#
# The reason this is enforced rather than remembered: an empty anomaly log is
# only evidence if the denominator covers the write surface the question asks
# about. things-mcp#27 found the census watching 2 of at least 5 write paths, so
# 300 clean writes would have printed "zero anomalies" while three surfaces were
# never observed -- a falsification that cannot be defended, because the
# numerator was blind exactly where the denominator was.
#
# The default is "must be guarded". Adding a write tool without a guard fails
# tests/test_writes_unit.py::test_every_exposed_write_tool_is_guarded rather
# than silently shrinking the census.
GUARDED_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "schedule_item",
        "update_item",
        "move_to_context",
        "link_blocker",
        "unlink_blocker",
        "reconcile_completion",
    }
)

UNGUARDED_WRITE_TOOLS: dict[str, str] = {
    "create_todo": (
        "Creates the item. There is no prior status, so there is no "
        "close-during-write to detect."
    ),
    "create_project": (
        "Creates the item. There is no prior status, so there is no "
        "close-during-write to detect."
    ),
    "delete_item": (
        "Trashing the item is the requested effect. A status change here is "
        "the point of the call, not an anomaly."
    ),
}


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
#   - the dependent D gets the `gated` tag and a `Gated by:` block listing each
#     blocker as a title line + a `things:///show?id=uuid` deep-link line
#   - the blocker B gets a reciprocal `Gates:` block listing each dependent
#
# Two correctness traps drive the read->splice->write-back design:
#   - `set tag names` REPLACES the whole tag set, so adding `gated` must merge
#     into the existing tags (never set just {"gated"}).
#   - `set notes` REPLACES the whole notes body, so updating a managed block
#     must parse the current notes, splice, and write the whole string back.
# Both operations are kept idempotent by keying entries on uuid.
# ---------------------------------------------------------------------------

_GATED_TAG = "gated"

# Things 3 notes do NOT render Markdown -- no bold, no `[label](url)` named
# links (verified in-app 2026-06-26: the literal `**` and `[]()` show). Things
# only auto-linkifies a bare URL. So the managed block is plain text: a
# `Gated by:` / `Gates:` label line, then per blocker a title line (kept
# human-scannable) followed by the bare `things:///show?id=<uuid>` deep link on
# its own line (which Things makes clickable). The label lines locate a block.
_REL_GATED_BY = "Gated by:"
_REL_GATES = "Gates:"

# The deep-link line of a managed entry. The base62 uuid class bounds the id;
# the line immediately above it is the entry's (free-text) title.
_REL_URL_RE = re.compile(r"^things:///show\?id=(?P<uuid>[A-Za-z0-9]{21,22})$")


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
    """Render a managed relation block: `label`, then a title line + a bare
    deep-link line for each entry.

    `entries` is a list of (title, uuid). Returns "" when there are no entries
    (the caller drops the block entirely rather than leaving an empty label).
    The bare URL line is what Things auto-linkifies; the title stays human
    scannable on its own line (Things renders neither bold nor `[label](url)`).
    """
    if not entries:
        return ""
    lines = [label]
    for title, uuid in entries:
        lines.append(title)
        lines.append(f"things:///show?id={uuid}")
    return "\n".join(lines)


def _parse_relation_block(
    notes: str | None, label: str
) -> tuple[str, list[tuple[str, str]]]:
    """Split notes into (text_without_block, entries) for the given `label`.

    Locates the `label` line, then consumes the contiguous (title line, deep-link
    line) pairs that follow, and returns the notes with that block (and the
    single blank separator line preceding it, if any) removed, plus the parsed
    entries as (title, uuid) pairs in document order. If the label is absent,
    returns (notes, []). Inverse of _render_relation_block + _splice_notes.
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
            # Consume (title, url) pairs: a title line immediately backed by a
            # bare deep-link line. Stop at the first line with no URL beneath it.
            while i + 1 < n:
                m = _REL_URL_RE.match(lines[i + 1].strip())
                if m is None:
                    break
                entries.append((lines[i].strip(), m.group("uuid")))
                i += 2
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
    """Remove the dependent from the blocker's `Gates:` block.

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
    """Remove the blocker from the dependent's `Gated by:` block.

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


def _refuse_repeat_template(uuid: str) -> ErrorResponse | None:
    """Typed refusal for a move Things will reject on repeat-template content.

    Things answers `move`/`schedule` on a repeating template, or on a to-do
    inside a repeating project template, with AppleScript error 301 ("Cannot
    move to-do"). Checking first turns that into an error a caller can act
    on, instead of a raw osascript string mid-batch (things-mcp#26).
    Returns None when the write may proceed.
    """
    info = repeats.repeat_info(uuid)
    # Only the roles Things refuses. An instance is an ordinary to-do, and
    # "unknown" is a failed read -- refusing it would let a busy database
    # block every write; run_applescript still maps a real 301 to text.
    if info is None or info.role not in ("template", "template_child"):
        return None
    tpl = things.get(info.template_uuid) if info.template_uuid else None
    name = f"'{tpl['title']}'" if isinstance(tpl, dict) and tpl.get("title") else info.template_uuid
    what = "is a repeating template" if info.role == "template" else f"belongs to the repeating project template {name}"
    return ErrorResponse(
        error="REPEAT_TEMPLATE",
        message=f"Item {uuid} {what}. Things does not allow moving or scheduling "
        "repeat-template content, and no scripted surface can edit repeats. "
        "Change it in Things by editing the repeating project, or act on the "
        "generated copy instead. Safe to skip in a batch.",
    )


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
    - "inbox" -> move to list "Inbox" (clears start_date, sets start=Inbox)

    CRITICAL: "anytime" must map to move to list "Anytime", NOT Someday.
    """
    _validate_uuid(uuid)

    refused = _refuse_repeat_template(uuid)
    if refused is not None:
        return refused

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

    elif when_lower == "inbox":
        script = f'''
tell application "Things3"
    set theToDo to to do id "{uuid}"
    move theToDo to list "Inbox"
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
            "Expected: today, tomorrow, evening, anytime, someday, inbox, or YYYY-MM-DD.",
        )

    # Map when values to action strings
    when_to_action = {
        "today": "scheduled",
        "tomorrow": "scheduled",
        "evening": "scheduled_evening",
        "anytime": "moved_to_anytime",
        "someday": "moved_to_someday",
        "inbox": "moved_to_inbox",
    }
    action = when_to_action.get(when_lower, "scheduled")

    # Verify write (CLAUDE.md rule 8)
    raw = things.get(uuid)
    if raw is None:
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Item {uuid} not found after scheduling.",
        )

    guard = _check_unexpected_close(
        uuid,
        pre_status,
        raw.get("status") if isinstance(raw, dict) else None,
        source="schedule_item",
    )
    if guard is not None:
        return guard

    # Inbox is the one move whose landing is cheap to check field-for-field:
    # start=Inbox with no start_date. Anything else means Things refused the
    # move (e.g. a project, which cannot live in the Inbox).
    if when_lower == "inbox" and (raw.get("start") != "Inbox" or raw.get("start_date")):
        return ErrorResponse(
            error="VERIFY_FAILED",
            message=f"Item {uuid} did not land in the Inbox "
            f"(start={raw.get('start')!r}, start_date={raw.get('start_date')!r}).",
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
    if completed is True and canceled is True:
        return ErrorResponse(
            error="INVALID_INPUT",
            message="Provide completed=true or canceled=true, not both.",
        )
    if project_uuid is not None:
        _validate_uuid(project_uuid)
    if area_uuid is not None:
        _validate_uuid(area_uuid)
    # `when` is guarded inside schedule_item; the structural move is guarded here.
    if project_uuid is not None or area_uuid is not None:
        refused = _refuse_repeat_template(uuid)
        if refused is not None:
            return refused

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

    # things-mcp#6 guard: Things' AppleScript treats `set status` as a toggle
    # when the item is already in that state — `set status to completed` on a
    # completed task un-logbooks it back to open. Make the request idempotent:
    # only emit the status write when it would actually change the status.
    # Decide against the status as it is NOW, not as it was before `when=` and
    # the project/area move ran. `pre_status` was captured before those
    # delegations and still governs the anomaly guard below -- which is right,
    # since the guard's job is to notice a close that happened anywhere inside
    # this call. But using it to decide whether to WRITE a status re-introduces
    # things-mcp#6's redundant status write whenever a delegation changed it.
    # Only re-read when something in THIS call could have moved the status
    # underneath us -- i.e. a delegation actually ran. On the common path
    # nothing has, so pre_status is still current and the extra read is waste.
    if when is not None or project_uuid is not None or area_uuid is not None:
        _mid = things.get(uuid)
        decision_status = (
            _mid.get("status") if isinstance(_mid, dict) else None
        ) or pre_status
    else:
        decision_status = pre_status

    apply_completed = completed is True and decision_status != "completed"
    apply_canceled = canceled is True and decision_status != "canceled"

    # completed=False / canceled=False mean "put it back", the inverse of the
    # True case. They used to fall through every branch here and produce
    # "Updated item: ." -- a success message for a call that wrote nothing,
    # which is the worst possible answer: the caller has no way to tell it from
    # a real update. Callers were driving raw osascript to reopen tasks instead.
    #
    # A close request wins over a reopen request, so completed=False with
    # canceled=True still cancels; the explicit close is the more specific
    # instruction.
    # Each flag reopens only the status it names. The first cut accepted either
    # False against either closed status, which meant canceled=false
    # un-completed a completed item -- a flag doing something its name does not
    # say, on the user's finished work. With the user and the agent both
    # ticking checkboxes, a caller sending default field values could silently
    # un-logbook real completions. Now completed=false only un-completes and
    # canceled=false only un-cancels; a mismatched flag is the no-op it was
    # before this feature existed.
    reopen_requested = (completed is False or canceled is False) and not (
        completed is True or canceled is True
    )
    apply_reopen = (completed is False and decision_status == "completed") or (
        canceled is False and decision_status == "canceled"
    )

    if apply_completed:
        script_lines.append("set status of theToDo to completed")

    if apply_canceled:
        script_lines.append("set status of theToDo to canceled")

    if apply_reopen:
        script_lines.append("set status of theToDo to open")

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
        parts.append(
            "completed"
            if apply_completed
            else "completed (no-op — item was already completed)"
        )
    if canceled is True:
        parts.append(
            "canceled"
            if apply_canceled
            else "canceled (no-op — item was already canceled)"
        )
    if reopen_requested:
        if apply_reopen:
            parts.append("reopened")
        elif decision_status in ("completed", "canceled"):
            # Say which status it actually has, so a mismatched flag reads as a
            # deliberate refusal rather than a mystery no-op.
            parts.append(
                f"reopen skipped (no-op — item is {decision_status}; use "
                f"{'completed' if decision_status == 'completed' else 'canceled'}=false)"
            )
        else:
            parts.append("reopened (no-op — item was already open)")
    if project_uuid is not None:
        parts.append("project")
    if area_uuid is not None:
        parts.append("area")

    # Guard: a routine update (notes/title/tags/deadline/reschedule/move) must
    # never silently complete or cancel an open item. If it did, reopen and
    # report rather than logbooking an active task.
    post_status = raw.get("status") if isinstance(raw, dict) else None
    if completed is not True and canceled is not True and not apply_reopen:
        guard = _check_unexpected_close(
            uuid, pre_status, post_status, source="update_item"
        )
        if guard is not None:
            return guard

    # things-mcp#6: an explicit status request must be reflected by the store,
    # not assumed. Report a mismatch instead of an optimistic success.
    if completed is True and post_status != "completed":
        return ErrorResponse(
            error="STATUS_MISMATCH",
            message=(
                f"Requested completed=true but the item's status is "
                f"{post_status!r} after the write. Re-check the item in Things; "
                "other requested field changes may have applied."
            ),
        )
    if canceled is True and post_status != "canceled":
        return ErrorResponse(
            error="STATUS_MISMATCH",
            message=(
                f"Requested canceled=true but the item's status is "
                f"{post_status!r} after the write. Re-check the item in Things; "
                "other requested field changes may have applied."
            ),
        )
    if apply_reopen and post_status != "incomplete":
        return ErrorResponse(
            error="STATUS_MISMATCH",
            message=(
                f"Requested a reopen but the item's status is {post_status!r} "
                "after the write. Re-check the item in Things; other requested "
                "field changes may have applied."
            ),
        )

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

    refused = _refuse_repeat_template(uuid)
    if refused is not None:
        return refused

    _pre = things.get(uuid)
    pre_status = _pre.get("status") if isinstance(_pre, dict) else None

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

    guard = _check_unexpected_close(
        uuid,
        pre_status,
        raw.get("status") if isinstance(raw, dict) else None,
        source="move_to_context",
    )
    if guard is not None:
        return guard

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
      2. Ensure the dependent's `Gated by:` block links to the blocker.
      3. Ensure the blocker's `Gates:` block links to the dependent.

    Idempotent (a second identical call writes nothing) and many-to-many (a
    dependent may be gated by several blockers; a blocker's `Gates:` grows).
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
    blk_pre_status = blocker.get("status")
    dep_pre_status = dependent.get("status")

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

    guard = _check_unexpected_close(
        dependent_uuid,
        dep_pre_status,
        dep_after.get("status"),
        source="link_blocker",
        already_written=(
            "The dependent side WAS written before this was noticed — it now "
            f"carries the `gated` tag and a link to {blocker_uuid}, while the "
            "blocker side is not wired. Re-run link_blocker to finish it, or "
            "unlink_blocker to undo it."
        ),
    )
    if guard is not None:
        return guard

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

    guard = _check_unexpected_close(
        blocker_uuid,
        blk_pre_status,
        blk_after.get("status"),
        source="link_blocker",
    )
    if guard is not None:
        return guard

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
      1. Drop the dependent from the blocker's `Gates:` block.
      2. Drop the blocker from the dependent's `Gated by:` block.
      3. Drop the `gated` tag from the dependent ONLY if it has no remaining
         blockers (merge-aware: other tags are preserved).

    Idempotent and tolerant: an item that no longer exists has its side skipped
    (its counterpart is still cleaned), so this also unwinds a half-broken link.
    """
    _validate_uuid(blocker_uuid)
    _validate_uuid(dependent_uuid)

    blk_pre = things.get(blocker_uuid)
    dep_pre = things.get(dependent_uuid)
    blocker_exists = blk_pre is not None
    dependent_exists = dep_pre is not None
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
    if blk_after is not None and blk_pre is not None:
        guard = _check_unexpected_close(
            blocker_uuid,
            blk_pre.get("status"),
            blk_after.get("status"),
            source="unlink_blocker",
        )
        if guard is not None:
            return guard

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
        if dep_pre is not None:
            guard = _check_unexpected_close(
                dependent_uuid,
                dep_pre.get("status"),
                dep_after.get("status"),
                source="unlink_blocker",
            )
            if guard is not None:
                return guard

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
      - As a dependent: for each blocker in its `Gated by:` block, remove
        this task from that blocker's `Gates:` block (and clear its own
        `Gated by:` + `gated` tag).
      - As a blocker: for each task in its `Gates:` block, remove this task
        from that dependent's `Gated by:` block, dropping the dependent's
        `gated` tag if this was its last blocker (and clear its own `Gates:`).

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

    # Counterpart status before any write, so a close during the scrub is
    # attributable. The subject itself is normally already closed (that is why
    # reconcile runs), but every counterpart is an open item being written.
    pre_statuses: dict[str, str | None] = {uuid: item.get("status")}
    for _title, counterpart_uuid in blockers + dependents:
        counterpart_pre = things.get(counterpart_uuid)
        pre_statuses[counterpart_uuid] = (
            counterpart_pre.get("status") if isinstance(counterpart_pre, dict) else None
        )

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

    # Census + close check for every item this call wrote to, subject included.
    for touched_uuid in pre_statuses:
        touched_after = (
            after if touched_uuid == uuid else things.get(touched_uuid)
        )
        if touched_after is None:
            continue
        guard = _check_unexpected_close(
            touched_uuid,
            pre_statuses[touched_uuid],
            touched_after.get("status"),
            source="reconcile_completion",
        )
        if guard is not None:
            return guard

    count = len(blockers) + len(dependents)
    return SuccessResponse(
        uuid=uuid,
        message=f"Reconciled {count} blocker relation(s) for {uuid}.",
        action="reconciled",
        temporal_state=_read_temporal_state(uuid),
    )
