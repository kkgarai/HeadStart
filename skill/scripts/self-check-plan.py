#!/usr/bin/env python3
"""Fail-fast checks the Run Planner model must pass before publish-page.sh.

Python fetch is plumbing. This script refuses a page that is still search hits
or Peek stubs. The model must write Peek and classify Slack/Mail/GUS, then stamp
aiAnalyzed + inboxReviewed + gusReviewed + sourcesAnalyzed.

publish-page.sh also --fix (compact when, string chronology, string rows).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
GATHER = pathlib.Path("/tmp/planner-gather.json")
COMPACT = re.compile(r"^\d{8}T\d{6}$")
# Stub field labels only. Real Peek often says "Kiran's last outbound (Mon 10:17 AM) …"
# without a colon after the parens; that must pass.
STUB_PEEK = re.compile(
    r"last wrote:|"
    r"Last outbound \([^)]+\):|"
    r"OrgCS status is ",
    re.I,
)


def is_stub_peek(summary: str) -> bool:
    return bool(STUB_PEEK.search(str(summary or "")))


def _load_sanitize():
    spec = importlib.util.spec_from_file_location("sanitize", HERE / "sanitize-briefing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _section_key(title: object) -> str:
    t = re.sub(r"^[^\w#]+", "", str(title or ""))
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()


def _rows(sec: dict) -> list:
    out = []
    for it in sec.get("items") or []:
        out.append(it)
    for g in sec.get("groups") or []:
        if isinstance(g, dict):
            out.extend(g.get("items") or [])
    return out


def _peek_summary(it: dict) -> str:
    peek = it.get("peek") if isinstance(it.get("peek"), dict) else {}
    return str(peek.get("summary") or it.get("summary") or "").strip()


def _item_keys(rows: list) -> list[str]:
    keys = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        key = str(
            it.get("id")
            or it.get("slackUrl")
            or it.get("mailUrl")
            or it.get("channelId")
            or ""
        ).strip()
        if key:
            keys.append(key)
    return keys


def _load_gather() -> dict:
    try:
        loaded = json.loads(GATHER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _copied_candidates(page_rows: list, candidates: list) -> bool:
    """A raw paste of the search list. Opened rows already sorted into a bucket are not that list."""
    raw = [
        it
        for it in page_rows
        if isinstance(it, dict)
        and not it.get("mailBucket")
        and not it.get("slackBucket")
        and it.get("opened") is not True
    ]
    page = _item_keys(raw)
    cand = _item_keys(candidates)
    if len(cand) < 4 or not page:
        return False
    return len(page) == len(cand) and set(page) == set(cand)


def _plan_role(row: dict) -> str:
    rid = str(row.get("id") or "").lower()
    kind = str(row.get("kind") or "").lower()
    label = str(row.get("label") or "").strip().lower()
    if "plan-new-cases" in rid or "new cases" in label:
        return "new"
    if kind in ("break", "pause") or "plan-break" in rid or "short break" in label:
        return "break"
    if "plan-open" in rid or label in ("open", "free"):
        return "open"
    return "work"


def _closeout_nums(data: dict, key: str) -> list[str]:
    out = []
    raw = data.get(key)
    if not isinstance(raw, list):
        return out
    for val in raw:
        if isinstance(val, dict):
            num = re.sub(r"\D", "", str(val.get("caseNumber") or val.get("id") or ""))
        else:
            num = re.sub(r"\D", "", str(val or ""))
        if len(num) >= 6:
            out.append(num)
    return out


def _summary_on_case_row(data: dict, num: str) -> str:
    want = re.sub(r"\D", "", str(num or ""))
    if len(want) < 6:
        return ""
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        rows = list(sec.get("items") or [])
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                rows.extend(group.get("items") or [])
        for it in rows:
            if not isinstance(it, dict):
                continue
            got = re.sub(r"\D", "", str(it.get("caseNumber") or it.get("id") or ""))
            if want not in got:
                continue
            peek = it.get("peek") if isinstance(it.get("peek"), dict) else {}
            summary = str(it.get("summary") or peek.get("summary") or "").strip()
            if summary:
                return summary
    return ""


def _check_model_rules(data: dict, gather: dict, fails: list[str]) -> None:
    """Block publish when the model skipped a required part of the build."""
    if data.get("aiAnalyzed") is not True:
        return
    peeks = data.get("peeks") if isinstance(data.get("peeks"), dict) else {}
    if not peeks:
        try:
            ai = json.loads(pathlib.Path("/tmp/plan-ai.json").read_text(encoding="utf-8"))
            if isinstance(ai, dict) and isinstance(ai.get("peeks"), dict):
                peeks = ai["peeks"]
        except (OSError, json.JSONDecodeError, TypeError):
            peeks = {}
    missing = []
    for row in gather.get("cases") or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("caseNumber") or row.get("CaseNumber") or "").strip()
        if not num:
            continue
        peek = peeks.get(num) if isinstance(peeks.get(num), dict) else {}
        summary = str(peek.get("summary") or "").strip()
        if not summary:
            summary = _summary_on_case_row(data, num)
        if not summary:
            missing.append(num)
    if missing:
        fails.append(
            "FAIL peek: missing summary for " + ", ".join(missing[:8]) + " — Peek every gather case"
        )
    logoff = _closeout_nums(data, "beforeYouLogOff")
    tomorrow = _closeout_nums(data, "tomorrowFirst")
    both = [n for n in logoff if n in set(tomorrow)]
    if both:
        fails.append(
            "FAIL closeout: same case in beforeYouLogOff and tomorrowFirst: " + ", ".join(both[:6])
        )
    for name, nums in (("beforeYouLogOff", logoff), ("tomorrowFirst", tomorrow)):
        if len(nums) != len(set(nums)):
            fails.append(f"FAIL closeout: duplicate case in {name}")
    schedule = str(gather.get("assembledSchedule") or "").strip().lower()
    assembled = (
        gather.get("assembledFromCalendar") is True
        and bool(schedule)
        and schedule != "nothing scheduled"
    )
    try:
        free = int(gather.get("freeMinutes") or 0)
    except (TypeError, ValueError):
        free = 0
    plan = data.get("todayPlan")
    rows = [r for r in plan if isinstance(r, dict)] if isinstance(plan, list) else []
    eod = str(gather.get("daypart") or "").strip().lower() == "eod"
    if not assembled or free < 60:
        return
    if not rows:
        if eod:
            return
        fails.append("FAIL today: todayPlan is empty while the shift still has open time")
        return
    roles = [_plan_role(r) for r in rows]
    workish = [role for role in roles if role in ("new", "work")]
    if not eod and "new" not in roles:
        fails.append(
            "FAIL today: include Take new cases (plan-new-cases) in the earliest open hole"
        )
    elif not eod and workish and workish[0] != "new":
        fails.append("FAIL today: Take new cases must be the first work block")
    if not eod and free >= 180 and "break" not in roles:
        fails.append("FAIL today: include a 10–15 minute short break (plan-break-1)")
    for cand in gather.get("slackCandidates") or []:
        if not isinstance(cand, dict) or not cand.get("sev1Case"):
            continue
        num = str(cand.get("sev1Case") or "")
        channel = str(cand.get("channel") or "").lstrip("#").strip().lower()
        if len(channel) < 3:
            continue
        peek = peeks.get(num) if isinstance(peeks.get(num), dict) else {}
        blob = str(peek.get("summary") or "").lower()
        if channel not in blob:
            fails.append(
                f"FAIL sev1: #{num} channel was opened and is missing from that case's Peek"
            )


def check(data: dict) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    if not isinstance(data, dict):
        return ["FAIL json: not an object"], []

    if data.get("notThePage") is True or str(data.get("kind") or "") == "planner-evidence":
        return [
            "FAIL gather: this file is search/evidence plumbing, not the page. "
            "Open leftover humans, write Peek, classify inbox, write /tmp/plan.json."
        ], []

    gather = _load_gather()
    slack_cand = gather.get("slackCandidates") or data.get("slackCandidates") or []
    mail_cand = gather.get("mailCandidates") or data.get("mailCandidates") or []
    if not isinstance(slack_cand, list):
        slack_cand = []
    if not isinstance(mail_cand, list):
        mail_cand = []
    slack_ok = gather.get("slackFetchOk")
    if slack_ok is None:
        slack_ok = data.get("slackFetchOk")
    mail_ok = gather.get("mailFetchOk")
    if mail_ok is None:
        mail_ok = data.get("mailFetchOk")
    inbox_ready = slack_ok is not False and mail_ok is not False

    if data.get("aiAnalyzed") is not True:
        fails.append(
            "FAIL ai: set aiAnalyzed true after you write Peek and classify Slack/Mail "
            "(fetching the list is not the work)"
        )
    if inbox_ready and data.get("inboxReviewed") is not True:
        fails.append(
            "FAIL ai: set inboxReviewed true after you classify leftover Slack/Mail from openedClip / lastHumanIsMe / reactions"
        )
    gus_ok = gather.get("gusFetchOk")
    if gus_ok is None:
        gus_ok = data.get("gusFetchOk")
    if gus_ok is True and data.get("gusReviewed") is not True:
        fails.append(
            "FAIL ai: set gusReviewed true after you classify GUS SLA, Support Contact, Follow, and OrgCS related list"
        )
    if data.get("aiAnalyzed") is True and data.get("sourcesAnalyzed") is not True:
        fails.append(
            "FAIL ai: set sourcesAnalyzed true after you analyze OrgCS, GUS, Slack, Calendar, and Mail this run"
        )
    if data.get("aiAnalyzed") is True and "todayPlan" not in data:
        fails.append(
            "FAIL ai: set todayPlan this run (rebuild the remaining shift; empty list is allowed)"
        )

    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            fails.append("FAIL sections: a section is not an object")
            continue
        title = _section_key(sec.get("title"))
        rows = _rows(sec)
        if re.search(r"needs (us|you) now|still watching|no action needed|follow-up due", title, re.I):
            for it in rows:
                if not isinstance(it, dict):
                    fails.append(f"FAIL when: Needs us now row is {type(it).__name__} — use an object")
                    continue
                ident = str(it.get("id") or it.get("caseNumber") or it.get("label") or "?")
                when = str(it.get("when") or "").strip()
                if COMPACT.match(when):
                    fails.append(
                        f"FAIL when: {ident} when={when} — use 12-hour 5:00 PM, never YYYYMMDDTHHMMSS"
                    )
                chrono = it.get("chronology")
                if isinstance(it.get("peek"), dict) and isinstance(it["peek"].get("chronology"), list):
                    chrono = it["peek"]["chronology"]
                if isinstance(chrono, list):
                    for i, ev in enumerate(chrono):
                        if isinstance(ev, str):
                            fails.append(
                                f"FAIL chronology: {ident} chronology[{i}] is str — use "
                                "{when, who, kind, text}"
                            )
                    if not chrono:
                        warns.append(
                            f"WARN peek: {ident} chronology is empty — include last public "
                            "CaseComment (who + clock + hold/ask)"
                        )
                elif it.get("kind") == "case":
                    warns.append(
                        f"WARN peek: {ident} missing chronology[] objects"
                    )
                if it.get("kind") == "case":
                    summary = _peek_summary(it)
                    if not summary:
                        fails.append(
                            f"FAIL peek: {ident} missing summary — you write 4–8 sentences from activity"
                        )
                    elif is_stub_peek(summary):
                        fails.append(
                            f"FAIL peek: {ident} is a fetch stub (last wrote / Last outbound / OrgCS status) "
                            "— rewrite from the thread"
                        )
                    elif summary.rstrip(".") == str(it.get("label") or "").rstrip("."):
                        fails.append(f"FAIL peek: {ident} summary is only the label")
        if re.match(r"customer asked for a (meeting|call)|meetings to (book|schedule)", title, re.I):
            for it in rows:
                if not isinstance(it, dict):
                    fails.append("FAIL meeting: Customer asked for a meeting row is not an object")
                    continue
                ident = str(it.get("id") or it.get("label") or "?")
                num = re.sub(r"\D", "", str(it.get("caseNumber") or ""))
                email = str(it.get("customerEmail") or it.get("contactEmail") or "")
                start = str(it.get("meetingStartStamp") or "")
                end = str(it.get("meetingEndStamp") or "")
                named = bool(COMPACT.match(start) and COMPACT.match(end))
                partial = bool(start or end) and not named
                if len(num) < 6 or partial or (named and "@" not in email):
                    fails.append(
                        f"FAIL meeting: {ident} needs a caseNumber. A named day or time also needs "
                        "customerEmail and both stamps. An ask with no day and no time leaves both stamps off. "
                        "Enablement dates are not meetings."
                    )
        if re.match(r"slack\b", title, re.I):
            dicts = [it for it in rows if isinstance(it, dict)]
            strings = [it for it in rows if isinstance(it, str)]
            if strings:
                fails.append(
                    f"FAIL slack: {len(strings)} string row(s) — wrap as objects with slackUrl"
                )
            if _copied_candidates(dicts, slack_cand):
                fails.append(
                    "FAIL slack: page is the search hit list — open leftover humans and keep only what still needs you"
                )
            if not dicts and data.get("inboxReviewed") is not True and slack_cand:
                fails.append(
                    "FAIL slack: candidates were fetched but inbox was not reviewed — "
                    "open leftover DMs/threads then classify"
                )
            for it in dicts:
                ident = str(it.get("id") or "")
                has_link = bool(
                    str(it.get("slackUrl") or "").strip()
                    or str(it.get("channelId") or "").strip()
                    or re.search(r"(?:slack-)?[CGD][A-Z0-9]{8,}", ident, re.I)
                )
                if not has_link:
                    fails.append(
                        f"FAIL slack: {ident or it.get('label') or '?'} missing slackUrl (or channelId+ts)"
                    )
        if re.match(r"(mail|email|gmail)\b", title, re.I):
            dicts = [it for it in rows if isinstance(it, dict)]
            strings = [it for it in rows if isinstance(it, str)]
            if strings:
                fails.append(
                    f"FAIL mail: {len(strings)} string row(s) — wrap as objects with mailUrl"
                )
            if _copied_candidates(dicts, mail_cand):
                fails.append(
                    "FAIL mail: page is the search hit list — open leftover humans and keep only what still needs you"
                )
            if not dicts and data.get("inboxReviewed") is not True and mail_cand:
                fails.append(
                    "FAIL mail: candidates were fetched but inbox was not reviewed — "
                    "open leftover mail then classify"
                )
            for it in dicts:
                if not (
                    str(it.get("mailUrl") or "").strip()
                    or str(it.get("messageId") or it.get("gmailId") or "").strip()
                ):
                    ident = str(it.get("id") or it.get("label") or "?")
                    fails.append(f"FAIL mail: {ident} missing mailUrl (or messageId)")
        if re.search(r"follow-up due|needs a touch|still watching|^watch\b|no action", title, re.I):
            for it in rows:
                if not isinstance(it, dict):
                    fails.append(
                        f"FAIL case-row: {title} row is {type(it).__name__} — one case object per entry"
                    )
                    continue
                ident = str(it.get("id") or it.get("label") or "?")
                blob = str(it.get("label") or "") + " " + str(it.get("detail") or "")
                if re.search(r"\bbatch\b|\b\d+\s+cases\b", blob, re.I):
                    fails.append(
                        f"FAIL bunch: {ident} — Follow-up due / No action needed is one case per row, not a batch"
                    )
                nums = re.findall(r"\b(\d{8,})\b", blob)
                cn = re.sub(r"\D", "", str(it.get("caseNumber") or ""))
                if cn:
                    nums.append(cn)
                uniq = []
                for n in nums:
                    if n not in uniq:
                        uniq.append(n)
                if len(uniq) > 1:
                    fails.append(
                        f"FAIL bunch: {ident} lists {len(uniq)} cases — split into one row each"
                    )
                if not cn:
                    fails.append(f"FAIL case-row: {ident} missing caseNumber")
    _check_model_rules(data, gather, fails)
    return fails, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument(
        "--fix",
        action="store_true",
        help="Rewrite compact when, string chronology, and string slack/mail rows, then re-check.",
    )
    args = ap.parse_args()
    path = pathlib.Path(args.path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if args.fix:
        sanitize = _load_sanitize()
        sanitize.sanitize(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fails, warns = check(data)
    for line in fails + warns:
        print(line)
    if not fails and not warns:
        print("OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
