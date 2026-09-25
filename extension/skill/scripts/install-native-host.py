#!/usr/bin/env python3
"""One-time native-host registration for Day Planner.

Chrome cannot write NativeMessagingHosts from the extension. Run this once,
by file path, from the folder you loaded:

    python3 extension/skill/scripts/install-native-host.py

After that, the toolbar starts the bridge. Quitting Chrome does not require
this command again.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

CHROME_EXTENSION_ID = "ojpfakkcgmefanbomdfpglbioapoabfh"


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
    if not (parent / "panel.html").is_file():
        return False
    root = parent if (parent / "manifest.json").is_file() else parent.parent
    return (root / "manifest.json").is_file()


def as_skill(path: pathlib.Path) -> pathlib.Path | None:
    try:
        p = path.expanduser()
    except OSError:
        return None
    if is_packed_extension_skill(p):
        return p.resolve()
    for nested in (p / "skill", p / "extension" / "skill"):
        if is_packed_extension_skill(nested):
            return nested.resolve()
    return None


def rec_enabled(rec: dict) -> bool:
    reasons = rec.get("disable_reasons")
    if reasons in (None, 0, 0.0, "", [], {}):
        return True
    return False


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


def explicit_skill() -> pathlib.Path | None:
    for arg in sys.argv[1:]:
        if arg.startswith("-"):
            continue
        skill = as_skill(pathlib.Path(arg))
        if skill is not None:
            return skill
    return None


def in_protected_home(path: pathlib.Path) -> bool:
    home = pathlib.Path.home()
    try:
        rel = path.resolve().relative_to(home.resolve())
    except (OSError, ValueError):
        return False
    return bool(rel.parts) and rel.parts[0] in {"Desktop", "Documents", "Downloads"}


def host_support_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "Application Support" / "engineer-day-planner"


def remember_update_source(skill: pathlib.Path) -> None:
    parent = skill.parent
    root = parent if (parent / ".git").exists() else parent.parent
    if not (root / ".git").exists():
        return
    try:
        remote = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return
    if branch.startswith("origin/"):
        branch = branch[len("origin/") :]
    if not remote or not branch or branch == "HEAD":
        return
    dest = host_support_dir()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "update-source.json").write_text(
            json.dumps({"remote": remote, "branch": branch}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def write_note(name: str, skill: pathlib.Path) -> None:
    dest = host_support_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / name).write_text(str(skill.resolve()) + "\n", encoding="utf-8")


def snapshot_extension(skill: pathlib.Path) -> pathlib.Path:
    """Copy this extension aside without removing the copy a previous version is using."""
    version = "0"
    manifest = skill.parent / "manifest.json"
    if not manifest.is_file():
        manifest = skill.parent.parent / "manifest.json"
    if manifest.is_file():
        try:
            version = str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "0")
        except (OSError, ValueError):
            version = "0"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in version) or "0"
    digest = hashlib.sha256(str(skill.resolve()).encode()).hexdigest()[:8]
    bundle = host_support_dir() / "runs" / f"{safe}-{digest}"
    staging = host_support_dir() / "runs" / f".{safe}-{digest}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    skill_dir = staging / "skill"
    skill_dir.mkdir(parents=True)
    for name in ("scripts", "page"):
        src = skill / name
        if src.is_dir():
            shutil.copytree(
                src,
                skill_dir / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    skill_md = skill / "SKILL.md"
    if skill_md.is_file():
        shutil.copy2(skill_md, skill_dir / "SKILL.md")
    (skill_dir / "out").mkdir(parents=True, exist_ok=True)
    if manifest.is_file():
        (staging / "manifest.json").write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    panel = skill.parent / "panel.html"
    if panel.is_file():
        shutil.copy2(panel, staging / "panel.html")
    if bundle.exists():
        shutil.rmtree(bundle)
    staging.rename(bundle)
    return bundle / "skill"


def prune_old_runs(keep: pathlib.Path) -> None:
    runs = host_support_dir() / "runs"
    if not runs.is_dir():
        return
    keep_top = keep.resolve().parent
    live: list[str] = []
    for port in range(8765, 8800):
        try:
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.4) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            continue
        root = str((data or {}).get("root") or "")
        if root:
            live.append(os.path.normpath(root))
    for child in runs.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            if child.resolve() == keep_top:
                continue
        except OSError:
            continue
        child_s = str(child.resolve())
        if any(root == child_s or root.startswith(child_s + os.sep) for root in live):
            continue
        shutil.rmtree(child, ignore_errors=True)


def find_skill() -> pathlib.Path | None:
    """The file path that was executed wins. A leftover env path must not keep the old version."""
    chosen = explicit_skill()
    if chosen is not None:
        return chosen
    here = pathlib.Path(__file__).resolve()
    for cand in (here.parent.parent, here.parent, here.parent / "skill"):
        skill = as_skill(cand)
        if skill is not None:
            return skill
    for key in ("DAY_PLANNER_SKILL", "ENGINEER_DAY_PLANNER_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        skill = as_skill(pathlib.Path(raw))
        if skill is not None:
            return skill
    chrome = browser_skill_roots()
    if chrome:
        return chrome[0]
    return None


def main() -> int:
    skill = find_skill()
    if skill is None:
        sys.stderr.write(
            "Day Planner skill/ not found.\n"
            "Install the extension (Load unpacked or Chrome Web Store), then run this again.\n"
        )
        return 1
    if "--print-skill" in sys.argv:
        print(skill)
        return 0
    bridge = skill / "scripts" / "calendar-bridge.py"
    if not bridge.is_file():
        sys.stderr.write(f"calendar-bridge.py missing under {skill}\n")
        return 1
    run_skill = skill
    if in_protected_home(skill):
        run_skill = snapshot_extension(skill)
        print(
            "That folder is on the Desktop, in Documents, or in Downloads. "
            "This command copied it so the toolbar can start it later.",
            file=sys.stderr,
        )
    bridge = run_skill / "scripts" / "calendar-bridge.py"
    env = os.environ.copy()
    env.pop("DAY_PLANNER_SKILL", None)
    env.pop("ENGINEER_DAY_PLANNER_ROOT", None)
    env["DAY_PLANNER_SKILL"] = str(run_skill)
    env["ENGINEER_DAY_PLANNER_ROOT"] = str(run_skill)
    env["DAY_PLANNER_PAGE"] = str(run_skill / "out" / "current.html")
    env["DAY_PLANNER_PORT"] = env.get("DAY_PLANNER_PORT") or "8765"
    env["DAY_PLANNER_PORT_FILE"] = str(run_skill / "out" / ".bridge-port")
    print(f"Using packed skill: {skill}", file=sys.stderr)
    try:
        out = subprocess.check_output(
            [sys.executable, str(bridge), "--ensure"],
            env=env,
            cwd=str(run_skill),
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            "The new folder did not start. The previous registration is unchanged.\n"
            f"calendar-bridge --ensure failed ({exc.returncode})\n"
        )
        return exc.returncode or 1
    remember_update_source(skill)
    write_note("extension-skill.txt", skill)
    run_note = host_support_dir() / "run-skill.txt"
    if run_skill.resolve() != skill.resolve():
        write_note("run-skill.txt", run_skill)
        prune_old_runs(run_skill)
    else:
        try:
            run_note.unlink()
        except OSError:
            pass
        prune_old_runs(skill)
    url = (out.decode("utf-8", "replace") or "").strip().splitlines()
    print(url[-1] if url else "ok")
    print("Native host registered. Click the Day Planner toolbar icon.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
