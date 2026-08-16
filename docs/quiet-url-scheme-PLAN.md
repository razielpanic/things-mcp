# Quiet URL-scheme writes — feature plan

Status: PLANNED (2026-08-16). Owner: RP. Origin: live incident, see below.

## Problem

Any write that routes through the `things:///` URL scheme foregrounds Things and
steals keyboard focus. On 2026-08-16 a `create_todo` with `checklist_items`
yanked focus mid-keystroke while RP was typing in another app. The same call
also returned `VERIFY_FAILED` *and* silently dropped the checklist — the to-do
was created (title/notes/project/when all correct) but with no checklist items
and a failed verification.

Two distinct defects:

1. **Focus steal.** `writes.py` launches the URL via
   `osascript -e 'open location "…"'`, which activates the handler app.
2. **Unreliable create + verify.** Title-based verification runs after a fixed
   0.5 s sleep; the incident shows the window is too tight (create landed,
   verify missed it) and the checklist payload never attached at all.

## Interim state (shipped 2026-08-16)

`checklist_items` is removed from the `create_todo` MCP tool schema in
`server.py` — the quiet AppleScript path is the only one the tool offers.
`writes.create_todo` retains full checklist support for direct callers.
`create_project` with initial to-dos still uses the URL scheme and still
foregrounds; it is rarely called with them, but it is the same defect.

## Plan

1. **Quiet launch.** Replace `osascript -e 'open location …'` with
   `/usr/bin/open -g "<url>"` at both URL-scheme call sites in `writes.py`
   (`create_todo`, `create_project`). `-g` opens without bringing the app
   forward. Verify empirically: Things in background, focus in another app,
   create with checklist → focus must not move. If `-g` proves insufficient
   for a URL handler, fallback candidate: `NSWorkspace.open` with
   `activates=False` via PyObjC (heavier dependency — only if needed).
2. **Fix the checklist drop.** Reproduce the 2026-08-16 payload against the
   `things:///json` endpoint and find why `checklist-items` didn't attach
   (key name / nesting / encoding). Add a mocked test pinning the exact JSON
   emitted, and one live-fixture test asserting the checklist lands.
3. **Verification with backoff.** Replace the single 0.5 s sleep with retries
   (e.g. 0.3 s × up to 5) in `_verify_url_scheme_write` and the title-based
   post-create search. `VERIFY_FAILED` on a write that actually landed is
   worse than slow verification — it invites double-creates.
4. **Re-expose `checklist_items`** in the `create_todo` schema once 1–3 are
   proven, with a docstring stating the quiet-launch guarantee. Remove the
   interim comment in `server.py`. Update `docs/tools.md` and the global
   CLAUDE.md Things section (which currently documents the removal).

## Acceptance

- Creating a to-do with a checklist moves neither focus nor the frontmost app,
  attaches every checklist item, and returns the verified UUID.
- No fixed-sleep verification remains in `writes.py`.
- `create_project` with initial to-dos is equally quiet.
