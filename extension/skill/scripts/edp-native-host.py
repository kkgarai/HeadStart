#!/usr/bin/env python3
"""Chrome native messaging host: start the local planner bridge if it is down.

Chrome launches this file from the native-host JSON (Load unpacked or Chrome
Web Store). Finds packed skill/ from env, the live Chrome/Edge install
(absolute unpacked path or Store Extensions/{id}/{version}), then __file__.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys

CHROME_EXTENSION_ID = "ojpfakkcgmefanbomdfpglbioapoabfh"
HOST_LOGIC_VERSION = 5


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


def packed_version_tuple(skill: pathlib.Path) -> tuple[int, ...]:
    try:
        root = skill.parent if (skill.parent / "manifest.json").is_file() else skill.parent.parent
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
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


def can_read_file(path: pathlib.Path) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def skill_is_readable(skill: pathlib.Path | None) -> bool:
    if skill is None:
        return False
    return can_read_file(skill / "scripts" / "calendar-bridge.py")


def support_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "Application Support" / "engineer-day-planner"


def note_skill(name: str) -> pathlib.Path | None:
    try:
        raw = (support_dir() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return as_skill(pathlib.Path(raw))


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text or "0").split(".")[:6]:
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts) or (0,)


def in_protected_home(path: pathlib.Path) -> bool:
    """Desktop, Documents, Downloads, and any folder inside them."""
    home = pathlib.Path.home()
    try:
        rel = path.resolve().relative_to(home.resolve())
    except (OSError, ValueError):
        return False
    return bool(rel.parts) and rel.parts[0] in {"Desktop", "Documents", "Downloads"}


def remember_skill(name: str, skill: pathlib.Path) -> None:
    dest = support_dir()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text(str(skill.resolve()) + "\n", encoding="utf-8")
    except OSError:
        pass


def copy_prior_snapshot_ledgers(dest_out: pathlib.Path) -> None:
    try:
        raw = (support_dir() / "run-skill.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not raw:
        return
    prior = pathlib.Path(raw)
    try:
        prior.resolve().relative_to((support_dir() / "runs").resolve())
    except (OSError, ValueError):
        return
    copy_ledgers(prior / "out", dest_out)


def copy_ledgers(src_out: pathlib.Path, dest_out: pathlib.Path) -> None:
    """Keep Done marks and holds when a new version gets a fresh snapshot."""
    if not src_out.is_dir():
        return
    dest_out.mkdir(parents=True, exist_ok=True)
    for name in (".done-keys.json", ".case-holds.json"):
        src = src_out / name
        dest = dest_out / name
        if src.is_file() and not dest.is_file():
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass


def snapshot_skill(skill: pathlib.Path) -> pathlib.Path:
    """Copy a protected folder aside so Chrome can run it from any location."""
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
    bundle = support_dir() / "runs" / f"{safe}-{digest}"
    existing = bundle / "skill"
    if skill_is_readable(existing):
        return existing
    staging = support_dir() / "runs" / f".{safe}-{digest}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    skill_dir = staging / "skill"
    skill_dir.mkdir(parents=True)
    for name in ("scripts", "page"):
        src = skill / name
        if src.is_dir():
            shutil.copytree(src, skill_dir / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    skill_md = skill / "SKILL.md"
    if skill_md.is_file():
        shutil.copy2(skill_md, skill_dir / "SKILL.md")
    (skill_dir / "out").mkdir(parents=True, exist_ok=True)
    copy_ledgers(skill / "out", skill_dir / "out")
    copy_prior_snapshot_ledgers(skill_dir / "out")
    if manifest.is_file():
        (staging / "manifest.json").write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    panel = skill.parent / "panel.html"
    if panel.is_file():
        shutil.copy2(panel, staging / "panel.html")
    if bundle.exists():
        shutil.rmtree(bundle)
    staging.rename(bundle)
    return bundle / "skill"


def candidate_skills() -> list[pathlib.Path]:
    """Every readable copy. Chrome's load path wins, wherever that folder is."""
    found: list[pathlib.Path] = []
    seen: set[str] = set()

    def push(skill: pathlib.Path | None) -> None:
        if not skill_is_readable(skill):
            return
        try:
            key = str(skill.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        found.append(skill.resolve())

    for skill in browser_skill_roots():
        push(skill)
    push(note_skill("extension-skill.txt"))
    push(note_skill("run-skill.txt"))
    runs = support_dir() / "runs"
    if runs.is_dir():
        try:
            children = list(runs.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            push(as_skill(child))
            push(as_skill(child / "skill"))
    for key in ("DAY_PLANNER_SKILL", "ENGINEER_DAY_PLANNER_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            push(as_skill(pathlib.Path(raw)))
    beside = pathlib.Path(__file__).resolve().parent / "skill"
    push(as_skill(beside))
    return found


def runnable_skill(skill: pathlib.Path) -> pathlib.Path:
    if not in_protected_home(skill):
        return skill
    try:
        snapped = snapshot_skill(skill)
    except OSError:
        return skill
    if skill_is_readable(snapped):
        remember_skill("extension-skill.txt", skill)
        remember_skill("run-skill.txt", snapped)
        return snapped
    return skill


def root_for_request(requested: str) -> pathlib.Path | None:
    """Start the version Chrome loaded, from whatever folder it was loaded from."""
    pool = candidate_skills()
    if not pool:
        return None
    want = version_tuple(requested)
    chosen = None
    if want > (0,):
        exact = [skill for skill in pool if packed_version_tuple(skill) == want]
        if exact:
            chosen = exact[0]
    if chosen is None:
        chosen = max(pool, key=packed_version_tuple)
    return runnable_skill(chosen)


def skill_root() -> pathlib.Path | None:
    return root_for_request("")


def shutdown_other_versions(keep: tuple[int, ...]) -> None:
    """Stop bridges on a different version so the loaded one can take the port."""
    if keep <= (0,):
        return
    import urllib.request

    for port in range(8765, 8800):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%s/health" % port, timeout=0.4
            ) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except Exception:
            continue
        if not isinstance(body, dict) or body.get("service") != "engineer-day-planner":
            continue
        if version_tuple(str(body.get("version") or "")) == keep:
            continue
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%s/__shutdown" % port,
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=1).read()
        except Exception:
            pass


def stage_root(version: str) -> pathlib.Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version or "0")) or "0"
    return support_dir() / "runs" / f".{safe}-loaded.staging"


