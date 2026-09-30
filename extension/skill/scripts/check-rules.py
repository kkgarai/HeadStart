#!/usr/bin/env python3
"""Run this after changes, before a version ships.

    python3 extension/skill/scripts/check-rules.py

Green means the locked rules still hold: Omni, logout, publish, Slack names,
lookback, Take New Cases, and the version strings match. It does not prove
every future case. A red line is a rule that already shipped and just broke.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Gate:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.passed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print("ok  ", name)
            return
        line = name if not detail else name + " — " + detail
        self.failed.append(line)
        print("FAIL", line)


def main() -> int:
    gate = Gate()
    bridge = load("edp_bridge", "calendar-bridge.py")
    sanit = load("edp_san", "sanitize-briefing.py")
    plan = load("edp_plan", "self-check-plan.py")

    version_strings(gate)
    toolbar_rules(gate)
    python_parses(gate)
    host_versions_match(gate)
    omni_rules(gate, bridge)
    logout_rules(gate, bridge)
    publish_rules(gate, sanit)
    slack_name_rules(gate, sanit)
    lookback_rules(gate, bridge)
    today_plan_rules(gate, plan)
    peek_rules(gate, plan)

    print("")
    if gate.failed:
        print(f"{len(gate.failed)} rule(s) broke. {gate.passed} held.")
        return 1
    print(f"{gate.passed} rules held.")
    return 0


def toolbar_rules(gate: Gate) -> None:
    """Help and Feedback belong on the extension bar, not above the calendar."""
    page = (ROOT / "extension" / "skill" / "page" / "template.html").read_text(encoding="utf-8")
    start = page.find('<div class="toolbar">')
    end = page.find('<div id="app">')
    toolbar = page[start:end] if start >= 0 and end > start else ""
    gate.check("planner toolbar exists", bool(toolbar))
    gate.check(
        "Feedback stays off the planner toolbar",
        "feedback-link" not in toolbar and ">Feedback<" not in toolbar,
    )
    gate.check(
        "Help stays off the planner toolbar",
        'id="help-btn"' not in toolbar and ">Help<" not in toolbar,
    )
    gate.check(
        "Refresh Calendar stays on the planner toolbar",
        'id="refresh-cal"' in toolbar,
    )
    panel = (ROOT / "extension" / "panel.html").read_text(encoding="utf-8")
    gate.check("Feedback stays on the extension bar", 'id="feedback-btn"' in panel)
    gate.check("Help stays on the extension bar", 'id="help-btn"' in panel)


def version_strings(gate: Gate) -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    name = str(manifest.get("version_name") or "")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    gate.check("version is set", bool(version))
    gate.check("version name matches", name == version + " beta", name)
    gate.check(
        "README matches the manifest",
        f"Version **{version} beta**." in readme,
        version,
    )


def python_parses(gate: Gate) -> None:
    broken = []
    for path in sorted(HERE.glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            broken.append(f"{path.name}:{exc.lineno}")
    gate.check("Python files parse", not broken, ", ".join(broken))


def host_versions_match(gate: Gate) -> None:
    versions = []
    for name in ("edp-native-host.py", "edp-native-host-main.py"):
        text = (HERE / name).read_text(encoding="utf-8")
        found = ""
        for line in text.splitlines():
            if line.startswith("HOST_LOGIC_VERSION"):
                found = "".join(ch for ch in line if ch.isdigit())
        versions.append(found)
    gate.check(
        "native host copies match",
        len(versions) == 2 and versions[0] and versions[0] == versions[1],
        " ".join(versions),
    )


def omni_rules(gate: Gate, bridge) -> None:
    gate.check(
        "Messaging counts as Chat",
        bridge.omni_in_adherence("Available - Messaging", ["chat"]),
    )
    gate.check(
        "Chat counts as Messaging",
        bridge.omni_in_adherence("Available - Chat", ["messaging"]),
    )
    gate.check(
        "Casework does not count as Chat",
        not bridge.omni_in_adherence("Available - Casework", ["chat"]),
    )
    gate.check(
        "Screen Sharing stays in adherence",
        bridge.omni_in_adherence("Screen Sharing", ["chat"]),
    )
    gate.check(
        "Busy is out of adherence on Chat",
        not bridge.omni_in_adherence("Busy", ["chat"]),
    )
    js = (HERE.parents[1] / "background.js").read_text(encoding="utf-8")
    chat_line = next(line for line in js.splitlines() if line.strip().startswith("chat:"))
    gate.check(
        "extension Omni tokens match the bridge",
        "messaging" in chat_line and "message" in chat_line and '"chat"' in chat_line,
    )


def logout_rules(gate: Gate, bridge) -> None:
    follow = [
        {"done": True, "status": "Solution Provided"},
        {"status": "Solution Provided", "detail": "Sev1 · Solution Provided"},
        {"status": "Need More Information", "detail": "waiting"},
        {"status": "Working", "detail": "still ours"},
    ]
    gate.check("logout follow-ups skip customer-wait", bridge._logout_follow_count(follow) == 1)
    mail = [
        {"label": "mentioned you in a post", "detail": "orgcs-chatter-notifications@salesforce.com"},
        {"label": "Customer question", "detail": "marine@example.com"},
    ]
    gate.check("logout mail skips chatter notices", bridge._logout_mail_count(mail) == 1)
    slack = [
        {"detail": "Dhairya says you can close the swarm."},
        {"detail": "You pinged Pratik and have no answer yet."},
        {"detail": "Ashwin asked where you went."},
    ]
    gate.check("logout Slack skips threads waiting on them", bridge._logout_slack_count(slack) == 1)
    gus = [
        {"id": "gusmail-95893490", "label": "LAP 95893490", "detail": "limit"},
        {"id": "lap-95893490", "label": "LAP 95893490 — Closed - Executed", "detail": "Closed - Executed"},
        {"id": "lap-mail-95893490", "label": "LAP 95893490", "detail": "mention"},
        {"id": "gusmail-95213540", "label": "LAP 95213540", "detail": "open"},
        {"id": "gusmail-sai", "label": "Sai mentioned you in a comment", "detail": "LAP-BlackTab-Bot mentioned you."},
    ]
    gate.check("logout GUS counts one open LAP", bridge._logout_gus_count(gus) == 1)


def publish_rules(gate: Gate, sanit) -> None:
    sanit._load_planner_gather = lambda: {
        "orgcsFetchOk": True,
        "cases": [{"caseNumber": "474712902"}],
    }
    live = sanit.live_owned_case_numbers(owned={"11111111"})
    gate.check(
        "gather case survives a shorter case query",
        "474712902" in live and "11111111" in live,
    )


def slack_name_rules(gate: Gate, sanit) -> None:
    raw = "#ZC:C0C24BX6YNN:68080741-Credent-Partners-Sàrl"
    cleaned = sanit.slack_channel_title(raw)
    gate.check(
        "Slack card drops the ZC prefix",
        cleaned == "68080741-Credent-Partners-Sàrl",
        cleaned,
    )


def lookback_rules(gate: Gate, bridge) -> None:
    from datetime import datetime

    tz = bridge._shift_tz("America/Los_Angeles")
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = bridge.inbox_cutoff("America/Los_Angeles")
    days = bridge.inbox_lookback_days("America/Los_Angeles")
    gate.check("Monday looks back 4 days, other days 2", days == (4 if now.weekday() == 0 else 2))
    gate.check("lookback starts before today", cutoff == start - bridge.timedelta(hours=days * 24))
    after = bridge.inbox_search_after("America/Los_Angeles")
    gate.check(
        "Slack search date is the day before the cutoff",
        after == (cutoff.date() - bridge.timedelta(days=1)).strftime("%Y-%m-%d"),
        after,
    )


def today_plan_rules(gate: Gate, plan) -> None:
    chat = _today_fails(plan, "Chat 9:00 AM–5:00 PM", [{"id": "plan-new-cases", "label": "Take New Cases", "kind": "plan"}])
    gate.check(
        "Take New Cases is refused on a Chat day",
        any("Take New Cases" in line for line in chat),
    )
    casework = _today_fails(
        plan,
        "Casework 9:00 AM–5:00 PM",
        [
            {"id": "plan-new-cases", "label": "Take New Cases", "kind": "plan"},
            {"id": "plan-break-1", "label": "Short Break", "kind": "break"},
        ],
    )
    gate.check(
        "Take New Cases stays on a Casework day",
        not any("Take New Cases" in line for line in casework),
        "; ".join(casework),
    )


def peek_rules(gate: Gate, plan) -> None:
    gate.check("a stub Peek is rejected", plan.is_stub_peek("OrgCS status is Working."))
    gate.check(
        "a written Peek is kept",
        not plan.is_stub_peek("Raj fixed the promotion himself and asked about the GH006 error."),
    )
    fails = _peek_fails(plan, "474712902", {})
    gate.check(
        "a gather case with no summary fails publish",
        any("474712902" in line and "missing summary" in line for line in fails),
        "; ".join(fails),
    )
    held = _peek_fails(
        plan,
        "474712902",
        {"474712902": {"summary": "Raj fixed the promotion himself and asked about the GH006 error."}},
    )
    gate.check(
        "a gather case with a summary passes",
        not any("474712902" in line and "missing summary" in line for line in held),
        "; ".join(held),
    )


def _today_fails(plan, schedule: str, rows: list) -> list[str]:
    gather = {
        "assembledFromCalendar": True,
        "assembledSchedule": schedule,
        "freeMinutes": 240,
        "daypart": "mid",
        "cases": [],
    }
    data = {"aiAnalyzed": True, "sourcesAnalyzed": True, "todayPlan": rows, "sections": []}
    fails: list[str] = []
    plan._load_sanitize = lambda: type("S", (), {"live_owned_case_numbers": staticmethod(lambda owned=None: set())})()
    plan._check_model_rules(data, gather, fails)
    return fails


def _peek_fails(plan, number: str, peeks: dict) -> list[str]:
    gather = {
        "assembledFromCalendar": False,
        "cases": [{"caseNumber": number}],
        "orgcsFetchOk": True,
    }
    data = {
        "aiAnalyzed": True,
        "sourcesAnalyzed": True,
        "inboxReviewed": True,
        "gusReviewed": True,
        "todayPlan": [],
        "peeks": peeks,
        "sections": [],
    }
    fails: list[str] = []
    plan._load_sanitize = lambda: type(
        "S", (), {"live_owned_case_numbers": staticmethod(lambda owned=None: {number})}
    )()
    real_read = plan.pathlib.Path.read_text

    def _read(self, *args, **kwargs):
        if getattr(self, "name", "") == "plan-ai.json":
            raise OSError("fixture")
        return real_read(self, *args, **kwargs)

    plan.pathlib.Path.read_text = _read
    try:
        plan._check_model_rules(data, gather, fails)
    finally:
        plan.pathlib.Path.read_text = real_read
    return fails


if __name__ == "__main__":
    sys.exit(main())
