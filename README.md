# Day Planner

Chrome extension for an engineer’s shift. Version **0.2.2.2 beta**.

Python 3 is required. It is the same Python the planner uses later to run the model. This package does not install Python, Claude Code, or the OrgCS, Slack, Gmail, or Calendar logins.

Load this folder in Chrome. Do not copy `skill/` into `~/.cursor/skills` or `~/.claude/skills`.

## Install

Clone the public repository, then load that folder in Chrome. The folder contains `manifest.json`, the three installers, and an `extension` folder with the rest of the files.

```bash
git clone https://github.com/kkgarai/day-planner.git
cd day-planner
```

1. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → choose the `day-planner` folder you just cloned. That is the folder with `manifest.json`, not the `extension` folder inside it.

2. Click the toolbar icon. The first time, the page says the bridge is not running. Go back to that same folder and double-click the installer for your computer. The page lists all three and marks the one for this computer.

Do the double-click once per machine. After that, the toolbar starts the bridge. Quitting Chrome does not ask for the installer again.

If the bridge has stopped, the page sends you back to that folder to double-click the same installer.

3. Return to Chrome and click the toolbar icon again. The page asks for the Express token, the model, and the runner.

### What the installer does

The installer registers a small local helper so the toolbar can start the planner bridge. It does not install Python, Claude Code, or any login.

| This computer | Double-click | What it runs |
|---|---|---|
| Mac | `Install Mac.command` | `python3 extension/skill/scripts/install-native-host.py` |
| Windows | `Install Windows.bat` | `py -3`, or `python`, or `python3`, on `extension\skill\scripts\install-native-host.py` |
| Linux | `Install Linux.sh` | `python3 extension/skill/scripts/install-native-host.py` |

On every platform that helper:

- Registers the native host for Chrome, Chrome Beta, Chrome Dev, Chrome Canary, Chromium, Edge, Brave, and Vivaldi where that browser is installed.
- On Windows, also writes the native-host registry keys Chrome uses.
- On a Mac, writes a login helper that does not keep the bridge running after you quit Chrome.
- If this folder is on the Desktop, in Documents, or in Downloads, copies the bridge to a local snapshot so Chrome is allowed to start it.
- Records the GitHub branch this clone follows, so **Update** can see a newer version even when Chrome cannot read those folders.

On a Mac, the first double-click can be blocked until you right-click the file and choose **Open**. On Windows, SmartScreen can ask you to allow it once. On Linux, if the file will not run, mark it executable (`chmod +x "Install Linux.sh"`) and double-click it again, or run it from a terminal.

Python 3 has to be on the machine already. The Windows file looks for `py -3`, then `python`, then `python3`.

## Uninstall the host

Double-click the uninstaller in that same folder. Then open `chrome://extensions` and remove Day Planner. The uninstaller does not delete the cloned folder, and it does not remove the extension from Chrome.

| This computer | Double-click | What it runs |
|---|---|---|
| Mac | `Uninstall Mac.command` | `python3 extension/skill/scripts/uninstall-native-host.py` |
| Windows | `Uninstall Windows.bat` | `py -3`, or `python`, or `python3`, on `extension\skill\scripts\uninstall-native-host.py` |
| Linux | `Uninstall Linux.sh` | `python3 extension/skill/scripts/uninstall-native-host.py` |

On every platform that helper:

- Stops the bridge only when it belongs to this copy.
- Removes the native-host registration for the same browsers the installer wrote.
- On Windows, removes those registry keys.
- On a Mac, removes the login helper.
- Removes the host program, the version snapshots, and the cache.
- Leaves the cloned folder and your Done marks in that folder.

This host name is shared with any other Day Planner on the same computer. After uninstall, that other copy needs its installer again.

## Update

**Update** appears on the extension bar when a newer version is available for this copy. Click it. The extension reloads, and the bridge starts from that version. Done marks stay on this computer.

If this folder is on the Desktop, in Documents, or in Downloads, double-click the installer once. Chrome cannot read those folders. The installer records the GitHub branch that Update follows.

## What you still set up yourself

- Python 3
- Claude Code, or another runner the panel lists
- Express LLM Gateway token
- Sign-in for OrgCS, GUS, Slack, Gmail, and Calendar on that machine

The extension does not ship those.