def _safe_parts(rel: str) -> tuple[str, ...] | None:
    text = str(rel or "").replace("\\", "/").lstrip("/")
    if text.startswith("extension/"):
        text = text[len("extension/") :]
    parts = pathlib.PurePosixPath(text).parts
    if not text or ".." in parts:
        return None
    allowed = {
        "manifest.json",
        "panel.html",
        "panel.js",
        "panel.css",
        "background.js",
        "content.js",
        "done-ledger.js",
        "welcome.html",
        "skill/SKILL.md",
    }
    if text not in allowed and not text.startswith("skill/scripts/") and not text.startswith("skill/page/"):
        return None
    return parts


def stage_files(version: str, files: list) -> None:
    root = stage_root(version)
    root.mkdir(parents=True, exist_ok=True)
    for item in files or []:
        if not isinstance(item, dict):
            continue
        parts = _safe_parts(str(item.get("path") or ""))
        if not parts:
            continue
        dest = root.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(item.get("text") or ""), encoding="utf-8")
        if dest.suffix == ".sh":
            dest.chmod(0o755)


def commit_staged(version: str) -> pathlib.Path:
    staging = stage_root(version)
    bridge = staging / "skill" / "scripts" / "calendar-bridge.py"
    manifest = staging / "manifest.json"
    if not bridge.is_file() or not manifest.is_file():
        raise RuntimeError("this version did not include the bridge")
    (staging / "skill" / "out").mkdir(parents=True, exist_ok=True)
    for script in (staging / "skill" / "scripts").glob("*.sh"):
        try:
            script.chmod(0o755)
        except OSError:
            pass
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version or "0")) or "0"
    bundle = support_dir() / "runs" / f"{safe}-loaded"
    if bundle.exists():
        shutil.rmtree(bundle)
    staging.rename(bundle)
    skill = bundle / "skill"
    remember_skill("run-skill.txt", skill)
    return skill


def loaded_git_root(requested: str) -> pathlib.Path | None:
    """Folder Chrome loaded, when that folder is a git clone."""
    want = version_tuple(requested)
    matches: list[pathlib.Path] = []
    for skill in browser_skill_roots():
        root = skill.parent if (skill.parent / ".git").exists() else skill.parent.parent
        if not (root / ".git").exists() or not (root / "manifest.json").is_file():
            continue
        matches.append(root)
    if not matches:
        return None
    if want > (0,):
        exact = [root for root in matches if packed_version_tuple(root / "skill") == want]
        if exact:
            return exact[0]
    return matches[0]


