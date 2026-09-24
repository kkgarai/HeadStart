#!/usr/bin/env python3
"""Chrome native messaging host: start the local planner bridge if it is down.

Chrome launches this file from the native-host JSON (Load unpacked or Chrome
Web Store). Finds packed skill/ from env, the live Chrome/Edge install
(absolute unpacked path or Store Extensions/{id}/{version}), then __file__.
"""
from __future__ import annotations

import json
import os
import pathlib
import struct
import subprocess
import sys

CHROME_EXTENSION_ID = "ojpfakkcgmefanbomdfpglbioapoabfh"


def read_msg():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("<I", raw)[0]
    blob = sys.stdin.buffer.read(n)
    if not blob:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def write_msg(obj: dict) -> None:
    blob = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(blob)))
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()


def is_host_skill_copy(path: pathlib.Path) -> bool:
    try:
        text = str(path.resolve()).replace("\\", "/").lower()
    except OSError:
        return False
    return "/.claude/skills/" in text or "/.cursor/skills/" in text or "/claude/skills/" in text


def is_packed_extension_skill(path: pathlib.Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if is_host_skill_copy(resolved):
        return False
    if resolved.name != "skill":
        return False
    if not (resolved / "SKILL.md").is_file() or not (
        resolved / "scripts" / "calendar-bridge.py"
    ).is_file():
        return False
    parent = resolved.parent
    return (parent / "manifest.json").is_file() and (parent / "panel.html").is_file()


def as_skill(path: pathlib.Path) -> pathlib.Path | None:
    try:
        p = path.expanduser()
    except OSError:
        return None
    if is_packed_extension_skill(p):
        return p.resolve()
    nested = p / "skill"
    if is_packed_extension_skill(nested):
        return nested.resolve()
    return None


def rec_enabled(rec: dict) -> bool:
    reasons = rec.get("disable_reasons")
    if reasons in (None, 0, 0.0, "", [], {}):
        return True
    return False


def packed_version_tuple(skill: pathlib.Path) -> tuple[int, ...]:
    try:
        data = json.loads((skill.parent / "manifest.json").read_text(encoding="utf-8"))
        parts = str((data or {}).get("version") or "0").split(".")
        return tuple(int(p) if str(p).isdigit() else 0 for p in parts[:6])
    except Exception:
        return (0,)


def browser_user_data_roots() -> list[pathlib.Path]:
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return [
            support / "Google" / "Chrome",
            support / "Google" / "Chrome Beta",
            support / "Google" / "Chrome Dev",
            support / "Google" / "Chrome Canary",
            support / "Chromium",
            support / "Microsoft Edge",
            support / "Microsoft Edge Beta",
            support / "BraveSoftware" / "Brave-Browser",
            support / "Vivaldi",
        ]
    if os.name == "nt":
        local = pathlib.Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return [
            local / "Google" / "Chrome" / "User Data",
            local / "Google" / "Chrome Beta" / "User Data",
            local / "Google" / "Chrome Dev" / "User Data",
            local / "Google" / "Chrome SxS" / "User Data",
            local / "Chromium" / "User Data",
            local / "Microsoft" / "Edge" / "User Data",
            local / "BraveSoftware" / "Brave-Browser" / "User Data",
            local / "Vivaldi" / "User Data",
        ]
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    return [
        cfg / "google-chrome",
        cfg / "google-chrome-beta",
        cfg / "google-chrome-unstable",
        cfg / "chromium",
        cfg / "microsoft-edge",
        cfg / "BraveSoftware" / "Brave-Browser",
    ]


def iter_pref_files():
    skip = {"System Profile", "Guest Profile", "Snapshots"}
    for root in browser_user_data_roots():
        try:
            if not root.is_dir():
                continue
            for folder in root.iterdir():
                if not folder.is_dir() or folder.name in skip:
                    continue
                if "Snapshot" in folder.name:
                    continue
                for name in ("Secure Preferences", "Preferences"):
                    pref = folder / name
                    if pref.is_file():
                        yield pref
        except OSError:
            continue


def newest_store_skill(ext_id_dir: pathlib.Path) -> pathlib.Path | None:
    try:
        versions = [p for p in ext_id_dir.iterdir() if p.is_dir()]
    except OSError:
        return None
    versions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in versions:
        skill = as_skill(folder)
        if skill is not None:
            return skill
    return None


def skill_from_install_path(raw: str, profile: pathlib.Path) -> pathlib.Path | None:
    raw = (raw or "").strip()
    candidates: list[pathlib.Path] = []
    if raw:
        path = pathlib.Path(raw)
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(profile / "Extensions" / raw)
            candidates.append(profile / "Extensions" / CHROME_EXTENSION_ID / raw)
            candidates.append(profile / raw)
    for cand in candidates:
        skill = as_skill(cand)
        if skill is not None:
            return skill
    ext_root = profile / "Extensions" / CHROME_EXTENSION_ID
    if ext_root.is_dir():
        return newest_store_skill(ext_root)
    return None


def browser_skill_roots() -> list[pathlib.Path]:
    """Live Chrome/Edge install: Load unpacked (absolute path) or Store (relative)."""
    enabled: list[pathlib.Path] = []
    others: list[pathlib.Path] = []
    seen: set[str] = set()

    def add(skill: pathlib.Path | None, bucket: list[pathlib.Path]) -> None:
        if skill is None:
            return
        key = str(skill)
        if key in seen:
            return
        seen.add(key)
        bucket.append(skill)

    for pref in iter_pref_files():
        try:
            data = json.loads(pref.read_text(encoding="utf-8"))
        except Exception:
            continue
        rec = ((data.get("extensions") or {}).get("settings") or {}).get(
            CHROME_EXTENSION_ID
        )
        if not isinstance(rec, dict):
            continue
        skill = skill_from_install_path(str(rec.get("path") or ""), pref.parent)
        add(skill, enabled if rec_enabled(rec) else others)

    for root in browser_user_data_roots():
        try:
            if not root.is_dir():
                continue
            for folder in root.iterdir():
                if not folder.is_dir():
                    continue
                ext = folder / "Extensions" / CHROME_EXTENSION_ID
                if ext.is_dir():
                    add(newest_store_skill(ext), others)
        except OSError:
            continue

    return enabled or others


def skill_root() -> pathlib.Path | None:
    here = pathlib.Path(__file__).resolve()
    for key in ("DAY_PLANNER_SKILL", "ENGINEER_DAY_PLANNER_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        skill = as_skill(pathlib.Path(raw))
        if skill is not None:
            return skill
    here_skill = None
    candidates: list[pathlib.Path] = [here.parent.parent]
    for folder in [here.parent, *here.parents]:
        candidates.append(folder)
        candidates.append(folder / "skill")
    seen: set[str] = set()
    for candidate in candidates:
        skill = as_skill(candidate)
        if skill is None:
            continue
        key = str(skill)
        if key in seen:
            continue
        seen.add(key)
        here_skill = skill
        break
    chrome = browser_skill_roots()
    if here_skill and chrome:
        if packed_version_tuple(chrome[0]) > packed_version_tuple(here_skill):
            return chrome[0]
        return here_skill
    return here_skill or (chrome[0] if chrome else None)


def main() -> None:
    msg = read_msg() or {}
    cmd = str(msg.get("cmd") or "ensure").strip().lower()
    if cmd == "stop":
        flag = "--stop"
    elif cmd == "restart":
        flag = "--restart"
    else:
        flag = "--ensure"
    root = skill_root()
    if not root:
        write_msg(
            {
                "ok": False,
                "error": "planner skill folder not found (need Load unpacked or Chrome Web Store skill/, not Claude/Cursor skills)",
            }
        )
        return
    bridge = root / "scripts" / "calendar-bridge.py"
    env = os.environ.copy()
    env["DAY_PLANNER_SKILL"] = str(root)
    env["ENGINEER_DAY_PLANNER_ROOT"] = str(root)
    env["DAY_PLANNER_PAGE"] = str(root / "out" / "current.html")
    env["DAY_PLANNER_PORT"] = env.get("DAY_PLANNER_PORT") or "8765"
    env["DAY_PLANNER_PORT_FILE"] = str(root / "out" / ".bridge-port")
    env["DAY_PLANNER_EXTENSION_VERSION"] = str(msg.get("version") or "").strip()
    try:
        out = subprocess.check_output(
            [sys.executable, str(bridge), flag],
            env=env,
            cwd=str(root),
            timeout=40,
            stderr=subprocess.DEVNULL,
        )
        if cmd == "stop":
            write_msg({"ok": True, "stopped": True})
            return
        url = (out.decode("utf-8", "replace") or "").strip().splitlines()[-1].strip()
        if not url.startswith("http://127.0.0.1:"):
            write_msg({"ok": False, "error": "bridge did not start"})
            return
        write_msg({"ok": True, "url": url})
    except Exception as exc:
        write_msg({"ok": False, "error": str(exc)[:240]})


if __name__ == "__main__":
    main()
