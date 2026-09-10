# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **List views carry checklist rows.** `get_today`, `get_upcoming`,
  `get_anytime`, `get_someday`, `get_inbox`, `get_logbook` and `search`
  reported `checklist: []` for every item, while `get_item` on the same uuid
  showed the rows. A list-view row from things.py carries only the
  `TMTask.checklist` 0/1 flag, which the mapper treated as "no checklist".
  Flagged items are now resolved in one batched query per list. (#29)
- **`search` matches checklist-row text.** things.py's search covers title,
  notes and area title; a to-do whose only match was inside a checklist row
  was invisible. Such parents are now found through the checklist table and
  returned through the same filters. (#29)
- **`get_logbook(period=…)` filters by completion date.** It passed `period`
  through as things.py's `last=`, which limits by *creation* date, so
  `period="1d"` returned only items both created and completed within a day
  and an item finished this morning but created last month was missing. The
  period is now a calendar-day window on the completion date: `"1d"` is
  yesterday and today, `"0d"` is today only. (#24)

## [0.3.0] - 2026-09-07

### Added

- **`completed=false` / `canceled=false` reopen a logbooked item** — status
  returns to `incomplete` and the item leaves the Logbook for its temporal
  placement. Previously the `false` case matched no branch, wrote nothing, and
  returned `"Updated item: ."` — a success message for a call that did nothing,
  which a caller cannot tell from a real update. Each flag acts only on the
  status it names: `completed=false` un-completes, `canceled=false` un-cancels,
  and a mismatched flag reports a no-op rather than reaching for the other
  status. An explicit close beats a reopen when both are passed.

### Changed

- **Undeclared tool arguments are rejected instead of dropped.** Every tool's
  argument model forbids extras and publishes `additionalProperties: false`, so
  a misspelled or obsolete parameter fails validation before the tool body runs
  and nothing is written. **This can reject calls that previously "succeeded".**
  They were not succeeding: `create_todo(title=…, list_title="Today")` returned
  a real uuid and filed the item to the Inbox, and a typo like `projct_uuid`
  became a silently partial write.
- **`temporal_state.evening` reports a real value, and its type is now
  `Optional[bool]`.** `null` means the flag could not be read and is not a
  claim that the item is out of the evening.

### Fixed

- **The evening flag was a constant.** It was read as
  `bool(raw.get("evening", False))` from a things.py dict that has never
  contained an `evening` key, so it was `False` for every item, in every call,
  for the life of the field. It now reads `TMTask.startBucket` from the
  database. Three issues were filed against this constant, all blaming the
  write path for evening scheduling "not taking" when the writes had landed.
- **The evening reader no longer caches its SQLite connection**, which had
  bought three silent failures: it served a deleted inode after the database
  file was replaced (Things Cloud re-sync, restore), it was not thread-safe
  despite holding a lock, and it leaked a file descriptor per failed open.
  `startBucket` is nullable, and NULL now reads as unknown rather than `False`.
- **The write census survives concurrent processes.** It was an unlocked
  read-modify-write ending in a truncating write, so a second process reading
  mid-write reset the whole file to a single entry, silently. Measured at 4
  processes × 400 writes reporting 15. Now flock-guarded and written via
  `os.replace`.
- **Census regime accounting** credited every closing regime with the running
  total rather than its own count, inflating narrow instrumentation into
  looking well-observed.
- **The unexpected-close guard covers every write tool**, not the two it
  started with — `move_to_context`, `link_blocker`, `unlink_blocker` and
  `reconcile_completion` were writing unwatched. Coverage is enforced by a test
  that derives the tool surface from `server.py`.
- **`update_item` decides status writes from the current status**, not one read
  before the `when=` and project/area delegations ran.
- **`link_blocker` no longer claims "LEFT AS-IS"** when its guard fires after
  one side of the relation has already been written.

### Internal

- The test fixture database is built per session instead of being committed.
  The stored copy had rotted (its dates are relative to generation, so a
  deadline aged past today and changed what `things.today()` returned) and
  drifted (columns added to the `.sqlite` by hand and never to the generator,
  including an `evening` column Things has never had). Tests are also
  redirected away from the real Things database wholesale, rather than only
  where a fixture was requested.

## [0.2.0] - 2026-06-26

### Added

- **Blocker-relation verbs** — `link_blocker`, `unlink_blocker`, and
  `reconcile_completion` give Things a "blocked by" task-to-task relation it has
  no native concept of. A blocked task gets the `gated` tag plus a bidirectional
  deep link in notes: the dependent lists what it's `Gated by:` and the blocker
  lists what it `Gates:`. The verbs own atomicity, idempotency, many-to-many
  wiring, and cleanup; tags and notes are read-merged-rewritten, never
  clobbered. Both sides are verified via `things.get` after every write
  (CLAUDE.md rule #8). `reconcile_completion` scrubs a finished task from every
  relation in both directions.

### Changed

- Managed blocker blocks render as **plain text** — a `Gated by:` / `Gates:`
  label line, then a title line + a bare `things:///show?id=…` deep link per
  entry — rather than Markdown. Per Cultured Code's
  [Markdown Guide](https://culturedcode.com/things/support/articles/4651820/),
  Things 3 always displays Markdown syntax literally and only auto-links bare
  URLs, so the plain form reads cleanly in-app while the link stays clickable.

### Fixed

- `.gitignore` now actually ignores `.venv-fda/` — a trailing comment on the
  pattern line was being parsed as part of the pattern.

### Documentation

- Documented the three new tools in the tool reference and corrected the stale
  `update_item` entry, including a caution that `update_item` replaces tags and
  notes wholesale and will drop a gated task's tag and managed blocks.

## [0.1.0]

Initial public release — Cultured-Code-aligned read, query, and write tools
built on the core insight that Things' temporal lists (Today, Upcoming, Anytime,
Someday) are computed views derived from `start` + `start_date`, not containers.
167 tests.
