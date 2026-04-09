# Contributing

Thanks for looking. This guide is for anyone wanting to extend `things-mcp` — new tools, bug fixes, or architectural changes. If you just want to use the MCP, the [README](README.md) and [setup guide](docs/setup.md) are what you want.

## Dev setup

```bash
git clone https://github.com/YOUR_ORG/things-mcp.git
cd things-mcp
pip install -e ".[dev]"
pytest
```

That's the whole setup. No build step, no code generation, no external services.

- `pip install -e ".[dev]"` installs runtime dependencies (`mcp[cli]`, `things.py`, `pydantic`) plus dev dependencies (`pytest`, `pytest-asyncio`)
- `pytest` runs the 167-test suite. Expect a clean pass in ~0.5 seconds on a modern Mac
- Tests run against a fixture SQLite database (`tests/fixtures/things_fixture.sqlite`) — they don't touch your real Things 3 database, so it's safe to run pytest anytime

## Architecture

```
src/things_mcp/
  server.py       — FastMCP tool definitions (thin wrappers over reads/writes)
  reads.py        — things.py (SQLite) queries + derivation enrichment
  writes.py       — AppleScript for scheduling/moves, URL scheme for checklists
  models.py       — Pydantic models matching Things' real data model
  derivation.py   — List derivation logic (the core value)
```

**Read path:** `things.py` → `_item_from_dict()` → `derive_list()` → `ThingsItem` → response

Every read flows through `reads._item_from_dict()`, which builds a `ThingsItem` with nested `temporal_state` (populated from `derive_list()`) and `context`. This is the invariant that enforces "every response has `derived_list`". Don't bypass it.

**Write path:** AppleScript primary, URL scheme for checklist operations and create-with-initial-todos.

