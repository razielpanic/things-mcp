# Tool reference

`things-mcp` exposes 19 MCP tools — 10 read tools and 9 write tools. You never need to call these directly; Claude picks the right tool based on what you ask. This page is for when you're curious what's available, or when something doesn't work and you want to know what Claude was probably trying to do.

Every read response includes a `derived_list` field on each item showing the real list the item is in (Today, Upcoming, Anytime, Someday, Inbox, or Logbook). See [how-it-works.md](how-it-works.md) for why this matters.

## Every tool rejects arguments it does not declare

Passing a parameter a tool doesn't have is a validation error, not a silent
drop. You get a message naming the offending argument and **nothing is
written** — the check runs before the tool body.

This changed in 0.3.0. Before that, an undeclared argument was discarded and the
call proceeded as if it had never been passed, so the tool reported success for
work it hadn't done. A misspelling like `projct_uuid` became a silently partial
write, and a parameter that never existed (`list_title`) filed items to the
Inbox while returning `success: true` with a real uuid.

## Read tools

### `get_inbox`

Returns items in the Inbox — untriaged items with `start=Inbox`. These stay in Inbox regardless of `start_date` until you explicitly triage them (by scheduling, moving to Anytime, or deferring to Someday).

**Args:** `limit` (default 50)
**Returns:** `{view: "Inbox", description, items: [...], count}`

### `get_today`

Returns items in the Today computed view. An item is in Today when its `start_date` is today or earlier AND its status is open.

**Args:** `limit` (default 50)
**Returns:** `{view: "Today", description, items: [...], count}`

### `get_upcoming`

Returns items in the Upcoming computed view — items with `start_date` in the future. Does not include Today (which is a separate view).

**Args:** `limit` (default 50), `days_ahead` (default 30)
**Returns:** `{view: "Upcoming", description, items: [...], count}`

### `get_anytime`

Returns items in the Anytime list — the default active state for items you plan to do but haven't scheduled. These have `start=Anytime` and no `start_date`.

**Args:** `limit` (default 50)
**Returns:** `{view: "Anytime", description, items: [...], count}`

### `get_someday`

Returns items deferred to Someday — `start=Someday` with no `start_date`. These are indefinitely postponed.

**Args:** `limit` (default 50)
**Returns:** `{view: "Someday", description, items: [...], count}`

### `get_logbook`

Returns completed and cancelled items, filtered by a time period.

**Args:** `limit` (default 50), `period` (default `"7d"` — accepts strings like `"7d"`, `"30d"`, `"1m"`)
**Returns:** `{view: "Logbook", description, items: [...], count}`

**Not yet logged.** Things keeps a completed item, checked off, in its original list until its next logbook sweep. The sweep timing comes from the "Move completed items to Logbook" setting. Until the sweep runs, `derived_list` reports that list (`Today`, `Anytime`, …), so it matches what the Things UI shows, and `status` is still `completed` or `canceled`. The sweep time comes from `TMSettings.manualLogDate`. This only applies for `logInterval` values that have been verified against the UI; any other value falls back to `Logbook` (see `logbook.py`).

### `get_item`

Returns a single item by UUID with full detail — full notes (not truncated), full checklist, structural context. Use this when Claude needs the complete picture of one item rather than a list view.

**Args:** `uuid` (required)
**Returns:** the item, or an error if not found

**Repeats.** A `repeat` block appears on repeating to-dos and is `null` otherwise. `role` is `template` (the hidden item Things' repeat UI edits), `instance` (a copy it generated), or `unknown` (the repeat columns couldn't be read). Both roles report the template's `template_trashed`, `template_paused`, `template_status`, and `next_instance_date`, so an instance shows whether the repeat behind it is still live. These come from Things' `rt1_*` columns, which things.py doesn't expose. Stopping or pausing a repeat is done by hand in Things: as of 3.24 no AppleScript, URL-scheme, or Shortcuts surface exposes it (see `repeats.py`).

A third role, `template_child`, marks a to-do inside a repeating *project* template, directly or under one of its headings; `template_uuid` is that project. Things never lists these, and neither does this MCP: list views, search, and area children drop them. `schedule_item`, `move_to_context`, and `update_item` moves refuse them with a typed `REPEAT_TEMPLATE` error, because Things itself rejects the move (AppleScript error 301).

