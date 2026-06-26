# How it works

This is the part where we explain why `things-mcp` exists and why it gets results that other Things 3 MCP servers don't. If you just want to use it, the [README](../README.md) and [setup guide](setup.md) are all you need. Read this if you want to understand *why* `when=anytime` is the single most mis-mapped concept in other Things MCP servers, or if you want to contribute.

## The core insight: temporal lists are computed views

In Things 3, the lists in the sidebar — **Inbox, Today, Upcoming, Anytime, Someday, Logbook** — are not folders. They're not storage bins. An item doesn't "belong to" Today the way it belongs to the "Work" area or the "Q2 Planning" project.

The temporal lists are **computed views** over two underlying fields on every item:

- **`start`** — a sticky flag that takes one of three values: `Inbox`, `Anytime`, or `Someday`. This reflects *intent*: do I want to do this eventually, or am I deferring it?
- **`start_date`** — an optional date. If set, the item is scheduled for that day (or later).

Given those two fields plus the item's `status` (open, completed, cancelled), Things computes which list the item *actually* appears in when you look at the sidebar. The derivation is:

| `status` | `start` | `start_date` | Appears in |
|---|---|---|---|
| completed or cancelled | any | any | **Logbook** |
| open | Inbox | any | **Inbox** |
| open | Anytime or Someday | today or earlier | **Today** |
| open | Anytime or Someday | after today | **Upcoming** |
| open | Anytime | none | **Anytime** (the default active state) |
| open | Someday | none | **Someday** |

That's the whole derivation. `src/things_mcp/derivation.py` implements it in about 30 lines, and `tests/test_derivation.py` covers every row of the truth table.

## Why this matters for MCP servers

Most Things 3 MCP servers read Things' SQLite database, see a field called `start`, and report that as "the list the item is on". This is wrong in a specific and hard-to-debug way:

- You have an item with `start=Anytime` and `start_date=2025-11-14` (today). Things shows it in **Today**. But the other MCP server reads `start` and tells Claude it's in **Anytime**. Claude confidently reports "this item is in Anytime" and maybe reschedules it, not realizing it was already in Today.
- You ask the MCP to move an item to Upcoming. It might naively try to set `start=Upcoming`, which isn't even a valid value for the `start` field — there is no Upcoming flag. The right move is to set `start_date` to a future date.
- You ask the MCP to schedule something for "anytime". The MCP looks at its enum of list values and sees `Anytime` and `Someday` as adjacent options in a dropdown somewhere, and maps "anytime" → Someday because someone thought they were synonyms. They're not. Your task silently vanishes into the "I'll deal with this eventually" pile.

`things-mcp` solves this by always computing `derived_list` from `start` + `start_date` before handing any item back to Claude. Every read response includes this field. Claude never has to read `start` directly — it reads `derived_list`, which is the truth.

## The `when=anytime` bug

This is the bug that prompted the whole project. Here's the scenario:

1. You say to Claude: "Schedule 'Book dentist' for anytime."
2. Claude calls the MCP's `schedule_item` tool with `when="anytime"`.
3. The MCP needs to figure out: what does "anytime" mean as a scheduling operation?

The correct answer, in Things' model:
- **Clear** the `start_date` (if set)
- **Set** the `start` flag to `Anytime`

That's it. The item is now in the Anytime list (default active state, no date). If you open Things, you'll see it under Anytime.

The common wrong answer (that many MCP servers ship with):
- Map "anytime" to the Someday list because the naming feels similar

