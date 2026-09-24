# Day Planner

Chrome extension for an engineer’s shift. Version **0.2.0.8 beta**.

Python 3 is required. It is the same Python the planner uses later to run the model. This package does not install Python, Claude Code, or the OrgCS, Slack, Gmail, or Calendar logins.

Load this folder in Chrome. Do not copy `skill/` into `~/.cursor/skills` or `~/.claude/skills`.

## Install

Clone the public repository, then load that folder in Chrome. The folder contains `manifest.json`, the three installers, and an `extension` folder with the rest of the files.

```bash
git clone https://github.com/kkgarai/day-planner.git
cd day-planner
```

1. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → choose the `day-planner` folder you just cloned. That is the folder with `manifest.json`, not the `extension` folder inside it.

2. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → choose that folder.

3. Click the toolbar icon. The first time, the page says the bridge is not running. Go back to that same folder and double-click the installer for your computer:

| This computer | Double-click |
|---|---|
| Mac | `Install Mac.command` |
| Windows | `Install Windows.bat` |
| Linux | `Install Linux.sh` |

The page lists all three and marks the one for the computer you are on.

On a Mac, the first double-click can be blocked until you right-click the file and choose **Open**. On Windows, SmartScreen can ask you to allow it once.

4. Return to Chrome and click the toolbar icon again. The page asks for the Express token, the model, and the runner.

Do the double-click once per machine. After that, the toolbar starts the bridge. Quitting Chrome does not ask for the installer again.

If the bridge has stopped, the page sends you back to that folder to double-click the same installer.

## Uninstall the host

Double-click the uninstaller in that same folder. It stops this copy's bridge and removes the native host, its snapshots, and its cache. Then open `chrome://extensions` and remove Engineer Day Planner.

| This computer | Double-click |
|---|---|
| Mac | `Uninstall Mac.command` |
| Windows | `Uninstall Windows.bat` |
| Linux | `Uninstall Linux.sh` |

This host name is shared with any other Engineer Day Planner loaded on the same Mac. After uninstall, that other copy needs its installer again.

## Update

**Update** appears on the extension bar when a newer version is available for this copy. Click it. The extension reloads, and the bridge starts from that version. Done marks stay on this computer.

If this folder is on the Desktop, in Documents, or in Downloads, double-click the installer once. Chrome cannot read those folders. The installer records the GitHub branch that Update follows.

## What you still set up yourself

- Python 3
- Claude Code, or another runner the panel lists
- Express LLM Gateway token
- Sign-in for OrgCS, GUS, Slack, Gmail, and Calendar on that machine

The extension does not ship those.
