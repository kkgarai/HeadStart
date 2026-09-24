#!/usr/bin/env python3
"""Local Google Calendar bridge for engineer-day-planner (duration + RSVP).

Serves the planner page on 127.0.0.1 (prefers 8765; next free port if that
is taken) so Update calendar / RSVP / Refresh calendar are same-origin.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

def local_zone_name() -> str:
    """This computer's IANA zone. Never store this as the shift zone."""
    try:
        key = str(getattr(datetime.now().astimezone().tzinfo, "key", "") or "").strip()
        if key:
            return key
    except Exception:
        pass
    try:
        link = pathlib.Path("/etc/localtime").resolve()
        parts = link.parts
        if "zoneinfo" in parts:
            name = "/".join(parts[parts.index("zoneinfo") + 1 :])
            if name:
                ZoneInfo(name)
                return name
    except Exception:
        pass
    return ""


def zoneinfo_or_local(name: str = ""):
    """Datetime math only. An empty name is not stored as the shift zone."""
    raw = str(name or "").strip()
    if raw:
        try:
            return ZoneInfo(raw)
        except Exception:
            pass
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc


def zone_for_math(tzname: str = ""):
    return zoneinfo_or_local(tzname)


HOST = "127.0.0.1"
SERVICE = "engineer-day-planner"
PORT_START = int(os.environ.get("DAY_PLANNER_PORT", "8765"))
PORT_SPAN = int(os.environ.get("DAY_PLANNER_PORT_SPAN", "35"))
NATIVE_HOST_NAME = "com.kgarai.engineerdayplanner.bridge"
LAUNCH_LABEL = "com.engineerdayplanner.bridge"
LEGACY_LAUNCH_LABELS = ("com.kgarai.engineer-day-planner.bridge",)
CHROME_EXTENSION_ID = "ojpfakkcgmefanbomdfpglbioapoabfh"


def is_host_skill_copy(path: pathlib.Path) -> bool:
    try:
        text = str(path.resolve()).replace("\\", "/").lower()
    except OSError:
        return False
    return "/.claude/skills/" in text or "/.cursor/skills/" in text or "/claude/skills/" in text


def is_packed_extension_skill(path: pathlib.Path) -> bool:
    try:
        p = path.resolve()
    except OSError:
        return False
    if is_host_skill_copy(p):
        return False
    if p.name != "skill":
        return False
    if not (p / "SKILL.md").is_file() or not (p / "scripts" / "calendar-bridge.py").is_file():
        return False
    parent = p.parent
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
    """Live install: Load unpacked (absolute) or Chrome Web Store (relative Extensions path)."""
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


def resolve_skill_root() -> pathlib.Path:
    """Packed Chrome extension skill/ only. Never ~/.claude/skills or ~/.cursor/skills."""
    here = pathlib.Path(__file__).resolve()
    for key in ("DAY_PLANNER_SKILL", "ENGINEER_DAY_PLANNER_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        skill = as_skill(pathlib.Path(raw))
        if skill is not None:
            return skill
    for cand in [here.parent.parent, *[folder / "skill" for folder in here.parents]]:
        skill = as_skill(cand)
        if skill is not None:
            return skill
    chrome = browser_skill_roots()
    if chrome:
        return chrome[0]
    raise RuntimeError(
        "Engineer day planner must run from the Chrome extension skill/ folder "
        "(Load unpacked or Chrome Web Store, next to manifest.json). "
        "Not ~/.claude/skills or ~/.cursor/skills."
    )


SKILL_ROOT = resolve_skill_root()
PAGE = pathlib.Path(os.environ["DAY_PLANNER_PAGE"]) if os.environ.get("DAY_PLANNER_PAGE") else (SKILL_ROOT / "out" / "current.html")
PROCESS_VERSION = ""


def packed_extension_version() -> str:
    path = SKILL_ROOT.parent / "manifest.json"
    if not path.is_file():
        path = SKILL_ROOT.parent.parent / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("version") or "").strip()
    except Exception:
        pass
    return (os.environ.get("DAY_PLANNER_EXTENSION_VERSION") or "").strip()


PLAN_SYSTEM_FILE = SKILL_ROOT / "scripts" / "PLAN_SYSTEM.md"
FETCH_SYSTEM_FILE = SKILL_ROOT / "scripts" / "FETCH_SYSTEM.md"
CASE_ACTIVITY_FILE = pathlib.Path("/tmp/case-activity.json")
CASE_DIGEST_FILE = pathlib.Path("/tmp/case-digest.json")
PLANNER_CARDS_FILE = pathlib.Path("/tmp/planner-cards.txt")
PLANNER_DIGEST_FILE = pathlib.Path("/tmp/planner-digest.txt")
PLANNER_GATHER_FILE = pathlib.Path("/tmp/planner-gather.json")
PLANNER_INBOX_FILE = pathlib.Path("/tmp/planner-inbox.json")
PLANNER_INBOX_TXT = pathlib.Path("/tmp/planner-inbox.txt")
OWNED_CASES_FILE = pathlib.Path("/tmp/owned-cases.json")
MAX_GATHER_CHARS = 12_000
INBOX_CLIP_N = 4000
MAX_INBOX_CHARS = 14_000
MAX_DIGEST_CHARS = 14_000
INLINE_SOQL_TMP = pathlib.Path("/tmp/planner-inline-soql.json")
BODY_FIELD_RE = re.compile(r"CommentBody|TextBody|HtmlBody")
FETCH_TIMEOUT_SEC = 240
IDENTITY_FILE = SKILL_ROOT / "out" / ".identity.json"
SLIM_TOOL = SKILL_ROOT / "scripts" / "slim-tool-result.py"
OVERFLOW_SAVED_RE = re.compile(
    r"(?:Output has been saved to |result was saved to |saved to[:\s]+|wrote (?:output )?to[:\s]+)"
    r"(\S+)",
    re.I,
)
OVERFLOW_PATH_RE = re.compile(r"(/(?:Users|home)/[^\s\"'<>]+/tool-results?/[^\s\"'<>]+)")


def load_identity_cache() -> dict:
    try:
        obj = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def apply_identity_cache(data: dict) -> None:
    if not isinstance(data, dict):
        return
    blob = load_identity_cache()
    if not blob.get("name"):
        try:
            prev = load_page_briefing()
        except Exception:
            prev = {}
        if isinstance(prev, dict):
            blob = {
                "name": str(prev.get("name") or blob.get("name") or "").strip(),
                "title": str(prev.get("title") or blob.get("title") or "").strip(),
                "manager": str(prev.get("manager") or blob.get("manager") or "").strip(),
                "email": str(blob.get("email") or "").strip(),
            }
    for key in ("name", "title", "manager", "email"):
        if str(data.get(key) or "").strip():
            continue
        val = str(blob.get(key) or "").strip()
        if val:
            data[key] = val


def persist_identity(data: dict) -> None:
    if not isinstance(data, dict):
        return
    email = str(data.get("email") or "").strip()
    if "@" not in email:
        email = str(load_identity_cache().get("email") or "").strip()
    blob = {
        "name": str(data.get("name") or "").strip(),
        "title": str(data.get("title") or "").strip(),
        "manager": str(data.get("manager") or "").strip(),
        "email": email if "@" in email else "",
    }
    if not blob["name"] and not blob["email"]:
        return
    try:
        IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_FILE.write_text(json.dumps(blob) + "\n", encoding="utf-8")
    except OSError:
        pass


PART_CHARS = 60_000


def _split_evidence(src: pathlib.Path, stem: str, cwd: pathlib.Path) -> list[str]:
    """Write whole blocks into part files under the model Read limit. Nothing is dropped."""
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return []
    for old in cwd.glob(stem + "-part-*.txt"):
        try:
            old.unlink()
        except OSError:
            pass
    blocks = re.split(r"(?=^## )", text, flags=re.M)
    parts: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if not buf.strip():
            buf = ""
            return
        name = f"{stem}-part-{len(parts) + 1}.txt"
        (cwd / name).write_text(buf, encoding="utf-8")
        parts.append(name)
        buf = ""

    for block in blocks:
        if not block:
            continue
        if len(block) > PART_CHARS:
            if buf:
                flush()
            lines = block.splitlines(keepends=True)
            chunk = ""
            for line in lines:
                if chunk and len(chunk) + len(line) > PART_CHARS:
                    buf = chunk
                    flush()
                    chunk = ""
                chunk += line
            buf = chunk
            flush()
            continue
        if buf and len(buf) + len(block) > PART_CHARS:
            flush()
        buf += block
    flush()
    if len(parts) <= 1 and len(text) <= PART_CHARS:
        for name in parts:
            try:
                (cwd / name).unlink()
            except OSError:
                pass
        return []
    return parts


def link_planner_evidence() -> None:
    """Put digest and inbox where the model can Read them. Large files become ordered parts."""
    cwd = plan_cli_cwd()
    for src, name in (
        (PLANNER_DIGEST_FILE, "planner-digest.txt"),
        (PLANNER_INBOX_TXT, "planner-inbox.txt"),
    ):
        dest = cwd / name
        try:
            if dest.is_symlink() or dest.exists():
                dest.unlink()
        except OSError:
            continue
        stem = name.replace(".txt", "")
        parts = _split_evidence(src, stem, cwd) if src.is_file() else []
        try:
            if parts:
                lines = [
                    "This file is an index. Read every part once, in order. Do not skip a part. Do not read /tmp/" + name + " — that combined file is too large.",
                    "",
                ]
                lines.extend(f"- {part}" for part in parts)
                dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif src.is_file():
                dest.symlink_to(src)
        except OSError:
            try:
                if src.is_file() and not parts:
                    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass


def plan_cli_cwd() -> pathlib.Path:
    """Scratch dir for the tool CLI. Not the packed skill — Claude Code would treat that folder as the project."""
    if sys.platform == "darwin":
        root = HOME / "Library" / "Caches" / "engineer-day-planner"
    elif os.name == "nt":
        root = pathlib.Path(os.environ.get("LOCALAPPDATA") or HOME) / "engineer-day-planner"
    else:
        root = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache")) / "engineer-day-planner"
    path = root / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


OUT_KEEP = {
    "current.html",
    "briefing.json",
    ".case-holds.json",
    ".done-keys.json",
    ".runner",
    ".bridge-port",
    ".served-extension-version",
    ".page-generation",
    ".bridge.log",
    ".plan-run.json",
    ".plan-run.log",
    ".identity.json",
    ".notepad.json",
}


def _rm_tree(path: pathlib.Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError:
        pass


def sweep_run_scratch() -> None:
    """Each Run Planner overwrites a small fixed set. Drop dated snapshots and stray writes."""
    try:
        run = plan_cli_cwd()
        for path in list(run.iterdir()):
            _rm_tree(path)
        out = SKILL_ROOT / "out"
        if out.is_dir():
            for path in list(out.iterdir()):
                if path.name in OUT_KEEP or path.name.endswith(".tmp"):
                    continue
                _rm_tree(path)
        for path in SKILL_ROOT.glob("data-eod-*.json"):
            _rm_tree(path)
        for path in SKILL_ROOT.glob("*.snapshot.json"):
            _rm_tree(path)
        for name in ("plan.json", "day-planner-data.json", "briefing-data.json"):
            extra = SKILL_ROOT / name
            if extra.is_file():
                _rm_tree(extra)
    except Exception:
        return


PORT_FILE = pathlib.Path(os.environ.get("DAY_PLANNER_PORT_FILE", str(SKILL_ROOT / "out" / ".bridge-port")))
HOME = pathlib.Path.home()
PLANNER_MCPS = (
    ("orgcs", "OrgCS", ("orgcs", "user-orgcs", "org-cs", "org_cs")),
    ("gus", "GUS", ("gus_server", "gus-server", "gus")),
    ("slack", "Slack", ("slack",)),
    ("gmail", "Gmail", ("google-workspace", "google_workspace", "gmail")),
    ("calendar", "Calendar", ("google-workspace", "google_workspace", "google-calendar", "gcal")),
)
MCP_SKIP_KEYS = (
    "omni",
    "assembled",
    "black-tab",
    "black_tab",
    "codesearch",
    "mcp-adaptor",
    "columbo",
    "splunk",
    "argus",
)
PORT = PORT_START
RUNNER_FILE = SKILL_ROOT / "out" / ".runner"
PLAN_FILE = SKILL_ROOT / "out" / ".plan-run.json"
PLAN_TIMEOUT_SEC = int(os.environ.get("DAY_PLANNER_PLAN_TIMEOUT", str(20 * 60)))
PLAN_STEPS_MAX = 400
PLAN_LOG_FILE = SKILL_ROOT / "out" / ".plan-run.log"
PLAN_LOCK = threading.Lock()
PLAN_PROC_LOCK = threading.Lock()
OMNI_LOCK = threading.Lock()
PLAN_PROC: subprocess.Popen | None = None
PLAN_STOP = threading.Event()
PLAN_ACTIVE = threading.Event()
PLAN_FORCE_MODEL = ""
PLAN_SYSTEM_OVERRIDE: pathlib.Path | None = None
BRIDGE_IDLE_SEC = 2 * 60 * 60
BRIDGE_HEARTBEAT_PATHS = {
    "/health",
    "/snapshot",
    "/omni/check",
    "/plan/status",
}
_BRIDGE_LIFE = {"page": time.time(), "bye": 0.0}
_idle_stop = threading.Event()
SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_\-]{10,}|Bearer\s+\S+|x-api-key\s*[:=]\s*\S+|ANTHROPIC_API_KEY\s*[:=]\s*\S+)"
)
PLAN_PROMPT = (
    "PLAN_SYSTEM is loaded. The BRIEF is an index only. "
    "Full text is planner-digest.txt and planner-inbox.txt in this working directory "
    "(also /tmp/planner-digest.txt and /tmp/planner-inbox.txt). "
    "You decide: one Read of a file, or forward chunks if one Read would overload you. "
    "Each span once, in order. Do not re-read a span. Do not start over. "
    "You summarize. Python did not shorten those files. "
    "Do not Read /tmp/plan.json. "
    "Mandatory every run, no exception: OrgCS (threads + Initial Response + GUS related list), GUS (Support Contact, Follow, investigation SLA fields, LAP start/end), Slack, Calendar, Mail. Analyze all of them. "
    "Python already fetched: digest = this run's live OrgCS comments+emails+IR+related list+GUS on **open owned** cases only; "
    "inbox.txt = opened Slack leftovers + full unread mail bodies. "
    "You write every peeks.<caseNumber>.summary (4–8 sentences). Python does not. "
    "You still decide Peek, keep/drop, Not opened vs Needs a reply, GUS rows, and ranks from those clips. "
    "Do not write todayPlan. Leave todayPlan as []. Today's plan is built after this analysis finishes. "
    "Python does not classify Slack/Mail/GUS. Never Slack MCP. Never Gmail batch. Never body SOQL. Never related/GUS MCP. "
    "Who has the ball is last customer / last public in this digest, not an older sandbox-login beat. Need More Information / last ask is the customer → bucket watch, not Investigate on todayPlan. "
    "Pending Initial Response is case SLA. A 'will breach SLA in 30 minutes' mail is Needs us now until the public comment is on the case. Investigation SLA is the stored fields plus slamonitor mail. LAP uses requested start and end. Python does not mark overdue or approaching. "
    "ONE Write of /tmp/plan-ai.json: peeks + ranks + slack + mail + gus, todayPlan [], aiAnalyzed true, sourcesAnalyzed true. "
    "inboxReviewed true only after you classify every ## slack / ## mail clip. "
    "gusReviewed true only after you classify ## gus / gusCandidates / IR / related list. "
    "Empty inbox.txt is not Slack/Mail — clear unless gather slackFetchOk / mailFetchOk is true. "
    "gusFetchOk false is not GUS — clear. You classify Slack from clip + - reactions: — Python does not keep/drop. "
    "A failed tool is not a stop. Finish from the digest and inbox already on disk and still Write /tmp/plan-ai.json. "
    "If you are shown a failure list, you fix it in this run. Do not leave the error for someone else. "
    "Then stop. Reply: published. Do not a second write. "
    "Do not skip a PLAN_SYSTEM rule. Peek every gather case. Classify every Slack, Mail, and GUS clip. "
    "Use an opened Sev-1 channel name from the BRIEF. A case is in beforeYouLogOff or tomorrowFirst, never both."
)


def plan_system_text() -> str:
    try:
        return PLAN_SYSTEM_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def planner_mcp_prompt(runner: str = "") -> str:
    rows = planner_mcp_status(runner)
    if not rows:
        return ""
    bits = [f"{row['label']} {row['status']}" for row in rows]
    down = {str(row.get("id") or "") for row in rows if row.get("status") != "connected"}
    lines = [
        "This runner's planner MCPs: " + ", ".join(bits) + ".",
        "BRIEF is an index. Read planner-digest.txt and planner-inbox.txt yourself, one Read or forward chunks. "
        "Never Slack MCP. Never CaseComment/EmailMessage/CaseFeed SOQL. "
        "ONE Write /tmp/plan-ai.json with peeks, ranks, and gus. Leave todayPlan as []. Do not write slack or mail. "
        "Do not skip a PLAN_SYSTEM section. Peek every gather case. Classify GUS. Then stop. "
        "Do not Read /tmp/case-digest.json, overflow, or ~/.claude/projects. Never jq gather. "
        "Do not Read /tmp/plan.json."
    ]
    if "gus" in down:
        lines.append("GUS was disconnected at gather — do not invent GUS rows.")
    if "orgcs" in down:
        lines.append("OrgCS was disconnected at gather — do not invent the case queue. Stamp offline orgcs if the gather has no cases.")
    return " ".join(lines)


def plan_prompt_for(runner: str = "") -> str:
    extra = planner_mcp_prompt(runner)
    if extra:
        return PLAN_PROMPT + " " + extra
    return PLAN_PROMPT


def model_brief() -> str:
    """Short evidence for the planner model. A Read of the full files overflows."""
    lines = ["BRIEF"]
    gather: dict = {}
    try:
        loaded = json.loads(PLANNER_GATHER_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            gather = loaded
    except (OSError, json.JSONDecodeError):
        gather = {}
    lines.append(
        "header daypart={dp} shift={a}-{b} timezone={tz} timezoneShort={tzs} engineerShift={es} calendar={cal} slackFetchOk={s} mailFetchOk={m} gusFetchOk={g} assembled={asm}".format(
            dp=gather.get("daypart") or "",
            a=gather.get("shiftStart") or "",
            b=gather.get("shiftEnd") or "",
            tz=gather.get("timezone") or "unresolved",
            tzs=gather.get("timezoneShort") or "",
            es=gather.get("engineerShift") or "",
            cal=gather.get("calendarFetchOk") is True,
            s=gather.get("slackFetchOk") is True,
            m=gather.get("mailFetchOk") is True,
            g=gather.get("gusFetchOk") is True,
            asm=str(gather.get("assembledSchedule") or "")[:180],
        )
    )
    if not str(gather.get("timezone") or "").strip():
        lines.append(
            "timezone is unresolved. You write timezone (IANA) and timezoneShort on /tmp/plan-ai.json. "
            "Use Engineer_Shift__c when engineerShift is set. Assembled is only when that field is empty. "
            "Asia/Kolkata, Asia/Calcutta, and Asia/Colombo are IST. That is the label only. "
            "Do not change a timezone or shift hours that are already set. Do not invent Pacific. "
            "Do not invent a night window. Hours differ per engineer."
        )
    lines.append("Meetings:")
    for ev in (gather.get("meetings") or [])[:16]:
        if isinstance(ev, dict):
            lines.append(f"- {ev.get('startStamp') or ''} {str(ev.get('label') or '')[:60]}")
    lines.append("Cases:")
    for row in gather.get("cases") or []:
        if isinstance(row, dict):
            lines.append(
                f"- {row.get('caseNumber') or ''} {str(row.get('status') or '')[:40]} {str(row.get('label') or '')[:70]}"
            )
    digest = ""
    try:
        digest = PLANNER_DIGEST_FILE.read_text(encoding="utf-8")
    except OSError:
        digest = ""
    lines.append("Digest:")
    for num, fields in _digest_case_fields(digest).items():
        lines.append(
            f"- {num} live={str(fields.get('live') or '')[:60]} ir={str(fields.get('ir') or '')[:40]} "
            f"customer={str(fields.get('last customer') or '')[:100]}"
        )
    lines.append("Slack:")
    for row in (gather.get("slackCandidates") or [])[:24]:
        if isinstance(row, dict):
            lines.append(
                f"- {row.get('id') or ''} unread={bool(row.get('unread'))} me={row.get('lastHumanIsMe')} {str(row.get('label') or '')[:60]}"
            )
    lines.append("Mail:")
    for row in (gather.get("mailCandidates") or [])[:16]:
        if isinstance(row, dict):
            lines.append(f"- {row.get('id') or ''} {str(row.get('from') or '')[:30]} {str(row.get('label') or '')[:60]}")
    blob = "\n".join(lines).strip()
    if len(blob) > 7000:
        blob = blob[:6999].rstrip() + "…"
    return blob


def compact_plan_prompt(runner: str = "") -> str:
    return plan_prompt_for(runner) + "\n\n" + model_brief()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")
SLACK_WRITE_DENY = ",".join(
    [
        "mcp__plugin_slack_slack__slack_send_message",
        "mcp__plugin_slack_slack__slack_send_message_draft",
        "mcp__plugin_slack_slack__slack_schedule_message",
        "mcp__plugin_slack_slack__slack_create_canvas",
        "mcp__plugin_slack_slack__slack_update_canvas",
        "mcp__plugin_slack_slack__slack_create_conversation",
        "mcp__plugin_slack_slack__slack_add_reaction",
    ]
)
SLACK_READ_DENY = ",".join(
    [
        "mcp__plugin_slack_slack__slack_read_channel",
        "mcp__plugin_slack_slack__slack_read_thread",
        "mcp__plugin_slack_slack__slack_search_public_and_private",
        "mcp__plugin_slack_slack__slack_search_public",
        "mcp__plugin_slack_slack__slack_search_channels",
        "mcp__plugin_slack_slack__slack_get_reactions",
        "slack_read_channel",
        "slack_read_thread",
        "slack_search_public_and_private",
        "slack_search_public",
        "slack_search_channels",
        "slack_get_reactions",
    ]
)
GMAIL_BODY_DENY = ",".join(
    [
        "mcp__plugin_google-workspace_google-workspace__get_gmail_messages_content_batch",
        "get_gmail_messages_content_batch",
    ]
)
PLANNER_MCP_DENY = ",".join(
    [
        "soqlQuery",
        "query_gus_records",
        "query_gus_chatter",
        "getRelatedRecords",
        "get_gmail_messages_content_batch",
    ]
)
PLANNER_TOOL_DENY = (
    "Skill,ToolSearch,"
    + SLACK_WRITE_DENY
    + ","
    + SLACK_READ_DENY
    + ","
    + GMAIL_BODY_DENY
    + ","
    + PLANNER_MCP_DENY
)
ASK_SYSTEM = (
    "You are Planner Buddy for this engineer's published day-plan page. "
    "Use only the JSON, the stamped current time, the Board object, the Clock object, "
    "and the recent thread. Speak naturally and concisely. Do not invent. "
    "If it is not on this page, say it is not on this page. "
    "Do not fetch live. Do not tell the user to look anything up. Do not mention OrgCS, Slack, "
    "or mail as systems you would query. Do not mention that you are a model unless asked. "
    "Prefer the next useful move over dumping every field. "
    "When asked the latest, an update, or status on a case, use that case's summary, chronology, "
    "and latest beats already on the page — that is the latest update. "
    "If they ask to draft a follow-up, write paste-ready text from the last public / last outbound "
    "on that case row. Recap only what that last outbound said. "
    "Google Calendar meetings can be marked Done only when they sit on Today's plan. "
    "That strikes the row and silences the reminder. It does not delete Google Calendar. Undo restores it. "
    "Work blocks, cases, Slack, and mail already have Done. Breaks do not. "
    "If the bridge already toggled Done, confirm it. If you cannot match the row, say so. "
    "Board.fire is Needs us now. Burning, on fire, FIRE, urgent, and Needs us now are the same list. "
    "If Board.fire has rows, those cases ARE burning — say they are burning and name them. "
    "Open cases is the whole queue, not FIRE. Follow-up due is not burning unless it is also in Board.fire. "
    "Clock is meetings only. Never use Clock, in-progress events, or 'no meetings' to decide burning. "
    "If asked why something is or is not burning, use Board.fire, not the calendar. "
    "Clock is already computed from start/end stamps versus now. "
    "Clock.now, Clock.when, and the stamped current time are already in the selected display timezone. "
    "Repeat those strings as written. Do not convert them to another zone or change the abbreviation. "
    "For now, next, upcoming, or calendar questions, trust Clock — do not re-rank by reading labels. "
    "Never call a past item next. If it is not in Clock.inProgress and not in Clock.next "
    "or Clock.nextCalendar, it is not current. "
    "When the user says calendar, meeting, invite, or RSVP, use calendar:true rows only "
    "(Clock.inProgress where calendar is true, plus Clock.nextCalendar). "
    "Follow-up blocks, casework, meals, and breaks are not calendar meetings. "
    "Items with done:true are completed. They are not still owed. Do not list them as next, open, or Need you. "
    "If asked what is done or complete, use those rows. Clock already omits them. "
    "The thread remembers 'that case' / 'the burning one' / 'draft that'. "
    "Never claim you marked Done or undone. The bridge writes the ledger. "
    "If this prompt is answering a question, do not pretend a row was completed."
)
ASK_DONE_SYSTEM = (
    "You map a natural-language Done/undo request to planner rows. JSON only, no markdown. "
    '{"action":"done"|"undo"|"none","scope":"item"|"section"|"none","section":"","ids":[],"ask":""} '
    "action=none if they are asking a question, not toggling. "
    "scope=section when they mean a whole board list and did not name a case number or unique case. "
    "section must be one of: follow-up due, needs us now, slack, mail, today's plan, before you log off. "
    "ids must be catalog ids only (or 8+ digit case numbers that exist). Empty when scope=section. "
    "ask is a short clarifying question when two catalog rows fit and they did not mean the whole section. "
    "Follow-up due / follow-ups / follow-up items / the DNS holds / those CTI follow-ups = section "
    "follow-up due (the CASE LIST). Never pick plan-follow or a Today's plan leftover for that. "
    "plan-follow and labels like 'Follow-up due — N cases' are the CLOCK leftover. Use them only if they "
    "say today's plan, on the clock, leftover block, or calendar. "
    "A named case number or unique case label = that one row. "
    "Google Calendar meetings: only kind=calendar on Today's plan. Do not invent ids."
)
_SSL_CTX = None
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/[\]-]{1,120}$")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((HOST, port)) == 0


def health_payload(port: int):
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=1.5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def is_planner_service(port: int) -> bool:
    data = health_payload(port)
    return (
        isinstance(data, dict)
        and bool(data.get("ok"))
        and data.get("service") == SERVICE
    )


def listener_root(port: int) -> str:
    data = health_payload(port)
    if not isinstance(data, dict):
        return ""
    return os.path.normpath(str(data.get("root") or ""))


def is_our_listener(port: int) -> bool:
    if not is_planner_service(port):
        return False
    return listener_root(port) == os.path.normpath(str(SKILL_ROOT))


def shutdown_our_listeners() -> None:
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        if not is_planner_service(port):
            continue
        try:
            req = urllib.request.Request(
                f"http://{HOST}:{port}/__shutdown",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=1).read()
        except Exception:
            pass
    for _ in range(20):
        if not any(is_planner_service(p) for p in range(PORT_START, PORT_START + PORT_SPAN)):
            break
        time.sleep(0.05)


def pids_listening(port: int) -> list[int]:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def process_args(pid: int) -> str:
    try:
        return subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def stop_pidfile() -> None:
    for path in pid_files():
        if not path.is_file():
            continue
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            continue
        args = process_args(pid)
        if args and "calendar-bridge.py" not in args and "publish-page.sh" not in args:
            continue
        stop_pid(pid)
        try:
            path.unlink()
        except OSError:
            pass


def stop_other_planner_listeners(keep_root: str) -> None:
    """Stop planner bridges from other folders. Leave keep_root and this process."""
    launchctl_bootout()
    keep = os.path.normpath(keep_root)
    keep_script = str(pathlib.Path(keep) / "scripts" / "calendar-bridge.py")
    me = os.getpid()
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        if not is_planner_service(port):
            continue
        if listener_root(port) == keep:
            continue
        try:
            req = urllib.request.Request(
                f"http://{HOST}:{port}/__shutdown",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=1).read()
        except Exception:
            pass
    try:
        out = subprocess.check_output(["pgrep", "-f", "calendar-bridge.py"], text=True)
        extras = [int(line) for line in out.split() if line.strip().isdigit()]
    except (OSError, subprocess.CalledProcessError, ValueError):
        extras = []
    for pid in extras:
        if pid == me:
            continue
        args = process_args(pid)
        if keep_script in args:
            continue
        if "calendar-bridge.py" in args:
            stop_pid(pid)
    for _ in range(20):
        busy = False
        for port in range(PORT_START, PORT_START + PORT_SPAN):
            if not is_planner_service(port):
                continue
            if listener_root(port) == keep:
                continue
            busy = True
            break
        if not busy:
            break
        time.sleep(0.05)


def stop_our_processes() -> None:
    stop_pidfile()
    shutdown_our_listeners()
    me = os.getpid()
    try:
        out = subprocess.check_output(["pgrep", "-f", "calendar-bridge.py"], text=True)
        extras = [int(line) for line in out.split() if line.strip().isdigit() and int(line) != me]
    except (OSError, subprocess.CalledProcessError, ValueError):
        extras = []
    for pid in extras:
        if "calendar-bridge.py" in process_args(pid):
            stop_pid(pid)
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        for pid in pids_listening(port):
            if pid != me and "calendar-bridge.py" in process_args(pid):
                stop_pid(pid)
    for _ in range(20):
        busy = False
        for port in range(PORT_START, PORT_START + PORT_SPAN):
            if is_planner_service(port):
                busy = True
                break
            if any("calendar-bridge.py" in process_args(pid) for pid in pids_listening(port)):
                busy = True
                break
        if not busy:
            break
        time.sleep(0.05)


def choose_port() -> int:
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        if not port_in_use(port):
            return port
    raise SystemExit(
        f"engineer-day-planner: no free port in {PORT_START}–{PORT_START + PORT_SPAN - 1} "
        f"(set DAY_PLANNER_PORT to start the search elsewhere)"
    )


def tmp_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")


def xdg_config() -> pathlib.Path:
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))


def pid_files() -> tuple[pathlib.Path, pathlib.Path]:
    return (tmp_dir() / "day-planner-bridge.pid", SKILL_ROOT / "out" / ".bridge-pid")


def settings_files() -> list[pathlib.Path]:
    return [
        HOME / ".claude" / "settings.json",
        HOME / ".claude" / "settings.local.json",
        xdg_config() / "claude" / "settings.json",
        HOME / ".config" / "claude" / "settings.json",
    ]


def mcp_plugin_roots_for(runner: str = "") -> list[pathlib.Path]:
    claude = [
        HOME / ".claude" / "plugins" / "cache",
        HOME / ".claude" / "plugins" / "marketplaces",
        HOME / ".claude" / "plugins",
        xdg_config() / "claude" / "plugins",
    ]
    cursor = [
        HOME / ".cursor" / "plugins" / "cache",
        HOME / ".cursor" / "plugins" / "marketplaces",
        HOME / ".cursor" / "extensions",
    ]
    name = canonicalize_runner_id(runner)
    if name == "opencode":
        return []
    if name == "claude":
        return claude
    if name == "cursor":
        return cursor
    return claude + cursor


def mcp_config_files_for(runner: str = "") -> list[pathlib.Path]:
    name = canonicalize_runner_id(runner)
    claude = [
        HOME / ".claude.json",
        HOME / ".claude" / "mcp.json",
        HOME / ".claude" / "settings.json",
        xdg_config() / "claude" / "mcp.json",
        HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        HOME / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json",
    ]
    cursor = [
        HOME / ".cursor" / "mcp.json",
        HOME / "AppData" / "Roaming" / "Cursor" / "mcp.json",
    ]
    opencode = [
        xdg_config() / "opencode" / "opencode.json",
        xdg_config() / "opencode" / "config.json",
        HOME / ".opencode" / "opencode.json",
    ]
    walked = []
    for start in (SKILL_ROOT, pathlib.Path.cwd()):
        try:
            cur = start.resolve()
        except OSError:
            continue
        for _ in range(8):
            walked.append(cur / ".cursor" / "mcp.json")
            walked.append(cur / ".mcp.json")
            walked.append(cur / "opencode.json")
            if cur.parent == cur:
                break
            cur = cur.parent
    if name == "opencode":
        files = opencode + [p for p in walked if p.name == "opencode.json"]
    elif name == "claude":
        files = claude
    elif name == "cursor":
        files = cursor + [p for p in walked if p.name != "opencode.json"]
    else:
        files = claude + cursor + opencode + walked
    seen = set()
    out = []
    for path in files:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def mcp_plugin_roots() -> list[pathlib.Path]:
    return mcp_plugin_roots_for("")


def mcp_config_files() -> list[pathlib.Path]:
    return mcp_config_files_for("")


def mcp_servers_from_payload(data) -> dict:
    if not isinstance(data, dict):
        return {}
    for key in ("mcpServers", "mcp"):
        block = data.get(key)
        if isinstance(block, dict):
            return block
    return {}


def normalize_mcp_cfg(cfg: dict) -> dict:
    out = dict(cfg)
    cmd = out.get("command")
    if isinstance(cmd, list) and cmd:
        out["command"] = str(cmd[0])
        if len(cmd) > 1 and not out.get("args"):
            out["args"] = [str(part) for part in cmd[1:]]
    return out


def looks_oauth(cfg: dict) -> bool:
    auth = cfg.get("auth")
    if isinstance(auth, dict) and auth:
        return True
    if isinstance(cfg.get("oauth"), dict) and cfg.get("oauth"):
        return True
    return str(auth or "").lower() in {"oauth", "oauth2"}


def mcp_cfg_enabled(cfg: dict) -> bool:
    if not isinstance(cfg, dict):
        return False
    if cfg.get("enabled") is False:
        return False
    return True


def load_mcp():
    servers = discover_mcp_servers()
    cfg = pick_mcp_server(
        servers,
        ("google-workspace", "google_workspace", "gmail", "google-calendar", "gcal"),
    )
    if not cfg:
        raise RuntimeError("google-workspace MCP is not available locally")
    url = str(cfg.get("url") or "").strip()
    headers = _mcp_headers(cfg)
    auth = headers.get("Authorization") or headers.get("authorization")
    if url and auth:
        return url, auth
    raise RuntimeError("google-workspace MCP is not available locally")


def _mcp_headers(cfg: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    extra = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    for key, val in extra.items():
        if isinstance(key, str) and isinstance(val, str) and val.strip():
            headers[key] = val
    auth = cfg.get("auth")
    if isinstance(auth, dict):
        bearer = auth.get("Authorization") or auth.get("authorization")
        if isinstance(bearer, str) and bearer.strip():
            headers["Authorization"] = bearer
    return headers


def aisuite_port() -> int:
    path = HOME / ".aisuite" / "mcp-port"
    try:
        port = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 29051
    if 1 <= port <= 65535:
        return port
    return 29051


def aisuite_authorization() -> str:
    """Bearer for AI Suite on this machine. Never copied from another laptop."""
    path = HOME / ".aisuite" / "mcp-secret"
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not secret:
        return ""
    return secret if secret.lower().startswith("bearer ") else "Bearer " + secret


def aisuite_server_cfg(server: str) -> dict | None:
    """Google and Slack on this machine come from AI Suite, not DevBar."""
    auth = aisuite_authorization()
    if not auth:
        return None
    return {
        "type": "http",
        "url": "http://127.0.0.1:%s/mcp/servers/%s" % (aisuite_port(), server),
        "headers": {"Authorization": auth},
    }


def find_mcp_adaptor_bin() -> str:
    folder = HOME / ".mcp-adaptor" / "bin"
    hinted = (os.environ.get("MCP_ADAPTOR_BIN") or "").strip()
    if hinted and os.path.isfile(hinted):
        return hinted
    if folder.is_dir():
        versioned = sorted(
            (p for p in folder.glob("mcp-adaptor-go*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if versioned:
            return str(versioned[0])
        plain = folder / "mcp-adaptor"
        if plain.is_file():
            return str(plain)
    return shutil.which("mcp-adaptor-go") or shutil.which("mcp-adaptor") or ""


def dx_google_connected() -> bool:
    return dx_provider_connected("google-workspace-rw")


def dx_provider_connected(provider: str) -> bool:
    binary = find_mcp_adaptor_bin()
    if not binary or not provider:
        return False
    try:
        proc = subprocess.run(
            [binary, "auth", "--provider", provider, "--validate"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_DX_AUTH_LOCK = threading.Lock()
_DX_AUTH_PROCS: dict[str, subprocess.Popen] = {}


def start_dx_provider_auth(provider: str, log_name: str) -> None:
    binary = find_mcp_adaptor_bin()
    if not binary:
        raise RuntimeError("DX adaptor is not installed on this machine")
    with _DX_AUTH_LOCK:
        running = _DX_AUTH_PROCS.get(provider)
        if running is not None and running.poll() is None:
            return
        log = SKILL_ROOT / "out" / log_name
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log, "a", encoding="utf-8")
        _DX_AUTH_PROCS[provider] = subprocess.Popen(
            [binary, "auth", "--provider", provider],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def start_dx_google_auth() -> None:
    start_dx_provider_auth("google-workspace-rw", ".dx-google-auth.log")


def start_dx_gus_auth() -> None:
    start_dx_provider_auth("gus", ".dx-gus-auth.log")


def sf_gus_connected() -> bool:
    """True when the Salesforce CLI already has a usable gus org. Does not print the token."""
    binary = _gus_sf_bin()
    if not binary:
        return False
    try:
        proc = subprocess.run(
            [binary, "org", "display", "--target-org", "gus", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return False
    return bool(result.get("username") or result.get("id") or result.get("alias"))


_DX_LOCK = threading.Lock()
_DX_PROC: subprocess.Popen | None = None
_DX_SERVER = ""
_DX_SERVER_SKIP: set[str] = set()
_GOOGLE_FALLBACK_FAILED = False
_GOOGLE_HTTP_DEAD = False
_DX_BUF = bytearray()
_DX_TOOLS: dict[str, str] = {}
_DX_SEQ = 10
_ORGCS_BROWSER_SID = ""


def set_orgcs_browser_sid(sid: str) -> None:
    global _ORGCS_BROWSER_SID
    token = (sid or "").strip()
    _ORGCS_BROWSER_SID = token if len(token) >= 20 else ""


def orgcs_rest(path: str, timeout: float = 25) -> dict:
    sid = _ORGCS_BROWSER_SID
    if not sid:
        raise RuntimeError("OrgCS browser session is not available")
    req = urllib.request.Request(
        "https://orgcs.my.salesforce.com" + path,
        method="GET",
        headers={"Authorization": "Bearer " + sid, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            set_orgcs_browser_sid("")
            raise RuntimeError("OrgCS browser session expired") from exc
        raise
    parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
    return parsed if isinstance(parsed, dict) else {}


def orgcs_browser_session_ok() -> bool:
    if not _ORGCS_BROWSER_SID:
        return False
    try:
        me = orgcs_rest("/services/data/v68.0/chatter/users/me", timeout=8)
    except Exception:
        return False
    return str(me.get("id") or "").startswith("005")


def orgcs_browser_call(tool: str, arguments: dict, timeout: float = 25) -> str:
    if tool == "getUserInfo":
        me = orgcs_rest("/services/data/v68.0/chatter/users/me", timeout)
        uid = str(me.get("id") or "")
        return json.dumps(
            {
                "id": uid,
                "userId": uid,
                "email": me.get("email") or "",
                "username": me.get("username") or "",
                "name": me.get("name") or me.get("displayName") or "",
            }
        )
    if tool == "soqlQuery":
        query = str((arguments or {}).get("q") or (arguments or {}).get("query") or "").strip()
        if not query:
            raise RuntimeError("SOQL is required")
        body = orgcs_rest("/services/data/v68.0/query?q=" + urllib.parse.quote(query), timeout)
        records = body.get("records") if isinstance(body.get("records"), list) else []
        return json.dumps(
            {"records": records, "totalSize": body.get("totalSize"), "done": body.get("done")}
        )
    raise RuntimeError("unavailable")


def _dx_stop_locked() -> None:
    global _DX_PROC, _DX_TOOLS
    proc = _DX_PROC
    _DX_PROC = None
    _DX_TOOLS = {}
    _DX_BUF.clear()
    if proc and proc.poll() is None:
        proc.kill()


def _dx_take_message() -> dict | None:
    raw = bytes(_DX_BUF)
    if raw.startswith(b"{") or raw.startswith(b"["):
        line, _, rest = raw.partition(b"\n")
        if b"\n" not in raw and not line.endswith(b"}"):
            return None
        try:
            parsed = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        del _DX_BUF[: len(line) + (1 if rest or raw.endswith(b"\n") else 0)]
        return parsed if isinstance(parsed, dict) else None
    marker = b"Content-Length:"
    start = raw.find(marker)
    if start < 0:
        if len(raw) > 8192:
            del _DX_BUF[:-256]
        return None
    if start:
        del _DX_BUF[:start]
        raw = bytes(_DX_BUF)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return None
    length = 0
    for line in raw[:sep].decode("ascii", "replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip() or "0")
    body_at = sep + 4
    if len(raw) < body_at + length:
        return None
    parsed = json.loads(raw[body_at : body_at + length].decode("utf-8"))
    del _DX_BUF[: body_at + length]
    return parsed if isinstance(parsed, dict) else None


def _dx_read_locked(proc: subprocess.Popen, timeout: float) -> dict:
    deadline = time.time() + timeout
    fd = proc.stdout.fileno()
    while time.time() < deadline:
        got = _dx_take_message()
        if got is not None and (got.get("id") is not None or got.get("error")):
            return got
        wait = min(1.0, max(0.05, deadline - time.time()))
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        _DX_BUF.extend(chunk)
    raise TimeoutError("DX adaptor timed out")


def _dx_send_locked(proc: subprocess.Popen, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(raw))
    proc.stdin.write(raw)
    proc.stdin.flush()


def _dx_rpc_locked(proc: subprocess.Popen, method: str, params: dict, timeout: float) -> dict:
    global _DX_SEQ
    _DX_SEQ += 1
    msg_id = _DX_SEQ
    _dx_send_locked(
        proc,
        {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = _dx_read_locked(proc, max(0.2, deadline - time.time()))
        if msg.get("id") == msg_id:
            return msg
    raise TimeoutError("DX adaptor timed out")


def _dx_server_names() -> tuple[str, ...]:
    # Same provider the sign-in button uses. Other names each open another login.
    if _DX_SERVER == "google-workspace-rw" and "google-workspace-rw" not in _DX_SERVER_SKIP:
        return ("google-workspace-rw",)
    if "google-workspace-rw" in _DX_SERVER_SKIP:
        return tuple(
            name
            for name in ("google-workspace", "google_workspace")
            if name not in _DX_SERVER_SKIP
        )
    return ("google-workspace-rw",)


def _dx_ensure_locked(timeout: float) -> subprocess.Popen:
    global _DX_PROC, _DX_SERVER
    proc = _DX_PROC
    if proc and proc.poll() is None and _DX_TOOLS:
        return proc
    _dx_stop_locked()
    binary = find_mcp_adaptor_bin()
    if not binary:
        raise RuntimeError("DX adaptor is not installed on this machine")
    env = dict(os.environ)
    env.setdefault("MCP_ADAPTOR_ENV", "prod")
    last = ""
    for server in _dx_server_names():
        if _DX_PROC and _DX_PROC.poll() is None and _DX_TOOLS:
            return _DX_PROC
        proc = subprocess.Popen(
            [binary, "serve", "--server", server, "--log-level", "error"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            bufsize=0,
        )
        try:
            _dx_send_locked(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "engineer-day-planner", "version": "1"},
                    },
                },
            )
            init = _dx_read_locked(proc, timeout)
            if init.get("error"):
                raise RuntimeError("DX adaptor did not accept the Google login")
            _dx_send_locked(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = _dx_rpc_locked(proc, "tools/list", {}, timeout)
            tools = ((listed.get("result") or {}).get("tools") or []) if isinstance(listed, dict) else []
            mapped: dict[str, str] = {}
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "")
                if name:
                    mapped[name] = name
            if not mapped:
                raise RuntimeError("DX adaptor listed no Google tools")
            _DX_TOOLS.clear()
            _DX_TOOLS.update(mapped)
            _DX_PROC = proc
            _DX_SERVER = server
            return proc
        except Exception as exc:
            last = str(exc) or last
            _DX_SERVER_SKIP.add(server)
            if _DX_SERVER == server:
                _DX_SERVER = ""
            proc.kill()
            _DX_BUF.clear()
    raise RuntimeError(last or "DX adaptor did not accept the Google login")


def _dx_tool_name(wanted: str) -> str:
    if wanted in _DX_TOOLS:
        return wanted
    suffix = "_" + wanted
    for name in _DX_TOOLS:
        if name.endswith(suffix) or name.endswith("/" + wanted):
            return name
    return wanted


def dx_google_tools_call(name: str, arguments: dict, timeout: float = 30) -> str:
    with _DX_LOCK:
        proc = _dx_ensure_locked(timeout)
        tool = _dx_tool_name(name)
        result = _dx_rpc_locked(
            proc,
            "tools/call",
            {"name": tool, "arguments": arguments or {}},
            timeout,
        )
    return mcp_tools_text(result) or "ok"


def discover_mcp_servers(runner: str = "") -> dict[str, dict]:
    found: dict[str, tuple[float, int, dict]] = {}

    def score(cfg: dict) -> int:
        headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
        if any(str(headers.get(key) or "").strip() for key in ("Authorization", "authorization")):
            return 3
        if looks_oauth(cfg):
            return 2
        if cfg.get("url") or cfg.get("command"):
            return 1
        return 0

    def consider(name: str, cfg: dict, mtime: float) -> None:
        if not name or not isinstance(cfg, dict):
            return
        if not mcp_cfg_enabled(cfg):
            return
        ranked = score(cfg)
        if "/d/orgcs/" in str(cfg.get("url") or "").lower():
            ranked += 10
        prev = found.get(name)
        if prev and (prev[1] > ranked or (prev[1] == ranked and prev[0] >= mtime)):
            return
        found[name] = (mtime, ranked, cfg)

    for root in mcp_plugin_roots_for(runner):
        if not root.is_dir():
            continue
        for path in root.glob("**/.mcp.json"):
            if (path.parent / ".orphaned_at").exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            for name, cfg in mcp_servers_from_payload(data).items():
                consider(str(name), normalize_mcp_cfg(cfg) if isinstance(cfg, dict) else {}, mtime)
    for path in mcp_config_files_for(runner):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        for name, cfg in mcp_servers_from_payload(data).items():
            consider(str(name), normalize_mcp_cfg(cfg) if isinstance(cfg, dict) else {}, mtime)
    if "google-workspace" not in found:
        suite = aisuite_server_cfg("google-workspace")
        if suite:
            consider("google-workspace", suite, 0.0)
    return {name: cfg for name, (_mtime, _score, cfg) in found.items()}


def ping_http_mcp(cfg: dict, timeout: float = 4.0) -> str:
    url = str(cfg.get("url") or "").strip()
    if not url:
        return "disconnected"
    # Claude/Cursor/OpenCode OrgCS is OAuth HTTP. Tokens live in the host, not
    # this JSON. An unauthenticated initialize (401) is not a disconnect.
    if looks_oauth(cfg):
        return "connected"
    headers = _mcp_headers(cfg)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "engineer-day-planner", "version": "1"},
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    ctx = None
    if url.lower().startswith("https://"):
        ctx = ssl_ctx()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            code = int(getattr(resp, "status", 200) or 200)
    except Exception:
        return "disconnected"
    if code >= 400:
        return "disconnected"
    parsed = mcp_decode_http_json(raw)
    if isinstance(parsed, dict) and parsed.get("error"):
        return "disconnected"
    return "connected"


def ping_stdio_mcp(cfg: dict) -> str:
    command = str(cfg.get("command") or "").strip()
    if not command:
        return "disconnected"
    if os.path.isfile(command) or shutil.which(command):
        return "connected"
    return "disconnected"


def mcp_key_matches(key: str, needle: str) -> bool:
    if key == needle:
        return True
    parts = [part for part in re.split(r"[^a-z0-9]+", key) if part]
    return needle in parts


def pick_mcp_server(servers: dict, names: tuple[str, ...]) -> dict | None:
    lowered = {str(key).lower(): cfg for key, cfg in servers.items()}
    for name in names:
        cfg = lowered.get(name.lower())
        if cfg:
            return cfg
    for name in names:
        needle = name.lower()
        for key, cfg in lowered.items():
            if any(skip in key for skip in MCP_SKIP_KEYS):
                continue
            if mcp_key_matches(key, needle):
                return cfg
    return None


def mcp_server_candidates(servers: dict, names: tuple[str, ...]) -> list[dict]:
    """Every config that can serve this MCP. The first hit is not the only one."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(cfg: dict | None) -> None:
        if not isinstance(cfg, dict) or not mcp_cfg_enabled(cfg):
            return
        key = (str(cfg.get("url") or "").strip(), str(cfg.get("command") or "").strip())
        if key == ("", "") or key in seen:
            return
        seen.add(key)
        out.append(cfg)

    lowered = {str(key).lower(): cfg for key, cfg in servers.items()}
    for name in names:
        add(lowered.get(name.lower()))
    for name in names:
        needle = name.lower()
        for key, cfg in servers.items():
            if any(skip in str(key).lower() for skip in MCP_SKIP_KEYS):
                continue
            if mcp_key_matches(str(key), needle):
                add(cfg)
    return out


def aisuite_candidates_for(mcp_id: str) -> list[dict]:
    """AI Suite on this machine. DevBar does not host these servers."""
    if mcp_id in {"gmail", "calendar"}:
        cfg = aisuite_server_cfg("google-workspace")
        return [cfg] if cfg else []
    if mcp_id == "gus":
        cfg = aisuite_server_cfg("gus")
        return [cfg] if cfg else []
    if mcp_id == "slack":
        cfg = aisuite_server_cfg("slack")
        return [cfg] if cfg else []
    return []


def planner_mcp_status(runner: str = "") -> list[dict]:
    del runner
    rows = [
        {"id": key, "label": label, "status": "disconnected", "note": ""}
        for key, label, _names in PLANNER_MCPS
    ]
    google = aisuite_server_cfg("google-workspace")
    google_status = ping_http_mcp(google) if google else "disconnected"
    if google_status == "connected" or dx_google_connected():
        for row in rows:
            if row["id"] in {"gmail", "calendar"}:
                row["status"] = "connected"
                row["note"] = ""
    elif not google:
        for row in rows:
            if row["id"] in {"gmail", "calendar"}:
                row["note"] = "AI Suite has no Google credential on this machine"
    else:
        for row in rows:
            if row["id"] in {"gmail", "calendar"}:
                row["note"] = "AI Suite did not accept the Google login"
    servers = discover_mcp_servers("")
    jobs = []
    url_index: dict[str, list[int]] = {}
    by_id = {row["id"]: row for row in rows}
    for key, _label, names in PLANNER_MCPS:
        row = by_id[key]
        if row["status"] == "connected":
            continue
        candidates = mcp_server_candidates(servers, names) + aisuite_candidates_for(key)
        for cfg in candidates:
            if looks_oauth(cfg):
                row["status"] = "connected"
                row["note"] = ""
                break
            url = str(cfg.get("url") or "").strip()
            if not url:
                if ping_stdio_mcp(cfg) == "connected":
                    row["status"] = "connected"
                    row["note"] = ""
                    break
                continue
            headers = _mcp_headers(cfg)
            auth = str(headers.get("Authorization") or headers.get("authorization") or "")
            slot = url + "\n" + auth
            url_index.setdefault(slot, []).append(key)
            if not any(job[0] == url and job[2] == auth for job in jobs):
                jobs.append((url, cfg, auth))
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            futs = {pool.submit(ping_http_mcp, cfg): (url, auth) for url, cfg, auth in jobs}
            for fut in as_completed(futs):
                url, auth = futs[fut]
                try:
                    status = fut.result() or "disconnected"
                except Exception:
                    status = "disconnected"
                if status != "connected":
                    continue
                for mcp_id in url_index.get(url + "\n" + auth, []):
                    by_id[mcp_id]["status"] = "connected"
                    by_id[mcp_id]["note"] = ""
                if "google-workspace" in url:
                    for mcp_id in ("gmail", "calendar"):
                        by_id[mcp_id]["status"] = "connected"
                        by_id[mcp_id]["note"] = ""
    for row in rows:
        if row.get("status") != "connected":
            row["status"] = "disconnected"
    orgcs = by_id.get("orgcs")
    if orgcs and orgcs.get("status") != "connected" and orgcs_browser_session_ok():
        orgcs["status"] = "connected"
        orgcs["note"] = ""
    gus = by_id.get("gus")
    if gus and gus.get("status") != "connected" and (sf_gus_connected() or dx_provider_connected("gus")):
        gus["status"] = "connected"
        gus["note"] = ""
    return rows


def mcp_decode_http_json(body: bytes) -> dict:
    text = (body or b"").decode("utf-8", "replace").strip()
    if not text:
        return {}
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        try:
            parsed = json.loads(data_lines[-1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def mcp_tools_text(parsed: dict) -> str:
    if parsed.get("error"):
        err = parsed.get("error")
        if isinstance(err, dict):
            raise RuntimeError(str(err.get("message") or "MCP error"))
        raise RuntimeError(str(err) or "MCP error")
    result = parsed.get("result") or {}
    chunks = result.get("content") or []
    text = "\n".join(c.get("text") or "" for c in chunks if isinstance(c, dict))
    structured = result.get("structuredContent")
    if not text and isinstance(structured, dict):
        inner = structured.get("result")
        if isinstance(inner, (dict, list)):
            text = json.dumps(inner)
        elif inner:
            text = str(inner)
        else:
            text = json.dumps(structured)
    return text or ""


def mcp_tools_call(url: str, headers: dict, name: str, arguments: dict, timeout: float = 30) -> str:
    ctx = ssl_ctx() if str(url).lower().startswith("https://") else None

    def post(payload, session=None):
        h = dict(headers)
        if session:
            h["Mcp-Session-Id"] = session
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=h
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.headers.get("Mcp-Session-Id"), resp.read()

    _, session, _ = post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "engineer-day-planner", "version": "1"},
            },
        }
    )
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    st, _, body = post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session,
    )
    parsed = mcp_decode_http_json(body)
    text = mcp_tools_text(parsed)
    if st >= 400:
        raise RuntimeError(text or f"HTTP {st}")
    return text or "ok"


def reset_google_fallback() -> None:
    """One Gmail/Calendar sign-in attempt per Run Planner, not one per fetch."""
    global _GOOGLE_FALLBACK_FAILED, _GOOGLE_HTTP_DEAD, _DX_SERVER
    _GOOGLE_FALLBACK_FAILED = False
    _GOOGLE_HTTP_DEAD = False
    _DX_SERVER = ""
    _DX_SERVER_SKIP.clear()


def _dx_session_ready() -> bool:
    proc = _DX_PROC
    return bool(proc and proc.poll() is None and _DX_TOOLS)


def mcp_call(name: str, arguments: dict) -> str:
    """Gmail and Calendar. A 404 on the local HTTP server is not a failed login.

    After that 404, use the Google sign-in session. Signing in does not make the
    dead address start working, so later calls in the same run must not go back to it.
    """
    global _GOOGLE_HTTP_DEAD
    if _dx_session_ready() and _GOOGLE_HTTP_DEAD:
        return dx_google_tools_call(name, arguments)
    primary = None
    if not _GOOGLE_HTTP_DEAD:
        try:
            url, auth = load_mcp()
            headers = {
                "Authorization": auth,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            return mcp_tools_call(url, headers, name, arguments)
        except Exception as exc:
            primary = exc
            text = str(exc)
            if "404" in text or "Not Found" in text:
                _GOOGLE_HTTP_DEAD = True
    if not dx_provider_connected("google-workspace-rw"):
        try:
            start_dx_google_auth()
        except Exception as exc:
            if primary is not None and not _GOOGLE_HTTP_DEAD:
                raise primary
            raise RuntimeError(str(exc) or "Could not open the Google sign-in") from exc
        raise RuntimeError("Google sign-in is open. Finish it, then run the planner again.")
    try:
        return dx_google_tools_call(name, arguments)
    except Exception as exc:
        detail = str(exc).strip() or "Google sign-in session could not load Gmail or Calendar"
        raise RuntimeError(detail) from exc


def mcp_http_ready(cfg: dict) -> bool:
    if not isinstance(cfg, dict) or not mcp_cfg_enabled(cfg):
        return False
    url = str(cfg.get("url") or "").strip()
    headers = _mcp_headers(cfg)
    auth = headers.get("Authorization") or headers.get("authorization")
    return bool(url and auth)


def callable_mcp_cfgs(names: tuple[str, ...]) -> list[dict]:
    servers = discover_mcp_servers()
    ranked: list[tuple[int, dict]] = []
    seen = set()
    for key, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        kl = str(key).lower()
        if any(skip in kl for skip in MCP_SKIP_KEYS):
            continue
        if not any(kl == n.lower() or mcp_key_matches(kl, n.lower()) for n in names):
            continue
        if not mcp_http_ready(cfg):
            continue
        url = str(cfg.get("url") or "").strip()
        if url in seen:
            continue
        seen.add(url)
        headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
        score = 3 if any(str(headers.get(h) or "").strip() for h in ("Authorization", "authorization")) else 1
        if "127.0.0.1" in url or "localhost" in url:
            score += 1
        ranked.append((score, cfg))
    ranked.sort(key=lambda row: -row[0])
    return [cfg for _score, cfg in ranked]


def mcp_call_named(names: tuple[str, ...], tool: str, arguments: dict, timeout: float = 25) -> str:
    last = None
    for cfg in callable_mcp_cfgs(names):
        url = str(cfg.get("url") or "").strip()
        headers = _mcp_headers(cfg)
        try:
            return mcp_tools_call(url, headers, tool, arguments, timeout=timeout)
        except Exception as exc:
            last = exc
            continue
    orgcs_names = {item.lower() for item in ORGCS_MCP_NAMES}
    if any(str(name).lower() in orgcs_names for name in names) and tool in {"getUserInfo", "soqlQuery"}:
        if _ORGCS_BROWSER_SID:
            return orgcs_browser_call(tool, arguments, timeout)
    if last is not None:
        raise last
    raise RuntimeError("unavailable")


SKIP_TITLES = {
    "casework",
    "chat",
}
SKIP_PREFIXES = ("chat /", "chat —", "chat -")

ASK_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "did", "can", "could", "should", "would", "will",
    "what", "whats", "which", "who", "whom", "whose", "when", "where",
    "why", "how", "this", "that", "these", "those", "it", "its",
    "my", "me", "i", "you", "we", "our", "your", "please", "tell",
    "give", "show", "about", "for", "from", "with", "and", "or", "of",
    "to", "in", "on", "at", "by", "any", "some", "just", "also",
    "as", "mark",
}


def unpublished_page_html() -> bytes:
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>engineer day planner</title>
  <script>
    (function () {
      try {
        var saved = localStorage.getItem("engineer-day-planner-theme");
        var t = (saved === "light" || saved === "dark")
          ? saved
          : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
        document.documentElement.setAttribute("data-theme", t);
      } catch (e) {
        try {
          document.documentElement.setAttribute(
            "data-theme",
            window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
          );
        } catch (e2) {}
      }
    })();
  </script>
  <style>
    :root, html[data-theme="light"] {
      color-scheme: light;
      --bg: #f3eee6;
      --surface: #fffaf4;
      --text: #2c2416;
      --muted: #6e6456;
      --line: #e4d9c8;
      --accent: #b4532a;
    }
    html[data-theme="dark"] {
      color-scheme: dark;
      --bg: #1a1612;
      --surface: #2a241c;
      --text: #f6efe6;
      --muted: #c9b8a4;
      --line: #8a7a68;
      --accent: #e08a4a;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .wrap { position: relative; max-width: 36rem; margin: 18vh auto 0; padding: 0 16px 24px; }
    .theme-toggle {
      position: absolute;
      top: 0;
      right: 16px;
      width: 36px;
      height: 36px;
      padding: 0;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
    }
    .theme-toggle .moon { display: none; }
    html[data-theme="dark"] .theme-toggle .sun { display: none; }
    html[data-theme="dark"] .theme-toggle .moon { display: block; }
    .theme-toggle svg { width: 18px; height: 18px; display: block; margin: 0 auto; }
    main {
      padding: 24px 24px 18px;
      background: var(--surface);
      color: var(--text);
      border-radius: 12px;
      border: 1px solid var(--line);
    }
    h1 { font-size: 18px; margin: 0 0 8px; }
    .ver { margin: 0 0 8px; color: var(--muted); font-size: 12px; }
    p { margin: 0 0 10px; color: var(--muted); }
    strong { color: var(--accent); }
    .copy { margin: 16px 0 0; font-size: 12px; }
  </style>
</head>
<body>
  <div class="wrap">
    <button type="button" class="theme-toggle" id="theme-toggle" role="switch" aria-checked="false" aria-label="Switch to dark theme">
      <svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="4"></circle>
        <path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M6.3 17.7 4.9 19.1M19.1 4.9l-1.4 1.4"></path>
      </svg>
      <svg class="moon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M15.1 3.3a9 9 0 1 0 5.6 14.2 7.2 7.2 0 0 1-5.6-14.2z"></path>
      </svg>
    </button>
    <main>
      <h1>No plan for today yet</h1>
      <p class="ver">Version: __EDP_VERSION__</p>
      <p>A plan stays for six hours so you are not working from an old one. Run Planner when you want a fresh plan.</p>
      <p class="copy">© 2026 Kiran Kumar Garai &lt;kgarai@salesforce.com&gt;. Skill authored by Kiran Kumar Garai.</p>
    </main>
  </div>
  <script>
    (function () {
      function systemTheme() {
        try {
          return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        } catch (e) {
          return "light";
        }
      }
      function savedTheme() {
        try {
          var saved = localStorage.getItem("engineer-day-planner-theme");
          return (saved === "light" || saved === "dark") ? saved : "";
        } catch (e) {
          return "";
        }
      }
      function applyTheme(t, persist) {
        if (t !== "dark" && t !== "light") t = systemTheme();
        document.documentElement.setAttribute("data-theme", t);
        var sw = document.getElementById("theme-toggle");
        if (sw) {
          sw.setAttribute("aria-checked", t === "dark" ? "true" : "false");
          sw.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
        }
        if (persist) {
          try { localStorage.setItem("engineer-day-planner-theme", t); } catch (e) {}
        }
        try {
          if (window.parent !== window) {
            window.parent.postMessage({ source: "engineer-day-planner", type: "theme", theme: t }, "*");
          }
        } catch (e2) {}
      }
      applyTheme(savedTheme() || systemTheme(), false);
      try {
        window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
          if (!savedTheme()) applyTheme(systemTheme(), false);
        });
      } catch (e) {}
      var btn = document.getElementById("theme-toggle");
      if (btn) {
        btn.onclick = function () {
          var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
          applyTheme(cur === "dark" ? "light" : "dark", true);
        };
      }
    })();
  </script>
</body>
</html>
"""
    version = packed_extension_version() or ""
    return html.replace("__EDP_VERSION__", version).encode("utf-8")


def unpublished_snapshot() -> dict:
    return {
        "ok": True,
        "unpublished": True,
        "needYou": 0,
        "openCases": 0,
        "reminders": [],
        "page": "/current.html",
        "briefing": "/briefing.json",
    }


BRIEFING_SCRIPT_START = '<script type="application/json" id="briefing-data">'
BRIEFING_SCRIPT_END = "</script>"
PAGE_SYNC_MAX = 2_000_000


def briefing_json_path() -> pathlib.Path:
    return PAGE.parent / "briefing.json"


def extract_briefing_from_html(text: str) -> dict:
    """Literal slice — never a greedy regex over current.html."""
    start = text.find(BRIEFING_SCRIPT_START)
    if start < 0:
        raise RuntimeError("no briefing data on the page")
    start += len(BRIEFING_SCRIPT_START)
    end = text.find(BRIEFING_SCRIPT_END, start)
    if end < 0:
        raise RuntimeError("no briefing data on the page")
    payload = json.loads(text[start:end])
    if not isinstance(payload, dict):
        raise RuntimeError("briefing data is not an object")
    return payload


def write_briefing_json_file(payload: dict) -> None:
    path = briefing_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)


def load_page_briefing() -> dict:
    path = briefing_json_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    if not PAGE.is_file():
        raise FileNotFoundError("planner page not published yet")
    payload = extract_briefing_from_html(PAGE.read_text(encoding="utf-8"))
    try:
        write_briefing_json_file(payload)
    except OSError:
        pass
    return payload


def write_briefing_data(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("briefing data is not an object")
    clean = {k: v for k, v in payload.items() if k not in ("_viewTz", "_viewShort")}
    write_briefing_json_file(clean)
    if not PAGE.is_file():
        raise FileNotFoundError("planner page not published yet")
    raw = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if "</" in raw:
        raw = raw.replace("</", "<\\/")
    if len(raw) > PAGE_SYNC_MAX:
        raise RuntimeError("page is too large to save")
    text = PAGE.read_text(encoding="utf-8")
    start = text.find(BRIEFING_SCRIPT_START)
    if start < 0:
        raise RuntimeError("no briefing data on the page")
    start += len(BRIEFING_SCRIPT_START)
    end = text.find(BRIEFING_SCRIPT_END, start)
    if end < 0:
        raise RuntimeError("no briefing data on the page")
    PAGE.write_text(text[:start] + raw + text[end:], encoding="utf-8")


def done_ledger_path() -> pathlib.Path:
    return PAGE.parent / ".done-keys.json"


def load_done_ledger() -> dict:
    path = done_ledger_path()
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rec, dict) and isinstance(rec.get("keys"), dict):
            return rec
    except Exception:
        pass
    return {"keys": {}, "updatedAt": 0}


def save_done_ledger_from_data(data: dict) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "edp_sanitize_done", SKILL_ROOT / "scripts" / "sanitize-briefing.py"
    )
    sanit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sanit)
    rec = load_done_ledger()
    keys = rec.get("keys") if isinstance(rec.get("keys"), dict) else {}
    now_ms = int(time.time() * 1000)
    keys = dict(keys)
    for k in sanit.collect_done_keys(data):
        keys[k] = keys.get(k) or now_ms
    for k in sanit.undone_item_keys(data):
        keys.pop(k, None)
    rec["keys"] = sanit.drop_google_done_keys(sanit.prune_done_key_map(keys, now_ms))
    rec["updatedAt"] = now_ms
    path = done_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


def _sanitize_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "edp_sanitize_mod", SKILL_ROOT / "scripts" / "sanitize-briefing.py"
    )
    sanit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sanit)
    return sanit


def _self_check_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "edp_self_check", SKILL_ROOT / "scripts" / "self-check-plan.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_stub_peek(summary: str) -> bool:
    try:
        return bool(_self_check_mod().is_stub_peek(summary))
    except Exception:
        return bool(
            re.search(
                r"last wrote:|Last outbound \([^)]+\):|OrgCS status is ",
                str(summary or ""),
                re.I,
            )
        )


def _important_event_ids(data: dict) -> list:
    ids = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        rows = list(sec.get("items") or [])
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                rows.extend(group.get("items") or [])
        for item in rows:
            if not isinstance(item, dict):
                continue
            eid = str(item.get("eventId") or "").strip()
            if eid and item.get("important") is True:
                ids.append(eid)
    return ids


def reattach_primary_calendar(data: dict | None = None) -> None:
    """Re-fetch today's primary events so morning Google rows survive a gather that started at now."""
    if data is None:
        data = load_live_briefing()
    tzname = str(data.get("timezone") or "").strip()
    result = fetch_primary_events(
        tzname,
        important_ids=_important_event_ids(data),
        shift_start=data.get("shiftStart"),
        shift_end=data.get("shiftEnd"),
    )
    merge_calendar_into_page(result.get("events") or [], tzname)


WATCH_TAKEN_RE = re.compile(
    r"needs (us|you) now|follow-up due|customer asked for a meeting",
    re.I,
)
WATCH_SEC_RE = re.compile(r"still watching|no action needed", re.I)
ORGCS_CASE_URL = "https://orgcs.lightning.force.com/lightning/r/Case/{id}/view"


def _short_watch_label(number: str, subject: str) -> str:
    sub = re.sub(r"(?i)^outbound contact:\s*action required:\s*", "", subject or "")
    sub = re.sub(r"(?i)^resolve dns records for\s*", "DNS ", sub)
    sub = re.sub(r"\s+", " ", sub).strip()
    if len(sub) > 70:
        sub = sub[:69] + "…"
    return f"#{number} — {sub}"


def _sev_short(raw: str) -> str:
    text = str(raw or "").strip()
    match = re.search(r"Level\s+(\d+)", text, re.I)
    if match:
        return f"Sev{match.group(1)}"
    return text.split(" - ")[0] if text else ""


def _row_is_closed_case(row: dict) -> bool:
    flag = row.get("IsClosed")
    if flag is True:
        return True
    return str(flag).strip().lower() in {"true", "1"}


def persist_owned_cases(rows: list[dict], *, fetched: bool = False) -> None:
    open_rows = [row for row in rows if isinstance(row, dict) and not _row_is_closed_case(row)]
    if not open_rows and not fetched:
        return
    try:
        OWNED_CASES_FILE.write_text(
            json.dumps({"records": open_rows, "fetched": True}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def persist_owned_open_from_recs(recs: list) -> int:
    """Sidecar Case SOQL (no CommentBody) → /tmp/owned-cases.json. Closed rows dropped."""
    rows = []
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("CommentBody") is not None or rec.get("TextBody") is not None:
            return 0
        if rec.get("ParentId") and not rec.get("CaseNumber"):
            return 0
        cid = str(rec.get("Id") or "").strip()
        num = str(rec.get("CaseNumber") or "").strip()
        if not cid.startswith("500") or not num:
            continue
        if _row_is_closed_case(rec):
            continue
        rows.append(rec)
    if rows:
        persist_owned_cases(rows, fetched=True)
    return len(rows)


def _sidecar_owned_cases() -> list[dict]:
    path = OWNED_CASES_FILE
    if not path.is_file():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    recs = []
    if isinstance(obj, list):
        recs = [row for row in obj if isinstance(row, dict)]
    elif isinstance(obj, dict) and isinstance(obj.get("records"), list):
        recs = [row for row in obj["records"] if isinstance(row, dict)]
    return [row for row in recs if not _row_is_closed_case(row)]


def _owned_fetch_ok() -> bool:
    try:
        obj = json.loads(OWNED_CASES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(obj, dict) and obj.get("fetched") is True


def fetch_owned_open_cases() -> list[dict]:
    try:
        info = mcp_call_named(ORGCS_MCP_NAMES, "getUserInfo", {}, timeout=20)
        uid = parse_orgcs_user_id(info)
        if uid and "'" not in uid:
            rows = []
            offset = 0
            queried = False
            while offset <= 2000:
                soql = (
                    "SELECT Id, CaseNumber, Subject, Description, Status, Severity_Level__c, LastModifiedDate, "
                    "SE_Initial_Response_Status__c, SE_Target_Response__c, First_Response_Date_Time__c, "
                    "GUS_Investigation_Number__c, Display_Bug__c "
                    "FROM Case WHERE OwnerId = '%s' AND IsClosed = false "
                    "ORDER BY LastModifiedDate DESC LIMIT 80 OFFSET %d" % (uid, offset)
                )
                text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=25)
                queried = True
                chunk = parse_soql_records(text)
                if not chunk:
                    break
                rows.extend(chunk)
                if len(chunk) < 80:
                    break
                offset += 80
            if queried:
                persist_owned_cases(rows, fetched=True)
                return rows
    except Exception:
        pass
    return _sidecar_owned_cases()


def repair_plan_payload(
    data: dict,
    rows: list | None = None,
    *,
    refresh_inbox: bool | None = None,
    wipe_peek_summary: bool = False,
) -> dict:
    """Fill skinny salvage plans: Slack group titles, caseUrls, leftover Still watching."""
    if not isinstance(data, dict):
        return data
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for group in sec.get("groups") or []:
            if (
                isinstance(group, dict)
                and not str(group.get("title") or "").strip()
                and group.get("name")
            ):
                group["title"] = group["name"]
    if rows is None:
        try:
            rows = fetch_owned_open_cases()
        except Exception:
            rows = []
    by_num = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("CaseNumber") or "").strip()
        if num:
            by_num[num] = row
    snapshot_ok = bool(by_num) or _owned_fetch_ok()
    if snapshot_ok:
        try:
            _sanitize_mod().drop_unowned_cases(data, owned=set(by_num))
        except Exception:
            pass
        data["openCases"] = len(by_num)
    taken = set()
    watch_sec = None
    watch_idx = None
    follow_idx = None
    sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for idx, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        if WATCH_SEC_RE.search(title):
            watch_sec = sec
            watch_idx = idx
        if re.search(r"follow-up due", title, re.I):
            follow_idx = idx
        if WATCH_TAKEN_RE.search(title):
            for item in sec.get("items") or []:
                if isinstance(item, dict) and item.get("caseNumber"):
                    taken.add(str(item["caseNumber"]))
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        bag = list(sec.get("items") or [])
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                bag.extend(group.get("items") or [])
        for item in bag:
            if not isinstance(item, dict):
                continue
            num = str(item.get("caseNumber") or "").strip()
            row = by_num.get(num)
            if not row:
                continue
            cid = str(row.get("Id") or "").strip()
            if cid:
                item["caseUrl"] = ORGCS_CASE_URL.format(id=cid)
            status = str(row.get("Status") or "").strip()
            sev = _sev_short(str(row.get("Severity_Level__c") or ""))
            ir_bit = "IR pending" if ir_is_pending(row) else ""
            live_detail = " · ".join(part for part in (sev, status, ir_bit) if part)
            if live_detail:
                item["detail"] = live_detail
            if status:
                item["status"] = status
    if snapshot_ok:
        taken |= fill_need_now_from_status(data, rows)
        if watch_sec is None:
            watch_sec = {"title": "Still watching", "open": False, "items": []}
            insert_at = (follow_idx + 1) if follow_idx is not None else len(sections)
            sections.insert(insert_at, watch_sec)
            data["sections"] = sections
        new_items = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            num = str(row.get("CaseNumber") or "").strip()
            cid = str(row.get("Id") or "").strip()
            if not num or not cid or num in taken:
                continue
            sev = _sev_short(str(row.get("Severity_Level__c") or ""))
            status = str(row.get("Status") or "").strip()
            ir_bit = "IR pending" if ir_is_pending(row) else ""
            new_items.append(
                {
                    "id": f"watch-{num}",
                    "kind": "case",
                    "label": _short_watch_label(num, str(row.get("Subject") or "")),
                    "detail": " · ".join(part for part in (sev, status, ir_bit) if part),
                    "status": status,
                    "caseNumber": num,
                    "caseUrl": ORGCS_CASE_URL.format(id=cid),
                }
            )
        existing_by_num = {
            str(it.get("caseNumber")): it
            for it in (watch_sec.get("items") or [])
            if isinstance(it, dict) and it.get("caseNumber")
        }
        merged_watch = []
        for item in new_items:
            prev = existing_by_num.get(str(item.get("caseNumber") or ""))
            if prev:
                if item.get("caseUrl"):
                    prev["caseUrl"] = item["caseUrl"]
                if item.get("label"):
                    prev["label"] = item["label"]
                if item.get("detail"):
                    prev["detail"] = item["detail"]
                if item.get("status"):
                    prev["status"] = item["status"]
                merged_watch.append(prev)
            else:
                merged_watch.append(item)
        watch_sec["items"] = merged_watch
        if merged_watch:
            watch_sec.pop("empty", None)
        else:
            watch_sec["empty"] = "No action needed — clear"
        data["openCases"] = len(rows)
        fill_needs_peek(data, rows, wipe_summary=wipe_peek_summary)
    if refresh_inbox is None:
        refresh_inbox = data.get("inboxReviewed") is not True
    if refresh_inbox and not data.get("_mailFetchTried"):
        data["inboxReviewed"] = False
        data["_mailFetchTried"] = True
        try:
            fill_slack_leftovers(data)
        except Exception as exc:
            data["slackFetchOk"] = False
            data["slackFetchError"] = clip(str(exc), 180)
            append_plan_step(kind="log", label="Slack leftover fetch failed · " + data["slackFetchError"])
        try:
            fill_mail_leftovers(data)
        except Exception as exc:
            data["mailFetchOk"] = False
            data["mailFetchError"] = clip(str(exc), 180)
            append_plan_step(kind="log", label="Mail leftover fetch failed · " + data["mailFetchError"])
    return data


NEED_NOW_STATUSES = {
    "working",
    "new",
    "escalated",
    "assigned",
    "researching",
    "investigating",
}
PEEK_SKIP_COMMENT = re.compile(
    r"HTIR Integration|Hyperforce\s*-",
    re.I,
)
PEEK_SUBSTANCE_N = 4000
MAIL_DROP_RE = re.compile(
    r"case comment added on case|has been assigned\s+\||outbound contact:|"
    r"ref:!|customersupport@salesforce|email-to-case|employee comms|"
    r"inbox summary|prepare for the .*instance refresh",
    re.I,
)
SLACK_MCP_NAMES = ("slack",)
GUS_MCP_NAMES = ("gus_server", "gus-server", "gus")


def _pt_when(iso: str, tzname: str = "") -> str:
    """Calendar date and time in GMT. Weekday labels are not stored."""
    del tzname
    raw = str(iso or "").strip()
    if not raw:
        return ""
    raw = raw.replace(".000+0000", "+00:00").replace("+0000", "+00:00").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    hour = utc.strftime("%I").lstrip("0") or "12"
    return f"{utc.strftime('%b')} {utc.day}, {utc.year}, {hour}:{utc.strftime('%M %p')} GMT"


def _comment_substance(body: str) -> str:
    text = re.sub(r"(?i)<Created By:[^>]+>", " ", body or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > PEEK_SUBSTANCE_N:
        text = text[: PEEK_SUBSTANCE_N - 1].rstrip() + "…"
    return text


def _comment_kind(body: str, who: str, published: bool) -> str | None:
    raw = body or ""
    if PEEK_SKIP_COMMENT.search(raw):
        return None
    if re.search(r"<Created By:", raw, re.I):
        return "customer"
    if not published:
        return "internal"
    return "public"


def fetch_case_comments(case_id: str) -> list[dict]:
    cid = str(case_id or "").replace("'", "")
    if not cid.startswith("500"):
        return []
    return _pages_for_parent(
        cid,
        "SELECT CommentBody, CreatedDate, CreatedBy.Name, IsPublished FROM CaseComment",
        "CreatedDate",
    )


def peek_from_comments(row: dict, comments: list[dict], tzname: str) -> dict:
    events = []
    for rec in comments or []:
        if not isinstance(rec, dict):
            continue
        who_obj = rec.get("CreatedBy") if isinstance(rec.get("CreatedBy"), dict) else {}
        who = str(who_obj.get("Name") or rec.get("CreatedBy") or "").strip()
        body = str(rec.get("CommentBody") or "")
        kind = _comment_kind(body, who, rec.get("IsPublished") is True)
        if not kind:
            continue
        text = _comment_substance(body)
        if not text:
            continue
        events.append(
            {
                "when": _pt_when(str(rec.get("CreatedDate") or ""), tzname),
                "who": who,
                "kind": kind,
                "text": text,
                "_ts": str(rec.get("CreatedDate") or ""),
            }
        )
    chrono = [{k: ev[k] for k in ("when", "who", "kind", "text", "_ts") if ev.get(k) not in (None, "")} for ev in events]
    return {"summary": "", "chronology": chrono}


_ACTIVITY_PAGE = 200


def _soql_literal_time(ts: str) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", str(ts or ""))
    return match.group(1) + "Z" if match else ""


def _pages_for_parent(cid: str, select_from: str, order_field: str) -> list[dict]:
    """Every child row for one case. Page until a short page. Do not share a LIMIT across cases."""
    rows: list[dict] = []
    cursor = ""
    for _ in range(25):
        older = f" AND {order_field} < {cursor}" if cursor else ""
        soql = (
            f"{select_from} WHERE ParentId = '{cid}'{older} "
            f"ORDER BY {order_field} DESC LIMIT {_ACTIVITY_PAGE}"
        )
        try:
            recs = parse_soql_records(
                mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=30)
            )
        except Exception:
            break
        page = [rec for rec in recs if isinstance(rec, dict)]
        if not page:
            break
        rows.extend(page)
        if len(page) < _ACTIVITY_PAGE:
            break
        cursor = _soql_literal_time(page[-1].get(order_field) or page[-1].get("CreatedDate"))
        if not cursor:
            break
    return rows


def _case_ids(case_ids: list[str]) -> list[str]:
    clean = []
    seen = set()
    for raw in case_ids:
        cid = str(raw or "").replace("'", "")
        if cid.startswith("500") and cid not in seen:
            seen.add(cid)
            clean.append(cid)
    return clean


def fetch_comments_by_parent(case_ids: list[str]) -> dict[str, list]:
    out: dict[str, list] = {}
    select_from = "SELECT ParentId, CommentBody, CreatedDate, CreatedBy.Name, IsPublished FROM CaseComment"
    for cid in _case_ids(case_ids):
        recs = _pages_for_parent(cid, select_from, "CreatedDate")
        if recs:
            out[cid] = recs
    return out


def _stamp_thread_objects(flags: dict, recs: list | None = None, sample: str = "") -> None:
    bag = flags.setdefault("thread_objects", set())
    blob = sample or json.dumps(recs or [])[:12000]
    if "CommentBody" in blob:
        bag.add("comment")
    if "TextBody" in blob or '"Incoming"' in blob or '"incoming"' in blob:
        bag.add("email")
    if '"Type"' in blob and ("TextPost" in blob or "CaseFeed" in blob or "Body" in blob):
        bag.add("feed")
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("CommentBody") is not None:
            bag.add("comment")
        if rec.get("TextBody") is not None or rec.get("Incoming") is not None:
            bag.add("email")
        if rec.get("Type") and (rec.get("Body") is not None or rec.get("ParentId")):
            bag.add("feed")


def _soql_by_parent(soql: str) -> list[dict]:
    try:
        text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=45)
    except Exception:
        return []
    recs = [row for row in parse_soql_records(text) if isinstance(row, dict)]
    if recs:
        return recs
    for saved in list(OVERFLOW_SAVED_RE.findall(text or "")) + list(OVERFLOW_PATH_RE.findall(text or "")):
        ingest_overflow_file(str(saved or "").strip())
    return []


def _group_parent_rows(recs: list[dict], ts_key: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for rec in recs:
        pid = str(rec.get("ParentId") or "")
        if pid:
            out.setdefault(pid, []).append(rec)
    for pid, rows in out.items():
        rows.sort(key=lambda row: str(row.get(ts_key) or row.get("CreatedDate") or ""), reverse=True)
        out[pid] = rows
    return out


def fetch_emails_by_parent(case_ids: list[str]) -> dict[str, list]:
    out: dict[str, list] = {}
    select_from = (
        "SELECT ParentId, Subject, TextBody, FromName, FromAddress, Incoming, "
        "MessageDate, CreatedDate FROM EmailMessage"
    )
    for cid in _case_ids(case_ids):
        recs = _pages_for_parent(cid, select_from, "MessageDate")
        if recs:
            out[cid] = recs
    return out


def fetch_feeds_by_parent(case_ids: list[str]) -> dict[str, list]:
    out: dict[str, list] = {}
    skip_types = {
        "casecomment",
        "casecommentpost",
        "emailmessageevent",
        "collaborationgrouppost",
        "trackedchange",
        "changestatuspost",
        "createrecordevent",
    }
    select_from = "SELECT ParentId, Type, CreatedDate, CreatedBy.Name, Body FROM CaseFeed"
    for cid in _case_ids(case_ids):
        kept = []
        for rec in _pages_for_parent(cid, select_from, "CreatedDate"):
            kind = str(rec.get("Type") or "").strip().lower()
            body = str(rec.get("Body") or "").strip()
            if kind in skip_types or not body:
                continue
            kept.append(rec)
        if kept:
            out[cid] = kept
    return out


def _truthy(val) -> bool:
    return val is True or val == 1 or str(val).strip().lower() in {"true", "1"}


def _activity_epoch(ts: str) -> float:
    text = str(ts or "").strip()
    if not text:
        return 0.0
    text = text.replace(".000+0000", "+00:00").replace("+0000", "+00:00").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _activity_event(when: str, who: str, kind: str, text: str, ts: str) -> dict | None:
    body = _comment_substance(text)
    if not body:
        return None
    return {
        "when": when or "",
        "who": str(who or "").strip(),
        "kind": kind,
        "text": body,
        "_ts": str(ts or ""),
    }


def merge_case_activity(
    comments: list[dict], emails: list[dict], feeds: list[dict], tzname: str
) -> list[dict]:
    events = []
    for rec in comments or []:
        if not isinstance(rec, dict):
            continue
        who_obj = rec.get("CreatedBy") if isinstance(rec.get("CreatedBy"), dict) else {}
        who = str(who_obj.get("Name") or rec.get("CreatedBy") or "").strip()
        kind = _comment_kind(str(rec.get("CommentBody") or ""), who, _truthy(rec.get("IsPublished")))
        if not kind:
            continue
        ev = _activity_event(
            _pt_when(str(rec.get("CreatedDate") or ""), tzname),
            who,
            kind,
            str(rec.get("CommentBody") or ""),
            str(rec.get("CreatedDate") or ""),
        )
        if ev:
            events.append(ev)
    for rec in emails or []:
        if not isinstance(rec, dict):
            continue
        incoming = _truthy(rec.get("Incoming"))
        who = str(rec.get("FromName") or rec.get("FromAddress") or "").strip()
        body = str(rec.get("TextBody") or rec.get("Subject") or "")
        ts = str(rec.get("MessageDate") or rec.get("CreatedDate") or "")
        ev = _activity_event(
            _pt_when(ts, tzname),
            who,
            "customer" if incoming else "public",
            body,
            ts,
        )
        if ev:
            events.append(ev)
    for rec in feeds or []:
        if not isinstance(rec, dict):
            continue
        who_obj = rec.get("CreatedBy") if isinstance(rec.get("CreatedBy"), dict) else {}
        who = str(who_obj.get("Name") or rec.get("CreatedBy") or "").strip()
        body = str(rec.get("Body") or "").strip()
        typ = str(rec.get("Type") or "").strip()
        if not body or body.lower() == typ.lower():
            continue
        ts = str(rec.get("CreatedDate") or "")
        ev = _activity_event(_pt_when(ts, tzname), who, "internal", body, ts)
        if ev:
            events.append(ev)
    seen = set()
    uniq = []
    for ev in sorted(events, key=lambda row: _activity_epoch(str(row.get("_ts") or ""))):
        key = (ev.get("when"), ev.get("who"), ev.get("text"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append({k: ev[k] for k in ("when", "who", "kind", "text", "_ts") if ev.get(k) not in (None, "")})
    return uniq


def fill_case_peeks(
    data: dict, rows: list[dict] | None = None, *, wipe_summary: bool = False
) -> None:
    tzname = str(data.get("timezone") or "").strip()
    by_num = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("CaseNumber"):
            by_num[str(row["CaseNumber"])] = row
    items = []
    ids = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        if not re.search(
            r"needs (us|you) now|still watching|no action needed|follow-up due",
            title,
            re.I,
        ):
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict) or not (item.get("caseNumber") or item.get("caseUrl")):
                continue
            num = str(item.get("caseNumber") or "").strip()
            row = by_num.get(num) or {}
            cid = str(row.get("Id") or "")
            if not cid:
                match = re.search(r"/Case/(500[A-Za-z0-9]+)/", str(item.get("caseUrl") or ""))
                cid = match.group(1) if match else ""
            items.append((item, row, cid))
            if cid:
                ids.append(cid)
    comments_by = fetch_comments_by_parent(ids) if ids else {}
    emails_by = fetch_emails_by_parent(ids) if ids else {}
    feeds_by = fetch_feeds_by_parent(ids) if ids else {}
    for item, row, cid in items:
        comments = comments_by.get(cid) or []
        if not comments and cid:
            comments = fetch_case_comments(cid)
        chrono = merge_case_activity(
            comments, emails_by.get(cid) or [], feeds_by.get(cid) or [], tzname
        )
        problem = _one_line(str(row.get("Description") or ""), 280)
        if problem:
            item["problem"] = problem
        peek_obj = item.get("peek") if isinstance(item.get("peek"), dict) else {}
        summary = str(peek_obj.get("summary") or item.get("summary") or "").strip()
        if wipe_summary or _is_stub_peek(summary):
            summary = ""
            peek_obj.pop("summary", None)
            item.pop("summary", None)
        item["activity"] = chrono
        if wipe_summary or not peek_obj.get("chronology"):
            peek_obj.pop("chronology", None)
            item.pop("chronology", None)
        peek_obj["summary"] = summary
        item["peek"] = peek_obj
        if summary:
            item["summary"] = summary
        else:
            item.pop("summary", None)


def fill_needs_peek(
    data: dict, rows: list[dict] | None = None, *, wipe_summary: bool = False
) -> None:
    fill_case_peeks(data, rows, wipe_summary=wipe_summary)


def _is_need_now_row(row: dict) -> bool:
    return str(row.get("Status") or "").strip().lower() in NEED_NOW_STATUSES


def _case_item(row: dict, *, prefix: str) -> dict:
    num = str(row.get("CaseNumber") or "").strip()
    cid = str(row.get("Id") or "").strip()
    sev = _sev_short(str(row.get("Severity_Level__c") or ""))
    status = str(row.get("Status") or "").strip()
    item = {
        "id": f"{prefix}-{num}",
        "kind": "case",
        "label": _short_watch_label(num, str(row.get("Subject") or "")),
        "detail": " · ".join(part for part in (sev, status) if part),
        "status": status,
        "caseNumber": num,
        "caseUrl": ORGCS_CASE_URL.format(id=cid),
    }
    if prefix == "need":
        item["when"] = "5:00 PM"
    return item


def fill_need_now_from_status(data: dict, rows: list[dict]) -> set[str]:
    """Do not rank fire from Status. Empty until the model writes burning cases."""
    taken = set()
    need_sec = None
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and re.search(r"needs (us|you) now", str(sec.get("title") or ""), re.I):
            need_sec = sec
            break
    if need_sec is None:
        need_sec = {"title": "Needs us now", "open": True, "items": [], "tone": "now"}
        data.setdefault("sections", []).insert(0, need_sec)
    items = [it for it in (need_sec.get("items") or []) if isinstance(it, dict)]
    need_sec["items"] = items
    need_sec["tone"] = "now"
    need_sec["open"] = True
    for it in items:
        if it.get("caseNumber"):
            taken.add(str(it["caseNumber"]))
    return taken


def _ts_from_slack_permalink(url: str) -> str:
    match = re.search(r"/p(\d{10})(\d{1,6})", url or "")
    if not match:
        return ""
    return f"{match.group(1)}.{match.group(2)}"


def _thread_ts_from_slack_hit(chunk: str, url: str = "") -> str:
    """Parent timestamp. A mention reply's own ts does not open the rest of the thread."""
    for blob in (url or "", chunk or ""):
        match = re.search(r"thread_ts=(\d{10}\.\d+)", blob)
        if match:
            return match.group(1)
        match = re.search(r"(?:Thread_ts|thread_ts):\s*(\d{10}\.\d+)", blob)
        if match:
            return match.group(1)
    return ""


_SLACK_BOT_HIT = re.compile(
    r"\[BOT\]|\(ID:\s*USLACK\)|\bbot\b|notifications|storm|psbot|"
    r"career connect|google calendar|idm notifications",
    re.I,
)
_SLACK_CURSOR_RE = re.compile(
    r"(?:for the next page of results use cursor|next_cursor)\s*[`'\"]?\s*[:=]?\s*[`'\"]?([A-Za-z0-9=+\-/_]+)",
    re.I,
)
_CLIP_SPEAKER_RE = re.compile(
    r"(?m)^(?:\s*)([^:\n]{2,80}?)(?:\s*<[^>\n]+>)?:\s+\S"
)
_CLIP_SPEAKER_SKIP = re.compile(
    r"^(Channel|From|Time|Participants|Permalink|Text|Message_ts|Context|Reactions|Reacted|Timestamp|User)$",
    re.I,
)


def _slack_search_body(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            inner = parsed.get("results")
            if isinstance(inner, str) and inner.strip():
                return inner
            if isinstance(inner, (dict, list)):
                return json.dumps(inner)
    return text or ""


def _slack_next_cursor(text: str) -> str:
    body = _slack_search_body(text)
    match = _SLACK_CURSOR_RE.search(body) or _SLACK_CURSOR_RE.search(text or "")
    if not match:
        return ""
    return match.group(1).strip()


def _slack_hit_is_bot(item: dict, chunk: str = "") -> bool:
    who = str(item.get("from") or "")
    blob = " ".join(str(item.get(k) or "") for k in ("from", "channel", "peer"))
    if re.search(r"\[BOT\]", chunk or "") or re.search(r"\(ID:\s*USLACK\)", chunk or ""):
        return True
    return bool(_SLACK_BOT_HIT.search(who) or _SLACK_BOT_HIT.search(blob))


def _stamp_clip_last_human(item: dict, raw: str, data: dict | None) -> None:
    """Newest-first Slack history: first human speaker is who still has the last word."""
    if not isinstance(item, dict):
        return
    last = ""
    for match in _CLIP_SPEAKER_RE.finditer(raw or ""):
        who = re.sub(r"\s+", " ", match.group(1) or "").strip()
        who = re.sub(r"\s*\(ID:.*$", "", who).strip()
        if not who or _CLIP_SPEAKER_SKIP.match(who) or _SLACK_BOT_HIT.search(who):
            continue
        last = who
        break
    if not last:
        return
    item["lastHuman"] = last[:80]
    try:
        item["lastHumanIsMe"] = bool(_sanitize_mod()._is_self_person(last, data))
    except Exception:
        item["lastHumanIsMe"] = False


_REACT_TS_RE = re.compile(r"(?:Message_ts|message_ts|\bts)\s*[:=]\s*(\d{10}\.\d+)", re.I)
_REACT_LINE_RE = re.compile(r"(?im)^(?:\s*[-*]?\s*)(?:reactions?|reacted)\s*:\s*(.+)$")
_REACT_COLON_RE = re.compile(
    r":([a-z0-9_+-]+):\s*(?:\((\d+)\))?(?:\s*[-–:]?\s*([A-Za-z][^\n;|]{0,60}))?",
    re.I,
)
_REACT_BUDGET_LOCK = threading.Lock()
_REACT_BUDGET = 0


def _reset_react_budget(n: int) -> None:
    global _REACT_BUDGET
    with _REACT_BUDGET_LOCK:
        _REACT_BUDGET = max(0, int(n))


def _take_react_budget() -> bool:
    global _REACT_BUDGET
    with _REACT_BUDGET_LOCK:
        if _REACT_BUDGET <= 0:
            return False
        _REACT_BUDGET -= 1
        return True


def _slack_reaction_timestamps(history: str, fallback: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        ts = str(raw or "").strip()
        if not re.match(r"^\d{10}\.\d+$", ts) or ts in seen:
            return
        seen.add(ts)
        out.append(ts)

    add(fallback)
    for match in _REACT_TS_RE.finditer(history or ""):
        add(match.group(1))
        if len(out) >= 4:
            break
    if len(out) < 4:
        for match in re.finditer(r"/p(\d{10})(\d{1,6})", history or ""):
            add(f"{match.group(1)}.{match.group(2)}")
            if len(out) >= 4:
                break
    return out[:3]


def _unique_react_bits(bits: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for bit in bits:
        piece = re.sub(r"\s+", " ", str(bit or "")).strip()
        if not piece:
            continue
        key = piece.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(piece[:200])
        if len(out) >= 8:
            break
    return "; ".join(out)[:800]


def _compact_slack_reactions(text: str, *, history: bool = False) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if re.search(r"\bno reactions\b", raw, re.I) and not re.search(r":[a-z0-9_+-]+:", raw, re.I):
        return ""
    bits: list[str] = []
    for match in _REACT_LINE_RE.finditer(raw):
        line = re.sub(r"\s+", " ", match.group(1) or "").strip()
        if line:
            bits.append(line[:160])
    if history:
        return _unique_react_bits(bits)
    if not bits:
        for match in _REACT_COLON_RE.finditer(raw):
            name = match.group(1)
            count = match.group(2) or ""
            who = re.sub(r"\s+", " ", match.group(3) or "").strip()
            who = re.sub(r"\s*\(ID:.*$", "", who).strip()
            piece = name
            if count:
                piece += f"×{count}"
            if who:
                piece += " " + who[:40]
            bits.append(piece)
    if not bits:
        compact = re.sub(r"\s+", " ", raw)
        compact = re.sub(r"(?i)reactions? on message[:\s]*", "", compact).strip()
        if compact and not re.search(r"\bno reactions\b", compact, re.I):
            bits.append(compact[:200])
    return _unique_react_bits(bits)


def _fetch_slack_message_reactions(channel_id: str, ts: str) -> str:
    cid = str(channel_id or "").strip()
    stamp = str(ts or "").strip()
    if not cid or not stamp or "'" in cid or "'" in stamp:
        return ""
    if not _take_react_budget():
        return ""
    try:
        text = mcp_call_named(
            SLACK_MCP_NAMES,
            "slack_get_reactions",
            {"channel_id": cid, "message_ts": stamp},
            timeout=12,
        )
    except Exception:
        return ""
    return _compact_slack_reactions(str(text or ""))


def _stamp_slack_reactions(item: dict, channel_id: str, history: str) -> str:
    """Fetch emoji on leftover messages. The planner model still keep/drops."""
    inline = _compact_slack_reactions(history, history=True)
    fetched: list[str] = []
    for ts in _slack_reaction_timestamps(history, str(item.get("ts") or "")):
        blob = _fetch_slack_message_reactions(channel_id, ts)
        if blob:
            fetched.append(blob)
    return _unique_react_bits(fetched + ([inline] if inline else [])) or "(none)"


def _slack_user_in_hit(chunk: str, user_id: str) -> bool:
    """True when this engineer was @mentioned or wrote the message. Channel membership is not enough."""
    me = str(user_id or "").strip()
    if not me:
        return False
    if f"<@{me}" in (chunk or ""):
        return True
    return bool(re.search(r"\(ID:\s*" + re.escape(me) + r"\)", chunk or ""))


def _parse_slack_search(
    text: str, data: dict | None = None, *, keep_bots: bool = False, involved_user: str = ""
) -> list[dict]:
    rows = []
    body = _slack_search_body((text or "").replace("\\/", "/"))
    chunks = re.split(r"(?=### Result \d+)", body or "")
    for chunk in chunks:
        if "Channel:" not in chunk and "Permalink:" not in chunk:
            continue
        if not keep_bots and (re.search(r"\[BOT\]", chunk) or re.search(r"\(ID:\s*USLACK\)", chunk)):
            continue
        cid_m = re.search(r"Channel:\s*[^\n]*\(ID:\s*([A-Z0-9]+)\)", chunk)
        if not cid_m:
            cid_m = re.search(r"\(ID:\s*([DGC][A-Z0-9]+)\)", chunk)
        ch_m = re.search(r"Channel:\s*(.+)", chunk)
        frm_m = re.search(r"From:\s*(.+)", chunk)
        link_m = re.search(
            r"(https://[^\s)]+slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)]*)?)",
            chunk,
            re.I,
        )
        ts_m = re.search(r"(?:Message_ts|ts):\s*(\d{10}\.\d+)", chunk)
        body_m = re.search(r"Text:\s*(.*?)(?:\n---|\Z)", chunk, re.S)
        who = ""
        if frm_m:
            who = re.split(r"[<(]", frm_m.group(1), maxsplit=1)[0].strip()
            who = re.sub(r"\s*\(ID:.*$", "", who).strip()
        if not keep_bots and _SLACK_BOT_HIT.search(who):
            continue
        cid = cid_m.group(1) if cid_m else ""
        url = link_m.group(1) if link_m else ""
        ts = (ts_m.group(1) if ts_m else "") or _ts_from_slack_permalink(url)
        if cid.startswith("C") or cid.startswith("G"):
            if not ts:
                continue
        elif not cid.startswith("D"):
            continue
        channel = (ch_m.group(1).split("(ID")[0].strip() if ch_m else "").strip()
        raw_text = re.sub(r"\s+", " ", (body_m.group(1) if body_m else "")).strip()
        file_m = re.search(r"Files:\s*(.+)", chunk)
        if file_m:
            raw_text = (raw_text + " " + file_m.group(1)).strip()
        text_clip = raw_text[:400] if keep_bots else raw_text[:120]
        if cid.startswith("D") or re.search(r"\bDM\b", channel, re.I):
            label = "DM"
        else:
            label = f"{who or 'Slack'} — {channel}" if channel else (who or "Slack")
        if text_clip:
            label = f"{label} — {text_clip}"
        if not url and cid:
            url = f"https://salesforce.enterprise.slack.com/archives/{cid}"
            if ts:
                stamp = ts.replace(".", "")
                url = f"{url}/p{stamp}"
        ident = f"slack-{cid}" if cid.startswith("D") else f"slack-{cid}-{ts.replace('.', '')}"
        item = {
            "id": ident,
            "kind": "slack",
            "label": label[:160],
            "detail": "candidate",
            "from": who,
            "snippet": text_clip,
            "slackUrl": url,
            "channelId": cid,
            "channel": channel,
        }
        if ts:
            item["ts"] = ts
        reply_m = re.search(r"Reply count:\s*(\d+)", chunk, re.I)
        if reply_m:
            item["replyCount"] = int(reply_m.group(1))
        parent = _thread_ts_from_slack_hit(chunk, url)
        if parent:
            item["threadTs"] = parent
        if not keep_bots and _slack_hit_is_bot(item, chunk):
            continue
        if cid[:1] in "CG" and not re.search(r"\bDM\b", channel, re.I):
            involved = _slack_user_in_hit(chunk, involved_user) if involved_user else False
            bot_keep = keep_bots and bool(
                re.search(
                    r"15 Minute SLA Warning|Work Notifier|has an SLO due|Out of SLO|long running category",
                    chunk or "",
                    re.I,
                )
            )
            if not involved and not bot_keep:
                continue
        rows.append(item)
    return rows


def _slack_search_args(query: str, *, unread: bool = False, mentions: bool = False) -> dict:
    after = ""
    m = re.search(r"after:(\d{4}-\d{2}-\d{2})", query)
    if m:
        after = m.group(1)
    filters = query
    if unread:
        filters = f"is:unread {query}".strip()
    args = {
        "query": query,
        "filters": filters,
        "keywords": [],
        "natural_language_query": "",
        "include_bots": False,
        "include_context": False,
        "limit": 20,
        "sort": "timestamp",
        "sort_dir": "desc",
        "response_format": "detailed",
    }
    if not mentions:
        args["channel_types"] = "im,mpim"
    else:
        args["only_my_channels"] = True
    if after and "after:" not in filters:
        args["filters"] = f"{filters} after:{after}".strip()
    return args


def _owned_level1_cases() -> list[dict]:
    """Open owned Level 1 cases. File first, then a live OrgCS read."""
    rows: list[dict] = []
    try:
        obj = json.loads(OWNED_CASES_FILE.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and obj.get("fetched") is True:
            rows = [r for r in (obj.get("records") or []) if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        rows = []
    if not rows:
        try:
            rows = fetch_owned_open_cases()
        except Exception:
            rows = []
    out = []
    for row in rows:
        sev = str(row.get("Severity_Level__c") or "")
        if not re.search(r"Level\s*1\b", sev, re.I):
            continue
        num = re.sub(r"\D", "", str(row.get("CaseNumber") or ""))
        if len(num) < 6:
            continue
        out.append(row)
        if len(out) >= 8:
            break
    return out


def _case_account_name(case_number: str) -> str:
    if not case_number or "'" in case_number:
        return ""
    soql = (
        "SELECT Account.Name FROM Case WHERE CaseNumber = '%s' AND IsClosed = false LIMIT 1"
        % case_number
    )
    try:
        text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=20)
    except Exception:
        return ""
    for rec in parse_soql_records(text) or []:
        account = rec.get("Account") if isinstance(rec.get("Account"), dict) else {}
        name = str((account or {}).get("Name") or rec.get("Account.Name") or "").strip()
        if name and len(name) >= 3:
            return name[:80]
    return ""


def _sev1_tokens(case_number: str, account: str = "", subject: str = "") -> list[str]:
    tokens = []
    for raw in (case_number, account):
        text = str(raw or "").strip()
        if len(text) >= 4 and text not in tokens:
            tokens.append(text)
    blob = str(subject or "")
    for match in re.findall(r"00D[A-Za-z0-9]{12,15}", blob):
        if match not in tokens:
            tokens.append(match)
    for match in re.findall(r"\b[A-Z]{2,6}\d{2,5}\b", blob):
        if match not in tokens:
            tokens.append(match)
    return tokens[:6]


def _sev1_hit_score(item: dict, tokens: list[str]) -> int:
    name = str(item.get("channel") or "")
    snippet = str(item.get("snippet") or "")
    blob = f"{name} {snippet}"
    score = 0
    if re.search(r"sev\s*-?\s*1", name, re.I):
        score += 5
    if re.search(r"\bicc\b|incident|throttl", name, re.I):
        score += 4
    for token in tokens:
        if len(token) < 4:
            continue
        if token.lower() in name.lower():
            score += 4
        elif token.lower() in snippet.lower():
            score += 2
    if re.search(r"sev\s*-?\s*1|\bicc\b|incident|throttl", blob, re.I) and score < 4:
        score += 3
    return score


def _parse_channel_catalog(text: str) -> list[dict]:
    rows = []
    seen = set()
    for match in re.finditer(
        r"#([A-Za-z0-9][A-Za-z0-9._-]{0,80})\s+\(([CG][A-Z0-9]+)\)",
        text or "",
    ):
        cid = match.group(2)
        if cid in seen:
            continue
        seen.add(cid)
        rows.append(
            {
                "channel": match.group(1),
                "channelId": cid,
                "snippet": "",
                "slackUrl": f"https://salesforce.enterprise.slack.com/archives/{cid}",
            }
        )
    return rows


def _search_sev1_channel_by_name(case_number: str) -> dict | None:
    """Channel names are sev1-<case>-<account>. Message search does not find those."""
    if not case_number:
        return None
    queries = (f"sev1-{case_number}", case_number)
    for query in queries:
        try:
            text = mcp_call_named(
                SLACK_MCP_NAMES,
                "slack_search_channels",
                {
                    "query": query,
                    "keywords": [query],
                    "natural_language_query": "",
                    "channel_types": "public_channel,private_channel",
                    "limit": 10,
                    "response_format": "concise",
                },
                timeout=30,
            )
        except Exception:
            continue
        for item in _parse_channel_catalog(str(text or "")):
            name = str(item.get("channel") or "").lower()
            if case_number in name and name.startswith("sev"):
                return item
    return None


def _search_sev1_channel(case_number: str, account: str = "", subject: str = "") -> dict | None:
    """Best #sev1 / ICC channel for this Level 1 case. Not a random mention."""
    named = _search_sev1_channel_by_name(case_number)
    if named:
        return named
    tokens = _sev1_tokens(case_number, account, subject)
    queries = list(tokens) or [case_number]
    best = None
    best_score = 0
    for query in queries:
        args = _slack_search_args(query, mentions=True)
        args["query"] = query
        args["filters"] = query
        args["limit"] = 15
        try:
            text = mcp_call_named(
                SLACK_MCP_NAMES, "slack_search_public_and_private", args, timeout=45
            )
        except Exception:
            continue
        hits = _parse_slack_search(text or "")
        if not hits:
            hits = []
            for match in re.finditer(
                r"Channel:\s*([^(\n]+?)\s*\(ID:\s*([CG][A-Z0-9]+)\)",
                text or "",
            ):
                hits.append(
                    {
                        "channel": match.group(1).strip().lstrip("#"),
                        "channelId": match.group(2),
                        "snippet": "",
                    }
                )
        for item in hits:
            cid = str(item.get("channelId") or "")
            if cid[:1] not in "CG":
                continue
            score = _sev1_hit_score(item, tokens)
            if score < 4:
                continue
            if score > best_score:
                best = item
                best_score = score
        if best_score >= 5:
            break
    return best


def attach_sev1_channels(found: list, seen: dict) -> int:
    """Open the incident channel for each owned Level 1 case."""
    cases = _owned_level1_cases()
    if not cases:
        return 0
    added = 0
    for row in cases:
        num = re.sub(r"\D", "", str(row.get("CaseNumber") or ""))
        account = _case_account_name(num)
        hit = _search_sev1_channel(num, account, str(row.get("Subject") or ""))
        if not hit:
            continue
        cid = str(hit.get("channelId") or "")
        if not cid or f"sev1:{cid}" in seen:
            continue
        if any(str(item.get("channelId") or "") == cid and item.get("sev1Channel") for item in found):
            continue
        name = str(hit.get("channel") or "").strip().lstrip("#")
        pretty = f"#{name}" if name else "Sev-1 channel"
        item = {
            "id": f"slack-{cid}",
            "kind": "slack",
            "label": f"#{num} — {pretty}"[:160],
            "detail": "Sev-1 channel",
            "channel": pretty,
            "channelId": cid,
            "slackUrl": hit.get("slackUrl") or "",
            "sev1Case": num,
            "sev1Channel": True,
            "unread": False,
        }
        if hit.get("ts"):
            item["ts"] = hit["ts"]
        ensure_slack_link(item)
        seen[f"sev1:{cid}"] = item
        found.append(item)
        added += 1
    append_plan_step(
        kind="log",
        label=(
            f"Sev-1 channels · {added} of {len(cases)} Level 1 "
            f"case{'s' if len(cases) != 1 else ''} matched"
        ),
    )
    return added


def _owned_case_rows() -> list[dict]:
    rows: list[dict] = []
    try:
        obj = json.loads(OWNED_CASES_FILE.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and obj.get("fetched") is True:
            rows = [r for r in (obj.get("records") or []) if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        rows = []
    if not rows:
        try:
            rows = fetch_owned_open_cases()
        except Exception:
            rows = []
    return rows[:40]


def _swarm_thread_parts(rec: dict) -> tuple[str, str, str, str]:
    room = rec.get("CollaborationRoom") if isinstance(rec.get("CollaborationRoom"), dict) else {}
    channel_id = str(room.get("PlatformKey") or "").strip()
    channel = str(room.get("Name") or "").strip().lstrip("#")
    ts = str(rec.get("MessageKey") or "").strip()
    url = str(rec.get("CollaborationUrl") or "").strip()
    if not ts:
        match = re.search(r"thread/[CG][A-Z0-9]+-(\d+\.\d+)", url)
        if match:
            ts = match.group(1)
    if not channel_id:
        match = re.search(r"/client/[^/]+/([CG][A-Z0-9]+)/", url)
        if match:
            channel_id = match.group(1)
    return channel_id, channel, ts, url


def attach_swarm_threads(found: list, seen: dict) -> int:
    """Open the swarm thread linked on the case's Swarm record. Do not search Slack for the case number."""
    rows = _owned_case_rows()
    id_to_num = {}
    for row in rows:
        cid = str(row.get("Id") or "")
        num = re.sub(r"\D", "", str(row.get("CaseNumber") or ""))
        if cid.startswith("500") and len(num) >= 6:
            id_to_num[cid] = num
    if not id_to_num:
        return 0
    ids = list(id_to_num)
    records = []
    for i in range(0, len(ids), 10):
        chunk = ids[i : i + 10]
        quoted = ",".join("'" + cid.replace("'", "") + "'" for cid in chunk)
        soql = (
            "SELECT RelatedRecordId, Status, CollaborationUrl, MessageKey, "
            "CollaborationRoom.Name, CollaborationRoom.PlatformKey "
            f"FROM Swarm WHERE RelatedRecordId IN ({quoted}) AND Status != 'Closed' "
            "ORDER BY CreatedDate DESC LIMIT 50"
        )
        try:
            text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=30)
        except Exception:
            continue
        records.extend(parse_soql_records(text) or [])
    added = 0
    seen_case: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        num = id_to_num.get(str(rec.get("RelatedRecordId") or ""))
        if not num or num in seen_case:
            continue
        channel_id, channel, ts, url = _swarm_thread_parts(rec)
        if channel_id[:1] not in "CG" or not ts:
            continue
        key = f"swarm:{num}:{channel_id}:{ts}"
        if key in seen:
            continue
        pretty = f"#{channel}" if channel else "Swarm"
        item = {
            "id": f"slack-swarm-{num}",
            "kind": "slack",
            "label": f"#{num} — {pretty}"[:160],
            "detail": "Swarm thread",
            "channel": pretty,
            "channelId": channel_id,
            "slackUrl": url,
            "swarmCase": num,
            "swarmThread": True,
            "unread": False,
            "ts": ts,
            "threadTs": ts,
        }
        ensure_slack_link(item)
        seen[key] = item
        found.append(item)
        seen_case.add(num)
        added += 1
    append_plan_step(
        kind="log",
        label=f"Swarm threads · {added} of {len(id_to_num)} owned case{'s' if len(id_to_num) != 1 else ''} linked",
    )
    return added


def inbox_lookback_days(tzname: str = "") -> int:
    """Monday catches Friday through the weekend. Later in the week, two days is enough."""
    name = str(tzname or "").strip()
    try:
        tz = ZoneInfo(name) if name else timezone.utc
    except Exception:
        tz = timezone.utc
    if datetime.now(tz).weekday() == 0:
        return 4
    return 2


def fill_slack_leftovers(data: dict) -> None:
    append_plan_step(kind="log", label="Opening leftover Slack DMs and mention threads")
    apply_identity_cache(data)
    lookback = inbox_lookback_days(str(data.get("timezone") or ""))
    after = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    me = str(data.get("slackUserId") or "U03PRB7ADAL").strip() or "U03PRB7ADAL"
    unread_queries = (
        _slack_search_args(f"is:dm after:{after}", unread=True),
        _slack_search_args(f"<@{me}> after:{after}", unread=True, mentions=True),
        _slack_search_args(f"is:thread <@{me}> after:{after}", unread=True, mentions=True),
    )
    def gus_notice_query(name: str) -> dict:
        """GUS Bot (Work Notifier) and GUS Chatter both post in a DM."""
        args = _slack_search_args(f"after:{after}")
        args["include_bots"] = True
        args["channel_types"] = "im"
        args["keywords"] = [f'"{name}"']
        args["filters"] = f"is:dm after:{after}"
        args["query"] = name
        args["natural_language_query"] = ""
        args["_keep_bots"] = True
        args["_gus_bot"] = True
        return args

    gus_queries = (
        gus_notice_query("GUS Bot"),
        gus_notice_query("Work Notifier"),
        gus_notice_query("GUS Chatter"),
    )
    gus_work = gus_notice_query("W-")
    gus_work["keywords"] = ["W-"]
    gus_work["_gus_bot"] = False
    case_sla = _slack_search_args(f"\"15 Minute SLA Warning\" after:{after}", mentions=True)
    case_sla["include_bots"] = True
    case_sla["query"] = f"\"15 Minute SLA Warning\" after:{after}"
    case_sla["_keep_bots"] = True
    psbot = _slack_search_args(f"after:{after}")
    psbot["include_bots"] = True
    psbot["channel_types"] = "mpim"
    psbot["only_my_channels"] = True
    psbot["query"] = f"after:{after}"
    psbot["filters"] = f"from:<@U01SMM74NHM> after:{after}"
    psbot["_keep_bots"] = True
    authored = _slack_search_args(f"from:<@{me}> after:{after}", mentions=True)
    authored["_authored_threads"] = True
    all_queries = (
        _slack_search_args(f"is:dm after:{after}"),
        _slack_search_args(f"<@{me}> after:{after}", mentions=True),
        _slack_search_args(f"is:thread <@{me}> after:{after}", mentions=True),
        *gus_queries,
        gus_work,
        case_sla,
        psbot,
        authored,
    )
    found: list[dict] = []
    seen: dict[str, dict] = {}
    sanit = _sanitize_mod()
    searched = False
    last_err = ""
    data["slackFetchOk"] = False
    data["slackFetchError"] = ""

    def add_hits(text: str, unread: bool, *, keep_bots: bool = False, authored_threads: bool = False, gus_bot: bool = False) -> None:
        for item in _parse_slack_search(
            text, data, keep_bots=keep_bots, involved_user="" if keep_bots else me
        ):
            notice = " ".join(
                str(item.get(key) or "") for key in ("from", "label", "snippet", "channel")
            )
            if gus_bot or re.search(r"gus chatter|gus bot|work notifier", notice, re.I):
                item["gusBot"] = True
            if authored_threads:
                cid0 = str(item.get("channelId") or "")
                channel0 = str(item.get("channel") or "")
                if cid0.startswith("D") or re.search(r"\bDM\b", channel0, re.I):
                    continue
            if not item.get("gusBot"):
                sanit.stamp_slack_dm_label(item, data)
            cid = str(item.get("channelId") or "")
            ts = str(item.get("threadTs") or item.get("ts") or "")
            if cid.startswith("D"):
                key = cid
            elif cid and ts:
                key = f"{cid}:{ts}"
            else:
                key = str(item.get("slackUrl") or item.get("id") or "")
            if not key:
                continue
            if key in seen:
                if gus_bot:
                    seen[key]["gusBot"] = True
                if unread:
                    seen[key]["unread"] = True
                continue
            item["unread"] = bool(unread)
            seen[key] = item
            found.append(item)

    def run_queries(queries, unread: bool) -> None:
        nonlocal searched, last_err
        for args in queries:
            cursor = ""
            keep_bots = bool(args.get("_keep_bots"))
            authored_threads = bool(args.get("_authored_threads"))
            gus_bot_query = bool(args.get("_gus_bot"))
            for _ in range(5):
                payload = {k: v for k, v in args.items() if not str(k).startswith("_")}
                if cursor:
                    payload["cursor"] = cursor
                try:
                    text = mcp_call_named(
                        SLACK_MCP_NAMES, "slack_search_public_and_private", payload, timeout=45
                    )
                    searched = True
                except Exception as exc:
                    last_err = clip(str(exc), 180)
                    break
                add_hits(text, unread, keep_bots=keep_bots, authored_threads=authored_threads, gus_bot=gus_bot_query)
                if "0 results" in (text or "").lower() and "### Result" not in _slack_search_body(text):
                    break
                cursor = _slack_next_cursor(text)
                if not cursor:
                    break

    run_queries(unread_queries, True)
    run_queries(all_queries, False)
    if attach_swarm_threads(found, seen):
        searched = True
    if searched:
        attach_sev1_channels(found, seen)
    data["slackCandidates"] = found
    data["slackFetchOk"] = bool(searched)
    data["slackFetchError"] = last_err if not searched else ""
    if not searched:
        append_plan_step(
            kind="log",
            label="Slack leftover fetch failed"
            + (f" · {last_err}" if last_err else "")
            + " — will not stamp Slack — clear",
        )
        return
    slack_sec = None
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and re.match(r"slack\b", str(sec.get("title") or ""), re.I):
            slack_sec = sec
            break
    if slack_sec is None:
        slack_sec = {"title": "Slack", "open": True, "groups": [], "items": []}
        data.setdefault("sections", []).insert(1, slack_sec)
    n_open = clip_leftover_slack(found, data)
    for item in found:
        sanit.stamp_slack_dm_label(item, data)
    try:
        sanit.stamp_items_done_from_ledger(found, load_done_ledger(), data)
    except Exception:
        pass
    if n_open:
        append_plan_step(kind="log", label=f"Opened {n_open} Slack leftovers into clips (planner will not re-read)")
    else:
        append_plan_step(
            kind="log",
            label=f"Slack leftover fetch ok · {len(found)} human DM/thread candidate{'s' if len(found) != 1 else ''}",
        )
    slack_sec["groups"] = []
    slack_sec["items"] = []
    slack_sec["empty"] = "Slack — clear"


def _inbox_plain(text: str, n: int = INBOX_CLIP_N) -> str:
    raw = re.sub(r"(?i)<[^>]+>", " ", text or "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if n and len(raw) > n:
        raw = raw[: n - 1].rstrip() + "…"
    return raw


def _gmail_body(block: str) -> str:
    raw = block or ""
    m = re.search(r"(?im)^(?:Body|Plain text|Plaintext|Snippet)\s*:\s*", raw)
    if m:
        raw = raw[m.end() :]
        raw = re.split(r"(?m)^(?:Message ID:|\s*---\s*$)", raw)[0]
    return _inbox_plain(raw, INBOX_CLIP_N)


def ensure_slack_link(row: dict) -> str:
    """Permalink for one leftover. Uses the row link, or channel id plus timestamp."""
    if not isinstance(row, dict):
        return ""
    host = "https://salesforce.enterprise.slack.com"
    for key in ("slackUrl", "slackPermalink", "permalink"):
        url = str(row.get(key) or "").strip()
        if "slack.com" in url.lower() or url.lower().startswith("slack://"):
            row["slackUrl"] = url
            return url
    ch = str(row.get("channelId") or row.get("slackChannel") or row.get("channel") or "").strip()
    ts = str(row.get("ts") or row.get("threadTs") or row.get("message_ts") or "").strip()
    ident = str(row.get("id") or "").strip()
    match = re.match(r"^(?:slack-)?([CGD][A-Z0-9]{8,})(?:-(\d{11,16}))?$", ident, re.I)
    if match:
        if not re.match(r"^[CGD][A-Z0-9]{8,}$", ch, re.I):
            ch = match.group(1)
        compact = match.group(2) or ""
        if compact and not re.match(r"^\d{10}\.\d+$", ts) and len(compact) > 10:
            ts = f"{compact[:10]}.{compact[10:]}"
    if not re.match(r"^[CGD][A-Z0-9]{8,}$", ch, re.I):
        return ""
    row["channelId"] = ch
    if re.match(r"^\d{10}\.\d+$", ts):
        row["ts"] = ts
        url = f"{host}/archives/{ch}/p{ts.replace('.', '')}"
    else:
        url = f"{host}/archives/{ch}"
    row["slackUrl"] = url
    return url


def _inbox_still_open(row: dict, keys: set | None = None) -> bool:
    """A Slack or Mail row marked Done stays out of the next run."""
    if not isinstance(row, dict) or row.get("done") is True:
        return False
    if not keys:
        return True
    try:
        return not _sanitize_mod().inbox_row_is_done(row, keys)
    except Exception:
        return True


def _done_inbox_keys() -> set:
    try:
        return _sanitize_mod()._ledger_keys(load_done_ledger())
    except Exception:
        return set()


def write_planner_inbox_txt(slack: list, mail: list) -> None:
    done_keys = _done_inbox_keys()
    slack_rows = [
        row
        for row in (slack or [])
        if not _sanitize_mod().is_gus_notice(row) and _inbox_still_open(row, done_keys)
    ]
    mail_rows = [
        row
        for row in (mail or [])
        if not _sanitize_mod()._is_gus_notice_mail(row) and _inbox_still_open(row, done_keys)
    ]

    def slack_block(row: dict, n: int) -> str:
        lines = [f"## slack {row.get('id') or ''}", f"- label: {row.get('label') or ''}", f"- peer: {row.get('peer') or ''}"]
        link = ensure_slack_link(row)
        if link:
            lines.append(f"- slackUrl: {link}")
        if row.get("channel"):
            lines.append(f"- channel: {row.get('channel')}")
        if row.get("sev1Case"):
            lines.append(f"- sev1Case: {row.get('sev1Case')}")
        if row.get("swarmCase"):
            lines.append(f"- swarmCase: {row.get('swarmCase')}")
        if row.get("channelId"):
            lines.append(f"- channelId: {row.get('channelId')}")
        if row.get("ts"):
            lines.append(f"- ts: {row.get('ts')}")
        lines.append(f"- unread: {bool(row.get('unread'))}")
        if "lastHumanIsMe" in row:
            lines.append(f"- lastHumanIsMe: {bool(row.get('lastHumanIsMe'))}")
        if row.get("lastHuman"):
            lines.append(f"- lastHuman: {row.get('lastHuman')}")
        if "reactions" in row:
            lines.append(f"- reactions: {_inbox_plain(str(row.get('reactions') or '(none)'), 240)}")
        lines.append(f"- clip: {_inbox_plain(row.get('openedClip') or '', n) or '(not opened)'}")
        return "\n".join(lines)

    def mail_block(row: dict, n: int) -> str:
        lines = [
            f"## mail {row.get('id') or ''}",
            f"- from: {row.get('from') or ''}",
            f"- subject: {row.get('label') or ''}",
        ]
        if row.get("mailUrl"):
            lines.append(f"- mailUrl: {row.get('mailUrl')}")
        elif row.get("messageId") or row.get("gmailId"):
            lines.append(f"- messageId: {row.get('messageId') or row.get('gmailId')}")
        lines.append(f"- clip: {_inbox_plain(row.get('openedClip') or row.get('snippet') or '', n) or '(no body)'}")
        return "\n".join(lines)

    def pack(slack_n: int, mail_n: int, slack_keep: int) -> str:
        use_slack = slack_rows if slack_keep < 0 else slack_rows[:slack_keep]
        parts = [mail_block(row, mail_n) for row in mail_rows]
        parts.extend(slack_block(row, slack_n) for row in use_slack)
        omitted = len(slack_rows) - len(use_slack)
        if omitted > 0:
            parts.append(
                f"- slack clips shown: {len(use_slack)} of {len(slack_rows)}. "
                "Do not stamp Slack — clear."
            )
        if mail_rows:
            parts.insert(0, f"- mail clips: {len(mail_rows)}. Classify every ## mail block.")
        return "\n\n".join(part for part in parts if part).strip()

    blob = pack(0, 0, -1)
    try:
        PLANNER_INBOX_TXT.write_text(blob + ("\n" if blob else ""), encoding="utf-8")
    except OSError:
        pass


def clip_leftover_slack(found: list, data: dict | None = None) -> int:
    """Open every leftover DM and mention thread. Planner still keep/drops from the clips."""
    jobs: list[tuple[dict, str, dict]] = []
    for item in found or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("channelId") or "")
        ts = str(item.get("threadTs") or item.get("ts") or "")
        if item.get("sev1Channel") and cid[:1] in "CG":
            jobs.append(
                (item, "slack_read_channel", {"channel_id": cid, "response_format": "detailed", "limit": 40})
            )
        elif cid.startswith("D"):
            jobs.append(
                (item, "slack_read_channel", {"channel_id": cid, "response_format": "detailed", "limit": 40})
            )
        elif cid[:1] in "CG" and ts:
            jobs.append(
                (
                    item,
                    "slack_read_thread",
                    {
                        "channel_id": cid,
                        "message_ts": ts,
                        "response_format": "detailed",
                        "limit": 80,
                    },
                )
            )
    if not jobs:
        return 0
    _reset_react_budget(min(80, len(jobs) * 2))

    def one(job: tuple[dict, str, dict]) -> int:
        item, tool, args = job
        clip_parts: list[str] = []
        cursor = ""
        for _ in range(3):
            payload = dict(args)
            if cursor:
                payload["cursor"] = cursor
            try:
                text = mcp_call_named(SLACK_MCP_NAMES, tool, payload, timeout=25)
            except Exception:
                break
            raw = str(text or "").strip()
            if raw:
                clip_parts.append(raw)
            cursor = _slack_next_cursor(text)
            if not cursor:
                break
        joined = "\n".join(clip_parts)
        clip_txt = _inbox_plain(joined, INBOX_CLIP_N)
        if not clip_txt:
            return 0
        item["openedClip"] = clip_txt
        item["opened"] = True
        cid = str(item.get("channelId") or args.get("channel_id") or "")
        item["reactions"] = _stamp_slack_reactions(item, cid, joined)
        if not item.get("channelId"):
            item["channelId"] = cid
        if not item.get("ts") and args.get("message_ts"):
            item["ts"] = str(args.get("message_ts") or "")
        ensure_slack_link(item)
        _stamp_clip_last_human(item, joined, data)
        try:
            _sanitize_mod().stamp_slack_dm_label(item, data)
        except Exception:
            pass
        return 1

    opened = 0
    workers = min(6, len(jobs))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n in pool.map(one, jobs):
            opened += n
    return opened


def fill_mail_leftovers(data: dict) -> None:
    data["mailFetchOk"] = False
    data["mailFetchError"] = ""
    ids = []
    token = None
    searched = False
    last_err = ""
    for _ in range(6):
        lookback = inbox_lookback_days(str(data.get("timezone") or ""))
        args = {"query": f"is:unread in:inbox newer_than:{lookback}d", "page_size": 50}
        if token:
            args["page_token"] = token
        try:
            text = mcp_call("search_gmail_messages", args)
            searched = True
        except Exception as exc:
            last_err = clip(str(exc), 180)
            break
        ids.extend(re.findall(r"Message ID:\s*(\S+)", text or ""))
        nxt = re.search(r"(?:next_page_token|page_token):\s*(\S+)", text or "", re.I)
        token = nxt.group(1) if nxt else None
        if not token:
            break
    ids = list(dict.fromkeys(ids))
    rows = []
    for i in range(0, len(ids), 25):
        chunk = ids[i : i + 25]
        try:
            text = mcp_call(
                "get_gmail_messages_content_batch",
                {"format": "full", "message_ids": chunk},
            )
        except Exception:
            continue
        blocks = re.split(r"(?=Message ID:)", text or "")
        for block in blocks:
            if "Message ID:" not in block:
                continue
            mid_m = re.search(r"Message ID:\s*(\S+)", block)
            sub_m = re.search(r"Subject:\s*(.+)", block)
            frm_m = re.search(r"From:\s*(.+)", block)
            link_m = re.search(r"Web Link:\s*(\S+)", block)
            subject = (sub_m.group(1).strip() if sub_m else "")
            frm = (frm_m.group(1).strip() if frm_m else "")
            if MAIL_DROP_RE.search(f"{subject} {frm}"):
                continue
            mid = mid_m.group(1) if mid_m else ""
            body = _gmail_body(block)
            rows.append(
                {
                    "id": f"mail-{mid[:16]}",
                    "kind": "mail",
                    "label": subject or "Unread mail",
                    "detail": frm.split("<")[0].strip(" \"") or "candidate",
                    "from": frm,
                    "snippet": body[:240] if body else subject,
                    "openedClip": body,
                    "opened": bool(body),
                    "messageId": mid,
                    "mailUrl": (link_m.group(1) if link_m else f"https://mail.google.com/mail/u/0/#all/{mid}"),
                }
            )
    data["mailCandidates"] = rows
    data["mailFetchOk"] = bool(searched)
    data["mailFetchError"] = last_err if not searched else ""
    if not searched:
        append_plan_step(
            kind="log",
            label="Mail leftover fetch failed"
            + (f" · {last_err}" if last_err else "")
            + " — will not stamp Mail — clear",
        )
        return
    if not rows:
        append_plan_step(kind="log", label="Mail leftover fetch ok · 0 unread")
        return
    mail_sec = None
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and re.match(r"(mail|email|gmail)\b", str(sec.get("title") or ""), re.I):
            mail_sec = sec
            break
    if mail_sec is None:
        mail_sec = {"title": "Mail", "open": True, "groups": [], "items": []}
        data.setdefault("sections", []).append(mail_sec)
    try:
        _sanitize_mod().stamp_items_done_from_ledger(rows, load_done_ledger(), data)
    except Exception:
        pass
    mail_sec["groups"] = []
    mail_sec["items"] = []
    mail_sec["empty"] = "Mail — clear"
    n_open = sum(1 for row in rows if row.get("openedClip"))
    if n_open:
        append_plan_step(kind="log", label=f"Opened {n_open} unread mail bodies into clips (planner will not re-read)")


def fill_live_details(data: dict, rows: list[dict] | None = None) -> dict:
    fill_need_now_from_status(data, rows or [])
    fill_needs_peek(data, rows)
    try:
        fill_slack_leftovers(data)
    except Exception as exc:
        data["slackFetchOk"] = False
        data["slackFetchError"] = clip(str(exc), 180)
        append_plan_step(kind="log", label="Slack leftover fetch failed · " + data["slackFetchError"])
    try:
        fill_mail_leftovers(data)
    except Exception as exc:
        data["mailFetchOk"] = False
        data["mailFetchError"] = clip(str(exc), 180)
        append_plan_step(kind="log", label="Mail leftover fetch failed · " + data["mailFetchError"])
    try:
        sanit = _sanitize_mod()
        sanit.apply_persisted_done(data, None, load_done_ledger())
    except Exception:
        pass
    return data


def _peek_digest_blob() -> str:
    try:
        blob = PLANNER_DIGEST_FILE.read_text(encoding="utf-8")
    except OSError:
        blob = ""
    if not blob.strip():
        try:
            blob = PLANNER_CARDS_FILE.read_text(encoding="utf-8")
        except OSError:
            blob = ""
    return blob[:48_000]


def _peek_cards_blob() -> str:
    return _peek_digest_blob()


def case_ids_from_evidence(evidence: dict) -> list[str]:
    ids = []
    seen = set()
    for row in evidence.get("cases") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if not cid.startswith("500") or cid in seen:
            continue
        seen.add(cid)
        ids.append(cid)
        if len(ids) >= 40:
            break
    return ids


def quoted_parent_ids(ids: list[str]) -> str:
    return ",".join(f"'{cid}'" for cid in ids if cid.startswith("500"))


def _gather_plan_clock(seeded: dict, meetings: list) -> dict:
    """Minutes still free from now through logout, minus Google meetings."""
    try:
        sanit = _sanitize_mod()
        start, end = sanit._shift_window_today(seeded)
        now_floor = sanit._plan_now_wall(seeded, start, end)
        work_lo = start if now_floor <= start else now_floor
        occ = sanit._occupied_ranges(meetings)
        gaps = sanit._gap_list(work_lo, end, occ)
        free = int(sum((g[1] - g[0]).total_seconds() for g in gaps) // 60)
        return {
            "nowStamp": sanit._to_stamp(now_floor),
            "planFrom": sanit._to_stamp(work_lo),
            "planUntil": sanit._to_stamp(end),
            "freeMinutes": max(0, free),
        }
    except Exception:
        return {"freeMinutes": 0}


def evidence_from_seed(seeded: dict) -> dict:
    """Shape the model cannot publish as the page."""
    cases = []
    seen_cases: set[str] = set()
    for sec in seeded.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        if not re.search(
            r"needs (us|you) now|follow-up due|still watching|no action needed",
            title,
            re.I,
        ):
            continue
        for it in sec.get("items") or []:
            if not isinstance(it, dict) or not it.get("caseNumber"):
                continue
            cid_m = re.search(r"/Case/(500[A-Za-z0-9]+)/", str(it.get("caseUrl") or ""))
            problem = re.sub(r"\s+", " ", str(it.get("problem") or "")).strip()
            row = {
                "caseNumber": it.get("caseNumber"),
                "id": cid_m.group(1) if cid_m else "",
                "label": str(it.get("label") or "")[:90],
                "detail": str(it.get("detail") or "")[:80],
                "status": str(it.get("status") or "")[:40],
                "caseUrl": it.get("caseUrl"),
                "problem": problem[:120],
            }
            if it.get("promisedClose") is True:
                row["promisedClose"] = True
            if it.get("done") is True:
                row["done"] = True
            on = str(it.get("promisedCloseOn") or it.get("closeOn") or "").strip()
            if on:
                row["promisedCloseOn"] = on[:16]
            num = str(it.get("caseNumber") or "").strip()
            ir_row = (seeded.get("_irByNum") or {}).get(num) or {}
            if ir_row:
                row["irStatus"] = str(ir_row.get("SE_Initial_Response_Status__c") or "")[:40]
                row["irTarget"] = str(ir_row.get("SE_Target_Response__c") or "")[:40]
                row["irPending"] = ir_is_pending(ir_row)
            cases.append(row)
            if num:
                seen_cases.add(num)
    for rec in _sidecar_owned_cases():
        num = str(rec.get("CaseNumber") or "").strip()
        cid = str(rec.get("Id") or "").strip()
        if not num or num in seen_cases:
            continue
        ir_row = (seeded.get("_irByNum") or {}).get(num) or rec
        cases.append(
            {
                "caseNumber": num,
                "id": cid,
                "label": clip(str(rec.get("Subject") or ""), 90),
                "status": str(rec.get("Status") or "")[:40],
                "caseUrl": ORGCS_CASE_URL.format(id=cid) if cid.startswith("500") else "",
                "irStatus": str(ir_row.get("SE_Initial_Response_Status__c") or "")[:40],
                "irTarget": str(ir_row.get("SE_Target_Response__c") or "")[:40],
                "irPending": ir_is_pending(ir_row),
            }
        )
        seen_cases.add(num)
    meetings = []
    cached = seeded.get("_calendarEvents")
    if seeded.get("calendarFetchOk") is True and isinstance(cached, list):
        meetings = [ev for ev in cached if isinstance(ev, dict)][:40]
    elif seeded.get("_calendarFetchTried"):
        meetings = []
    else:
        seeded["_calendarFetchTried"] = True
        try:
            tzname = str(seeded.get("timezone") or "").strip()
            got = fetch_primary_events(
                tzname,
                shift_start=str(seeded.get("shiftStart") or "") or None,
                shift_end=str(seeded.get("shiftEnd") or "") or None,
            )
            seeded["calendarFetchOk"] = True
            seeded.pop("calendarFetchError", None)
            for ev in got.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                lab = str(ev.get("label") or "").strip()
                start = str(ev.get("startStamp") or "").strip()
                if not lab or not start:
                    continue
                meetings.append(
                    {
                        "label": lab[:80],
                        "startStamp": start[:32],
                        "endStamp": str(ev.get("endStamp") or "")[:32],
                        "eventId": str(ev.get("eventId") or "")[:80],
                    }
                )
                if len(meetings) >= 40:
                    break
            seeded["_calendarEvents"] = list(meetings)
            append_plan_step(kind="log", label=f"Primary calendar fetch ok · {len(meetings)} events")
        except Exception as exc:
            seeded["calendarFetchOk"] = False
            seeded["calendarFetchError"] = clip(str(exc), 180)
            meetings = []
            append_plan_step(kind="log", label="Primary calendar fetch failed · " + seeded["calendarFetchError"])
    clock = _gather_plan_clock(seeded, meetings)
    forget_prior_fetches(seeded)
    try:
        fill_slack_leftovers(seeded)
    except Exception as exc:
        seeded["slackFetchOk"] = False
        seeded["slackFetchError"] = clip(str(exc), 180)
        seeded["slackCandidates"] = []
        append_plan_step(kind="log", label="Slack leftover fetch failed · " + seeded["slackFetchError"])
    try:
        fill_mail_leftovers(seeded)
    except Exception as exc:
        seeded["mailFetchOk"] = False
        seeded["mailFetchError"] = clip(str(exc), 180)
        seeded["mailCandidates"] = []
        append_plan_step(kind="log", label="Mail leftover fetch failed · " + seeded["mailFetchError"])
    def _slim_cand(row: dict, keys: tuple[str, ...]) -> dict:
        out = {}
        for k in keys:
            if k not in row:
                continue
            val = row[k]
            if k == "unread" or k == "opened" or k == "done" or k == "lastHumanIsMe":
                out[k] = bool(val)
                continue
            if val is None or val == "":
                continue
            if k == "label":
                val = str(val)[:70]
            elif k == "openedClip":
                val = str(val)[:INBOX_CLIP_N]
            elif k == "reactions":
                val = str(val)[:800]
            elif k == "detail" and str(val).strip().lower() == "candidate":
                continue
            out[k] = val
        return out
    done_inbox = _done_inbox_keys()
    slack = [
        _slim_cand(row, ("id", "kind", "label", "slackUrl", "channelId", "channel", "sev1Case", "swarmCase", "ts", "threadTs", "unread", "opened", "openedClip", "from", "peer", "done", "lastHumanIsMe", "lastHuman", "reactions", "gusBot", "snippet"))
        for row in (seeded.get("slackCandidates") or [])
        if _inbox_still_open(row, done_inbox)
    ]
    mail = [
        _slim_cand(row, ("id", "kind", "label", "mailUrl", "from", "messageId", "unread", "opened", "openedClip", "snippet", "done"))
        for row in (seeded.get("mailCandidates") or [])
        if _inbox_still_open(row, done_inbox)
    ]
    try:
        write_planner_inbox_txt(slack, mail)
    except Exception as exc:
        append_plan_step(kind="log", label="Inbox clip file failed · " + clip(str(exc), 160))
    try:
        PLANNER_INBOX_FILE.write_text(
            json.dumps({"slack": slack, "mail": mail}, ensure_ascii=False, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    apply_identity_cache(seeded)
    return {
        "kind": "planner-evidence",
        "notThePage": True,
        "instruction": (
            "Not the page. Read this file once, then /tmp/planner-digest.txt once, then /tmp/planner-inbox.txt once. "
            "Mandatory every run: OrgCS (IR + GUS related list), GUS (Support Contact/Follow/SLA/LAP), Slack, Calendar, Mail. "
            "meetings[] is this run's Google Calendar fetch. Do not copy meetings from the previous page. "
            "Analyze all of them. Slack and Mail leftovers are already opened in inbox.txt. Never Slack MCP. Never Gmail batch. Never body SOQL. Never GUS MCP. "
            "You classify Slack from clip + - reactions:; Python does not keep/drop. Empty inbox.txt is not Slack — clear unless slackFetchOk is true. "
            "Missing ## gus with gusFetchOk false is not GUS — clear. "
            "ONE Write of complete /tmp/plan-ai.json then stop. Never Read /tmp/plan.json."
        ),
        "name": seeded.get("name") or "",
        "title": seeded.get("title") or "",
        "manager": seeded.get("manager") or "",
        "timezone": seeded.get("timezone") or "",
        "timezoneUnresolved": not str(seeded.get("timezone") or "").strip(),
        "timezoneShort": seeded.get("timezoneShort") or "",
        "engineerShift": seeded.get("engineerShift") or "",
        "daypart": seeded.get("daypart") or "mid",
        "shiftStart": seeded.get("shiftStart") or "",
        "shiftEnd": seeded.get("shiftEnd") or "",
        "assembledFromCalendar": bool(seeded.get("assembledFromCalendar")),
        "assembledSchedule": seeded.get("assembledSchedule") or "",
        "facts": seeded.get("facts") or [],
        "openCases": seeded.get("openCases") or len(cases),
        "needYou": seeded.get("needYou") or 0,
        "cases": cases,
        "meetings": meetings,
        "nowStamp": clock.get("nowStamp") or "",
        "planFrom": clock.get("planFrom") or "",
        "planUntil": clock.get("planUntil") or "",
        "freeMinutes": int(clock.get("freeMinutes") or 0),
        "slackCandidates": slack,
        "mailCandidates": mail,
        "slackFetchOk": seeded.get("slackFetchOk") is True,
        "mailFetchOk": seeded.get("mailFetchOk") is True,
        "orgcsFetchOk": seeded.get("orgcsFetchOk") is True,
        "gusFetchOk": seeded.get("gusFetchOk") is True,
        "calendarFetchOk": seeded.get("calendarFetchOk") is True,
        "orgcsSlaFetchOk": seeded.get("orgcsSlaFetchOk") is True,
        "orgcsRelatedFetchOk": seeded.get("orgcsRelatedFetchOk") is True,
        "slackFetchError": str(seeded.get("slackFetchError") or "")[:180],
        "mailFetchError": str(seeded.get("mailFetchError") or "")[:180],
        "gusFetchError": str(seeded.get("gusFetchError") or "")[:180],
        "calendarFetchError": str(seeded.get("calendarFetchError") or "")[:180],
        "gusCandidates": [
            _slim_cand(
                row,
                (
                    "id", "kind", "role", "name", "workId", "status", "due",
                    "outOfSla", "slaValue", "slaWarningSent", "slaViolations",
                    "start", "end", "label", "detail", "gusUrl", "caseNumber", "type",
                ),
            )
            for row in (seeded.get("gusCandidates") or [])
            if isinstance(row, dict)
        ][:40],
        "slaPending": [
            row
            for row in (seeded.get("slaPending") or [])
            if isinstance(row, dict)
        ][:20],
        "activityClipped": _digest_file_ready(),
        "peekDigestFile": "/tmp/planner-digest.txt",
        "inboxFile": "/tmp/planner-inbox.txt",
    }


def _digest_file_ready() -> bool:
    try:
        return PLANNER_DIGEST_FILE.is_file() and PLANNER_DIGEST_FILE.stat().st_size > 20
    except OSError:
        return False


def digest_activity_stats() -> tuple[int, int]:
    """(case headers, cases with a clipped comment/email — not the empty stub)."""
    try:
        text = PLANNER_DIGEST_FILE.read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    headers = 0
    filled = 0
    started = False
    empty = True
    for ln in text.splitlines():
        if ln.startswith("## "):
            title = ln[3:].strip().split()[0] if ln[3:].strip() else ""
            if started and not empty:
                filled += 1
            if title.isdigit():
                headers += 1
                started = True
                empty = True
            else:
                started = False
                empty = True
            continue
        if started and ln.strip() and "no clipped comment/email yet" not in ln:
            empty = False
    if started and not empty:
        filled += 1
    return headers, filled


def digest_case_count() -> int:
    headers, _filled = digest_activity_stats()
    return headers


def _id_only_candidates(rows, extra: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = {"id": row.get("id")}
        if "unread" in row:
            item["unread"] = bool(row.get("unread"))
        if "lastHumanIsMe" in row:
            item["lastHumanIsMe"] = bool(row.get("lastHumanIsMe"))
        for key in extra:
            val = row.get(key)
            if val:
                item[key] = str(val)[:40]
        out.append(item)
    return out


def write_planner_gather(evidence: dict) -> dict:
    """Compact gather JSON under Claude's Read token cap. Peek lives in planner-digest.txt."""
    thin = dict(evidence) if isinstance(evidence, dict) else {}
    cases = []
    for row in thin.get("cases") or []:
        if not isinstance(row, dict):
            continue
        item = {
            k: v
            for k, v in row.items()
            if k not in {"activity", "chronology", "peeks", "peek", "summary", "sectionHint"}
        }
        cases.append(item)
    thin["cases"] = cases
    thin.pop("peekDigest", None)
    thin["peekDigestFile"] = "/tmp/planner-digest.txt"
    thin["inboxFile"] = "/tmp/planner-inbox.txt"
    thin["activityClipped"] = _digest_file_ready()
    thin.pop("_calendarEvents", None)
    for key in ("slackCandidates", "mailCandidates", "gusCandidates"):
        slim = []
        for row in thin.get(key) or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row.pop("openedClip", None)
            row.pop("snippet", None)
            row.pop("reactions", None)
            if isinstance(row.get("label"), str):
                row["label"] = row["label"][:70]
            slim.append(row)
        thin[key] = slim
    blob = json.dumps(thin, ensure_ascii=False, separators=(",", ":"))
    if len(blob) > MAX_GATHER_CHARS:
        for row in cases:
            row.pop("problem", None)
            if isinstance(row.get("label"), str):
                row["label"] = row["label"][:50]
        blob = json.dumps(thin, ensure_ascii=False, separators=(",", ":"))
    if len(blob) > MAX_GATHER_CHARS:
        for row in thin.get("slackCandidates") or []:
            if isinstance(row, dict):
                row.pop("openedClip", None)
        for row in thin.get("mailCandidates") or []:
            if isinstance(row, dict):
                row.pop("openedClip", None)
                row.pop("snippet", None)
        blob = json.dumps(thin, ensure_ascii=False, separators=(",", ":"))
    if len(blob) > MAX_GATHER_CHARS:
        thin["slackCandidates"] = _id_only_candidates(thin.get("slackCandidates"))
        thin["mailCandidates"] = _id_only_candidates(thin.get("mailCandidates"), ("from",))
        blob = json.dumps(thin, ensure_ascii=False, separators=(",", ":"))
    try:
        PLANNER_GATHER_FILE.write_text(blob + "\n", encoding="utf-8")
    except OSError:
        pass
    return thin


def seed_plan_from_live() -> dict:
    path = pathlib.Path("/tmp/plan.json")
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    if path.is_file():
        try:
            path.replace(pathlib.Path("/tmp/plan-prev.json"))
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass
    if not isinstance(data.get("sections"), list) or not data.get("sections"):
        data = {
            "timezone": str(data.get("timezone") or ""),
            "timezoneShort": str(data.get("timezoneShort") or ""),
            "shiftStart": str(data.get("shiftStart") or "08:00"),
            "shiftEnd": str(data.get("shiftEnd") or "17:00"),
            "daypart": str(data.get("daypart") or "mid"),
            "name": data.get("name") or "",
            "title": data.get("title") or "",
            "manager": data.get("manager") or "",
            "sections": [
                {"title": "Needs us now", "open": True, "items": [], "tone": "now"},
                {"title": "Slack", "open": True, "groups": [], "items": []},
                {"title": "Mail", "open": True, "items": []},
                {"title": "GUS", "open": True, "items": []},
                {"title": "Follow-up due", "open": True, "items": []},
                {"title": "Still watching", "open": False, "items": []},
                {"title": "Today's plan", "open": True, "items": []},
                {"title": "Tomorrow, first thing", "open": True, "items": []},
            ],
        }
    data.pop("_calendarEvents", None)
    data["calendarFetchOk"] = False
    try:
        _sanitize_mod().stamp_clock(data)
    except Exception:
        pass
    repair_plan_payload(data, refresh_inbox=False, wipe_peek_summary=True)
    apply_identity_cache(data)
    try:
        fetch_orgcs_identity(data)
    except Exception:
        pass
    persist_identity(data)
    apply_live_assembled(data)
    forget_prior_fetches(data)
    data["todayPlan"] = []
    data["aiAnalyzed"] = False
    data["inboxReviewed"] = False
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(r"today['’]?s plan|^calendar$|^day plan", str(sec.get("title") or ""), re.I):
            continue
        sec["items"] = [
            it
            for it in (sec.get("items") or [])
            if isinstance(it, dict)
            and it.get("composed") is not True
            and not str(it.get("id") or "").startswith("plan-")
        ]
    return data


def forget_prior_fetches(data: dict) -> None:
    """A previous plan's fetch flags are not this run. Search Slack, Mail, and GUS again.

    Done marks stay in the ledger. Fresh hits are still dropped when they match it.
    """
    if not isinstance(data, dict):
        return
    for key in (
        "slackFetchOk",
        "slackFetchError",
        "slackCandidates",
        "mailFetchOk",
        "mailFetchError",
        "mailCandidates",
        "_mailFetchTried",
        "gusFetchOk",
        "gusFetchError",
        "gusCandidates",
    ):
        data.pop(key, None)


def _replace_inbox_section(data: dict, title_re: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not re.match(title_re, str(sec.get("title") or ""), re.I):
            continue
        if payload.get("groups") is None and isinstance(payload.get("rows"), list):
            payload = dict(payload)
            payload["groups"] = [{"title": "Not opened", "items": payload.get("rows")}]
        if payload.get("groups") is not None:
            groups = []
            for grp in payload.get("groups") or []:
                if not isinstance(grp, dict):
                    continue
                grp = dict(grp)
                items = grp.get("items")
                if not isinstance(items, list) or not items:
                    if isinstance(grp.get("rows"), list):
                        grp["items"] = grp.get("rows")
                groups.append(grp)
            sec["groups"] = groups
        if "items" in payload:
            sec["items"] = payload.get("items") or []
        empty = str(payload.get("empty") or "").strip()
        if empty:
            sec["empty"] = empty
        elif payload.get("groups") or payload.get("items"):
            sec.pop("empty", None)
        return


def apply_ai_overlay(data: dict, started_epoch: float) -> dict:
    apply_activity_file(data)
    path = pathlib.Path("/tmp/plan-ai.json")
    try:
        if not path.is_file() or path.stat().st_mtime < (started_epoch - 2):
            return data
        ai = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(ai, dict):
            return data
        if ai.get("notThePage") is True or str(ai.get("kind") or "") == "planner-evidence":
            return data
        peeks = ai.get("peeks") if isinstance(ai.get("peeks"), dict) else {}
        if peeks:
            prev = data.get("peeks") if isinstance(data.get("peeks"), dict) else {}
            merged = dict(prev)
            merged.update(peeks)
            data["peeks"] = merged
        if not str(data.get("timezone") or "").strip():
            chosen = str(ai.get("timezone") or "").strip()
            if chosen:
                data["timezone"] = chosen
            short = str(ai.get("timezoneShort") or "").strip()
            if short and not str(data.get("timezoneShort") or "").strip():
                data["timezoneShort"] = short
        apply_general_shift_hours(data)
        for sec in data.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            overlay_items = list(sec.get("items") or [])
            for group in sec.get("groups") or []:
                if isinstance(group, dict):
                    overlay_items.extend(group.get("items") or [])
            for item in overlay_items:
                if not isinstance(item, dict):
                    continue
                num = re.sub(r"\D", "", str(item.get("caseNumber") or ""))
                if len(num) < 6:
                    found = re.search(
                        r"(\d{6,})",
                        str(item.get("id") or "") + " " + str(item.get("label") or ""),
                    )
                    num = found.group(1) if found else ""
                spec = peeks.get(num) if num else None
                if spec is None and num:
                    for key, val in peeks.items():
                        if re.sub(r"\D", "", str(key)) == num and isinstance(val, dict):
                            spec = val
                            break
                if not isinstance(spec, dict):
                    continue
                summary = str(spec.get("summary") or "").strip()
                peek = item.get("peek") if isinstance(item.get("peek"), dict) else {}
                if summary and _is_stub_peek(summary):
                    summary = ""
                if summary:
                    item["summary"] = summary
                    peek["summary"] = summary
                if spec.get("chronology"):
                    peek["chronology"] = spec["chronology"]
                    item["chronology"] = spec["chronology"]
                for stamp_key in ("meetingStartStamp", "meetingEndStamp", "customerEmail", "meetingGuests"):
                    if spec.get(stamp_key) not in (None, "", []):
                        item[stamp_key] = spec[stamp_key]
                if summary or spec.get("chronology") or spec.get("bucket"):
                    item["peek"] = peek
                if spec.get("bucket"):
                    peek["bucket"] = spec["bucket"]
                    item["bucket"] = spec["bucket"]
                    item["peek"] = peek
        if "slack" in ai and data.get("slackFetchOk") is not False:
            _replace_inbox_section(data, r"slack\b", ai.get("slack") or {})
        if "mail" in ai and data.get("mailFetchOk") is not False:
            _replace_inbox_section(data, r"(mail|email|gmail)\b", ai.get("mail") or {})
        if "gus" in ai and data.get("gusFetchOk") is not False:
            have_gus = any(
                isinstance(s, dict) and re.match(r"^gus\b", str(s.get("title") or ""), re.I)
                for s in (data.get("sections") or [])
            )
            if not have_gus:
                data.setdefault("sections", []).append({"title": "GUS", "open": True, "items": [], "groups": []})
            _replace_inbox_section(data, r"^gus\b", ai.get("gus") or {})
        if ai.get("aiAnalyzed") is True:
            data["aiAnalyzed"] = True
        slack_ok = data.get("slackFetchOk") is not False
        mail_ok = data.get("mailFetchOk") is not False
        if ai.get("inboxReviewed") is True and slack_ok and mail_ok:
            data["inboxReviewed"] = True
        elif ai.get("inboxReviewed") is True and (data.get("slackFetchOk") is False or data.get("mailFetchOk") is False):
            data["inboxReviewed"] = False
        if ai.get("gusReviewed") is True and data.get("gusFetchOk") is not False:
            data["gusReviewed"] = True
        if ai.get("sourcesAnalyzed") is True:
            data["sourcesAnalyzed"] = True
        for key in ("name", "title", "manager"):
            val = str(ai.get(key) or "").strip()
            if val and not str(data.get(key) or "").strip():
                data[key] = val
        persist_identity(data)
        for key in (
            "needsUsNow",
            "followUpDue",
            "stillWatching",
            "quickWins",
            "customerAskedMeeting",
            "beforeYouLogOff",
            "tomorrowFirst",
        ):
            if isinstance(ai.get(key), list):
                data[key] = list(ai[key])
        if "todayPlan" in ai:
            data["todayPlan"] = [x for x in (ai.get("todayPlan") or []) if isinstance(x, dict)]
        if ai.get("aiAnalyzed") is True:
            for sec in data.get("sections") or []:
                if not isinstance(sec, dict):
                    continue
                if not re.search(
                    r"today['’]?s plan|^calendar$|^day plan",
                    str(sec.get("title") or ""),
                    re.I,
                ):
                    continue
                sec["items"] = [
                    it
                    for it in (sec.get("items") or [])
                    if isinstance(it, dict)
                    and it.get("composed") is not True
                    and not str(it.get("id") or "").startswith("plan-")
                ]
        return data
    except (OSError, json.JSONDecodeError):
        return data
    finally:
        try:
            sanit = _sanitize_mod()
            sanit.peel_stray_inbox_rows(data)
            sanit.normalize_inbox_buckets(data)
        except Exception:
            pass
        try:
            sanit = _sanitize_mod()
            sanit.apply_ai_case_buckets(data)
            sanit.apply_ai_closeout(data)
            sanit.apply_ai_tomorrow(data)
            sanit.dedupe_closeout_sections(data)
        except Exception:
            pass
        try:
            sanit = _sanitize_mod()
            sanit.apply_persisted_done(data, None, load_done_ledger())
            sanit.omit_done_inbox_rows(data)
        except Exception:
            pass
        try:
            rows = fetch_owned_open_cases()
            if rows or _owned_fetch_ok():
                _sanitize_mod().drop_unowned_cases(data, owned={str(r.get("CaseNumber") or "").strip() for r in (rows or []) if r.get("CaseNumber")})
        except Exception:
            pass


LAST_CHECK_FAILS: list[str] = []
LAST_PUBLISH_ERROR = ""


def ensure_open_plan_row() -> None:
    """A missing Open row is a clock hole, not a reason to throw away the run."""
    path = pathlib.Path("/tmp/plan-ai.json")
    gather_path = pathlib.Path("/tmp/planner-gather.json")
    try:
        ai = json.loads(path.read_text(encoding="utf-8"))
        gather = json.loads(gather_path.read_text(encoding="utf-8")) if gather_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(ai, dict) or not isinstance(gather, dict):
        return
    if str(gather.get("daypart") or "").strip().lower() == "eod":
        return
    try:
        free = int(gather.get("freeMinutes") or 0)
    except (TypeError, ValueError):
        free = 0
    if free < 45:
        return
    plan = ai.get("todayPlan")
    if not isinstance(plan, list):
        plan = []

    def _is_open(row: dict) -> bool:
        rid = str(row.get("id") or "").lower()
        label = str(row.get("label") or "").strip().lower()
        return "plan-open" in rid or label in ("open", "free")

    if any(_is_open(row) for row in plan if isinstance(row, dict)):
        return
    plan.append(
        {
            "id": "plan-open-1",
            "kind": "plan",
            "label": "Open",
            "minutes": 15,
            "detail": "Open time.",
        }
    )
    ai["todayPlan"] = plan
    try:
        path.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def repair_plan_prompt(fails: list[str]) -> str:
    lines = "\n".join("- " + f for f in fails[:12])
    return (
        "The page did not publish. These are the only blockers. Find a way through them "
        "from the files already on disk. Do not re-gather. Do not Slack, Gmail, or SOQL. "
        "Do not Read /tmp/plan.json or /tmp/planner-gather.json. "
        "Read /tmp/plan-ai.json once. For a missing Peek, Read /tmp/planner-digest.txt once and write "
        "peeks.<caseNumber>.summary as 4-8 sentences from that case's thread. "
        "Keep every other peek, rank, slack, mail, gus, and todayPlan row. "
        "Write the complete /tmp/plan-ai.json again. Then stop.\n"
        + lines
    )


def missing_plan_prompt() -> str:
    return (
        "You stopped before /tmp/plan-ai.json existed, so nothing can publish. "
        "That file is the page. Use the digest and inbox already on disk. "
        "Do not re-gather. Do not Slack, Gmail, or SOQL. "
        "Write the complete /tmp/plan-ai.json now (Peek for every gather case, slack, mail, gus, todayPlan, "
        "aiAnalyzed true, sourcesAnalyzed true). Then stop."
    )


def _digest_case_fields(text: str) -> dict[str, dict[str, str]]:
    parts = re.split(r"(?m)^##\s+(\d{6,})\s*$", text or "")
    found: dict[str, dict[str, str]] = {}
    for i in range(1, len(parts) - 1, 2):
        fields: dict[str, str] = {}
        for line in parts[i + 1].splitlines():
            match = re.match(
                r"-\s+(live|ir|gus related|last customer|last public|last internal):\s*(.*)",
                line.strip(),
                re.I,
            )
            if match:
                fields[match.group(1).lower()] = match.group(2).strip()
        found[parts[i]] = fields
    return found


def _peek_from_digest(num: str, fields: dict[str, str]) -> str:
    live = fields.get("live") or "open"
    sentences = [f"Case {num} is {live}."]
    ir = fields.get("ir") or ""
    if ir:
        sentences.append(f"Initial response is {ir.rstrip('.')}.")
    related = fields.get("gus related") or ""
    if related and related.lower() != "none":
        sentences.append(f"Linked GUS work is {related.rstrip('.')}.")
    customer = fields.get("last customer") or ""
    public = fields.get("last public") or ""
    internal = fields.get("last internal") or ""
    if customer:
        sentences.append("The latest customer message was " + customer[:360].rstrip(".") + ".")
    if public:
        sentences.append("The latest public reply was " + public[:360].rstrip(".") + ".")
    elif internal:
        sentences.append("The latest internal note was " + internal[:360].rstrip(".") + ".")
    while len(sentences) < 4:
        sentences.append("Next step follows whoever wrote last in this thread.")
    return " ".join(sentences)[:1400]


def _chronology_from_digest(fields: dict[str, str]) -> list[dict]:
    beats = []
    for key, kind in (
        ("last customer", "email"),
        ("last public", "comment"),
        ("last internal", "comment"),
    ):
        raw = fields.get(key) or ""
        if not raw:
            continue
        beats.append({"when": raw[:48], "who": "", "kind": kind, "text": raw[:180]})
    return beats


def _cap_digest_file(path: pathlib.Path, limit: int) -> None:
    """Leave the digest intact. The model summarizes. It chooses one Read or forward chunks."""
    return


def _cap_text_file(path: pathlib.Path, limit: int) -> None:
    """Leave the inbox intact. The model summarizes. It chooses one Read or forward chunks."""
    return


def _inbox_payload_rows(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    n = len(payload.get("items") or [])
    for group in payload.get("groups") or []:
        if isinstance(group, dict):
            n += len(group.get("items") or [])
    return n


def _inbox_groups_from_gather(gather: dict) -> tuple[dict, dict, bool]:
    """Keep opened leftovers on the page when the model never classified them."""
    slack_unread: list[dict] = []
    slack_reply: list[dict] = []
    done_inbox = set()
    try:
        done_inbox = _sanitize_mod()._ledger_keys(load_done_ledger())
    except Exception:
        done_inbox = set()
    for row in gather.get("slackCandidates") or []:
        if _sanitize_mod().is_gus_notice(row):
            continue
        if not _inbox_still_open(row, done_inbox):
            continue
        if row.get("lastHumanIsMe") is True and not row.get("sev1Case") and not row.get("swarmCase"):
            continue
        item = {
            "id": row.get("id") or "",
            "kind": "slack",
            "label": str(row.get("label") or "Slack")[:140],
            "slackUrl": row.get("slackUrl") or "",
            "channelId": row.get("channelId") or "",
            "channel": row.get("channel") or "",
            "ts": row.get("ts") or row.get("threadTs") or "",
            "unread": bool(row.get("unread")),
            "lastHumanIsMe": row.get("lastHumanIsMe") is True,
            "opened": bool(row.get("opened") or row.get("openedClip")),
        }
        if row.get("sev1Case"):
            item["sev1Case"] = row.get("sev1Case")
        if row.get("unread") and row.get("lastHumanIsMe") is not False:
            item["slackBucket"] = "unread"
            slack_unread.append(item)
        else:
            item["slackBucket"] = "reply"
            slack_reply.append(item)
    mail_unread: list[dict] = []
    for row in gather.get("mailCandidates") or []:
        if not _inbox_still_open(row, done_inbox):
            continue
        mail_unread.append(
            {
                "id": row.get("id") or "",
                "kind": "mail",
                "label": str(row.get("label") or "Mail")[:140],
                "from": row.get("from") or "",
                "mailUrl": row.get("mailUrl") or "",
                "messageId": row.get("messageId") or "",
                "unread": True,
                "mailBucket": "unread",
                "opened": bool(row.get("opened") or row.get("openedClip")),
            }
        )
    def groups(unread: list, reply: list | None = None) -> list[dict]:
        out = []
        if unread:
            out.append({"title": "Not opened", "items": unread[:40]})
        if reply:
            out.append({"title": "Needs a reply", "items": reply[:40]})
        return out
    had = bool(gather.get("slackCandidates") or gather.get("mailCandidates"))
    return (
        {"groups": groups(slack_unread, slack_reply)},
        {"groups": groups(mail_unread)},
        had,
    )


TODAY_PLAN_SYSTEM = """
Build Today's plan only. Do not Read files. Do not rewrite /tmp/plan-ai.json.
Write /tmp/plan-today.json once: {"todayPlan":[...]}.
Each row: id, label, minutes, kind. Case windows also get caseNumber.
Start of Day and Mid-Day: first work row is Take New Cases (id plan-new-cases, 25 minutes, label Take New Cases). Then named work from the brief: Needs us now (id plan-case-<number>, 20–30 minutes, one case per row), Follow-up (id plan-follow-<number>, 5–10 minutes, one case per row), Slack and Mail only if the brief still has leftovers, promised close near logout (id plan-close).
Short breaks: 10–15 minutes, kind break, id plan-break-1 then plan-break-2. At most 4 a day. At least 45 minutes of other work between them. Never within 45 minutes before or after Breakfast, Lunch, Dinner, or a Snack already on the calendar. Not the last block of the shift. If freeMinutes is at least 180 and a break fits that gap, add one.
Do not write an Open row. Leftover holes stay empty. The page draws them.
End of Day: no Take New Cases and no short break. Named work only if the brief still has something owed. Empty todayPlan is allowed.
Do not invent a meal. Do not copy Google meetings into todayPlan. Then stop. Reply: planned.
""".strip()


def today_plan_user_prompt() -> str:
    gather: dict = {}
    ai: dict = {}
    try:
        loaded = json.loads(PLANNER_GATHER_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            gather = loaded
    except (OSError, json.JSONDecodeError):
        gather = {}
    try:
        loaded_ai = json.loads(pathlib.Path("/tmp/plan-ai.json").read_text(encoding="utf-8"))
        if isinstance(loaded_ai, dict):
            ai = loaded_ai
    except (OSError, json.JSONDecodeError):
        ai = {}
    lines = [
        "Analysis is finished. Build Today's plan from this brief only.",
        f"daypart={gather.get('daypart') or ''} freeMinutes={gather.get('freeMinutes') or 0} "
        f"planFrom={gather.get('planFrom') or ''} planUntil={gather.get('planUntil') or ''}",
        "Meetings:",
    ]
    for ev in (gather.get("meetings") or [])[:12]:
        if isinstance(ev, dict):
            lines.append(f"- {ev.get('startStamp') or ''} {str(ev.get('label') or '')[:50]}")
    for key in ("needsUsNow", "followUpDue", "beforeYouLogOff"):
        nums = []
        for row in ai.get(key) or []:
            num = re.sub(r"\D", "", str(row if not isinstance(row, dict) else row.get("caseNumber") or ""))
            if len(num) >= 6:
                nums.append(num)
        lines.append(f"{key}: {', '.join(nums[:8]) or 'none'}")
    blob = "\n".join(lines)
    if len(blob) > 2500:
        blob = blob[:2499].rstrip() + "…"
    return blob


def merge_today_plan_file() -> bool:
    src = pathlib.Path("/tmp/plan-today.json")
    dest = pathlib.Path("/tmp/plan-ai.json")
    try:
        loaded = json.loads(src.read_text(encoding="utf-8"))
        plan = loaded.get("todayPlan") if isinstance(loaded, dict) else None
        if not isinstance(plan, list) or not plan:
            return False
        ai = json.loads(dest.read_text(encoding="utf-8")) if dest.is_file() else {}
        if not isinstance(ai, dict):
            ai = {}
        ai["todayPlan"] = [row for row in plan if isinstance(row, dict)]
        dest.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
        return bool(ai["todayPlan"])
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def fallback_today_plan() -> None:
    gather: dict = {}
    ai: dict = {}
    try:
        loaded = json.loads(PLANNER_GATHER_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            gather = loaded
    except (OSError, json.JSONDecodeError):
        gather = {}
    dest = pathlib.Path("/tmp/plan-ai.json")
    try:
        loaded_ai = json.loads(dest.read_text(encoding="utf-8"))
        if isinstance(loaded_ai, dict):
            ai = loaded_ai
    except (OSError, json.JSONDecodeError):
        ai = {}
    daypart = str(gather.get("daypart") or "").strip().lower()
    try:
        free = int(gather.get("freeMinutes") or 0)
    except (TypeError, ValueError):
        free = 0
    plan: list[dict] = []
    if daypart != "eod" and free >= 60:
        plan.append(
            {
                "id": "plan-new-cases",
                "kind": "plan",
                "label": "Take New Cases",
                "minutes": 25,
                "detail": "Take new cases.",
            }
        )
        if free >= 180:
            plan.append(
                {
                    "id": "plan-break-1",
                    "kind": "break",
                    "label": "Short Break",
                    "minutes": 15,
                    "detail": "Short break.",
                }
            )
    for row in (ai.get("needsUsNow") or [])[:3]:
        num = re.sub(r"\D", "", str(row if not isinstance(row, dict) else row.get("caseNumber") or ""))
        if len(num) < 6:
            continue
        plan.append(
            {
                "id": f"plan-case-{num}",
                "kind": "plan",
                "label": f"Case {num}",
                "minutes": 25,
                "caseNumber": num,
            }
        )
    ai["todayPlan"] = plan
    try:
        dest.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def repair_known_publish_blocks() -> bool:
    """When the runner is dead, fix the blockers Python can see and publish anyway."""
    dest = pathlib.Path("/tmp/plan-ai.json")
    try:
        ai = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(ai, dict):
        return False
    changed = False
    for key, bucket_key in (("slack", "slackBucket"), ("mail", "mailBucket")):
        payload = ai.get(key)
        if not isinstance(payload, dict):
            continue
        groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
        for group in groups:
            if not isinstance(group, dict):
                continue
            title = str(group.get("title") or "").lower()
            bucket = "unread" if "not opened" in title else "reply"
            for item in group.get("items") or []:
                if not isinstance(item, dict):
                    continue
                if not item.get(bucket_key):
                    item[bucket_key] = bucket
                    changed = True
                if item.get("opened") is not True and (item.get("openedClip") or item.get("slackUrl") or item.get("mailUrl")):
                    item["opened"] = True
                    changed = True
    if not changed:
        return False
    try:
        dest.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


INBOX_CLASSIFY_SYSTEM = """
Classify this run's Slack and Gmail. Write only /tmp/plan-inbox.json.
The clips are planner-inbox.txt in this working directory. If that file is an index, read every listed part once, in order. Each part is already small enough for one Read. Do not read /tmp/planner-inbox.txt. Do not skip a part. Do not re-read a part.
Mail: the kept row label is the subject line from `- subject:`. Detail is the sender from `- from:`. Never use the message id as the label.
Cover every ## slack and ## mail block before you Write. If a read fails or a slice is thin, Write from what you covered and do not exit without that file. Python does not keep or drop.

Slack:
- Drop done:true.
- A leftover is something you still owe.
- Drop public #help / #support / #ask shouts with no @ you.
- Keep a public thread only when you were @-mentioned and a reply is still owed.
- Keep GUS Bot Work Notifier posts. Do not drop them because they are a bot. Those posts are investigation updates. LAP updates are not in this bot. Investigation SLA is not in this bot.
- Keep PSBot only in the group conversation it opens with you and your current manager. In that conversation: "has an SLO due" is a warning, "Out of SLO" is already late, and "long running category" names the Support Contact. Drop PSBot posts in any other channel.
- Keep a Slackbot file titled "ALERT! 15 Minute SLA Warning" and a DM that says a named case will breach or must meet SLA.
- Drop "SLA REMINDER - Schedule started" rota pings.
- Drop when lastHumanIsMe is true, or the last human line is you.
- Closing reactions (ack, white_check_mark, eyes as acknowledgment) can drop even when lastHumanIsMe is false.
- Drop FYI, huddle over, and thanks that say they will update the customer.
- Not opened = unread and still yours. Needs a reply = opened and you still owe a reply.
- Skip STORM and broadcast FYI. Do not skip the PSBot group conversation with the current manager.
- DM label is "{peer} (DM)".

Mail:
- Drop done:true.
- Drop demo-org expiry, calendar invitations, ICS, Gemini notes, Google Meet, Out of Office, and Black Tab sandbox success mail.
- Drop meeting mail that only schedules, reschedules, cancels, or records accepted, declined, tentative, or maybe. Those already show on the calendar.
- Keep Chatter, GUS, or Black Tab mention, or ACTION REQUIRED, that still needs a look.
- Keep a case SLA mail from no.reply@salesforce.com whose subject is "Case <number> will breach SLA in 30 minutes" or "Action Required | SLA Missed". The body names the case, the Response Target, and the ask (accept and a public comment, or close the loop).
- Keep email about an investigation you support or follow. That is an investigation update, with GUS Bot. Investigation SLA is the PSBot group conversation, not mail.
- Drop "SLA ROTA" roster mail. That is not a case or an investigation.
- Drop case-thread Chatter that only repeats the case.

Write {"inboxReviewed": true, "slack": {"groups": [...]}, "mail": {"groups": [...]}}.
Group titles are only "Not opened" or "Needs a reply".
Each group is {"title": "...", "items": [...]}. The array key is items.
Each kept row copies id, label, slackUrl or mailUrl, channelId, ts, from, unread from the clip.
slackBucket is "unread" or "reply". mailBucket is "unread" or "reply".
Omit every dropped row. Empty groups are allowed only when every clip was a drop.
Then stop.
""".strip()


def inbox_classify_prompt() -> str:
    n = 0
    try:
        text = PLANNER_INBOX_TXT.read_text(encoding="utf-8")
        n = text.count("\n## ")
    except OSError:
        text = ""
    if n <= 0:
        return "No opened Slack or Gmail clips this run. Write inboxReviewed true and empty groups.\n"
    return (
        f"planner-inbox.txt has {n} clip blocks and {len(text)} characters. "
        "Read it in one go or in forward chunks. Then Write only the kept rows.\n"
    )


def merge_classified_inbox() -> bool:
    src = pathlib.Path("/tmp/plan-inbox.json")
    dest = pathlib.Path("/tmp/plan-ai.json")
    try:
        classified = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(classified, dict) or classified.get("inboxReviewed") is not True:
        return False
    try:
        ai = json.loads(dest.read_text(encoding="utf-8")) if dest.is_file() else {}
    except (OSError, json.JSONDecodeError):
        ai = {}
    if not isinstance(ai, dict):
        ai = {}
    if isinstance(classified.get("slack"), dict):
        ai["slack"] = classified["slack"]
    if isinstance(classified.get("mail"), dict):
        ai["mail"] = classified["mail"]
    ai["inboxReviewed"] = True
    try:
        dest.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def classify_inbox(run_cli) -> bool:
    """Mandatory Slack and Gmail analysis. The page shows only the kept rows."""
    global PLAN_SYSTEM_OVERRIDE
    system = pathlib.Path("/tmp/planner-inbox-system.md")
    system.write_text(INBOX_CLASSIFY_SYSTEM + "\n", encoding="utf-8")
    PLAN_SYSTEM_OVERRIDE = system
    append_plan_step(kind="log", label="Analyzing Slack and Gmail")
    try:
        pathlib.Path("/tmp/plan-inbox.json").unlink()
    except OSError:
        pass
    try:
        run_cli(prompt=INBOX_CLASSIFY_SYSTEM + "\n\n" + inbox_classify_prompt(), timeout_sec=4 * 60)
    except OSError as exc:
        append_plan_step(kind="log", label="Slack and Gmail analysis could not start: " + clip(str(exc), 160))
    finally:
        PLAN_SYSTEM_OVERRIDE = None
    ready = False
    try:
        loaded = json.loads(pathlib.Path("/tmp/plan-inbox.json").read_text(encoding="utf-8"))
        ready = isinstance(loaded, dict) and loaded.get("inboxReviewed") is True
    except (OSError, json.JSONDecodeError):
        ready = False
    if ready:
        append_plan_step(kind="log", label="Slack and Gmail filtered")
        return True
    append_plan_step(kind="log", label="Slack and Gmail analysis did not finish")
    return False


def install_today_plan(run_cli) -> None:
    """Today's plan runs after Peek, Slack, Mail, and GUS are already written."""
    global PLAN_SYSTEM_OVERRIDE
    system = pathlib.Path("/tmp/planner-today-system.md")
    system.write_text(TODAY_PLAN_SYSTEM + "\n", encoding="utf-8")
    PLAN_SYSTEM_OVERRIDE = system
    append_plan_step(kind="log", label="Building Today's plan")
    try:
        pathlib.Path("/tmp/plan-today.json").unlink()
    except OSError:
        pass
    try:
        run_cli(prompt=TODAY_PLAN_SYSTEM + "\n\n" + today_plan_user_prompt(), timeout_sec=4 * 60)
    except OSError as exc:
        append_plan_step(kind="log", label="Today's plan could not start: " + clip(str(exc), 160))
    finally:
        PLAN_SYSTEM_OVERRIDE = None
    if merge_today_plan_file():
        return
    fallback_today_plan()
    append_plan_step(kind="log", label="Today's plan filled from the finished ranks")


def finish_plan_from_evidence() -> bool:
    """Fill holes the model left, using this run's digest, then let publish proceed."""
    gather: dict = {}
    digest_fields: dict[str, dict[str, str]] = {}
    try:
        loaded = json.loads(pathlib.Path("/tmp/planner-gather.json").read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            gather = loaded
    except (OSError, json.JSONDecodeError):
        gather = {}
    try:
        digest_fields = _digest_case_fields(
            pathlib.Path("/tmp/planner-digest.txt").read_text(encoding="utf-8")
        )
    except OSError:
        digest_fields = {}
    ai_path = pathlib.Path("/tmp/plan-ai.json")
    ai: dict = {}
    try:
        loaded_ai = json.loads(ai_path.read_text(encoding="utf-8"))
        if isinstance(loaded_ai, dict):
            ai = loaded_ai
    except (OSError, json.JSONDecodeError):
        ai = {}
    if not gather and not digest_fields and not ai:
        return False
    peeks = ai.get("peeks") if isinstance(ai.get("peeks"), dict) else {}
    cases = list(gather.get("cases") or [])
    numbers = []
    case_by: dict[str, dict] = {}
    for row in cases:
        if isinstance(row, dict):
            num = str(row.get("caseNumber") or row.get("CaseNumber") or "").strip()
            if num:
                numbers.append(num)
                case_by[num] = row
    if not numbers:
        numbers = list(digest_fields.keys())
    for num in numbers:
        spec = peeks.get(num) if isinstance(peeks.get(num), dict) else {}
        summary = str(spec.get("summary") or "").strip()
        if summary and not _is_stub_peek(summary):
            peeks[num] = spec
            continue
        fields = digest_fields.get(num) or {}
        if not fields and not summary:
            row = case_by.get(num) or {}
            label = str(row.get("label") or num)[:160]
            status = str(row.get("status") or "open")[:40]
            detail = str(row.get("detail") or "").strip()[:180]
            spec = dict(spec)
            spec["summary"] = (
                f"Case {num} is {status}. "
                f"The subject is {label}. "
                + (f"{detail}. " if detail else "No newer thread line was in this run's digest. ")
            )
            spec.setdefault("chronology", [])
            peeks[num] = spec
            continue
        spec = dict(spec)
        spec["summary"] = _peek_from_digest(num, fields) if fields else summary
        if not spec.get("chronology"):
            spec["chronology"] = _chronology_from_digest(fields)
        peeks[num] = spec
    for cand in gather.get("slackCandidates") or []:
        if not isinstance(cand, dict) or not cand.get("sev1Case"):
            continue
        num = str(cand.get("sev1Case") or "")
        channel = str(cand.get("channel") or "").lstrip("#").strip()
        spec = peeks.get(num) if isinstance(peeks.get(num), dict) else None
        if not spec or len(channel) < 3:
            continue
        if channel.lower() not in str(spec.get("summary") or "").lower():
            spec["summary"] = str(spec.get("summary") or "").rstrip(".") + f". Slack channel {channel} was opened."
            peeks[num] = spec
    ai["peeks"] = peeks
    ai["aiAnalyzed"] = True
    ai["sourcesAnalyzed"] = True
    for key in (
        "needsUsNow",
        "followUpDue",
        "stillWatching",
        "quickWins",
        "customerAskedMeeting",
        "beforeYouLogOff",
        "tomorrowFirst",
    ):
        if not isinstance(ai.get(key), list):
            ai[key] = []
    ai["tomorrowFirst"] = [row for row in (ai.get("tomorrowFirst") or [])]
    plan = [row for row in (ai.get("todayPlan") or []) if isinstance(row, dict)]
    daypart = str(gather.get("daypart") or "").strip().lower()
    try:
        free = int(gather.get("freeMinutes") or 0)
    except (TypeError, ValueError):
        free = 0
    if daypart != "eod" and free >= 60:
        new_row = {
            "id": "plan-new-cases",
            "kind": "plan",
            "label": "Take New Cases",
            "minutes": 25,
            "detail": "Take new cases.",
        }
        plan = [row for row in plan if "plan-new-cases" not in str(row.get("id") or "")]
        plan.insert(0, new_row)
        if free >= 180 and not any("plan-break" in str(row.get("id") or "") for row in plan):
            plan.append(
                {
                    "id": "plan-break-1",
                    "kind": "break",
                    "label": "Short Break",
                    "minutes": 15,
                    "detail": "Short break.",
                }
            )
    ai["todayPlan"] = plan
    if not merge_classified_inbox():
        if not isinstance(ai.get("slack"), dict):
            ai["slack"] = {"groups": []}
        if not isinstance(ai.get("mail"), dict):
            ai["mail"] = {"groups": []}
        ai["inboxReviewed"] = False
    if gather.get("gusFetchOk") is True:
        ai["gusReviewed"] = True
    if not isinstance(ai.get("gus"), dict):
        ai["gus"] = {"groups": []}
    try:
        ai_path.write_text(json.dumps(ai, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def publish_plan_inprocess(path: pathlib.Path) -> str:
    """Write the page when the shell publisher cannot start. Returns an error, or ''."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "plan is not an object"
        if data.get("notThePage") is True or str(data.get("kind") or "") == "planner-evidence":
            return "plan file is gather evidence"
        template_path = SKILL_ROOT / "page" / "template.html"
        template = template_path.read_text(encoding="utf-8")
        if "__BRIEFING_DATA__" not in template:
            return "template missing __BRIEFING_DATA__"
        sanit = _sanitize_mod()
        sanit.sanitize(data)
        raw = json.dumps(data, ensure_ascii=False)
        if "</" in raw:
            raw = raw.replace("</", "<\\/")
        PAGE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PAGE.with_name(PAGE.name + ".tmp")
        tmp.write_text(template.replace("__BRIEFING_DATA__", raw), encoding="utf-8")
        tmp.replace(PAGE)
        json_out = PAGE.parent / "briefing.json"
        json_tmp = json_out.with_name("briefing.json.tmp")
        json_tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        json_tmp.replace(json_out)
        return ""
    except Exception as exc:
        return clip(str(exc), 300)


def salvage_publish_plan(started_epoch: float) -> bool:
    """Publish when Peek landed. Inbox may be last good Slack/Mail if the model died mid-open."""
    global LAST_CHECK_FAILS, LAST_PUBLISH_ERROR
    path = pathlib.Path("/tmp/plan.json")
    try:
        if not path.is_file():
            return False
        if path.stat().st_mtime < (started_epoch - 2):
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        if data.get("notThePage") is True or str(data.get("kind") or "") == "planner-evidence":
            return False
        try:
            fetch_orgcs_identity(data)
        except Exception:
            pass
        apply_live_assembled(data)
        data = apply_ai_overlay(data, started_epoch)
        apply_general_shift_hours(data)
        prev = None
        try:
            prev = load_page_briefing()
        except Exception:
            prev = None
        try:
            _sanitize_mod().restore_unreviewed_inbox(data, prev)
        except Exception:
            pass
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, Exception) as exc:
        append_plan_step(kind="log", label="Publish skipped: " + clip(str(exc), 220))
        return False
    env = os.environ.copy()
    env["DAY_PLANNER_NO_OPEN"] = "1"
    env["DAY_PLANNER_NO_BRIDGE"] = "1"
    env["DAY_PLANNER_SKILL"] = str(SKILL_ROOT)
    check = SKILL_ROOT / "scripts" / "self-check-plan.py"
    publish = SKILL_ROOT / "scripts" / "publish-page.sh"

    def peek_hard(fails: list[str]) -> bool:
        return any(
            re.search(
                r"FAIL peek:|FAIL when:|FAIL meeting:|FAIL bunch:|FAIL case-row:|FAIL today:"
                r"|FAIL closeout:|FAIL sev1:"
                r"|FAIL json:|FAIL gather:|FAIL sections:|FAIL ai: set aiAnalyzed",
                f,
            )
            for f in fails
        )

    def recheck() -> list[str]:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ["FAIL json: unreadable"]
        fails, _ = _self_check_mod().check(blob)
        return fails

    try:
        subprocess.run(
            [sys.executable, str(check), "--fix", str(path)],
            cwd=str(SKILL_ROOT),
            env=env,
            check=False,
            capture_output=True,
            timeout=60,
        )
        fails = recheck()
        if fails and not peek_hard(fails):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                prev = load_page_briefing()
                _sanitize_mod().restore_unreviewed_inbox(data, prev, force=True)
                path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
            subprocess.run(
                [sys.executable, str(check), "--fix", str(path)],
                cwd=str(SKILL_ROOT),
                env=env,
                check=False,
                capture_output=True,
                timeout=60,
            )
            fails = recheck()
        if fails:
            LAST_CHECK_FAILS = list(fails)
            append_plan_step(
                kind="log",
                label="Self-check blocked publish: " + "; ".join(fails)[:400],
            )
            return False
        LAST_CHECK_FAILS = []
        result = subprocess.run(
            ["/bin/bash", str(publish), str(path)],
            cwd=str(SKILL_ROOT),
            env=env,
            check=False,
            capture_output=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = None
        try:
            fails = recheck()
        except Exception:
            fails = ["FAIL json: unreadable"]
        if fails:
            LAST_CHECK_FAILS = list(fails)
            append_plan_step(
                kind="log",
                label="Self-check blocked publish: " + "; ".join(fails)[:400],
            )
            return False
        shell_error = clip(str(exc), 300)
    else:
        shell_error = ""
        if result.returncode != 0:
            err = (result.stderr or b"").decode("utf-8", "replace").strip()
            out = (result.stdout or b"").decode("utf-8", "replace").strip()
            shell_error = clip(err or out or f"publish exited {result.returncode}", 300)
    if shell_error or not PAGE.is_file():
        append_plan_step(
            kind="log",
            label="Publish script failed. Writing the page directly: " + (shell_error or "page file missing")[:220],
        )
        direct = publish_plan_inprocess(path)
        if direct or not PAGE.is_file():
            LAST_PUBLISH_ERROR = direct or shell_error or "page file missing"
            append_plan_step(kind="log", label="Publish failed: " + LAST_PUBLISH_ERROR)
            return False
    try:
        repair_published_page()
        reattach_primary_calendar()
    except Exception:
        pass
    LAST_PUBLISH_ERROR = ""
    return True


def repair_published_page() -> None:
    """After Run Planner, repair compact when / string Peek / Assembled rows on disk."""
    if not PAGE.is_file():
        return
    try:
        data = load_page_briefing()
    except Exception:
        return
    try:
        apply_live_assembled(data)
        sanit = _sanitize_mod()
        sanit.apply_persisted_done(data, None, load_done_ledger())
        sanit.sanitize(data)
        sanit.apply_persisted_done(data, None, load_done_ledger())
        sanit.omit_done_inbox_rows(data)
        sanit.drop_done_from_plan(data)
        save_done_ledger_from_data(data)
        write_briefing_data(data)
    except Exception:
        return
    try:
        reattach_primary_calendar()
    except Exception:
        return


def is_plan_title(title: str) -> bool:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(title or ""))
    text = re.sub(r"^[^\w#]+", "", text).strip().lower().replace("'", "").replace("’", "")
    return bool(re.match(r"^(day plan|todays plan|rest of day|rest-of-day|rest of-day|calendar)\b", text) or re.search(r"\btodays plan\b", text))


def is_todays_plan_section(title: str) -> bool:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(title or ""))
    text = re.sub(r"^[^\w#]+", "", text).strip().lower().replace("'", "").replace("’", "")
    return bool(re.search(r"today.?s plan|day plan|rest of[- ]day", text))


def ask_can_complete(section: str, item: dict) -> bool:
    if not isinstance(item, dict) or not item.get("id"):
        return False
    if item.get("autoBreak") is True or str(item.get("kind") or "").lower() == "break":
        return False
    if item.get("assembled") is True or str(item.get("kind") or "").lower() == "assembled":
        return False
    if _is_google_cal_event(item):
        return is_todays_plan_section(section)
    if is_todays_plan_section(section) or is_plan_title(section):
        return True
    t = _section_key(section)
    return bool(
        re.search(
            r"needs (us|you) now|customer asked|^slack\b|^mail\b|^email\b|^gmail\b|^gus\b|"
            r"needs a touch|follow-up due|follow up due|quick win|before you log off",
            t,
        )
    )


def is_cal_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("eventId") or item.get("htmlLink") or item.get("invite") is True:
        return True
    return str(item.get("kind") or "").lower() == "meeting"


def calendar_item_from_event(event: dict):
    label = str(event.get("label") or "Meeting").strip()
    if re.match(r"^(casework|chat)$", label, re.I):
        return None
    event_id = str(event.get("eventId") or "")
    row = {
        "id": event.get("id") or ("cal-" + event_id),
        "kind": "meeting",
        "label": label,
        "detail": event.get("detail") or "",
        "startStamp": event.get("startStamp") or "",
        "endStamp": event.get("endStamp") or "",
        "eventId": event_id,
        "htmlLink": event.get("htmlLink") or "",
        "joinUrl": event.get("joinUrl") or "",
        "invite": bool(event.get("invite")),
        "rsvp": event.get("rsvp") or "",
        "important": bool(event.get("important")),
    }
    return {k: v for k, v in row.items() if v != "" and v is not False}


SOFT_ENABLE_RE = re.compile(
    r"dreamforce|salesforce\+|office hours|blood drive|webinar|keynote|\benablement\b",
    re.I,
)
HARD_MEETING_RE = re.compile(
    r"^(log[\s-]?in|dinner|breakfast|lunch|important|break|daily pod|pod call|team connect|case discussion)\b",
    re.I,
)
MEAL_TITLE_RE = re.compile(r"\b(dinner|breakfast|lunch|brunch|supper|snack|snacks)\b", re.I)
CUSTOMER_MEET_RE = re.compile(
    r"case discussion|customer|sev-?1|war room|daily pod|pod call|team connect",
    re.I,
)
NOW_SEC_RE = re.compile(r"needs us now|^fire\b|new/?escalated|new or escalated", re.I)
FOLLOW_SEC_RE = re.compile(r"follow-up due|needs a touch|follow up due", re.I)
CASE_NUM_RE = re.compile(r"(\d{8,})")
TROUBLE_RE = re.compile(r"troubleshoot|freeze|gack|sev-?1|level 1|\bcti\b", re.I)


def _asks_follow_up_due_list(ql: str) -> bool:
    return bool(re.search(r"follow[\s-]*up\s+due|followup\s+due", ql or "", re.I))


def _asks_todays_plan(ql: str) -> bool:
    return bool(re.search(r"today.?s plan|\bon the clock\b|\bon the plan\b", ql or "", re.I))


def _follow_up_due_list_items(data: dict) -> list[dict]:
    out = []
    for section, item in walk_briefing_items(data):
        if not ask_can_complete(section, item):
            continue
        if is_todays_plan_section(section) or is_plan_title(section):
            continue
        if FOLLOW_SEC_RE.search(_section_key(section)):
            out.append(item)
    return out


def _section_key(title: str) -> str:
    text = re.sub(r"^[^\w#]+", "", str(title or ""))
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    return text.strip().lower()


def _wall(stamp: str):
    raw = (stamp or "").strip()
    if len(raw) < 15:
        return None
    try:
        return datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _to_stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _case_num(item: dict) -> str:
    m = CASE_NUM_RE.search(str((item or {}).get("caseNumber") or (item or {}).get("label") or (item or {}).get("id") or ""))
    return m.group(1) if m else ""


def _one_line(text, n=160) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def _is_soft_enablement(item: dict) -> bool:
    if item.get("important") is True:
        return False
    label = str(item.get("label") or "").strip()
    if HARD_MEETING_RE.search(label):
        return False
    if CUSTOMER_MEET_RE.search(label):
        return False
    return bool(SOFT_ENABLE_RE.search(label))


def _is_keep_meeting(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("assembled") is True or str(item.get("kind") or "").lower() == "assembled":
        return False
    kind = str(item.get("kind") or "").lower()
    if not (item.get("eventId") or item.get("htmlLink") or item.get("invite") is True or kind == "meeting"):
        return False
    if not item.get("startStamp") or not item.get("endStamp"):
        return False
    if _is_soft_enablement(item):
        return False
    return True


def _is_composed_row(item: dict) -> bool:
    return isinstance(item, dict) and (
        item.get("composed") is True or str(item.get("id") or "").startswith("plan-")
    )


def _is_existing_work(item: dict) -> bool:
    if not isinstance(item, dict) or item.get("done") is True:
        return False
    kind = str(item.get("kind") or "").lower()
    if item.get("eventId") or item.get("htmlLink") or item.get("invite") is True or kind == "meeting":
        return False
    if not item.get("startStamp") or not item.get("endStamp"):
        return False
    if _is_composed_row(item):
        return False
    return kind in ("plan", "task", "case", "break")


def _walk_section_items(sec: dict):
    for it in sec.get("items") or []:
        if isinstance(it, dict):
            yield it
    for group in sec.get("groups") or []:
        if isinstance(group, dict):
            for it in group.get("items") or []:
                if isinstance(it, dict):
                    yield it


def _real_case_row(item: dict) -> bool:
    if not isinstance(item, dict) or item.get("done") is True:
        return False
    kind = str(item.get("kind") or "").lower()
    if kind in ("note", "meeting", "slack", "mail"):
        return False
    lab = str(item.get("label") or "")
    if re.search(r"\bclear\b", lab, re.I) and not item.get("caseNumber"):
        return False
    return bool(item.get("caseNumber") or kind == "case" or CASE_NUM_RE.search(lab))


def _collect_ranked_cases(data: dict) -> tuple[list, list]:
    now_cases, follow_cases = [], []
    seen = set()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        bucket = None
        if NOW_SEC_RE.search(key):
            bucket = now_cases
        elif FOLLOW_SEC_RE.search(key):
            bucket = follow_cases
        if bucket is None:
            continue
        for it in _walk_section_items(sec):
            if not _real_case_row(it):
                continue
            num = _case_num(it)
            if num and num in seen:
                continue
            if num:
                seen.add(num)
            bucket.append(it)
    return now_cases, follow_cases


def _shift_window(data: dict, meetings: list) -> tuple[datetime, datetime]:
    day = None
    for item in meetings:
        parsed = _wall(item.get("startStamp") or "")
        if parsed:
            day = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
            break
    if day is None:
        tzname = str(data.get("timezone") or "").strip()
        now = datetime.now(zone_for_math(tzname))
        day = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    known_zone = bool(str(data.get("timezone") or "").strip())
    sh, sm, eh, em = (8, 0, 17, 0) if known_zone else (0, 0, 0, 0)
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(data.get("shiftStart") or "").strip())
    if match:
        sh, sm = int(match.group(1)), int(match.group(2))
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(data.get("shiftEnd") or "").strip())
    if match:
        eh, em = int(match.group(1)), int(match.group(2))
    start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end


def _occupied_ranges(items: list) -> list:
    rows = []
    for item in items:
        start = _wall(item.get("startStamp") or "")
        end = _wall(item.get("endStamp") or "")
        if start and end and end > start:
            rows.append((start, end, item))
    rows.sort(key=lambda row: row[0])
    return rows


def _gap_list(start: datetime, end: datetime, occupied: list) -> list:
    cursor = start
    gaps = []
    for a, b, _item in occupied:
        a = max(a, start)
        b = min(b, end)
        if a >= end:
            break
        if a > cursor:
            gaps.append([cursor, min(a, end)])
        if b > cursor:
            cursor = b
        if cursor >= end:
            break
    if cursor < end:
        gaps.append([cursor, end])
    return [g for g in gaps if (g[1] - g[0]).total_seconds() >= 5 * 60]


def _is_meal(item: dict) -> bool:
    return bool(MEAL_TITLE_RE.search(str((item or {}).get("label") or "").strip()))


def _is_customer_meeting(item: dict) -> bool:
    return bool(CUSTOMER_MEET_RE.search(str((item or {}).get("label") or "")))


def _in_customer_buffer(when: datetime, occupied: list, minutes: int = 20) -> bool:
    for a, _b, item in occupied:
        if _is_customer_meeting(item) and a - timedelta(minutes=minutes) < when <= a:
            return True
    return False


def _take_gap(gaps: list, minutes: int, occupied: list, *, after=None, avoid_buffer=False):
    need = timedelta(minutes=max(5, int(minutes)))
    for i, gap in enumerate(gaps):
        lo, hi = gap[0], gap[1]
        start = lo
        if after is not None and start < after:
            start = after
        if avoid_buffer:
            n = 0
            while _in_customer_buffer(start, occupied) and start + need <= hi and n < 36:
                start += timedelta(minutes=5)
                n += 1
        if hi - start < need:
            continue
        end = start + need
        rest = []
        if start - lo >= timedelta(minutes=5):
            rest.append([lo, start])
        if hi - end >= timedelta(minutes=5):
            rest.append([end, hi])
        gaps[i : i + 1] = rest
        occupied.append((start, end, {"label": "work"}))
        occupied.sort(key=lambda row: row[0])
        return start, end
    return None


def _work_row(row_id: str, kind: str, label: str, start: datetime, end: datetime, src=None, detail=""):
    row = {
        "id": row_id,
        "kind": kind,
        "label": label,
        "detail": detail or (_one_line((src or {}).get("detail")) if src else ""),
        "startStamp": _to_stamp(start),
        "endStamp": _to_stamp(end),
        "composed": True,
        "asTask": kind in ("plan", "task", "case"),
    }
    if src:
        for key in ("caseNumber", "caseUrl", "summary", "chronology"):
            if src.get(key):
                row[key] = src[key]
    return {k: v for k, v in row.items() if v != "" and v is not False}


def _work_label(prefix: str, item: dict) -> str:
    label = re.sub(r"^[^\w#]+", "", str(item.get("label") or "")).strip()
    return f"{prefix} {label}".strip()


def ensure_page_stamp(data: dict, *, refresh: bool = False) -> None:
    tzname = str(data.get("timezone") or "").strip()
    now = datetime.now(zone_for_math(tzname))
    stamp = str(data.get("stamp") or "").strip()
    generated = str(data.get("generatedAt") or "").strip()
    if refresh or not stamp:
        data["stamp"] = fmt_day_clock(now)
    if refresh or not generated:
        data["generatedAt"] = now.strftime("%Y%m%dT%H%M%S")
    if tzname and not str(data.get("timezoneShort") or "").strip():
        if tzname in {"Asia/Kolkata", "Asia/Calcutta", "Asia/Colombo"}:
            data["timezoneShort"] = "IST"
        else:
            short = str(now.tzname() or "").replace("PDT", "PT").replace("PST", "PT").replace("EDT", "ET").replace("EST", "ET")
            if short:
                data["timezoneShort"] = short


def compose_work_into_data(data: dict) -> dict:
    sanit = _sanitize_mod()
    sanit.compose_work_blocks(data)
    return data


def merge_calendar_into_page(events: list, tzname: str = "") -> None:
    data = load_live_briefing()
    sanit = None
    try:
        sanit = _sanitize_mod()
        sanit.apply_persisted_done(data, None, load_done_ledger())
    except Exception:
        sanit = None
    sections = data.get("sections")
    if not isinstance(sections, list):
        sections = []
        data["sections"] = sections
    plan_idx = -1
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        if is_plan_title(str(sec.get("title") or "")):
            plan_idx = i
        if isinstance(sec.get("items"), list):
            sec["items"] = [it for it in sec["items"] if not is_cal_item(it)]
        for group in sec.get("groups") or []:
            if isinstance(group, dict) and isinstance(group.get("items"), list):
                group["items"] = [it for it in group["items"] if not is_cal_item(it)]
    if plan_idx < 0:
        sections.append({"title": "Calendar", "open": True, "items": []})
        plan_idx = len(sections) - 1
    sec = sections[plan_idx]
    if not isinstance(sec.get("items"), list):
        sec["items"] = []
    rows = []
    for ev in events or []:
        if not isinstance(ev, dict) or not ev.get("eventId") or not ev.get("startStamp") or not ev.get("endStamp"):
            continue
        row = calendar_item_from_event(ev)
        if not row:
            continue
        row.pop("done", None)
        rows.append(row)
    sec["items"] = list(sec.get("items") or []) + rows
    sec["open"] = True
    try:
        if sanit is None:
            sanit = _sanitize_mod()
        sanit.organize_plan(data)
        sanit.compose_work_blocks(data)
        sanit.drop_done_from_plan(data)
        save_done_ledger_from_data(data)
    except Exception:
        pass
    zone = str(tzname or data.get("timezone") or "").strip()
    data["stamp"] = fmt_day_clock(datetime.now(zone_for_math(zone)))
    write_briefing_data(data)


def ask_payload(body: dict | None = None) -> dict:
    page = (body or {}).get("page")
    if page is None:
        page = (body or {}).get("data")
    if isinstance(page, dict) and page:
        data = dict(page)
        data.pop("copyright", None)
        data.pop("bridgeUrl", None)
        data.pop("refreshPrompt", None)
        data.pop("_viewTz", None)
        data.pop("_viewShort", None)
        if not briefing_is_today(data):
            raise FileNotFoundError("planner page not published yet")
        apply_ask_view(data, body)
        return data
    data = ask_page_json()
    apply_ask_view(data, body)
    return data


def walk_briefing_items(data: dict):
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        for item in sec.get("items") or []:
            if isinstance(item, dict):
                yield title, item
        for group in sec.get("groups") or []:
            if not isinstance(group, dict):
                continue
            gtitle = (title + " / " + str(group.get("title") or "")).strip(" /")
            for item in group.get("items") or []:
                if isinstance(item, dict):
                    yield gtitle, item


def item_blob(section: str, item: dict) -> str:
    parts = [
        section,
        item.get("label"),
        item.get("detail"),
        item.get("summary"),
        item.get("when"),
        item.get("caseNumber"),
        item.get("caseSubject"),
        item.get("kind"),
        "completed" if item.get("done") is True else "",
        item.get("customerEmail"),
    ]
    for row in item.get("chronology") or []:
        if isinstance(row, dict):
            parts.extend([row.get("when"), row.get("who"), row.get("text"), row.get("kind")])
    return " ".join(str(part or "") for part in parts)


def ask_tokens(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[a-z0-9]+", (text or "").lower()) if tok not in ASK_STOP and len(tok) > 1]


def stem_tok(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def score_item(qtoks: list[str], qraw: str, section: str, item: dict) -> int:
    blob = item_blob(section, item).lower()
    score = 0
    label = (item.get("label") or "").lower()
    for tok in qtoks:
        keys = {tok, stem_tok(tok)}
        if any(key in blob for key in keys):
            score += 4 if len(tok) > 3 else 2
            if any(key in label for key in keys):
                score += 3
    case = str(item.get("caseNumber") or "")
    digits = re.sub(r"\D", "", qraw)
    if case and case in digits:
        score += 40
    sl = section.lower()
    ql = qraw.lower()
    if "needs us now" in sl and re.search(r"\b(fire|urgent|need(s)? (us|me))\b", ql):
        score += 12
    if "follow-up due" in sl and "follow" in ql:
        score += 8
    if sl.startswith("slack") and "slack" in ql:
        score += 6
    if sl.startswith("mail") and re.search(r"\b(mail|gmail|email)\b", ql):
        score += 6
    if is_meeting(item) and re.search(r"\bmeet", ql):
        score += 8
    return score


def fmt_item(section: str, item: dict, with_summary: bool = False) -> str:
    label = (item.get("label") or "").strip() or "Untitled"
    when = (item.get("when") or "").strip()
    detail = (item.get("detail") or "").strip()
    head = label + (f" ({when})" if when else "")
    bits = [head if head.endswith(".") else head + "."]
    if detail:
        bits.append(detail if detail.endswith(".") else detail + ".")
    if with_summary:
        summary = (item.get("summary") or "").strip()
        if summary:
            bits.append(summary)
        chrono = item.get("chronology") or []
        last = chrono[-1] if chrono and isinstance(chrono[-1], dict) else None
        if last:
            who = last.get("who") or "Someone"
            text = last.get("text") or ""
            when_c = last.get("when") or ""
            stamp = f" ({when_c})" if when_c else ""
            bits.append(f"Last on the thread: {who}{stamp} — {text}")
    return " ".join(bits)


def clip_ask_history(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for turn in raw[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        text = str(turn.get("text") or turn.get("content") or "").strip()[:500]
        if not text:
            continue
        if role in ("user", "you"):
            out.append({"role": "user", "text": text})
        elif role in ("assistant", "bot"):
            out.append({"role": "assistant", "text": text})
    return out


def _history_blob(history: list[dict] | None) -> str:
    parts = []
    for turn in history or []:
        parts.append(str(turn.get("text") or ""))
    return " ".join(parts)


def _ask_case_digits(text: str) -> str:
    found = re.findall(r"\d{8,}", text or "")
    return found[-1] if found else ""


def wants_case_latest(ql: str) -> bool:
    if not re.search(r"\d{8,}", ql):
        return False
    return bool(
        re.search(
            r"\b(latest|update|status|peek|summary|what happened|what's going on|whats going on|"
            r"who has the ball|last (wrote|outbound|customer|public)|where .* stand)\b",
            ql,
        )
    )


def answer_case_latest(question: str, data: dict) -> dict | None:
    num = _ask_case_digits(question)
    if not num:
        return None
    ranked = []
    for section, item in walk_briefing_items(data):
        if not isinstance(item, dict):
            continue
        blob = str(item.get("caseNumber") or "") + " " + str(item.get("label") or "") + " " + str(item.get("id") or "")
        if num not in blob:
            continue
        peek = item.get("peek") if isinstance(item.get("peek"), dict) else {}
        summary = str(item.get("summary") or peek.get("summary") or "").strip()
        chrono = peek.get("chronology") if isinstance(peek.get("chronology"), list) else item.get("chronology")
        activity = item.get("activity") if isinstance(item.get("activity"), list) else []
        score = 10 + (20 if summary else 0) + (8 if chrono else 0) + (6 if activity else 0)
        if str(item.get("id") or "").startswith("plan-"):
            score -= 4
        ranked.append((score, section, item, summary, chrono, activity))
    if not ranked:
        return None
    ranked.sort(key=lambda row: -row[0])
    _score, section, item, summary, chrono, activity = ranked[0]
    label = str(item.get("label") or f"#{num}").strip()
    detail = str(item.get("detail") or "").strip()
    bits = [f"{label}."]
    if detail:
        bits.append(detail if detail.endswith(".") else detail + ".")
    if summary:
        bits.append(summary)
    beat = None
    if isinstance(activity, list) and activity and isinstance(activity[0], dict):
        beat = activity[0]
    elif isinstance(chrono, list) and chrono and isinstance(chrono[-1], dict):
        beat = chrono[-1]
    if beat:
        who = beat.get("who") or "Someone"
        when_c = beat.get("when") or ""
        text = str(beat.get("text") or "").strip()
        stamp = f" ({when_c})" if when_c else ""
        bits.append(f"Latest on the page: {who}{stamp} — {text}")
    if item.get("done") is True:
        bits.append("Marked Done on this page.")
    return {"answer": " ".join(bits), "mode": "case", "hits": 1}


def wants_done_toggle(ql: str) -> str:
    ql = (ql or "").strip().lower()
    if not ql:
        return ""
    if re.search(r"\b(is|was|are)\b.{0,40}\b(done|complete)\b", ql) and not re.search(
        r"\b(mark|set|tick|check|undo|undone)\b", ql
    ):
        return ""
    if re.search(r"\b(undo|undone|un-done|uncomplete|incomplete|not done|still open)\b", ql):
        return "undo"
    if re.search(r"\bmark .{0,80}\b(as )?(not done|undone)\b", ql):
        return "undo"
    if re.search(
        r"\b(mark|set|tick|check off|complete|i('m| am)? done with|done with)\b",
        ql,
    ) and re.search(r"\b(done|complete|completed|finished)\b", ql):
        return "done"
    if re.search(r"\bmark .{0,40}\b(done|complete)\b", ql):
        return "done"
    if re.search(r"\b(undone|undo)\s*$", ql) and len(ask_tokens(ql)) >= 2:
        return "undo"
    if re.search(r"\b(done|complete|completed)\s*$", ql) and len(ask_tokens(ql)) >= 2:
        return "done"
    return ""


def _last_user_ask(history: list[dict] | None) -> str:
    for turn in reversed(history or []):
        if str(turn.get("role") or "") == "user":
            return str(turn.get("text") or "")
    return ""


def _looks_like_row_pick(ql: str) -> bool:
    ql = (ql or "").strip().lower()
    if not ql:
        return False
    if re.search(r"\b(this one|that one|this row|that row|that item|this item)\b", ql):
        return True
    if _ask_case_digits(ql):
        return True
    return len(ask_tokens(ql)) >= 3


def _apply_ask_done(data: dict, item: dict, on: bool, extra: list[dict] | None = None) -> list[str]:
    sanit = _sanitize_mod()
    targets = [item] + [row for row in (extra or []) if isinstance(row, dict)]
    keys = []
    ids = set()
    for target in targets:
        keys.extend(k for k in sanit.item_done_keys(target) if k)
        tid = str(target.get("id") or "")
        if tid:
            ids.add(tid)
    keyset = set(keys)
    for _sec, other in walk_briefing_items(data):
        if not isinstance(other, dict):
            continue
        other_keys = set(sanit.item_done_keys(other))
        hit = (ids and str(other.get("id") or "") in ids) or (keyset and keyset & other_keys)
        if hit:
            other["done"] = True if on else False
            keys.extend(sanit.item_done_keys(other))
    sanit.sync_queue_plan_blocks(data)
    save_done_ledger_from_data(data)
    sanit.apply_persisted_done(data, None, load_done_ledger())
    write_briefing_data(data)
    return sorted(set(keys))


def _toggle_item_list(
    data: dict,
    on: bool,
    items: list[dict],
    already: str,
    empty_undo: str,
    bulk_label: str,
) -> dict | None:
    if not items:
        return None
    if on:
        need = [it for it in items if it.get("done") is not True]
        if not need:
            return {
                "answer": already,
                "mode": "done",
                "reload": False,
                "done": {"on": True, "id": "", "label": bulk_label, "keys": []},
            }
    else:
        need = [it for it in items if it.get("done") is True]
        if not need:
            return {
                "answer": empty_undo,
                "mode": "done",
                "reload": False,
                "done": {"on": False, "id": "", "label": bulk_label, "keys": []},
            }
    keys = _apply_ask_done(data, need[0], on, extra=need[1:])
    labels = [str(it.get("label") or "Untitled") for it in need]
    if len(labels) == 1:
        head = labels[0]
    else:
        head = f"{len(labels)} {bulk_label}"
    note = f"Marked Done: {head}." if on else f"Undone: {head}."
    return {
        "answer": note,
        "mode": "done",
        "reload": True,
        "done": {
            "on": on,
            "id": str(need[0].get("id") or ""),
            "label": head,
            "keys": keys,
        },
    }


def _follow_up_due_list_toggle(data: dict, on: bool) -> dict | None:
    return _toggle_item_list(
        data,
        on,
        _follow_up_due_list_items(data),
        "Follow-up due list is already Done.",
        "Follow-up due list is not marked Done.",
        "Follow-up due cases",
    )


def _might_toggle_done(ql: str, regex_action: str = "") -> bool:
    if regex_action:
        return True
    ql = (ql or "").strip().lower()
    if not ql:
        return False
    if re.search(r"\b(is|was|are)\b.{0,40}\b(done|complete|finished)\b", ql) and not re.search(
        r"\b(mark|set|tick|check)\b", ql
    ):
        return False
    if re.search(r"\b(what|whats|which|who|when|where|why|how)\b", ql) and not re.search(
        r"\b(mark|set|tick|check off|undo)\b", ql
    ):
        return False
    return bool(
        re.search(
            r"\b(done|undo|undone|complete|completed|finished|finish|check(ed)? off|"
            r"tick(ed)?|strike|crossed off|wrap(ped)? up|all set|clear(ed)?)\b",
            ql,
        )
    )


def _parse_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def ask_done_catalog(data: dict) -> list[dict]:
    rows = []
    for section, item in walk_briefing_items(data):
        if not ask_can_complete(section, item):
            continue
        rid = str(item.get("id") or "")
        leftover = rid in ("plan-follow", "plan-slack", "plan-mail", "plan-close") or rid.startswith(
            "plan-follow-"
        )
        rows.append(
            {
                "id": rid,
                "section": _section_key(section),
                "label": str(item.get("label") or "").strip()[:120],
                "case": _case_num(item),
                "done": item.get("done") is True,
                "kind": (
                    "clock-leftover"
                    if leftover
                    else ("calendar" if _is_google_cal_event(item) else "row")
                ),
            }
        )
    return rows


def resolve_done_intent(
    question: str,
    data: dict,
    history: list[dict] | None,
    token: str,
    model: str,
    complete_fn=None,
) -> dict | None:
    catalog = ask_done_catalog(data)
    if not catalog or not token:
        return None
    thread = ""
    if history:
        thread = "\nThread:\n" + json.dumps(history, ensure_ascii=False)
    user = (
        "Catalog of completable rows:\n"
        + json.dumps(catalog, ensure_ascii=False)
        + thread
        + "\nRequest:\n"
        + (question or "").strip()
    )
    fn = complete_fn or express_complete
    try:
        text = fn(token, model, ASK_DONE_SYSTEM, user)
    except Exception:
        return None
    return _parse_json_object(text)


def _normalize_done_section(raw: str) -> str:
    text = (raw or "").strip().lower().replace("’", "'")
    if re.search(r"follow", text):
        return "follow-up due"
    if re.search(r"need(s)? us now|fire", text):
        return "needs us now"
    if text.startswith("slack"):
        return "slack"
    if re.search(r"\b(mail|email|gmail)\b", text):
        return "mail"
    if re.search(r"today.?s plan|day plan", text):
        return "today's plan"
    if "log off" in text:
        return "before you log off"
    return text


def _board_section_items(data: dict, want: str) -> list[dict]:
    want = _normalize_done_section(want)
    items = []
    for section, item in walk_briefing_items(data):
        if not ask_can_complete(section, item):
            continue
        key = _section_key(section)
        plan = is_todays_plan_section(section) or is_plan_title(section)
        if want == "follow-up due":
            if plan:
                continue
            if FOLLOW_SEC_RE.search(key):
                items.append(item)
        elif want == "needs us now":
            if plan:
                continue
            if NOW_SEC_RE.search(key) or "needs us now" in key:
                items.append(item)
        elif want == "slack":
            if plan:
                continue
            if key.startswith("slack"):
                items.append(item)
        elif want == "mail":
            if plan:
                continue
            if key.startswith("mail") or key.startswith("email") or key.startswith("gmail"):
                items.append(item)
        elif want == "today's plan":
            if plan:
                items.append(item)
        elif want == "before you log off":
            if "before you log off" in key:
                items.append(item)
    return items


def _resolve_catalog_ids(data: dict, ids) -> list[str]:
    catalog = ask_done_catalog(data)
    by_id = {str(row.get("id") or ""): row for row in catalog}
    out = []
    seen = set()
    for raw in ids or []:
        text = str(raw or "").strip()
        if text in by_id and text not in seen:
            out.append(text)
            seen.add(text)
            continue
        digits = re.sub(r"\D", "", text)
        if len(digits) < 8:
            continue
        for row in catalog:
            case = str(row.get("case") or "")
            rid = str(row.get("id") or "")
            if case == digits and rid and rid not in seen:
                out.append(rid)
                seen.add(rid)
                break
    return out


def _items_for_ids(data: dict, ids: list[str]) -> list[dict]:
    want = set(ids)
    found = []
    for section, item in walk_briefing_items(data):
        rid = str(item.get("id") or "")
        if rid in want and ask_can_complete(section, item):
            found.append(item)
    return found


def _ids_are_follow_clock(ids: list[str]) -> bool:
    return bool(ids) and all(
        str(i) == "plan-follow" or str(i).startswith("plan-follow") for i in ids
    )


def _prefer_follow_list(question: str, intent: dict) -> bool:
    ql = (question or "").lower()
    if _asks_todays_plan(ql) or _ask_case_digits(question or ""):
        return False
    scope = str(intent.get("scope") or "").lower()
    section = _normalize_done_section(str(intent.get("section") or ""))
    ids = [str(x) for x in (intent.get("ids") or [])]
    case_ids = [
        i
        for i in ids
        if str(i).startswith("follow-") or (len(re.sub(r"\D", "", i)) >= 8 and not str(i).startswith("plan-"))
    ]
    if scope == "item" and case_ids and not _ids_are_follow_clock(ids):
        return False
    if scope == "section" and section == "follow-up due":
        return True
    if _asks_follow_up_due_list(ql) and not case_ids:
        return True
    if _ids_are_follow_clock(ids) and re.search(r"follow", ql):
        return True
    return False


def _apply_done_intent(
    data: dict, question: str, intent: dict, regex_action: str
) -> dict | None:
    action = str(intent.get("action") or "").strip().lower()
    if action not in ("done", "undo"):
        return None
    on = action == "done"
    ask = str(intent.get("ask") or "").strip()
    scope = str(intent.get("scope") or "").strip().lower()
    section = _normalize_done_section(str(intent.get("section") or ""))
    if _prefer_follow_list(question, intent):
        bulk = _follow_up_due_list_toggle(data, on)
        if bulk:
            return bulk
    if scope == "section" and section:
        items = _board_section_items(data, section)
        label = {
            "follow-up due": "Follow-up due cases",
            "needs us now": "Needs us now cases",
            "slack": "Slack rows",
            "mail": "Mail rows",
            "today's plan": "Today's plan rows",
            "before you log off": "log-off rows",
        }.get(section, "rows")
        bulk = _toggle_item_list(
            data,
            on,
            items,
            f"{label} already Done.",
            f"{label} are not marked Done.",
            label,
        )
        if bulk:
            return bulk
        if ask:
            return {"answer": ask, "mode": "done-ask", "reload": False}
        return None
    ids = _resolve_catalog_ids(data, intent.get("ids"))
    if _ids_are_follow_clock(ids) and _prefer_follow_list(question, {"ids": ids, "section": section}):
        bulk = _follow_up_due_list_toggle(data, on)
        if bulk:
            return bulk
    items = _items_for_ids(data, ids)
    if not items:
        if ask:
            return {"answer": ask, "mode": "done-ask", "reload": False}
        if regex_action:
            return None
        return {
            "answer": "Which row should I mark? Name the meeting, case, or the Follow-up due list.",
            "mode": "done-ask",
            "reload": False,
        }
    if len(items) > 1 and not _ask_case_digits(question or "") and scope != "item":
        return _toggle_item_list(
            data,
            on,
            items,
            "Those rows are already Done.",
            "Those rows are not marked Done.",
            "rows",
        )
    return _toggle_item_list(
        data,
        on,
        items,
        f"Already Done: {items[0].get('label') or 'that row'}.",
        f"That is not marked Done: {items[0].get('label') or 'that row'}.",
        str(items[0].get("label") or "that row"),
    )


def try_ask_done(
    question: str,
    data: dict,
    history: list[dict] | None = None,
    token: str = "",
    model: str = "",
    complete_fn=None,
) -> dict | None:
    ql = (question or "").lower()
    action = wants_done_toggle(ql)
    if not action:
        prev = _last_user_ask(history)
        if wants_done_toggle(prev) and _looks_like_row_pick(ql):
            action = wants_done_toggle(prev)
    if token and _might_toggle_done(ql, action):
        intent = resolve_done_intent(question, data, history, token, model, complete_fn)
        if intent:
            applied = _apply_done_intent(data, question, intent, action)
            if applied is not None:
                return applied
    if not action:
        return None
    on = action == "done"
    blob = (question or "") + " " + _history_blob(history)
    q_nums = set(re.findall(r"\d{8,}", blob))
    case_num = _ask_case_digits(blob)
    this_case = _ask_case_digits(question)
    list_intent = _asks_follow_up_due_list(ql) and not _asks_todays_plan(ql)
    if list_intent and not this_case:
        bulk = _follow_up_due_list_toggle(data, on)
        if bulk:
            return bulk
    ranked = []
    for section, item in walk_briefing_items(data):
        if not ask_can_complete(section, item):
            continue
        score = score_item(ask_tokens(question), question, section, item)
        item_nums = set(
            re.findall(
                r"\d{8,}",
                " ".join(
                    str(item.get(k) or "")
                    for k in ("caseNumber", "label", "id", "detail", "summary")
                ),
            )
        )
        overlap = q_nums & item_nums
        if len(q_nums) >= 2:
            score += 18 * len(overlap)
        elif case_num and case_num in (
            str(item.get("caseNumber") or "") + str(item.get("label") or "") + str(item.get("id") or "")
        ):
            score += 50
        if is_todays_plan_section(section):
            if list_intent:
                score -= 40
            else:
                score += 8
                if _asks_todays_plan(ql):
                    score += 10
                if re.search(r"\bfollow", ql) and not _asks_follow_up_due_list(ql):
                    score += 14
        elif list_intent and FOLLOW_SEC_RE.search(_section_key(section)):
            score += 40
        if action == "undo" and item.get("done") is True:
            score += 24
        if action == "done" and item.get("done") is not True:
            score += 6
        if _is_google_cal_event(item) and is_todays_plan_section(section):
            if re.search(r"\b(meeting|event|calendar|lunch|dinner|standup|invite)\b", ql):
                score += 16
            else:
                score += 4
        if score:
            ranked.append((score, section, item))
    if not ranked:
        return {
            "answer": (
                "I can mark Done on Today's plan (including those Google Calendar meetings), "
                "plus cases, Slack, and mail. I could not match that row."
            ),
            "mode": "done-miss",
            "reload": False,
        }
    ranked.sort(key=lambda row: -row[0])
    top = ranked[0][0]
    close = [row for row in ranked if row[0] >= max(8, top - 4)]
    if len(close) > 1 and close[1][0] >= top - 2:
        labels = [str(row[2].get("label") or "Untitled") for row in close[:3]]
        return {
            "answer": "Which one — " + "; ".join(labels) + "?",
            "mode": "done-ask",
            "reload": False,
        }
    _score, section, item = ranked[0]
    if top < 6 and not case_num:
        return {
            "answer": "Which row should I mark? Name the meeting, case, or plan block.",
            "mode": "done-ask",
            "reload": False,
        }
    already = item.get("done") is True
    if on and already:
        return {
            "answer": f"Already Done: {item.get('label') or 'that row'}.",
            "mode": "done",
            "reload": False,
            "done": {"on": True, "id": item.get("id") or "", "label": item.get("label") or "", "keys": []},
        }
    if (not on) and item.get("done") is not True:
        return {
            "answer": f"That is not marked Done: {item.get('label') or 'that row'}.",
            "mode": "done",
            "reload": False,
            "done": {"on": False, "id": item.get("id") or "", "label": item.get("label") or "", "keys": []},
        }
    keys = _apply_ask_done(data, item, on)
    label = str(item.get("label") or "that row")
    if on:
        note = f"Marked Done: {label}."
        if _is_google_cal_event(item):
            note += " Still on Today's plan — reminder off, calendar event kept."
    else:
        note = f"Undone: {label}."
    return {
        "answer": note,
        "mode": "done",
        "reload": True,
        "done": {"on": on, "id": item.get("id") or "", "label": label, "keys": keys},
    }


def parse_stamp(stamp: str, tzname: str):
    stamp = (stamp or "").strip()
    if len(stamp) < 15:
        return None
    try:
        dt = datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=zone_for_math(tzname))
    except ValueError:
        return None


_STAMP_DAY_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\b",
    re.I,
)
_STAMP_MONTH = {
    name: idx
    for idx, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        1,
    )
}


def briefing_tz(data: dict):
    """Shift zone only. An empty name means the model still has to resolve it."""
    tzname = str((data or {}).get("timezone") or "").strip()
    if not tzname:
        return "", zoneinfo_or_local()
    try:
        return tzname, ZoneInfo(tzname)
    except Exception:
        return "", zoneinfo_or_local()


def tz_short_for(tzname: str, fallback: str = "") -> str:
    t = str(tzname or "")
    try:
        abbr = (datetime.now(ZoneInfo(t)).tzname() or "").strip()
    except Exception:
        return fallback
    return abbr or fallback


def ask_view_tz(data: dict, body: dict | None = None) -> tuple[str, str]:
    shift_name, _ = briefing_tz(data)
    clock = "shift"
    view = ""
    short = ""
    if isinstance(body, dict):
        clock = str(body.get("clock") or body.get("planClock") or "shift").strip().lower()
        view = str(body.get("viewZone") or body.get("viewTimezone") or "").strip()
        short = str(body.get("viewShort") or body.get("viewTimezoneShort") or "").strip()
    if clock == "local" and view:
        try:
            ZoneInfo(view)
            return view, short or tz_short_for(view, "")
        except Exception:
            pass
    return (
        shift_name,
        short
        or str((data or {}).get("timezoneShort") or "").strip()
        or tz_short_for(shift_name, ""),
    )


def apply_ask_view(data: dict, body: dict | None = None) -> None:
    view, short = ask_view_tz(data, body)
    data["_viewTz"] = view
    data["_viewShort"] = short


def view_tz_name(data: dict) -> str:
    return str(data.get("_viewTz") or data.get("timezone") or "").strip()


def view_tz_short(data: dict) -> str:
    return str(data.get("_viewShort") or data.get("timezoneShort") or "")


def to_view(dt: datetime, data: dict) -> datetime:
    try:
        return dt.astimezone(ZoneInfo(view_tz_name(data)))
    except Exception:
        return dt


def view_now(data: dict):
    try:
        return datetime.now(ZoneInfo(view_tz_name(data)))
    except Exception:
        return shift_now(data)[0]


def briefing_publish_date(data: dict):
    if not isinstance(data, dict):
        return None
    tzname, tz = briefing_tz(data)
    generated = parse_stamp(str(data.get("generatedAt") or ""), tzname)
    if generated:
        return generated.date()
    stamped = parse_stamp(str(data.get("stamp") or ""), tzname)
    if stamped:
        return stamped.date()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        piles = list(sec.get("items") or [])
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                piles.extend(group.get("items") or [])
        for item in piles:
            if not isinstance(item, dict):
                continue
            start = parse_stamp(str(item.get("startStamp") or ""), tzname)
            if start:
                return start.date()
    match = _STAMP_DAY_RE.search(str(data.get("stamp") or ""))
    if match:
        month = _STAMP_MONTH.get(match.group(1)[:3].lower())
        if month:
            day = int(match.group(2))
            today = datetime.now(tz).date()
            try:
                got = datetime(today.year, month, day, tzinfo=tz).date()
            except ValueError:
                got = None
            if got and got > today:
                try:
                    got = datetime(today.year - 1, month, day, tzinfo=tz).date()
                except ValueError:
                    got = None
            if got:
                return got
    try:
        if PAGE.is_file():
            return datetime.fromtimestamp(PAGE.stat().st_mtime, tz).date()
    except Exception:
        return None
    return None


PLAN_FRESH = timedelta(hours=6)


def plan_generated_at(data: dict) -> datetime | None:
    """When this plan was run. Shift zone if the page has one, otherwise this Mac's clock."""
    if not isinstance(data, dict):
        return None
    raw = str(data.get("generatedAt") or "").strip()
    if len(raw) < 15:
        return None
    try:
        naive = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    name = str(data.get("timezone") or "").strip()
    if name:
        try:
            return naive.replace(tzinfo=ZoneInfo(name))
        except Exception:
            pass
    return naive.replace(tzinfo=datetime.now().astimezone().tzinfo)


def briefing_is_today(data: dict) -> bool:
    """A plan older than 6 hours is not shown. No run time means there is no plan."""
    generated = plan_generated_at(data)
    if generated is None:
        return False
    age = datetime.now(generated.tzinfo) - generated
    return timedelta(minutes=-5) <= age <= PLAN_FRESH


def page_generation_path() -> pathlib.Path:
    return PAGE.parent / ".page-generation"


def served_version_path() -> pathlib.Path:
    return PAGE.parent / ".served-extension-version"


def bump_page_generation() -> None:
    path = page_generation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.6f}\n", encoding="utf-8")


def leftover_before_restart() -> bool:
    path = page_generation_path()
    try:
        gen = float(path.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        return False
    if gen <= 0 or not PAGE.is_file():
        return False
    try:
        return PAGE.stat().st_mtime < gen
    except OSError:
        return False


def apply_extension_version() -> None:
    version = (os.environ.get("DAY_PLANNER_EXTENSION_VERSION") or "").strip()
    path = served_version_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = ""
    try:
        if path.is_file():
            prev = path.read_text(encoding="utf-8").strip()
    except OSError:
        prev = ""
    if version:
        path.write_text(version + "\n", encoding="utf-8")
        if prev != version:
            bump_page_generation()
        return
    if "--restart" in sys.argv:
        bump_page_generation()


def published_page_is_current() -> bool:
    if not PAGE.is_file():
        return False
    if leftover_before_restart():
        return False
    try:
        return briefing_is_today(load_page_briefing())
    except Exception:
        return False


def live_page_html() -> bytes:
    return PAGE.read_bytes() if published_page_is_current() else unpublished_page_html()


def load_live_briefing() -> dict:
    if leftover_before_restart():
        raise FileNotFoundError("planner page not published yet")
    data = load_page_briefing()
    if not briefing_is_today(data):
        raise FileNotFoundError("planner page not published yet")
    return data


def plan_section_title(title: str) -> bool:
    head = title.split(" / ", 1)[0].lower()
    return head.startswith("today") or "rest-of-day" in head


def is_meeting(item: dict) -> bool:
    if not item or item.get("autoBreak") or item.get("kind") == "break":
        return False
    kind = str(item.get("kind") or "").lower()
    if kind in ("meeting", "event"):
        return True
    return bool(item.get("eventId") or item.get("joinUrl") or item.get("htmlLink"))


def shift_now(data: dict):
    tzname = str(data.get("timezone") or "").strip()
    try:
        tz = ZoneInfo(tzname) if tzname else zoneinfo_or_local()
    except Exception:
        tz = zoneinfo_or_local()
        tzname = ""
    return datetime.now(tz), tzname


def fmt_hm(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.strftime('%M')} {dt.strftime('%p')}"


def fmt_day_clock(dt: datetime) -> str:
    return f"{dt.strftime('%A')}, {dt.strftime('%b')} {dt.day} · {fmt_hm(dt)}"


def fmt_when_range(start: datetime, end: datetime) -> str:
    if start.strftime("%p") == end.strftime("%p"):
        sh = start.hour % 12 or 12
        eh = end.hour % 12 or 12
        return f"{sh}:{start.strftime('%M')}–{eh}:{end.strftime('%M')} {end.strftime('%p')}"
    return f"{fmt_hm(start)}–{fmt_hm(end)}"


def clock_rsvp(item: dict) -> str:
    raw = str(item.get("rsvp") or "").strip()
    key = raw.lower()
    if key in ("accepted", "declined", "tentative"):
        return raw
    if key in ("needsaction", "needs_action", "none"):
        return "needsAction"
    return raw


def pack_clock_item(row: dict, data: dict) -> dict:
    start = to_view(row["start"], data)
    end = to_view(row["end"], data)
    out = {
        "label": row["label"],
        "when": fmt_when_range(start, end),
        "kind": row["kind"],
        "calendar": row["calendar"],
    }
    rsvp = row.get("rsvp") or ""
    if row["calendar"]:
        out["rsvp"] = rsvp if rsvp else "needsAction"
    elif rsvp:
        out["rsvp"] = rsvp
    return out


def ask_clock(data: dict, now: datetime | None = None) -> dict:
    live, tzname = shift_now(data)
    if now is None:
        now = live
    elif now.tzinfo is None:
        now = now.replace(tzinfo=zone_for_math(tzname))
    short = view_tz_short(data)
    stamp = fmt_day_clock(to_view(now, data))
    if short:
        stamp += " " + short
    rows = []
    for _section, item in walk_briefing_items(data):
        if not item or item.get("assembled") is True:
            continue
        if item.get("done") is True:
            continue
        if str(item.get("kind") or "").lower() == "assembled":
            continue
        start = parse_stamp(item.get("startStamp") or "", tzname)
        end = parse_stamp(item.get("endStamp") or "", tzname)
        if not start or not end:
            continue
        cal = is_meeting(item)
        kind = str(item.get("kind") or "").lower()
        if kind == "break" or item.get("autoBreak"):
            cal = False
            kind = "break"
        elif not kind:
            kind = "meeting" if cal else "plan"
        rows.append(
            {
                "label": item.get("label") or "Untitled",
                "start": start,
                "end": end,
                "kind": kind,
                "calendar": cal,
                "rsvp": clock_rsvp(item) if cal else "",
            }
        )
    rows.sort(key=lambda row: (row["start"], row["end"]))
    in_progress = [pack_clock_item(row, data) for row in rows if row["start"] <= now < row["end"]]
    later = [row for row in rows if row["start"] > now]
    later_cal = [row for row in later if row["calendar"]]
    return {
        "now": stamp,
        "timezone": view_tz_name(data),
        "timezoneShort": short,
        "inProgress": in_progress,
        "next": pack_clock_item(later[0], data) if later else None,
        "nextCalendar": pack_clock_item(later_cal[0], data) if later_cal else None,
        "laterCalendar": [pack_clock_item(row, data) for row in later_cal[1:5]],
    }


def timed_plan_rows(data: dict):
    now, tzname = shift_now(data)
    rows = []
    for section, item in walk_briefing_items(data):
        if item.get("autoBreak") or item.get("kind") == "break":
            continue
        if item.get("done") is True:
            continue
        if not plan_section_title(section):
            continue
        start = parse_stamp(item.get("startStamp") or "", tzname)
        end = parse_stamp(item.get("endStamp") or "", tzname)
        if not start or not end:
            continue
        rows.append((start, end, section, item))
    rows.sort(key=lambda row: row[0])
    return now, rows


def answer_clock(data: dict) -> str:
    now, timed = timed_plan_rows(data)
    current = [row for row in timed if row[0] <= now < row[1]]
    upcoming = [row for row in timed if row[0] > now]
    zshort = view_tz_short(data)
    now_s = fmt_hm(to_view(now, data)) + ((" " + zshort) if zshort else "")
    lines = [f"Now is {now_s}."]
    if current:
        _, end, _, item = current[0]
        left = max(0, int((end - now).total_seconds() // 60))
        lines.append("On the clock: " + (item.get("label") or "this block") + f" ({left} min left).")
    if upcoming:
        start, _, _, item = upcoming[0]
        mins = max(0, int((start - now).total_seconds() // 60))
        when = fmt_hm(to_view(start, data))
        lines.append(f"Next: {item.get('label')} at {when} (in {mins} min).")
    elif not current:
        lines.append("Nothing else timed on Today's plan after now.")
    return " ".join(lines)


def answer_meetings(data: dict) -> str:
    now, timed = timed_plan_rows(data)
    upcoming = [row for row in timed if row[1] > now and is_meeting(row[3])]
    if not upcoming:
        return "No more meetings on Today's plan after now."
    lines = ["Next meetings:"]
    for start, _, _, item in upcoming[:6]:
        when = fmt_hm(to_view(start, data))
        lines.append(f"{when} — {item.get('label') or 'Untitled'}.")
    extra = len(upcoming) - 6
    if extra > 0:
        lines.append(f"Plus {extra} more on Today's plan.")
    return " ".join(lines)


def answer_identity(data: dict) -> str:
    name = data.get("name") or "this engineer"
    title = data.get("title") or ""
    mgr = data.get("manager") or ""
    facts = [str(fact) for fact in (data.get("facts") or []) if fact]
    bits = [f"This page is for {name}" + (f" — {title}" if title else "") + "."]
    if mgr:
        bits.append(f"Manager: {mgr}.")
    bits.append(
        f"{data.get('daypartLabel') or data.get('daypart') or 'This run'} · refreshed {data.get('stamp') or 'unknown'}."
    )
    if facts:
        bits.append(" ".join(facts[:2]))
    open_cases = data.get("openCases")
    need_you = data.get("needYou")
    if open_cases is not None:
        extra = f", {need_you} need you" if need_you is not None else ""
        bits.append(f"On this page: {open_cases} open cases{extra}.")
    return " ".join(bits)


def now_items(data: dict) -> list[tuple[str, dict]]:
    found = []
    for section, item in walk_briefing_items(data):
        if "needs us now" in section.lower():
            found.append((section, item))
    return found


def pack_ask_item(section: str, item: dict, data: dict | None = None) -> dict:
    row = {
        "section": section,
        "label": item.get("label") or "Untitled",
        "detail": item.get("detail") or "",
        "kind": item.get("kind") or "",
    }
    if item.get("id"):
        row["id"] = item.get("id")
    if item.get("caseNumber"):
        row["caseNumber"] = item.get("caseNumber")
    when = ""
    tzname = (data or {}).get("timezone") or ""
    if data and tzname:
        start = parse_stamp(item.get("startStamp") or "", tzname)
        end = parse_stamp(item.get("endStamp") or "", tzname)
        if start and end:
            when = fmt_when_range(to_view(start, data), to_view(end, data))
        elif start:
            when = fmt_hm(to_view(start, data))
    if not when:
        when = item.get("when") or ""
    if when:
        row["when"] = when
    if item.get("status"):
        row["status"] = item.get("status")
    if item.get("eventId"):
        row["eventId"] = item.get("eventId")
    if item.get("startStamp"):
        row["startStamp"] = item.get("startStamp")
    if item.get("done") is True:
        row["done"] = True
    elif item.get("done") is False:
        row["done"] = False
    peek = item.get("peek") if isinstance(item.get("peek"), dict) else {}
    summary = str(item.get("summary") or peek.get("summary") or "").strip()
    if summary:
        row["summary"] = summary[:800]
    chrono = peek.get("chronology") if isinstance(peek.get("chronology"), list) else item.get("chronology")
    if isinstance(chrono, list) and chrono:
        last = chrono[-1] if isinstance(chrono[-1], dict) else None
        if last:
            row["lastBeat"] = {
                "when": last.get("when") or "",
                "who": last.get("who") or "",
                "kind": last.get("kind") or "",
                "text": str(last.get("text") or "")[:180],
            }
    latest = []
    for beat in (item.get("activity") or [])[:2]:
        if not isinstance(beat, dict):
            continue
        latest.append(
            {
                "when": beat.get("when") or "",
                "who": beat.get("who") or "",
                "kind": beat.get("kind") or "",
                "text": str(beat.get("text") or "")[:180],
            }
        )
    if latest:
        row["latest"] = latest
    if ask_can_complete(section, item):
        row["canDone"] = True
    return row


def ask_page_slim(data: dict) -> dict:
    """Board + Clock already carry FIRE/meetings. Do not send Peek dumps to Planner Buddy."""
    out = {
        key: data.get(key)
        for key in (
            "name",
            "title",
            "manager",
            "stamp",
            "daypart",
            "daypartLabel",
            "timezone",
            "timezoneShort",
            "shiftStart",
            "shiftEnd",
            "facts",
            "openCases",
            "needYou",
        )
    }
    out["displayTimezone"] = view_tz_name(data)
    out["displayTimezoneShort"] = view_tz_short(data)
    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        slim_sec = {
            "title": title,
            "tone": sec.get("tone"),
            "empty": sec.get("empty"),
            "items": [
                pack_ask_item(title, item, data)
                for item in (sec.get("items") or [])
                if isinstance(item, dict)
            ],
        }
        groups = []
        for group in sec.get("groups") or []:
            if not isinstance(group, dict):
                continue
            groups.append(
                {
                    "title": group.get("title"),
                    "items": [
                        pack_ask_item(title, item, data)
                        for item in (group.get("items") or [])
                        if isinstance(item, dict)
                    ],
                }
            )
        if groups:
            slim_sec["groups"] = groups
        sections.append(slim_sec)
    out["sections"] = sections
    return out


def section_items(data: dict, needle: str) -> list[tuple[str, dict]]:
    found = []
    for section, item in walk_briefing_items(data):
        if needle in section.lower():
            found.append((section, item))
    return found


def ask_board(data: dict) -> dict:
    fire = [pack_ask_item(sec, item, data) for sec, item in now_items(data)]
    follow = [pack_ask_item(sec, item, data) for sec, item in section_items(data, "follow-up due")]
    return {
        "fire": fire,
        "followUp": follow,
        "needYou": data.get("needYou"),
        "openCases": data.get("openCases"),
        "fireMeans": "Needs us now. Burning, on fire, FIRE, and urgent are this list — not Clock, not openCases.",
    }


def answer_fire(data: dict, with_summary: bool = False) -> str:
    rows = now_items(data)
    if not rows:
        return (
            "Nothing is burning on this page. Needs us now is empty. "
            "Open cases are the rest of the queue, not FIRE."
        )
    heads = [fmt_item(sec, item, with_summary=with_summary) for sec, item in rows[:8]]
    lead = "Burning (Needs us now)"
    if len(rows) == 1:
        return lead + ": " + heads[0]
    return lead + f", {len(rows)}: " + " ".join(heads)


def first_in_section(data: dict, needle: str) -> tuple[str, dict] | None:
    for section, item in walk_briefing_items(data):
        if needle in section.lower():
            return section, item
    return None


def answer_next_action(data: dict) -> str:
    lines = []
    now_rows = now_items(data)
    if now_rows:
        lines.append("Do this next: " + fmt_item(now_rows[0][0], now_rows[0][1]))
    meet = first_in_section(data, "customer asked for a meeting")
    if meet:
        lines.append("Also: " + fmt_item(meet[0], meet[1]))
    slack = first_in_section(data, "slack")
    if slack:
        lines.append("Slack: " + fmt_item(slack[0], slack[1]))
    clock = answer_clock(data)
    if clock:
        lines.append(clock)
    if not lines:
        return "Nothing on this page is stamped as the next move."
    return " ".join(lines[:4])


def wants_clock(ql: str) -> bool:
    if wants_meetings(ql) or wants_next_action(ql):
        return False
    return bool(
        re.search(
            r"\b(on the clock|this hour|right now|next meeting|next event|coming up)\b",
            ql,
        )
    )


def wants_meetings(ql: str) -> bool:
    return bool(re.search(r"\bmeetings\b|\bcalendar\b|on my cal", ql))


def wants_next_action(ql: str) -> bool:
    return bool(
        re.search(
            r"\b(what to do|do next|first thing|start with|where do i start|what should i (do|work on)|what now)\b",
            ql,
        )
    )


def wants_identity(ql: str) -> bool:
    return bool(re.search(r"\b(who am i|my manager|what.?s my shift|who is this|my shift|how many|open cases)\b", ql))


def wants_now(ql: str) -> bool:
    return bool(
        re.search(
            r"\b(burn(ing)?|on fire|(what's |whats |what is )?(on )?fire|fires?\b|"
            r"urgent|sev-?1|need(s)? (us|me) now|needs us now)\b",
            ql,
        )
    )


def wants_depth(ql: str) -> bool:
    return bool(re.search(r"\b(why|status|summary|peek|history|said|last|unanswered)\b", ql))


def answer_briefing(question: str, data: dict) -> dict:
    q = (question or "").strip()
    ql = q.lower()
    if not q:
        return {
            "answer": "Ask about this page — cases, Slack, mail, or the clock.",
            "mode": "empty",
            "hits": 0,
        }
    if wants_now(ql):
        rows = now_items(data)
        return {
            "answer": answer_fire(data, with_summary=wants_depth(ql)),
            "mode": "now",
            "hits": len(rows),
        }
    if wants_identity(ql) and not re.search(r"\d{6,}", q):
        return {"answer": answer_identity(data), "mode": "identity", "hits": 1}
    if wants_next_action(ql):
        return {"answer": answer_next_action(data), "mode": "next", "hits": 1}
    if wants_meetings(ql):
        return {"answer": answer_meetings(data), "mode": "meetings", "hits": 1}
    if wants_clock(ql):
        return {"answer": answer_clock(data), "mode": "clock", "hits": 1}
    qtoks = ask_tokens(q)
    ranked = []
    for section, item in walk_briefing_items(data):
        score = score_item(qtoks, q, section, item)
        if score:
            ranked.append((score, section, item))
    ranked.sort(key=lambda row: -row[0])
    if not ranked:
        if re.search(r"\bmeet", ql):
            return {"answer": answer_meetings(data), "mode": "meetings", "hits": 1}
        if re.search(r"\bnext\b", ql):
            return {"answer": answer_next_action(data), "mode": "next", "hits": 1}
        return {
            "answer": "That’s not on this page.",
            "mode": "miss",
            "hits": 0,
        }
    depth = wants_depth(ql)
    top = ranked[:3]
    parts = [fmt_item(sec, item, with_summary=(depth and i == 0)) for i, (_, sec, item) in enumerate(top)]
    return {"answer": " ".join(parts), "mode": "search", "hits": len(top)}


def _email_from(value: str) -> str:
    if not value or value == "None":
        return ""
    m = re.search(r"<([^>]+@[^>]+)>", value)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"([^\s<>]+@[^\s<>]+)", value)
    return (m.group(1).strip().lower() if m else "")


def _norm_rsvp(status: str) -> str:
    cleaned = re.sub(r"\s*\(organizer\)\s*", "", status or "", flags=re.I).strip().lower()
    if cleaned in ("accepted", "declined", "tentative"):
        return cleaned
    if cleaned in ("needsaction", "needs_action", "none"):
        return "needsAction"
    return ""


def _self_rsvp(chunk: str, owner: str) -> str:
    """This engineer's reply from Attendee Details. Those lines wrap, one person per line."""
    if not owner:
        return ""
    owner_email = owner.strip().lower()
    mark = re.search(r"(?m)^  Attendee Details: (.*)$", chunk)
    if not mark:
        return ""
    parts = []
    first = mark.group(1).strip()
    if first and first not in ("None",):
        parts.append(first)
    for line in chunk[mark.end():].splitlines():
        if not line.strip():
            continue
        cont = re.match(r"^[ \t]+(\S+@\S+:\s*.+)$", line)
        if cont:
            parts.append(cont.group(1).strip())
            continue
        break
    for part in parts:
        email, _, status = part.partition(":")
        if email.strip().lower() == owner_email:
            return _norm_rsvp(status)
    return ""


def _field(block: str, name: str) -> str:
    m = re.search(rf"(?m)^  {re.escape(name)}: (.*)$", block)
    if not m:
        return ""
    val = m.group(1).strip()
    if val in ("None", "No Description", "No Location"):
        return ""
    return val


def _to_stamp(raw: str, tzname: str, *, allow_all_day: bool = False) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "T" not in raw:
        if not allow_all_day or not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return None
        raw = raw + "T00:00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    tz = zone_for_math(tzname)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    local = dt.astimezone(tz)
    return local.strftime("%Y%m%dT%H%M%S")


def parse_calendar_events(
    text: str,
    tzname: str,
    *,
    skip_planner_titles: bool = True,
    allow_all_day: bool = False,
) -> tuple[str, list[dict]]:
    owner = ""
    m = re.search(r"for\s+([^\s:]+@[^\s:]+)", text or "")
    if m:
        owner = m.group(1).strip().lower()
    events = []
    chunks = re.split(r"\n(?=- \")", text or "")
    for chunk in chunks:
        head = re.match(
            r'- "(?P<title>.*)" \(Starts: (?P<start>[^,]*), Ends: (?P<end>[^)]*)\)',
            chunk.strip(),
        )
        if not head:
            continue
        title = (head.group("title") or "").strip()
        low = title.lower()
        if skip_planner_titles and (low in SKIP_TITLES or any(low.startswith(p) for p in SKIP_PREFIXES)):
            continue
        start = _to_stamp(head.group("start"), tzname, allow_all_day=allow_all_day)
        end = _to_stamp(head.group("end"), tzname, allow_all_day=allow_all_day)
        if not start or not end:
            continue
        event_id = ""
        html_link = ""
        id_line = re.search(r"(?m)^  ID:\s+(\S+)(?:\s*\|\s*Link:\s+(\S+))?", chunk)
        if id_line:
            event_id = id_line.group(1)
            html_link = id_line.group(2) or ""
        if not event_id:
            if not allow_all_day:
                continue
            event_id = "assembled-" + re.sub(r"[^A-Za-z0-9_-]+", "-", title.lower())[:40] + "-" + start
        join = _field(chunk, "Meeting Link")
        location = _field(chunk, "Location")
        if not join:
            for url in re.findall(r"https://[^\s\"'<>]+", chunk):
                if re.search(r"meet\.google|zoom\.us|teams\.microsoft|salesforce\.com/plus|epochapp\.com", url, re.I):
                    join = url.rstrip(").,]")
                    break
        organizer = _email_from(_field(chunk, "Organizer") or _field(chunk, "Creator"))
        rsvp = _self_rsvp(chunk, owner)
        invite = bool(owner and organizer and organizer != owner)
        events.append(
            {
                "id": "cal-" + re.sub(r"[^A-Za-z0-9_-]+", "-", event_id)[:80],
                "kind": "meeting",
                "label": title,
                "detail": location,
                "eventId": event_id,
                "htmlLink": html_link,
                "joinUrl": join,
                "startStamp": start,
                "endStamp": end,
                "invite": invite,
                "rsvp": rsvp,
            }
        )
    events.sort(key=lambda e: e.get("startStamp") or "")
    return owner, events


CAL_MAX_EVENTS = 50
CAL_FETCH = 50
CAL_MAX_MINUTES = 12 * 60
CAL_SHIFT_START = (8, 0)
CAL_SHIFT_END = (17, 0)
CAL_PAD_HOURS = 2


def keep_planner_event(event: dict) -> bool:
    title = (event.get("label") or "").strip().lower()
    if title in SKIP_TITLES or any(title.startswith(p) for p in SKIP_PREFIXES):
        return False
    return True


def _parse_hm(raw, fallback: tuple[int, int]) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(raw or "").strip())
    if not m:
        return fallback
    return int(m.group(1)), int(m.group(2))


def shift_pad_stamps(
    tzname: str,
    shift_start: str | None = None,
    shift_end: str | None = None,
) -> tuple[str, str]:
    """Shift login−2h through logout+2h, as YYYYMMDDTHHMMSS wall stamps."""
    tz = zone_for_math(tzname)
    now = datetime.now(tz)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    have_hours = bool(str(shift_start or "").strip() and str(shift_end or "").strip())
    if not have_hours and not str(tzname or "").strip():
        lo = day
        hi = day + timedelta(days=1)
        return lo.strftime("%Y%m%dT%H%M%S"), hi.strftime("%Y%m%dT%H%M%S")
    sh, sm = _parse_hm(shift_start, CAL_SHIFT_START)
    eh, em = _parse_hm(shift_end, CAL_SHIFT_END)
    lo = day.replace(hour=sh, minute=sm) - timedelta(hours=CAL_PAD_HOURS)
    hi = day.replace(hour=eh, minute=em) + timedelta(hours=CAL_PAD_HOURS)
    if hi <= lo:
        hi = hi + timedelta(days=1)
    return lo.strftime("%Y%m%dT%H%M%S"), hi.strftime("%Y%m%dT%H%M%S")


def day_window(tzname: str, time_min: str | None = None, time_max: str | None = None) -> tuple[str, str, str]:
    """Return (query_start, query_end, keep_until).

    Query the civil day so Refresh can still see important meetings outside
    shift±2h. bound_events then keeps shift±2h, plus ids the gather marked important.
    """
    tz = zone_for_math(tzname)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    if time_min and time_max:
        return time_min, time_max, time_max
    return day_start.isoformat(), day_end.isoformat(), day_end.isoformat()


def _stamp_from_iso(raw: str, tzname: str) -> str:
    dt = datetime.fromisoformat(raw)
    tz = zone_for_math(tzname)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz).strftime("%Y%m%dT%H%M%S")


def _duration_min(start: str, end: str) -> int:
    try:
        a = datetime.strptime(start, "%Y%m%dT%H%M%S")
        b = datetime.strptime(end, "%Y%m%dT%H%M%S")
    except ValueError:
        return 0
    return max(0, int((b - a).total_seconds() // 60))


def bound_events(
    events: list[dict],
    window_min: str,
    keep_until: str,
    tzname: str,
    important_ids: list | None = None,
    shift_start: str | None = None,
    shift_end: str | None = None,
) -> list[dict]:
    pad_lo, pad_hi = shift_pad_stamps(tzname, shift_start, shift_end)
    keep_ids = {str(x) for x in (important_ids or []) if x}
    out = []
    seen = set()
    for event in events:
        event_id = event.get("eventId") or ""
        start = event.get("startStamp") or ""
        end = event.get("endStamp") or ""
        if not event_id or event_id in seen:
            continue
        if not start or not end:
            continue
        overlaps = end > pad_lo and start < pad_hi
        if not overlaps and event_id not in keep_ids:
            continue
        if _duration_min(start, end) > CAL_MAX_MINUTES:
            continue
        if not keep_planner_event(event):
            continue
        if event_id in keep_ids:
            event["important"] = True
        seen.add(event_id)
        out.append(event)
        if len(out) >= CAL_MAX_EVENTS:
            break
    return out


def fetch_primary_events(
    tzname: str,
    time_min: str | None = None,
    time_max: str | None = None,
    important_ids: list | None = None,
    shift_start: str | None = None,
    shift_end: str | None = None,
) -> dict:
    window_min, window_max, keep_until = day_window(tzname, time_min, time_max)
    text = mcp_call(
        "get_events",
        {
            "calendar_id": "primary",
            "detailed": True,
            "max_results": CAL_FETCH,
            "time_min": window_min,
            "time_max": window_max,
        },
    )
    owner, events = parse_calendar_events(text, tzname)
    events = bound_events(
        events,
        window_min,
        keep_until,
        tzname,
        important_ids=important_ids,
        shift_start=shift_start,
        shift_end=shift_end,
    )
    return {
        "ok": True,
        "email": owner,
        "timezone": tzname,
        "timeMin": window_min,
        "timeMax": keep_until,
        "queryMax": window_max,
        "events": events,
        "count": len(events),
        "capped": CAL_MAX_EVENTS,
    }


OMNI_OFF_RE = re.compile(r"\b(pto|vto|sick\s+leave|comp\s*off)\b", re.I)
OMNI_CAL_LINE_RE = re.compile(
    r'- "(?P<summary>[^"]+)"(?P<primary>\s*\(Primary\))?\s*\(ID:\s*(?P<id>[^)]+)\)'
)
ORGCS_MCP_NAMES = ("orgcs", "user-orgcs", "org-cs", "org_cs")
OMNI_ALERT_TEXT = "Omni Is Out Of Adherence. You should be available for this Assembled block."


def omni_alert_message(omni_status: str, assembled_now: str) -> str:
    status = (omni_status or "").strip() or "Offline"
    block = (assembled_now or "").strip()
    if block:
        return f"Omni: {status} · Assembled now: {block}"
    return f"Omni: {status}"


OMNI_SHIFT_GAP = timedelta(hours=3)
OMNI_KIND_TOKENS = {
    "casework": frozenset({"case", "casework"}),
    "chat": frozenset({"chat", "live"}),
    "messaging": frozenset({"messaging", "message"}),
    "voice": frozenset({"voice"}),
    "lunch": frozenset({"lunch", "meal"}),
    "break": frozenset({"break"}),
}
OMNI_WORK_KINDS = frozenset({"casework", "chat", "messaging", "voice"})
OMNI_MEAL_KINDS = frozenset({"lunch", "break"})


def _omni_payload(action: str, reason: str, day: str, **extra) -> dict:
    out = {"ok": True, "action": action, "reason": reason, "day": day}
    out.update(extra)
    return out


def parse_calendar_list(text: str) -> list[dict]:
    rows = []
    for match in OMNI_CAL_LINE_RE.finditer(text or ""):
        summary = (match.group("summary") or "").strip()
        cal_id = (match.group("id") or "").strip()
        if not summary or not cal_id:
            continue
        rows.append(
            {
                "summary": summary,
                "id": cal_id,
                "primary": bool(match.group("primary")),
            }
        )
    return rows


def find_assembled_calendar(rows: list[dict]) -> dict | None:
    """Assembled calendar only. Never primary, team PTO, or holidays."""
    hits = []
    for row in rows:
        summary = (row.get("summary") or "").strip()
        cal_id = str(row.get("id") or "").strip()
        low = summary.lower()
        if "assembled" not in low:
            continue
        if row.get("primary") is True or cal_id.lower() == "primary":
            continue
        if "team pto" in low or "holiday" in low:
            continue
        hits.append(row)
    if not hits:
        return None
    hits.sort(
        key=lambda row: (
            0 if (row.get("summary") or "").lower().startswith("assembled") else 1,
            len(row.get("summary") or ""),
        )
    )
    return hits[0]


def assembled_is_off_title(title: str) -> bool:
    low = (title or "").strip().lower()
    if low in {"pto", "vto", "sick leave", "comp off", "sick"}:
        return True
    return bool(OMNI_OFF_RE.search(title or ""))


def _parse_iso_dt(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        raw = raw + "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_assembled_instants(text: str) -> list[dict]:
    """Parse Assembled get_events only. Keep Casework/Chat/Lunch/Break/PTO."""
    events = []
    for match in re.finditer(
        r'- "(?P<title>.*)" \(Starts: (?P<start>[^,]*), Ends: (?P<end>[^)]*)\)',
        text or "",
    ):
        title = (match.group("title") or "").strip()
        start = _parse_iso_dt(match.group("start"))
        end = _parse_iso_dt(match.group("end"))
        if not title or not start or not end:
            continue
        events.append({"label": title, "start": start, "end": end})
    events.sort(key=lambda row: row["start"])
    return events


def _assembled_clock_parts(dt) -> tuple[int, int, str]:
    h = dt.hour % 12 or 12
    ap = "AM" if dt.hour < 12 else "PM"
    return h, dt.minute, ap


def _assembled_span(start, end) -> str:
    sh, sm, sap = _assembled_clock_parts(start)
    eh, em, eap = _assembled_clock_parts(end)
    left = f"{sh}:{sm:02d}"
    right = f"{eh}:{em:02d}"
    if sap == eap:
        return f"{left}–{right} {eap}"
    return f"{left} {sap}–{right} {eap}"


def _assembled_block_schedule(work: list, tz) -> tuple[str, object, object, str]:
    rows: list[dict] = []
    for ev in sorted(work, key=lambda e: e["start"]):
        lab = str(ev.get("label") or "").strip() or "Block"
        start = ev["start"].astimezone(tz)
        end = ev["end"].astimezone(tz)
        if rows and rows[-1]["label"] == lab and start <= rows[-1]["end"] + timedelta(minutes=2):
            if end > rows[-1]["end"]:
                rows[-1]["end"] = end
            continue
        rows.append({"label": lab, "start": start, "end": end})
    login = min(r["start"] for r in rows)
    logout = max(r["end"] for r in rows)
    try:
        short = _sanitize_mod().fold_tz_abbr(login.tzname() or "")
    except Exception:
        short = str(login.tzname() or "").strip()
    hours = " · ".join(f"{r['label']} {_assembled_span(r['start'], r['end'])}" for r in rows)
    if short:
        hours += " " + short
    return hours, login, logout, short


def fetch_assembled_today(tzname: str) -> dict:
    """Today's Assembled calendar blocks. Shift login/logout is min/max of those blocks."""
    given = str(tzname or "").strip()
    try:
        tz = ZoneInfo(given) if given else timezone.utc
    except Exception:
        tz = timezone.utc
        given = ""
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    try:
        listing = mcp_call("list_calendars", {})
    except Exception:
        return {}
    assembled = find_assembled_calendar(parse_calendar_list(listing))
    cal_id = str((assembled or {}).get("id") or "").strip()
    if not assembled or not cal_id or cal_id.lower() == "primary":
        return {}
    try:
        text = mcp_call(
            "get_events",
            {
                "calendar_id": cal_id,
                "detailed": True,
                "max_results": CAL_FETCH,
                "time_min": start_local.isoformat(),
                "time_max": end_local.isoformat(),
            },
        )
    except Exception:
        return {}
    if re.search(r"no events found|retrieved 0 events", text or "", re.I):
        return {}
    events = parse_assembled_instants(text)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    work = []
    for ev in events:
        if ev["end"] <= start_utc or ev["start"] >= end_utc:
            continue
        if assembled_is_off_title(str(ev.get("label") or "")):
            continue
        work.append(ev)
    if not work:
        return {}
    display = tz
    resolved = given
    if not given:
        src = getattr(work[0]["start"], "tzinfo", None)
        key = str(getattr(src, "key", "") or "").strip()
        if key:
            try:
                display = ZoneInfo(key)
                resolved = key
            except Exception:
                display = src or timezone.utc
        else:
            display = src or timezone.utc
    hours, login, logout, short = _assembled_block_schedule(work, display)
    out = {
        "assembledFromCalendar": True,
        "assembledSchedule": hours,
        "shiftStart": login.strftime("%H:%M"),
        "shiftEnd": logout.strftime("%H:%M"),
    }
    if resolved:
        out["timezone"] = resolved
        if short:
            out["timezoneShort"] = short
    return out


# OrgCS Engineer_Shift__c codes. Unknown codes stay empty so the model resolves them.
ORGCS_SHIFT_ZONES = {
    "PST": ("America/Los_Angeles", "PT"),
    "AMER-PST": ("America/Los_Angeles", "PT"),
    "EST": ("America/New_York", "ET"),
    "AMER-EST": ("America/New_York", "ET"),
    "CST": ("America/Chicago", "CT"),
    "MST": ("America/Denver", "MT"),
    "IST": ("Asia/Kolkata", "IST"),
    "APAC-INDIA": ("Asia/Kolkata", "IST"),
    "JAPAN": ("Asia/Tokyo", "JST"),
    "APAC-ANZ": ("Australia/Sydney", "AEST"),
    "EMEA": ("Europe/London", "GMT"),
    "AMER-LATAM": ("America/Sao_Paulo", "BRT"),
}


def orgcs_shift_zone(code: str) -> tuple[str, str]:
    key = re.sub(r"\s+", "-", str(code or "").strip()).upper()
    return ORGCS_SHIFT_ZONES.get(key) or ("", "")


def _clock_hhmm(hour: str, minute: str, ap: str) -> str:
    h = int(hour) % 12
    if str(ap or "").upper() == "PM":
        h += 12
    return f"{h:02d}:{int(minute or 0):02d}"


def parse_aboutme_hours(text: str) -> tuple[str, str]:
    """Working Hours written on the OrgCS User. Empty when that line is absent."""
    hit = re.search(r"working\s*hours?\s*[:\-]?\s*(.+)", str(text or ""), re.I)
    if not hit:
        return "", ""
    hm = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:-|–|—|to)\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)",
        hit.group(1),
        re.I,
    )
    if not hm:
        return "", ""
    return _clock_hhmm(hm.group(1), hm.group(2), hm.group(3)), _clock_hhmm(hm.group(4), hm.group(5), hm.group(6))


def apply_orgcs_shift(data: dict, code: str, about: str) -> None:
    """OrgCS owns the shift when Engineer_Shift__c is set. Hours come from AboutMe when written."""
    label = str(code or "").strip()
    if not label:
        return
    data["engineerShift"] = label
    zone, short = orgcs_shift_zone(label)
    if zone:
        data["timezone"] = zone
        if short and not str(data.get("timezoneShort") or "").strip():
            data["timezoneShort"] = short
    start, end = parse_aboutme_hours(about)
    if start and end:
        data["shiftStart"] = start
        data["shiftEnd"] = end
        data["shiftHoursFrom"] = "orgcs"


def apply_general_shift_hours(data: dict) -> None:
    """8:00 AM–5:00 PM in the shift zone, only when no source gave hours."""
    if not isinstance(data, dict):
        return
    if str(data.get("shiftStart") or "").strip() and str(data.get("shiftEnd") or "").strip():
        return
    if not str(data.get("timezone") or "").strip():
        return
    data["shiftStart"] = "08:00"
    data["shiftEnd"] = "17:00"
    data["shiftHoursFrom"] = "general"


def apply_live_assembled(data: dict) -> None:
    if not isinstance(data, dict):
        return
    orgcs_owns = bool(str(data.get("engineerShift") or "").strip())
    try:
        got = fetch_assembled_today(str(data.get("timezone") or ""))
        data["calendarFetchOk"] = True
    except Exception as exc:
        data["calendarFetchOk"] = False
        data["calendarFetchError"] = clip(str(exc), 180)
        append_plan_step(kind="log", label="Calendar/Assembled fetch failed · " + data["calendarFetchError"])
        data["assembledFromCalendar"] = False
        data["assembledSchedule"] = ""
        apply_general_shift_hours(data)
        return
    live = bool(got.get("assembledFromCalendar") and got.get("assembledSchedule"))
    data["assembledFromCalendar"] = live
    if live:
        data["assembledSchedule"] = got["assembledSchedule"]
        if not orgcs_owns:
            if not str(data.get("shiftStart") or "").strip():
                data["shiftStart"] = got["shiftStart"]
                data["shiftEnd"] = got["shiftEnd"]
                data["shiftHoursFrom"] = "assembled"
            if not str(data.get("timezone") or "").strip() and got.get("timezone"):
                data["timezone"] = got["timezone"]
            if got.get("timezoneShort") and not str(data.get("timezoneShort") or "").strip():
                data["timezoneShort"] = got["timezoneShort"]
    else:
        data["assembledSchedule"] = ""
    apply_general_shift_hours(data)


def assembled_shift_cluster(now, events: list[dict]):
    if not events:
        return None
    clusters: list[list[dict]] = []
    cur: list[dict] = []
    for ev in events:
        if not cur:
            cur = [ev]
            continue
        if ev["start"] <= cur[-1]["end"] + OMNI_SHIFT_GAP:
            cur.append(ev)
        else:
            clusters.append(cur)
            cur = [ev]
    if cur:
        clusters.append(cur)
    for cluster in clusters:
        login = min(ev["start"] for ev in cluster)
        logout = max(ev["end"] for ev in cluster)
        if login <= now <= logout:
            return cluster, login, logout
    return None


def assembled_schedule_kinds(title: str) -> list[str]:
    """Assembled title → schedule kinds. live-queue counts as chat."""
    low = (title or "").strip().lower()
    kinds: list[str] = []
    if "voice" in low:
        kinds.append("voice")
    if "messaging" in low or re.search(r"\bmessages?\b", low):
        kinds.append("messaging")
    if "chat" in low or re.search(r"\blive\b", low):
        kinds.append("chat")
    if "casework" in low or "case work" in low or re.search(r"\bcases?\b", low):
        kinds.append("casework")
    if "lunch" in low or "meal" in low or "dinner" in low or "breakfast" in low:
        kinds.append("lunch")
    if re.search(r"\bbreak\b", low):
        kinds.append("break")
    return kinds


def assembled_kinds_now(now, cluster: list[dict]) -> list[str]:
    covering = [ev for ev in cluster if ev["start"] <= now <= ev["end"]]
    if not covering:
        return []
    meal: list[str] = []
    work: list[str] = []
    for ev in covering:
        for kind in assembled_schedule_kinds(str(ev.get("label") or "")):
            if kind in OMNI_MEAL_KINDS and kind not in meal:
                meal.append(kind)
            elif kind in OMNI_WORK_KINDS and kind not in work:
                work.append(kind)
    return meal or work


def _omni_display_tz():
    name = ""
    try:
        data = load_page_briefing()
        name = str((data or {}).get("timezone") or "").strip()
    except Exception:
        name = ""
    try:
        return zoneinfo_or_local(name)
    except Exception:
        return timezone.utc


def assembled_now_schedule(now, cluster: list[dict], tz) -> str:
    covering = [ev for ev in (cluster or []) if ev["start"] <= now <= ev["end"]]
    if not covering:
        return ""
    rows: list[dict] = []
    for ev in sorted(covering, key=lambda e: e["start"]):
        lab = str(ev.get("label") or "").strip() or "Block"
        start = ev["start"].astimezone(tz)
        end = ev["end"].astimezone(tz)
        if rows and rows[-1]["label"] == lab and start <= rows[-1]["end"] + timedelta(minutes=2):
            if end > rows[-1]["end"]:
                rows[-1]["end"] = end
            continue
        rows.append({"label": lab, "start": start, "end": end})
    try:
        short = _sanitize_mod().fold_tz_abbr(rows[0]["start"].tzname() or "")
    except Exception:
        short = str(rows[0]["start"].tzname() or "").strip()
    text = " · ".join(f"{r['label']} {_assembled_span(r['start'], r['end'])}" for r in rows)
    if short:
        text += " " + short
    return text


def omni_presence_label(records: list) -> str:
    if not records:
        return "Offline"
    rec = records[0] if isinstance(records[0], dict) else {}
    status = rec.get("ServicePresenceStatus") if isinstance(rec.get("ServicePresenceStatus"), dict) else {}
    label = str(status.get("MasterLabel") or rec.get("MasterLabel") or "").strip()
    return label or "Offline"


def omni_label_tokens(label: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (label or "").lower()))


def omni_is_available_style(low: str) -> bool:
    return low.startswith("available") or "chat online" in low


def omni_meal_only(kinds: list[str]) -> bool:
    """Assembled Lunch / Break / Dinner covering now — not Casework/Chat."""
    listed = [str(k) for k in (kinds or []) if k]
    return bool(listed) and all(k in OMNI_MEAL_KINDS for k in listed)


def omni_is_offline_status(label: str) -> bool:
    low = (label or "").strip().lower()
    return (not low) or "offline" in low or low == "end of work"


def omni_in_adherence(label: str, kinds: list[str]) -> bool:
    """Inclusion, not an exact label.

    In adherence = Screen Sharing (any block, never OOA) or the status for
    the Assembled block covering now. Work: Available* / Chat Online that
    includes that channel (combos count). Lunch / Break / Dinner: that meal
    label, or Offline / no Omni row / End of Work. Busy is never in
    adherence on a work block.
    """
    low = (label or "").strip().lower()
    if re.search(r"screen[\s-]*sharing", low):
        return True
    if omni_meal_only(kinds) and omni_is_offline_status(label):
        return True
    if not low:
        return False
    if low == "busy" or low.startswith("busy ") or low.startswith("busy-"):
        return False
    if not kinds:
        return False
    tokens = omni_label_tokens(low)
    meal = [kind for kind in kinds if kind in OMNI_MEAL_KINDS]
    work = [kind for kind in kinds if kind in OMNI_WORK_KINDS]
    if meal and not work:
        need: set[str] = set()
        for kind in meal:
            need |= OMNI_KIND_TOKENS[kind]
        return bool(tokens & need)
    if not work or not omni_is_available_style(low):
        return False
    for kind in work:
        if not (tokens & OMNI_KIND_TOKENS[kind]):
            return False
    return True


def omni_out_of_adherence(records: list, kinds: list[str]) -> bool:
    if omni_meal_only(kinds):
        if not records:
            return False
        rec = records[0] if isinstance(records[0], dict) else {}
        status = rec.get("ServicePresenceStatus") if isinstance(rec.get("ServicePresenceStatus"), dict) else {}
        label = str(status.get("MasterLabel") or rec.get("MasterLabel") or "").strip()
        if omni_is_offline_status(label):
            return False
        return not omni_in_adherence(label, kinds)
    if not records:
        return True
    rec = records[0] if isinstance(records[0], dict) else {}
    status = rec.get("ServicePresenceStatus") if isinstance(rec.get("ServicePresenceStatus"), dict) else {}
    label = str(status.get("MasterLabel") or rec.get("MasterLabel") or "").strip()
    return not omni_in_adherence(label, kinds)


def parse_orgcs_user_id(text: str) -> str:
    raw = (text or "").strip()
    obj = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            obj = loaded
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        ident = obj.get("identity") if isinstance(obj.get("identity"), dict) else obj
        for key in ("userId", "user_id", "id"):
            uid = ident.get(key) if isinstance(ident, dict) else None
            if isinstance(uid, str) and uid.startswith("005"):
                return uid
        uid = obj.get("userId")
        if isinstance(uid, str) and uid.startswith("005"):
            return uid
    m = re.search(r"\b(005[A-Za-z0-9]{12,18})\b", raw)
    return m.group(1) if m else ""


def parse_orgcs_email(text: str) -> str:
    raw = (text or "").strip()
    obj = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            obj = loaded
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        ident = obj.get("identity") if isinstance(obj.get("identity"), dict) else obj
        bag = ident if isinstance(ident, dict) else obj
        for key in ("email", "Email", "username", "userName", "Username", "user_email", "userEmail"):
            val = str(bag.get(key) or "").strip()
            if "@" in val:
                return val
        for child in bag.values():
            if not isinstance(child, dict):
                continue
            for key in ("email", "Email", "username", "Username"):
                val = str(child.get(key) or "").strip()
                if "@" in val:
                    return val
    m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", raw)
    return m.group(0) if m else ""


def google_workspace_email() -> str:
    """Primary Google calendar id is the signed-in Workspace address."""
    try:
        listing = mcp_call("list_calendars", {})
    except Exception:
        return ""
    for row in parse_calendar_list(listing):
        cal_id = str(row.get("id") or "").strip()
        if row.get("primary") and "@" in cal_id:
            return cal_id
    return ""


def resolve_engineer_email(seeded: dict | None = None) -> str:
    """Email for the GUS User lookup. Google Workspace is the signed-in address."""
    found = google_workspace_email()
    if "@" in found:
        return found
    if isinstance(seeded, dict):
        cached = str(seeded.get("email") or "").strip()
        if "@" in cached:
            return cached
    blob = load_identity_cache()
    cached = str(blob.get("email") or "").strip()
    if "@" in cached:
        return cached
    info = ""
    try:
        info = mcp_call_named(ORGCS_MCP_NAMES, "getUserInfo", {}, timeout=20)
    except Exception:
        info = ""
    email = parse_orgcs_email(info)
    if "@" in email:
        return email
    uid = parse_orgcs_user_id(info)
    if not uid or "'" in uid:
        return ""
    try:
        text = mcp_call_named(
            ORGCS_MCP_NAMES,
            "soqlQuery",
            {"q": "SELECT Email, Username FROM User WHERE Id = '%s' LIMIT 1" % uid},
            timeout=20,
        )
    except Exception:
        return ""
    rec = (parse_soql_records(text) or [{}])[0]
    for key in ("Email", "Username"):
        val = str(rec.get(key) or "").strip()
        if "@" in val:
            return val
    return ""


def fetch_orgcs_identity(data: dict) -> None:
    """Name, title, and manager from OrgCS User. Every run. Does not use a previous page."""
    if not isinstance(data, dict) or os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return
    try:
        info = mcp_call_named(ORGCS_MCP_NAMES, "getUserInfo", {}, timeout=20)
    except Exception:
        return
    uid = parse_orgcs_user_id(info)
    name = title = manager = ""
    if uid and "'" not in uid:
        try:
            text = mcp_call_named(
                ORGCS_MCP_NAMES,
                "soqlQuery",
                {"q": "SELECT Name, Title, Email, Username, AboutMe, Engineer_Shift__c, Manager.Name FROM User WHERE Id = '%s' LIMIT 1" % uid},
                timeout=20,
            )
        except Exception:
            text = ""
        recs = parse_soql_records(text)
        rec = recs[0] if recs else {}
        name = str(rec.get("Name") or "").strip()
        title = str(rec.get("Title") or "").strip()
        mgr = rec.get("Manager") if isinstance(rec.get("Manager"), dict) else {}
        manager = str((mgr or {}).get("Name") or "").strip()
        apply_orgcs_shift(data, str(rec.get("Engineer_Shift__c") or ""), str(rec.get("AboutMe") or ""))
        for key in ("Email", "Username"):
            val = str(rec.get(key) or "").strip()
            if "@" in val:
                data["email"] = val
                break
    if not str(data.get("email") or "").strip():
        found = resolve_engineer_email(data)
        if found:
            data["email"] = found
    if name:
        data["name"] = name
    if title:
        data["title"] = title
    if manager:
        data["manager"] = manager
    if name or title or manager:
        persist_identity(data)


def parse_soql_records(text: str) -> list:
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []
    if isinstance(obj, dict):
        records = obj.get("records")
        if isinstance(records, list):
            return [row for row in records if isinstance(row, dict)]
        inner = obj.get("result")
        if isinstance(inner, dict) and isinstance(inner.get("records"), list):
            return [row for row in inner["records"] if isinstance(row, dict)]
    return []


def parse_omni_presence(text: str) -> tuple[str, list]:
    """('ok', records) only when SOQL JSON actually parsed. Never treat junk as Offline."""
    raw = (text or "").strip()
    if not raw:
        return "error", []
    obj = None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return "error", []
        else:
            return "error", []
    if not isinstance(obj, dict):
        return "error", []
    records = obj.get("records")
    if not isinstance(records, list):
        inner = obj.get("result")
        if isinstance(inner, dict) and isinstance(inner.get("records"), list):
            records = inner["records"]
        else:
            return "error", []
    return "ok", [row for row in records if isinstance(row, dict)]


def omni_presence_via_mcp() -> tuple[str, list]:
    info = mcp_call_named(ORGCS_MCP_NAMES, "getUserInfo", {}, timeout=20)
    user_id = parse_orgcs_user_id(info)
    if not user_id:
        raise RuntimeError("no_user")
    soql = (
        "SELECT Id, UserId, IsCurrentState, StatusStartDate, "
        "ServicePresenceStatus.MasterLabel, ServicePresenceStatus.DeveloperName "
        "FROM UserServicePresence "
        f"WHERE UserId = '{user_id}' AND IsCurrentState = true "
        "LIMIT 5"
    )
    text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": soql}, timeout=20)
    status, records = parse_omni_presence(text)
    if status != "ok":
        raise RuntimeError("unparsed")
    return user_id, records


def omni_check_payload() -> dict:
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    try:
        listing = mcp_call("list_calendars", {})
    except Exception:
        return _omni_payload("skip", "assembled_unreachable", day)
    assembled = find_assembled_calendar(parse_calendar_list(listing))
    cal_id = str((assembled or {}).get("id") or "").strip()
    if not assembled or not cal_id or cal_id.lower() == "primary":
        return _omni_payload("skip", "no_calendar", day)
    try:
        text = mcp_call(
            "get_events",
            {
                "calendar_id": cal_id,
                "detailed": True,
                "max_results": CAL_FETCH,
                "time_min": (now - timedelta(hours=18)).isoformat(),
                "time_max": (now + timedelta(hours=18)).isoformat(),
            },
        )
    except Exception:
        return _omni_payload("skip", "assembled_unreachable", day)
    if re.search(r"no events found|retrieved 0 events", text or "", re.I):
        return _omni_payload("skip", "empty", day)
    events = parse_assembled_instants(text)
    if not events:
        return _omni_payload("skip", "empty", day)
    if any(
        assembled_is_off_title(str(ev.get("label") or ""))
        and ev["start"] <= now <= ev["end"]
        for ev in events
    ):
        return _omni_payload("skip", "pto", day)
    hit = assembled_shift_cluster(now, events)
    if not hit:
        return _omni_payload("skip", "off_shift", day)
    cluster, login, logout = hit
    if any(assembled_is_off_title(str(ev.get("label") or "")) for ev in cluster):
        return _omni_payload("skip", "pto", day, shiftEndMs=int(logout.timestamp() * 1000))
    kinds = assembled_kinds_now(now, cluster)
    if not kinds:
        return _omni_payload("skip", "off_shift", day, shiftEndMs=int(logout.timestamp() * 1000))
    assembled_now = assembled_now_schedule(now, cluster, _omni_display_tz())
    extra = {
        "shiftEndMs": int(logout.timestamp() * 1000),
        "scheduleKinds": kinds,
        "assembledNow": assembled_now,
    }
    try:
        _user_id, records = omni_presence_via_mcp()
    except Exception:
        return _omni_payload("need_omni", "omni_mcp_unavailable", day, **extra)
    label = omni_presence_label(records)
    extra["omniStatus"] = label
    if omni_out_of_adherence(records, kinds):
        extra.update(
            title="Omni Is Out Of Adherence",
            message=omni_alert_message(label, assembled_now),
        )
        return _omni_payload("alert", "out_of_adherence", day, **extra)
    return _omni_payload("ok", "in_adherence", day, **extra)


def omni_check() -> dict:
    if not OMNI_LOCK.acquire(blocking=False):
        return {"ok": True, "action": "skip", "reason": "busy", "day": ""}
    try:
        return omni_check_payload()
    except Exception:
        return {"ok": True, "action": "skip", "reason": "error", "day": ""}
    finally:
        OMNI_LOCK.release()


def normalize_runner(raw: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "", (raw or "").strip().lower())[:40]


def parent_pid(pid: int) -> int:
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid="], text=True, stderr=subprocess.DEVNULL)
        return int(out.strip() or "0")
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


KNOWN_RUNNER_EXES = {
    "opencode": "opencode",
    "claude": "claude",
    "cursor-agent": "cursor",
    "cursor": "cursor",
    "aider": "aider",
    "codex": "codex",
    "gemini": "gemini",
    "qwen": "qwen",
    "amp": "amp",
}


def infer_runner_from_process() -> str:
    pid = os.getppid()
    for _ in range(12):
        if pid <= 1:
            break
        args = process_args(pid)
        if not args:
            pid = parent_pid(pid)
            continue
        tokens = args.split()
        exe = pathlib.Path(tokens[0]).name.lower() if tokens else ""
        if exe == "agent" and "cursor" in args.lower():
            return "cursor"
        if exe in KNOWN_RUNNER_EXES:
            return KNOWN_RUNNER_EXES[exe]
        for tok in tokens[1:]:
            name = pathlib.Path(tok).name.lower()
            if name in KNOWN_RUNNER_EXES:
                return KNOWN_RUNNER_EXES[name]
        pid = parent_pid(pid)
    return ""


def detect_runner(existing: str = "") -> str:
    env = normalize_runner(os.environ.get("DAY_PLANNER_RUNNER", ""))
    if env:
        return env
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDECODE"):
        return "claude"
    if os.environ.get("CURSOR_TRACE_ID") or os.environ.get("CURSOR_AGENT"):
        return "cursor"
    if os.environ.get("OPENCODE") or os.environ.get("OPENCODE_SERVER"):
        return "opencode"
    walked = infer_runner_from_process()
    if walked:
        return walked
    return normalize_runner(existing)


def claude_settings_env() -> dict:
    merged = {}
    for path in settings_files():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        env = data.get("env") if isinstance(data, dict) else {}
        if not isinstance(env, dict):
            continue
        for key, val in env.items():
            if isinstance(val, str) and val:
                merged[str(key)] = val
    for key in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "NODE_EXTRA_CA_CERTS",
        "CLAUDE_BIN",
        "DEVBAR_BIN",
    ):
        val = os.environ.get(key)
        if val:
            merged[key] = val
    return merged


def first_existing_file(paths) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return str(path)
    return ""


PLAN_RUNNER_SPECS = (
    {
        "id": "claude",
        "label": "Claude Code",
        "env": "CLAUDE_BIN",
        "names": ("claude", "claude.exe"),
        "extra": (
            str(HOME / ".local" / "bin" / "claude"),
            str(HOME / ".local" / "bin" / "claude.exe"),
            str(HOME / ".claude" / "local" / "claude"),
            str(HOME / ".claude" / "local" / "claude.exe"),
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            str(HOME / "AppData" / "Roaming" / "npm" / "claude.cmd"),
            str(HOME / "AppData" / "Roaming" / "npm" / "claude.exe"),
            str(HOME / "AppData" / "Local" / "Programs" / "claude.exe"),
        ),
    },
    {
        "id": "cursor",
        "label": "Cursor Agent",
        "env": "CURSOR_AGENT_BIN",
        "names": ("cursor-agent", "cursor-agent.exe"),
        "extra": (str(HOME / ".local" / "bin" / "cursor-agent"),),
    },
    {
        "id": "opencode",
        "label": "OpenCode",
        "env": "OPENCODE_BIN",
        "names": ("opencode", "opencode.exe"),
        "extra": (
            str(HOME / ".aisuite" / "bin" / "opencode"),
            str(HOME / ".local" / "bin" / "opencode"),
        ),
    },
    {
        "id": "codex",
        "label": "Codex",
        "env": "CODEX_BIN",
        "names": ("codex", "codex.exe"),
        "extra": (str(HOME / ".local" / "bin" / "codex"),),
    },
    {
        "id": "gemini",
        "label": "Gemini CLI",
        "env": "GEMINI_BIN",
        "names": ("gemini", "gemini.exe"),
        "extra": (str(HOME / ".local" / "bin" / "gemini"),),
    },
    {
        "id": "aider",
        "label": "Aider",
        "env": "AIDER_BIN",
        "names": ("aider", "aider.exe"),
        "extra": (str(HOME / ".local" / "bin" / "aider"),),
    },
)


def canonicalize_runner_id(raw: str) -> str:
    name = normalize_runner(raw)
    return {
        "cursor-agent": "cursor",
        "agent": "cursor",
        "claude-code": "claude",
        "claudecode": "claude",
        "gemini-cli": "gemini",
    }.get(name, name)


def login_path_dirs() -> list[str]:
    """Dirs to search for a runner CLI. Chrome's native host often has a short PATH."""
    raw = [part for part in (os.environ.get("PATH") or "").split(os.pathsep) if part]
    home = pathlib.Path.home()
    if os.name == "nt":
        try:
            import winreg

            for root, sub in (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            ):
                try:
                    with winreg.OpenKey(root, sub) as key:
                        val, _ = winreg.QueryValueEx(key, "Path")
                except OSError:
                    continue
                raw.extend(part for part in os.path.expandvars(str(val or "")).split(";") if part)
        except Exception:
            pass
        local = os.environ.get("LOCALAPPDATA") or ""
        appdata = os.environ.get("APPDATA") or ""
        raw.extend(
            str(path)
            for path in (
                home / ".local" / "bin",
                home / ".claude" / "local",
                pathlib.Path(local) / "Programs" if local else None,
                pathlib.Path(appdata) / "npm" if appdata else None,
            )
            if path
        )
    else:
        raw.extend(
            [
                str(home / ".local" / "bin"),
                str(home / ".claude" / "local"),
                "/opt/homebrew/bin",
                "/usr/local/bin",
            ]
        )
    out: list[str] = []
    seen: set[str] = set()
    for part in raw:
        key = os.path.normcase(os.path.normpath(part))
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def which_named(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    suffixes = ("",)
    if os.name == "nt" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        suffixes = ("", ".exe", ".cmd", ".bat")
    for directory in login_path_dirs():
        for suffix in suffixes:
            candidate = pathlib.Path(directory) / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return ""


def find_spec_bin(spec: dict) -> str:
    hinted = (os.environ.get(str(spec.get("env") or "")) or "").strip()
    named = [which_named(name) for name in spec.get("names") or ()]
    extras = [str(path) for path in spec.get("extra") or ()]
    return first_existing_file([hinted, *named, *extras])


def runner_catalog() -> list[dict]:
    rows = []
    for spec in PLAN_RUNNER_SPECS:
        path = find_spec_bin(spec)
        row = {
            "id": spec["id"],
            "label": spec["label"],
            "installed": bool(path),
            "bin": path,
            "ready": bool(path),
            "note": "",
        }
        if spec["id"] == "cursor" and path:
            ok, note = cursor_agent_ready(path)
            row["ready"] = ok
            row["note"] = "" if ok else note
        rows.append(row)
    return rows


def public_runner_catalog() -> list[dict]:
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "installed": row["installed"],
            "ready": row.get("ready", row["installed"]),
            "note": row.get("note") or "",
        }
        for row in runner_catalog()
    ]


def resolve_plan_runner(requested: str) -> dict:
    wanted = canonicalize_runner_id(requested)
    if not wanted:
        claude = installed_runner("claude")
        if claude:
            return claude
        raise RuntimeError(
            "Pick a runner on the Token screen (Claude Code, Cursor Agent, OpenCode, …)."
        )
    for row in runner_catalog():
        if row["id"] != wanted:
            continue
        if not row["installed"]:
            raise RuntimeError(f"{row['label']} is not installed.")
        return row
    raise RuntimeError(
        "Pick a runner on the Token screen (Claude Code, Cursor Agent, OpenCode, …)."
    )


def find_claude_bin() -> str:
    for row in runner_catalog():
        if row["id"] == "claude":
            return str(row.get("bin") or "")
    return ""


_CURSOR_STATUS = {"at": 0.0, "ok": False, "note": "not checked"}


def cursor_agent_ready(binary: str) -> tuple[bool, str]:
    if not binary:
        return False, "not installed"
    now = time.monotonic()
    if _CURSOR_STATUS["at"] and now - _CURSOR_STATUS["at"] < 20:
        return bool(_CURSOR_STATUS["ok"]), str(_CURSOR_STATUS["note"])
    try:
        out = subprocess.check_output(
            [binary, "status"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
    except Exception:
        out = ""
    low = (out or "").strip().lower()
    ok = bool(low) and "not logged in" not in low
    note = "logged in" if ok else "not logged in"
    _CURSOR_STATUS.update({"at": now, "ok": ok, "note": note})
    return ok, note


def installed_runner(runner_id: str) -> dict | None:
    wanted = canonicalize_runner_id(runner_id)
    for row in runner_catalog():
        if row["id"] == wanted and row.get("installed") and row.get("bin"):
            return row
    return None


def looks_like_cursor_model(model_id: str) -> bool:
    low = (model_id or "").strip().lower()
    if not low:
        return False
    if low.startswith("grok") or low.startswith("gpt-4o") or "anthropic." in low:
        return False
    if low.startswith("cursor") or "thinking" in low:
        return True
    if re.match(r"^(gpt-5|sonnet-4|opus-4|composer)", low):
        return True
    return False


def uses_express_proxy(runner_id: str) -> bool:
    return canonicalize_runner_id(runner_id) == "claude"


def opencode_cli_model(raw: str) -> str:
    """OpenCode -m is provider/model. This Mac's OpenCode provider is llmgw."""
    try:
        requested = sanitize_model(raw)
    except RuntimeError:
        requested = ""
    if not requested:
        return "llmgw/gpt-4o"
    if "/" in requested:
        return requested
    return "llmgw/" + requested


def resolve_tool_runner(picked: dict, model_id: str) -> tuple[dict, str]:
    """Keep the runner the engineer picked. Do not move the run onto Claude Code."""
    if picked["id"] == "cursor":
        ready, _note = cursor_agent_ready(str(picked.get("bin") or ""))
        if not ready:
            raise RuntimeError("Cursor Agent is not logged in. Run `agent login`.")
    return picked, ""


def planner_empty_mcp_file() -> pathlib.Path:
    """Planner Claude does not call MCP. An empty config keeps those schemas out of the prompt."""
    path = pathlib.Path("/tmp/planner-mcp-empty.json")
    path.write_text('{"mcpServers":{}}\n', encoding="utf-8")
    return path


def plan_command(runner: dict, chosen: str) -> list[str]:
    binary = str(runner.get("bin") or "")
    name = str(runner.get("id") or "")
    work = str(plan_cli_cwd())
    system_file = PLAN_SYSTEM_OVERRIDE or (PLAN_SYSTEM_FILE if PLAN_SYSTEM_FILE.is_file() else None)
    if name == "claude":
        cmd = [
            binary,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            str(planner_empty_mcp_file()),
            "--tools",
            "Read,Write",
        ]
        if system_file:
            cmd.extend(["--append-system-prompt-file", str(system_file)])
        cmd.extend(
            [
                "--model",
                cli_flag_model(chosen),
                "--effort",
                "low" if is_opus_model(chosen) else "medium",
                "--dangerously-skip-permissions",
                "--add-dir",
                str(SKILL_ROOT / "scripts"),
                "--add-dir",
                str(SKILL_ROOT / "page"),
            ]
        )
        return cmd
    if name == "cursor":
        return [
            binary,
            "--print",
            "--output-format",
            "stream-json",
            "--force",
            "--trust",
            "--approve-mcps",
            "--workspace",
            work,
            "--add-dir",
            str(SKILL_ROOT / "scripts"),
            "--add-dir",
            str(SKILL_ROOT / "page"),
            "--model",
            chosen,
        ]
    if name == "opencode":
        return [
            binary,
            "run",
            "--format",
            "json",
            "--print-logs",
            "--log-level",
            "INFO",
            "--auto",
            "--dir",
            work,
            "-m",
            opencode_cli_model(chosen),
            compact_plan_prompt(name),
        ]
    if name == "codex":
        return [binary, "exec", "--json", compact_plan_prompt(name)]
    if name == "gemini":
        return [binary, "--prompt", compact_plan_prompt(name)]
    if name == "aider":
        return [binary, "--yes", "--message", compact_plan_prompt(name)]
    return [binary]


def find_devbar_bin() -> str:
    hinted = (os.environ.get("DEVBAR_BIN") or "").strip()
    which = shutil.which("devbar") or shutil.which("devbar.exe") or ""
    candidates = [
        hinted,
        which,
        str(HOME / "Applications" / "devbar.app" / "Contents" / "MacOS" / "devbar"),
        "/Applications/devbar.app/Contents/MacOS/devbar",
        str(HOME / ".local" / "bin" / "devbar"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return which


def gateway_token_from_devbar() -> str:
    binary = find_devbar_bin()
    if not binary:
        raise RuntimeError("DevBar is not installed. Paste a gateway token instead.")
    out = subprocess.check_output([binary, "auth", "claude"], text=True, timeout=20)
    tok = (out or "").strip()
    if not tok:
        raise RuntimeError("DevBar returned an empty gateway token")
    return tok


def resolve_gateway_token(override: str = "") -> str:
    pasted = (override or "").strip()
    if pasted:
        return pasted
    return gateway_token_from_devbar()


def ssl_ctx():
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    env = claude_settings_env()
    ctx = ssl.create_default_context()
    ca = env.get("NODE_EXTRA_CA_CERTS") or os.environ.get("NODE_EXTRA_CA_CERTS") or ""
    if ca and pathlib.Path(ca).is_file():
        ctx.load_verify_locations(ca)
    _SSL_CTX = ctx
    return ctx


def ask_page_json() -> dict:
    data = dict(load_live_briefing())
    data.pop("copyright", None)
    data.pop("bridgeUrl", None)
    data.pop("refreshPrompt", None)
    return data


def gateway_headers(token: str, openai: bool = False) -> dict:
    env = claude_settings_env()
    headers = {
        "Content-Type": "application/json",
        "x-api-key": token,
        "Authorization": "Bearer " + token,
    }
    if not openai:
        headers["anthropic-version"] = "2023-06-01"
    custom = env.get("ANTHROPIC_CUSTOM_HEADERS") or ""
    if ":" in custom:
        key, val = custom.split(":", 1)
        headers[key.strip()] = val.strip()
    return headers


def gateway_base() -> str:
    base = (claude_settings_env().get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    return base


def sanitize_model(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if not MODEL_ID_RE.fullmatch(text):
        raise RuntimeError("invalid model")
    return text


def default_model() -> str:
    env = claude_settings_env()
    for key in ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        try:
            value = sanitize_model(env.get(key) or "")
        except RuntimeError:
            value = ""
        if value:
            return value
    return "claude-sonnet-5"


def resolve_model(raw: str) -> str:
    return sanitize_model(raw) or default_model()


def is_cli_model(model_id: str) -> bool:
    low = (model_id or "").strip().lower()
    if not low:
        return False
    if low in {"sonnet", "opus", "haiku", "fable"}:
        return True
    if re.match(r"^(sonnet|opus|haiku|fable)\b", low):
        return True
    if "vertex" in low or low.endswith("-bedrock"):
        return False
    if low.startswith("claude-"):
        return True
    if "anthropic.claude" in low or low.startswith("us.anthropic."):
        return True
    return False


def is_claude_model(model_id: str) -> bool:
    return is_cli_model(model_id)


def is_opus_model(model_id: str) -> bool:
    return bool(re.search(r"opus", (model_id or "").lower()))


def fetch_sidecar_models(chosen: str) -> tuple[str, str]:
    """Throwaway comment/email fetch. Opus compact-kills that session — Claude opus uses sonnet here only."""
    if is_claude_model(chosen) and is_opus_model(chosen):
        return "sonnet", "claude-sonnet-5"
    return cli_flag_model(chosen), chosen


def cli_flag_model(raw: str) -> str:
    """Claude Code's --model flag. Inference still uses the selected gateway id."""
    try:
        requested = sanitize_model(raw)
    except RuntimeError:
        requested = ""
    if requested and is_cli_model(requested):
        return requested
    return "sonnet"


def set_plan_force_model(model_id: str) -> None:
    global PLAN_FORCE_MODEL
    PLAN_FORCE_MODEL = (model_id or "").strip()


def plan_force_model() -> str:
    """Pin Express inference to this run only. After publish, the global model pick can change."""
    if not PLAN_ACTIVE.is_set():
        return ""
    return (PLAN_FORCE_MODEL or "").strip()


def usable_planner_model(model_id: str) -> bool:
    """Chat models the planner can send on Express /v1/messages. Drop routing aliases and non-chat SKUs."""
    mid = (model_id or "").strip()
    if not mid:
        return False
    low = mid.lower()
    if re.search(
        r"(bedrock|vertex|embedding|whisper|tts|dall-e|dalle|imagen|moderation|"
        r"transcri|codec|auto-router|auto-model|-1m\b|codex)",
        low,
    ):
        return False
    if low.startswith(("us.", "global.", "anthropic.")):
        return False
    if "anthropic.claude" in low:
        return False
    return True


def _model_version_tuple(model_id: str) -> tuple:
    nums = tuple(int(part) for part in re.findall(r"\d+", model_id or ""))
    return nums if nums else (0,)


def preferred_planner_default(models: list[dict]) -> str:
    ids = [str(row.get("id") or "") for row in models if isinstance(row, dict) and row.get("id")]
    if not ids:
        return default_model()
    for needle in ("claude-sonnet-5", "claude-sonnet-4-6"):
        for mid in ids:
            if mid.lower() == needle or mid.lower().startswith(needle):
                return mid
    grok = [mid for mid in ids if re.search(r"(?:^|/)grok-", mid, re.I)]
    if grok:
        return max(grok, key=lambda mid: _model_version_tuple(mid.lower()))
    sonnet = [mid for mid in ids if "sonnet" in mid.lower()]
    if sonnet:
        return max(sonnet, key=lambda mid: _model_version_tuple(mid.lower()))
    return ids[0]


def _gateway_model_rows(token: str) -> list:
    """Every page of GET /v1/models. No pinned SKUs."""
    base = gateway_base().rstrip("/") + "/v1/models"
    headers = gateway_headers(token)
    rows: list = []
    cursor = ""
    seen: set[str] = set()
    for _ in range(8):
        url = base if not cursor else base + "?after=" + urllib.parse.quote(cursor)
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"gateway HTTP {exc.code}") from None
        if isinstance(payload, list):
            chunk, more, nxt = payload, False, ""
        elif isinstance(payload, dict):
            chunk = payload.get("data")
            if not isinstance(chunk, list):
                chunk = []
            more = payload.get("has_more") is True
            nxt = str(payload.get("last_id") or payload.get("next_cursor") or payload.get("next_page_token") or "")
        else:
            chunk, more, nxt = [], False, ""
        rows.extend(chunk)
        if not more:
            break
        if not nxt and chunk:
            last = chunk[-1]
            nxt = str(last.get("id") or last.get("model") or last.get("name") or last) if isinstance(last, dict) else str(last)
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    return rows


def list_gateway_models(token: str) -> list[dict]:
    rows = _gateway_model_rows(token)
    models = []
    seen = set()

    def add(model_id: str, owned_by: str = "", extra: dict | None = None):
        try:
            mid = sanitize_model(model_id)
        except RuntimeError:
            return
        if not mid or mid in seen:
            return
        seen.add(mid)
        item = {"id": mid, "label": mid}
        if owned_by:
            item["ownedBy"] = owned_by
        extra = extra or {}
        if extra.get("max_input_tokens") is not None:
            item["maxInputTokens"] = extra.get("max_input_tokens")
        if extra.get("max_output_tokens") is not None:
            item["maxOutputTokens"] = extra.get("max_output_tokens")
        models.append(item)

    env = claude_settings_env()
    for key in ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        add(env.get(key) or "")
    for row in rows:
        if isinstance(row, str):
            add(row)
            continue
        if not isinstance(row, dict):
            continue
        add(row.get("id") or row.get("model") or row.get("name") or "", row.get("owned_by") or "", row)
    models = [row for row in models if usable_planner_model(str(row.get("id") or ""))]
    models.sort(key=lambda row: str(row.get("id") or "").lower())
    if not models:
        add(default_model())
        models = [row for row in models if usable_planner_model(str(row.get("id") or ""))]
        if not models:
            add(default_model())
    return models


def ask_now_line(data: dict) -> str:
    tzname = view_tz_name(data)
    short = view_tz_short(data)
    stamp = fmt_day_clock(view_now(data))
    if short:
        stamp += " " + short
    return f"Current time in the selected display timezone ({tzname}): {stamp}."


def ask_max_tokens(_model: str = "") -> int:
    return 4096


def uses_completion_tokens(model: str) -> bool:
    low = (model or "").strip().lower()
    return bool(
        low.startswith("gpt-5")
        or low.startswith("gpt-4.1")
        or re.match(r"^o[134]", low)
        or "thinking" in low
    )


def _gateway_text_pieces(value, parts: list) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            parts.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _gateway_text_pieces(item, parts)
        return
    if not isinstance(value, dict):
        return
    kind = str(value.get("type") or "").lower()
    if kind in {"thinking", "reasoning", "redacted_thinking", "thought"}:
        return
    if kind in {"text", "output_text"}:
        _gateway_text_pieces(value.get("text") or value.get("output_text"), parts)
        return
    for key in ("text", "output_text", "content", "value", "message", "parts"):
        if key in value:
            _gateway_text_pieces(value.get(key), parts)
            return


def extract_gateway_text(body: object) -> str:
    parts: list[str] = []
    if isinstance(body, str):
        return body.strip()
    if not isinstance(body, dict):
        return ""
    _gateway_text_pieces(body.get("content"), parts)
    if not parts:
        _gateway_text_pieces(body.get("output_text"), parts)
    if not parts:
        _gateway_text_pieces(body.get("output"), parts)
    if not parts:
        _gateway_text_pieces(body.get("refusal"), parts)
    if not parts:
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            first = choices[0]
            _gateway_text_pieces(first.get("message") or first.get("delta") or first.get("text"), parts)
    if not parts:
        cands = body.get("candidates")
        if isinstance(cands, list) and cands:
            _gateway_text_pieces(cands[0], parts)
    return "\n".join(parts).strip()


def _gateway_json(url: str, payload: dict, token: str, openai: bool = False, timeout: int = 60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=gateway_headers(token, openai=openai),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as resp:
            raw = resp.read().decode()
        err = ""
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        err = f"gateway HTTP {exc.code}"
        try:
            parsed_err = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed_err = {}
        if isinstance(parsed_err, dict):
            detail = parsed_err.get("error")
            if isinstance(detail, dict):
                err = str(detail.get("message") or err)
            elif detail:
                err = str(detail)
            return parsed_err, err
        return {}, (raw[:400] if raw else err)
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        text = (raw or "").strip()
        return ({"output_text": text} if text else {}), err
    return parsed if isinstance(parsed, dict) else {}, err


def _strip_reasoning(payload: dict) -> dict:
    out = dict(payload)
    for key in ("thinking", "reasoning", "reasoning_effort", "reasoningEffort", "output_config"):
        out.pop(key, None)
    extra = out.get("extra_body")
    if isinstance(extra, dict):
        extra = dict(extra)
        extra.pop("reasoning", None)
        extra.pop("thinking", None)
        out["extra_body"] = extra
    return out


def express_complete(token: str, model: str, system: str, user: str) -> str:
    """Same Express model id Planner and Planner Buddy send. Anthropic first, OpenAI if needed."""
    chosen = resolve_model(model)
    max_tok = ask_max_tokens(chosen)
    anth = _strip_reasoning(
        {
            "model": chosen,
            "max_tokens": max_tok,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    )
    body, err = _gateway_json(gateway_base() + "/v1/messages", anth, token, openai=False)
    text = extract_gateway_text(body)
    if text:
        return text
    blob = ((err or "") + " " + json.dumps(body, ensure_ascii=False)).lower()
    if "max_completion_tokens" in blob or (
        "unsupported parameter" in blob and "max_tokens" in blob
    ):
        retry = dict(anth)
        retry.pop("max_tokens", None)
        retry["max_completion_tokens"] = max_tok
        body, err = _gateway_json(gateway_base() + "/v1/messages", retry, token, openai=False)
        text = extract_gateway_text(body)
        if text:
            return text
        blob = ((err or "") + " " + json.dumps(body, ensure_ascii=False)).lower()
    oai = _strip_reasoning(
        {
            "model": chosen,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    )
    if uses_completion_tokens(chosen) or "max_completion_tokens" in blob:
        oai["max_completion_tokens"] = max_tok
    else:
        oai["max_tokens"] = max_tok
    body, err = _gateway_json(gateway_base() + "/v1/chat/completions", oai, token, openai=True)
    text = extract_gateway_text(body)
    if text:
        return text
    oai_blob = ((err or "") + " " + json.dumps(body, ensure_ascii=False)).lower()
    if "max_tokens" in oai_blob and "max_completion_tokens" not in oai:
        oai.pop("max_tokens", None)
        oai["max_completion_tokens"] = max_tok
        body, err = _gateway_json(gateway_base() + "/v1/chat/completions", oai, token, openai=True)
        text = extract_gateway_text(body)
        if text:
            return text
    return ""


def gateway_complete(
    question: str, data: dict, token: str, model: str = "", history: list[dict] | None = None
) -> str:
    chosen = resolve_model(model)
    clock = ask_clock(data)
    board = ask_board(data)
    slim = ask_page_slim(data)
    thread = ""
    if history:
        thread = "\n\nRecent Planner Buddy thread:\n" + json.dumps(history, ensure_ascii=False)
    user = (
        ask_now_line(data)
        + "\n\nBoard JSON (trust this for burning / FIRE / urgent / Needs us now):\n"
        + json.dumps(board, ensure_ascii=False)
        + "\n\nClock JSON (trust this for now/next/calendar meetings only):\n"
        + json.dumps(clock, ensure_ascii=False)
        + thread
        + "\n\nQuestion:\n"
        + question.strip()
        + "\n\nPage JSON:\n"
        + json.dumps(slim, ensure_ascii=False)
    )
    text = express_complete(token, chosen, ASK_SYSTEM, user)
    if text:
        return text
    local = answer_briefing(question, data)
    fallback = str((local or {}).get("answer") or "").strip()
    if fallback and fallback != "That’s not on this page.":
        return fallback
    name = str(data.get("name") or "").strip().split()[0] if data.get("name") else ""
    fire = now_items(data)
    if name:
        if fire:
            return f"Hi {name}. {len(fire)} on Needs us now — start there."
        return f"Hi {name}. Nothing is burning on this page."
    if fire:
        return f"Hi. {len(fire)} on Needs us now — start there."
    raise RuntimeError("gateway returned an empty answer")


def read_plan_state() -> dict:
    if not PLAN_FILE.is_file():
        return {"state": "idle"}
    try:
        data = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "idle"}
    return data if isinstance(data, dict) else {"state": "idle"}


def _write_plan_state_unlocked(**fields) -> dict:
    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = read_plan_state()
    state.update(fields)
    tmp = PLAN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(PLAN_FILE)
    return state


def write_plan_state(**fields) -> dict:
    with PLAN_LOCK:
        return _write_plan_state_unlocked(**fields)


def set_plan_proc(proc: subprocess.Popen | None) -> None:
    global PLAN_PROC
    with PLAN_PROC_LOCK:
        PLAN_PROC = proc


def kill_plan_proc(wait_sec: float = 3) -> bool:
    with PLAN_PROC_LOCK:
        proc = PLAN_PROC
    if not proc or proc.poll() is not None:
        return False
    try:
        if sys.platform != "win32":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=max(1.0, float(wait_sec)))
        except subprocess.TimeoutExpired:
            if sys.platform != "win32":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
            else:
                proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
    except OSError:
        pass
    set_plan_proc(None)
    return True


def plan_job_busy() -> bool:
    if not PLAN_ACTIVE.is_set():
        return False
    with PLAN_PROC_LOCK:
        proc = PLAN_PROC
    if proc is not None and proc.poll() is None:
        return True
    state = read_plan_state()
    return state.get("state") == "running"


def stop_plan_run(reason: str = "Stopped.") -> dict:
    PLAN_STOP.set()
    kill_plan_proc()
    msg = (reason or "Stopped.").strip()[:400]
    with PLAN_LOCK:
        current = read_plan_state()
        if current.get("state") == "running":
            _write_plan_state_unlocked(
                state="error",
                error=msg,
                finishedAt=now_stamp(),
                headline="Stopped",
                pid=0,
            )
    append_plan_step(kind="error", label=msg)
    PLAN_ACTIVE.clear()
    set_plan_proc(None)
    return read_plan_state()


def now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def clip(text: str, n: int = 220) -> str:
    text = re.sub(r"\s+", " ", redact(strip_ansi(text or ""))).strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def redact(text: str) -> str:
    return SECRET_RE.sub("[redacted]", text or "")


def reset_plan_log() -> None:
    try:
        PLAN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAN_LOG_FILE.write_text("", encoding="utf-8")
    except OSError:
        pass


def write_plan_log_line(text: str) -> None:
    line = redact((text or "").rstrip())
    if not line:
        return
    try:
        with PLAN_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def append_plan_step(**step) -> None:
    row = {key: val for key, val in step.items() if val not in (None, "")}
    row["at"] = now_stamp()
    label = str(row.get("label") or "")
    detail = str(row.get("detail") or "")
    write_plan_log_line(
        f"{row['at']} [{row.get('kind') or 'log'}] {label}" + (f" — {detail}" if detail else "")
    )
    if detail:
        row["detail"] = clip(detail, 400)
    with PLAN_LOCK:
        state = read_plan_state()
        steps = list(state.get("steps") or []) if isinstance(state.get("steps"), list) else []
        steps.append(row)
        if len(steps) > PLAN_STEPS_MAX:
            steps = steps[-PLAN_STEPS_MAX:]
        kind = str(row.get("kind") or "log")
        headline = str(state.get("headline") or "")
        if kind in {"init", "tool", "done", "error", "log"} and label:
            headline = label
        _write_plan_state_unlocked(
            steps=steps,
            headline=headline,
            stepCount=len(steps),
        )


def label_tool(name: str) -> str:
    raw = (name or "tool").strip()
    if not raw.startswith("mcp__"):
        return raw
    bits = [bit for bit in raw.split("__") if bit and bit != "mcp"]
    if not bits:
        return "MCP"
    host = bits[0]
    tool = bits[-1]
    host_l = host.lower()
    tool_l = tool.lower()
    if "orgcs" in host_l:
        host = "OrgCS"
    elif "slack" in host_l:
        host = "Slack"
    elif "gmail" in host_l or "gmail" in tool_l:
        host = "Gmail"
    elif "gus" in host_l:
        host = "GUS"
    elif "task" in tool_l:
        host = "Tasks"
    elif any(word in tool_l for word in ("event", "calendar", "freebusy")) or "google" in host_l or "workspace" in host_l:
        host = "Calendar"
    else:
        host = host.replace("plugin_", "").replace("_", " ")
    tool = re.sub(r"^(slack_|gmail_|calendar_|google_)", "", tool)
    return f"{host} · {tool}"


def tool_detail(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    if name == "Bash" or name.endswith("Bash"):
        cmd = str(inp.get("command") or inp.get("cmd") or "").strip().splitlines()
        return clip(cmd[0] if cmd else "", 180)
    for key in ("query", "q", "sql", "soql", "path", "file_path", "pattern", "url", "command"):
        val = inp.get(key)
        if val:
            return clip(str(val), 180)
    for val in inp.values():
        if isinstance(val, str) and val.strip():
            return clip(val, 180)
    return ""


def result_text(content) -> str:
    if isinstance(content, str):
        return clip(content, 240)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return clip(" ".join(parts), 240)
    if isinstance(content, dict):
        return clip(json.dumps(content, ensure_ascii=False), 240)
    return ""


def plan_fatal_message(line: str) -> str:
    low = strip_ansi(line).lower()
    if "authentication required" in low or "agent login" in low:
        return clip(line, 220)
    if "unsupportedparamserror" in low or "doesn't support tool calling" in low:
        return (
            "OpenCode's LiteLLM path cannot tool-call this model. "
            "The planner now sends OpenCode through the Express Anthropic proxy — run it again."
        )
    if "reasoning: extra inputs are not permitted" in low:
        return (
            "This model rejected OpenCode's reasoning payload. "
            "The planner now sends OpenCode through the Express Anthropic proxy — run it again."
        )
    if "error doing the fallback" in low and "litellm" in low:
        return "OpenCode LiteLLM fallback failed. Run Planner again."
    return ""


def strip_anthropic_env(env: dict) -> None:
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_CUSTOM_HEADERS",
    ):
        env.pop(key, None)


def label_opencode_perm(perm: str) -> str:
    raw = str(perm or "").strip()
    low = raw.lower()
    if low in {"bash", "edit", "read", "write", "external_directory"}:
        return ""
    if low.startswith("orgcs_"):
        return "OrgCS · " + raw[6:]
    if "gmail" in low:
        return "Gmail · " + raw.rsplit("_", 1)[-1]
    if low.startswith("google-workspace_") or low.startswith("google_workspace_"):
        return "Calendar · " + raw.split("_")[-1]
    if low.startswith("slack_"):
        return "Slack · " + re.sub(r"^slack_(?:slack_)?", "", raw)
    if low.startswith("gus"):
        return "GUS · " + raw.split("_", 1)[-1]
    return raw


def handle_opencode_log(line: str, flags: dict, last: dict) -> None:
    raw = strip_ansi(line or "")
    if not raw.strip():
        return
    fatal = plan_fatal_message(raw)
    if fatal:
        flags["fatal"] = True
        flags["fatal_msg"] = fatal
        PLAN_STOP.set()
        append_plan_step(kind="error", label=fatal)
        return
    match = re.search(r"evaluated permission=([A-Za-z0-9_.-]+)", raw)
    if match:
        perm = match.group(1)
        label = label_opencode_perm(perm)
        if not label or last.get("perm") == perm:
            return
        last["perm"] = perm
        append_plan_step(kind="tool", label=label)
        return
    if "creating instance" in raw:
        append_plan_step(kind="log", label="OpenCode booting")
    ingest_overflow_from_tool_result(raw, flags)


def apply_activity_file(data: dict, path: pathlib.Path | None = None) -> None:
    """Attach quiet-merged CaseComment rows onto plan case items."""
    src = path or CASE_ACTIVITY_FILE
    try:
        blob = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(blob, dict):
        return
    by_num = blob.get("byNumber") if isinstance(blob.get("byNumber"), dict) else {}
    by_id = blob.get("byId") if isinstance(blob.get("byId"), dict) else {}
    if not by_num and not by_id:
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            num = str(item.get("caseNumber") or "").strip()
            events = by_num.get(num) if num else None
            if not events:
                match = re.search(r"/Case/(500[A-Za-z0-9]+)/", str(item.get("caseUrl") or ""))
                events = by_id.get(match.group(1)) if match else None
            if not isinstance(events, list) or not events:
                continue
            if item.get("activity"):
                continue
            item["activity"] = events


def fill_missing_peek_summaries(data: dict) -> None:
    """Peek is the model's case analysis. Python must not invent summaries."""
    return


def tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return " ".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content or "")


def ingest_overflow_file(path: str) -> bool:
    raw = str(path or "").strip().rstrip(".,;:")
    if not raw.startswith("/") or ".." in raw:
        return False
    if not pathlib.Path(raw).is_file() or not SLIM_TOOL.is_file():
        return False
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SLIM_TOOL),
                raw,
                "--merge",
                str(CASE_ACTIVITY_FILE),
            ],
            check=False,
            capture_output=True,
            timeout=25,
        )
        return result.returncode == 0
    except Exception:
        return False


def stub_stale_planner_overflows() -> None:
    if not SLIM_TOOL.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(SLIM_TOOL), "--sweep-stale"],
            check=False,
            capture_output=True,
            timeout=20,
        )
    except Exception:
        pass


def ingest_read_overflow_path(name: str, inp: dict, flags: dict) -> None:
    """If the model Reads an overflow path, slim+stub so the next compact cannot refill."""
    if str(name or "").split("__")[-1].lower() not in {"read", "readfile", "view"}:
        return
    if not isinstance(inp, dict):
        return
    raw = str(inp.get("path") or inp.get("file_path") or inp.get("filePath") or "").strip()
    if not raw.startswith("/"):
        return
    low = raw.lower()
    if raw == str(CASE_DIGEST_FILE) or raw.endswith("/case-digest.json"):
        try:
            CASE_DIGEST_FILE.write_text('{"use":"--cards"}\n', encoding="utf-8")
        except OSError:
            pass
        return
    if "tool-result" not in low and "tool_results" not in low:
        return
    seen = flags.setdefault("ingested_overflow", set())
    if raw in seen:
        return
    seen.add(raw)
    try:
        sample = pathlib.Path(raw).read_bytes()[:12000].decode("utf-8", "replace")
    except OSError:
        sample = ""
    _stamp_thread_objects(flags, sample=sample)
    ingest_overflow_file(raw)


def ingest_overflow_from_tool_result(content, flags: dict) -> None:
    text = tool_result_text(content)
    seen = flags.setdefault("ingested_overflow", set())
    found = list(OVERFLOW_SAVED_RE.findall(text)) + list(OVERFLOW_PATH_RE.findall(text))
    merged = False
    for saved in found:
        saved = str(saved or "").strip().rstrip(".,;:)")
        if not saved or saved in seen:
            continue
        seen.add(saved)
        try:
            sample = pathlib.Path(saved).read_bytes()[:12000].decode("utf-8", "replace")
        except OSError:
            sample = ""
        _stamp_thread_objects(flags, sample=sample)
        if ingest_overflow_file(saved):
            merged = True
            append_plan_step(kind="log", label="Merged overflow on disk")
    if ingest_inline_soql_blob(content, text, flags):
        merged = True
    if merged:
        flags["body_merges"] = int(flags.get("body_merges") or 0) + 1
        flags["need_sweep"] = True


def _soql_records_from_blob(blob) -> list:
    if isinstance(blob, list):
        if blob and isinstance(blob[0], dict) and (
            blob[0].get("CommentBody") is not None
            or blob[0].get("TextBody") is not None
            or blob[0].get("ParentId")
        ):
            return [row for row in blob if isinstance(row, dict)]
        recs = []
        for item in blob:
            recs.extend(_soql_records_from_blob(item))
        return recs
    if isinstance(blob, dict):
        rec = blob.get("records")
        if isinstance(rec, list):
            return [row for row in rec if isinstance(row, dict)]
        for key in ("result", "data", "output", "content", "text"):
            inner = blob.get(key)
            if isinstance(inner, str) and inner.strip()[:1] in "{[":
                try:
                    inner = json.loads(inner)
                except json.JSONDecodeError:
                    continue
            got = _soql_records_from_blob(inner) if inner is not None else []
            if got:
                return got
    return []


def _blob_has_thread_bodies(recs: list) -> bool:
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if rec.get("CommentBody") or rec.get("TextBody") or rec.get("HtmlBody"):
            return True
        body = rec.get("Body")
        if body and (rec.get("ParentId") or rec.get("Type")):
            return True
    return False


def ingest_inline_soql_blob(content, text: str, flags: dict) -> bool:
    """Clip CaseComment / EmailMessage / CaseFeed records that stayed inline (under overflow)."""
    blob = content if isinstance(content, dict) else None
    raw = (text or "").strip()
    if blob is None:
        start = raw.find("{")
        if start < 0:
            start = raw.find("[")
        if start < 0:
            return False
        try:
            parsed = json.loads(raw[start:])
        except json.JSONDecodeError:
            return False
        blob = parsed
    recs = _soql_records_from_blob(blob)
    persist_owned_open_from_recs(recs)
    if not recs or not _blob_has_thread_bodies(recs):
        return False
    try:
        INLINE_SOQL_TMP.write_text(json.dumps(blob), encoding="utf-8")
    except OSError:
        return False
    ok = ingest_overflow_file(str(INLINE_SOQL_TMP))
    try:
        INLINE_SOQL_TMP.write_text("{}\n", encoding="utf-8")
    except OSError:
        pass
    if ok:
        _stamp_thread_objects(flags, recs)
        append_plan_step(kind="log", label="Clipped inline comments/email off the model path")
    return ok


def write_planner_cards() -> int:
    if not SLIM_TOOL.is_file():
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, str(SLIM_TOOL), "--digest"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    out = proc.stdout or ""
    match = re.search(r"ok (?:digest|cards)=(\d+)", out)
    return int(match.group(1)) if match else 0


def dump_seeded_activity_file(seeded: dict) -> None:
    by_num = {}
    for sec in seeded.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            num = str(item.get("caseNumber") or "").strip()
            act = item.get("activity")
            if num and isinstance(act, list) and act:
                by_num[num] = act
    if not by_num:
        return
    blob = {}
    if CASE_ACTIVITY_FILE.is_file():
        try:
            loaded = json.loads(CASE_ACTIVITY_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                blob = loaded
        except (OSError, json.JSONDecodeError):
            blob = {}
    nums = blob.get("byNumber") if isinstance(blob.get("byNumber"), dict) else {}
    nums.update(by_num)
    blob["byNumber"] = nums
    by_id = blob.get("byId") if isinstance(blob.get("byId"), dict) else {}
    for sec in seeded.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            act = item.get("activity")
            if not isinstance(act, list) or not act:
                continue
            match = re.search(r"/Case/(500[A-Za-z0-9]+)/", str(item.get("caseUrl") or ""))
            cid = match.group(1) if match else ""
            if not cid:
                continue
            bag = list(by_id.get(cid) or [])
            keys = {
                (e.get("when"), e.get("who"), str(e.get("text") or "")[:80])
                for e in bag
                if isinstance(e, dict)
            }
            for row in act:
                if not isinstance(row, dict):
                    continue
                key = (row.get("when"), row.get("who"), str(row.get("text") or "")[:80])
                if key in keys:
                    continue
                bag.append(row)
                keys.add(key)
            bag.sort(key=lambda ev: str(ev.get("_ts") or ""), reverse=True)
            by_id[cid] = bag
    blob["byId"] = by_id
    try:
        CASE_ACTIVITY_FILE.write_text(json.dumps(blob) + "\n", encoding="utf-8")
    except OSError:
        pass


IR_DONE = {"met", "completed after violation"}
GUS_WORK_URL = "https://gus.lightning.force.com/lightning/r/ADM_Work__c/{id}/view"
GUS_CASE_URL = "https://gus.lightning.force.com/lightning/r/Case/{id}/view"
W_NAME_RE = re.compile(r"\bW-\d+\b", re.I)
ORGCS_IR_FIELDS = (
    "SE_Initial_Response_Status__c, SE_Target_Response__c, First_Response_Date_Time__c, "
    "GUS_Investigation_Number__c, Display_Bug__c"
)


def _soql_in(ids: list[str], limit: int = 80) -> str:
    out = []
    for raw in ids:
        val = str(raw or "").strip()
        if not val or "'" in val:
            continue
        out.append("'" + val + "'")
        if len(out) >= limit:
            break
    return ",".join(out)


def ir_is_pending(row: dict) -> bool:
    status = str(row.get("SE_Initial_Response_Status__c") or "").strip()
    if status.lower() in IR_DONE:
        return False
    target = str(row.get("SE_Target_Response__c") or "").strip()
    first = str(row.get("First_Response_Date_Time__c") or "").strip()
    if not status:
        return bool(target) and not first
    return True


def _append_digest_section(title: str, body: str) -> None:
    blob = str(body or "").rstrip()
    if not blob:
        return
    try:
        prev = PLANNER_DIGEST_FILE.read_text(encoding="utf-8") if PLANNER_DIGEST_FILE.is_file() else ""
        PLANNER_DIGEST_FILE.write_text(
            prev.rstrip() + f"\n\n## {title}\n" + blob + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _gus_sf_bin() -> str:
    """Chrome's bridge often has a PATH without /usr/local/bin."""
    found = shutil.which("sf") or ""
    if found:
        return found
    for candidate in ("/usr/local/bin/sf", "/opt/homebrew/bin/sf"):
        if os.path.isfile(candidate):
            return candidate
    return ""


def _gus_sf_soql(soql: str) -> list | None:
    binary = _gus_sf_bin()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "data", "query", "--target-org", "gus", "-q", soql, "--json"],
            capture_output=True,
            text=True,
            timeout=40,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_soql_records(proc.stdout or "")


def gus_soql(soql: str) -> list:
    last = None
    try:
        text = mcp_call_named(GUS_MCP_NAMES, "query_gus_records", {"soql": soql}, timeout=28)
        recs = parse_soql_records(text)
        if recs:
            return recs
        raw = str(text or "")
        if re.search(r'"records"\s*:\s*\[', raw) or re.search(r'"totalSize"\s*:', raw):
            return recs
    except Exception as exc:
        last = exc
    recs = _gus_sf_soql(soql)
    if recs is not None:
        return recs
    raise last or RuntimeError("gus query failed")


def _modified_within_days(raw: str, days: int) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= datetime.now(timezone.utc) - timedelta(days=days)


def _gus_role_has(role: str, token: str) -> bool:
    return token in [part.strip() for part in str(role or "").split("+") if part.strip()]


def gus_chatter_clip(record_id: str, *, tracked: bool = False) -> str:
    rid = str(record_id or "").strip()
    if not rid or "'" in rid:
        return ""
    try:
        text = mcp_call_named(
            GUS_MCP_NAMES,
            "query_gus_chatter",
            {"record_id": rid, "limit": 8, "include_tracked_changes": tracked},
            timeout=18,
        )
    except Exception:
        return ""
    raw = str(text or "")
    if re.search(r"saved to|overflow|wrote (?:output )?to", raw, re.I):
        return "(chatter large — status from the work row)"
    return clip(raw, 800)


def _rel_name(rec: dict, key: str) -> str:
    nested = rec.get(key)
    if isinstance(nested, dict):
        return str(nested.get("Name") or "").strip()
    return ""


def fill_orgcs_ir_and_related(seeded: dict, rows: list) -> None:
    """Always-on OrgCS Initial Response + Case GUS related lists. Empty is a result."""
    by_id = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("Id") or "").strip()
        if cid.startswith("500"):
            by_id[cid] = row
    ids = list(by_id.keys())[:80]
    quoted = _soql_in(ids)
    if quoted:
        need_ir = any(
            "SE_Initial_Response_Status__c" not in (by_id[cid] or {})
            for cid in ids
        )
        if need_ir:
            try:
                text = mcp_call_named(
                    ORGCS_MCP_NAMES,
                    "soqlQuery",
                    {
                        "q": (
                            "SELECT Id, CaseNumber, "
                            + ORGCS_IR_FIELDS
                            + f" FROM Case WHERE Id IN ({quoted}) LIMIT 80"
                        )
                    },
                    timeout=25,
                )
                for rec in parse_soql_records(text):
                    cid = str(rec.get("Id") or "").strip()
                    if cid in by_id:
                        by_id[cid].update(rec)
                seeded["orgcsSlaFetchOk"] = True
            except Exception as exc:
                seeded["orgcsSlaFetchOk"] = False
                seeded["orgcsSlaFetchError"] = clip(str(exc), 180)
                append_plan_step(kind="log", label="OrgCS Initial Response fetch failed · " + seeded["orgcsSlaFetchError"])
        else:
            seeded["orgcsSlaFetchOk"] = True
        related_lines = {}
        candidates = []
        try:
            rel_soql = (
                "SELECT Case__c, Name, RecordType.Name, Type__c, GUSText__c, "
                "GUS_PRB_LAP_Link__c, Work_Status__c, Work_Subject__c, "
                "GUS_Work__c, GUS_Work__r.Name, Work_Record_Type__c "
                f"FROM Case_Relationship__c WHERE Case__c IN ({quoted}) "
                "ORDER BY LastModifiedDate DESC LIMIT 80"
            )
            rel_text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": rel_soql}, timeout=25)
            for rec in parse_soql_records(rel_text):
                cid = str(rec.get("Case__c") or "").strip()
                row = by_id.get(cid) or {}
                num = str(row.get("CaseNumber") or "").strip()
                work = rec.get("GUS_Work__r") if isinstance(rec.get("GUS_Work__r"), dict) else {}
                wname = str(work.get("Name") or "").strip()
                kind = (
                    str(rec.get("Type__c") or "").strip()
                    or str(rec.get("Work_Record_Type__c") or "").strip()
                    or "GUS"
                )
                gusn = str(rec.get("GUSText__c") or "").strip() or wname
                status = str(rec.get("Work_Status__c") or "").strip()
                subj = clip(str(rec.get("Work_Subject__c") or ""), 80)
                line = " · ".join(part for part in (kind, gusn, status, subj) if part)
                if line:
                    related_lines.setdefault(num or cid, []).append(line)
                wid = str(rec.get("GUS_Work__c") or "").strip()
                candidates.append(
                    {
                        "id": f"rel-{str(gusn or rec.get('Name') or wid)[:40]}",
                        "kind": "gus",
                        "role": "orgcs-related",
                        "type": kind,
                        "name": gusn or str(rec.get("Name") or ""),
                        "status": status,
                        "label": clip(line, 90),
                        "caseNumber": num,
                        "workId": wid,
                    }
                )
            rec_soql = (
                "SELECT AssociatedCase__c, Name, GUSNumber__c, Type__c, Link__c "
                f"FROM GUSRecord__c WHERE AssociatedCase__c IN ({quoted}) "
                "ORDER BY LastModifiedDate DESC LIMIT 80"
            )
            rec_text = mcp_call_named(ORGCS_MCP_NAMES, "soqlQuery", {"q": rec_soql}, timeout=25)
            for rec in parse_soql_records(rec_text):
                cid = str(rec.get("AssociatedCase__c") or "").strip()
                row = by_id.get(cid) or {}
                num = str(row.get("CaseNumber") or "").strip()
                kind = str(rec.get("Type__c") or "").strip() or "GUS"
                gusn = str(rec.get("GUSNumber__c") or rec.get("Name") or "").strip()
                link = str(rec.get("Link__c") or "").strip()
                line = " · ".join(part for part in (kind, gusn) if part)
                if line:
                    related_lines.setdefault(num or cid, []).append(line)
                candidates.append(
                    {
                        "id": f"gusrec-{gusn or str(rec.get('Name') or '')}"[:60],
                        "kind": "gus",
                        "role": "orgcs-related",
                        "type": kind,
                        "name": gusn,
                        "label": clip(line, 90),
                        "caseNumber": num,
                        "gusUrl": link[:180] if link else "",
                    }
                )
            seeded["orgcsRelatedFetchOk"] = True
        except Exception as exc:
            seeded["orgcsRelatedFetchOk"] = False
            seeded["orgcsRelatedFetchError"] = clip(str(exc), 180)
            append_plan_step(kind="log", label="OrgCS GUS related list fetch failed · " + seeded["orgcsRelatedFetchError"])
        for row in by_id.values():
            num = str(row.get("CaseNumber") or "").strip()
            row["_gusRelated"] = related_lines.get(num) or []
            row["_irPending"] = ir_is_pending(row)
        seeded["_irByNum"] = {
            str(row.get("CaseNumber") or ""): row
            for row in by_id.values()
            if row.get("CaseNumber")
        }
        seeded["gusRelatedCandidates"] = candidates
        pending = []
        for row in by_id.values():
            if not ir_is_pending(row):
                continue
            pending.append(
                {
                    "caseNumber": str(row.get("CaseNumber") or ""),
                    "irStatus": str(row.get("SE_Initial_Response_Status__c") or "") or "(blank)",
                    "irTarget": str(row.get("SE_Target_Response__c") or "")[:40],
                }
            )
        seeded["slaPending"] = pending
        sla_lines = ["- orgcsSlaFetchOk: " + ("true" if seeded.get("orgcsSlaFetchOk") is True else "false")]
        sla_lines.append("- orgcsRelatedFetchOk: " + ("true" if seeded.get("orgcsRelatedFetchOk") is True else "false"))
        if pending:
            for item in pending[:20]:
                sla_lines.append(
                    f"- pending #{item['caseNumber']} Initial Response={item['irStatus']} target={item['irTarget'] or '—'}"
                )
        else:
            sla_lines.append("- pending Initial Response: none")
        seeded["_slaDigest"] = "\n".join(sla_lines)
        n_rel = sum(len(v) for v in related_lines.values())
        append_plan_step(
            kind="log",
            label=f"OrgCS IR + GUS related list · pendingIR={len(pending)} · related={n_rel}",
        )
        return
    seeded["orgcsSlaFetchOk"] = False
    seeded["orgcsRelatedFetchOk"] = False
    seeded["_irByNum"] = {}
    seeded["slaPending"] = []
    seeded["gusRelatedCandidates"] = []
    seeded["_slaDigest"] = "- orgcsSlaFetchOk: false\n- pending Initial Response: (no owned Case Ids this run)"


def _append_lap_rows(nums: list, case_ids: list, candidates: list, digest_lines: list) -> list:
    """LAP rows for the GUS section. Does not need the engineer's GUS User id."""
    laps = []
    quoted_nums = _soql_in(nums, 80)
    if quoted_nums:
        laps.extend(
            gus_soql(
                "SELECT Id, CaseNumber, Subject, Status, LAP_type__c, LAP_Global_Case_Number__c, "
                "SM_Target_Execution_Date_Time__c, SM_Target_Execution_End_Date_Time__c "
                "FROM Case WHERE RecordType.Name = 'LAP' AND LAP_Global_Case_Number__c IN ("
                f"{quoted_nums})"
            )
        )
    quoted_c = _soql_in(case_ids, 40)
    if quoted_c:
        laps.extend(
            gus_soql(
                "SELECT Id, CaseNumber, Subject, Status, LAP_type__c, LAP_Global_Case_Number__c, "
                "SM_Target_Execution_Date_Time__c, SM_Target_Execution_End_Date_Time__c "
                "FROM Case WHERE Id IN ("
                f"{quoted_c}) AND RecordType.Name = 'LAP' AND IsClosed = false"
            )
        )
    seen_lap = set()
    lap_rows = []
    for rec in laps:
        lid = str(rec.get("Id") or "").strip()
        if not lid or lid in seen_lap:
            continue
        seen_lap.add(lid)
        lap_rows.append(rec)
    chatter_n = 0
    for rec in lap_rows[:12]:
        lid = str(rec.get("Id") or "")
        chatter = gus_chatter_clip(lid) if chatter_n < 8 else ""
        if chatter:
            chatter_n += 1
        orgcs = str(rec.get("LAP_Global_Case_Number__c") or "")
        item = {
            "id": f"lap-{rec.get('CaseNumber') or lid}",
            "kind": "gus",
            "role": "lap",
            "name": str(rec.get("CaseNumber") or ""),
            "status": str(rec.get("Status") or "")[:40],
            "label": clip(
                "LAP "
                + str(rec.get("CaseNumber") or "")
                + (" · #" + orgcs if orgcs else ""),
                90,
            ),
            "detail": clip(str(rec.get("Subject") or ""), 120),
            "caseNumber": orgcs,
            "start": str(rec.get("SM_Target_Execution_Date_Time__c") or "")[:32],
            "end": str(rec.get("SM_Target_Execution_End_Date_Time__c") or "")[:32],
            "gusUrl": GUS_CASE_URL.format(id=lid),
            "workId": lid,
            "chatter": chatter,
        }
        candidates.append(item)
        digest_lines.append(f"## lap {rec.get('CaseNumber') or lid}")
        digest_lines.append(f"- orgcs: {orgcs or '—'}")
        digest_lines.append(f"- status: {rec.get('Status') or '—'}")
        digest_lines.append(f"- type: {rec.get('LAP_type__c') or '—'}")
        digest_lines.append(f"- start: {rec.get('SM_Target_Execution_Date_Time__c') or '—'}")
        digest_lines.append(f"- end: {rec.get('SM_Target_Execution_End_Date_Time__c') or '—'}")
        if chatter:
            digest_lines.append("- chatter: " + chatter)
    return lap_rows


def fill_gus_work(seeded: dict, rows: list) -> None:
    """Support Contact, Follow, stored investigation SLA fields, LAP start/end. Always run."""
    if "@" not in str(seeded.get("email") or ""):
        try:
            fetch_orgcs_identity(seeded)
        except Exception:
            pass
    nums = []
    seen_n = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("CaseNumber") or "").strip()
        if num.isdigit() and num not in seen_n:
            seen_n.add(num)
            nums.append(num)
    for num in (seeded.get("_irByNum") or {}):
        if str(num).isdigit() and num not in seen_n:
            seen_n.add(num)
            nums.append(str(num))
    email = resolve_engineer_email(seeded)
    if email and isinstance(seeded, dict):
        seeded["email"] = email
        persist_identity(seeded)
    digest_lines = []
    candidates = list(seeded.get("gusRelatedCandidates") or [])
    work_by_id = {}

    def take_work(rec: dict, role: str) -> None:
        wid = str(rec.get("Id") or "").strip()
        if not wid or wid in work_by_id:
            if wid in work_by_id and role not in str(work_by_id[wid].get("role") or ""):
                work_by_id[wid]["role"] = work_by_id[wid]["role"] + "+" + role
            return
        name = str(rec.get("Name") or "").strip()
        due = rec.get("Due_Date__c")
        assignee = _rel_name(rec, "Assignee__r")
        rtype = _rel_name(rec, "RecordType")
        modified = str(rec.get("LastModifiedDate") or "")[:32]
        item = {
            "id": f"gus-{name or wid}",
            "kind": "gus",
            "role": role,
            "name": name,
            "workId": wid,
            "status": str(rec.get("Status__c") or "")[:40],
            "recordType": rtype[:40],
            "modified": modified,
            "due": str(due or "")[:32],
            "outOfSla": rec.get("Out_of_SLA__c"),
            "slaValue": rec.get("SLA__c"),
            "slaWarningSent": rec.get("SLA_Warning_Notification_Sent__c"),
            "slaViolations": rec.get("Number_of_SLA_Violations__c"),
            "label": clip(" · ".join(part for part in (name, str(rec.get("Subject__c") or "")) if part), 90),
            "detail": clip(
                " · ".join(
                    part
                    for part in (
                        str(rec.get("Status__c") or ""),
                        ("due " + str(due)[:10]) if due else "",
                        ("assignee " + assignee) if assignee else "",
                    )
                    if part
                ),
                120,
            ),
            "gusUrl": GUS_WORK_URL.format(id=wid),
            "subject": clip(str(rec.get("Subject__c") or ""), 80),
        }
        work_by_id[wid] = item
        candidates.append(item)

    try:
        gus_user = ""
        if email and "'" not in email:
            users = gus_soql(
                "SELECT Id, Email, Name FROM User WHERE Email = '%s' OR Username = '%s' LIMIT 1"
                % (email, email)
            )
            if users:
                gus_user = str(users[0].get("Id") or "").strip()
        if not gus_user:
            seeded["gusFetchError"] = "no GUS User for " + (email or "blank email")
            digest_lines.append("- " + seeded["gusFetchError"])
            append_plan_step(kind="log", label="GUS user resolve failed · " + seeded["gusFetchError"])
            lap_rows = _append_lap_rows(nums, [], candidates, digest_lines)
            seeded["gusFetchOk"] = bool(lap_rows)
            digest_lines.insert(0, "- fetchOk: " + ("true" if lap_rows else "false"))
            if not lap_rows:
                digest_lines.append("- open work / LAP: none")
        else:
            digest_lines.append("- fetchOk: true")
            digest_lines.append("- gusUser: " + gus_user)
            contact = gus_soql(
                "SELECT Id, Name, Subject__c, Status__c, Priority__c, RecordType.Name, Due_Date__c, "
                "Out_of_SLA__c, SLA__c, SLA_Warning_Notification_Sent__c, Number_of_SLA_Violations__c, "
                "Sprint__r.Name, CS_Contact__r.Name, Assignee__r.Name, LastModifiedDate "
                "FROM ADM_Work__c WHERE CS_Contact__c = '%s' AND Closed__c = 0 "
                "ORDER BY LastModifiedDate DESC LIMIT 80" % gus_user
            )
            for rec in contact:
                take_work(rec, "support-contact")
            follows = gus_soql(
                "SELECT ParentId FROM EntitySubscription WHERE SubscriberId = '%s' LIMIT 200" % gus_user
            )
            work_ids = []
            case_ids = []
            for rec in follows:
                pid = str(rec.get("ParentId") or "").strip()
                if pid.startswith("a07"):
                    work_ids.append(pid)
                elif pid.startswith("500"):
                    case_ids.append(pid)
            quoted_w = _soql_in(work_ids, 80)
            if quoted_w:
                for rec in gus_soql(
                    "SELECT Id, Name, Subject__c, Status__c, Priority__c, RecordType.Name, Due_Date__c, "
                    "Out_of_SLA__c, SLA__c, SLA_Warning_Notification_Sent__c, Number_of_SLA_Violations__c, "
                    "CS_Contact__r.Name, Assignee__r.Name, LastModifiedDate "
                    f"FROM ADM_Work__c WHERE Id IN ({quoted_w}) AND Closed__c = 0"
                ):
                    take_work(rec, "follow")
            w_names = []
            seen_w = set()
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                blob = " ".join(
                    str(row.get(k) or "")
                    for k in ("Display_Bug__c", "GUS_Investigation_Number__c")
                )
                for line in row.get("_gusRelated") or []:
                    blob += " " + line
                for match in W_NAME_RE.findall(blob):
                    token = match.upper()
                    if token not in seen_w:
                        seen_w.add(token)
                        w_names.append(token)
            quoted_n = _soql_in(w_names, 40)
            if quoted_n:
                for rec in gus_soql(
                    "SELECT Id, Name, Subject__c, Status__c, Priority__c, RecordType.Name, Due_Date__c, "
                    "Out_of_SLA__c, SLA__c, SLA_Warning_Notification_Sent__c, Number_of_SLA_Violations__c, "
                    "Assignee__r.Name, LastModifiedDate "
                    f"FROM ADM_Work__c WHERE Name IN ({quoted_n}) AND Closed__c = 0"
                ):
                    take_work(rec, "orgcs-related")
            lap_rows = _append_lap_rows(nums, case_ids, candidates, digest_lines)
            inv_n = 0
            for item in work_by_id.values():
                rtype = str(item.get("recordType") or "")
                role = str(item.get("role") or "")
                if "investigat" not in rtype.lower():
                    continue
                if not (_gus_role_has(role, "support-contact") or _gus_role_has(role, "follow")):
                    continue
                lookback = inbox_lookback_days(str(seeded.get("timezone") or ""))
                if not _modified_within_days(str(item.get("modified") or ""), lookback):
                    continue
                if inv_n >= 6:
                    break
                inv_n += 1
                note = gus_chatter_clip(str(item.get("workId") or ""), tracked=True)
                if note:
                    item["update"] = note
            digest_lines.insert(2, "- investigation SLA fields are copied as stored. LAP rows have start and end.")
            for item in list(work_by_id.values())[:40]:
                digest_lines.append(f"## work {item.get('name') or item.get('workId')}")
                digest_lines.append(f"- subject: {item.get('subject') or '—'}")
                digest_lines.append(f"- status: {item.get('status') or '—'}")
                digest_lines.append(f"- type: {item.get('recordType') or '—'}")
                digest_lines.append(f"- modified: {item.get('modified') or '—'}")
                if item.get("update"):
                    digest_lines.append("- chatter: " + str(item.get("update")))
                digest_lines.append(f"- due: {item.get('due') or '—'}")
                digest_lines.append(f"- out of sla: {item.get('outOfSla') if item.get('outOfSla') is not None else '—'}")
                digest_lines.append(f"- sla: {item.get('slaValue') if item.get('slaValue') is not None else '—'}")
                digest_lines.append(
                    f"- sla warning sent: {item.get('slaWarningSent') if item.get('slaWarningSent') is not None else '—'}"
                )
                digest_lines.append(
                    f"- sla violations: {item.get('slaViolations') if item.get('slaViolations') is not None else '—'}"
                )
                digest_lines.append(f"- role: {item.get('role') or '—'}")
            if not work_by_id and not lap_rows:
                digest_lines.append("- open work / LAP: none")
            seeded["gusFetchOk"] = True
            append_plan_step(
                kind="log",
                label=(
                    f"GUS fetch · support/follow={len(work_by_id)} · lap={len(lap_rows)}"
                ),
            )
    except Exception as exc:
        seeded["gusFetchOk"] = False
        seeded["gusFetchError"] = clip(str(exc), 180)
        digest_lines.append("- fetchOk: false")
        digest_lines.append("- " + seeded["gusFetchError"])
        append_plan_step(kind="log", label="GUS fetch failed · " + seeded["gusFetchError"])
    seeded["gusCandidates"] = candidates
    seeded["_gusDigest"] = "\n".join(digest_lines) if digest_lines else "- fetchOk: false"


def inject_ir_related_into_digest(seeded: dict) -> None:
    by_num = seeded.get("_irByNum") if isinstance(seeded.get("_irByNum"), dict) else {}
    if not by_num:
        return
    try:
        text = PLANNER_DIGEST_FILE.read_text(encoding="utf-8") if PLANNER_DIGEST_FILE.is_file() else ""
    except OSError:
        return
    if not text.strip():
        return

    def repl(match: re.Match) -> str:
        num = match.group(1)
        row = by_num.get(num) or {}
        ir = str(row.get("SE_Initial_Response_Status__c") or "").strip() or "(blank)"
        pending = " PENDING" if ir_is_pending(row) else ""
        tgt = _pt_when(str(row.get("SE_Target_Response__c") or ""), str(seeded.get("timezone") or ""))
        extra = [f"- ir: {ir}{pending}" + (f" · target {tgt}" if tgt else "")]
        rel = row.get("_gusRelated") or []
        extra.append("- gus related: " + (" ; ".join(rel[:4]) if rel else "none"))
        return match.group(0) + "\n" + "\n".join(extra)

    new = re.sub(r"(?m)^## (\d{6,})\s*$", repl, text)
    if new == text:
        return
    try:
        PLANNER_DIGEST_FILE.write_text(new, encoding="utf-8")
    except OSError:
        pass


def append_gus_blocks_to_digest(seeded: dict) -> None:
    sla = str(seeded.get("_slaDigest") or "").strip()
    if sla:
        _append_digest_section("sla", sla)
    gus = str(seeded.get("_gusDigest") or "").strip()
    if gus:
        _append_digest_section("gus", gus)


def fill_mandatory_gus_orgcs(seeded: dict, rows: list) -> None:
    """OrgCS IR + GUS related list + GUS work/Follow/SLA/LAP. Never skip."""
    fill_orgcs_ir_and_related(seeded, rows)
    fill_gus_work(seeded, rows)


def append_related_to_digest(seeded: dict) -> None:
    """Always-on GUS/IR digest append. Does not skip when the seed page is empty."""
    if not seeded.get("_gusDigest") and not seeded.get("_slaDigest"):
        rows = list((seeded.get("_irByNum") or {}).values()) or fetch_owned_open_cases()
        fill_mandatory_gus_orgcs(seeded, rows)
    inject_ir_related_into_digest(seeded)
    append_gus_blocks_to_digest(seeded)


def append_case_holds_to_digest() -> None:
    try:
        holds = _sanitize_mod().load_case_holds()
    except Exception:
        return
    if not holds:
        return
    marker = "## engineer holds"
    try:
        prev = PLANNER_DIGEST_FILE.read_text(encoding="utf-8") if PLANNER_DIGEST_FILE.is_file() else ""
    except OSError:
        return
    if marker in prev:
        return
    lines = [
        "",
        marker,
        "bucket=watch until a new inbound. You still write Peek summary from this digest.",
    ]
    for num in holds:
        lines.append(f"#{num}")
    try:
        PLANNER_DIGEST_FILE.write_text(prev.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def attach_clipped_activity(seeded: dict, evidence: dict) -> dict:
    apply_activity_file(seeded)
    dump_seeded_activity_file(seeded)
    rows = fetch_owned_open_cases()
    if rows or _owned_fetch_ok():
        try:
            repair_plan_payload(seeded, rows or [], refresh_inbox=False, wipe_peek_summary=False)
        except Exception:
            pass
        seeded["orgcsFetchOk"] = True
    elif seeded.get("orgcsFetchOk") is not True:
        seeded["orgcsFetchOk"] = False
    try:
        fill_mandatory_gus_orgcs(seeded, rows)
    except Exception as exc:
        seeded["gusFetchOk"] = False
        seeded["gusFetchError"] = clip(str(exc), 180)
        append_plan_step(kind="log", label="GUS/IR fetch failed · " + seeded["gusFetchError"])
    refreshed = write_planner_gather(evidence_from_seed(seeded))
    n = write_planner_cards()
    inject_ir_related_into_digest(seeded)
    append_gus_blocks_to_digest(seeded)
    append_case_holds_to_digest()
    refreshed = write_planner_gather(evidence_from_seed(seeded))
    n_digest, n_filled = digest_activity_stats()
    gchars = 0
    dchars = 0
    try:
        gchars = PLANNER_GATHER_FILE.stat().st_size
    except OSError:
        pass
    try:
        dchars = PLANNER_DIGEST_FILE.stat().st_size
    except OSError:
        pass
    if n or n_digest:
        append_plan_step(
            kind="log",
            label=(
                f"Clipped case threads · digest={n_digest} · activity on {n_filled}/{n_digest} · "
                f"gather={gchars} chars · digestFile={dchars} chars"
            ),
        )
    return refreshed


def fetch_prompt_for_ids(ids: list[str]) -> str:
    return (
        "getUserInfo once, then "
        "SELECT Id, CaseNumber, Subject, Status, Severity_Level__c, LastModifiedDate, IsClosed, "
        "SE_Initial_Response_Status__c, SE_Target_Response__c, First_Response_Date_Time__c, "
        "GUS_Investigation_Number__c, Display_Bug__c "
        "FROM Case WHERE OwnerId = '<that Id>' AND IsClosed = false "
        "ORDER BY LastModifiedDate DESC LIMIT 80 (never Description). "
        "Write {\"records\":[...]} to /tmp/owned-cases.json. "
        "Then three soqlQuery calls with ParentId IN those Ids only "
        "(CaseComment, EmailMessage with TextBody, CaseFeed). "
        "EmailMessage is mandatory — do not skip it. Never fetch a closed or not-owned case. Reply: fetched"
    )


def run_orgcs_fetch_sidecar(env: dict, chosen: str, ids: list[str]) -> int:
    """Short throwaway Claude session: dump comment+email+feed bodies; Python clips; process dies."""
    binary = find_claude_bin()
    if not binary or not FETCH_SYSTEM_FILE.is_file():
        return 0
    cli_model, express_model = fetch_sidecar_models(chosen)
    cmd = [
        binary,
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--setting-sources",
        "user",
        "--append-system-prompt-file",
        str(FETCH_SYSTEM_FILE),
        "--model",
        cli_model,
        "--effort",
        "low",
        "--dangerously-skip-permissions",
        "--disallowedTools",
        "Skill,ToolSearch," + SLACK_WRITE_DENY + "," + SLACK_READ_DENY,
        "--add-dir",
        str(SKILL_ROOT / "scripts"),
    ]
    flags = {
        "unrecognized": False,
        "requested": express_model,
        "fatal": False,
        "fatal_msg": "",
        "runner": "claude",
        "body_merges": 0,
        "ingested_overflow": set(),
        "fetch_mode": True,
    }
    prompt = fetch_prompt_for_ids(ids)
    prev_force = plan_force_model()
    if is_claude_model(chosen) and is_opus_model(chosen):
        set_plan_force_model(express_model)
        append_plan_step(
            kind="log",
            label=f"Fetching case comments and emails (clip before planner · {express_model}, not opus)",
        )
    else:
        append_plan_step(kind="log", label="Fetching case comments and emails (clip before planner)")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(plan_cli_cwd()),
            env=env,
            bufsize=0,
            start_new_session=sys.platform != "win32",
        )
    except OSError:
        set_plan_force_model(prev_force)
        return 0
    set_plan_proc(proc)
    deadline = time.monotonic() + FETCH_TIMEOUT_SEC
    stop_watch = threading.Event()

    def pump_stderr() -> None:
        if not proc.stderr:
            return
        try:
            for line in iter_fd_lines(proc.stderr.fileno(), deadline, proc):
                if PLAN_STOP.is_set():
                    return
                if keep_stderr(line):
                    append_plan_step(kind="log", label=clip(line, 220))
        except (subprocess.TimeoutExpired, OSError):
            return

    def watch_overflows() -> None:
        while not stop_watch.wait(0.8):
            stub_stale_planner_overflows()

    err_thread = threading.Thread(target=pump_stderr, daemon=True)
    watch = threading.Thread(target=watch_overflows, daemon=True)
    err_thread.start()
    watch.start()
    try:
        if proc.stdin:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass
        if proc.stdout:
            for line in iter_fd_lines(proc.stdout.fileno(), deadline, proc):
                if PLAN_STOP.is_set():
                    break
                handle_stream_line(line, flags)
                if flags.pop("need_sweep", False):
                    stub_stale_planner_overflows()
                objs = flags.get("thread_objects") or set()
                if "comment" in objs and "email" in objs:
                    if "feed" in objs or time.monotonic() > deadline - 20:
                        break
    except subprocess.TimeoutExpired:
        pass
    finally:
        stop_watch.set()
        watch.join(timeout=2)
        stub_stale_planner_overflows()
        kill_plan_proc(3)
        set_plan_proc(None)
        err_thread.join(timeout=2)
        set_plan_force_model(prev_force)
    return int(flags.get("body_merges") or 0)


def try_python_orgcs_activity(ids: list[str], seeded: dict) -> int:
    if not ids:
        return 0
    try:
        comments_by = fetch_comments_by_parent(ids)
        emails_by = fetch_emails_by_parent(ids)
        feeds_by = fetch_feeds_by_parent(ids)
    except Exception:
        return 0
    if not comments_by and not emails_by and not feeds_by:
        return 0
    tzname = str(seeded.get("timezone") or "").strip()
    n = 0
    for sec in seeded.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            match = re.search(r"/Case/(500[A-Za-z0-9]+)/", str(item.get("caseUrl") or ""))
            cid = match.group(1) if match else ""
            if not cid:
                continue
            chrono = merge_case_activity(
                comments_by.get(cid) or [],
                emails_by.get(cid) or [],
                feeds_by.get(cid) or [],
                tzname,
            )
            if chrono:
                item["activity"] = chrono
                n += 1
    return n


def gather_clipped_case_threads(
    env: dict, chosen: str, runner_id: str, seeded: dict, evidence: dict
) -> dict:
    """Owned-open queue first. Python clips comment and email bodies. The model sidecar runs only if that clip is empty."""
    rows = fetch_owned_open_cases()
    snapshot_ok = bool(rows) or _owned_fetch_ok()
    if snapshot_ok:
        repair_plan_payload(
            seeded, rows=rows or [], refresh_inbox=False, wipe_peek_summary=True
        )
        evidence = evidence_from_seed(seeded)
        write_planner_gather(evidence)
        append_plan_step(
            kind="log",
            label=f"Owned-open queue · {len(rows or [])} cases (closed/reassigned dropped)",
        )
        ids = case_ids_from_evidence(evidence)
        n_py = try_python_orgcs_activity(ids, seeded) if ids else 0
        if n_py:
            append_plan_step(kind="log", label=f"Python clipped comments/email on {n_py} cases")
            return attach_clipped_activity(seeded, evidence)
    if runner_id == "claude" or find_claude_bin():
        run_orgcs_fetch_sidecar(env, chosen, [])
        rows = fetch_owned_open_cases()
        snapshot_ok = bool(rows) or _owned_fetch_ok()
        if snapshot_ok:
            repair_plan_payload(
                seeded, rows=rows or [], refresh_inbox=False, wipe_peek_summary=True
            )
            evidence = evidence_from_seed(seeded)
            write_planner_gather(evidence)
            ids = case_ids_from_evidence(evidence)
            n_py = try_python_orgcs_activity(ids, seeded) if ids else 0
            if n_py:
                append_plan_step(kind="log", label=f"Python clipped comments/email on {n_py} cases")
            return attach_clipped_activity(seeded, evidence)
    append_plan_step(
        kind="log",
        label="Owned-open OrgCS fetch did not land — not replacing the previous page from a failed query",
    )
    return write_planner_gather(evidence_from_seed(seeded))


def request_planner_halt(flags: dict) -> None:
    if flags.get("fetch_mode"):
        return
    PLAN_STOP.set()


def handle_stream_line(line: str, flags: dict) -> None:
    raw = strip_ansi(line or "").strip()
    if not raw:
        return
    fatal = plan_fatal_message(raw)
    if fatal:
        flags["fatal"] = True
        flags["fatal_msg"] = fatal
        request_planner_halt(flags)
        append_plan_step(kind="error", label=fatal)
        return
    if "unrecognized_model" in raw:
        flags["unrecognized"] = True
    try:
        evt = json.loads(raw)
    except json.JSONDecodeError:
        if flags.get("runner") == "opencode":
            return
        append_plan_step(kind="log", label=clip(raw, 220))
        return
    if not isinstance(evt, dict):
        return
    et = str(evt.get("type") or evt.get("event") or "")
    props = evt.get("properties") if isinstance(evt.get("properties"), dict) else {}
    part = evt.get("part") if isinstance(evt.get("part"), dict) else {}
    if et in {"tool.start", "tool.call", "tool_use"} or et.endswith("tool.start"):
        name = str(
            evt.get("name")
            or evt.get("tool")
            or props.get("tool")
            or props.get("name")
            or part.get("tool")
            or "tool"
        )
        inp = props.get("args") or props.get("input") or evt.get("input") or part.get("state") or {}
        if not isinstance(inp, dict):
            inp = {}
        ingest_read_overflow_path(name, inp, flags)
        append_plan_step(kind="tool", label=label_tool(name), detail=tool_detail(name, inp))
        return
    if et in {"session.error", "message.error"}:
        err = str(evt.get("error") or props.get("error") or evt.get("message") or raw)
        fatal = plan_fatal_message(err) or clip(err, 220)
        if re.search(r"unexpected server error|unknownerror", fatal, re.I):
            flags["runner_down"] = True
        flags["fatal"] = True
        flags["fatal_msg"] = fatal
        request_planner_halt(flags)
        append_plan_step(kind="error", label=fatal)
        return
    if et in {
        "stream_event",
        "content_block_delta",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "message_start",
        "message_stop",
    }:
        return
    subtype = str(evt.get("subtype") or "")
    if et == "system" and subtype == "init":
        model = str(evt.get("model") or "")
        requested = str(flags.get("requested") or plan_force_model() or model)
        append_plan_step(kind="init", label="Started" + (f" · {requested}" if requested else ""))
        return
    if et == "assistant":
        msg = evt.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                thought = clip(block.get("thinking") or block.get("text") or "", 400)
                if thought:
                    append_plan_step(kind="text", label="Thinking", detail=thought)
            elif btype == "text":
                text = redact((block.get("text") or "").strip())
                if text:
                    if re.search(r"prompt is too long", text, re.I):
                        flags["prompt_too_long"] = True
                        flags["fatal"] = False
                        flags["fatal_msg"] = ""
                    first = text.splitlines()[0]
                    append_plan_step(kind="text", label=clip(first, 140), detail=clip(text, 800))
            elif btype == "tool_use":
                name = str(block.get("name") or "tool")
                label = label_tool(name)
                inp = block.get("input") or {}
                if not isinstance(inp, dict):
                    inp = {}
                ingest_read_overflow_path(name, inp, flags)
                append_plan_step(kind="tool", label=label, detail=tool_detail(name, inp))
        return
    if et == "user":
        msg = evt.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            ingest_overflow_from_tool_result(block.get("content"), flags)
            flags["need_sweep"] = True
            err = bool(block.get("is_error"))
            append_plan_step(
                kind="error" if err else "result",
                label="Tool error" if err else "Tool done",
                detail=result_text(block.get("content")),
            )
        return
    if et == "result":
        err = bool(evt.get("is_error"))
        dur = evt.get("duration_ms")
        label = "Failed" if err else "Finished"
        try:
            if dur:
                label += f" · {max(0, int(dur) // 1000)}s"
        except (TypeError, ValueError):
            pass
        detail = str(evt.get("result") or "")
        if err and re.search(r"prompt is too long", detail, re.I):
            flags["prompt_too_long"] = True
            flags["fatal"] = False
            flags["fatal_msg"] = ""
        append_plan_step(
            kind="error" if err else "done",
            label=label,
            detail=clip(detail, 240),
        )
        return
    if et in {"error", "stderr"}:
        append_plan_step(
            kind="error",
            label=clip(str(evt.get("error") or evt.get("message") or raw), 220),
        )


def set_nonblocking(fd: int) -> None:
    try:
        os.set_blocking(fd, False)
    except OSError:
        pass


def read_fd_chunk(fd: int, size: int = 8192) -> bytes | None:
    try:
        return os.read(fd, size)
    except BlockingIOError:
        return None
    except OSError:
        return b""


def iter_fd_lines(fd: int, deadline: float, proc: subprocess.Popen):
    buf = ""
    eof = False
    set_nonblocking(fd)
    while not eof:
        if PLAN_STOP.is_set():
            kill_plan_proc()
            break
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(proc.args, PLAN_TIMEOUT_SEC)
        ready, _, _ = select.select([fd], [], [], 0.4)
        chunk = b""
        if ready:
            got = read_fd_chunk(fd)
            if got is None:
                chunk = b""
            elif not got:
                eof = True
            else:
                chunk = got
        elif proc.poll() is not None:
            leftover = read_fd_chunk(fd, 65536)
            if leftover:
                chunk = leftover
            else:
                eof = True
        if chunk:
            buf += chunk.decode("utf-8", "replace")
        while True:
            nl = buf.find("\n")
            if nl < 0:
                break
            line, buf = buf[:nl], buf[nl + 1 :]
            yield line
    if buf.strip():
        yield buf


def keep_stderr(line: str) -> bool:
    text = (line or "").strip()
    if not text or text.startswith("{") or text.startswith("["):
        return False
    low = text.lower()
    return any(
        word in low
        for word in ("error", "failed", "unrecognized", "denied", "timeout", "not found", "warning")
    )


def _is_google_cal_event(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("composed") is True or str(item.get("id") or "").startswith("plan-"):
        return False
    if item.get("autoBreak") is True or str(item.get("kind") or "").lower() == "break":
        return False
    if item.get("eventId") or item.get("htmlLink") or item.get("invite") is True:
        return True
    return str(item.get("kind") or "").lower() == "meeting"


def _is_custom_plan_block(item: dict) -> bool:
    if not isinstance(item, dict) or _is_google_cal_event(item):
        return False
    if item.get("composed") is True or str(item.get("id") or "").startswith("plan-"):
        return True
    kind = str(item.get("kind") or "").lower()
    if kind == "break" or item.get("autoBreak") is True:
        return True
    if kind in ("plan", "task", "case") and item.get("startStamp") and item.get("endStamp"):
        return True
    return False


def snapshot_payload() -> dict:
    data = load_live_briefing()
    remind = []
    tzname = str(data.get("timezone") or "").strip()
    now, _ = shift_now(data)
    done_keys = set()
    sanit = None
    try:
        sanit = _sanitize_mod()
        done_keys = sanit.collect_done_keys(data) | sanit._ledger_keys(load_done_ledger())
    except Exception:
        sanit = None
    for section, item in walk_briefing_items(data):
        if not item or item.get("assembled") is True:
            continue
        if str(item.get("kind") or "").lower() == "assembled":
            continue
        if not is_plan_title(section):
            continue
        google = _is_google_cal_event(item)
        custom = _is_custom_plan_block(item)
        if not google and not custom:
            continue
        if item.get("done") is True:
            continue
        if sanit and done_keys and any(k in done_keys for k in sanit.item_done_keys(item)):
            continue
        start = parse_stamp(item.get("startStamp") or "", tzname)
        if not start or start <= now:
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        end = parse_stamp(item.get("endStamp") or "", tzname)
        remind.append(
            {
                "id": item_id,
                "label": item.get("label") or "Upcoming event",
                "startMs": int(start.timestamp() * 1000),
                "endMs": int(end.timestamp() * 1000) if end else None,
                "source": "google" if google else "plan",
                "eventId": item.get("eventId") or "",
            }
        )
    remind.sort(key=lambda row: row["startMs"])
    pending = {"slack": 0, "mail": 0, "followUps": 0, "needsNow": 0, "gus": 0}
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "").lower()
        bag = [it for it in (sec.get("items") or []) if isinstance(it, dict)]
        for grp in sec.get("groups") or []:
            if isinstance(grp, dict):
                bag.extend(it for it in (grp.get("items") or []) if isinstance(it, dict))
        n = sum(1 for it in bag if it.get("done") is not True)
        if title.startswith("slack"):
            pending["slack"] += n
        elif re.search(r"\b(mail|email|gmail)\b", title):
            pending["mail"] += n
        elif "follow-up" in title or "follow up" in title:
            pending["followUps"] += n
        elif "needs us now" in title or "needs you now" in title:
            pending["needsNow"] += n
        elif title.startswith("gus"):
            pending["gus"] += n
    summary_lines = ["30 Minutes Until Logout"]
    if pending["needsNow"]:
        summary_lines.append(str(pending["needsNow"]) + " Needs Us Now")
    if pending["followUps"]:
        word = " Follow-Up" if pending["followUps"] == 1 else " Follow-Ups"
        summary_lines.append(str(pending["followUps"]) + word)
    if pending["slack"]:
        summary_lines.append(str(pending["slack"]) + " Slack")
    if pending["mail"]:
        summary_lines.append(str(pending["mail"]) + " Email")
    if pending["gus"]:
        summary_lines.append(str(pending["gus"]) + " GUS")
    if len(summary_lines) == 1:
        summary_lines.append("Nothing Still Open")
    else:
        summary_lines[-1] += " Still Open"
    pending["summary"] = "\n".join(summary_lines)
    end_ms = 0
    end_match = re.match(r"^(\d{1,2}):(\d{2})$", str(data.get("shiftEnd") or "").strip())
    if end_match:
        end = now.replace(
            hour=int(end_match.group(1)),
            minute=int(end_match.group(2)),
            second=0,
            microsecond=0,
        )
        end_ms = int(end.timestamp() * 1000)
    return {
        "ok": True,
        "generatedAt": data.get("generatedAt"),
        "stamp": data.get("stamp"),
        "timezone": tzname,
        "shiftEnd": data.get("shiftEnd") or "",
        "shiftEndMs": end_ms,
        "pending": pending,
        "needYou": data.get("needYou"),
        "openCases": data.get("openCases"),
        "reminders": remind,
        "page": "/current.html",
    }


def run_plan_job(token: str, model: str = "", runner_id: str = "") -> None:
    try:
        run_plan_job_inner(token, model, runner_id)
    finally:
        try:
            sweep_run_scratch()
        except Exception:
            pass
        PLAN_ACTIVE.clear()
        set_plan_proc(None)
        set_plan_force_model("")


def run_plan_job_inner(token: str, model: str = "", runner_id: str = "") -> None:
    if not is_packed_extension_skill(SKILL_ROOT):
        write_plan_state(
            state="error",
            error="Run Planner uses the unpacked Chrome extension skill/, not Claude or Cursor skills.",
            finishedAt=now_stamp(),
        )
        return
    sweep_run_scratch()
    run_started = time.time()
    try:
        runner = resolve_plan_runner(runner_id)
    except RuntimeError as exc:
        write_plan_state(
            state="error",
            error=str(exc) or "Pick a runner on the Token screen.",
            finishedAt=now_stamp(),
        )
        return
    try:
        chosen = resolve_model(model)
    except RuntimeError:
        write_plan_state(
            state="error",
            error="invalid model",
            finishedAt=now_stamp(),
        )
        return
    try:
        tool, switch_note = resolve_tool_runner(runner, chosen)
    except RuntimeError as exc:
        write_plan_state(
            state="error",
            error=str(exc)[:400],
            finishedAt=now_stamp(),
        )
        return
    env = os.environ.copy()
    env.update(claude_settings_env())
    env["DAY_PLANNER_NO_OPEN"] = "1"
    env["DAY_PLANNER_NO_BRIDGE"] = "1"
    env["DAY_PLANNER_RUNNER"] = tool["id"]
    env["DAY_PLANNER_SKILL"] = str(SKILL_ROOT)
    env["ENGINEER_DAY_PLANNER_ROOT"] = str(SKILL_ROOT)
    if uses_express_proxy(tool["id"]):
        env["ANTHROPIC_API_KEY"] = token
        env["ANTHROPIC_BASE_URL"] = f"http://{HOST}:{PORT}"
        env.pop("ANTHROPIC_MODEL", None)
        set_plan_force_model(chosen)
    elif tool["id"] == "opencode":
        strip_anthropic_env(env)
        set_plan_force_model("")
    else:
        env["ANTHROPIC_API_KEY"] = token
        set_plan_force_model("")
    reset_plan_log()
    write_plan_state(
        state="running",
        error="",
        model=chosen,
        gatewayModel=chosen,
        runner=tool["id"],
        runnerLabel=tool["label"],
        startedAt=now_stamp(),
        finishedAt="",
        steps=[],
        headline=f"Starting {chosen} with {tool['label']}…",
        stepCount=0,
    )
    append_plan_step(kind="init", label=f"Starting {chosen} · {tool['label']}")
    if switch_note:
        append_plan_step(kind="log", label=switch_note)
    flags = {"unrecognized": False, "requested": chosen, "fatal": False, "fatal_msg": "", "runner": tool["id"]}
    reset_google_fallback()
    append_plan_step(kind="log", label="Gathering evidence (cases, Slack/Mail candidates, CaseComment activity)")
    seeded = None
    try:
        try:
            pathlib.Path("/tmp/plan-ai.json").unlink()
        except OSError:
            pass
        try:
            pathlib.Path("/tmp/plan-inbox.json").unlink()
        except OSError:
            pass
        try:
            CASE_ACTIVITY_FILE.unlink()
        except OSError:
            pass
        try:
            CASE_DIGEST_FILE.unlink()
        except OSError:
            pass
        try:
            PLANNER_DIGEST_FILE.unlink()
        except OSError:
            pass
        try:
            PLANNER_CARDS_FILE.unlink()
        except OSError:
            pass
        try:
            OWNED_CASES_FILE.unlink()
        except OSError:
            pass
        stub_stale_planner_overflows()
        seeded = seed_plan_from_live()
        evidence = write_planner_gather(evidence_from_seed(seeded))
        evidence = gather_clipped_case_threads(env, chosen, tool["id"], seeded, evidence)
        n_slack = len(evidence.get("slackCandidates") or [])
        n_mail = len(evidence.get("mailCandidates") or [])
        _cap_digest_file(PLANNER_DIGEST_FILE, MAX_DIGEST_CHARS)
        _cap_text_file(PLANNER_INBOX_TXT, MAX_INBOX_CHARS)
        cases = evidence.get("cases") or []
        n_digest, n_act = digest_activity_stats()
        append_plan_step(
            kind="log",
            label=(
                f"Gathered evidence · {evidence.get('openCases') or 0} cases · "
                f"activity on {n_act}/{len(cases)} · digest={n_digest} · "
                f"{n_slack} Slack candidates · {n_mail} Mail candidates — "
                "model will write Peek and classify inbox"
            ),
        )
        if tool["id"] == "claude":
            append_plan_step(kind="log", label="Claude planner tools: Write only. No MCP servers.")
        append_plan_step(kind="log", label="Analyzing Peek and inbox with the model")
    except Exception as exc:
        append_plan_step(kind="log", label=f"Live gather incomplete: {exc}")
    if PLAN_STOP.is_set() and read_plan_state().get("state") != "running":
        return

    def run_cli(prompt: str | None = None, timeout_sec: int | None = None) -> tuple[int, bool, bool]:
        cmd = plan_command(tool, chosen)
        prompt_on_argv = tool["id"] in {"opencode", "codex", "gemini", "aider"}
        if prompt and prompt_on_argv:
            baked = compact_plan_prompt(tool["id"])
            cmd = [prompt if part == baked else part for part in cmd]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(plan_cli_cwd()),
            env=env,
            bufsize=0,
            start_new_session=sys.platform != "win32",
        )
        set_plan_proc(proc)
        deadline = time.monotonic() + int(timeout_sec or PLAN_TIMEOUT_SEC)
        try:
                if proc.stdin:
                    if not prompt_on_argv:
                        text = prompt if prompt is not None else compact_plan_prompt(tool["id"])
                        proc.stdin.write(text.encode("utf-8"))
                    proc.stdin.close()
        except BrokenPipeError:
            pass

        oc_last = {"perm": ""}

        def pump_stderr() -> None:
            if not proc.stderr:
                return
            try:
                for line in iter_fd_lines(proc.stderr.fileno(), deadline, proc):
                    if PLAN_STOP.is_set():
                        return
                    if tool["id"] == "opencode":
                        handle_opencode_log(line, flags, oc_last)
                        continue
                    if "unrecognized_model" in line:
                        flags["unrecognized"] = True
                    fatal = plan_fatal_message(line)
                    if fatal:
                        flags["fatal"] = True
                        flags["fatal_msg"] = fatal
                        PLAN_STOP.set()
                    if keep_stderr(line):
                        append_plan_step(kind="log", label=clip(line, 220))
            except subprocess.TimeoutExpired:
                return
            except OSError:
                return

        err_thread = threading.Thread(target=pump_stderr, daemon=True)
        err_thread.start()
        stop_watch = threading.Event()

        def watch_overflows() -> None:
            while not stop_watch.wait(1.2):
                stub_stale_planner_overflows()

        watch = threading.Thread(target=watch_overflows, daemon=True)
        watch.start()
        timed_out = False
        try:
            if proc.stdout:
                for line in iter_fd_lines(proc.stdout.fileno(), deadline, proc):
                    if PLAN_STOP.is_set():
                        break
                    handle_stream_line(line, flags)
                    if flags.pop("need_sweep", False):
                        stub_stale_planner_overflows()
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            stop_watch.set()
            watch.join(timeout=2)
            stub_stale_planner_overflows()
            err_thread.join(timeout=2)
            if proc.poll() is None:
                kill_plan_proc(15 if timed_out else 3)
            set_plan_proc(None)
        return (
            int(proc.returncode if proc.returncode is not None else (1 if PLAN_STOP.is_set() else 0)),
            bool(flags["unrecognized"] or flags.get("fatal")),
            timed_out,
        )

    user_abort = False
    try:
        if not user_abort:
            link_planner_evidence()
            classify_inbox(run_cli)
            PLAN_STOP.clear()
            flags["prompt_too_long"] = False
            flags["fatal"] = False
            flags["fatal_msg"] = ""
            flags["runner_down"] = False
        try:
            pathlib.Path("/tmp/plan-ai.json").unlink()
        except OSError:
            pass
        code, unrecognized, timed_out = run_cli()
        user_abort = PLAN_STOP.is_set() and read_plan_state().get("state") != "running"
        if user_abort and not pathlib.Path("/tmp/plan-ai.json").is_file():
            return
    except OSError as exc:
        msg = str(exc) or f"Could not start {tool['label']}"
        append_plan_step(kind="error", label=msg)
        write_plan_state(
            state="error",
            error=msg,
            finishedAt=now_stamp(),
        )
        return
    finally:
        set_plan_force_model("")
    stub_stale_planner_overflows()
    if seeded is not None:
        pathlib.Path("/tmp/plan.json").write_text(
            json.dumps(seeded, indent=2) + "\n", encoding="utf-8"
        )
    killed = timed_out or code in (-9, -15, 137, 143)
    prompt_too_long = bool(flags.get("prompt_too_long")) or "prompt is too long" in str(flags.get("fatal_msg") or "").lower()
    filled = False
    if prompt_too_long:
        append_plan_step(
            kind="log",
            label="Prompt is too long. Keeping the analysis and building Today's plan next.",
        )
        flags["fatal"] = False
        flags["fatal_msg"] = ""
        if code != 0:
            code = 0
    ai_path = pathlib.Path("/tmp/plan-ai.json")
    has_ai = ai_path.is_file() and ai_path.stat().st_mtime >= (run_started - 2)
    if not has_ai and not user_abort:
        if finish_plan_from_evidence():
            filled = True
            append_plan_step(kind="log", label="Filled what the model left, from this run's clips.")
    if not user_abort and not killed:
        if prompt_too_long or flags.get("runner_down"):
            PLAN_STOP.clear()
        if flags.get("runner_down"):
            append_plan_step(
                kind="log",
                label="Runner server error. Today's plan filled from the finished ranks.",
            )
            fallback_today_plan()
            code = 0
            flags["fatal"] = False
            flags["fatal_msg"] = ""
        else:
            if uses_express_proxy(tool["id"]):
                set_plan_force_model(chosen)
            install_today_plan(run_cli)
            set_plan_force_model("")
    merge_classified_inbox()
    published = salvage_publish_plan(run_started)
    if not published and not user_abort and repair_known_publish_blocks():
        append_plan_step(kind="log", label="Fixed the publish blockers from this run's clips.")
        published = salvage_publish_plan(run_started)
    if prompt_too_long or flags.get("runner_down"):
        flags["fatal"] = False
        flags["fatal_msg"] = ""
        if code != 0:
            code = 0
    if published:
        if filled or prompt_too_long:
            publish_label = "Published from this run's clips. Today's plan was built after the analysis."
        elif killed:
            publish_label = "Timed out; published analyzed /tmp/plan.json"
        else:
            publish_label = "Published after analysis, then Today's plan."
        append_plan_step(
            kind="log",
            label=publish_label,
        )
        repair_published_page()
        write_plan_state(
            state="ok",
            error="",
            model=chosen,
            gatewayModel=chosen,
            runner=tool["id"],
            runnerLabel=tool["label"],
            finishedAt=now_stamp(),
            headline="Published",
            pageMtime=PAGE.stat().st_mtime if PAGE.is_file() else 0,
        )
        return
    if killed:
        append_plan_step(kind="error", label="Planner run timed out")
        write_plan_state(
            state="error",
            error="Planner run timed out.",
            finishedAt=now_stamp(),
        )
        return
    if code != 0:
        msg = f"{tool['label']} exited {code}."
        if flags.get("fatal"):
            msg = str(flags.get("fatal_msg") or f"{tool['label']} failed.")
        elif unrecognized:
            msg = (
                f"{tool['label']} could not use {chosen}. "
                "Express models run through Claude Code or OpenCode."
            )
        append_plan_step(kind="error", label=msg)
        write_plan_state(
            state="error",
            error=msg[:400],
            model=chosen,
            runner=tool["id"],
            finishedAt=now_stamp(),
        )
        return
    ai_analyzed = False
    try:
        ai = json.loads(pathlib.Path("/tmp/plan-ai.json").read_text(encoding="utf-8"))
        ai_analyzed = isinstance(ai, dict) and (
            ai.get("aiAnalyzed") is True or ai.get("inboxReviewed") is True
        )
    except Exception:
        ai_analyzed = False
    if ai_analyzed:
        if LAST_CHECK_FAILS:
            reason = "Self-check blocked publish — previous page kept: " + "; ".join(LAST_CHECK_FAILS)[:240]
        else:
            reason = "Publish failed — previous page kept"
            if LAST_PUBLISH_ERROR:
                reason += ": " + LAST_PUBLISH_ERROR
        append_plan_step(kind="error", label=reason[:400])
        write_plan_state(
            state="error",
            error=reason[:400],
            model=chosen,
            runner=tool["id"],
            finishedAt=now_stamp(),
        )
        return
    append_plan_step(
        kind="error",
        label="Model did not analyze Peek and inbox — previous page kept",
    )
    write_plan_state(
        state="error",
        error="Model did not analyze Peek and inbox. Previous page kept.",
        model=chosen,
        runner=tool["id"],
        finishedAt=now_stamp(),
    )
    return


def start_plan_run(token: str, model: str = "", runner_id: str = "") -> dict:
    if plan_job_busy():
        return read_plan_state()
    PLAN_STOP.clear()
    PLAN_ACTIVE.set()
    with PLAN_LOCK:
        _write_plan_state_unlocked(
            state="running",
            error="",
            pid=os.getpid(),
            startedAt=now_stamp(),
            finishedAt="",
            steps=[],
            headline="Starting…",
            stepCount=0,
        )
    threading.Thread(
        target=run_plan_job, args=(token, model, runner_id), daemon=True
    ).start()
    return read_plan_state()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _proxy_anthropic(self):
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self._json(403, {"error": "local only"})
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        upstream = gateway_base().rstrip("/") + path
        if parsed.query:
            upstream += "?" + parsed.query
        up = urllib.parse.urlparse(upstream)
        if (up.hostname or "") in {"127.0.0.1", "localhost", "::1"} and int(up.port or 0) == int(PORT):
            self._json(508, {"error": "gateway proxy loop"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.command == "POST" and raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and path.rstrip("/").endswith("messages"):
                forced = plan_force_model()
                if forced:
                    payload["model"] = forced
                    for key in (
                        "thinking",
                        "reasoning",
                        "reasoning_effort",
                        "reasoningEffort",
                        "output_config",
                    ):
                        payload.pop(key, None)
                    extra = payload.get("extra_body")
                    if isinstance(extra, dict):
                        extra.pop("reasoning", None)
                        extra.pop("thinking", None)
                    raw = json.dumps(payload).encode("utf-8")
        headers = {}
        for key in (
            "x-api-key",
            "anthropic-version",
            "anthropic-beta",
            "content-type",
            "accept",
            "authorization",
        ):
            val = self.headers.get(key)
            if val:
                headers[key] = val
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("anthropic-version", "2023-06-01")
        key = headers.get("x-api-key") or headers.get("X-Api-Key") or ""
        if key and not headers.get("authorization") and not headers.get("Authorization"):
            headers["Authorization"] = "Bearer " + key
        if raw and self.command != "GET":
            headers["Content-Length"] = str(len(raw))
        req = urllib.request.Request(
            upstream,
            data=raw if self.command != "GET" else None,
            method=self.command,
            headers=headers,
        )
        try:
            resp = urllib.request.urlopen(req, timeout=PLAN_TIMEOUT_SEC, context=ssl_ctx())
            status = getattr(resp, "status", 200)
            resp_headers = resp.headers
            body_iter = resp
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = exc.headers
            body_iter = exc
        self.send_response(status)
        for key, val in (resp_headers or {}).items():
            if key.lower() in {
                "content-length",
                "transfer-encoding",
                "connection",
                "keep-alive",
                "content-encoding",
            }:
                continue
            self.send_header(key, val)
        self.end_headers()
        try:
            while True:
                chunk = body_iter.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except BrokenPipeError:
            return

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        note_bridge_traffic(path)
        if parsed.path.startswith("/v1/"):
            self._proxy_anthropic()
            return
        if path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": SERVICE,
                    "root": str(SKILL_ROOT),
                    "port": PORT,
                    "ask": True,
                    "plan": True,
                    "models": True,
                    "steps": True,
                    "mcp": True,
                    "omni": True,
                    "briefing": True,
                    "busy": plan_job_busy(),
                    "version": PROCESS_VERSION or packed_extension_version(),
                },
            )
            return
        if path == "/notepad":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            self._json(200, {"ok": True, "items": _sanitize_mod().load_notepad_items()})
            return
        if path == "/snapshot":
            try:
                self._json(200, snapshot_payload())
            except FileNotFoundError:
                self._json(200, unpublished_snapshot())
            except Exception as exc:
                self._json(500, {"error": str(exc) or "snapshot failed"})
            return
        if path == "/omni/check":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                self._json(200, omni_check())
            except Exception:
                self._json(200, {"ok": True, "action": "skip", "reason": "error", "day": ""})
            return
        if path == "/plan/status":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                after = max(0, int((qs.get("after") or ["0"])[0]))
            except ValueError:
                after = 0
            state = read_plan_state()
            steps = state.get("steps") if isinstance(state.get("steps"), list) else []
            payload = dict(state)
            payload.pop("token", None)
            payload["steps"] = steps[after:]
            payload["stepCount"] = len(steps)
            payload["canStop"] = plan_job_busy()
            self._json(200, payload)
            return
        if path == "/mcp/status":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            qs = urllib.parse.parse_qs(parsed.query)
            runner = str((qs.get("runner") or [""])[0] or "").strip()
            try:
                mcps = planner_mcp_status(runner)
            except Exception as exc:
                self._json(502, {"error": str(exc) or "mcp status failed"})
                return
            self._json(200, {"ok": True, "mcps": mcps, "runner": canonicalize_runner_id(runner)})
            return
        if path == "/runners":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                runners = public_runner_catalog()
            except Exception as exc:
                self._json(502, {"error": str(exc) or "could not list runners"})
                return
            self._json(200, {"ok": True, "runners": runners})
            return
        if path == "/gateway-token":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                token = gateway_token_from_devbar()
            except Exception as exc:
                self._json(502, {"error": str(exc) or "gateway token unavailable"})
                return
            self._json(200, {"ok": True, "token": token, "source": "devbar"})
            return
        if path == "/briefing.json":
            try:
                payload = load_live_briefing()
            except FileNotFoundError:
                self._json(200, unpublished_snapshot())
                return
            except Exception as exc:
                self._json(500, {"error": str(exc) or "briefing failed"})
                return
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path in ("/", "/index.html", "/current.html"):
            try:
                raw = live_page_html()
            except Exception:
                raw = unpublished_page_html()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/v1/"):
            self._proxy_anthropic()
            return
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path in ("/__bye", "/__shutdown"):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            if path == "/__bye":
                note_page_bye()
                self._json(200, {"ok": True})
                return
            self._json(200, {"ok": True})
            threading.Thread(
                target=_shutdown_and_unload, args=(self.server,), daemon=True
            ).start()
            return
        if path == "/mcp/google-auth":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                start_dx_google_auth()
            except Exception as exc:
                self._json(502, {"error": str(exc) or "could not start the Google sign-in"})
                return
            self._json(200, {"ok": True, "started": True})
            return
        if path == "/mcp/gus-auth":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                start_dx_gus_auth()
            except Exception as exc:
                self._json(502, {"error": str(exc) or "could not start the GUS sign-in"})
                return
            self._json(200, {"ok": True, "started": True})
            return
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return
        note_bridge_traffic(path)
        if path == "/orgcs/session":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            set_orgcs_browser_sid(str(body.get("sid") or ""))
            if not orgcs_browser_session_ok():
                set_orgcs_browser_sid("")
                self._json(401, {"error": "Sign in to OrgCS in this browser, then try again."})
                return
            self._json(200, {"ok": True})
            return
        if path == "/notepad":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            items = _sanitize_mod().save_notepad_items(body.get("items"))
            self._json(200, {"ok": True, "items": items})
            return
        if path == "/skill/adopt":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            version = str(body.get("version") or "").strip()
            files = body.get("files") if isinstance(body.get("files"), list) else []
            try:
                skill = write_adopted_skill(version, files)
            except Exception as exc:
                self._json(400, {"error": str(exc) or "could not store this version"})
                return
            self._json(200, {"ok": True, "version": version})
            threading.Thread(
                target=_launch_adopted_and_stop, args=(self.server, skill, version), daemon=True
            ).start()
            return
        if path == "/models":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            try:
                token = resolve_gateway_token((body.get("token") or body.get("gatewayToken") or "").strip())
            except Exception as exc:
                self._json(400, {"error": str(exc) or "Express gateway token required"})
                return
            if not token:
                self._json(400, {"error": "Paste the Express LLM Gateway token in the extension, or install DevBar."})
                return
            try:
                models = list_gateway_models(token)
            except Exception as exc:
                msg = str(exc) or "could not list models"
                if "sk-" in msg or "ANTHROPIC_API_KEY" in msg:
                    msg = "Could not list models. Check the Express gateway token."
                self._json(502, {"error": msg})
                return
            self._json(200, {"ok": True, "models": models, "default": preferred_planner_default(models)})
            return
        if path == "/page/sync":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            payload = body.get("data") if isinstance(body.get("data"), dict) else None
            if payload is None and isinstance(body, dict) and "sections" in body:
                payload = {k: v for k, v in body.items() if k not in ("token", "gatewayToken", "model")}
            if not isinstance(payload, dict):
                self._json(400, {"error": "page data is required"})
                return
            try:
                if not briefing_is_today(payload):
                    raise FileNotFoundError("planner page not published yet")
                try:
                    save_done_ledger_from_data(payload)
                    sanit = _sanitize_mod()
                    sanit.apply_persisted_done(payload, None, load_done_ledger())
                    sanit.omit_done_inbox_rows(payload)
                    sanit.drop_done_from_plan(payload)
                except Exception:
                    pass
                write_briefing_data(payload)
            except FileNotFoundError:
                self._json(404, {"error": "planner page not published yet"})
                return
            except Exception as exc:
                self._json(502, {"error": str(exc) or "could not save page"})
                return
            self._json(200, {"ok": True})
            return
        if path == "/plan/run":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            current = read_plan_state()
            if plan_job_busy():
                self._json(
                    409,
                    {
                        "error": "a planner run is already in progress",
                        **{k: v for k, v in current.items() if k != "token"},
                    },
                )
                return
            try:
                token = resolve_gateway_token((body.get("token") or body.get("gatewayToken") or "").strip())
            except Exception as exc:
                self._json(400, {"error": str(exc) or "Express gateway token required"})
                return
            if not token:
                self._json(400, {"error": "Paste the Express LLM Gateway token in extension options, or install DevBar."})
                return
            try:
                requested = sanitize_model((body.get("model") or "").strip())
            except RuntimeError:
                self._json(400, {"error": "invalid model"})
                return
            if not requested:
                requested = default_model()
            runner_id = canonicalize_runner_id(
                (body.get("runner") or body.get("planRunner") or "").strip()
            ) or "claude"
            try:
                resolve_plan_runner(runner_id)
            except RuntimeError as exc:
                self._json(400, {"error": str(exc) or "Pick a runner on the Token screen."})
                return
            state = start_plan_run(token, requested, runner_id)
            self._json(
                202,
                {
                    "ok": True,
                    "model": requested,
                    "requested": requested,
                    "runner": runner_id,
                    **{k: v for k, v in state.items() if k != "token"},
                },
            )
            return
        if path == "/plan/stop":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json(403, {"error": "local only"})
                return
            state = stop_plan_run("Stopped.")
            self._json(
                200,
                {
                    "ok": True,
                    **{k: v for k, v in state.items() if k != "token"},
                },
            )
            return
        if path == "/calendar/replies":
            tz = str(body.get("timezone") or "").strip()
            try:
                result = fetch_primary_events(
                    tz,
                    shift_start=body.get("shiftStart") or body.get("shift_start"),
                    shift_end=body.get("shiftEnd") or body.get("shift_end"),
                )
                replies = []
                for ev in result.get("events") or []:
                    if not isinstance(ev, dict) or not ev.get("eventId"):
                        continue
                    replies.append(
                        {
                            "eventId": ev.get("eventId") or "",
                            "rsvp": ev.get("rsvp") or "",
                            "invite": bool(ev.get("invite")),
                        }
                    )
                self._json(200, {"ok": True, "events": replies})
            except Exception as exc:
                msg = str(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        msg = exc.read().decode()[:400]
                    except Exception:
                        msg = str(exc)
                self._json(502, {"error": msg or "calendar reply lookup failed"})
            return
        if path == "/calendar/refresh":
            tz = str(body.get("timezone") or "").strip()
            try:
                result = fetch_primary_events(
                    tz,
                    body.get("timeMin") or body.get("time_min"),
                    body.get("timeMax") or body.get("time_max"),
                    important_ids=body.get("importantEventIds") or body.get("important_event_ids") or [],
                    shift_start=body.get("shiftStart") or body.get("shift_start"),
                    shift_end=body.get("shiftEnd") or body.get("shift_end"),
                )
                try:
                    merge_calendar_into_page(result.get("events") or [], tz)
                    result["saved"] = True
                except FileNotFoundError:
                    result["saved"] = False
                except Exception:
                    result["saved"] = False
                self._json(200, result)
            except Exception as exc:
                msg = str(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        msg = exc.read().decode()[:400]
                    except Exception:
                        msg = str(exc)
                self._json(502, {"error": msg or "calendar refresh failed"})
            return
        if path == "/ask":
            q = (body.get("question") or body.get("q") or "").strip()
            if not q:
                self._json(400, {"error": "question is required"})
                return
            if len(q) > 400:
                self._json(400, {"error": "question is too long"})
                return
            try:
                token = resolve_gateway_token((body.get("token") or body.get("gatewayToken") or "").strip())
            except Exception as exc:
                self._json(400, {"error": str(exc) or "Express gateway token required"})
                return
            if not token:
                self._json(400, {"error": "Paste the Express LLM Gateway token in the extension, or install DevBar."})
                return
            try:
                chosen = resolve_model((body.get("model") or "").strip())
            except RuntimeError:
                self._json(400, {"error": "invalid model"})
                return
            started = time.perf_counter()
            try:
                data = ask_payload(body)
                history = clip_ask_history(body.get("history"))
                toggled = try_ask_done(q, data, history, token=token, model=chosen)
                if toggled:
                    payload = {
                        "answer": toggled.get("answer") or "",
                        "model": chosen,
                        "ms": max(0, int((time.perf_counter() - started) * 1000)),
                        "mode": toggled.get("mode") or "done",
                        "reload": bool(toggled.get("reload")),
                    }
                    if toggled.get("done"):
                        payload["done"] = toggled["done"]
                    self._json(200, payload)
                    return
                if wants_case_latest(q.lower()):
                    local = answer_case_latest(q, data)
                    if local:
                        self._json(
                            200,
                            {
                                "answer": local.get("answer") or "",
                                "model": chosen,
                                "ms": max(0, int((time.perf_counter() - started) * 1000)),
                                "mode": local.get("mode") or "case",
                            },
                        )
                        return
                ql = q.lower()
                if wants_now(ql) or wants_meetings(ql) or wants_clock(ql):
                    local = answer_briefing(q, data)
                    self._json(
                        200,
                        {
                            "answer": local.get("answer") or "",
                            "model": chosen,
                            "ms": max(0, int((time.perf_counter() - started) * 1000)),
                            "mode": local.get("mode") or "page",
                        },
                    )
                    return
                answer = gateway_complete(q, data, token, chosen, history)
            except FileNotFoundError:
                self._json(
                    200,
                    {
                        "answer": "No planner page for today. Click Run Planner once.",
                        "model": chosen,
                        "ms": max(0, int((time.perf_counter() - started) * 1000)),
                        "mode": "unpublished",
                    },
                )
                return
            except Exception as exc:
                msg = str(exc) or "ask failed"
                if "sk-" in msg or "ANTHROPIC_API_KEY" in msg:
                    msg = "Planner Buddy failed. Check the Express gateway token."
                self._json(502, {"error": msg})
                return
            self._json(
                200,
                {
                    "answer": answer,
                    "model": chosen,
                    "ms": max(0, int((time.perf_counter() - started) * 1000)),
                    "mode": "gateway",
                },
            )
            return
        if path not in ("/calendar/update", "/calendar/rsvp", "/calendar/delete"):
            self._json(404, {"error": "not found"})
            return
        event_id = body.get("eventId") or body.get("event_id")
        if not event_id:
            self._json(400, {"error": "eventId is required"})
            return
        tz = str(body.get("timezone") or "").strip()
        try:
            if path == "/calendar/update":
                start = body.get("start") or body.get("start_time")
                end = body.get("end") or body.get("end_time")
                if not start or not end:
                    self._json(400, {"error": "start and end are required"})
                    return
                text = mcp_call(
                    "manage_event",
                    {
                        "action": "update",
                        "event_id": event_id,
                        "start_time": start,
                        "end_time": end,
                        "timezone": tz,
                        "send_updates": "all",
                    },
                )
                self._json(200, {"ok": True, "result": text})
                return
            if path == "/calendar/rsvp":
                response = body.get("response")
                if response not in ("accepted", "declined", "tentative"):
                    self._json(400, {"error": "response must be Yes/No/Maybe (accepted/declined/tentative)"})
                    return
                text = mcp_call(
                    "manage_event",
                    {
                        "action": "rsvp",
                        "event_id": event_id,
                        "response": response,
                        "send_updates": "all",
                    },
                )
                self._json(200, {"ok": True, "result": text})
                return
            if path == "/calendar/delete":
                text = mcp_call(
                    "manage_event",
                    {
                        "action": "delete",
                        "event_id": event_id,
                        "send_updates": "none",
                    },
                )
                self._json(200, {"ok": True, "result": text})
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            msg = str(exc)
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    msg = exc.read().decode()[:400]
                except Exception:
                    msg = str(exc)
            self._json(502, {"error": msg or "calendar update failed"})

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def note_bridge_traffic(path: str) -> None:
    """Page/panel traffic keeps 8765 up. Extension /health pings do not."""
    p = str(path or "").split("?")[0].rstrip("/") or "/"
    if p in BRIDGE_HEARTBEAT_PATHS:
        return
    _BRIDGE_LIFE["page"] = time.time()


def note_page_bye() -> None:
    _BRIDGE_LIFE["bye"] = time.time()


def idle_watch(httpd) -> None:
    """Stop the detached listener when the opened page is gone.

    /plan/status is a heartbeat, so a poll still in flight after close does not
    cancel /__bye. A real page load after that still does.
    """
    while not _idle_stop.wait(1):
        if PLAN_ACTIVE.is_set():
            continue
        now = time.time()
        bye = float(_BRIDGE_LIFE.get("bye") or 0)
        page = float(_BRIDGE_LIFE.get("page") or 0)
        if bye and now - bye >= 2:
            if page > bye:
                _BRIDGE_LIFE["bye"] = 0.0
                continue
            try:
                httpd.shutdown()
            except Exception:
                pass
            return
        if now - page < BRIDGE_IDLE_SEC:
            continue
        try:
            httpd.shutdown()
        except Exception:
            pass
        return


def bind_server(preferred: int) -> tuple[ThreadingHTTPServer, int]:
    last_error = None
    for port in range(preferred, PORT_START + PORT_SPAN):
        if port != preferred and port_in_use(port):
            continue
        try:
            httpd = ThreadingHTTPServer((HOST, port), Handler)
            return httpd, port
        except OSError as exc:
            last_error = exc
            continue
    raise SystemExit(f"engineer-day-planner: could not bind 127.0.0.1 from {preferred}: {last_error}")


def xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def find_our_port() -> int | None:
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        if is_our_listener(port):
            return port
    return None


def find_bound_bridge_port() -> int | None:
    """LISTEN port for calendar-bridge.py even when /health is slow or busy."""
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        for pid in pids_listening(port):
            if "calendar-bridge.py" in process_args(pid):
                return port
    return None


def find_planner_port() -> int | None:
    for port in range(PORT_START, PORT_START + PORT_SPAN):
        if is_planner_service(port):
            return port
    return None


def wait_for_our_port(seconds: float) -> int | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        port = find_our_port()
        if port:
            return port
        time.sleep(0.15)
    return None


def native_host_script() -> pathlib.Path:
    return SKILL_ROOT / "scripts" / "edp-native-host.py"


def launch_agent_path() -> pathlib.Path:
    return HOME / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def native_host_dirs() -> list[pathlib.Path]:
    if sys.platform == "darwin":
        support = HOME / "Library" / "Application Support"
        return [
            support / "Google" / "Chrome" / "NativeMessagingHosts",
            support / "Google" / "Chrome Beta" / "NativeMessagingHosts",
            support / "Google" / "Chrome Dev" / "NativeMessagingHosts",
            support / "Google" / "Chrome Canary" / "NativeMessagingHosts",
            support / "Chromium" / "NativeMessagingHosts",
            support / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts",
            support / "Microsoft Edge" / "NativeMessagingHosts",
            support / "Microsoft Edge Beta" / "NativeMessagingHosts",
            support / "Vivaldi" / "NativeMessagingHosts",
        ]
    if os.name == "nt":
        local = pathlib.Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local")
        return [
            local / "Google" / "Chrome" / "User Data" / "NativeMessagingHosts",
            local / "Google" / "Chrome Beta" / "User Data" / "NativeMessagingHosts",
            local / "Google" / "Chrome Dev" / "User Data" / "NativeMessagingHosts",
            local / "Google" / "Chrome SxS" / "User Data" / "NativeMessagingHosts",
            local / "Chromium" / "User Data" / "NativeMessagingHosts",
            local / "Microsoft" / "Edge" / "User Data" / "NativeMessagingHosts",
            local / "BraveSoftware" / "Brave-Browser" / "User Data" / "NativeMessagingHosts",
            local / "Vivaldi" / "User Data" / "NativeMessagingHosts",
        ]
    if sys.platform.startswith("linux"):
        cfg = xdg_config()
        return [
            cfg / "google-chrome" / "NativeMessagingHosts",
            cfg / "google-chrome-beta" / "NativeMessagingHosts",
            cfg / "google-chrome-unstable" / "NativeMessagingHosts",
            cfg / "chromium" / "NativeMessagingHosts",
            cfg / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts",
            cfg / "microsoft-edge" / "NativeMessagingHosts",
        ]
    return []


def write_windows_native_host_registry(manifest_path: pathlib.Path) -> None:
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return
    hives = (
        "Software\\Google\\Chrome\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Google\\Chrome Beta\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Google\\Chrome Dev\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Google\\Chrome SxS\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Chromium\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Microsoft\\Edge\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\BraveSoftware\\Brave-Browser\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
        "Software\\Vivaldi\\NativeMessagingHosts\\" + NATIVE_HOST_NAME,
    )
    value = str(manifest_path)
    for hive in hives:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, hive)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)
            winreg.CloseKey(key)
        except OSError:
            continue


def windows_host_command() -> pathlib.Path | None:
    """Chrome on Windows launches an executable, not a .py file."""
    if os.name != "nt":
        return None
    host = native_host_script()
    if not host.is_file():
        return None
    py = pathlib.Path(sys.executable)
    if py.name.lower() in {"pythonw.exe", "pythonw"}:
        sibling = py.with_name("python.exe")
        if sibling.is_file():
            py = sibling
    cmd = host.with_suffix(".cmd")
    body = (
        "@echo off\r\n"
        f"\"{py}\" -u \"{host}\"\r\n"
    )
    try:
        cmd.write_text(body, encoding="utf-8")
    except OSError:
        return None
    return cmd


def in_protected_home(path: pathlib.Path) -> bool:
    """Desktop, Documents, and Downloads are blocked for Chrome's native host."""
    try:
        rel = path.resolve().relative_to(HOME.resolve())
    except (OSError, ValueError):
        return False
    return bool(rel.parts) and rel.parts[0] in {"Desktop", "Documents", "Downloads"}


def mac_host_command() -> pathlib.Path | None:
    """Install a Python host Chrome can execute. A .command file opens Terminal and the pipe dies."""
    if sys.platform != "darwin":
        return None
    host = native_host_script()
    src = host.with_name("edp-native-host-main.py")
    if not src.is_file():
        src = host
    if not src.is_file():
        return None
    py = str(pathlib.Path(sys.executable))
    dest_dir = HOME / "Library" / "Application Support" / "engineer-day-planner"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    text = src.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = "#!" + py + "\n" + text.split("\n", 1)[1]
    else:
        text = "#!" + py + "\n" + text
    dest = dest_dir / "edp-native-host"

    def logic_version(body: str) -> int:
        for line in body.splitlines():
            if line.startswith("HOST_LOGIC_VERSION"):
                digits = "".join(ch for ch in line if ch.isdigit())
                return int(digits or "0")
        return 0

    if dest.is_file():
        try:
            have = logic_version(dest.read_text(encoding="utf-8"))
        except OSError:
            have = 0
        if have > logic_version(text):
            return dest
        subprocess.run(["chflags", "nouchg", str(dest)], capture_output=True, text=True)
    tmp = dest_dir / ".edp-native-host.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o755)
        tmp.replace(dest)
        subprocess.run(["chflags", "uchg", str(dest)], capture_output=True, text=True)
    except OSError:
        return dest if dest.is_file() else None
    return dest


def write_native_host_manifests() -> None:
    host = native_host_script()
    if not host.is_file():
        return
    try:
        host.chmod(host.stat().st_mode | 0o111)
    except OSError:
        pass
    launch = windows_host_command() or mac_host_command()
    if launch is None and host.is_file() and not in_protected_home(host):
        launch = host
    if launch is None:
        return
    payload = {
        "name": NATIVE_HOST_NAME,
        "description": "Start the engineer day planner local bridge",
        "path": str(launch),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{CHROME_EXTENSION_ID}/"],
    }
    raw = json.dumps(payload, indent=2)
    manifest_path = None
    for folder in native_host_dirs():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{NATIVE_HOST_NAME}.json"
            path.write_text(raw + "\n", encoding="utf-8")
            if manifest_path is None:
                manifest_path = path
        except OSError:
            continue
    if manifest_path is not None:
        write_windows_native_host_registry(manifest_path)


def write_launch_agent_plist() -> None:
    if sys.platform != "darwin":
        return
    page = PAGE if PAGE else (SKILL_ROOT / "out" / "current.html")
    log = SKILL_ROOT / "out" / ".bridge.log"
    port_file = PORT_FILE
    (SKILL_ROOT / "out").mkdir(parents=True, exist_ok=True)
    py = sys.executable
    bridge = SKILL_ROOT / "scripts" / "calendar-bridge.py"
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        "<plist version=\"1.0\">\n<dict>\n"
        "<key>Label</key><string>" + xml_escape(LAUNCH_LABEL) + "</string>\n"
        "<key>ProgramArguments</key>\n<array>\n"
        "<string>" + xml_escape(py) + "</string>\n"
        "<string>" + xml_escape(str(bridge)) + "</string>\n"
        "</array>\n"
        "<key>RunAtLoad</key><false/>\n"
        "<key>KeepAlive</key><false/>\n"
        "<key>WorkingDirectory</key><string>" + xml_escape(str(SKILL_ROOT)) + "</string>\n"
        "<key>EnvironmentVariables</key>\n<dict>\n"
        "<key>DAY_PLANNER_PAGE</key><string>" + xml_escape(str(page)) + "</string>\n"
        "<key>DAY_PLANNER_PORT</key><string>" + xml_escape(str(PORT_START)) + "</string>\n"
        "<key>DAY_PLANNER_PORT_FILE</key><string>" + xml_escape(str(port_file)) + "</string>\n"
        "<key>DAY_PLANNER_SKILL</key><string>" + xml_escape(str(SKILL_ROOT)) + "</string>\n"
        "<key>ENGINEER_DAY_PLANNER_ROOT</key><string>" + xml_escape(str(SKILL_ROOT)) + "</string>\n"
        "<key>PYTHONUNBUFFERED</key><string>1</string>\n"
        "</dict>\n"
        "<key>StandardOutPath</key><string>" + xml_escape(str(log)) + "</string>\n"
        "<key>StandardErrorPath</key><string>" + xml_escape(str(log)) + "</string>\n"
        "</dict>\n</plist>\n"
    )
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")


def write_autostart_files() -> None:
    write_launch_agent_plist()
    write_native_host_manifests()


def launchctl_label() -> str:
    return f"gui/{os.getuid()}/{LAUNCH_LABEL}"


def launchctl_is_loaded() -> bool:
    if sys.platform != "darwin":
        return False
    probe = subprocess.run(
        ["launchctl", "print", launchctl_label()],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def launchctl_bootout() -> None:
    """Unload our job and the old com.kgarai job so launchd does not respawn it."""
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
        plist = HOME / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.is_file():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(plist)],
                capture_output=True,
                text=True,
            )
        if label in LEGACY_LAUNCH_LABELS:
            try:
                plist.unlink()
            except OSError:
                pass


def launchctl_load_quiet() -> None:
    if sys.platform != "darwin":
        return
    if launchctl_is_loaded():
        return
    plist = launch_agent_path()
    if not plist.is_file():
        return
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
        capture_output=True,
        text=True,
    )


def launchctl_kickstart() -> None:
    if sys.platform != "darwin":
        return
    if not launchctl_is_loaded():
        return
    subprocess.run(
        ["launchctl", "kickstart", "-k", launchctl_label()],
        capture_output=True,
        text=True,
    )


def stop_bridge() -> None:
    """Unload launchd so the port stays free, then stop our HTTP listener only."""
    launchctl_bootout()
    stop_our_processes()


def start_detached() -> None:
    if find_our_port():
        return
    env = os.environ.copy()
    env["DAY_PLANNER_PAGE"] = str(PAGE)
    env["DAY_PLANNER_PORT"] = str(PORT_START)
    env["DAY_PLANNER_PORT_FILE"] = str(PORT_FILE)
    env["DAY_PLANNER_SKILL"] = str(SKILL_ROOT)
    env["ENGINEER_DAY_PLANNER_ROOT"] = str(SKILL_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    log = SKILL_ROOT / "out" / ".bridge.log"
    try:
        (SKILL_ROOT / "out").mkdir(parents=True, exist_ok=True)
        log_handle = open(log, "a", encoding="utf-8")
    except OSError:
        log_handle = open(os.devnull, "a", encoding="utf-8")
    cmd = [sys.executable, str(SKILL_ROOT / "scripts" / "calendar-bridge.py")]
    kwargs = {
        "cwd": str(SKILL_ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def launchctl_replace() -> None:
    if sys.platform != "darwin":
        return
    plist = launch_agent_path()
    if not plist.is_file():
        return
    if launchctl_is_loaded():
        subprocess.run(
            ["launchctl", "bootout", launchctl_label()],
            capture_output=True,
            text=True,
        )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
        capture_output=True,
        text=True,
    )


def finish_ensure(port: int) -> int:
    write_native_host_manifests()
    try:
        write_launch_agent_plist()
    except Exception:
        pass
    return port


def shutdown_port(port: int) -> None:
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{port}/__shutdown",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:
        pass


def _shutdown_and_unload(server) -> None:
    launchctl_bootout()
    try:
        server.shutdown()
    except Exception:
        pass


def write_adopted_skill(version: str, files: list) -> pathlib.Path:
    """Store the loaded extension where the host can run it, whatever folder it came from."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (version or "0")) or "0"
    root = HOME / "Library" / "Application Support" / "engineer-day-planner" / "runs"
    staging = root / f".{safe}-loaded.staging"
    bundle = root / f"{safe}-loaded"
    if staging.exists():
        shutil.rmtree(staging)
    allowed_exact = {
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
    wrote = 0
    for item in files:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        if rel.startswith("extension/"):
            rel = rel[len("extension/") :]
        parts = pathlib.PurePosixPath(rel).parts
        if not rel or ".." in parts:
            continue
        ok = rel in allowed_exact or rel.startswith("skill/scripts/") or rel.startswith("skill/page/")
        if not ok:
            continue
        dest = staging.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(item.get("text") or ""), encoding="utf-8")
        if dest.suffix == ".sh":
            dest.chmod(0o755)
        wrote += 1
    bridge = staging / "skill" / "scripts" / "calendar-bridge.py"
    manifest = staging / "manifest.json"
    if not bridge.is_file() or not manifest.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("this version did not include the bridge")
    (staging / "skill" / "out").mkdir(parents=True, exist_ok=True)
    for script in (staging / "skill" / "scripts").glob("*.sh"):
        try:
            script.chmod(0o755)
        except OSError:
            pass
    if bundle.exists():
        shutil.rmtree(bundle)
    staging.rename(bundle)
    skill = bundle / "skill"
    note_dir = HOME / "Library" / "Application Support" / "engineer-day-planner"
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / "run-skill.txt").write_text(str(skill.resolve()) + "\n", encoding="utf-8")
    return skill


def _launch_adopted_and_stop(server, skill: pathlib.Path, version: str) -> None:
    time.sleep(0.3)
    env = os.environ.copy()
    env["DAY_PLANNER_SKILL"] = str(skill)
    env["ENGINEER_DAY_PLANNER_ROOT"] = str(skill)
    env["DAY_PLANNER_PAGE"] = str(skill / "out" / "current.html")
    env["DAY_PLANNER_PORT"] = str(PORT_START)
    env["DAY_PLANNER_PORT_FILE"] = str(skill / "out" / ".bridge-port")
    env["DAY_PLANNER_EXTENSION_VERSION"] = version
    subprocess.Popen(
        [sys.executable, str(skill / "scripts" / "calendar-bridge.py"), "--ensure"],
        cwd=str(skill),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _shutdown_and_unload(server)


def ensure_bridge() -> int:
    """Start this extension's bridge. An older planner server is shut down first."""
    wanted = packed_extension_version()
    ours = os.path.normpath(str(SKILL_ROOT))
    stop_other_planner_listeners(ours)
    port = find_our_port()
    if port:
        health = health_payload(port) or {}
        got = str(health.get("version") or "")
        if not wanted or got == wanted:
            return finish_ensure(port)
        shutdown_port(port)
        time.sleep(0.2)
        start_detached()
        port = wait_for_our_port(8)
        if not port:
            raise SystemExit("engineer-day-planner: could not start the local bridge")
        stop_other_planner_listeners(ours)
        return finish_ensure(port)
    start_detached()
    port = wait_for_our_port(8)
    if not port:
        raise SystemExit(
            "engineer-day-planner: could not start the local bridge; the previous one is still running"
        )
    stop_other_planner_listeners(ours)
    return finish_ensure(port)


def main():
    global PORT, PROCESS_VERSION
    if "--repair-plan" in sys.argv:
        idx = sys.argv.index("--repair-plan")
        path = pathlib.Path(
            sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "/tmp/plan.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        repair_plan_payload(data)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        started = time.time()
        ai_path = pathlib.Path("/tmp/plan-ai.json")
        if ai_path.is_file():
            started = min(started, ai_path.stat().st_mtime)
        ok = salvage_publish_plan(started)
        print("repaired" if ok else "repair-failed", path)
        return
    if "--choose-port" in sys.argv:
        stop_bridge()
        print(choose_port())
        return
    if "--current-port" in sys.argv:
        if PORT_FILE.is_file():
            print(PORT_FILE.read_text(encoding="utf-8").strip() or str(PORT_START))
        else:
            print(PORT_START)
        return
    if "--detect-runner" in sys.argv:
        print(detect_runner())
        return
    if "--stop" in sys.argv:
        stop_bridge()
        return
    if "--ensure" in sys.argv or "--install-autostart" in sys.argv:
        apply_extension_version()
        port = ensure_bridge()
        print(f"http://{HOST}:{port}")
        return
    if "--restart" in sys.argv:
        apply_extension_version()
        stop_bridge()
        port = ensure_bridge()
        print(f"http://{HOST}:{port}")
        return
    preferred = int(os.environ.get("DAY_PLANNER_PORT", str(PORT_START)))
    httpd, bound = bind_server(preferred)
    PORT = bound
    PROCESS_VERSION = packed_extension_version()
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(bound), encoding="utf-8")
    try:
        write_autostart_files()
    except Exception:
        pass
    _idle_stop.clear()
    threading.Thread(target=idle_watch, args=(httpd,), daemon=True).start()
    with PLAN_LOCK:
        current = read_plan_state()
        if current.get("state") == "running" and current.get("pid") != os.getpid():
            _write_plan_state_unlocked(
                state="error",
                error="Planner run interrupted. Start it again.",
                finishedAt=now_stamp(),
                headline="Interrupted",
                pid=0,
            )
    try:
        httpd.serve_forever()
    finally:
        _idle_stop.set()
        try:
            if PORT_FILE.is_file() and PORT_FILE.read_text(encoding="utf-8").strip() == str(bound):
                PORT_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