### `search`

Structured search across all items. Supports filter combinations that don't correspond to any single sidebar view.

**Args:** `query` (optional substring), `project_uuid`, `area`, `tag`, `start_date`, `deadline`, `include_completed` (default false), `limit` (default 50)
**Returns:** `{view: "Search", items: [...], count}`

### `get_projects`

Returns all projects in your Things 3 database. Projects are structural containers (not temporal views).

**Args:** `include_items` (default false) — when true, each project includes its child todos
**Returns:** `{view: "Projects", description, items: [...], count}`

### `get_areas`

Returns all areas (the top-level structural containers above projects). Areas have no temporal state — they're purely organizational.

**Args:** `include_items` (default false) — when true, each area includes its child projects and todos
**Returns:** `{view: "Areas", description, items: [...], count}` where `items` are `AreaItem`s (distinct from `ThingsItem`)

### A note on `temporal_state.evening`

`true` / `false` when it could be read, and **`null` when it could not**. Null
is not "no" — treat it as verification unavailable, never as evidence that an
evening write failed.

Before 0.3.0 this field was `false` for every item ever returned: it was read
from a key `things.py` does not emit, so the value was a constant rather than an
observation. It now comes from `TMTask.startBucket` in the database directly.

## Write tools

### `create_todo`

Creates a new todo. Supports all the attributes Things 3 understands: title, notes, scheduling, deadline, tags, structural placement (project or area + optional heading), and an initial checklist.

**Args:** `title` (required), `notes`, `when`, `deadline`, `tags` (comma-separated), `project_uuid`, `area_uuid`, `heading`, `checklist_items` (list of strings)
**Returns:** `SuccessResponse` with the new UUID, or `ErrorResponse`

**Behind the scenes:** With no checklist, uses AppleScript (reliable, synchronous, UUID returned immediately). With a checklist, uses the `things:///json` URL scheme endpoint (which accepts title, notes, scheduling, tags, and checklist items in one payload — no auth token required). Title-based verification after a 0.5s delay to confirm creation.

### `create_project`

Creates a new project, optionally with initial todos inside it.

**Args:** `title` (required), `notes`, `when`, `deadline`, `tags` (comma-separated), `area_uuid`, `todos` (list of strings)
**Returns:** `SuccessResponse`

**Notable:** You can schedule a project to "anytime", "someday", or a specific date, but **not** to "today". Things 3 refuses to put projects in Today (they cause sidebar duplication issues). The MCP returns an `INVALID_INPUT` error if you try.

### `schedule_item`

The core temporal operation: change which computed view an item appears in. Maps `when` values to the correct combination of `start` flag and `start_date`:

- `"today"` → `start_date = today` → item appears in **Today**
- `"tomorrow"` → `start_date = tomorrow` → item appears in **Upcoming** (auto-promotes to Today when the date rolls over)
- `"evening"` → `start_date = today` + evening flag → item appears in **Today** with evening grouping
  (this is the one `when` value that rides the `things:///` URL scheme, so it briefly foregrounds Things)
- `"YYYY-MM-DD"` → `start_date = that date` → Today or Upcoming depending on the date
- `"anytime"` → clears `start_date`, sets `start = Anytime` → item appears in **Anytime** (**CRITICAL: not Someday**)
- `"someday"` → clears `start_date`, sets `start = Someday` → item appears in **Someday**
- `"inbox"` → clears `start_date`, sets `start = Inbox` → item appears in **Inbox** (un-triage). Things also detaches it from its area, as Inbox items have no context (verified live 2026-09-25)

**Args:** `uuid` (required), `when` (required)
**Returns:** `SuccessResponse` with updated `temporal_state` showing the new derivation

**Repeating to-dos.** On a generated copy, every `when` value, including `evening`, moves only that copy: Things records an exception and leaves the recurrence rule alone. Completing a copy with `update_item` leaves the rule alone too. The template itself, and to-dos inside a repeating project template, are refused with `REPEAT_TEMPLATE`. Verified on Things 3.24 on 2026-09-25, by comparing the template's `rt1_recurrenceRule` bytes, modification date, and next-instance date before and after each write.

