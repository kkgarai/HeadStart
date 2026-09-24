#!/usr/bin/env python3
"""Remove the native host this folder's installer registered.

Stops a bridge only when its /health root is this skill, or the snapshot
written for this skill. Does not remove the Chrome extension. Chrome still
has to be removed from chrome://extensions.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.request

HOST_NAME = "com.kgarai.engineerdayplanner.bridge"
LAUNCH_LABEL = "com.engineerdayplanner.bridge"
LEGACY_LAUNCH_LABELS = ("com.kgarai.engineer-day-planner.bridge",)
PORT_START = 8765
PORT_END = 8799


def support_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "Application Support" / "engineer-day-planner"


def this_skill() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def read_note(name: str) -> pathlib.Path | None:
    try:
        raw = (support_dir() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return pathlib.Path(raw)


def same_path(left: pathlib.Path | None, right: pathlib.Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.4) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("service") != "engineer-day-planner":
        return None
    return data


def under_runs(path: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to((support_dir() / "runs").resolve())
    except (OSError, ValueError):
        return False
    return True


def should_stop(root: str, skill: pathlib.Path) -> bool:
    if not root:
        return False
    heard = pathlib.Path(root)
    if same_path(heard, skill) or same_path(heard, read_note("run-skill.txt")):
        return True
    return under_runs(heard)


def stop_our_bridges(skill: pathlib.Path) -> int:
    stopped = 0
    for port in range(PORT_START, PORT_END + 1):
        data = health(port)
        if not data or not should_stop(str(data.get("root") or ""), skill):
            continue
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/__shutdown",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=1).read()
            stopped += 1
            print(f"Stopped bridge on port {port}")
        except Exception as exc:
            print(f"Could not stop port {port}: {exc}", file=sys.stderr)
    return stopped


def host_dirs() -> list[pathlib.Path]:
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        names = [
            "Google/Chrome",
            "Google/Chrome Beta",
            "Google/Chrome Dev",
            "Google/Chrome Canary",
            "Chromium",
            "BraveSoftware/Brave-Browser",
            "Microsoft Edge",
            "Microsoft Edge Beta",
            "Vivaldi",
        ]
        return [support.joinpath(*name.split("/"), "NativeMessagingHosts") for name in names]
    if os.name == "nt":
        local = pathlib.Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        names = [
            "Google/Chrome/User Data",
            "Google/Chrome Beta/User Data",
            "Google/Chrome Dev/User Data",
            "Google/Chrome SxS/User Data",
            "Chromium/User Data",
            "Microsoft/Edge/User Data",
            "BraveSoftware/Brave-Browser/User Data",
            "Vivaldi/User Data",
        ]
        return [local.joinpath(*name.split("/"), "NativeMessagingHosts") for name in names]
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    names = [
        "google-chrome",
        "google-chrome-beta",
        "google-chrome-unstable",
        "chromium",
        "BraveSoftware/Brave-Browser",
        "microsoft-edge",
    ]
    return [cfg.joinpath(*name.split("/"), "NativeMessagingHosts") for name in names]


def remove_host_manifests() -> int:
    removed = 0
    name = HOST_NAME + ".json"
    for folder in host_dirs():
        path = folder / name
        try:
            path.unlink()
            removed += 1
            print(f"Removed {path}")
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Could not remove {path}: {exc}", file=sys.stderr)
    return removed


def remove_windows_registry() -> None:
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    hives = (
        "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME,
        "Software\\Google\\Chrome Beta\\NativeMessagingHosts\\" + HOST_NAME,
        "Software\\Google\\Chrome Dev\\NativeMessagingHosts\\" + HOST_NAME,
        "Software\\Chromium\\NativeMessagingHosts\\" + HOST_NAME,
        "Software\\Microsoft\\Edge\\NativeMessagingHosts\\" + HOST_NAME,
        "Software\\BraveSoftware\\Brave-Browser\\NativeMessagingHosts\\" + HOST_NAME,
    )
    for hive in hives:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, hive)
            print(f"Removed registry {hive}")
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Could not remove registry {hive}: {exc}", file=sys.stderr)


def remove_host_binary() -> None:
    dest = support_dir() / "edp-native-host"
    if sys.platform == "darwin" and dest.exists():
        subprocess.run(["chflags", "nouchg", str(dest)], capture_output=True, text=True)
    for path in (dest, support_dir() / ".edp-native-host.tmp"):
        try:
            path.unlink()
            print(f"Removed {path}")
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Could not remove {path}: {exc}", file=sys.stderr)


def remove_launch_agent() -> None:
    if sys.platform != "darwin":
        return
    uid = os.getuid()
    labels = (LAUNCH_LABEL,) + LEGACY_LAUNCH_LABELS
    for label in labels:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
        )
        plist = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        try:
            plist.unlink()
            print(f"Removed {plist}")
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Could not remove {plist}: {exc}", file=sys.stderr)


def cache_dir() -> pathlib.Path:
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "engineer-day-planner"
    if os.name == "nt":
        return pathlib.Path(os.environ.get("LOCALAPPDATA") or home) / "engineer-day-planner"
    root = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
    return root / "engineer-day-planner"


def unlock(path: pathlib.Path) -> None:
    if sys.platform != "darwin" or not path.exists():
        return
    subprocess.run(["chflags", "-R", "nouchg", str(path)], capture_output=True, text=True)


def remove_tree(path: pathlib.Path) -> None:
    if not path.exists():
        return
    unlock(path)
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        print(f"Could not remove {path}", file=sys.stderr)
        return
    print(f"Removed {path}")


def remove_file(path: pathlib.Path) -> None:
    try:
        path.unlink()
        print(f"Removed {path}")
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"Could not remove {path}: {exc}", file=sys.stderr)


def remove_owned_files(skill: pathlib.Path) -> None:
    """Drop every file this install wrote outside the clone."""
    remove_tree(support_dir() / "runs")
    remove_tree(cache_dir())
    for name in (
        "extension-skill.txt",
        "run-skill.txt",
        "update-source.json",
        "apply-update.status",
        "apply-update.command",
    ):
        remove_file(support_dir() / name)
    remove_file(skill / "scripts" / "edp-native-host.cmd")
    try:
        support_dir().rmdir()
        print(f"Removed {support_dir()}")
    except OSError:
        pass


def main() -> int:
    skill = this_skill()
    print(f"Uninstalling the native host for {skill}")
    stop_our_bridges(skill)
    remove_host_manifests()
    remove_windows_registry()
    remove_host_binary()
    remove_launch_agent()
    remove_owned_files(skill)
    print("Native host removed.")
    print("In Chrome, open chrome://extensions and remove Engineer Day Planner.")
    print("Another copy of this extension on this Mac will need its installer again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
