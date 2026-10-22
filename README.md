# HeadStart

Chrome extension for an engineer’s shift. Version **0.2.6.15 beta**.

Python 3 is required. It is the same Python the planner uses later to run the model. This package does not install Python, Claude Code, or the OrgCS, Slack, Gmail, or Calendar logins.

Download the zip. Do not clone the repository yourself, and do not download an installer on its own. Unzip it, then run the installer for this computer. That installer downloads the rest.

## Install

[![Download](https://img.shields.io/badge/Download-HeadStart--Setup.zip-2ea44f?style=for-the-badge)](https://github.com/kkgarai/HeadStart/releases/download/v0.2.6.8/HeadStart-Setup.zip)

The zip contains three installers. Use the one for this computer.

| This computer | Double-click |
|---|---|
| Mac | `Setup Mac.command` |
| Windows | `Setup Windows.bat` |
| Linux | `Setup Linux.sh` |

On a Mac, double-click the zip, then double-click `Setup Mac.command`. The first open can be blocked until you right-click that file and choose **Open**. On Windows, SmartScreen can ask you to allow it once. On Linux, if the file will not run, mark it executable (`chmod +x "Setup Linux.sh"`) and double-click it again.

The installer detects this computer. It asks which installed browser to use, downloads HeadStart into `~/HeadStart` (`%USERPROFILE%\HeadStart` on Windows), registers the planner host, and opens that browser’s extensions page. It prints the folder to load. That folder contains `manifest.json`.

1. In the browser it opened: turn on **Developer mode** → **Load unpacked** → choose the folder it printed.

2. Click the HeadStart toolbar icon. The page asks for the Express token, the model, and the runner.

If the bridge has stopped, double-click the same installer again.

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