The rationale for this split is in [docs/how-it-works.md § the write path](docs/how-it-works.md#the-write-path-applescript-first-url-scheme-as-a-last-resort). Short version: AppleScript returns UUIDs, runs synchronously, and doesn't need the auth token. URL scheme is only used where AppleScript can't do the job (checklist attachments at creation time).

## The rules (non-negotiable)

These are in [CLAUDE.md](CLAUDE.md), which is the internal source of truth. They exist because violating them causes silent data corruption or wrong-list bugs:

1. **Every read response must include `derived_list`.** No exceptions.
2. **`when=anytime` means "clear `start_date`, set `start=Anytime`."** Never map it to Someday. This is the single most common wrong mapping in Things MCP servers.
3. **Use the AppleScript `schedule` command for dates.** Never use `set activation date` — it's broken in recent Things 3 versions.
4. **Use `move to list "X"`** AppleScript for Anytime/Someday/Inbox moves.
5. **URL scheme only for:** checklist operations, bulk creation with initial todos.
6. **Auth token (`~/.things-auth`) required** before any URL scheme update/delete operation.
7. **Never schedule a project to Today** — only tasks get Today.
8. **Verify writes actually happened.** Never silently report success — every write path must confirm the mutation landed before returning `SuccessResponse`.
9. **Truncate notes to 200 chars in list views**, full in `get_item`.
10. **No response optimizer, no context manager, no operation queue.** Keep it simple.

Most of these are enforced by tests. If you find yourself wanting to bend one, open an issue first and let's talk about it.

## Adding a new MCP tool

1. **Figure out which module it belongs in.** Read-only? `reads.py`. Mutates Things state? `writes.py`.
2. **Write the implementation** in the appropriate module, returning Pydantic models from `models.py` (not raw dicts). If you need a new model, add it to `models.py`.
3. **Wire it in `server.py`** with a thin `@mcp.tool()` wrapper that calls through to your implementation and returns `result.model_dump()`. Wrap the body in `try/except` and return `ErrorResponse` for failures.
4. **Write a docstring that teaches the Cultured Code concept.** The MCP's docstrings are the instructions LLM clients see — they should explain *why* the tool works the way it does, not just parameter lists. Phase 02 rewrote all existing docstrings to this standard; match the style.
5. **Add unit tests.** `tests/test_reads_unit.py` and `tests/test_writes_mocked.py` have the patterns to follow. Mock `subprocess.run` and `things.tasks/.get` — don't hit the real database or run real AppleScript.
6. **Add a server-handler test** in `tests/test_server.py` covering the async handler, the success path, and the error paths.
7. **Add an integration test** in `tests/test_reads_integration.py` if you're touching the read path — these run against the fixture database to verify end-to-end behavior.
8. **Run `pytest`** and make sure everything still passes.

## Test layout

- `tests/test_derivation.py` — Full coverage of the `derive_list` truth table. The derivation logic is the core value of this project; tests here are non-negotiable.
- `tests/test_models.py` — Pydantic model validation and serialization.
- `tests/test_reads_unit.py` — Pure-function tests for `reads.py` helpers (mocked `things.py`).
- `tests/test_reads_integration.py` — Integration tests against `tests/fixtures/things_fixture.sqlite`, a real Things 3 SQLite database with schema version > 21. Tests the full read path including `_item_from_dict` and `derive_list`.
- `tests/test_writes_mocked.py` — Tests `writes.py` with `subprocess.run` and `things.tasks/.get` mocked. Verifies AppleScript payload construction, URL scheme encoding, write verification, and error paths.
- `tests/test_server.py` — Async handler tests. Mocks `reads` and `writes` modules, verifies the `@mcp.tool()` wrappers return the right shapes and handle errors correctly.
- `tests/conftest.py` — Shared fixtures (`THINGSDB` env var, pytest async config).

## Things 3 quirks you'll run into

- **UUIDs are 22-char base62**, not RFC 4122. The `_UUID_RE` in `writes.py` is `^[A-Za-z0-9]{22}$`. Don't use a RFC 4122 regex.
- **`things:///json` endpoint** creates items but doesn't return their UUID. Recovery is title-based search after a 0.5s delay. This is why the MCP prefers AppleScript for the no-checklist path.
- **`things:///update` requires auth token**. `things:///json` (for creates) doesn't. This is why the checklist-create path uses `things:///json` instead of the more natural "create base + update with checklist" approach.
- **AppleScript `set activation date`** is broken in recent Things 3 versions. Use `schedule theToDo for theDate` instead.
- **Locale-safe date construction in AppleScript.** Build dates via property setting (`set year of theDate to 2026`, `set month of theDate to April`, etc.), not string parsing. String parsing breaks on non-US locales. See `_applescript_date_block` in `writes.py`.
- **iCloud sync** can temporarily lock the SQLite database. If you see flaky test failures mentioning `OperationalError`, it's usually sync contention on your dev machine — retry.

## Commit messages

Use conventional commit prefixes: `feat`, `fix`, `test`, `refactor`, `perf`, `docs`, `style`, `chore`. The repo has a pre-commit hook that enforces this.

Scope is encouraged — e.g. `fix(writes): use things:///json for checklist creation` is better than `fix: checklist bug`.

## Planning and verification workflow

This repo uses a structured milestone/phase workflow in `.vbw-planning/` (managed by the `vbw` plugin). You don't need to use it for small fixes, but if you're contributing a larger feature it's worth understanding:

- **Milestones** are shippable units, archived in `.vbw-planning/milestones/{slug}/` after they ship
- **Phases** decompose a milestone into independently plannable chunks (3–5 per milestone)
- **Plans** (PLAN.md files) describe what each phase does with must_haves and task breakdowns
- **QA verification** runs after each phase via the `vbw-qa` subagent, producing VERIFICATION.md
- **UAT** (user acceptance testing) runs interactively after QA for phases with user-observable behavior

For structural refactors, test infrastructure, and error-handling hardening, QA is authoritative — the human doesn't need to run scripted tests. See `.vbw-planning/milestones/01-core-tools-cc-alignment-extended-tools/` for examples of how the shipped milestone's phases documented this.

## License

MIT, same as the project. Contributions are licensed under the same terms.