def manifest_version_text(raw: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("version") or "").strip()


def git_env() -> dict:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def origin_ref(root: pathlib.Path) -> str:
    """The branch this clone already follows. A line that never merges to main still updates."""
    try:
        ref = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=str(root),
            env=git_env(),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        ).strip()
        if ref and not ref.endswith("HEAD"):
            return ref
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            env=git_env(),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    if branch and branch != "HEAD":
        try:
            subprocess.check_call(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                cwd=str(root),
                env=git_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            return f"origin/{branch}"
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("main", "master"):
        try:
            subprocess.check_call(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}"],
                cwd=str(root),
                env=git_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            return f"origin/{name}"
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("origin has no branch")


def fetch_origin(root: pathlib.Path) -> str:
    subprocess.check_call(
        ["git", "fetch", "origin", "--quiet"],
        cwd=str(root),
        env=git_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
    )
    return origin_ref(root)


def remote_manifest_version(root: pathlib.Path, ref: str) -> str:
    raw = subprocess.check_output(
        ["git", "show", f"{ref}:manifest.json"],
        cwd=str(root),
        env=git_env(),
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=20,
    )
    return manifest_version_text(raw)


def update_check(requested: str) -> dict:
    root = loaded_git_root(requested)
    local = str(requested or "").strip()
    if root is None:
        return {"ok": True, "update": False}
    if not local:
        local = manifest_version_text((root / "manifest.json").read_text(encoding="utf-8"))
    try:
        ref = fetch_origin(root)
        remote = remote_manifest_version(root, ref)
    except Exception:
        return {"ok": True, "update": False}
    if not remote or not local:
        return {"ok": True, "update": False}
    return {
        "ok": True,
        "update": version_tuple(remote) > version_tuple(local),
        "remote": remote,
        "local": local,
    }


def update_apply(requested: str) -> dict:
    root = loaded_git_root(requested)
    if root is None:
        return {"ok": False, "error": "The loaded folder is not a git clone."}
    try:
        ref = fetch_origin(root)
        subprocess.check_call(
            ["git", "reset", "--hard", ref],
            cwd=str(root),
            env=git_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except subprocess.CalledProcessError:
        return {"ok": False, "error": "Could not update from origin."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
    version = manifest_version_text((root / "manifest.json").read_text(encoding="utf-8"))
    return {"ok": True, "version": version}


def main() -> None:
    msg = read_msg() or {}
    cmd = str(msg.get("cmd") or "ensure").strip().lower()
    requested = str(msg.get("version") or "").strip()
    if cmd == "update-check":
        write_msg(update_check(requested))
        return
    if cmd == "update-apply":
        write_msg(update_apply(requested))
        return
    if cmd == "stage":
        try:
            stage_files(requested, msg.get("files") if isinstance(msg.get("files"), list) else [])
            write_msg({"ok": True})
        except Exception as exc:
            write_msg({"ok": False, "error": str(exc)[:240]})
        return
    if cmd == "commit":
        try:
            root = commit_staged(requested)
        except Exception as exc:
            write_msg({"ok": False, "error": str(exc)[:240]})
            return
        shutdown_other_versions(version_tuple(requested))
        flag = "--ensure"
    elif cmd == "stop":
        flag = "--stop"
        root = root_for_request(requested) or skill_root()
    elif cmd == "restart":
        flag = "--restart"
        root = root_for_request(requested)
    else:
        flag = "--ensure"
        root = root_for_request(requested)
    if root is not None and cmd not in ("stop", "commit"):
        shutdown_other_versions(packed_version_tuple(root))
    if not root:
        write_msg(
            {
                "ok": False,
                "error": "Run the setup command once from the folder you loaded.",
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
            stderr=subprocess.PIPE,
        )
        if cmd == "stop":
            write_msg({"ok": True, "stopped": True})
            return
        url = (out.decode("utf-8", "replace") or "").strip().splitlines()[-1].strip()
        if not url.startswith("http://127.0.0.1:"):
            write_msg({"ok": False, "error": "bridge did not start"})
            return
        write_msg({"ok": True, "url": url})
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace").strip()
        line = err.splitlines()[-1] if err else f"exit {exc.returncode}"
        write_msg({"ok": False, "error": line[:240]})
    except Exception as exc:
        write_msg({"ok": False, "error": str(exc)[:240]})


if __name__ == "__main__":
    main()
