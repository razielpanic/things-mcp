# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
