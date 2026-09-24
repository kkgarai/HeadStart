# engineer day planner

This folder is the skill packed inside the Chrome extension at `skill/`. Recipients **Load unpacked** the parent folder (the one with `manifest.json`) **or** install the Chrome Web Store listing. Do **not** copy or rsync this folder into `~/.cursor/skills/` or `~/.claude/skills/`.

## Install

**Chrome extension (recipients)**

Clone the repository, then load the folder that contains `manifest.json`. That folder is the clone root, not this `skill/` folder and not `extension/`.

```bash
git clone https://github.com/kkgarai/day-planner.git
cd day-planner
```

Enterprise copy, for people who already have access:

```bash
git clone https://github.com/kgarai_sfemu/day-planner.git
cd day-planner
```

In Chrome, `chrome://extensions` → Developer mode → **Load unpacked** → choose `day-planner`. First machine, once: double-click `Install Mac.command`, `Install Windows.bat`, or `Install Linux.sh` in that folder. After that, the toolbar starts the bridge. Do not copy this folder into a skills directory.

To remove the host, double-click `Uninstall Mac.command`, `Uninstall Windows.bat`, or `Uninstall Linux.sh` in that same folder, then remove the extension in `chrome://extensions`.

**AI CLI without the extension**

Keep the folder name `engineer-day-planner` if you invoke the skill from a chat on a machine that has no extension. That is a separate copy — do not keep it in sync with this extension from this repo.

## What is in this folder

| Path | Role |
|---|---|
| `SKILL.md` | The full skill (chat / copy). Run Planner injects `scripts/PLAN_SYSTEM.md` instead so the model prompt stays short. |
| `scripts/PLAN_SYSTEM.md` | Compact gather + publish instructions for Run Planner |
| `scripts/sanitize-briefing.py` | Fixes IST laptop clocks, empty Shift tiles, Needs you now, section order |
| `scripts/slim-tool-result.py` | Merge overflow CaseComment files into `/tmp/case-activity.json` (stdout is `ok` only) |
| `page/template.html` | Companion page |
| `scripts/publish-page.sh` | Writes `out/current.html` and opens it |
| `scripts/calendar-bridge.py` | Local Calendar update / RSVP / Run Planner |
| `scripts/edp-native-host.py` | Chrome native messaging host (Load unpacked or Chrome Web Store packed skill/) |
| `scripts/install-native-host.py` | One-time: write native-host JSON + start the local bridge |
| `out/` | Generated `current.html` + `briefing.json` each run — never ship a published page |

Do not put templates, scripts, or the published page anywhere else. Recipients of the extension do not need a second kit.

The page is empty until this engineer runs the skill. Do not pack sample cases, live cases, or `examples/` into the Chrome extension.

Needs `python3` and `curl`. Prefers [http://127.0.0.1:8765/](http://127.0.0.1:8765/); next free port through 8799 if 8765 is taken. It does **not** kill whatever else is using 8765. `DAY_PLANNER_PORT=9000` starts the search at that port. `DAY_PLANNER_NO_OPEN=1` skips launching a browser.

## Live brief (agents)

The page needs the same tools the recipient already uses for Support work: OrgCS, GUS, Slack, Gmail, Calendar, Assembled, Omni. Gmail and Calendar are usually **Google Workspace** on that machine (`search_gmail_messages`, `list_calendars`, `get_events`) — not a Salesforce-only `gmail_search` plugin. Assembled is a calendar on that list, not its own MCP. GUS is `query_gus_records` (or `sf` against `gus`). This folder does **not** ship MCP config, Bearer tokens, or org credentials. Point the AI host you already use at the tools you already have. A published page is a successful run — do not pop Tools offline because the tool names differ.

**Update calendar**, **Yes / No / Maybe**, and **Refresh calendar** need Google Workspace MCP on that machine (`manage_event` / `get_events`). The bridge reads whatever MCP config this machine already has (Cursor, Claude Code, OpenCode, Claude Desktop, project `.cursor/mcp.json`). It does not require a Salesforce-only plugin path. If that MCP is missing, the rest of the page still works.

Gather stays read-only. Calendar writes happen only when someone clicks Update calendar or RSVP on the page. Refresh calendar is a read of **today’s primary calendar** (shift ± 2 hours in the shift zone, plus gather-marked important meetings outside that pad).

## What this folder must never contain

- MCP `.mcp.json` files or Authorization headers
- Live or sample case JSON, or a published `out/current.html`
- Paths that only exist on one laptop (`/Users/…`)

Output is always `<this-folder>/out/current.html`.
