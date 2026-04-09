# Troubleshooting

Things that might go wrong, and what to do about them. Organized by symptom.

## "Claude says the MCP isn't available" / "things-mcp tools don't show up"

**Most likely cause:** You added the MCP to your Claude Desktop config but didn't relaunch Claude Desktop. MCP servers only load on startup.

1. Fully quit Claude Desktop (Cmd+Q — closing the window isn't enough)
2. Relaunch Claude Desktop
3. Start a new conversation and ask "what's in my Things Inbox?"

**Other causes to check:**

- **Config file syntax error.** Validate your `~/Library/Application Support/Claude/claude_desktop_config.json` — it has to be valid JSON. Missing commas and trailing commas both break it silently. You can check with `python3 -m json.tool < ~/Library/Application\ Support/Claude/claude_desktop_config.json`.
- **Wrong Python path.** Claude Desktop runs the `command` you specified literally. If `command: "python3"` doesn't find Python, try an absolute path like `/opt/homebrew/bin/python3` or `/usr/bin/python3`.
- **Wrong `PYTHONPATH`.** It must be the absolute path to the `src/` directory inside your clone, not the repo root. E.g. `/Users/YOU/Projects/things-mcp/src`, not `/Users/YOU/Projects/things-mcp`.
- **Dependencies not installed.** Run `python3 -c "import things_mcp; print('ok')"` from your terminal with the same Python interpreter Claude Desktop is using. If it fails, run `pip install -e .` from the repo root.

## `THINGS_UNAVAILABLE` error

**What it means:** The MCP tried to query Things 3's SQLite database and couldn't. Either Things 3 isn't running, or the database file is locked, or the `things.py` library can't find it in the standard location.

**Fix:**

1. **Is Things 3 running?** Launch it. The MCP reads the live Things database; if Things 3 isn't running, the database may be in a state the `things.py` library can't read.
2. **Is your Things library initialized?** First-time install of Things 3 creates the database lazily. If you've never actually used Things 3 on this Mac, open it and add one todo — that'll materialize the database.
3. **Did you change your iCloud/Things sync state recently?** iCloud sync can temporarily lock the SQLite file. Wait a minute and try again.

## `VERIFY_FAILED` error on a write operation

**What it means:** The MCP dispatched the AppleScript or URL scheme command, waited, and then checked whether the expected change happened. It didn't.

**Common causes:**

- **The UUID doesn't exist anymore.** You or Claude may have deleted the item since it was last listed. Re-fetch the current state and try again.
- **URL scheme command was rejected by Things 3.** The URL scheme is fire-and-forget — Things can silently reject a command (malformed payload, missing required field) and the MCP only knows the verification step didn't find the expected result. Check that the arguments you passed are valid (e.g. deadline is a real date, project_uuid exists).
- **Auth token missing or wrong** (for operations that need it). Check `~/.things-auth` exists and matches the token in Things 3 → Settings → General. See [setup.md § auth token](setup.md#step-5-optional-set-up-the-auth-token).

## `NO_AUTH_TOKEN` error

**What it means:** The operation you (or Claude) asked for uses a Things 3 URL scheme endpoint that requires authentication, and `~/.things-auth` doesn't exist or is empty.

**Fix:** Set up the auth token. See [setup.md § auth token](setup.md#step-5-optional-set-up-the-auth-token).

**Which operations need it?**

- In-place checklist updates on existing items (via `things:///update?append-checklist-items=...`)
- Any operation that uses `things:///update` or `things:///delete`

**Which operations do NOT need it?**

- Reading lists (`get_today`, etc.)
- Creating new items (`create_todo`, `create_project`) — even with checklists, because creation goes through `things:///json` which doesn't require the auth token
- Scheduling (`schedule_item`) — uses AppleScript
- Moving between projects/areas (`move_to_context`) — uses AppleScript
- Deleting (`delete_item`) — uses AppleScript `move to list "Trash"`, not the protected URL scheme

So you can do a lot of useful stuff without ever configuring the auth token. It's only in-place updates on existing items that need it.

## `INVALID_INPUT` on create_project

**Most likely cause:** You tried to schedule a project to "today".

Things 3 doesn't support projects in the Today list — projects in Today cause sidebar duplication issues. The MCP enforces this at the create step. Use `"anytime"`, `"someday"`, or a specific future date instead.

## `INVALID_DATE` error

**What it means:** The `when` or `deadline` argument wasn't a recognizable date string.

Valid `when` values:

- `"today"`, `"tomorrow"`, `"evening"`
- `"anytime"`, `"someday"`
- `"YYYY-MM-DD"` (e.g. `"2026-04-15"`)

Valid `deadline` values:

- `"YYYY-MM-DD"`

Natural-language dates like `"next Friday"` are **not** supported by the MCP layer — if Claude passes those through, you'll get `INVALID_DATE`. Claude should usually resolve natural language to an ISO date before calling the tool, but occasionally needs a reminder.

## Checklist items didn't attach to my new todo

**Most likely cause:** You updated the MCP code recently and didn't relaunch Claude Desktop.

The checklist path was simplified in a recent update to use `things:///json` directly (one call, no auth token, no intermediate AppleScript trash cycle). If Claude Desktop is still running the old subprocess, it might be using a stale code path.

Fully quit Claude Desktop (Cmd+Q) and relaunch.

**Other causes:**

- **Title collision.** The MCP recovers the new item's UUID via title-based search after the URL scheme call (because the `things:///json` endpoint doesn't return a UUID). If another todo already has the exact same title, the MCP might adopt the wrong UUID. Try a more unique title.
- **Things 3 silently rejected the checklist items.** Check the response — if `success: true` but the warning mentions "checklist items may not have been added", the create succeeded but the checklist didn't attach. This is usually a Things 3 quirk. Try recreating with a different item structure (e.g. shorter checklist item titles, no special characters).

## "schedule didn't actually move the item"

**What you'd see:** You ask Claude to schedule something for "tomorrow", Claude reports success, but in Things 3 the item is still where it was before.

**Debug steps:**

1. **Did Claude actually call `schedule_item`?** Ask Claude to tell you what tool it called. If it called `update_item` or `move_to_context` instead, that explains the wrong behavior.
2. **Is the UUID correct?** Have Claude fetch the item via `get_item` by UUID and confirm the UUID matches the item you expected.
3. **Did the `when` get interpreted correctly?** The response from `schedule_item` includes the post-write `temporal_state` — check what `derived_list` it reports. If it says `"Upcoming"` but you're looking at Today in Things 3, the MCP put it where you asked; you're looking at the wrong list.
4. **Restart Claude Desktop.** Stale MCP subprocess after a code update is a recurring culprit.

## "My notes are cut off"

List views truncate notes to 200 characters. This is intentional — it keeps list responses compact. If you need the full note, use `get_item` on the specific UUID, which returns the full note and the full checklist.

## Still stuck?

- **Check the commit history.** `git log --oneline src/things_mcp/` — if you pulled recently, the behavior you're seeing might be from a change you didn't know about.
- **Look at the tests.** `tests/` has 167 tests covering reads, writes, models, derivation, and server handlers. If you're seeing a behavior that conflicts with a test expectation, the test will tell you what the code thinks should happen.
- **Read [CLAUDE.md](../CLAUDE.md).** It's the internal source of truth for the project's rules and invariants.
- **Open an issue.** Describe what you asked Claude to do, what tool response you got, and what Things 3 shows.
