# Day Planner

Chrome extension for an engineer’s shift. Version **0.2.0.0 beta**.

Python 3 is required. It is the same Python the planner uses later to run the model. This package does not install Python, Claude Code, or the OrgCS, Slack, Gmail, or Calendar logins.

Load this folder in Chrome. Do not copy `skill/` into `~/.cursor/skills` or `~/.claude/skills`.

## Install

Clone the public repository, then load that folder in Chrome. The folder contains `manifest.json`, the three installers, and an `extension` folder with the rest of the files.

```bash
git clone https://github.com/kkgarai/day-planner.git
cd day-planner
```

The enterprise copy, for people who already have access:

```bash
git clone https://github.com/kgarai_sfemu/day-planner.git
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

Double-click the uninstaller in that same folder. It removes the native host and stops the bridge that belongs to this copy. Then open `chrome://extensions` and remove Engineer Day Planner.

| This computer | Double-click |
|---|---|
| Mac | `Uninstall Mac.command` |
| Windows | `Uninstall Windows.bat` |
| Linux | `Uninstall Linux.sh` |

This host name is shared with any other Engineer Day Planner loaded on the same Mac. After uninstall, that other copy needs its installer again.

## Update

**Update** appears on the extension bar only when the branch this clone follows has a higher version than the one Chrome loaded.

The click fetches that branch and replaces the tracked files with it, then reloads the extension. The bridge then starts from those files. Done marks and holds live in `skill/out/`, which git does not track, so that click leaves them in place.

A clone of `main` only sees versions that were merged to `main`. Development builds stay on their own branch.

Versions are **0.2.x.y**. During development, raise **y** only (`0.2.0.1`, `0.2.0.2`) and leave those commits off `main`. Merge to `main` when **x** changes (`0.2.1.0`).

## What you still set up yourself

- Python 3
- Claude Code, or another runner the panel lists
- Express LLM Gateway token
- Sign-in for OrgCS, GUS, Slack, Gmail, and Calendar on that machine

The extension does not ship those.
