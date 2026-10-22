#!/usr/bin/env python3
"""HeadStart setup demo.

Detects this computer, asks which installed browser to use, clones the main
branch when this file is not already inside a checkout, registers the native
host, and opens that browser's extensions page.

Chrome still needs one click: Developer mode, then Load unpacked, on the
folder this script prints.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/kkgarai/HeadStart.git"
BRANCH = "main"


def home() -> Path:
    return Path.home()


def clone_dest() -> Path:
    return home() / "HeadStart"


def repo_root_from(path: Path) -> Path | None:
    if (path / "manifest.json").is_file() and (path / "extension").is_dir():
        return path
    return None


def this_checkout() -> Path | None:
    return repo_root_from(Path(__file__).resolve().parent)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, **kwargs)


def ensure_tools() -> None:
    missing = [name for name in ("git", "python3" if os.name != "nt" else "") if name and shutil.which(name) is None]
    if os.name == "nt":
        if not (shutil.which("py") or shutil.which("python") or shutil.which("python3")):
            missing.append("Python 3")
    if missing:
        raise SystemExit("Required and not found: " + ", ".join(missing))


def ensure_checkout() -> Path:
    here = this_checkout()
    if here is not None:
        return here
    ensure_tools()
    dest = clone_dest()
    if dest.exists() and not (dest / ".git").is_dir():
        raise SystemExit(f"{dest} exists and is not a git checkout.")
    if not (dest / ".git").is_dir():
        print(f"Cloning {BRANCH} into {dest}")
        result = run(["git", "clone", "--branch", BRANCH, "--single-branch", REPO, str(dest)])
        if result.returncode != 0:
            raise SystemExit("Clone failed.")
    else:
        print(f"Updating {dest} ({BRANCH})")
        if run(["git", "-C", str(dest), "fetch", "origin", BRANCH]).returncode != 0:
            raise SystemExit("Could not fetch.")
        if run(["git", "-C", str(dest), "checkout", BRANCH]).returncode != 0:
            raise SystemExit("Could not check out " + BRANCH + ".")
        if run(["git", "-C", str(dest), "pull", "--ff-only", "origin", BRANCH]).returncode != 0:
            raise SystemExit("Could not fast-forward " + BRANCH + ".")
    script = dest / "setup-headstart.py"
    if not script.is_file():
        raise SystemExit(f"Checkout has no {script.name}.")
    os.execv(sys.executable, [sys.executable, str(script), *sys.argv[1:]])


class Browser:
    def __init__(self, label: str, url: str, launch: list[str]):
        self.label = label
        self.url = url
        self.launch = launch


def _mac_app(names: list[str]) -> Path | None:
    roots = [Path("/Applications"), home() / "Applications"]
    for root in roots:
        for name in names:
            app = root / name
            if app.is_dir():
                return app
    return None


def _win_exe(names: list[str]) -> Path | None:
    roots = []
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        raw = os.environ.get(key)
        if raw:
            roots.append(Path(raw))
    for root in roots:
        for name in names:
            exe = root / name
            if exe.is_file():
                return exe
    return None


def _linux_bin(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def installed_browsers() -> list[Browser]:
    """Chromium browsers that can be launched. Leftover profile folders are ignored."""
    catalog = [
        ("Google Chrome", "chrome://extensions", ["Google Chrome.app"], [r"Google\Chrome\Application\chrome.exe"], ["google-chrome", "google-chrome-stable"]),
        ("Chrome Beta", "chrome://extensions", ["Google Chrome Beta.app"], [r"Google\Chrome Beta\Application\chrome.exe"], ["google-chrome-beta"]),
        ("Chrome Dev", "chrome://extensions", ["Google Chrome Dev.app"], [r"Google\Chrome Dev\Application\chrome.exe"], ["google-chrome-unstable"]),
        ("Chrome Canary", "chrome://extensions", ["Google Chrome Canary.app"], [r"Google\Chrome SxS\Application\chrome.exe"], []),
        ("Microsoft Edge", "edge://extensions", ["Microsoft Edge.app"], [r"Microsoft\Edge\Application\msedge.exe"], ["microsoft-edge", "microsoft-edge-stable"]),
        ("Brave", "brave://extensions", ["Brave Browser.app"], [r"BraveSoftware\Brave-Browser\Application\brave.exe"], ["brave-browser", "brave"]),
        ("Vivaldi", "vivaldi://extensions", ["Vivaldi.app"], [r"Vivaldi\Application\vivaldi.exe"], ["vivaldi", "vivaldi-stable"]),
        ("Chromium", "chrome://extensions", ["Chromium.app"], [r"Chromium\Application\chrome.exe"], ["chromium", "chromium-browser"]),
    ]
    found: list[Browser] = []
    for label, url, mac_apps, win_exes, linux_bins in catalog:
        launch: list[str] = []
        if sys.platform == "darwin":
            app = _mac_app(mac_apps)
            if app is None:
                continue
            launch = ["open", "-a", str(app), url]
        elif os.name == "nt":
            exe = _win_exe(win_exes)
            if exe is None:
                continue
            launch = [str(exe), url]
        else:
            binary = _linux_bin(linux_bins)
            if binary is None:
                continue
            launch = [binary, url]
        found.append(Browser(label, url, launch))
    return found


def choose_browser(found: list[Browser]) -> Browser | None:
    if not found:
        return None
    if len(found) == 1 or not sys.stdin.isatty():
        print("Browser: " + found[0].label)
        return found[0]
    print("Choose a browser:")
    for index, browser in enumerate(found, 1):
        print(f"  {index}. {browser.label}")
    while True:
        raw = input("Number [1]: ").strip()
        if raw == "":
            return found[0]
        if raw.isdigit() and 1 <= int(raw) <= len(found):
            return found[int(raw) - 1]
        print("Enter a number from the list.")


def register_host(root: Path) -> int:
    script = root / "extension" / "skill" / "scripts" / "install-native-host.py"
    if not script.is_file():
        print(f"Missing {script}", file=sys.stderr)
        return 1
    print("Registering the local planner host…")
    return run([sys.executable, str(script)], cwd=str(root)).returncode


def open_extensions(browser: Browser | None) -> None:
    if browser is None or not browser.launch:
        print("No Chromium browser was launched. Open its extensions page yourself.")
        return
    print(f"Opening {browser.label} at {browser.url}")
    run(browser.launch)


def main() -> int:
    if "--list-browsers" in sys.argv:
        for browser in installed_browsers():
            print(browser.label)
        return 0
    root = ensure_checkout()
    print(f"HeadStart folder: {root}")
    browser = choose_browser(installed_browsers())
    if browser is None:
        print("No Chrome, Edge, Brave, Vivaldi, or Chromium install was found.")
    status = register_host(root)
    open_extensions(browser)
    print()
    print("One step is still in the browser:")
    print("  Developer mode → Load unpacked →")
    print(f"  {root}")
    print("Then click the HeadStart toolbar icon.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
