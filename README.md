# things-mcp

An MCP server for [Things 3](https://culturedcode.com/things/) that exposes Cultured Code's actual data model.

## Why this exists

Existing Things 3 MCP servers have a fundamental mismatch with how Things actually works. The root issues:

1. **Treating temporal lists as containers.** Things' lists (Today, Upcoming, Anytime, Someday) are computed views, not storage bins. An item's list membership is derived from the `start` flag + `start_date` fields.

2. **The `start` field is NOT the current list.** `start` is a sticky flag with three values: Inbox, Anytime, Someday. It does NOT change when an item moves to Today or Upcoming -- those transitions happen via `start_date`. Every existing MCP passes `start` through as-is, leading to wrong assessments like "this is in Anytime" when the item is actually in Today.

3. **`when=anytime` maps to Someday.** The most impactful bug. In Things, Anytime is the default state (no start date, start flag = Anytime). It is not a list you schedule into. The previous MCP treats "anytime" and "someday" as equivalent.

## Things 3's temporal model

Things has two orthogonal dimensions:

- **Context** (structural): Area > Project > Heading > To-do
- **Temporal placement** (when): derived from `start` flag + `start_date`

The derivation logic:

| `start` flag | `start_date` | Actual list |
|-------------|-------------|-------------|
| Inbox | (any) | Inbox |
| (any) | <= today | **Today** |
| (any) | > today | **Upcoming** |
| Someday | None | Someday |
| Anytime | None | **Anytime** (default) |

Completed/canceled items are always in Logbook regardless of other fields.

**Anytime is the default state.** When you process an Inbox item without scheduling it, it becomes Anytime. There is no "move to Anytime" action -- you clear the start date.

## Architecture

- **Read path:** things.py (SQLite) -> derive actual list -> response with `derived_list` field
- **Write path:** AppleScript for scheduling/moves, URL scheme for checklists only
- **No:** response optimizer, context manager, operation queue, shared cache, tag validation service

Every response includes `derived_list` -- the computed list the item actually appears in.

## Current status

**Scaffolding + derivation logic.** The derivation module is fully implemented and tested. Everything else is well-documented stubs.

- `src/things_mcp/derivation.py` -- Working. The core value.
- `src/things_mcp/models.py` -- Working. Pydantic models matching Things' real data model.
- `src/things_mcp/server.py` -- Stubs. 16 MCP tool definitions with correct signatures and docstrings.
- `src/things_mcp/reads.py` -- Stubs. things.py query wrappers.
- `src/things_mcp/writes.py` -- Stubs. AppleScript + URL scheme operations.
- `tests/test_derivation.py` -- Working. Full coverage of the derivation truth table.

## Research

The comprehensive research document that informed this design lives at:
`/OfficeManager/.office-manager/dev-research/things-mcp-research.md`

It contains the full issue catalog, data model analysis, alternatives comparison, and specification outline.

## License

MIT
