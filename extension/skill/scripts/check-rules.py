#!/usr/bin/env python3
"""Lock the rules that already failed, before the next version ships.

Run from the skill scripts folder:

    python3 check-rules.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fail(msg: str) -> None:
    print("FAIL", msg)
    raise SystemExit(1)


def main() -> None:
    bridge = load("edp_bridge", "calendar-bridge.py")
    sanit = load("edp_san", "sanitize-briefing.py")

    if not bridge.omni_in_adherence("Available - Messaging", ["chat"]):
        fail("Available - Messaging must count as Chat")
    if not bridge.omni_in_adherence("Available - Chat", ["messaging"]):
        fail("Available - Chat must count as Messaging")
    if bridge.omni_in_adherence("Available - Casework", ["chat"]):
        fail("Casework must not count as Chat")
    if not bridge.omni_in_adherence("Screen Sharing", ["chat"]):
        fail("Screen Sharing stays in adherence")

    js = (HERE.parents[1] / "background.js").read_text(encoding="utf-8")
    chat_line = next(line for line in js.splitlines() if line.strip().startswith("chat:"))
    if "messaging" not in chat_line or "message" not in chat_line:
        fail("extension Omni chat tokens must include messaging")

    follow = [
        {"done": True, "status": "Solution Provided", "detail": "done"},
        {"status": "Solution Provided", "detail": "Sev1 · Solution Provided"},
        {"status": "Need More Information", "detail": "waiting"},
        {"status": "Working", "detail": "still ours"},
    ]
    if bridge._logout_follow_count(follow) != 1:
        fail("follow-ups waiting on the customer are not still open")

    mail = [
        {"label": "Amit mentioned you in a post", "detail": "orgcs-chatter-notifications@salesforce.com"},
        {"label": "Customer question", "detail": "marine@example.com"},
    ]
    if bridge._logout_mail_count(mail) != 1:
        fail("chatter notices are not email still open")

    slack = [
        {"detail": "Dhairya says you can close the swarm."},
        {"detail": "You pinged Pratik and have no answer yet."},
        {"detail": "Ashwin asked where you went."},
    ]
    if bridge._logout_slack_count(slack) != 1:
        fail("a thread waiting on them is not still open")

    gus = [
        {"id": "gusmail-95893490", "label": "LAP 95893490", "detail": "limit"},
        {"id": "lap-95893490", "label": "LAP 95893490 — Closed - Executed", "detail": "Closed - Executed"},
        {"id": "lap-mail-95893490", "label": "LAP 95893490", "detail": "mention"},
        {"id": "gusmail-95213540", "label": "LAP 95213540", "detail": "open"},
        {"id": "gusmail-sai", "label": "Sai mentioned you in a comment", "detail": "LAP-BlackTab-Bot mentioned you."},
    ]
    if bridge._logout_gus_count(gus) != 1:
        fail("a closed LAP must count once, as closed")

    sanit._load_planner_gather = lambda: {
        "orgcsFetchOk": True,
        "cases": [{"caseNumber": "474712902"}],
    }
    live = sanit.live_owned_case_numbers(owned={"11111111"})
    if "474712902" not in live or "11111111" not in live:
        fail("this run's gather case must survive a shorter case query")

    print("ok")


if __name__ == "__main__":
    main()
