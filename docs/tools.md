# Tool reference

`things-mcp` exposes 19 MCP tools — 10 read tools and 9 write tools. You never need to call these directly; Claude picks the right tool based on what you ask. This page is for when you're curious what's available, or when something doesn't work and you want to know what Claude was probably trying to do.

Every read response includes a `derived_list` field on each item showing the real list the item is in (Today, Upcoming, Anytime, Someday, Inbox, or Logbook). See [how-it-works.md](how-it-works.md) for why this matters.

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

### `get_item`

Returns a single item by UUID with full detail — full notes (not truncated), full checklist, structural context. Use this when Claude needs the complete picture of one item rather than a list view.

**Args:** `uuid` (required)
**Returns:** the item, or an error if not found

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
- `"YYYY-MM-DD"` → `start_date = that date` → Today or Upcoming depending on the date
- `"anytime"` → clears `start_date`, sets `start = Anytime` → item appears in **Anytime** (**CRITICAL: not Someday**)
- `"someday"` → clears `start_date`, sets `start = Someday` → item appears in **Someday**

**Args:** `uuid` (required), `when` (required)
**Returns:** `SuccessResponse` with updated `temporal_state` showing the new derivation

**The response includes the post-write `temporal_state`** so Claude can confirm the item actually landed in the list the user asked for. This is how Claude can say "done, 'Book flight' is now in Upcoming" without having to call `get_item` afterward.

### `update_item`

Updates fields on an existing item: title, notes, tags, scheduling (`when`), deadline, completion/cancellation, and structural placement.

**Args:** `uuid` (required), `title`, `notes`, `when`, `deadline` (or `""` to clear), `tags` (comma-separated), `completed` (bool), `canceled` (bool), `project_uuid`, `area_uuid`
**Returns:** `SuccessResponse` or `ErrorResponse`

**Replaces wholesale — caution with gated tasks:** `tags` and `notes` each *replace* the item's entire tag set / notes body; they don't merge. If the item was wired with `link_blocker`, updating its `tags` drops the `gated` tag and updating its `notes` wipes the `**Gated by:**` / `**Gates:**` blocks. Read the current value, splice your change in, and write it all back — or re-run `link_blocker` afterward. Uses AppleScript; no auth token needed.

### `move_to_context`

Structural move — changes the project or area an item belongs to. This is **not** a scheduling operation. It doesn't touch `start` or `start_date`. An item that was in Today before the move is still in Today after the move (assuming its temporal state is unchanged).

**Args:** `uuid` (required), `project_uuid` or `area_uuid` (one of them)
**Returns:** `SuccessResponse` or `ErrorResponse`

### `delete_item`

Moves an item to the Trash. Not a hard delete (Things 3 keeps trashed items until you empty Trash in the app), but from Claude's perspective the item is gone.

**Args:** `uuid` (required)
**Returns:** `SuccessResponse` on success, or `ErrorResponse` with `VERIFY_FAILED` if the item didn't actually end up in Trash after the operation

### `link_blocker`

Wires a "blocked by" dependency between two tasks. Things has no native task-to-task relation, so this synthesizes one: the dependent (blocked) task gets the `gated` tag plus a `**Gated by:**` link to the blocker in its notes, and the blocker gets a reciprocal `**Gates:**` link to the dependent. Tags and notes are *merged*, so existing tags and user-written notes survive.

**Args:** `blocker_uuid` (required — the task that must finish first), `dependent_uuid` (required — the blocked task; receives the `gated` tag)
**Returns:** `SuccessResponse` (action `linked_blocker`), or an `ErrorResponse` with `PARTIAL_LINK` if only the dependent side landed (re-run to complete — it's idempotent)

**Notable:** Idempotent (calling twice changes nothing) and many-to-many (a task can be gated by several blockers, and one blocker can gate many tasks). Both sides are verified after writing. Always wire blockers through this verb rather than hand-editing notes, so the two sides never drift apart.

### `unlink_blocker`

The inverse of `link_blocker`, for explicit/manual resolution. Removes the dependent's link from the blocker's `**Gates:**` block and the blocker's link from the dependent's `**Gated by:**` block. The `gated` tag comes off the dependent **only** when it has no blockers left — its other tags and any remaining blockers are untouched.

**Args:** `blocker_uuid` (required), `dependent_uuid` (required)
**Returns:** `SuccessResponse` (action `unlinked_blocker`) or `ErrorResponse`

**Notable:** Idempotent, and tolerant of a missing item — if one side was already trashed, the other side is still cleaned. For automatic cleanup when a task is finished, prefer `reconcile_completion`.

### `reconcile_completion`

Cleanup verb to call when a task is completed or canceled. Things has no event hooks, so relation cleanup is caller-triggered. Scrubs every blocker relation the task is part of, in both directions: it removes the task from every blocker's `**Gates:**` block and every dependent's `**Gated by:**` block (dropping that dependent's `gated` tag when this was its last blocker), and clears the task's own managed blocks.

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
- **`READ_ERROR`** / **`WRITE_ERROR`** — generic catch-alls for unexpected exceptions.

## Further reading

- **[How it works](how-it-works.md)** — the data model and derivation logic
- **[Troubleshooting](troubleshooting.md)** — what to do when an error code shows up
