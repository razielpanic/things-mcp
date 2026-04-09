# things-mcp

## What this is

An MCP server for Things 3 that exposes Cultured Code's actual data model. Built to replace mcp-server-things (ebowman) which has fundamental data model mismatches.

## Key concept: list derivation

The most important code is in `derivation.py`. Things 3's temporal lists are computed views:

- `start` is a sticky flag (Inbox/Anytime/Someday) -- it does NOT change when items move to Today/Upcoming
- `start_date` drives Today (<=today) and Upcoming (>today) placement
- Every response must include `derived_list` computed from these fields

Never treat the `start` field as "which list is this on."

## Architecture

```
src/things_mcp/
  server.py       -- FastMCP tool definitions (thin wrappers)
  reads.py        -- things.py (SQLite) queries + derivation enrichment
  writes.py       -- AppleScript for scheduling/moves, URL scheme for checklists
  models.py       -- Pydantic models matching Things' real data model
  derivation.py   -- List derivation logic (the core value)
```

Read path: things.py -> derive_list() -> response
Write path: AppleScript primary, URL scheme for checklists only

## Rules

1. Every read response MUST include `derived_list`. No exceptions.
2. `when=anytime` means "clear start_date, set start=Anytime." Never map it to Someday.
3. Use `schedule` AppleScript command for dates, never `set activation date` (broken).
4. Use `move to list "X"` AppleScript for Anytime/Someday/Inbox moves.
5. URL scheme only for: checklist operations, bulk creation with initial todos.
6. Auth token (~/.things-auth) required before any URL scheme update/delete.
7. Never schedule a project to Today -- only tasks get Today.
8. Verify writes actually happened. Never silently report success.
9. Truncate notes to 200 chars in list views, full in get_item.
10. No response optimizer, no context manager, no operation queue.

## Research

See `docs/how-it-works.md` for the data model explanation and `CONTRIBUTING.md` for the architectural rationale and write-path rules.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

## Running the server

```bash
pip install -e .
things-mcp
```

Or via MCP CLI:
```bash
mcp run things-mcp
```
