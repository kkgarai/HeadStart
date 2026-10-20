# HeadStart

Chrome extension for an engineer’s shift. Version **0.2.6.0 beta**.

Python 3 is required. It is the same Python the planner uses later to run the model. This package does not install Python, Claude Code, or the OrgCS, Slack, Gmail, or Calendar logins.

Load this folder in Chrome. Do not copy `skill/` into `~/.cursor/skills` or `~/.claude/skills`.

## Install

The setup file is in the repository root, next to `manifest.json`, on the `dev` branch.

| This computer | Double-click |
|---|---|
| Mac | `Setup Mac.command` |
| Windows | `Setup Windows.bat` |
| Linux | `Setup Linux.sh` |

Git and Python 3 have to be on the machine already. The setup file does not install them. On a Mac, the first double-click can be blocked until you right-click the file and choose **Open**. On Windows, SmartScreen can ask you to allow it once. On Linux, if the file will not run, mark it executable (`chmod +x "Setup Linux.sh"`) and double-click it again.

It detects Mac, Windows, or Linux. It does not ask you to pick the computer. It lists the Chromium browsers that can actually open — Chrome, Chrome Beta, Chrome Dev, Chrome Canary, Edge, Brave, Vivaldi, Chromium — and asks which one to use. Safari and Firefox are not offered.

If you did not start it from a checkout, it clones the `dev` branch into `~/HeadStart` (`%USERPROFILE%\HeadStart` on Windows). It then registers the planner host and opens that browser’s extensions page. The folder to load is printed in the terminal. That folder is the one with `manifest.json`, not the `extension` folder inside it.

1. In the browser it opened: turn on **Developer mode** → **Load unpacked** → choose the folder it printed.

2. Click the HeadStart toolbar icon. The page asks for the Express token, the model, and the runner.

The browser cannot be given the extension by the script. **Load unpacked** stays that one click.

### Host only

When the folder is already cloned, the host installer only registers the local helper. It does not clone, and it does not open a browser. Double-click it once per machine. After that, the toolbar starts the bridge. If the bridge has stopped, double-click the same file again.

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

The Windows file looks for `py -3`, then `python`, then `python3`.

## Uninstall the host

Double-click the uninstaller in that same folder. Then open `chrome://extensions` and remove HeadStart. The uninstaller does not delete the cloned folder, and it does not remove the extension from Chrome.

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

This host name is shared with any other HeadStart on the same computer. After uninstall, that other copy needs its installer again.

## Update

**Update** appears on the extension bar when a newer version is available for this copy. Click it. The extension reloads, and the bridge starts from that version. Done marks stay on this computer.

If this folder is on the Desktop, in Documents, or in Downloads, double-click the installer once. Chrome cannot read those folders. The installer records the GitHub branch that Update follows.

## What you still set up yourself

- Git
- Python 3
- Claude Code, or another runner the panel lists
- Express LLM Gateway token
- Sign-in for OrgCS, GUS, Slack, Gmail, and Calendar on that machine

The extension does not ship those.
