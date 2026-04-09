# things-mcp

An MCP server for [Things 3](https://culturedcode.com/things/) that lets Claude read and write your Things database the way Things actually works — not the way most MCP servers *think* it works.

## Why this exists

If you've used another Things MCP server with Claude, you've probably noticed a few odd things: items that should be in Today showing up as "Anytime", scheduling something for "anytime" mysteriously dumping it in Someday, or Claude confidently reporting the wrong list for an item. That's not Claude being confused — it's the MCP server handing Claude the wrong data.

`things-mcp` exists because Things 3's data model is subtle and widely misunderstood:

- **Today and Upcoming aren't folders.** They're *computed views*. An item is "in Today" because its `start_date` is today or earlier — not because anything says "this item belongs to Today".
- **The `start` flag is sticky.** It only takes three values (Inbox, Anytime, Someday) and it does **not** change when an item moves to Today or Upcoming. Most MCP servers read `start` as "the current list", which is wrong.
- **Anytime ≠ Someday.** Scheduling something for "anytime" in Things means "I want to do this, no particular day" — it's the default active state. Someday is the deferred-indefinitely state. Mapping one to the other (which most MCP servers do) silently misfiles your tasks.

`things-mcp` computes the real list for every item it hands to Claude, uses the right write path for each operation (AppleScript for scheduling and moves, URL scheme only where it has to), and verifies every write landed before reporting success.

## What you can do

Once installed, you just talk to Claude normally:

> **You:** What's on my list for today?
> **Claude:** *(calls `get_today`)* You have 5 items in Today: "Review PR #42", "Call mom", "Finish draft email", "Grocery run", and "Book flight".

> **You:** Move "Book flight" to tomorrow.
> **Claude:** *(calls `schedule_item` with `when="tomorrow"`)* Done — "Book flight" is now scheduled for tomorrow. It'll show up in Upcoming until the date rolls over.

> **You:** Create a project called "Q2 Planning" with initial todos: draft goals, review last quarter, book offsite.
> **Claude:** *(calls `create_project` with a list of todo titles)* Created. "Q2 Planning" is in your Anytime list with 3 todos underneath it.

> **You:** Add a packing checklist to "Trip to Portland": passport, chargers, running shoes, rain jacket.
> **Claude:** *(calls `create_todo` with a checklist)* Done.

Claude handles the tool-calling automatically — you don't need to know or care about the tool names. But if you're curious, there's a [full tool reference](docs/tools.md).

## Install

### Claude Desktop (recommended)

1. Clone this repo somewhere stable:

   ```bash
   git clone https://github.com/YOUR_ORG/things-mcp.git ~/Projects/things-mcp
   cd ~/Projects/things-mcp
   ```

2. Install dependencies into your system Python (or a venv — your call):

   ```bash
   pip install -e .
   ```

3. Add `things-mcp` to your Claude Desktop config at `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

   Replace `/Users/YOU/Projects/things-mcp` with the path where you cloned the repo. Use an absolute path — Claude Desktop doesn't expand `~`.

4. **Quit and relaunch Claude Desktop.** It only loads MCP servers on startup. Any time you update the code, you'll need to relaunch.

5. Ask Claude "what's in my Today list?" to verify it's working.

Full setup walkthrough (Claude Code, troubleshooting, auth token for advanced operations) is in [docs/setup.md](docs/setup.md).

## How it works (short version)

Things 3 has two orthogonal dimensions:

- **Structure** (where it lives): Area → Project → Heading → To-do
- **Temporal placement** (when): derived from `start` flag + `start_date`

The list you see in Things is computed from this table:

| `start` flag | `start_date` | Actual list |
|-------------|-------------|-------------|
| Inbox | any | **Inbox** |
| Anytime or Someday | today or earlier | **Today** |
| Anytime or Someday | after today | **Upcoming** |
| Anytime | none | **Anytime** (default active state) |
| Someday | none | **Someday** (deferred) |

Completed/cancelled items are always in **Logbook**.

Every response from this MCP includes a `derived_list` field computed from the above — so Claude always knows the real list, not the stale `start` flag.

Want the full story? [docs/how-it-works.md](docs/how-it-works.md) explains the model in depth, including why `when=anytime` is the single most mis-mapped concept in other Things MCP servers.

## Docs

- **[Setup guide](docs/setup.md)** — Full installation for Claude Desktop and Claude Code, auth token setup, restart workflow, verification.
- **[How it works](docs/how-it-works.md)** — Cultured Code's data model, derivation logic, and why most Things MCP servers get it wrong.
- **[Tool reference](docs/tools.md)** — Every MCP tool, what it does, what it returns, and the gotchas.
- **[Troubleshooting](docs/troubleshooting.md)** — "Things 3 not running", "checklist didn't attach", "schedule didn't move the item", and other common issues.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, architecture pointers, test layout, and the rules for extending the write path without breaking Things' data model.

## License

MIT — see [LICENSE](LICENSE).