This is a silent data-loss bug. You asked for "anytime" (I want to do this, no particular day), you got Someday (I'm deferring this indefinitely). The item is now in a list you never look at.

`things-mcp`'s CLAUDE.md rule #2 is literally: *`when=anytime` means "clear start_date, set start=Anytime." Never map it to Someday.* The code path in `writes.py` uses the Things 3 AppleScript `move theToDo to list "Anytime"` command, which directly updates the `start` flag the way Things' own UI does.

## Two orthogonal axes

Once you internalize that temporal lists are computed from `start` + `start_date`, you start to see that Things has two completely independent dimensions for organizing items:

- **Structural axis** — where the item lives in your hierarchy: **Area → Project → Heading → To-do**
- **Temporal axis** — when you plan to do it: derived from `start` + `start_date`

An item can be in the "Work" area, under the "Q2 Planning" project, under the "Research" heading (that's its structural position), AND simultaneously be in the Today computed view because its `start_date` is today (that's its temporal position). These don't conflict. They're orthogonal.

Most "move this to X" operations in Things are really one of two very different things:

- **Structural move** — `move_to_context` in this MCP. Changes the Area/Project/Heading parent. Uses AppleScript `set project of` or `set area of`.
- **Temporal move** — `schedule_item` in this MCP. Changes `start` and/or `start_date`. Uses AppleScript `schedule` command (for dates) or `move to list` (for Anytime/Someday/Inbox).

If you try to "move an item to Today" by setting its Area or Project, nothing happens — Today isn't a structural container. If you try to "move an item to Work Project" by calling `schedule_item`, you get the wrong result because scheduling doesn't change structural position.

This MCP keeps those two operations strictly separate. Claude can call either one, but it never conflates them.

## The write path: AppleScript first, URL scheme as a last resort

Things 3 offers two ways to mutate data:

1. **AppleScript** — the "tell application Things3" scripting interface. Synchronous, returns the UUID of created items, supports every operation.
2. **URL scheme** — `things:///add`, `things:///add-project`, `things:///json`, `things:///update`, etc. Asynchronous, does NOT return the UUID of created items (you have to search by title afterward), some endpoints require an auth token.

This MCP uses AppleScript for almost everything:

- `create_todo` (without checklist) — AppleScript, returns reliable UUID
- `schedule_item` — AppleScript `schedule` command
- `move_to_context` — AppleScript `set project of` / `set area of`
- `delete_item` — AppleScript `move to list "Trash"`
- `update_item` (title, notes, tags) — AppleScript
- `link_blocker` / `unlink_blocker` / `reconcile_completion` — AppleScript (`set tag names`, `set notes`). Things has no native task-to-task relation, so these synthesize a "blocked by" link: the dependent gets the `gated` tag and a `Gated by:` block, the blocker a reciprocal `Gates:` block, each entry a title line + a bare `things:///show?id=…` deep link. Tags and notes are read-merged-rewritten (never clobbered), and both sides are verified.

The only times it uses the URL scheme:

- `create_todo` with a checklist — `things:///json` endpoint, because AppleScript can't attach checklist items at creation time
- `create_project` with initial todos — same reason, `things:///json` lets you specify the project and its children in one call
- In-place checklist updates on existing items — `things:///update?append-checklist-items=...`, which *does* require the auth token

Why favor AppleScript? Three reasons:

1. **Reliable UUIDs** — AppleScript returns the created item's UUID immediately. URL scheme operations are fire-and-forget: you know you dispatched a URL but you don't know the resulting UUID. To recover it you have to search by title and hope no other item has the same title.
2. **No auth token** — AppleScript works without the Things 3 auth token for basic operations. URL scheme update/delete endpoints require it.
3. **Synchronous verification** — AppleScript operations complete before the command returns. URL scheme operations are async and need a `time.sleep(0.5)` before you can verify the write landed.

CLAUDE.md rule #8 requires every write operation to verify it actually happened before returning `SuccessResponse`. The MCP never silently reports success on a write that didn't land — if verification fails, you get a `VERIFY_FAILED` error with details.

## Notes truncation in list views

Every list-view tool (`get_inbox`, `get_today`, etc.) truncates item notes to 200 characters. This is to keep list responses compact when you have items with long notes — Claude doesn't need the full 5000-character note just to tell you what's in Today.

`get_item` (the single-item detail tool) returns the full note, untruncated, plus the full checklist. Use it when you want the complete picture of one item.

## Further reading

- **[Tool reference](tools.md)** — every MCP tool, what it does, what it returns
- **[CLAUDE.md](../CLAUDE.md)** — the internal rules for anyone extending this codebase
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** — dev setup and architecture pointers