**The response includes the post-write `temporal_state`** so Claude can confirm the item actually landed in the list the user asked for. This is how Claude can say "done, 'Book flight' is now in Upcoming" without having to call `get_item` afterward.

### `update_item`

Updates fields on an existing item: title, notes, tags, scheduling (`when`), deadline, completion/cancellation, and structural placement.

**Args:** `uuid` (required), `title`, `notes`, `when`, `deadline` (or `""` to clear), `tags` (comma-separated), `completed` (bool), `canceled` (bool), `project_uuid`, `area_uuid`
**Returns:** `SuccessResponse` or `ErrorResponse`

**`completed` / `canceled` take `false` as well as `true`.** `true` closes the item; **`false` reopens it** — status goes back to `incomplete`, the item leaves the Logbook and returns to its temporal placement. Each flag only acts on the status it names: `completed=false` un-completes, `canceled=false` un-cancels, and a mismatched flag is a reported no-op rather than a surprise. If you pass a `true` and a `false` together, the close wins.

Both are idempotent. Closing an already-closed item, or reopening an already-open one, writes nothing and says so in the response message rather than returning an empty change list.

**Replaces wholesale — caution with gated tasks:** `tags` and `notes` each *replace* the item's entire tag set / notes body; they don't merge. If the item was wired with `link_blocker`, updating its `tags` drops the `gated` tag and updating its `notes` wipes the `Gated by:` / `Gates:` blocks. Read the current value, splice your change in, and write it all back — or re-run `link_blocker` afterward. Uses AppleScript; no auth token needed.

### `move_to_context`

Structural move — changes the project or area an item belongs to. This is **not** a scheduling operation. It doesn't touch `start` or `start_date`. An item that was in Today before the move is still in Today after the move (assuming its temporal state is unchanged).

**Args:** `uuid` (required), `project_uuid` or `area_uuid` (one of them)
**Returns:** `SuccessResponse` or `ErrorResponse`

### `delete_item`

Moves an item to the Trash. Not a hard delete (Things 3 keeps trashed items until you empty Trash in the app), but from Claude's perspective the item is gone.

**Args:** `uuid` (required)
**Returns:** `SuccessResponse` on success, or `ErrorResponse` with `VERIFY_FAILED` if the item didn't actually end up in Trash after the operation

### `link_blocker`

Wires a "blocked by" dependency between two tasks. Things has no native task-to-task relation, so this synthesizes one: the dependent (blocked) task gets the `gated` tag plus a `Gated by:` link to the blocker in its notes, and the blocker gets a reciprocal `Gates:` link to the dependent. Tags and notes are *merged*, so existing tags and user-written notes survive.

