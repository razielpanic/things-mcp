# SPEC: gated/gates blocker relation verbs

**Status:** ready to build · **Created:** 2026-06-26
**Source decision:** OfficeManagerDev dev-issue `dev-issues/things-mcp/2026-06-25-adopt-gated-waiting-tag-convention-with-bidirectional-deep-link-cross-wiring-for-blocked-things-tasks.md`

## Goal

Things has no native task-to-task relation. Give it one, for the "blocked by" case, as a first-class MCP capability so callers (the OM, any project's Claude) can't half-wire it. A blocked task gets the `gated` tag plus a bidirectional deep-link in notes: the dependent points at its blocker (`Gated by:`), the blocker lists what it blocks (`Gates:`). The verb owns atomicity, idempotency, and cleanup; callers just say "B blocks D."

This is the **mechanism** layer only. Policy (when to use `gated` vs `waiting`) lives in global `~/.claude/CLAUDE.md`; auto-apply-during-triage lives in OM procedures. Both are downstream of this and need the final verb name/signature first.

## Data-model reality (READ THIS — it dictates the implementation)

From `writes.py:update_item`:
- **`set tag names of theToDo to X` REPLACES all tags.** To add `gated` you must read current tags, add `gated` if absent, and set the full list. Never set just `{"gated"}` — it clobbers the task's other tags.
- **`set notes of theToDo to X` REPLACES the whole notes body.** To add/update a `Gated by:`/`Gates:` block you must read current notes, splice the managed block in (or update/remove it), and write the whole string back. Idempotency = **parse-and-rewrite**, never blind append.
- All tag/notes writes go through **AppleScript**, so CLAUDE.md **rule #6 (auth token) does NOT apply** here. **Rule #8 (verify the write actually happened) DOES** — round-trip `things.get(uuid)` after and confirm.
- `things.get(uuid)` returns the raw dict with `notes`, `tags`, `title`, `status` — use it to read current state and to get titles for link labels.

### Notes rendering constraints (corrected 2026-06-26 — supersedes the 2026-06-25 claim)
Grounded in Cultured Code's official [Markdown Guide](https://culturedcode.com/things/support/articles/4651820/), not just observation: **"the Markdown syntax is always displayed in Things. This is intentional and there's no way to hide it."** Things *styles* text (bold shows bold) but never hides the markers — so `**Gated by:**` shows the literal `**`, and a `[Title](url)` link shows the literal brackets, parens, and full URL. The original "named links render as clickable friendly text" assumption was wrong (the URL is never hidden). What Things *does* do is auto-detect a bare URL and make it tappable ("Things will detect pasted links without [the syntax]"); in-app testing on macOS 2026-06-26 confirms this includes the `things:///show?id=…` scheme. So the managed block is **plain text**: a `Gated by:` / `Gates:` label line, then per entry a **title line** (human-scannable) followed by the **bare deep-link line** `things:///show?id=<uuid>` (which Things auto-links). Do NOT wrap titles in `[...]()` or bold the labels — that syntax renders literally and only adds noise; the bare URL is what makes the link work. (The docs don't enumerate custom schemes, hence the in-app confirmation.)

## Verbs to build

### `link_blocker(blocker_uuid, dependent_uuid)`
Atomically, on success:
1. Add `gated` tag to **dependent** (merge into existing tags, dedup).
2. On **dependent** notes: ensure a `Gated by:` managed block lists the blocker as a title line + `things:///show?id=<blocker_uuid>` line. Add if absent; no-op if present (dedup by uuid).
3. On **blocker** notes: ensure a `Gates:` managed block lists the dependent as a title line + `things:///show?id=<dependent_uuid>` line. Add if absent; no-op if present.
- **Idempotent:** calling twice changes nothing the second time.
- **Many-to-many:** a dependent may be gated by several blockers (several entries under its `Gated by:`); a blocker's `Gates:` is a growing set.
- Verify both writes (rule #8). If the second side fails, report a clear partial-state error — do not silently leave one side wired. (Consider best-effort rollback of side 1, or at minimum a precise error naming which side is wired.)

### `unlink_blocker(blocker_uuid, dependent_uuid)`
The inverse, for manual/explicit resolution:
1. Remove the dependent's link from the blocker's `Gates:` block (drop the whole block + label if it empties).
2. Remove the blocker's link from the dependent's `Gated by:` block (drop block + label if it empties).
3. If the dependent has no remaining `Gated by:` entries, remove the `gated` tag (merge-aware — leave its other tags).

### `reconcile_completion(uuid)` (auto-cleanup — approved)
Things has no event hooks, so cleanup is caller-triggered, not automatic on the Things side. Given a just-completed/canceled task uuid, scrub every dangling reference to it:
- If `uuid` was a **dependent**: run the `unlink_blocker` cleanup against each blocker it referenced.
- If `uuid` was a **blocker**: for each task it gated, remove the matching `Gated by:` entry and drop `gated` if that task has no other blockers.
- Idempotent and safe to call on a task with no relations (no-op).
- OM/callers invoke this when they mark work done. (Optional future: a sweep that reconciles all tasks; out of scope here.)

## Managed-block format & parsing

Rendering (plain text — Things renders no Markdown, it only auto-links the bare URL):

```
Gated by:
Reconfigure compositor VLAN200
things:///show?id=ABC123
Order the SFP module
things:///show?id=DEF456
```

Each entry is two lines: a title line (whatever `things.get` reports as the item's title) immediately followed by its bare `things:///show?id=<uuid>` deep link. In Things this shows the title as scannable plain text with a tappable URL beneath it.

Parsing rule: locate the label line (`Gated by:` / `Gates:`), then consume the contiguous (title line, deep-link line) pairs that follow — a line is an entry's URL when it matches `^things:///show\?id=<uuid>$`, and the line immediately above it is that entry's title. Dedup/add/remove by uuid, re-render, and splice back into notes preserving surrounding user text. Keep the block at a stable position (appended at end, separated by one blank line) so user-authored notes above it are never disturbed.

> Decision (resolved in build, 2026-06-26): plain multi-line, two lines per entry (title, then bare URL). The earlier markdown form (`[Title](url)` + `**label**`) was dropped once in-app testing showed Things renders neither — it displayed the literal syntax. Identity is the uuid in the URL line, so titles may be any text (brackets/parens included) without escaping.

## Where the code goes

- `writes.py` — the three functions, following the `update_item` argv/AppleScript pattern (user strings via argv, never embedded). Add small helpers: `_merge_tag`, `_render_relation_block`, `_parse_relation_block`, `_splice_notes`.
- `server.py` — thin `@mcp.tool()` wrappers (`link_blocker`, `unlink_blocker`, `reconcile_completion`) with schema-disciplined docstrings (see the title/notes docstring style at server.py:316+). Keep them thin — all logic in writes.py.
- `models.py` — reuse `SuccessResponse`/`ErrorResponse`. No new model likely needed.
- No `derivation.py` changes (this doesn't touch list derivation).

## Test plan

- `test_writes_unit.py` / `test_writes_mocked.py` — mock `run_applescript` + `things.get`; assert: tag merge doesn't clobber existing tags; notes splice is idempotent (double `link_blocker` → identical notes); many-to-many add; `unlink` removes block + drops `gated` only when last blocker gone; `reconcile_completion` scrubs both directions; partial-failure error path.
- `test_server.py` — tool wiring + docstring presence.
- Integration (`test_reads_integration.py` style, guarded) — optional round-trip against a real task if the suite runs against live Things.
- Honor **rule #8** everywhere: every verb verifies via `things.get` post-write and never reports unverified success.

## Out of scope (downstream, separate sessions)
- Global `~/.claude/CLAUDE.md` policy (~3 lines: gated = internal dependency; waiting = GTD hand-off, must not decay into "not now"; "wire blocks via the MCP verb, never hand-assemble links"). Do after the verb is named.
- OM task-structuring procedure that calls `link_blocker` during triage and `reconcile_completion` on done. Lands in `~/Projects/OfficeManager`.
- Retiring the TI_AV_LAN-scoped memory note once both ship.
