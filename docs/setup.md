# Setup guide

This walks you through installing `things-mcp` and wiring it into Claude Desktop (or Claude Code). It also covers the auth token setup for the handful of operations that need it, and how to verify everything is working.

If you just want the three-step install, the [README](../README.md#install) has you covered. This guide goes slower and explains *why* each step exists.

## Requirements

- macOS with Things 3 installed (the MCP reads Things' SQLite database and drives Things via AppleScript — it won't work on Windows or Linux, and it won't work without Things 3)
- Python 3.11 or later
- [Claude Desktop](https://claude.ai/download) or [Claude Code](https://docs.claude.com/claude-code) — either works

## Step 1: Clone the repository

Pick a stable location. This MCP server runs directly from the cloned source (no wheel install required, though you can do both), so wherever you put it is where it lives long-term.

```bash
git clone https://github.com/YOUR_ORG/things-mcp.git ~/Projects/things-mcp
cd ~/Projects/things-mcp
```

Substitute any path you like — just remember the absolute path, you'll need it for the MCP client config in step 3.

## Step 2: Install dependencies

You have two options:

**Option A — install into your system Python (simpler):**

```bash
pip install -e .
```

This installs `things-mcp`'s three runtime dependencies (`mcp[cli]`, `things.py`, `pydantic`) and makes the `things-mcp` CLI command available on your PATH. The `-e` flag means "editable install" — future `git pull`s are picked up automatically without reinstalling.

**Option B — use a virtualenv (cleaner isolation):**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you go this route, your MCP client config needs to point at the venv's Python explicitly (see Step 3).

Either way, verify the install:

```bash
python3 -c "import things_mcp; print('ok')"
```

Should print `ok`. If it prints an import error, your Python environment can't find the package — double-check you're in the repo root and the install completed.

## Step 3: Wire it into your MCP client

### Claude Desktop (macOS)

Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json`. Add a `things` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "things": {
      "command": "python3",
      "args": ["-m", "things_mcp.server"],
      "env": {
        "PYTHONPATH": "/Users/YOU/Projects/things-mcp/src"
      }
    }
  }
}
```

**Important details:**

- Replace `/Users/YOU/Projects/things-mcp` with your actual absolute path. Claude Desktop does not expand `~`, `$HOME`, or environment variables in this config.
- The `command` should be whatever Python interpreter has the dependencies installed. If you used a venv (Option B above), point at `".venv/bin/python"` with the full absolute path.
- `PYTHONPATH` points at the `src/` directory so Python can find the `things_mcp` package. This works whether or not you ran `pip install -e .`, which is nice because it means the MCP works straight from a `git clone` with no install step if you already have the dependencies elsewhere.
- If you have other MCP servers in the same config (mail, calendar, whatever), just add `things` alongside them inside the `mcpServers` object.

**Then quit and relaunch Claude Desktop.** It loads MCP servers only at startup, and it caches them for the life of the app. Any time you update the code (e.g. `git pull`), you need to relaunch Claude Desktop for the changes to take effect.

### Claude Code

Claude Code uses a different config path. Add the `things` server to your project's `.claude.json` under the working-directory entry, or to `~/.claude.json` for a global registration:

```json
{
  "mcpServers": {
    "things": {
      "command": "python3",
      "args": ["-m", "things_mcp.server"],
      "env": {
        "PYTHONPATH": "/Users/YOU/Projects/things-mcp/src"
      }
    }
  }
}
```

Claude Code picks up MCP servers on session start. Restart your Claude Code session (or open a new terminal) after editing the config.

## Step 4: Verify it's working

Open Claude Desktop (or Claude Code), and in a new conversation ask:

> What's in my Today list in Things 3?

Claude should call the `get_today` tool and show you the items actually in your Today list, each with a `derived_list: "Today"` field confirming the derivation ran. If there's nothing in Today, Claude will say so — that's still a successful test (it means the MCP is running and returning an empty list, not erroring out).

If you see an error response like `THINGS_UNAVAILABLE` or `READ_ERROR`, jump to [troubleshooting](troubleshooting.md).

## Step 5 (optional): Set up the auth token

Most operations don't need an auth token. These ones do:

- `update_item` on a todo that already has a checklist
- Any operation that uses `things:///update` or `things:///delete` URL schemes

Things 3's "protected" URL scheme endpoints require a per-install token that you enable in Things' own settings:

1. Open Things 3
2. Go to **Things → Settings → General**
3. Enable **"Enable Things URLs"** (or similar — the exact label varies by version)
4. Copy the generated token
5. Save it to `~/.things-auth` (plain text, no newlines, just the token):

   ```bash
   echo -n "YOUR_TOKEN_HERE" > ~/.things-auth
   chmod 600 ~/.things-auth
   ```

The MCP reads this file when it needs the token. If the file is missing and an operation requires it, you'll get a `NO_AUTH_TOKEN` error with a message pointing you back to this step.

You can do all the basic things — read lists, create todos with or without checklists, schedule, move items between projects, delete, create projects — without the auth token. It's only the in-place update operations on existing checklists that need it.

## Updating

When you want to pull new changes:

```bash
cd ~/Projects/things-mcp
git pull
# if you used a venv, reactivate it
# editable install picks up the code changes automatically
# just relaunch your MCP client (Claude Desktop) to reload the server
```

No reinstall needed. The `-e` flag on `pip install` means Python resolves imports against the repo directly.

## What next

- [How it works](how-it-works.md) — the Cultured Code data model, so you understand what the MCP is doing under the hood
- [Tool reference](tools.md) — full list of what Claude can do via the MCP
- [Troubleshooting](troubleshooting.md) — common issues and fixes