**Args:** `blocker_uuid` (required — the task that must finish first), `dependent_uuid` (required — the blocked task; receives the `gated` tag)
**Returns:** `SuccessResponse` (action `linked_blocker`), or an `ErrorResponse` with `PARTIAL_LINK` if only the dependent side landed (re-run to complete — it's idempotent)

**Notable:** Idempotent (calling twice changes nothing) and many-to-many (a task can be gated by several blockers, and one blocker can gate many tasks). Both sides are verified after writing. Always wire blockers through this verb rather than hand-editing notes, so the two sides never drift apart.

### `unlink_blocker`

The inverse of `link_blocker`, for explicit/manual resolution. Removes the dependent's link from the blocker's `Gates:` block and the blocker's link from the dependent's `Gated by:` block. The `gated` tag comes off the dependent **only** when it has no blockers left — its other tags and any remaining blockers are untouched.

**Args:** `blocker_uuid` (required), `dependent_uuid` (required)
**Returns:** `SuccessResponse` (action `unlinked_blocker`) or `ErrorResponse`

**Notable:** Idempotent, and tolerant of a missing item — if one side was already trashed, the other side is still cleaned. For automatic cleanup when a task is finished, prefer `reconcile_completion`.

### `reconcile_completion`

Cleanup verb to call when a task is completed or canceled. Things has no event hooks, so relation cleanup is caller-triggered. Scrubs every blocker relation the task is part of, in both directions: it removes the task from every blocker's `Gates:` block and every dependent's `Gated by:` block (dropping that dependent's `gated` tag when this was its last blocker), and clears the task's own managed blocks.

**Args:** `uuid` (required — the task just completed or canceled)
**Returns:** `SuccessResponse` (action `reconciled`) or `ErrorResponse`

**Notable:** Idempotent and safe on a task with no relations (a no-op). Call it right after marking a blocked or blocking task done, so no dangling `gated` tags or stale links are left behind.

## Response shapes

### List-view responses

All list/query tools (`get_inbox`, `get_today`, `search`, `get_projects`, etc.) return the same self-describing shape:

```json
{
  "view": "Today",
  "description": "Items with start_date <= today. Today is a computed view, not a container — placement is derived from start_date.",
  "items": [ /* array of ThingsItem or AreaItem */ ],
  "count": 5
}
```

The `description` field teaches Claude (or any LLM client) the derivation rule for that specific view, so the model can reason correctly about why an item is or isn't showing up.

### ThingsItem shape

Each item returned by a list tool has this structure:

```json
{
  "uuid": "KWQXALHYULLxJdfUiM4jP6",
  "title": "Book flight",
  "type": "to-do",
  "notes": "truncated to 200 chars in list views, full in get_item",
  "tags": ["travel"],
  "deadline": null,
  "creation_date": "2026-04-01T10:00:00",
  "temporal_state": {
    "start": "Anytime",
    "start_date": "2026-04-15",
    "derived_list": "Upcoming",
    "status": "open",
    "evening": false
  },
  "context": {
    "project_uuid": "...",
    "project_title": "Trip Planning",
    "area_uuid": null,
    "area_title": null,
    "heading_title": null
  },
  "items": []
}
```

The two nested sub-objects keep the two axes separate:

- **`temporal_state`** — everything about *when* the item is scheduled and what computed list it's in
- **`context`** — everything about *where* the item lives structurally

Claude reads `temporal_state.derived_list` to know the real list. It reads `context.project_title` (or `context.area_title`) to know the structural parent. These don't conflict.

### SuccessResponse / ErrorResponse shapes

Write tools return one of:

```json
{
  "success": true,
  "uuid": "KWQXALHYULLxJdfUiM4jP6",
  "message": "Scheduled 'Book flight' for 2026-04-15.",
  "action": "scheduled",
  "temporal_state": {
    "start": "Anytime",
    "start_date": "2026-04-15",
    "derived_list": "Upcoming",
    "status": "open",
    "evening": false
  }
}
```

```json
{
  "success": false,
  "error": "THINGS_UNAVAILABLE",
  "message": "Things 3 is not running or the database is inaccessible."
}
```

Common error codes:

- **`THINGS_UNAVAILABLE`** — Things 3 isn't running or its SQLite DB is locked. Launch or unlock Things 3.
- **`INVALID_INPUT`** — Something in your arguments doesn't match what Things can accept (e.g. scheduling a project to Today).
- **`INVALID_DATE`** — `when` or `deadline` isn't a valid date string.
- **`NO_AUTH_TOKEN`** — Operation requires `~/.things-auth` (see [setup.md](setup.md#step-5-optional-set-up-the-auth-token)).
- **`VERIFY_FAILED`** — The write dispatched but verification afterward couldn't find the expected result. Usually means Things 3 silently rejected the operation.
- **`NOT_FOUND`** — The given UUID doesn't exist (e.g. `get_item`, or a blocker/dependent passed to `link_blocker`).
- **`PARTIAL_LINK`** — `link_blocker` wired the dependent side (`gated` tag + "Gated by" link) but the blocker side didn't land. Re-run `link_blocker` to finish — it's idempotent.
- **`READ_ERROR`** / **`WRITE_ERROR`** — generic catch-alls for unexpected exceptions.

## Further reading

- **[How it works](how-it-works.md)** — the data model and derivation logic
- **[Troubleshooting](troubleshooting.md)** — what to do when an error code shows up
