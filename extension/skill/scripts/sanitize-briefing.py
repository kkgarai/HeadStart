#!/usr/bin/env python3
"""Normalize planner JSON before it hits the page.

Fixes laptop-IST clocks, empty Shift tiles, Needs you now vs Needs us now,
and section order so a bad gather cannot bury the header.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

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
        if "zoneinfo" in parts and ZoneInfo is not None:
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
    if raw and ZoneInfo is not None:
        try:
            return ZoneInfo(raw)
        except Exception:
            pass
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:
        return timezone.utc


_TZ_FOLD = (
    ("PDT", "PT"),
    ("PST", "PT"),
    ("EDT", "ET"),
    ("EST", "ET"),
    ("CDT", "CT"),
    ("CST", "CT"),
    ("MDT", "MT"),
    ("MST", "MT"),
)


_IST_ZONE_NAMES = {"Asia/Kolkata", "Asia/Calcutta", "Asia/Colombo"}


def fold_tz_abbr(raw: object) -> str:
    """Fold DST abbreviations (PDT→PT). Not a city/zone pair."""
    token = str(raw or "").strip()
    if not token:
        return ""
    upper = token.upper()
    for src, dest in _TZ_FOLD:
        if upper == src:
            return dest
    return token


def display_zone_short(zone_name: object, abbr: object = "") -> str:
    """Label only. Asia/Kolkata, Asia/Calcutta, and Asia/Colombo read as IST. The zone is not changed."""
    if str(zone_name or "").strip() in _IST_ZONE_NAMES:
        return "IST"
    return fold_tz_abbr(abbr)


def fold_tz_in_text(text: object) -> str:
    s = str(text or "")
    for src, dest in _TZ_FOLD:
        s = re.sub(rf"\b{src}\b", dest, s)
    return s

TITLE_MAP = (
    (r"^needs you now$", "Needs us now"),
    (r"^need you now$", "Needs us now"),
    (r"^needs us now$", "Needs us now"),
    (r"^customer asked", "Customer asked for a meeting"),
    (r"^slack\b", "Slack"),
    (r"^(mail|email|gmail)\b", "Mail"),
    (r"^quick win", "Quick wins"),
    (r"^(needs a touch|follow[- ]up due)$", "Follow-up due"),
    (r"^(still watching|watch|no action needed)$", "Still watching"),
    (r"^(today['’]?s plan|day plan|calendar|rest of day)$", "Today's plan"),
    (r"^before you log off$", "Before you log off"),
    (r"^tomorrow", "Tomorrow, first thing"),
)

ORDER = (
    "Needs us now",
    "Customer asked for a meeting",
    "Slack",
    "Mail",
    "GUS",
    "Quick wins",
    "Follow-up due",
    "Still watching",
    "Today's plan",
    "Before you log off",
    "Tomorrow, first thing",
)


def _hm(raw: str, fb: tuple[int, int] = (8, 0)) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(raw or "").strip())
    if not m:
        return fb
    return int(m.group(1)), int(m.group(2))


def _ampm(hhmm: str) -> str:
    h, mi = _hm(hhmm)
    ap = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mi:02d} {ap}"


def _section_key(title: str) -> str:
    t = re.sub(r"^[^\w#]+", "", str(title or ""))
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
    return t.replace("’", "'").strip().lower()


def canonicalize_titles(data: dict) -> None:
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        for pat, canon in TITLE_MAP:
            if re.search(pat, key, re.I):
                sec["title"] = canon
                break
        if sec.get("title") == "Needs us now":
            sec["tone"] = "now"
        if sec.get("title") == "Customer asked for a meeting":
            sec["tone"] = sec.get("tone") or "now"
        if sec.get("title") == "Quick wins":
            sec["open"] = True


def sort_sections(data: dict) -> None:
    rank = {name: i for i, name in enumerate(ORDER)}
    secs = [s for s in (data.get("sections") or []) if isinstance(s, dict)]
    secs.sort(key=lambda s: rank.get(s.get("title"), 80))
    data["sections"] = secs


def fix_laptop_ist_clock(data: dict) -> None:
    """Night shifts stay as Assembled recorded them. Local and shift are display clocks."""
    return


def normalize_shift_hhmm(data: dict) -> None:
    for key in ("shiftStart", "shiftEnd"):
        raw = str(data.get(key) or "").strip()
        m = re.match(r"^\d{8}T(\d{2})(\d{2})", raw)
        if m:
            data[key] = f"{m.group(1)}:{m.group(2)}"


def _walk_items(data: dict):
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for it in sec.get("items") or []:
            if isinstance(it, dict):
                yield sec, it
        for grp in sec.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            for it in grp.get("items") or []:
                if isinstance(it, dict):
                    yield sec, it


INBOX_UNREAD_RE = re.compile(r"not opened|\bunread\b|\bunopened\b", re.I)
INBOX_REPLY_RE = re.compile(r"needs a reply|unresponded|still on you", re.I)


def inbox_group_kind(title: object) -> str:
    """unread = Not opened. opened = Needs a reply. Never treat 'not opened' as opened."""
    t = str(title or "").lower()
    if INBOX_UNREAD_RE.search(t):
        return "unread"
    if INBOX_REPLY_RE.search(t):
        return "opened"
    if re.search(r"\bopened\b", t) and "not opened" not in t:
        return "opened"
    return ""


def _slack_ts(item: dict) -> str:
    ts = str(item.get("ts") or item.get("thread_ts") or item.get("message_ts") or "").strip()
    if re.match(r"^\d{10}\.\d+$", ts):
        return ts
    url = str(item.get("slackUrl") or item.get("permalink") or "")
    m = re.search(r"/p(\d{10})(\d{1,6})", url)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    ident = str(item.get("id") or "")
    m = re.search(r"-(\d{10})(\d{1,6})$", ident)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return ""


def _slack_channel_key(item: dict) -> str:
    cid = str(item.get("channelId") or item.get("slackChannel") or item.get("channel") or "").strip()
    if not re.match(r"^[CGD][A-Z0-9]{8,}$", cid, re.I):
        url = str(item.get("slackUrl") or item.get("permalink") or "")
        m = re.search(r"/archives/([CGD][A-Z0-9]+)", url, re.I)
        if m:
            cid = m.group(1)
        else:
            ident = str(item.get("id") or "")
            m = re.search(r"([CGD][A-Z0-9]{8,})", ident)
            cid = m.group(1) if m else ident
    if re.match(r"^D[A-Z0-9]{8,}$", cid, re.I):
        return cid
    ts = _slack_ts(item)
    if cid and ts:
        return f"{cid}:{ts}"
    return cid or str(item.get("id") or "")


def _is_slack_row(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    kind = str(item.get("kind") or "").lower()
    if item.get("eventId") or kind in ("meeting", "case") or item.get("caseUrl") or item.get("caseNumber"):
        return False
    if kind == "slack":
        return True
    url = str(item.get("slackUrl") or item.get("permalink") or "")
    return "slack.com" in url.lower() or url.lower().startswith("slack://")


def _is_mail_row(item: dict) -> bool:
    if not isinstance(item, dict) or _is_slack_row(item):
        return False
    kind = str(item.get("kind") or "").lower()
    if item.get("eventId") or item.get("invite") is True or kind in (
        "meeting",
        "case",
        "plan",
        "task",
        "gus",
        "break",
        "slack",
    ):
        return False
    if item.get("caseUrl") or item.get("gusUrl") or item.get("workId") or item.get("caseNumber"):
        return False
    if kind in ("mail", "email", "gmail"):
        return True
    url = str(item.get("mailUrl") or item.get("gmailUrl") or "")
    return "mail.google.com" in url.lower()


def slack_item_kind(item: dict, data: dict | None = None) -> str:
    """Copy the model's slackBucket. Do not infer from unread or Peek bucket."""
    if not isinstance(item, dict):
        return ""
    raw = str(item.get("slackBucket") or "").lower()
    if INBOX_UNREAD_RE.search(raw) or raw in ("unread", "unopened"):
        return "unread"
    if INBOX_REPLY_RE.search(raw) or raw in ("reply", "opened"):
        return "opened"
    return ""


def mail_item_kind(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    raw = str(item.get("mailBucket") or "").lower()
    if INBOX_UNREAD_RE.search(raw) or raw in ("unread", "unopened"):
        return "unread"
    if INBOX_REPLY_RE.search(raw) or raw in ("reply", "opened"):
        return "opened"
    return ""


def peel_stray_inbox_rows(data: dict) -> None:
    if not isinstance(data, dict):
        return
    slack_sec = None
    mail_sec = None
    stray_slack: list[dict] = []
    stray_mail: list[dict] = []
    rest = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        if re.match(r"slack\b", title, re.I):
            slack_sec = sec
            continue
        if re.match(r"(mail|email|gmail)\b", title, re.I):
            mail_sec = sec
            continue

        def peel(lst, pred, bucket):
            stay = []
            for it in lst or []:
                if isinstance(it, dict) and pred(it):
                    bucket.append(it)
                elif isinstance(it, dict):
                    stay.append(it)
            return stay

        sec["items"] = peel(sec.get("items"), _is_slack_row, stray_slack)
        sec["items"] = peel(sec.get("items"), _is_mail_row, stray_mail)
        for grp in sec.get("groups") or []:
            if isinstance(grp, dict):
                grp["items"] = peel(grp.get("items"), _is_slack_row, stray_slack)
                grp["items"] = peel(grp.get("items"), _is_mail_row, stray_mail)
        rest.append(sec)
    if stray_slack:
        if slack_sec is None:
            slack_sec = {"title": "Slack", "asTask": True, "groups": [], "items": []}
        items = list(slack_sec.get("items") or [])
        items.extend(stray_slack)
        slack_sec["items"] = items
    if stray_mail:
        if mail_sec is None:
            mail_sec = {"title": "Mail", "asTask": True, "groups": [], "items": []}
        items = list(mail_sec.get("items") or [])
        items.extend(stray_mail)
        mail_sec["items"] = items
    insert_at = 0
    for i, sec in enumerate(rest):
        if re.search(r"needs (us|you) now|customer asked for a meeting", str(sec.get("title") or ""), re.I):
            insert_at = i + 1
    if slack_sec is not None:
        rest.insert(insert_at, slack_sec)
        insert_at += 1
    if mail_sec is not None:
        rest.insert(insert_at, mail_sec)
    data["sections"] = rest


def stamp_inbox_empty(sec: dict, data: dict | None, is_slack: bool) -> None:
    """Never print Slack/Mail — clear when leftover fetch did not run."""
    if not isinstance(sec, dict):
        return
    if _inbox_rows(sec):
        sec.pop("empty", None)
        return
    fetch_key = "slackFetchOk" if is_slack else "mailFetchOk"
    if isinstance(data, dict) and data.get(fetch_key) is False:
        sec.pop("empty", None)
        return
    sec["empty"] = sec.get("empty") or ("Slack — clear" if is_slack else "Mail — clear")


def normalize_inbox_buckets(data: dict) -> None:
    """Keep the model's Slack/Mail groups. Stamp bucket from the group title only."""
    if not isinstance(data, dict):
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        is_slack = bool(re.match(r"slack\b", title, re.I))
        is_mail = bool(re.match(r"(mail|email|gmail)\b", title, re.I))
        if not is_slack and not is_mail:
            continue
        unread: list[dict] = []
        opened: list[dict] = []
        seen: set[str] = set()

        def take(it: dict, group_kind: str) -> None:
            ident = _slack_channel_key(it) if is_slack else str(it.get("id") or it.get("mailUrl") or "")
            if ident and ident in seen:
                return
            if ident:
                seen.add(ident)
            kind = group_kind or (
                slack_item_kind(it) if is_slack else mail_item_kind(it)
            )
            if not kind:
                kind = "opened"
            if is_slack:
                it["slackBucket"] = "unread" if kind == "unread" else "reply"
            else:
                it["mailBucket"] = "unread" if kind == "unread" else "reply"
            (unread if kind == "unread" else opened).append(it)

        for grp in sec.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            gk = inbox_group_kind(grp.get("title") or grp.get("name"))
            rows = grp.get("items")
            if not isinstance(rows, list) or not rows:
                rows = grp.get("rows") if isinstance(grp.get("rows"), list) else []
            for it in rows:
                if isinstance(it, dict):
                    take(it, gk)
        for it in sec.get("items") or []:
            if isinstance(it, dict):
                take(it, "")
        groups = []
        if unread:
            groups.append({"title": "Not opened", "items": unread})
        if opened:
            groups.append({"title": "Needs a reply", "items": opened})
        sec["asTask"] = True
        sec["groups"] = groups
        sec["items"] = []
        if groups:
            sec.pop("empty", None)
        else:
            stamp_inbox_empty(sec, data, is_slack)


_SLACK_BOT_WHO = re.compile(
    r"\bbot\b|app$|notifications|storm|psbot|career connect",
    re.I,
)
_SLACK_PERSON_EMAIL = re.compile(
    r"([A-Z][A-Za-z][A-Za-z .'-]{0,70}?)\s*<([^>\s]+@[^>]+)>",
)


def _norm_person(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


def _is_self_person(who: object, data: dict | None) -> bool:
    w = _norm_person(who).lower()
    me = _norm_person((data or {}).get("name")).lower()
    if not w or not me:
        return False
    if w == me or w.startswith(me + " ") or me.startswith(w + " "):
        return True
    w_parts = [p for p in w.split() if p]
    m_parts = [p for p in me.split() if p]
    return (
        len(w_parts) >= 2
        and len(m_parts) >= 2
        and w_parts[0] == m_parts[0]
        and w_parts[-1] == m_parts[-1]
    )


def people_in_slack_text(text: object) -> list[str]:
    people: list[str] = []
    seen: set[str] = set()
    for match in _SLACK_PERSON_EMAIL.finditer(str(text or "")):
        name = _norm_person(match.group(1))
        addr = (match.group(2) or "").lower()
        if not name or _SLACK_BOT_WHO.search(name):
            continue
        if "botuser" in addr or "slackbot" in addr:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        people.append(name)
    return people


def is_slack_dm(item: dict) -> bool:
    cid = str(item.get("channelId") or item.get("slackChannel") or "").strip()
    if cid.startswith("D"):
        return True
    url = str(item.get("slackUrl") or item.get("permalink") or "")
    if re.search(r"/archives/D[A-Z0-9]+", url, re.I):
        return True
    if re.search(r"\(DM\)", str(item.get("label") or ""), re.I):
        return True
    return bool(re.search(r"\bDM\b", str(item.get("channel") or ""), re.I))


def _dm_label_who(label: object) -> str:
    match = re.match(r"^(.+?)\s*\(DM\)", str(label or ""), re.I)
    return _norm_person(match.group(1) if match else "")


def _dm_label_snippet(item: dict) -> str:
    lab = str(item.get("label") or "")
    match = re.search(r"\(DM\)\s*—\s*(.+)$", lab)
    if match:
        return _norm_person(match.group(1))[:120]
    return _norm_person(item.get("snippet") or "")[:120]


def slack_dm_peer(item: dict, data: dict | None) -> str:
    """Other human in a DM. Never this engineer."""
    people: list[str] = []
    seen: set[str] = set()

    def add(name: object) -> None:
        n = _norm_person(name)
        n = re.sub(r"\s*\(ID:.*$", "", n).strip()
        n = re.sub(
            r"^(direct message with|dm with|im with)\s+",
            "",
            n,
            flags=re.I,
        )
        if not n or re.fullmatch(r"DM|IM|MPI?M|Direct Message", n, re.I):
            return
        if _is_self_person(n, data) or _SLACK_BOT_WHO.search(n):
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        people.append(n)

    for name in people_in_slack_text(item.get("openedClip") or ""):
        add(name)
    add(item.get("peer"))
    add(item.get("from"))
    add(item.get("channel"))
    blob = " ".join(
        str(item.get(k) or "") for k in ("detail", "label", "snippet")
    )
    mgr = _norm_person((data or {}).get("manager"))
    if mgr:
        first = mgr.split()[0]
        if first and re.search(rf"\b{re.escape(first)}\b", blob):
            add(mgr)
    return ", ".join(people[:3])


def stamp_slack_dm_label(item: dict, data: dict | None) -> None:
    """Title a DM with the counterpart. Search From is often this engineer."""
    if not isinstance(item, dict) or item.get("gusBot") is True or not is_slack_dm(item):
        return
    peer = slack_dm_peer(item, data)
    snippet = _dm_label_snippet(item)
    current = _dm_label_who(item.get("label"))
    if peer:
        item["peer"] = peer
        who = peer
    elif current and not _is_self_person(current, data) and current.upper() != "DM":
        who = current
    else:
        who = "DM"
    label = f"{who} (DM)"
    if snippet:
        label = f"{label} — {snippet}"
    item["label"] = label[:160]


def relabel_slack_dms(data: dict) -> None:
    if not isinstance(data, dict):
        return
    for _sec, it in _walk_items(data):
        if str(it.get("kind") or "").lower() == "slack" or is_slack_dm(it):
            stamp_slack_dm_label(it, data)
    for row in data.get("slackCandidates") or []:
        if isinstance(row, dict):
            stamp_slack_dm_label(row, data)


def _slack_id_parts(item_id: str) -> tuple[str, str]:
    """slack-D06DB03LMEV or slack-C0C36JY64JC-1790021624045609 → channel, ts."""
    raw = str(item_id or "").strip()
    match = re.match(r"^(?:slack-)?([CGD][A-Z0-9]{8,})(?:-(\d{11,16}))?$", raw, re.I)
    if not match:
        return "", ""
    compact = match.group(2) or ""
    ts = f"{compact[:10]}.{compact[10:]}" if len(compact) > 10 else ""
    return match.group(1), ts


def fill_slack_urls(data: dict) -> None:
    host = "https://salesforce.enterprise.slack.com"
    for _sec, it in _walk_items(data):
        existing = ""
        for key in ("slackUrl", "slackPermalink", "permalink"):
            u = str(it.get(key) or "").strip()
            if "slack.com" in u.lower() or u.lower().startswith("slack://"):
                existing = u
                break
        if existing:
            it["slackUrl"] = existing
            continue
        id_ch, id_ts = _slack_id_parts(str(it.get("id") or ""))
        blob = " ".join(
            str(it.get(k) or "")
            for k in ("id", "channelId", "slackChannel", "channel", "ts", "threadTs", "message_ts", "detail", "label")
        )
        ch_m = re.search(r"\b([CGD][A-Z0-9]{8,})\b", blob)
        ts_m = re.search(r"\b(\d{10}\.\d+)\b", blob)
        ch = str(it.get("channelId") or it.get("slackChannel") or it.get("channel") or id_ch or "").strip()
        ts = str(it.get("ts") or it.get("threadTs") or it.get("message_ts") or id_ts or "").strip()
        if not re.match(r"^[CGD][A-Z0-9]{8,}$", ch, re.I) and ch_m:
            ch = ch_m.group(1)
        if not re.match(r"^\d{10}\.\d+$", ts) and ts_m:
            ts = ts_m.group(1)
        if not re.match(r"^[CGD][A-Z0-9]{8,}$", ch, re.I):
            continue
        if re.match(r"^\d{10}\.\d+$", ts):
            it["slackUrl"] = f"{host}/archives/{ch}/p{ts.replace('.', '')}"
            it["channelId"] = ch
            it["ts"] = ts
        else:
            it["slackUrl"] = f"{host}/archives/{ch}"
            it["channelId"] = ch


def _assembled_row(it: dict) -> bool:
    if it.get("assembled") is True:
        return True
    if str(it.get("kind") or "").lower() == "assembled":
        return True
    lab = str(it.get("label") or "").strip()
    return bool(re.match(r"^(log[\s-]?in|log[\s-]?out|casework|chat|lunch|break|coffee)$", lab, re.I))


def _all_day_stamp(raw: str) -> bool:
    s = str(raw or "").strip()
    return bool(s) and "T" not in s


def organize_plan(data: dict) -> None:
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        items = [it for it in (sec.get("items") or []) if isinstance(it, dict) and not _assembled_row(it)]
        for grp in sec.get("groups") or []:
            if isinstance(grp, dict):
                grp["items"] = [
                    it for it in (grp.get("items") or []) if isinstance(it, dict) and not _assembled_row(it)
                ]
        if re.search(r"today['’]?s plan|^calendar$|^day plan|rest of day", key, re.I):
            kept = []
            for it in items:
                start = str(it.get("startStamp") or "")
                end = str(it.get("endStamp") or "")
                if _all_day_stamp(start) or _all_day_stamp(end):
                    continue
                kept.append(it)
            kept.sort(key=lambda it: str(it.get("startStamp") or ""))
            sec["items"] = kept
            sec["title"] = "Today's plan"
        else:
            sec["items"] = items
        groups = sec.get("groups") or []
        if any(isinstance(g, dict) and (g.get("items") or []) for g in groups) or (sec.get("items") or []):
            if sec.get("empty") in ("Slack — clear", "Mail — clear", "No action needed — clear"):
                sec.pop("empty", None)


def _section_kind_guess(title: object) -> str:
    key = _section_key(title)
    if re.match(r"slack\b", key, re.I):
        return "slack"
    if re.match(r"(mail|email|gmail)\b", key, re.I):
        return "mail"
    return ""


def coerce_item(it, kind: str = ""):
    if isinstance(it, dict):
        return it
    if isinstance(it, str) and it.strip():
        return {"id": "", "label": it.strip(), "detail": "", "kind": kind or "task"}
    return None


def coerce_row_items(data: dict) -> None:
    """String leftover/groups would crash hoist (.get) and then get wiped. Wrap them."""
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        kind = _section_kind_guess(sec.get("title"))
        rows = []
        for it in sec.get("items") or []:
            row = coerce_item(it, kind)
            if row:
                rows.append(row)
        sec["items"] = rows
        for grp in sec.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            grown = []
            for it in grp.get("items") or []:
                row = coerce_item(it, kind)
                if row:
                    grown.append(row)
            grp["items"] = grown


def fill_mail_urls(data: dict) -> None:
    for _sec, it in _walk_items(data):
        existing = ""
        for key in ("mailUrl", "gmailUrl", "messageUrl"):
            u = str(it.get(key) or "").strip()
            if "mail.google.com" in u.lower() or "gmail.com" in u.lower():
                existing = u
                break
        if existing:
            it["mailUrl"] = existing
            continue
        mid = str(it.get("messageId") or it.get("gmailId") or "").strip()
        if not mid:
            id_m = re.match(r"^mail-([0-9a-f]{10,})$", str(it.get("id") or ""), re.I)
            if id_m:
                mid = id_m.group(1)
        if re.fullmatch(r"[0-9a-f]{10,}", mid, re.I):
            it["messageId"] = mid
            it["mailUrl"] = f"https://mail.google.com/mail/u/0/#inbox/{mid}"


def repair_mail_labels(data: dict) -> None:
    """A kept mail row shows the subject, not the Gmail id."""
    subjects: dict[str, tuple[str, str]] = {}
    try:
        text = pathlib.Path("/tmp/planner-inbox.txt").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for block in re.split(r"\n(?=## mail )", text):
        mid_m = re.search(r"## mail (\S+)", block)
        subj_m = re.search(r"(?m)^- subject: (.*)$", block)
        from_m = re.search(r"(?m)^- from: (.*)$", block)
        if not mid_m or not subj_m:
            continue
        subjects[mid_m.group(1)] = (subj_m.group(1).strip(), from_m.group(1).strip() if from_m else "")
    id_re = re.compile(r"^(?:mail-)?[0-9a-f]{8,}$", re.I)

    def fix(it: dict) -> None:
        ident = str(it.get("id") or "")
        if str(it.get("kind") or "").lower() != "mail" and not ident.startswith("mail-") and "mail.google.com" not in str(it.get("mailUrl") or ""):
            return
        ident = str(it.get("id") or "")
        label = str(it.get("label") or "").strip()
        hit = subjects.get(ident) or subjects.get("mail-" + label) or subjects.get(label)
        if not hit:
            for key, val in subjects.items():
                if ident and ident in key:
                    hit = val
                    break
        if not hit:
            return
        subject, sender = hit
        if subject and (not label or id_re.match(label) or label == ident):
            it["label"] = subject[:180]
            it["detail"] = sender[:160] if sender else str(it.get("detail") or "")

    for _sec, it in _walk_items(data):
        if isinstance(it, dict):
            fix(it)


def _mail_blob(it: dict) -> str:
    return " ".join(
        str(it.get(k) or "")
        for k in (
            "label",
            "from",
            "fromAddress",
            "sender",
            "subject",
        )
    )


def is_keep_mail(it: dict) -> bool:
    if not isinstance(it, dict):
        return False
    return bool(KEEP_MAIL_RE.search(_mail_blob(it)))


def is_case_comment_mail(it: dict) -> bool:
    """Gmail copies of OrgCS case EmailMessage / case comments. Not GUS, Chatter, or Black Tab."""
    if not isinstance(it, dict):
        return False
    if is_keep_mail(it):
        return False
    blob = _mail_blob(it)
    if CASE_COMMENT_MAIL_RE.search(blob):
        return True
    addr = str(it.get("from") or it.get("fromAddress") or it.get("sender") or "")
    if re.search(r"customersupport@salesforce\.com", addr, re.I):
        return True
    if it.get("caseNumber") or it.get("caseUrl"):
        kind = str(it.get("kind") or "").lower()
        if kind in ("mail", "email", "gmail", ""):
            return True
    return False


def drop_case_comment_mail(data: dict) -> None:
    if not isinstance(data, dict):
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not MAIL_SEC_RE.search(_section_key(sec.get("title"))):
            continue
        sec["items"] = [
            it
            for it in (sec.get("items") or [])
            if isinstance(it, dict) and not is_case_comment_mail(it)
        ]
        leftover = list(sec["items"])
        for grp in sec.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            grp["items"] = [
                it
                for it in (grp.get("items") or [])
                if isinstance(it, dict) and not is_case_comment_mail(it)
            ]
            leftover.extend(it for it in grp["items"] if isinstance(it, dict))
        if not leftover:
            sec["empty"] = sec.get("empty") or "Mail — clear"


MEETING_SEC_RE = re.compile(r"customer asked for a (meeting|call)|meetings to (book|schedule)", re.I)
STAMP_RE = re.compile(r"^\d{8}T\d{6}$")


def _is_real_customer_meeting(it: dict) -> bool:
    """A case they asked to meet on. A named slot keeps its stamps. No day and no time stays, with no invented slot."""
    if not isinstance(it, dict):
        return False
    num = re.sub(r"\D", "", str(it.get("caseNumber") or ""))
    if len(num) < 6:
        return False
    start = str(it.get("meetingStartStamp") or "").strip()
    end = str(it.get("meetingEndStamp") or "").strip()
    if STAMP_RE.match(start) and STAMP_RE.match(end):
        it["meetingSlot"] = "named"
        return True
    it.pop("meetingStartStamp", None)
    it.pop("meetingEndStamp", None)
    it["meetingSlot"] = "open"
    it["when"] = "No time"
    return True


def fetch_fresh_quote(avoid_text: str = "") -> dict | None:
    """One new quote every publish. Never reuse the previous page text. Fail → omit."""
    avoid = re.sub(r"\s+", " ", str(avoid_text or "")).strip().lower()

    def parse(payload) -> dict | None:
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if not isinstance(payload, dict):
            return None
        text = str(
            payload.get("q") or payload.get("quote") or payload.get("text") or ""
        ).strip()
        author = str(payload.get("a") or payload.get("author") or "").strip()
        if not text:
            return None
        return {"text": text[:280], "author": author[:80]}

    for url in (
        "https://zenquotes.io/api/random",
        "https://dummyjson.com/quotes/random",
    ):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "engineer-day-planner/1"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            quote = parse(payload)
            if not quote:
                continue
            if avoid and quote["text"].strip().lower() == avoid:
                continue
            return quote
        except Exception:
            continue
    return None


def drop_invented_meetings(data: dict) -> None:
    """Customer asked for a meeting. Named slots keep their times. An ask with no day or time stays. Omit when empty."""
    if not isinstance(data, dict):
        return
    secs = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not MEETING_SEC_RE.search(_section_key(sec.get("title"))):
            secs.append(sec)
            continue
        kept = [it for it in (sec.get("items") or []) if _is_real_customer_meeting(it)]
        if not kept:
            continue
        sec["items"] = kept
        sec["title"] = "Customer asked for a meeting"
        sec["open"] = True
        sec["tone"] = "now"
        sec.pop("empty", None)
        secs.append(sec)
    data["sections"] = secs


def stamp_needs_when(data: dict) -> None:
    compact = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$")

    def ampm(h: int, mi: int) -> str:
        h12 = h % 12 or 12
        ap = "AM" if h < 12 else "PM"
        return f"{h12}:{mi:02d} {ap}"

    def drop_orphan_start(it: dict) -> None:
        start = str(it.get("startStamp") or "").strip()
        end = str(it.get("endStamp") or "").strip()
        if compact.match(start) and not end:
            it.pop("startStamp", None)

    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(r"needs (us|you) now", _section_key(sec.get("title")), re.I):
            continue
        for it in sec.get("items") or []:
            if not isinstance(it, dict):
                continue
            when = str(it.get("when") or "").strip()
            cm = compact.match(when)
            if cm:
                it["when"] = ampm(int(cm.group(4)), int(cm.group(5)))
                drop_orphan_start(it)
                continue
            if when:
                drop_orphan_start(it)
                continue
            blob = str(it.get("detail") or "") + " " + str(it.get("label") or "")
            clock = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", blob, re.I)
            if clock:
                it["when"] = re.sub(r"\s+", " ", clock.group(1).upper().replace("AM", " AM").replace("PM", " PM")).strip()
                drop_orphan_start(it)
                continue
            start = str(it.get("startStamp") or "").strip()
            sm = compact.match(start)
            if sm:
                it["when"] = ampm(int(sm.group(4)), int(sm.group(5)))
                drop_orphan_start(it)
                continue
            it["when"] = "Now"


def hoist_peek_fields(data: dict) -> None:
    """Page Peek is the model's analysis. Do not hoist raw activity as chronology."""
    peeks = data.get("peeks") if isinstance(data.get("peeks"), dict) else {}
    for _sec, it in _walk_items(data):
        num = re.sub(r"\D", "", str(it.get("caseNumber") or it.get("id") or ""))
        spec = peeks.get(num) if len(num) >= 6 and isinstance(peeks.get(num), dict) else {}
        peek = it.get("peek") if isinstance(it.get("peek"), dict) else {}
        summary = str(peek.get("summary") or it.get("summary") or spec.get("summary") or "").strip()
        chrono = peek.get("chronology") if isinstance(peek.get("chronology"), list) else None
        if not chrono and isinstance(spec.get("chronology"), list):
            chrono = spec.get("chronology")
        if chrono is None and it.get("activity") is not it.get("chronology"):
            chrono = it.get("chronology") if isinstance(it.get("chronology"), list) else []
        if not isinstance(chrono, list):
            chrono = []
        if summary:
            it["summary"] = summary
        if chrono:
            it["chronology"] = chrono
        bucket = str(peek.get("bucket") or it.get("bucket") or "").strip().lower()
        if summary or chrono or bucket:
            it["peek"] = {"summary": summary, "chronology": chrono}
            if bucket in ("now", "follow", "watch", "meeting", "quick"):
                it["peek"]["bucket"] = bucket
                it["bucket"] = bucket


SOLUTION_PROVIDED_RE = re.compile(r"\bsolution\s+provided\b", re.I)
NMI_RE = re.compile(r"\bneed(?:s)?\s+more\s+information\b|\bnmi\b", re.I)


def _status_blob(it: dict) -> str:
    if not isinstance(it, dict):
        return ""
    peek = it.get("peek") if isinstance(it.get("peek"), dict) else {}
    return " ".join(
        str(x or "")
        for x in (
            it.get("status"),
            it.get("Status"),
            it.get("detail"),
            peek.get("status"),
            peek.get("orgStatus"),
        )
    )


def _is_solution_provided(it: dict) -> bool:
    """OrgCS Status=Solution Provided is waiting on the customer — never Needs us now."""
    return bool(SOLUTION_PROVIDED_RE.search(_status_blob(it)))


def _is_nmi(it: dict) -> bool:
    """OrgCS Status=Need More Information is waiting on the customer — never Needs us now."""
    return bool(NMI_RE.search(_status_blob(it)))


def _is_customer_wait_status(it: dict) -> bool:
    return _is_solution_provided(it) or _is_nmi(it)


def _wait_bucket(it: dict, num: str, follow_nums: set[str]) -> str:
    if _is_nmi(it):
        return "watch"
    if _is_solution_provided(it) and num in follow_nums:
        return "follow"
    return "watch"


def _set_peek_bucket(data: dict, num: str, dest: str, it: dict | None = None) -> None:
    if it is not None and isinstance(it.get("peek"), dict):
        it["peek"]["bucket"] = dest
        it["bucket"] = dest
    elif it is not None:
        it["bucket"] = dest
    peeks = data.get("peeks") if isinstance(data.get("peeks"), dict) else None
    if not isinstance(peeks, dict) or not num:
        return
    row = peeks.get(num) if isinstance(peeks.get(num), dict) else {}
    row["bucket"] = dest
    peeks[num] = row
    data["peeks"] = peeks


def _append_ranked_cases(data: dict, dest: str, items: list) -> None:
    if not items:
        return
    titles = {
        "now": "Needs us now",
        "follow": "Follow-up due",
        "watch": "Still watching",
        "meeting": "Customer asked for a meeting",
        "quick": "Quick wins",
    }
    found = _ranked_case_sections(data)
    sec = found.get(dest)
    if sec is None:
        sec = {"title": titles.get(dest) or dest, "open": dest != "watch", "items": []}
        if dest == "watch":
            sec["empty"] = "No action needed — clear"
        sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        data["sections"] = sections
        sections.append(sec)
        found[dest] = sec
    existing = list(sec.get("items") or [])
    have = {_case_num(it) for it in existing if isinstance(it, dict)}
    for it in items:
        if not isinstance(it, dict):
            continue
        num = _case_num(it)
        if num and num in have:
            continue
        existing.append(it)
        if num:
            have.add(num)
    sec["items"] = existing
    if existing:
        sec.pop("empty", None)


def _recount_need_you(data: dict) -> None:
    found = _ranked_case_sections(data)
    now_sec = found.get("now") or {}
    follow_sec = found.get("follow") or {}
    meet_sec = found.get("meeting") or {}
    data["needYou"] = sum(
        1
        for it in list(now_sec.get("items") or [])
        + list(follow_sec.get("items") or [])
        + list(meet_sec.get("items") or [])
        if isinstance(it, dict) and it.get("done") is not True and _real_case_row(it)
    )


def demote_solution_provided_from_now(data: dict) -> None:
    """The model ranks. Python does not move a case out of Needs us now."""
    return
    if not isinstance(data, dict):
        return
    found = _ranked_case_sections(data)
    now_sec = found.get("now")
    follow_nums = set(_ai_num_list(data, "followUpDue") or [])
    moved_follow: list[dict] = []
    moved_watch: list[dict] = []
    if now_sec:
        keep = []
        for it in list(now_sec.get("items") or []):
            if not isinstance(it, dict) or not _is_customer_wait_status(it):
                keep.append(it)
                continue
            num = _case_num(it)
            dest = _wait_bucket(it, num, follow_nums)
            if num:
                _set_peek_bucket(data, num, dest, it)
                it["id"] = f"{'follow' if dest == 'follow' else 'watch'}-{num}"
                it["caseNumber"] = num
                it.pop("when", None)
            if dest == "follow":
                moved_follow.append(it)
            else:
                moved_watch.append(it)
        now_sec["items"] = keep
        now_nums = [_case_num(it) for it in keep if isinstance(it, dict) and _case_num(it)]
    else:
        now_nums = []
    data["needsUsNow"] = now_nums
    if moved_follow:
        follow_list = list(_ai_num_list(data, "followUpDue") or [])
        for it in moved_follow:
            num = _case_num(it)
            if num and num not in follow_list:
                follow_list.append(num)
        data["followUpDue"] = follow_list
        _append_ranked_cases(data, "follow", moved_follow)
    if moved_watch:
        watch_list = list(_ai_num_list(data, "stillWatching") or [])
        for it in moved_watch:
            num = _case_num(it)
            if num and num not in watch_list:
                watch_list.append(num)
        data["stillWatching"] = watch_list
        _append_ranked_cases(data, "watch", moved_watch)
    canonicalize_titles(data)
    sort_sections(data)
    _recount_need_you(data)


def drop_solution_provided_from_plan(data: dict) -> None:
    """The model writes todayPlan. Python does not drop a case window by status."""
    return
    if not isinstance(data, dict):
        return
    sp_nums = set()
    for _sec, it in _walk_items(data):
        num = _case_num(it)
        if num and _is_customer_wait_status(it):
            sp_nums.add(num)
    if not sp_nums:
        return
    specs = data.get("todayPlan")
    if isinstance(specs, list):
        kept_specs = []
        for row in specs:
            if not isinstance(row, dict):
                continue
            num = _case_num(row)
            rid = str(row.get("id") or "")
            if (num and num in sp_nums) or any(rid == f"plan-case-{n}" for n in sp_nums):
                continue
            kept_specs.append(row)
        data["todayPlan"] = kept_specs
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(
            r"today['’]?s plan|^calendar$|^day plan|rest of day",
            _section_key(sec.get("title")),
            re.I,
        ):
            continue
        keep = []
        for it in list(sec.get("items") or []):
            if not isinstance(it, dict):
                keep.append(it)
                continue
            if it.get("eventId") or it.get("htmlLink") or it.get("invite") is True:
                keep.append(it)
                continue
            num = _case_num(it)
            rid = str(it.get("id") or "")
            if num in sp_nums and (
                rid.startswith("plan-case-") or rid.startswith("plan-work")
            ):
                continue
            keep.append(it)
        sec["items"] = keep


def _peek_bucket_of(it: dict) -> str:
    peek = it.get("peek") if isinstance(it.get("peek"), dict) else {}
    explicit = str(peek.get("bucket") or it.get("bucket") or "").strip().lower()
    if explicit == "meeting":
        return "meeting"
    if explicit in ("now", "follow", "watch", "quick"):
        return explicit
    return ""


def _ai_num_list(data: dict, *keys: str) -> list[str] | None:
    for key in keys:
        raw = data.get(key)
        if not isinstance(raw, list) or not raw:
            continue
        out = []
        for val in raw:
            if isinstance(val, dict):
                num = re.sub(r"\D", "", str(val.get("caseNumber") or val.get("id") or ""))
            else:
                num = re.sub(r"\D", "", str(val or ""))
            if len(num) >= 6:
                out.append(num)
        if out:
            return out
    return None


def _ranked_case_sections(data: dict) -> dict:
    found = {}
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        if re.search(r"needs (us|you) now", key, re.I):
            found["now"] = sec
        elif re.search(r"follow-up due|needs a touch", key, re.I):
            found["follow"] = sec
        elif re.search(r"still watching|no action needed|^watch\b", key, re.I):
            found["watch"] = sec
        elif re.search(r"customer asked", key, re.I):
            found["meeting"] = sec
        elif re.search(r"quick win", key, re.I):
            found["quick"] = sec
    return found


def apply_ai_case_buckets(data: dict) -> None:
    """Peek ranking is the Needs us now / Follow-up due / Still watching split."""
    if not isinstance(data, dict):
        return
    if data.get("aiAnalyzed") is not True:
        apply_case_holds(data)
        demote_solution_provided_from_now(data)
        return
    found = _ranked_case_sections(data)
    collected = []
    seen: set[str] = set()
    for name in ("now", "follow", "watch", "meeting", "quick"):
        sec = found.get(name)
        if not sec:
            continue
        keep_other = []
        for it in list(sec.get("items") or []):
            if not isinstance(it, dict):
                continue
            if str(it.get("id") or "").startswith("plan-"):
                keep_other.append(it)
                continue
            if not _real_case_row(it, include_done=True):
                keep_other.append(it)
                continue
            num = _case_num(it)
            if not num or num in seen:
                continue
            seen.add(num)
            collected.append((name, it))
        sec["items"] = keep_other
    if not collected:
        apply_case_holds(data)
        demote_solution_provided_from_now(data)
        return
    override: dict[str, str] = {}
    for num in _ai_num_list(data, "needsUsNow") or []:
        override[num] = "now"
    for num in _ai_num_list(data, "followUpDue") or []:
        override.setdefault(num, "follow")
    for num in _ai_num_list(data, "stillWatching") or []:
        override.setdefault(num, "watch")
    for num in _ai_num_list(data, "customerAskedMeeting") or []:
        override.setdefault(num, "meeting")
    for num in _ai_num_list(data, "quickWins") or []:
        override.setdefault(num, "quick")
    buckets: dict[str, list] = {"now": [], "follow": [], "watch": [], "meeting": [], "quick": []}
    close_only = set(_ai_num_list(data, "beforeYouLogOff") or [])
    close_only.update(_ai_num_list(data, "tomorrowFirst") or [])
    for source, it in collected:
        num = _case_num(it)
        if num in close_only:
            _park_close_case(data, it)
            continue
        peek = _peek_bucket_of(it)
        dest = override.get(num) or peek or source
        if dest not in buckets:
            dest = source if source in buckets else ""
        if not dest:
            continue
        buckets[dest].append(it)

    def stamp(it: dict, prefix: str) -> None:
        num = _case_num(it)
        if num:
            it["id"] = f"{prefix}-{num}"
            it["caseNumber"] = num
        it["kind"] = it.get("kind") or "case"

    for it in buckets["now"]:
        stamp(it, "need")
    for it in buckets["follow"]:
        stamp(it, "follow")
        it.pop("when", None)
    for it in buckets["watch"]:
        stamp(it, "watch")
        it.pop("when", None)
    for it in buckets["meeting"]:
        stamp(it, "meet")
    for it in buckets["quick"]:
        stamp(it, "qw")
        it.pop("when", None)

    def drop_sec(sec: dict | None) -> None:
        if not sec:
            return
        data["sections"] = [s for s in (data.get("sections") or []) if s is not sec]

    def put(name: str, title: str, items: list, *, tone=None, open_=True, empty=None) -> None:
        if name == "follow":
            def _activity_ms(it: dict) -> int:
                try:
                    return int(it.get("ourUpdateMs") or 0)
                except (TypeError, ValueError):
                    return 0
            items = sorted(items, key=_activity_ms)
        sec = found.get(name)
        if not items:
            if name in ("meeting", "quick"):
                drop_sec(sec)
                found.pop(name, None)
            else:
                if sec is None:
                    sec = {"title": title, "open": open_, "items": []}
                    sections = data.get("sections") if isinstance(data.get("sections"), list) else []
                    data["sections"] = sections
                    if name == "now":
                        sections.insert(0, sec)
                    elif name == "follow":
                        insert_at = 0
                        for i, row in enumerate(sections):
                            if re.search(
                                r"^mail\b|^slack\b|^gus\b|needs (us|you) now|customer asked",
                                _section_key(row.get("title")),
                                re.I,
                            ):
                                insert_at = i + 1
                        sections.insert(insert_at, sec)
                    else:
                        sections.append(sec)
                    found[name] = sec
                sec["title"] = title
                sec["items"] = []
                sec["open"] = open_
                if tone:
                    sec["tone"] = tone
                if empty:
                    sec["empty"] = empty
                elif name in ("now", "follow"):
                    sec.pop("empty", None)
            return
        if sec is None:
            sec = {"title": title, "open": open_, "items": []}
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            data["sections"] = sections
            insert_at = 0
            if name == "meeting":
                for i, row in enumerate(sections):
                    if re.search(r"needs (us|you) now", _section_key(row.get("title")), re.I):
                        insert_at = i + 1
                        break
            elif name == "quick":
                for i, row in enumerate(sections):
                    if re.search(
                        r"^mail\b|^slack\b|^gus\b|customer asked|needs (us|you) now",
                        _section_key(row.get("title")),
                        re.I,
                    ):
                        insert_at = i + 1
            elif name == "follow":
                for i, row in enumerate(sections):
                    if re.search(
                        r"^mail\b|^slack\b|^gus\b|needs (us|you) now|customer asked",
                        _section_key(row.get("title")),
                        re.I,
                    ):
                        insert_at = i + 1
            elif name == "watch":
                for i, row in enumerate(sections):
                    if re.search(
                        r"follow-up due|needs a touch|needs (us|you) now|^mail\b|^slack\b|^gus\b",
                        _section_key(row.get("title")),
                        re.I,
                    ):
                        insert_at = i + 1
            sections.insert(insert_at, sec)
            found[name] = sec
        sec["title"] = title
        sec["items"] = items
        sec["open"] = open_
        if tone:
            sec["tone"] = tone
        sec.pop("empty", None)

    put("now", "Needs us now", buckets["now"], tone="now", open_=True)
    put("meeting", "Customer asked for a meeting", buckets["meeting"], tone="now", open_=True)
    put("quick", "Quick wins", buckets["quick"], open_=True)
    put("follow", "Follow-up due", buckets["follow"], open_=True)
    put("watch", "Still watching", buckets["watch"], open_=False, empty="No action needed — clear")
    canonicalize_titles(data)
    sort_sections(data)
    data["needYou"] = sum(
        1
        for it in buckets["now"] + buckets["follow"] + buckets["meeting"]
        if it.get("done") is not True
    )
    apply_case_holds(data)
    demote_solution_provided_from_now(data)


def case_holds_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "out" / ".case-holds.json"


def load_case_holds() -> dict[str, dict]:
    path = case_holds_path()
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    blob = rec.get("cases") if isinstance(rec, dict) and isinstance(rec.get("cases"), dict) else rec
    if not isinstance(blob, dict):
        return {}
    out: dict[str, dict] = {}
    for key, spec in blob.items():
        num = re.sub(r"\D", "", str(key))
        if len(num) < 6 or not isinstance(spec, dict):
            continue
        if spec.get("active") is False:
            continue
        dest = str(spec.get("bucket") or "watch").strip().lower()
        if dest not in ("now", "follow", "watch", "meeting", "quick"):
            dest = "watch"
        out[num] = {"bucket": dest}
    return out


def _apply_hold_peek(item: dict, spec: dict) -> None:
    peek = item.get("peek") if isinstance(item.get("peek"), dict) else {}
    bucket = str(spec.get("bucket") or "watch").strip() or "watch"
    peek["bucket"] = bucket
    item["bucket"] = bucket
    item["peek"] = peek
    item.pop("when", None)


def _strip_held_today_plan(data: dict, holds: dict[str, dict]) -> None:
    if not holds:
        return
    rows = data.get("todayPlan")
    if isinstance(rows, list):
        kept = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            num = _case_num(row)
            label = str(row.get("label") or "")
            rid = str(row.get("id") or "")
            if num and num in holds:
                continue
            if any(n and n in label for n in holds):
                continue
            if any(n and n in rid for n in holds):
                continue
            kept.append(row)
        data["todayPlan"] = kept
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(
            r"today['’]?s plan|^calendar$|^day plan|rest of day",
            _section_key(sec.get("title")),
            re.I,
        ):
            continue
        kept_items = []
        for it in list(sec.get("items") or []):
            if not isinstance(it, dict):
                continue
            if not _is_composed_row(it):
                kept_items.append(it)
                continue
            num = _case_num(it)
            label = str(it.get("label") or "")
            rid = str(it.get("id") or "")
            if num and num in holds:
                continue
            if any(n and n in label for n in holds):
                continue
            if any(n and n in rid for n in holds):
                continue
            kept_items.append(it)
        sec["items"] = kept_items


def apply_case_holds(data: dict) -> None:
    """The model ranks. A stored hold file does not move a case."""
    return
    if not isinstance(data, dict):
        return
    holds = load_case_holds()
    if not holds:
        return
    _strip_held_today_plan(data, holds)
    watch_nums = list(_ai_num_list(data, "stillWatching") or [])
    for key in (
        "needsUsNow",
        "followUpDue",
        "quickWins",
        "customerAskedMeeting",
        "beforeYouLogOff",
    ):
        data[key] = [n for n in (_ai_num_list(data, key) or []) if n not in holds]
    for num in holds:
        if num not in watch_nums:
            watch_nums.append(num)
    live = live_owned_case_numbers()
    if live is not None:
        watch_nums = [n for n in watch_nums if n in live]
        holds = {n: spec for n, spec in holds.items() if n in live}
    data["stillWatching"] = watch_nums
    peeks = data.get("peeks") if isinstance(data.get("peeks"), dict) else {}
    for num, spec in holds.items():
        peek = peeks.get(num) if isinstance(peeks.get(num), dict) else {}
        peek["bucket"] = str(spec.get("bucket") or "watch")
        peeks[num] = peek
    if peeks:
        data["peeks"] = peeks
    found = _ranked_case_sections(data)
    moved: list[dict] = []
    seen: set[str] = set()
    for name in ("now", "follow", "meeting", "quick"):
        sec = found.get(name)
        if not sec:
            continue
        keep = []
        for it in list(sec.get("items") or []):
            if not isinstance(it, dict):
                keep.append(it)
                continue
            num = _case_num(it)
            if num and num in holds:
                if num not in seen:
                    _apply_hold_peek(it, holds[num])
                    it["id"] = f"watch-{num}"
                    it["caseNumber"] = num
                    it.pop("when", None)
                    moved.append(it)
                    seen.add(num)
                continue
            keep.append(it)
        sec["items"] = keep
        if not keep and name in ("follow", "meeting", "quick"):
            data["sections"] = [s for s in (data.get("sections") or []) if s is not sec]
            found.pop(name, None)
    watch = found.get("watch")
    if watch is None:
        watch = {
            "title": "Still watching",
            "open": False,
            "items": [],
            "empty": "No action needed — clear",
        }
        sections = data.get("sections") if isinstance(data.get("sections"), list) else []
        data["sections"] = sections
        insert_at = 0
        for i, row in enumerate(sections):
            if not isinstance(row, dict):
                continue
            if re.search(
                r"follow-up due|needs a touch|needs (us|you) now|^mail\b|^slack\b|^gus\b",
                _section_key(row.get("title")),
                re.I,
            ):
                insert_at = i + 1
        sections.insert(insert_at, watch)
        found["watch"] = watch
    existing = []
    for it in list(watch.get("items") or []):
        if not isinstance(it, dict):
            continue
        num = _case_num(it)
        if num and num in seen:
            continue
        if num and num in holds:
            _apply_hold_peek(it, holds[num])
            it["id"] = f"watch-{num}"
            it["caseNumber"] = num
            it.pop("when", None)
            seen.add(num)
        existing.append(it)
    watch["items"] = existing + moved
    watch["title"] = "Still watching"
    watch["open"] = False
    if watch["items"]:
        watch.pop("empty", None)
    else:
        watch["empty"] = "No action needed — clear"
    now_sec = found.get("now") or {}
    follow_sec = found.get("follow") or {}
    meet_sec = found.get("meeting") or {}
    data["needYou"] = sum(
        1
        for it in list(now_sec.get("items") or [])
        + list(follow_sec.get("items") or [])
        + list(meet_sec.get("items") or [])
        if isinstance(it, dict) and it.get("done") is not True and _real_case_row(it)
    )
    canonicalize_titles(data)
    sort_sections(data)


def apply_ai_closeout(data: dict) -> None:
    """Before you log off is the model's list. Omit the heading when that list is empty."""
    if not isinstance(data, dict) or data.get("aiAnalyzed") is not True:
        return
    if "beforeYouLogOff" not in data:
        return
    rows = data.get("beforeYouLogOff")
    if not isinstance(rows, list):
        return

    def drop_logoff() -> None:
        data["sections"] = [
            s
            for s in (data.get("sections") or [])
            if not (isinstance(s, dict) and LOGOFF_SEC_RE.search(_section_key(s.get("title"))))
        ]

    items = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            num = _case_num(row) or re.sub(r"\D", "", str(row.get("id") or ""))
            src = _close_case_source(data, num) if len(num) >= 6 else None
            it = dict(src) if src else dict(row)
            for key in ("label", "detail", "when", "promisedClose", "promisedCloseOn"):
                if row.get(key) in (None, ""):
                    continue
                if key == "label" and _case_row_rich(it) and not _case_row_rich(row):
                    continue
                it[key] = row[key]
        else:
            num = re.sub(r"\D", "", str(row or ""))
            it = dict(_close_case_source(data, num) or {})
            if not it and len(num) >= 6:
                it = {"id": num, "label": f"#{num}", "kind": "case", "caseNumber": num}
        if not it:
            continue
        num = _case_num(it) or str(it.get("id") or "")
        if not num or num in seen:
            continue
        seen.add(num)
        label = str(it.get("label") or "")
        if re.search(r"before you log off", label, re.I):
            it["label"] = f"#{num}" if re.fullmatch(r"\d{6,}", str(num)) else label
        for key in ("startStamp", "endStamp", "composed", "durationAdjustable", "timed", "durationMinutes"):
            it.pop(key, None)
        it["promisedClose"] = True
        items.append(it)
    if not items:
        drop_logoff()
        return
    sec = _logoff_section(data)
    sec["title"] = "Before you log off"
    sec["open"] = True
    sec["items"] = items
    sec.pop("empty", None)


def _closeout_item_key(it: dict) -> str:
    num = _case_num(it)
    if num:
        return "case:" + num
    label = re.sub(r"\s+", " ", str(it.get("label") or "")).strip().lower()
    if label:
        return "label:" + label
    return "id:" + str(it.get("id") or "")


def _unique_closeout_items(rows: list) -> list:
    out = []
    seen: set[str] = set()
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        key = _closeout_item_key(it)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _drop_case_from_other_buckets(data: dict, nums: set[str]) -> None:
    """A case on Before you log off or Tomorrow is not also Follow-up due or Still watching."""
    if not nums:
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        if LOGOFF_SEC_RE.search(key) or TOMORROW_SEC_RE.search(key):
            continue
        if not re.search(
            r"needs (us|you) now|follow-up due|needs a touch|still watching|no action|quick win|customer asked",
            key,
            re.I,
        ):
            continue
        sec["items"] = [
            it
            for it in (sec.get("items") or [])
            if not (isinstance(it, dict) and _case_num(it) in nums)
        ]


def dedupe_closeout_sections(data: dict) -> None:
    """One case in one close-out list. Before you log off wins over Tomorrow."""
    if not isinstance(data, dict):
        return
    logoff = None
    tomorrow = None
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        if LOGOFF_SEC_RE.search(key):
            logoff = sec
        elif TOMORROW_SEC_RE.search(key):
            tomorrow = sec
    log_keys: set[str] = set()
    if logoff is not None:
        items = _unique_closeout_items(list(logoff.get("items") or []))
        logoff["items"] = items
        logoff["groups"] = []
        log_keys = {_closeout_item_key(it) for it in items}
        _drop_case_from_other_buckets(data, {_case_num(it) for it in items if _case_num(it)})
    if tomorrow is None:
        return
    tomorrow_items = []
    for it in _unique_closeout_items(list(tomorrow.get("items") or [])):
        if _closeout_item_key(it) in log_keys:
            continue
        tomorrow_items.append(it)
    tomorrow["items"] = tomorrow_items
    tomorrow["groups"] = []
    _drop_case_from_other_buckets(data, {_case_num(it) for it in tomorrow_items if _case_num(it)})


def apply_ai_tomorrow(data: dict) -> None:
    """Tomorrow, first thing is the model's list."""
    if not isinstance(data, dict) or data.get("aiAnalyzed") is not True:
        return
    if "tomorrowFirst" not in data:
        return
    rows = data.get("tomorrowFirst")
    if not isinstance(rows, list):
        return
    items = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            num = _case_num(row) or re.sub(r"\D", "", str(row.get("id") or ""))
            src = _close_case_source(data, num) if len(num) >= 6 else None
            it = dict(src) if src else dict(row)
            for key in ("label", "detail", "when", "promisedClose", "promisedCloseOn"):
                if row.get(key) in (None, ""):
                    continue
                if key == "label" and _case_row_rich(it) and not _case_row_rich(row):
                    continue
                it[key] = row[key]
        else:
            num = re.sub(r"\D", "", str(row or ""))
            it = dict(_close_case_source(data, num) or {})
            if not it and len(num) >= 6:
                it = {"id": num, "label": f"#{num}", "kind": "case", "caseNumber": num}
        if not it:
            continue
        num = _case_num(it)
        key = num or str(it.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(it)
    sec = _tomorrow_section(data)
    sec["title"] = "Tomorrow, first thing"
    sec["open"] = True
    sec["items"] = items
    if items:
        sec.pop("empty", None)


def _overview_beat(text: str) -> str:
    """Chronology is a timeline overview, not a pasted comment/email body."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= 140:
        return text
    cut = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if not cut or len(cut) > 120:
        cut = text[:117].rstrip() + "…"
    return cut


def normalize_chronology(data: dict) -> None:
    for _sec, it in _walk_items(data):
        chrono = it.get("chronology")
        if not isinstance(chrono, list):
            continue
        out = []
        for ev in chrono:
            if isinstance(ev, str) and ev.strip():
                text = _overview_beat(ev)
                tm = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", text, re.I)
                out.append({
                    "when": tm.group(1) if tm else "",
                    "who": "",
                    "kind": "",
                    "text": text,
                })
            elif isinstance(ev, dict):
                beat = dict(ev)
                beat["text"] = _overview_beat(beat.get("text") or beat.get("body") or "")
                if beat.get("text"):
                    out.append(beat)
        out.sort(key=lambda ev: _parse_update_ms(str(ev.get("_ts") or "")) or 0)
        it["chronology"] = out
        if isinstance(it.get("peek"), dict):
            it["peek"]["chronology"] = out


def _listed_case_numbers(it: dict) -> list[str]:
    nums: list[str] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        digits = re.sub(r"\D", "", str(raw or ""))
        if len(digits) >= 6 and digits not in seen:
            seen.add(digits)
            nums.append(digits)

    add(it.get("caseNumber"))
    for key in ("caseNumbers", "cases"):
        raw = it.get(key)
        if isinstance(raw, list):
            for x in raw:
                if isinstance(x, dict):
                    add(x.get("caseNumber") or x.get("Id") or x.get("id"))
                else:
                    add(x)
    blob = str(it.get("label") or "") + " " + str(it.get("detail") or "")
    for m in re.finditer(r"\b(\d{8,})\b", blob):
        add(m.group(1))
    return nums


def _is_case_bunch(it: dict) -> bool:
    blob = str(it.get("label") or "") + " " + str(it.get("detail") or "")
    if re.search(r"\bbatch\b|\b\d+\s+cases\b", blob, re.I):
        return True
    return len(_listed_case_numbers(it)) > 1


def split_bunched_cases(data: dict) -> None:
    """Follow-up due / Still watching: one case per row, never a bunched count."""
    if not isinstance(data, dict):
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(
            r"follow-up due|needs a touch|still watching|^watch\b|no action",
            _section_key(sec.get("title")),
            re.I,
        ):
            continue

        def explode(rows: list) -> list:
            out = []
            for it in rows or []:
                if not isinstance(it, dict):
                    continue
                nums = _listed_case_numbers(it)
                if not _is_case_bunch(it) or len(nums) <= 1:
                    if nums and not it.get("caseNumber"):
                        it["caseNumber"] = nums[0]
                    out.append(it)
                    continue
                for n in nums:
                    row = {
                        k: v
                        for k, v in it.items()
                        if k not in ("id", "label", "caseNumber", "caseNumbers", "cases", "done", "doneIds")
                    }
                    row["id"] = "case-" + n
                    row["kind"] = "case"
                    row["caseNumber"] = n
                    row["asTask"] = True
                    lab = str(it.get("label") or "")
                    lab = re.sub(r"\bbatch\b.*", "", lab, flags=re.I).strip(" —-")
                    if not re.search(rf"\b{n}\b", lab):
                        lab = f"Case {n}" + (f" — {lab}" if lab else "")
                    row["label"] = lab or f"Case {n}"
                    out.append(row)
            return out

        sec["items"] = explode(sec.get("items") or [])
        for grp in sec.get("groups") or []:
            if isinstance(grp, dict):
                grp["items"] = explode(grp.get("items") or [])


def ensure_required_sections(data: dict) -> None:
    secs = [s for s in (data.get("sections") or []) if isinstance(s, dict)]
    have = {_section_key(s.get("title")) for s in secs}

    def add(title, empty):
        row = {"title": title, "open": True, "items": []}
        if empty:
            row["empty"] = empty
        secs.append(row)

    if not any(re.search(r"^slack\b", h) for h in have):
        add("Slack", None if data.get("slackFetchOk") is False else "Slack — clear")
    if not any(re.search(r"^(mail|email|gmail)\b", h) for h in have):
        add("Mail", None if data.get("mailFetchOk") is False else "Mail — clear")
    if not any(re.search(r"^gus\b", h) for h in have):
        add("GUS", None if data.get("gusFetchOk") is False else "GUS — clear")
    if not any(re.search(r"needs a touch|follow[- ]up due", h) for h in have):
        secs.append({"title": "Follow-up due", "open": True, "items": []})
    if not any(re.search(r"needs (us|you) now", h) for h in have):
        secs.insert(0, {"title": "Needs us now", "open": True, "items": [], "tone": "now"})
    if not any(re.search(r"still watching|^watch\b|no action", h) for h in have):
        add("Still watching", "No action needed — clear")
    else:
        for sec in secs:
            if re.search(r"still watching|^watch\b|no action", _section_key(sec.get("title"))):
                items = sec.get("items") or []
                groups = [g for g in (sec.get("groups") or []) if isinstance(g, dict) and (g.get("items") or [])]
                if not items and not groups:
                    sec["empty"] = sec.get("empty") or "No action needed — clear"
    for sec in secs:
        if not isinstance(sec, dict) or not re.search(r"^gus\b", _section_key(sec.get("title"))):
            continue
        items = [it for it in (sec.get("items") or []) if isinstance(it, dict)]
        groups = [g for g in (sec.get("groups") or []) if isinstance(g, dict) and (g.get("items") or [])]
        if items or groups:
            sec.pop("empty", None)
        elif data.get("gusFetchOk") is False:
            sec.pop("empty", None)
        else:
            sec["empty"] = sec.get("empty") or "GUS — clear"
    if not any(re.search(r"today['’]?s plan|^calendar$|^day plan", h) for h in have):
        add("Today's plan", "No timed meetings on the clock.")
    data["sections"] = secs


def _notepad_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "out" / ".notepad.json"


def load_notepad_items() -> list[dict]:
    try:
        raw = json.loads(_notepad_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        nid = str(row.get("id") or "").strip() or ("note-" + str(abs(hash(text)) % 10**12))
        out.append({"id": nid[:40], "text": text[:240], "checked": row.get("checked") is True})
    return out[:80]


def save_notepad_items(items: list) -> list[dict]:
    clean = []
    for row in items or []:
        if isinstance(row, dict):
            text = str(row.get("text") or "").strip()
            nid = str(row.get("id") or "").strip()
            checked = row.get("checked") is True
        else:
            text = str(row or "").strip()
            nid = ""
            checked = False
        if not text:
            continue
        if not nid:
            nid = "note-" + str(abs(hash(text + str(len(clean)))) % 10**12)
        clean.append({"id": nid[:40], "text": text[:240], "checked": checked})
    clean = clean[:80]
    path = _notepad_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"items": clean}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return clean
    return clean


def apply_notepad(data: dict) -> None:
    """Drop checked lines on publish. The list stays in the notepad, not on the page."""
    if not isinstance(data, dict):
        return
    items = load_notepad_items()
    kept = [row for row in items if not row.get("checked")]
    if len(kept) != len(items):
        save_notepad_items(kept)
    data.pop("notepad", None)
    data["sections"] = [
        sec
        for sec in (data.get("sections") or [])
        if isinstance(sec, dict) and not re.match(r"^notepad\b", _section_key(sec.get("title")), re.I)
    ]


def drop_empty_optional_sections(data: dict) -> None:
    """Quick wins / Customer asked / Before you log off paint only when they have rows."""
    if not isinstance(data, dict):
        return
    out = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        key = _section_key(sec.get("title"))
        if re.search(r"quick win|customer asked|before you log off", key):
            items = [it for it in (sec.get("items") or []) if isinstance(it, dict)]
            groups = [
                g
                for g in (sec.get("groups") or [])
                if isinstance(g, dict) and (g.get("items") or [])
            ]
            if not items and not groups:
                continue
            sec.pop("empty", None)
        out.append(sec)
    data["sections"] = out


def stamp_clock(data: dict) -> None:
    normalize_shift_hhmm(data)
    if os.environ.get("DAY_PLANNER_EMPTY") == "1":
        data.pop("stamp", None)
        data.pop("generatedAt", None)
        return
    incoming = str(data.get("timezone") or "").strip()
    tzname = incoming
    tz = zoneinfo_or_local(incoming)
    now = datetime.now(tz) if tz is not None else datetime.now()
    if incoming:
        data["timezone"] = incoming
        if not str(data.get("timezoneShort") or "").strip():
            if incoming in {"Asia/Kolkata", "Asia/Calcutta", "Asia/Colombo"}:
                data["timezoneShort"] = "IST"
            else:
                short = fold_tz_abbr(now.tzname() or "")
                if short:
                    data["timezoneShort"] = short
    h12 = now.hour % 12 or 12
    ap = "AM" if now.hour < 12 else "PM"
    data["stamp"] = f"{now.strftime('%A')}, {now.strftime('%b')} {now.day} · {h12}:{now.minute:02d} {ap}"
    data["generatedAt"] = now.strftime("%Y%m%dT%H%M%S")
    sh, sm = _hm(data.get("shiftStart") or "08:00")
    eh, em = _hm(data.get("shiftEnd") or "17:00", (17, 0))
    if not incoming:
        return
    mins = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if end <= start:
        in_shift = mins >= start or mins < end
        sod_until = start + 180
        if mins >= start and mins < sod_until:
            part = "sod"
        elif in_shift and (mins >= start or mins < end - 150):
            part = "mid"
        else:
            part = "eod"
    else:
        if mins < start + 180:
            part = "sod"
        elif mins <= end - 150:
            part = "mid"
        else:
            part = "eod"
    data["daypart"] = part
    data["daypartLabel"] = {"sod": "Start of Day", "mid": "Mid-Day", "eod": "End of Day"}[part]


def inject_facts(data: dict) -> None:
    """Shift = login–logout. Assembled schedule = today's Assembled blocks only."""
    facts = [str(f).strip() for f in (data.get("facts") or []) if str(f).strip()]
    facts = [
        f
        for f in facts
        if not re.search(r"assembled schedule|^working day\b|^Shift\b", f, re.I)
    ]
    short = fold_tz_abbr(data.get("timezoneShort") or "")
    ss = str(data.get("shiftStart") or "").strip()
    se = str(data.get("shiftEnd") or "").strip()
    live = data.get("assembledFromCalendar") is True
    hours = fold_tz_in_text(str(data.get("assembledSchedule") or "").strip())
    if hours.lower() == "nothing scheduled":
        hours = ""
    if live and hours:
        assembled = hours
        data["assembledSchedule"] = hours
    else:
        assembled = "Nothing Scheduled"
    if ss and se:
        shift_hours = f"{_ampm(ss)}–{_ampm(se)}" + (f" {short}" if short else "")
        facts.insert(0, f"Shift: {shift_hours}")
    facts.append(f"Assembled schedule: {assembled}")
    data["facts"] = facts


IDENTITY_KEYS = ("name", "title", "manager")


def identity_blob(src: object) -> dict:
    if not isinstance(src, dict):
        return {}
    out = {}
    for key in IDENTITY_KEYS:
        val = str(src.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def fill_identity(data: dict, *sources: object) -> None:
    """Stamp Name / Title / Manager from OrgCS or the last good page. Never invent."""
    if not isinstance(data, dict):
        return
    for src in sources:
        blob = identity_blob(src)
        for key, val in blob.items():
            if not str(data.get(key) or "").strip():
                data[key] = val


def _compact_dt(stamp: object):
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$", str(stamp or "").strip())
    if not m:
        return None
    try:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )
    except ValueError:
        return None


def _inbox_rows(sec: dict) -> list:
    rows = []
    if not isinstance(sec, dict):
        return rows
    for it in sec.get("items") or []:
        if isinstance(it, dict):
            rows.append(it)
    for grp in sec.get("groups") or []:
        if not isinstance(grp, dict):
            continue
        for it in grp.get("items") or []:
            if isinstance(it, dict):
                rows.append(it)
    return rows


def restore_last_good_inbox(data: dict, prev: dict | None) -> None:
    """If this publish emptied Slack/Mail minutes after a good same-day page, keep the good rows.

    Covers the hoist-crash recovery that used to stamp Slack — clear / Mail — clear.
    A later gather the same day (outside 45 minutes) can still publish a true clear.
    Never overwrite a run the model already classified for an inbox that actually fetched.
    """
    if not isinstance(data, dict) or not isinstance(prev, dict):
        return
    if os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return
    now = _compact_dt(data.get("generatedAt"))
    old = _compact_dt(prev.get("generatedAt"))
    if not now or not old:
        return
    if abs((now - old).total_seconds()) > 45 * 60:
        return

    def title_pred(kind: str):
        if kind == "slack":
            return lambda k: bool(re.match(r"slack\b", k, re.I))
        return lambda k: bool(re.match(r"(mail|email|gmail)\b", k, re.I))

    for kind, fetch_key in (("slack", "slackFetchOk"), ("mail", "mailFetchOk")):
        pred = title_pred(kind)
        if data.get("inboxReviewed") is True and data.get(fetch_key) is not False:
            continue
        new_sec = next((s for s in (data.get("sections") or []) if isinstance(s, dict) and pred(_section_key(s.get("title")))), None)
        old_sec = next((s for s in (prev.get("sections") or []) if isinstance(s, dict) and pred(_section_key(s.get("title")))), None)
        if not new_sec or not old_sec:
            continue
        if _inbox_rows(new_sec):
            continue
        if not _inbox_rows(old_sec):
            continue
        groups, items = _inbox_without_done(old_sec, collect_done_keys(data) | collect_done_keys(prev) | disk_done_keys())
        if not groups and not items:
            continue
        new_sec["groups"] = groups
        new_sec["items"] = items
        new_sec.pop("empty", None)


def _load_planner_gather() -> dict:
    try:
        loaded = json.loads(pathlib.Path("/tmp/planner-gather.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_human_slack_leftover(row: object) -> bool:
    if not isinstance(row, dict) or row.get("done") is True or row.get("gusBot") is True:
        return False
    if row.get("lastHumanIsMe") is True:
        return False
    if row.get("lastHumanIsMe") is False:
        return True
    return bool(row.get("peer"))


def refuse_false_inbox_clear(data: dict) -> None:
    """Do not publish Slack/Mail — clear while this run still has leftover humans."""
    if not isinstance(data, dict) or os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return
    if data.get("inboxReviewed") is True:
        return
    gather = _load_planner_gather()
    slack_cand = []
    for src in (data.get("slackCandidates"), gather.get("slackCandidates")):
        if isinstance(src, list):
            slack_cand = src
            break
    leftovers = [row for row in slack_cand if _is_human_slack_leftover(row)]
    if not leftovers:
        return
    sec = next(
        (
            s
            for s in (data.get("sections") or [])
            if isinstance(s, dict) and re.match(r"slack\b", _section_key(s.get("title")), re.I)
        ),
        None,
    )
    if sec is None:
        sec = {"title": "Slack", "open": True, "groups": [], "items": [], "asTask": True}
        data.setdefault("sections", []).insert(1, sec)
    if _inbox_rows(sec):
        return
    unread, reply = [], []
    for row in leftovers:
        item = dict(row)
        item["kind"] = "slack"
        if item.get("unread") is True:
            item["slackBucket"] = "unread"
            unread.append(item)
        else:
            item["slackBucket"] = "reply"
            reply.append(item)
    groups = []
    if unread:
        groups.append({"title": "Not opened", "items": unread})
    if reply:
        groups.append({"title": "Needs a reply", "items": reply})
    if not groups:
        return
    sec["groups"] = groups
    sec["items"] = []
    sec.pop("empty", None)


_OURS_WHO_RE = re.compile(r"salesforce support response", re.I)
_UPDATE_MARK_RE = re.compile(r"\b(?:Last update|Last activity|No update yet|New case)\b[^·]*", re.I)


def _activity_by_number():
    try:
        loaded = json.loads(pathlib.Path("/tmp/case-activity.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    nums = loaded.get("byNumber")
    return nums if isinstance(nums, dict) else None


def _parse_update_ms(ts: str) -> int | None:
    text = str(ts or "").strip()
    if not text:
        return None
    text = text.replace(".000+0000", "+00:00").replace("+0000", "+00:00").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _our_update_info(events: list, engineer: str) -> tuple[str, int | None]:
    rows = [ev for ev in events or [] if isinstance(ev, dict)]
    if not rows:
        return "new", None
    name = str(engineer or "").strip().lower()

    def ours(ev: dict) -> bool:
        if ev.get("kind") == "customer" or ev.get("ours") is False:
            return False
        if ev.get("ours") is True:
            return True
        who = str(ev.get("who") or "")
        if _OURS_WHO_RE.search(who):
            return True
        return bool(name) and who.strip().lower() == name

    hits = [ev for ev in rows if ours(ev)]
    if not hits:
        return "none", None
    hits.sort(key=lambda ev: str(ev.get("_ts") or ""), reverse=True)
    return "ts", _parse_update_ms(str(hits[0].get("_ts") or ""))


def stamp_case_our_updates(data: dict) -> None:
    """Store the newest comment, email, or note as UTC milliseconds. The page prints it in the selected zone."""
    if not isinstance(data, dict):
        return
    by_num = _activity_by_number() or {}

    def newest_ms(events: list) -> int | None:
        best = 0
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            ms = _parse_update_ms(str(ev.get("_ts") or "")) or 0
            if ms > best:
                best = ms
        return best or None

    def touch(it: dict) -> None:
        kind = str(it.get("kind") or "")
        num = str(it.get("caseNumber") or "").strip()
        if not num.isdigit() and kind == "case":
            match = re.search(r"\b(\d{6,})\b", str(it.get("id") or ""))
            num = match.group(1) if match else ""
        if not num.isdigit() or (kind and kind != "case" and not it.get("caseNumber")):
            return
        detail = _UPDATE_MARK_RE.sub("", str(it.get("detail") or ""))
        detail = re.sub(r"(?:\s*·\s*){2,}", " · ", detail).strip(" ·")
        it["detail"] = detail
        events = list(by_num.get(num) or [])
        if not events:
            chrono = it.get("chronology") if isinstance(it.get("chronology"), list) else []
            events = chrono
        if not events:
            it["ourUpdate"] = "new"
            it.pop("ourUpdateMs", None)
            return
        ms = newest_ms(events)
        if ms:
            it["ourUpdate"] = "ts"
            it["ourUpdateMs"] = ms
        else:
            it["ourUpdate"] = "none"
            it.pop("ourUpdateMs", None)

    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for it in sec.get("items") or []:
            if isinstance(it, dict):
                touch(it)
        for grp in sec.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            for it in grp.get("items") or []:
                if isinstance(it, dict):
                    touch(it)


_GUS_NOTICE_RE = re.compile(
    r"gus chatter|work notifier|\bgus bot\b|chatter feed of this work",
    re.I,
)


def is_gus_notice(row: object) -> bool:
    """A GUS Bot or GUS Chatter post. It belongs on the GUS card, not Slack."""
    if not isinstance(row, dict):
        return False
    if row.get("gusBot") is True:
        return True
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("label", "detail", "from", "peer", "snippet", "openedClip", "update")
    )
    return bool(_GUS_NOTICE_RE.search(blob))


def drop_gus_notices_from_slack(data: dict) -> None:
    """GUS Chatter and GUS Bot stay on the GUS card. They do not also sit in Slack."""
    if not isinstance(data, dict):
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.match(r"^slack\b", _section_key(sec.get("title")), re.I):
            continue
        sec["items"] = [it for it in (sec.get("items") or []) if not is_gus_notice(it)]
        kept = []
        for group in sec.get("groups") or []:
            if not isinstance(group, dict):
                continue
            group["items"] = [it for it in (group.get("items") or []) if not is_gus_notice(it)]
            if group["items"]:
                kept.append(group)
        sec["groups"] = kept


def _plain_gus_text(text: str) -> str:
    """Slack dumps, literal \\n, and non-breaking spaces become one readable line."""
    s = str(text or "")
    s = s.replace("\\/", "/")
    s = re.sub(r"\\n", "\n", s)
    s = re.sub(r"\\u00a0", " ", s, flags=re.I)
    s = s.replace("\u00a0", " ").replace("&nbsp;", " ").replace("\xa0", " ")
    s = re.sub(r"<https?://[^|>\s]+\|([^>]+)>", r"\1", s, flags=re.I)
    s = re.sub(r"<https?://[^>]+>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -–—")


def _gus_notice_copy(note: str, who: str = "") -> tuple[str, str, str]:
    """(label, detail, gus url). Label is the bot. Detail is one sentence plus the case number."""
    original = str(note or "").replace("\\/", "/")
    links = re.findall(r"https://gus\.(?:my|lightning)\.salesforce\.com/[A-Za-z0-9]+", original, re.I)
    work_links = [url for url in links if re.search(r"/a07", url, re.I)]
    gus_url = (work_links or links or [""])[0]
    raw = _plain_gus_text(original)
    blob = f"{who} {raw}"
    source = "GUS Chatter" if re.search(r"gus chatter|chatter feed", blob, re.I) else "GUS Bot"
    work = re.search(r"\bW-\d+\b", raw)
    case = re.search(r"OrgCS Case No\.?\s*#?\s*(\d{5,})", raw, re.I)
    said = re.search(r"\bsaid:\s*(.+?)(?:\s+Case Details\b|$)", raw, re.I)
    sentence = ""
    if said:
        sentence = re.split(r"(?<=\.)\s", said.group(1).strip())[0].strip(" .")
    elif raw and not re.fullmatch(r"gus (chatter|bot)|work notifier", raw, re.I):
        sentence = re.split(r"(?<=\.)\s", raw)[0].strip(" .")
        sentence = re.sub(r"^(GUS Chatter|GUS Bot|Work Notifier)\s*[-–—:]\s*", "", sentence, flags=re.I)
    label = f"{source} · {work.group(0)}" if work else source
    bits = []
    if sentence:
        bits.append(sentence if sentence.endswith(".") else sentence + ".")
    if case:
        bits.append(f"OrgCS #{case.group(1)}.")
    detail = " ".join(bits) or source
    return label[:140], detail[:220], gus_url


def _gus_slack_url(row: dict) -> str:
    for key in ("slackUrl", "slackPermalink", "permalink"):
        url = str(row.get(key) or "").strip()
        if "slack.com" in url.lower():
            return url.replace("\\/", "/")
    cid = str(row.get("channelId") or "")
    ts = str(row.get("ts") or row.get("threadTs") or "")
    if re.match(r"^[DGC][A-Z0-9]+$", cid, re.I):
        if re.match(r"^\d{10}\.\d+$", ts):
            return f"https://salesforce.enterprise.slack.com/archives/{cid}/p{ts.replace('.', '')}"
        return f"https://salesforce.enterprise.slack.com/archives/{cid}"
    return ""


def ensure_gus_bot_rows(data: dict) -> None:
    """Every GUS Bot Work Notifier post is a GUS row, even with no Support Contact or Follow."""
    if not isinstance(data, dict):
        return
    gather = _load_planner_gather()
    inbox_slack: list = []
    try:
        inbox = json.loads(pathlib.Path("/tmp/planner-inbox.json").read_text(encoding="utf-8"))
        if isinstance(inbox, dict) and isinstance(inbox.get("slack"), list):
            inbox_slack = inbox["slack"]
    except (OSError, json.JSONDecodeError):
        inbox_slack = []
    rows: list[dict] = []
    seen: set[str] = set()
    for src in (inbox_slack, gather.get("slackCandidates"), data.get("slackCandidates")):
        if not isinstance(src, list):
            continue
        for row in src:
            if not isinstance(row, dict):
                continue
            blob = " ".join(
                str(row.get(key) or "")
                for key in ("label", "channel", "from", "peer", "detail", "snippet", "openedClip")
            )
            if row.get("gusBot") is not True and not re.search(
                r"work notifier|gus bot|gus chatter|chatter feed", blob, re.I
            ):
                continue
            ident = str(row.get("id") or row.get("ts") or row.get("slackUrl") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            rows.append(row)
    mails = _gus_notice_mails(data, gather)
    if not rows and not mails:
        return
    sec = next(
        (
            s
            for s in (data.get("sections") or [])
            if isinstance(s, dict) and re.match(r"^gus\b", _section_key(s.get("title")), re.I)
        ),
        None,
    )
    if sec is None:
        sec = {"title": "GUS", "open": True, "items": []}
        data.setdefault("sections", []).append(sec)
    have = {str(it.get("id") or "") for it in (sec.get("items") or []) if isinstance(it, dict)}
    items = list(sec.get("items") or [])
    for it in items:
        if not isinstance(it, dict):
            continue
        blob = " ".join(str(it.get(key) or "") for key in ("label", "detail", "update", "from"))
        if it.get("gusBot") is not True and not re.search(
            r"gus chatter|gus bot|work notifier|\\n|\\u00a0", blob, re.I
        ):
            continue
        label, detail, gus_url = _gus_notice_copy(blob, str(it.get("from") or ""))
        it["label"] = label
        it["detail"] = detail
        it["gusBot"] = True
        it.pop("update", None)
        slack_url = _gus_slack_url(it)
        if slack_url:
            it["slackUrl"] = slack_url
        if gus_url and not it.get("gusUrl"):
            it["gusUrl"] = gus_url
    for row in rows:
        ident = "gusbot-" + re.sub(r"[^A-Za-z0-9._:-]", "", str(row.get("id") or row.get("ts") or ""))[:48]
        if ident in have:
            continue
        note = " ".join(
            str(row.get(key) or "")
            for key in ("openedClip", "snippet", "label", "detail", "update")
        )
        label, detail, gus_url = _gus_notice_copy(note, str(row.get("from") or ""))
        slack_url = _gus_slack_url(row)
        twin = next(
            (
                it
                for it in items
                if isinstance(it, dict) and (it.get("detail") == detail or it.get("label") == label)
            ),
            None,
        )
        if twin is not None:
            if slack_url:
                twin["slackUrl"] = slack_url
            if gus_url and not twin.get("gusUrl"):
                twin["gusUrl"] = gus_url
            if "W-" in label and "W-" not in str(twin.get("label") or ""):
                twin["label"] = label
            twin["gusBot"] = True
            continue
        items.append(
            {
                "id": ident or "gusbot",
                "kind": "gus",
                "label": label,
                "detail": detail,
                "gusBot": True,
                "slackUrl": slack_url,
                "gusUrl": gus_url,
            }
        )
        have.add(ident)
    _attach_gus_notice_mail(items, mails)
    _drop_gus_notice_mail(data, mails)
    sec["items"] = items
    if items:
        sec.pop("empty", None)


def _notice_ids(text: str) -> tuple[set[str], set[str]]:
    raw = _plain_gus_text(text)
    works = set(re.findall(r"W-\d+", raw, re.I))
    cases = set(re.findall(r"(?:OrgCS(?:\s+Case)?(?:\s+No\.?)?\s*#?\s*)(\d{6,})", raw, re.I))
    return works, cases


def _is_gus_notice_mail(row: dict) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("from", "label", "detail", "snippet", "openedClip", "subject")
    )
    if re.search(r"daily digest", blob, re.I):
        return False
    if not re.search(r"gus-chatter-notifications|gus chatter", blob, re.I):
        return False
    works, cases = _notice_ids(blob)
    return bool(works or cases or re.search(r"mentioned you", blob, re.I))


def _gus_mail_url(row: dict) -> str:
    for key in ("mailUrl", "gmailUrl", "messageUrl"):
        url = str(row.get(key) or "").strip()
        if "mail.google.com" in url.lower() or "gmail.com" in url.lower():
            return url
    mid = str(row.get("messageId") or "")
    if not mid:
        found = re.search(
            r"#all/([0-9a-f]{10,})",
            " ".join(str(row.get(k) or "") for k in ("id", "snippet", "openedClip")),
            re.I,
        )
        mid = found.group(1) if found else ""
    if mid:
        return f"https://mail.google.com/mail/u/0/#all/{mid}"
    return ""


def _gus_notice_mails(data: dict, gather: dict) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    pools: list = []
    for src in (gather.get("mailCandidates"), data.get("mailCandidates")):
        if isinstance(src, list):
            pools.extend(src)
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not re.match(r"^(mail|email|gmail)\b", _section_key(sec.get("title")), re.I):
            continue
        pools.extend(sec.get("items") or [])
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                pools.extend(group.get("items") or [])
    for row in pools:
        if not isinstance(row, dict) or not _is_gus_notice_mail(row):
            continue
        ident = str(row.get("id") or row.get("messageId") or _gus_mail_url(row))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        found.append(row)
    return found


def _attach_gus_notice_mail(items: list, mails: list[dict]) -> None:
    for mail in mails:
        blob = " ".join(str(mail.get(key) or "") for key in ("label", "detail", "snippet", "openedClip", "from"))
        works, cases = _notice_ids(blob)
        url = _gus_mail_url(mail)
        if not url:
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            have_w, have_c = _notice_ids(f"{it.get('label') or ''} {it.get('detail') or ''}")
            if (works and works & have_w) or (cases and cases & have_c):
                it["mailUrl"] = url
                break


def _drop_gus_notice_mail(data: dict, mails: list[dict]) -> None:
    drop_ids = {str(row.get("id") or "") for row in mails if row.get("id")}
    if not drop_ids:
        return
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not re.match(r"^(mail|email|gmail)\b", _section_key(sec.get("title")), re.I):
            continue
        sec["items"] = [
            it
            for it in (sec.get("items") or [])
            if str(it.get("id") or "") not in drop_ids and not _is_gus_notice_mail(it)
        ]
        kept = []
        for group in sec.get("groups") or []:
            if not isinstance(group, dict):
                continue
            group["items"] = [
                it
                for it in (group.get("items") or [])
                if str(it.get("id") or "") not in drop_ids and not _is_gus_notice_mail(it)
            ]
            if group["items"]:
                kept.append(group)
        sec["groups"] = kept


def ensure_lap_in_gus(data: dict) -> None:
    """GUS section keeps LAP rows from this run's candidates."""
    if not isinstance(data, dict):
        return
    gather = _load_planner_gather()
    laps = []
    for src in (data.get("gusCandidates"), gather.get("gusCandidates")):
        if not isinstance(src, list):
            continue
        for row in src:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            ident = str(row.get("id") or "")
            if role == "lap" or ident.startswith("lap-"):
                laps.append(row)
        if laps:
            break
    if not laps:
        return
    sec = next(
        (
            s
            for s in (data.get("sections") or [])
            if isinstance(s, dict) and re.match(r"^gus\b", _section_key(s.get("title")), re.I)
        ),
        None,
    )
    if sec is None:
        return
    have = set()
    for it in sec.get("items") or []:
        if isinstance(it, dict):
            have.add(str(it.get("id") or it.get("label") or ""))
    items = list(sec.get("items") or [])
    for row in laps:
        ident = str(row.get("id") or row.get("label") or "")
        if ident and ident in have:
            continue
        items.append(dict(row))
        have.add(ident)
    sec["items"] = items
    if items:
        sec.pop("empty", None)


def refuse_false_gus_clear(data: dict) -> None:
    """Do not stamp GUS — clear when this run's GUS fetch failed."""
    if not isinstance(data, dict) or os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return
    gather = _load_planner_gather()
    gus_ok = data.get("gusFetchOk")
    if gus_ok is None:
        gus_ok = gather.get("gusFetchOk")
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not re.match(r"^gus\b", _section_key(sec.get("title")), re.I):
            continue
        empty = str(sec.get("empty") or "")
        if gus_ok is False and re.search(r"gus\s*[—-]\s*clear", empty, re.I):
            sec.pop("empty", None)
        break


def _inbox_without_done(sec: dict, keys: set[str]) -> tuple[list, list]:
    def not_done(it: object) -> bool:
        return isinstance(it, dict) and not inbox_row_is_done(it, keys)

    groups = []
    for group in sec.get("groups") or []:
        if not isinstance(group, dict):
            continue
        items = [it for it in (group.get("items") or []) if not_done(it)]
        if items:
            copied = dict(group)
            copied["items"] = items
            groups.append(copied)
    items = [it for it in (sec.get("items") or []) if not_done(it)]
    return groups, items


def restore_unreviewed_inbox(data: dict, prev: dict | None, force: bool = False) -> bool:
    """If leftover fetch never finished, keep last classified Slack/Mail/GUS."""
    if not isinstance(data, dict) or not isinstance(prev, dict):
        return False
    if os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return False
    restored = False

    def title_pred(kind: str):
        if kind == "slack":
            return lambda k: bool(re.match(r"slack\b", k, re.I))
        if kind == "gus":
            return lambda k: bool(re.match(r"^gus\b", k, re.I))
        return lambda k: bool(re.match(r"(mail|email|gmail)\b", k, re.I))

    for kind, fetch_key in (("slack", "slackFetchOk"), ("mail", "mailFetchOk"), ("gus", "gusFetchOk")):
        fetch_ok = data.get(fetch_key)
        reviewed = data.get("gusReviewed") is True if kind == "gus" else data.get("inboxReviewed") is True
        if not force and fetch_ok is not False and reviewed:
            continue
        pred = title_pred(kind)
        new_sec = next((s for s in (data.get("sections") or []) if isinstance(s, dict) and pred(_section_key(s.get("title")))), None)
        old_sec = next((s for s in (prev.get("sections") or []) if isinstance(s, dict) and pred(_section_key(s.get("title")))), None)
        if not new_sec or not old_sec:
            continue
        if _inbox_rows(new_sec):
            continue
        if not _inbox_rows(old_sec):
            continue
        groups, items = _inbox_without_done(old_sec, collect_done_keys(data) | collect_done_keys(prev) | disk_done_keys())
        if not groups and not items:
            continue
        new_sec["groups"] = groups
        new_sec["items"] = items
        new_sec.pop("empty", None)
        restored = True
    return restored


DONE_MAX_AGE_SEC = 21 * 24 * 3600
DONE_ID_MAX_AGE_SEC = 2 * 24 * 3600
DONE_MAX_KEYS = 300
DURABLE_DONE_PREFIXES = (
    "case:",
    "event:",
    "calurl:",
    "slot:",
    "slack:",
    "slackch:",
    "slackth:",
    "mail:",
    "mailid:",
    "gus:",
    "gusurl:",
)

_SLACK_ARCH_RE = re.compile(
    r"(https://[^\s/]+)/archives/([CGD][A-Z0-9]+)(?:/p(\d{10,}))?",
    re.I,
)
_SLACK_CLIENT_RE = re.compile(r"slack\.com/client/([CGD][A-Z0-9]+)", re.I)
_MAIL_ID_RE = re.compile(r"#(?:all|inbox|sent|important|search|spam)/([0-9a-f]{10,})", re.I)
_SLACK_DM_ID_RE = re.compile(r"^slack-(D[A-Z0-9]{8,})$", re.I)
_MAIL_ROW_ID_RE = re.compile(r"^mail-([0-9a-f]{10,})$", re.I)


def is_durable_done_key(key: str) -> bool:
    return str(key or "").startswith(DURABLE_DONE_PREFIXES)


def prune_done_key_map(keys: object, now_ms: int | None = None, tzname: str = "") -> dict:
    if not isinstance(keys, dict):
        return {}
    now = int(now_ms or (datetime.now().timestamp() * 1000))
    durable_cut = now - DONE_MAX_AGE_SEC * 1000
    id_cut = now - DONE_ID_MAX_AGE_SEC * 1000
    out: dict = {}
    for k, at in keys.items():
        name = str(k or "")
        if not name:
            continue
        try:
            ts = float(at)
        except (TypeError, ValueError):
            ts = now
        ms = int(ts if ts > 1e12 else ts * 1000)
        if "plan-new-cases" in name.lower():
            tz = zoneinfo_or_local(tzname)
            marked = datetime.fromtimestamp(ms / 1000, tz).date()
            if marked != datetime.fromtimestamp(now / 1000, tz).date():
                continue
        if name.startswith("id:"):
            if ms < id_cut:
                continue
        elif is_durable_done_key(name):
            if ms < durable_cut:
                continue
        else:
            continue
        out[name] = ms
    if len(out) > DONE_MAX_KEYS:
        kept = sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:DONE_MAX_KEYS]
        out = dict(kept)
    return out


def _norm_done_url(raw: object) -> str:
    return str(raw or "").strip().split("?")[0].rstrip("/")


def _slack_channel_from_blob(blob: object) -> str:
    s = str(blob or "")
    m = _SLACK_ARCH_RE.search(s)
    if m:
        return m.group(2)
    m = _SLACK_CLIENT_RE.search(s)
    if m:
        return m.group(1)
    m = re.search(r"\b(D[A-Z0-9]{8,})\b", s)
    return m.group(1) if m else ""


def _mail_id_from_blob(blob: object) -> str:
    s = str(blob or "")
    m = _MAIL_ID_RE.search(s)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b([0-9a-f]{16,})\b", s, re.I)
    return m.group(1).lower() if m else ""


def item_done_keys(it: dict) -> list[str]:
    if not isinstance(it, dict):
        return []
    keys: list[str] = []
    if it.get("id"):
        keys.append("id:" + str(it.get("id")))
    digits = re.sub(r"\D", "", str(it.get("caseNumber") or ""))
    if len(digits) >= 6:
        keys.append("case:" + digits)
    m = re.search(r"#(\d{6,})", str(it.get("label") or ""))
    if m:
        keys.append("case:" + m.group(1))
    m = re.search(r"(?:need|follow|watch|meet|qw|case)-(\d{6,})", str(it.get("id") or ""), re.I)
    if m:
        keys.append("case:" + m.group(1))
    if it.get("eventId"):
        keys.append("event:" + str(it.get("eventId")))
    html = _norm_done_url(it.get("htmlLink"))
    if html and "calendar.google.com" in html.lower():
        keys.append("calurl:" + html)
    start = str(it.get("startStamp") or "").strip()
    lab = re.sub(r"\s+", " ", str(it.get("label") or "").strip().lower())
    if start and lab:
        keys.append("slot:" + start + ":" + lab)
    cid = str(it.get("channelId") or it.get("slackChannel") or "").strip()
    ts = str(it.get("ts") or it.get("threadTs") or it.get("message_ts") or "").strip()
    if re.match(r"^D[A-Z0-9]{8,}$", cid, re.I):
        keys.append("slackch:" + cid)
    elif re.match(r"^[CG][A-Z0-9]{8,}$", cid, re.I) and ts:
        keys.append("slackth:" + cid + ":" + ts)
    for key in ("slackUrl", "slackPermalink", "threadUrl", "permalink"):
        u = _norm_done_url(it.get(key))
        if "slack.com" not in u.lower() and not u.lower().startswith("slack://"):
            continue
        keys.append("slack:" + u)
        arch = _SLACK_ARCH_RE.search(u)
        if arch:
            host, ch, stamp = arch.group(1), arch.group(2), arch.group(3) or ""
            if ch.startswith("D"):
                keys.append("slackch:" + ch)
                keys.append("slack:" + f"{host}/archives/{ch}")
            elif stamp:
                keys.append("slackth:" + ch + ":" + (stamp[:10] + "." + stamp[10:] if "." not in stamp else stamp))
        client = _SLACK_CLIENT_RE.search(u)
        if client and client.group(1).startswith("D"):
            keys.append("slackch:" + client.group(1))
        break
    ident = str(it.get("id") or "")
    dm = _SLACK_DM_ID_RE.match(ident)
    if dm:
        keys.append("slackch:" + dm.group(1))
    for key in ("mailUrl", "gmailUrl", "messageUrl"):
        u = _norm_done_url(it.get(key))
        if "mail.google.com" not in u.lower() and "gmail.com" not in u.lower():
            continue
        keys.append("mail:" + u)
        mid = _mail_id_from_blob(u)
        if mid:
            keys.append("mailid:" + mid)
        break
    mid = str(it.get("messageId") or it.get("gmailId") or "").strip()
    if re.fullmatch(r"[0-9a-f]{10,}", mid, re.I):
        keys.append("mailid:" + mid.lower())
    mail_row = _MAIL_ROW_ID_RE.match(ident)
    if mail_row:
        keys.append("mailid:" + mail_row.group(1).lower())
    if it.get("workId"):
        keys.append("gus:" + str(it.get("workId")))
    if it.get("gusUrl"):
        keys.append("gusurl:" + _norm_done_url(it.get("gusUrl")))
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _ledger_slack_dms(keys: set[str]) -> set[str]:
    dms: set[str] = set()
    for k in keys or []:
        if str(k).startswith("slackch:"):
            dms.add(str(k).split(":", 1)[1])
            continue
        ch = _slack_channel_from_blob(k)
        if ch.startswith("D"):
            dms.add(ch)
        m = re.search(r"id:slack-(D[A-Z0-9]{8,})$", str(k), re.I)
        if m:
            dms.add(m.group(1))
    return dms


def _ledger_mail_ids(keys: set[str]) -> set[str]:
    ids: set[str] = set()
    for k in keys or []:
        s = str(k)
        if s.startswith("mailid:"):
            ids.add(s.split(":", 1)[1].lower())
            continue
        mid = _mail_id_from_blob(s)
        if mid:
            ids.add(mid)
        m = re.match(r"id:mail-([0-9a-f]{10,})$", s, re.I)
        if m:
            ids.add(m.group(1).lower())
    return ids


def _item_slack_dms(it: dict) -> set[str]:
    dms: set[str] = set()
    for k in item_done_keys(it):
        if k.startswith("slackch:"):
            dms.add(k.split(":", 1)[1])
        ch = _slack_channel_from_blob(k)
        if ch.startswith("D"):
            dms.add(ch)
    cid = str(it.get("channelId") or "")
    if cid.startswith("D"):
        dms.add(cid)
    return dms


def _item_mail_ids(it: dict) -> set[str]:
    ids: set[str] = set()
    for k in item_done_keys(it):
        if k.startswith("mailid:"):
            ids.add(k.split(":", 1)[1].lower())
        mid = _mail_id_from_blob(k)
        if mid:
            ids.add(mid)
    return ids


def _item_case_nums(it: dict) -> set[str]:
    nums: set[str] = set()
    digits = re.sub(r"\D", "", str(it.get("caseNumber") or ""))
    if len(digits) >= 6:
        nums.add(digits)
    m = re.search(r"#(\d{6,})", str(it.get("label") or ""))
    if m:
        nums.add(m.group(1))
    m = re.search(r"(?:need|follow|watch|meet|qw|case)-(\d{6,})", str(it.get("id") or ""), re.I)
    if m:
        nums.add(m.group(1))
    return nums


def _ledger_case_nums(keys: set[str]) -> set[str]:
    nums: set[str] = set()
    for k in keys or []:
        s = str(k)
        if s.startswith("case:"):
            n = re.sub(r"\D", "", s.split(":", 1)[1])
            if len(n) >= 6:
                nums.add(n)
            continue
        m = re.search(r"(?:need|follow|watch|meet|qw|case)-(\d{6,})", s, re.I)
        if m:
            nums.add(m.group(1))
    return nums


def item_matches_done(it: dict, keys: set[str], undone: set[str] | None = None) -> bool:
    if not isinstance(it, dict) or not keys:
        return False
    item_keys = _done_keys_for_match(it)
    undone = undone or set()
    if undone and any(k in undone for k in item_keys):
        return False
    if any(k in keys for k in item_keys):
        return True
    if _item_case_nums(it) & _ledger_case_nums(keys):
        return True
    if _item_slack_dms(it) & _ledger_slack_dms(keys):
        return True
    if _item_mail_ids(it) & _ledger_mail_ids(keys):
        return True
    return False


def _ledger_keys(rec: object) -> set[str]:
    if not isinstance(rec, dict):
        return set()
    blob = rec.get("keys") if isinstance(rec.get("keys"), dict) else rec
    return set(prune_done_key_map(blob).keys())


def undone_item_keys(data: dict) -> set[str]:
    out: set[str] = set()
    if not isinstance(data, dict):
        return out
    for _sec, it in _walk_items(data):
        if it.get("done") is False:
            out.update(item_done_keys(it))
    return out


def _is_google_cal_row(it: dict) -> bool:
    if not isinstance(it, dict):
        return False
    if it.get("assembled") is True or str(it.get("kind") or "").lower() == "assembled":
        return False
    rid = str(it.get("id") or "")
    if it.get("composed") is True or rid.startswith("plan-"):
        return False
    if it.get("autoBreak") or str(it.get("kind") or "").lower() == "break":
        return False
    return bool(it.get("eventId") or it.get("invite") is True or it.get("htmlLink"))


def _done_keys_for_match(it: dict) -> list[str]:
    keys = item_done_keys(it)
    if _is_google_cal_row(it):
        return [k for k in keys if k.startswith("id:") or k.startswith("event:")]
    return keys


def collect_done_keys(data: dict) -> set[str]:
    keys: set[str] = set()
    if not isinstance(data, dict):
        return keys
    keys |= _ledger_keys(data.get("doneLedger"))
    extra = data.get("doneKeys")
    if isinstance(extra, list):
        keys.update(str(k) for k in extra if k and (is_durable_done_key(str(k)) or str(k).startswith("id:")))
    for _sec, it in _walk_items(data):
        if it.get("done") is not True:
            continue
        if _is_google_cal_row(it):
            continue
        durable = [k for k in _done_keys_for_match(it) if is_durable_done_key(k) or str(k).startswith("id:")]
        if durable:
            keys.update(durable)
        elif it.get("id"):
            keys.add("id:" + str(it.get("id")))
    return {k for k in keys if k}


def apply_done_keys(data: dict, keys: set[str], undone: set[str] | None = None) -> None:
    if not isinstance(data, dict):
        return
    undone = undone or set()
    if not keys and not undone:
        return
    ids = []
    for _sec, it in _walk_items(data):
        if it.get("autoBreak") or str(it.get("kind") or "").lower() == "break":
            continue
        if _is_google_cal_row(it):
            it.pop("done", None)
            continue
        item_keys = _done_keys_for_match(it)
        if undone and any(k in undone for k in item_keys):
            it["done"] = False
            continue
        if item_matches_done(it, keys, undone):
            it["done"] = True
            if it.get("id"):
                ids.append(str(it.get("id")))
    if ids:
        data["doneIds"] = sorted(set(ids))


def stamp_items_done_from_ledger(items: list, extra: dict | None = None, data: dict | None = None) -> None:
    keys = collect_done_keys(data) if isinstance(data, dict) else set()
    keys |= _ledger_keys(extra)
    if not keys:
        return
    for it in items or []:
        if not isinstance(it, dict) or it.get("done") is False:
            continue
        if item_matches_done(it, keys):
            it["done"] = True


def inbox_row_is_done(it: dict, keys: set[str] | None = None) -> bool:
    """A Slack or Mail row already marked Done. Case Done does not hide a different message."""
    if not isinstance(it, dict):
        return False
    if it.get("done") is True:
        return True
    keys = keys or set()
    if not keys:
        return False
    if _item_slack_dms(it) & _ledger_slack_dms(keys):
        return True
    if _item_mail_ids(it) & _ledger_mail_ids(keys):
        return True
    for key in item_done_keys(it):
        if key.startswith(("slack:", "slackch:", "slackth:", "mail:", "mailid:", "id:slack-", "id:mail-")) and key in keys:
            return True
    return False


def _done_ledger_files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    here = pathlib.Path(__file__).resolve().parent.parent / "out" / ".done-keys.json"
    if here.is_file():
        found.append(here)
    runs = pathlib.Path.home() / "Library" / "Application Support" / "engineer-day-planner" / "runs"
    try:
        extra = [path for path in runs.glob("*/skill/out/.done-keys.json") if path.is_file()]
    except OSError:
        extra = []
    extra.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in extra:
        if path not in found:
            found.append(path)
    return found


def disk_done_keys() -> set[str]:
    """Done marks from this snapshot and earlier ones. A new run must omit those Slack and Mail rows."""
    keys: set[str] = set()
    for path in _done_ledger_files():
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        keys |= _ledger_keys(rec)
    return keys


def omit_done_inbox_rows(data: dict) -> None:
    """Next Run Planner drops Slack/Mail already marked Done. The open page keeps them struck."""
    if not isinstance(data, dict):
        return
    keys = collect_done_keys(data) | disk_done_keys()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        title = _section_key(sec.get("title"))
        if re.match(r"^slack\b", title, re.I):
            empty = "Slack — clear"
        elif re.match(r"^(mail|email|gmail)\b", title, re.I):
            empty = "Mail — clear"
        else:
            continue

        def keep(it: object) -> bool:
            return isinstance(it, dict) and not inbox_row_is_done(it, keys)

        for g in sec.get("groups") or []:
            if isinstance(g, dict):
                g["items"] = [it for it in (g.get("items") or []) if keep(it)]
        sec["groups"] = [
            g for g in (sec.get("groups") or []) if isinstance(g, dict) and (g.get("items") or [])
        ]
        sec["items"] = [it for it in (sec.get("items") or []) if keep(it)]
        if not _inbox_rows(sec):
            sec["groups"] = []
            sec["items"] = []
            stamp_inbox_empty(sec, data, bool(re.match(r"^slack\b", title, re.I)))


def drop_google_done_keys(keys: dict) -> dict:
    """Google events are fetched fresh each run. Done does not stick to the series."""
    if not isinstance(keys, dict):
        return {}
    return {
        k: v
        for k, v in keys.items()
        if not (
            str(k).startswith("event:")
            or str(k).startswith("calurl:")
            or str(k).startswith("id:cal-")
        )
    }


def drop_google_done_keys(keys: dict) -> dict:
    """Google events are fetched fresh each run. Done does not stick to the series."""
    if not isinstance(keys, dict):
        return {}
    return {
        k: v
        for k, v in keys.items()
        if not (
            str(k).startswith("event:")
            or str(k).startswith("calurl:")
            or str(k).startswith("id:cal-")
        )
    }


def apply_persisted_done(data: dict, prev: dict | None = None, extra: dict | None = None) -> None:
    undone = {
        key
        for key in undone_item_keys(data)
        if not str(key).startswith(("slack:", "slackch:", "slackth:", "mail:", "mailid:", "id:slack-", "id:mail-"))
    }
    keys = collect_done_keys(data)
    if isinstance(prev, dict):
        keys |= collect_done_keys(prev)
    keys |= _ledger_keys(extra)
    keys -= undone
    apply_done_keys(data, keys, undone)
    sync_queue_plan_blocks(data)
    now_ms = int(datetime.now().timestamp() * 1000)
    blob = extra.get("keys") if isinstance(extra, dict) and isinstance(extra.get("keys"), dict) else {}
    merged = dict(blob) if isinstance(blob, dict) else {}
    if isinstance(data.get("doneLedger"), dict) and isinstance(data["doneLedger"].get("keys"), dict):
        for k, at in data["doneLedger"]["keys"].items():
            if k and k not in merged:
                merged[k] = at
    for k in keys:
        merged[k] = merged.get(k) or now_ms
    for k in undone:
        merged.pop(k, None)
    merged = drop_google_done_keys(
        prune_done_key_map(merged, now_ms, str(data.get("timezone") or ""))
    )
    for _sec, it in _walk_items(data):
        if _is_google_cal_row(it):
            it.pop("done", None)
    data["doneLedger"] = {"keys": merged, "updatedAt": now_ms}
    data["doneKeys"] = sorted(k for k in merged if is_durable_done_key(k))


def drop_done_from_plan(data: dict) -> None:
    """Done stays on Today's plan (struck + silent). Do not drop clock rows."""
    return


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
SLACK_SEC_RE = re.compile(r"^slack\b", re.I)
MAIL_SEC_RE = re.compile(r"^(mail|email|gmail)\b", re.I)


def _is_queue_leftover_plan_row(item: dict) -> bool:
    """plan-follow / plan-slack / plan-mail leftover aggregators only — not AI work that reused the id."""
    rid = str((item or {}).get("id") or "")
    lab = str((item or {}).get("label") or "").strip().lower()
    det = str((item or {}).get("detail") or "").strip().lower()
    if rid.startswith("plan-follow-"):
        return True
    if rid == "plan-follow":
        return bool(re.match(r"follow-ups?\b", lab)) or "follow-up due" in det
    if rid == "plan-slack":
        return "leftover" in lab or lab.startswith("slack") or "leftover thread" in det
    if rid == "plan-mail":
        return "leftover" in lab or bool(re.match(r"(mail|email|gmail)\b", lab)) or "leftover email" in det
    if rid == "plan-close":
        return "close" in lab or "closure" in lab or "promised" in lab
    return False
KEEP_MAIL_RE = re.compile(
    r"\b(gus|w-\d{4,}|chatter|black\s*tab|blacktab|mentioned you)\b",
    re.I,
)
CASE_COMMENT_MAIL_RE = re.compile(
    r"("
    r"ref:!\d|"
    r"\[ ?ref:|"
    r"outbound contact\s*:|"
    r"email[- ]to[- ]case|"
    r"new case comment|"
    r"customer comments? on\s+case|"
    r"(?:new |a )?comments? on\s+case\s+\d|"
    r"posted a comments? on (?:case|this case)|"
    r"\bcase comments?\b|"
    r"customersupport@salesforce\.com|"
    r"you have been assigned.{0,40}\bcase\b|"
    r"new case assigned"
    r")",
    re.I,
)
LOGOFF_SEC_RE = re.compile(r"^before you log off$", re.I)
GEO_HANDOVER_RE = re.compile(r"\bgeo\b|handover|handoff", re.I)
CLOSE_LABEL_RE = re.compile(
    r"\b(promised close|case closures?|close this case|close if still quiet|plan to close|go ahead and close)\b",
    re.I,
)
CLOSE_PROMISE_RE = re.compile(
    r"\b(i will (?:plan to )?close|i(?:'ll| will) go ahead and close|plan to close(?: it)?|close it by|close this case|promised close)\b",
    re.I,
)
OFFER_AWAIT_RE = re.compile(
    r"if you are all set.{0,120}let us know|looking forward to your response",
    re.I,
)
TOMORROW_SEC_RE = re.compile(r"^tomorrow", re.I)
TROUBLE_RE = re.compile(r"troubleshoot|freeze|gack|sev-?1|level 1|\bcti\b", re.I)
SHORT_NOW_RE = re.compile(
    r"\b("
    r"hold(?:ing| off)?|"
    r"do not enable|"
    r"reschedule|"
    r"ack(?:nowledg(?:e|ment))?"
    r")\b",
    re.I,
)
_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_WORD_DAYS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "couple": 2,
    "three": 3,
    "few": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
}
CASE_NUM_RE = re.compile(r"(\d{8,})")


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
    m = CASE_NUM_RE.search(
        str((item or {}).get("caseNumber") or (item or {}).get("label") or (item or {}).get("id") or "")
    )
    return m.group(1) if m else ""


def _owned_digit_set(owned) -> set[str]:
    out: set[str] = set()
    for raw in owned or []:
        text = str(raw or "").strip()
        if not text:
            continue
        out.add(text)
        match = CASE_NUM_RE.search(text)
        if match:
            out.add(match.group(1))
    return out


def live_owned_case_numbers(owned=None) -> set[str] | None:
    """None = ownership unknown (do not strip). set, including empty, is this run's bin."""
    if owned is not None:
        return _owned_digit_set(owned)
    try:
        obj = json.loads(pathlib.Path("/tmp/owned-cases.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        obj = None
    if isinstance(obj, dict) and obj.get("fetched") is True:
        nums: set[str] = set()
        for rec in obj.get("records") or []:
            if not isinstance(rec, dict):
                continue
            n = str(rec.get("CaseNumber") or "").strip()
            if n:
                nums.add(n)
                match = CASE_NUM_RE.search(n)
                if match:
                    nums.add(match.group(1))
        return nums
    gather = _load_planner_gather()
    if gather.get("orgcsFetchOk") is True:
        nums = set()
        for row in gather.get("cases") or []:
            if not isinstance(row, dict):
                continue
            n = str(row.get("caseNumber") or row.get("CaseNumber") or "").strip()
            if n:
                nums.add(n)
                match = CASE_NUM_RE.search(n)
                if match:
                    nums.add(match.group(1))
        return nums
    return None


def drop_unowned_cases(data: dict, owned=None) -> None:
    """Reassigned or closed cases leave this engineer's bin. Not a rank decision."""
    if not isinstance(data, dict) or os.environ.get("DAY_PLANNER_EMPTY") == "1":
        return
    live = live_owned_case_numbers(owned)
    if live is None:
        return
    case_sec = re.compile(
        r"needs (us|you) now|follow-up due|still watching|no action needed|"
        r"quick wins|before you log off|tomorrow|customer asked|today['’]?s plan|^calendar$|^day plan",
        re.I,
    )

    def keep_item(item) -> bool:
        if not isinstance(item, dict):
            return True
        if item.get("eventId") or item.get("htmlLink") or str(item.get("kind") or "") == "meeting":
            return True
        num = _case_num(item)
        if not num:
            return True
        return num in live or str(item.get("caseNumber") or "").strip() in live

    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not case_sec.search(_section_key(sec.get("title"))):
            continue
        sec["items"] = [it for it in (sec.get("items") or []) if keep_item(it)]
        for group in sec.get("groups") or []:
            if isinstance(group, dict):
                group["items"] = [it for it in (group.get("items") or []) if keep_item(it)]
    for key in (
        "needsUsNow",
        "followUpDue",
        "stillWatching",
        "quickWins",
        "customerAskedMeeting",
        "beforeYouLogOff",
    ):
        bag = data.get(key)
        if not isinstance(bag, list):
            continue
        kept = []
        for it in bag:
            if isinstance(it, dict):
                if keep_item(it):
                    kept.append(it)
                continue
            match = CASE_NUM_RE.search(str(it or ""))
            if not match or match.group(1) in live:
                kept.append(it)
        data[key] = kept
    plan = data.get("todayPlan")
    if isinstance(plan, list):
        data["todayPlan"] = [it for it in plan if keep_item(it)]
    peeks = data.get("peeks")
    if isinstance(peeks, dict):
        data["peeks"] = {
            key: val
            for key, val in peeks.items()
            if str(key) in live
            or (CASE_NUM_RE.search(str(key) or "") and CASE_NUM_RE.search(str(key)).group(1) in live)
        }
    cases = data.get("cases")
    if isinstance(cases, list):
        data["cases"] = [it for it in cases if not isinstance(it, dict) or keep_item(it)]


def _one_line(text, n=160) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def _is_soft_enablement(item: dict) -> bool:
    if item.get("important") is True:
        return False
    label = str(item.get("label") or "").strip()
    if HARD_MEETING_RE.search(label) or CUSTOMER_MEET_RE.search(label):
        return False
    return bool(SOFT_ENABLE_RE.search(label))


CAL_MAX_MINUTES = 12 * 60


def _event_minutes(item: dict) -> int:
    start = _wall(item.get("startStamp") or "")
    end = _wall(item.get("endStamp") or "")
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _overlaps_range(item: dict, lo: datetime, hi: datetime) -> bool:
    start = _wall(item.get("startStamp") or "")
    end = _wall(item.get("endStamp") or "")
    return bool(start and end and end > lo and start < hi)


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
    if _event_minutes(item) > CAL_MAX_MINUTES:
        return False
    if _is_soft_enablement(item):
        return False
    return True


def merge_primary_events_into_plan(data: dict, events: list | None = None) -> None:
    """Upsert primary Google events onto Today's plan so already-ended meetings stay visible."""
    extra = events if isinstance(events, list) else data.get("primaryEvents")
    if not isinstance(extra, list) or not extra:
        return
    sections = data.get("sections")
    if not isinstance(sections, list):
        sections = []
        data["sections"] = sections
    plan = None
    for sec in sections:
        if isinstance(sec, dict) and re.search(
            r"today['’]?s plan|^calendar$|^day plan|rest of day",
            _section_key(sec.get("title")),
            re.I,
        ):
            plan = sec
            break
    if plan is None:
        plan = {"title": "Today's plan", "open": True, "items": []}
        sections.append(plan)
    items = [it for it in (plan.get("items") or []) if isinstance(it, dict)]
    by_eid: dict[str, dict] = {}
    for it in items:
        eid = str(it.get("eventId") or "").strip()
        if eid:
            by_eid[eid] = it
    for ev in extra:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("eventId") or "").strip()
        if not eid:
            continue
        if eid in by_eid:
            dest = by_eid[eid]
            for key in (
                "startStamp",
                "endStamp",
                "htmlLink",
                "joinUrl",
                "label",
                "invite",
                "rsvp",
                "important",
                "detail",
            ):
                if ev.get(key) not in (None, "", False) and dest.get(key) in (None, "", False):
                    dest[key] = ev[key]
            continue
        row = dict(ev)
        row.setdefault("kind", "meeting")
        row["eventId"] = eid
        items.append(row)
        by_eid[eid] = row
    plan["items"] = items


def _is_composed_row(item: dict) -> bool:
    return isinstance(item, dict) and (
        item.get("composed") is True or str(item.get("id") or "").startswith("plan-")
    )


def _real_case_row(item: dict, *, include_done: bool = False) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("done") is True and not include_done:
        return False
    kind = str(item.get("kind") or "").lower()
    if kind in ("note", "meeting", "slack", "mail", "break"):
        return False
    lab = str(item.get("label") or "")
    if re.search(r"\bclear\b", lab, re.I) and not item.get("caseNumber"):
        return False
    return bool(item.get("caseNumber") or kind == "case" or CASE_NUM_RE.search(lab))


def _find_case_row(data: dict, num: str) -> dict | None:
    want = str(num or "").replace("#", "").strip()
    if not want:
        return None
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for it in _walk_items_sec(sec):
            if isinstance(it, dict) and _case_num(it) == want:
                return it
    return None


def _case_row_rich(it: dict | None) -> bool:
    if not isinstance(it, dict):
        return False
    if it.get("caseUrl") or it.get("status") or it.get("detail"):
        return True
    return "—" in str(it.get("label") or "")


def _park_close_case(data: dict, it: dict) -> None:
    num = _case_num(it)
    if not num:
        return
    bag = data.setdefault("_closeCaseRows", {})
    if not isinstance(bag, dict):
        bag = {}
        data["_closeCaseRows"] = bag
    prev = bag.get(num)
    if not isinstance(prev, dict) or (_case_row_rich(it) and not _case_row_rich(prev)):
        bag[num] = dict(it)


def _close_case_source(data: dict, num: str) -> dict | None:
    found = _find_case_row(data, num)
    parked = data.get("_closeCaseRows") if isinstance(data.get("_closeCaseRows"), dict) else {}
    saved = parked.get(num) if isinstance(parked, dict) else None
    if _case_row_rich(found):
        return found
    if isinstance(saved, dict):
        return saved
    return found


def _collect_ranked_cases(data: dict) -> tuple[list, list]:
    now_cases, follow_cases = [], []
    seen: set[str] = set()
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
        for it in _walk_items_sec(sec):
            if not _real_case_row(it, include_done=True):
                continue
            num = _case_num(it)
            if num and num in seen:
                continue
            if num:
                seen.add(num)
            bucket.append(it)
    return now_cases, follow_cases


def _walk_items_sec(sec: dict):
    for it in sec.get("items") or []:
        if isinstance(it, dict):
            yield it
    for group in sec.get("groups") or []:
        if isinstance(group, dict):
            for it in group.get("items") or []:
                if isinstance(it, dict):
                    yield it


def _section_counts(data: dict, pred) -> tuple[int, int]:
    total = 0
    open_n = 0
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict) or not pred(sec):
            continue
        for it in _walk_items_sec(sec):
            if it.get("kind") == "break":
                continue
            total += 1
            if it.get("done") is not True:
                open_n += 1
    return total, open_n


def _leftover_count(data: dict, pred) -> int:
    return _section_counts(data, pred)[1]


def _pretty_close_day(iso: str) -> str:
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return str(iso or "")
    return d.strftime("%A, %b ") + str(d.day)


def _add_days(today, n: int, business: bool = False) -> str:
    if n < 1 or n > 21:
        return ""
    if not business:
        return (today + timedelta(days=n)).isoformat()
    d = today
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.isoformat()


def _ymd(year: int, month: int, day: int) -> str:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _relative_close_days(s: str, today) -> str:
    m = re.search(
        r"\b(?:in|after|within)\s+(?:a\s+)?(\d+|one|two|couple|few|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen)\s+(business\s+)?days?\b",
        s,
    )
    if m:
        token = m.group(1)
        n = int(token) if token.isdigit() else _WORD_DAYS.get(token, 0)
        return _add_days(today, n, bool(m.group(2)))
    m = re.search(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\s+from\s+(?:now|today)\b",
        s,
    )
    if m:
        token = m.group(1)
        n = int(token) if token.isdigit() else _WORD_DAYS.get(token, 0)
        return _add_days(today, n, False)
    m = re.search(r"\b(?:in|after|within)\s+(\d+)\s+hours?\b", s)
    if m:
        n = max(1, (int(m.group(1)) + 23) // 24)
        return _add_days(today, n, False)
    return ""


def _weekday_iso(name: str, today, nxt: bool = False) -> str:
    idx = _WEEKDAYS.get(str(name or "").strip().lower())
    if idx is None:
        return ""
    delta = (idx - today.weekday()) % 7
    if nxt and delta == 0:
        delta = 7
    return (today + timedelta(days=delta)).isoformat()


def _calendar_date_in(s: str, today) -> str:
    m = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b",
        s,
    )
    if m:
        month = _MONTHS.get(m.group(1)) or _MONTHS.get(m.group(1)[:3])
        year = int(m.group(3) or today.year)
        return _ymd(year, month or 0, int(m.group(2)))
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s+(\d{4}))?\b",
        s,
    )
    if m:
        month = _MONTHS.get(m.group(2)) or _MONTHS.get(m.group(2)[:3])
        year = int(m.group(3) or today.year)
        return _ymd(year, month or 0, int(m.group(1)))
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        year = today.year
        if m.group(3):
            y = int(m.group(3))
            year = y + 2000 if y < 100 else y
        if a > 12:
            day_n, month = a, b
        elif b > 12:
            month, day_n = a, b
        else:
            month, day_n = a, b
        return _ymd(year, month, day_n)
    return ""


_WD_GROUP = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)


def _extract_close_day(blob: str, today) -> str:
    """Read a close-by day out of comment/email/note language."""
    s = " " + re.sub(r"[.,;:]+", " ", str(blob or "").lower()) + " "
    s = re.sub(r"\s+", " ", s)
    if not s.strip():
        return ""
    rel = _relative_close_days(s, today)
    if rel:
        return rel
    dated = _calendar_date_in(s, today)
    if dated:
        return dated
    m = re.search(
        rf"\b(?:by\s+)?eod(?:\s+(?:on|by))?\s+({_WD_GROUP}|today|tomorrow|tmw)\b",
        s,
    )
    if m:
        return _resolve_named_close_day(m.group(1), today)
    m = re.search(
        rf"\bend of (?:the )?(?:business )?day(?:\s+(?:on\s+)?({_WD_GROUP}|today|tomorrow|tmw))?\b",
        s,
    )
    if m and m.group(1):
        return _resolve_named_close_day(m.group(1), today)
    eod = bool(
        re.search(
            r"\b(?:by\s+)?eod\b|\bend of (?:the )?(?:business )?day\b|"
            r"\bclose(?: this case)? today\b|\btonight\b|\bthis evening\b|"
            r"\bbefore (?:i |we )?log\s?off\b|\bby logout\b",
            s,
        )
    )
    if eod:
        return today.isoformat()
    m = re.search(rf"\b(?:this|next)\s+({_WD_GROUP})\b", s)
    if m:
        return _weekday_iso(m.group(1), today, nxt=m.group(0).strip().startswith("next"))
    m = re.search(rf"\b({_WD_GROUP})\b", s)
    if m:
        return _weekday_iso(m.group(1), today)
    if re.search(r"\btomorrow\b|\btmw\b|\bthe next day\b", s):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", s):
        return today.isoformat()
    return ""


def _resolve_named_close_day(raw: str, today) -> str:
    s = re.sub(r"^(by|on|before|until)\s+", "", str(raw or "").strip().lower())
    s = re.sub(r"[.,]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    if s in (
        "today",
        "eod",
        "eod today",
        "tonight",
        "this evening",
        "end of day",
        "end of the day",
        "end of business",
        "end of the business day",
        "end of the business day today",
    ):
        return today.isoformat()
    if s in ("tomorrow", "tmw"):
        return (today + timedelta(days=1)).isoformat()
    found = _extract_close_day(s, today)
    if found:
        return found
    token = s.split()[0]
    return _weekday_iso(token, today)


def _parse_close_on(raw, today: str | None = None) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if not today:
        today = datetime.now().strftime("%Y-%m-%d")
    try:
        day = datetime.strptime(str(today)[:10], "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()
    return _resolve_named_close_day(s, day)


def _item_text_blob(it: dict) -> str:
    parts = [it.get("label"), it.get("detail"), it.get("summary")]
    for ev in it.get("chronology") or []:
        if isinstance(ev, dict):
            parts.append(ev.get("text"))
            parts.append(ev.get("when"))
        else:
            parts.append(ev)
    return " ".join(str(p or "") for p in parts)


def _infer_close_on_from_text(it: dict, today: str) -> str:
    blob = _item_text_blob(it)
    if not blob:
        return ""
    try:
        day = datetime.strptime(str(today)[:10], "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()
    dated = _extract_close_day(blob, day)
    if OFFER_AWAIT_RE.search(blob) and not dated:
        return ""
    if not (CLOSE_PROMISE_RE.search(blob) or CLOSE_LABEL_RE.search(blob) or it.get("promisedClose") is True):
        if not dated:
            return ""
        if not re.search(r"\b(close|closing|eod|end of (?:the )?(?:business )?day)\b", blob, re.I):
            return ""
    if dated:
        return dated
    if CLOSE_PROMISE_RE.search(blob) or it.get("promisedClose") is True:
        return today
    return ""


def _item_resolved_close_on(it: dict, sec: dict | None, today: str) -> str:
    on = _parse_close_on(it.get("promisedCloseOn") or it.get("closeOn"), today)
    if not on:
        on = _infer_close_on_from_text(it, today)
    if not on and (it.get("promisedClose") is True or _is_close_item(it, sec, today)):
        on = today
    return on


def _shift_today_iso(data: dict) -> str:
    start, _end = _shift_window_today(data)
    return start.strftime("%Y-%m-%d")


def _is_close_item(it: dict, sec: dict | None = None, today: str = "") -> bool:
    if not isinstance(it, dict):
        return False
    if str(it.get("id") or "").startswith("plan-"):
        return False
    if it.get("promisedClose") is True:
        return True
    if _parse_close_on(it.get("promisedCloseOn") or it.get("closeOn"), today):
        return True
    blob = " ".join(str(it.get(k) or "") for k in ("label", "detail", "summary"))
    if GEO_HANDOVER_RE.search(blob) and not CLOSE_LABEL_RE.search(blob):
        return False
    in_logoff = bool(sec and LOGOFF_SEC_RE.search(_section_key(sec.get("title"))))
    return bool(in_logoff and CLOSE_LABEL_RE.search(blob))


def _promised_close_today(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    today = _shift_today_iso(data)
    out = []
    seen: set[str] = set()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for it in _walk_items_sec(sec):
            if not isinstance(it, dict) or it.get("kind") == "break":
                continue
            on = _item_resolved_close_on(it, sec, today)
            if not on or on > today:
                continue
            num = _case_num(it) or str(it.get("id") or "")
            if not num or num in seen:
                continue
            seen.add(num)
            out.append(it)
    return out


def _promised_close_on_day(data: dict, iso: str) -> list:
    if not isinstance(data, dict) or not iso:
        return []
    today = _shift_today_iso(data)
    out = []
    seen: set[str] = set()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for it in _walk_items_sec(sec):
            if not isinstance(it, dict) or it.get("kind") == "break":
                continue
            on = _item_resolved_close_on(it, sec, today)
            if on != iso:
                continue
            num = _case_num(it) or str(it.get("id") or "")
            if not num or num in seen:
                continue
            seen.add(num)
            out.append(it)
    return out


def _logoff_section(data: dict) -> dict:
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and LOGOFF_SEC_RE.search(_section_key(sec.get("title"))):
            return sec
    sec = {"title": "Before you log off", "open": True, "items": []}
    data.setdefault("sections", []).append(sec)
    return sec


def _ensure_close_logoff(data: dict, cases: list) -> None:
    if data.get("aiAnalyzed") is True:
        return
    if not cases:
        return
    today = _shift_today_iso(data)
    sec = _logoff_section(data)
    items = sec.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        sec["items"] = items
    have: set[str] = set()
    for it in _walk_items_sec(sec):
        num = _case_num(it)
        if num:
            have.add(num)
        if isinstance(it, dict) and (
            _parse_close_on(it.get("promisedCloseOn") or it.get("closeOn"), today)
            or it.get("promisedClose") is True
        ):
            it["promisedClose"] = True
    for src in cases:
        num = _case_num(src)
        if num and num in have:
            continue
        on = _item_resolved_close_on(src, None, today) or today
        overdue = on < today
        pretty = _pretty_close_day(on)
        row = {
            "id": "close-" + (num or str(len(items) + 1)),
            "kind": "case",
            "label": (
                f"#{num} — promised close overdue ({pretty})"
                if overdue and num
                else f"#{num} — promised close {pretty}"
                if num
                else f"Promised close {pretty}"
            ),
            "detail": _one_line(
                src.get("detail") or "Close if still quiet. Do not invent new close language.",
                160,
            ),
            "asTask": True,
            "promisedClose": True,
            "promisedCloseOn": on,
        }
        if src.get("caseNumber"):
            row["caseNumber"] = src["caseNumber"]
        if src.get("caseUrl"):
            row["caseUrl"] = src["caseUrl"]
        if src.get("done") is True:
            row["done"] = True
        items.append(row)
        if num:
            have.add(num)


def _tomorrow_section(data: dict) -> dict:
    for sec in data.get("sections") or []:
        if isinstance(sec, dict) and TOMORROW_SEC_RE.search(_section_key(sec.get("title"))):
            return sec
    sec = {"title": "Tomorrow, first thing", "open": True, "items": []}
    data.setdefault("sections", []).append(sec)
    return sec


def _ensure_tomorrow_closes(data: dict) -> None:
    if data.get("aiAnalyzed") is True:
        return
    today = datetime.strptime(_shift_today_iso(data), "%Y-%m-%d").date()
    tmr = (today + timedelta(days=1)).isoformat()
    cases = _promised_close_on_day(data, tmr)
    if not cases:
        return
    sec = _tomorrow_section(data)
    items = sec.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        sec["items"] = items
    have: set[str] = set()
    for it in _walk_items_sec(sec):
        num = _case_num(it)
        if num:
            have.add(num)
    pretty = _pretty_close_day(tmr)
    for src in cases:
        num = _case_num(src)
        if num and num in have:
            continue
        row = {
            "id": "close-tmr-" + (num or str(len(items) + 1)),
            "kind": "case",
            "label": f"#{num} — promised close {pretty}" if num else f"Promised close {pretty}",
            "detail": _one_line(
                src.get("detail") or "Named close day is tomorrow. Close if still quiet.",
                160,
            ),
            "asTask": True,
            "promisedClose": True,
            "promisedCloseOn": tmr,
        }
        if src.get("caseNumber"):
            row["caseNumber"] = src["caseNumber"]
        if src.get("caseUrl"):
            row["caseUrl"] = src["caseUrl"]
        items.append(row)
        if num:
            have.add(num)
        sec.pop("empty", None)


def sync_queue_plan_blocks(data: dict) -> None:
    """Strike Slack / Mail / Follow-ups / Case closures clock blocks when leftovers are Done."""
    if not isinstance(data, dict):
        return
    mapping = (
        ("plan-slack", lambda sec: SLACK_SEC_RE.search(_section_key(sec.get("title")))),
        ("plan-mail", lambda sec: MAIL_SEC_RE.search(_section_key(sec.get("title")))),
        ("plan-follow-", lambda sec: FOLLOW_SEC_RE.search(_section_key(sec.get("title")))),
    )
    for plan_id, pred in mapping:
        total, open_n = _section_counts(data, pred)
        if not total:
            continue
        prefix = plan_id.endswith("-")
        for _sec, it in _walk_items(data):
            row_id = str(it.get("id") or "")
            if prefix:
                if not row_id.startswith(plan_id):
                    continue
                if not _is_queue_leftover_plan_row(it):
                    continue
                it["done"] = open_n == 0
            elif row_id == plan_id:
                if not _is_queue_leftover_plan_row(it):
                    continue
                it["done"] = open_n == 0
                break
    close_cases = _promised_close_today(data)
    if close_cases:
        open_n = sum(1 for it in close_cases if it.get("done") is not True)
        for _sec, it in _walk_items(data):
            if str(it.get("id") or "") == "plan-close":
                it["done"] = open_n == 0
                break


def _shift_window_today(data: dict) -> tuple[datetime, datetime]:
    tzname = str(data.get("timezone") or "").strip()
    tz = zoneinfo_or_local(tzname)
    now = datetime.now(tz) if tz is not None else datetime.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    start_raw = str(data.get("shiftStart") or "").strip()
    end_raw = str(data.get("shiftEnd") or "").strip()
    sh, sm = _hm(start_raw, (8, 0) if tzname else (0, 0)) if start_raw or tzname else (0, 0)
    eh, em = _hm(end_raw, (17, 0) if tzname else (0, 0)) if end_raw or tzname else (0, 0)
    start = day.replace(hour=sh, minute=sm)
    end = day.replace(hour=eh, minute=em)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end


def _shift_pad_today(data: dict) -> tuple[datetime, datetime]:
    start, end = _shift_window_today(data)
    return start - timedelta(hours=2), end + timedelta(hours=2)


def _same_day(item: dict, day: datetime) -> bool:
    start = _wall(item.get("startStamp") or "")
    return bool(start and start.date() == day.date())


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


def _in_customer_buffer(when: datetime, occupied: list, minutes: int = 20) -> bool:
    for a, _b, item in occupied:
        if CUSTOMER_MEET_RE.search(str((item or {}).get("label") or "")) and a - timedelta(minutes=minutes) < when <= a:
            return True
    return False


def _take_gap(gaps: list, minutes: int, occupied: list, *, after=None, avoid_buffer=False):
    need = timedelta(minutes=max(10, int(minutes)))
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


def _take_partial_gap(gaps: list, minutes: int, occupied: list, *, after=None):
    """Use a leftover hole even if it is shorter than requested (min 10m)."""
    want = timedelta(minutes=max(10, int(minutes)))
    for i, gap in enumerate(gaps):
        lo, hi = gap[0], gap[1]
        start = lo
        if after is not None and start < after:
            start = after
        avail = hi - start
        if avail < timedelta(minutes=10):
            continue
        end = start + min(want, avail)
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


def _round_up_5(when: datetime) -> datetime:
    when = when.replace(second=0, microsecond=0)
    extra = (5 - when.minute % 5) % 5
    if extra:
        when = when + timedelta(minutes=extra)
    return when


def _plan_now_wall(data: dict, start: datetime, end: datetime, now: datetime | None = None) -> datetime:
    """Earliest slot for incomplete work. Past is only for completed blocks."""
    if now is None:
        tzname = str((data or {}).get("timezone") or "").strip()
        tz = zoneinfo_or_local(tzname)
        now = datetime.now(tz) if tz is not None else datetime.now()
    if getattr(now, "tzinfo", None) is not None:
        now = now.replace(tzinfo=None)
    now = _round_up_5(now)
    if now <= start:
        return start
    return now


def _item_is_done(it: dict, done_keys: set) -> bool:
    if not isinstance(it, dict):
        return False
    if it.get("done") is True:
        return True
    if it.get("done") is False:
        return False
    return bool(done_keys and any(k in done_keys for k in item_done_keys(it)))


def _clamp_work_mins(value, lo: int = 10, hi: int = 180) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, minutes))


def _harvest_work_durations(data: dict) -> dict:
    """Keep only user-adjusted minutes. Auto stamps must not lock the next compose."""
    out: dict[str, int] = {}
    user_ids: set[str] = set()
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        if not re.search(
            r"today['’]?s plan|^calendar$|^day plan|rest of day",
            _section_key(sec.get("title")),
            re.I,
        ):
            continue
        for it in sec.get("items") or []:
            if not isinstance(it, dict) or not _is_composed_row(it):
                continue
            if it.get("durationUserSet") is not True:
                continue
            row_id = str(it.get("id") or "").strip()
            if not row_id:
                continue
            minutes = it.get("durationMinutes")
            if not minutes:
                start = _wall(it.get("startStamp") or "")
                end = _wall(it.get("endStamp") or "")
                if start and end and end > start:
                    minutes = int((end - start).total_seconds() // 60)
            minutes = _clamp_work_mins(minutes)
            if minutes:
                out[row_id] = minutes
                user_ids.add(row_id)
    raw = data.get("workDurations")
    if isinstance(raw, dict):
        for key, value in raw.items():
            row_id = str(key or "").strip()
            if row_id not in user_ids:
                continue
            minutes = _clamp_work_mins(value)
            if row_id and minutes:
                out[row_id] = minutes
    return out


def _named_now_label(src: dict) -> str:
    num = _case_num(src)
    raw = _one_line(src.get("label") or "", 90)
    raw = re.sub(r"^[^\w#]+", "", raw)
    if num:
        raw = re.sub(r"^#?" + re.escape(num) + r"\s*[—\-:]*\s*", "", raw)
        return "🔧 #" + num + (f" — {raw}" if raw else "")
    return "🔧 " + (raw or "Needs us now")


def _now_default_minutes(src: dict) -> int:
    return 25


def _queue_block_minutes(open_n: int, *, slack: bool = False, mail: bool = False) -> int:
    n = max(0, int(open_n or 0))
    if slack:
        if n <= 2:
            return 10
        if n <= 5:
            return 15
        return 20
    if mail:
        if n <= 2:
            return 10
        if n <= 4:
            return 15
        return 20
    return 15


def _title_case_label(text: str) -> str:
    """Custom Today's plan titles: Take New Cases, not Take new cases. Keep DNS, IDM."""

    def cap(match: re.Match) -> str:
        word = match.group(0)
        if len(word) > 1 and word.isupper():
            return word
        return word[:1].upper() + word[1:]

    return re.sub(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", cap, text or "")


def _work_row(row_id: str, kind: str, label: str, start: datetime, end: datetime, src=None, detail=""):
    minutes = max(10, int((end - start).total_seconds() // 60)) if end > start else 20
    row = {
        "id": row_id,
        "kind": kind,
        "label": _title_case_label(label),
        "detail": detail or (_one_line((src or {}).get("detail")) if src else ""),
        "startStamp": _to_stamp(start),
        "endStamp": _to_stamp(end),
        "composed": True,
        "durationAdjustable": True,
        "durationMinutes": minutes,
        "asTask": kind in ("plan", "task", "case"),
    }
    if src:
        for key in ("caseNumber", "caseUrl", "summary", "chronology", "peek", "promisedClose", "promisedCloseOn"):
            if src.get(key) not in (None, "", False):
                row[key] = src[key]
    return {k: v for k, v in row.items() if v != "" and v is not False}


def compose_work_blocks(data: dict, now: datetime | None = None) -> dict:
    """Today's plan = Google meetings plus named work windows in free gaps.

    When aiAnalyzed, place only the model's todayPlan into shift gaps from now through
    logout. Python does not invent Slack / Mail / new-cases / work blocks. Leftover
    holes may stay free. Python does not invent meals.
    """
    if not isinstance(data, dict):
        return data
    drop_case_comment_mail(data)
    drop_invented_meetings(data)
    merge_primary_events_into_plan(data)
    sections = data.get("sections")
    if not isinstance(sections, list):
        sections = []
        data["sections"] = sections
    plan = None
    for sec in sections:
        if isinstance(sec, dict) and re.search(
            r"today['’]?s plan|^calendar$|^day plan|rest of day",
            _section_key(sec.get("title")),
            re.I,
        ):
            plan = sec
            break
    if plan is None:
        plan = {"title": "Today's plan", "open": True, "items": []}
        sections.append(plan)
    plan["title"] = "Today's plan"
    plan["open"] = True
    if not str(data.get("shiftStart") or "").strip() or not str(data.get("shiftEnd") or "").strip():
        keep = [
            it
            for it in (plan.get("items") or [])
            if isinstance(it, dict) and not _is_composed_row(it) and _is_keep_meeting(it)
        ]
        plan["items"] = keep
        if keep:
            plan.pop("empty", None)
        else:
            plan["empty"] = "No timed meetings on the clock."
        return data
    overrides = _harvest_work_durations(data)
    done_keys = collect_done_keys(data)
    start, end = _shift_window_today(data)
    now_floor = _plan_now_wall(data, start, end, now=now)
    mid_shift = now_floor > start + timedelta(minutes=20)
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    pad_start, pad_end = _shift_pad_today(data)
    items = [it for it in (plan.get("items") or []) if isinstance(it, dict) and not _is_composed_row(it)]
    meetings = []
    seen_eid: set[str] = set()
    for it in items:
        if not _is_keep_meeting(it):
            continue
        keep = _overlaps_range(it, pad_start, pad_end) or (
            it.get("important") is True and _same_day(it, day)
        )
        if not keep:
            continue
        eid = str(it.get("eventId") or "").strip()
        key = eid or (str(it.get("startStamp") or "") + "|" + str(it.get("label") or ""))
        if key in seen_eid:
            continue
        seen_eid.add(key)
        meetings.append(it)
    occupied = _occupied_ranges(meetings)
    work_lo = start if now_floor <= start else now_floor
    gaps = _gap_list(work_lo, end, occupied)
    placed = []
    after_login = start
    meals = sorted(
        (it for it in meetings if MEAL_TITLE_RE.search(str(it.get("label") or "").strip())),
        key=lambda it: _wall(it.get("startStamp") or "") or start,
    )
    for meal in meals:
        meal_start = _wall(meal.get("startStamp") or "")
        meal_end = _wall(meal.get("endStamp") or "")
        if meal_start and meal_end and meal_start < start + timedelta(hours=3):
            after_login = max(after_login, meal_end)
            break

    def place(minutes, row, *, after=None, avoid_buffer=False, shrink=15, done=False) -> bool:
        need = _clamp_work_mins(minutes) or 20
        after_at = after if after is not None else start
        if not done:
            after_at = max(after_at, now_floor)
        slot = _take_gap(gaps, need, occupied, after=after_at, avoid_buffer=avoid_buffer)
        if not slot and shrink:
            slot = _take_gap(
                gaps, max(10, need - shrink), occupied, after=after_at, avoid_buffer=avoid_buffer
            )
        if not slot:
            slot = _take_partial_gap(gaps, need, occupied, after=after_at)
        if not slot:
            return False
        used_mins = max(10, int((slot[1] - slot[0]).total_seconds() // 60))
        row["startStamp"] = _to_stamp(slot[0])
        row["endStamp"] = _to_stamp(slot[1])
        row["durationMinutes"] = used_mins
        row["durationAdjustable"] = True
        row["composed"] = True
        keys = item_done_keys(row)
        if done or (done_keys and any(k in done_keys for k in keys)):
            row["done"] = True
        placed.append(row)
        return True

    def _release_placed(row: dict) -> None:
        if row in placed:
            placed.remove(row)
        slot_start = _wall(row.get("startStamp") or "")
        slot_end = _wall(row.get("endStamp") or "")
        if not slot_start or not slot_end:
            return
        occupied[:] = [
            slot
            for slot in occupied
            if not (slot[0] == slot_start and slot[1] == slot_end)
        ]
        gaps.append([slot_start, slot_end])
        gaps.sort(key=lambda gap: gap[0])
        merged: list = []
        for lo, hi in gaps:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        gaps[:] = merged

    now_cases, follow_cases = _collect_ranked_cases(data)
    used = set()

    def place_new_cases() -> None:
        new_mins = overrides.get("plan-new-cases") or (15 if mid_shift else 25)
        place(
            new_mins,
            _work_row(
                "plan-new-cases",
                "plan",
                "📥 New cases",
                start,
                start + timedelta(minutes=new_mins),
                detail="Intake and first responses. Work the new queue here — not a case dump on this clock.",
            ),
            after=after_login,
            avoid_buffer=True,
            shrink=10,
        )

    def place_slack() -> None:
        slack_total, slack_open = _section_counts(
            data, lambda sec: SLACK_SEC_RE.search(_section_key(sec.get("title")))
        )
        if not slack_total:
            return
        slack_done = slack_open == 0
        slack_mins = overrides.get("plan-slack") or _queue_block_minutes(slack_open, slack=True)
        detail = (
            "All leftover threads handled."
            if slack_done
            else f"{slack_open} leftover thread{'s' if slack_open != 1 else ''} — open Slack, do not park cases here."
        )
        place(
            slack_mins,
            _work_row(
                "plan-slack",
                "plan",
                "💬 Slack",
                start,
                start + timedelta(minutes=slack_mins),
                detail=detail,
            ),
            after=after_login,
            done=slack_done,
        )

    def place_mail() -> None:
        mail_total, mail_open = _section_counts(
            data, lambda sec: MAIL_SEC_RE.search(_section_key(sec.get("title")))
        )
        if not mail_total:
            return
        mail_done = mail_open == 0
        mail_mins = overrides.get("plan-mail") or _queue_block_minutes(mail_open, mail=True)
        detail = (
            "All leftover mail handled."
            if mail_done
            else f"{mail_open} leftover email{'s' if mail_open != 1 else ''} — review and reply. Do not park cases here."
        )
        place(
            mail_mins,
            _work_row(
                "plan-mail",
                "plan",
                "✉️ Mail",
                start,
                start + timedelta(minutes=mail_mins),
                detail=detail,
            ),
            after=after_login,
            done=mail_done,
        )

    def place_needs() -> None:
        for src in now_cases[:5]:
            num = _case_num(src)
            if num:
                used.add(num)
            row_id = "plan-case-" + (num or str(len(placed) + 1))
            minutes = overrides.get(row_id) or _now_default_minutes(src)
            case_done = _item_is_done(src, done_keys)
            row = _work_row(
                row_id,
                "case",
                _named_now_label(src),
                start,
                start + timedelta(minutes=minutes),
                src,
                detail=_one_line(
                    src.get("detail")
                    or "Needs us now — named window. Stretch or shrink with −15/−5/+5/+15.",
                    160,
                ),
            )
            row["importantWork"] = True
            place(minutes, row, avoid_buffer=True, shrink=15, done=case_done)

    def place_follow() -> None:
        follow_all = [it for it in follow_cases if _case_num(it) not in used]
        follow_open = [it for it in follow_all if not _item_is_done(it, done_keys)]
        follow_left = follow_open or follow_all
        follow_done = bool(follow_all) and not follow_open
        for src in follow_left:
            num = _case_num(src)
            row_id = "plan-follow-" + (num or "1")
            minutes = overrides.get(row_id) or 10
            if row_id not in overrides:
                minutes = 10
            lab = "Follow-up — " + _one_line(src.get("label") or f"#{num}", 80)
            place(
                minutes,
                _work_row(row_id, "case", lab, start, start + timedelta(minutes=minutes), src),
                done=follow_done or _item_is_done(src, done_keys),
            )

    def place_ai_today_plan() -> None:
        specs = [x for x in (data.get("todayPlan") or []) if isinstance(x, dict)]

        def _plan_rank(spec: dict) -> int:
            rid = str(spec.get("id") or "")
            kind = str(spec.get("kind") or "").strip().lower()
            label = str(spec.get("label") or "").strip().lower()
            if "plan-new-cases" in rid or "new cases" in label:
                return 0
            if kind in ("break", "pause") or "plan-break" in rid or label.startswith("short break"):
                return 2
            if "plan-open" in rid or label in ("open", "free"):
                return 3
            return 1

        specs.sort(key=_plan_rank)
        hours = str(data.get("assembledSchedule") or "").strip()
        assembled = (
            data.get("assembledFromCalendar") is True
            and bool(hours)
            and hours.lower() != "nothing scheduled"
        )
        if assembled and str(data.get("daypart") or "").lower() != "eod" and not any(
            _plan_rank(spec) == 0 for spec in specs
        ):
            place_new_cases()
        max_short = 4
        short_n = 0
        last_break_end = None
        skip_until = [None]
        meal_windows = []
        for meal in meetings:
            if not MEAL_TITLE_RE.search(str(meal.get("label") or "").strip()):
                continue
            meal_start = _wall(meal.get("startStamp") or "")
            meal_end = _wall(meal.get("endStamp") or "")
            if meal_start and meal_end:
                meal_windows.append((meal_start, meal_end))

        def _break_against_meal(start_at: datetime, minutes: int) -> bool:
            """A meal is already a break. Do not sit a short break against it."""
            end_at = start_at + timedelta(minutes=minutes)
            pad = timedelta(minutes=45)
            for meal_start, meal_end in meal_windows:
                if meal_end <= start_at < meal_end + pad:
                    return True
                if meal_start - pad < end_at <= meal_start:
                    return True
                if start_at < meal_end and end_at > meal_start:
                    return True
            return False

        def _break_after(minutes: int) -> datetime | None:
            after_at = max(after_login, work_lo + timedelta(minutes=60))
            if last_break_end is not None:
                after_at = max(after_at, last_break_end + timedelta(minutes=45))
            if skip_until[0] is not None:
                after_at = max(after_at, skip_until[0])
            for lo, hi in list(gaps):
                start_at = lo if lo > after_at else after_at
                for _ in range(6):
                    if start_at + timedelta(minutes=10) > hi:
                        break
                    if not _break_against_meal(start_at, minutes):
                        return start_at
                    jumped = False
                    for meal_start, meal_end in meal_windows:
                        if meal_start - timedelta(minutes=45) <= start_at <= meal_end + timedelta(minutes=45):
                            start_at = meal_end + timedelta(minutes=45)
                            jumped = True
                            break
                    if not jumped:
                        break
            return None
        for spec in specs[:36]:
            row_id = str(spec.get("id") or "").strip()
            num = re.sub(r"\D", "", str(spec.get("caseNumber") or ""))
            if not row_id:
                row_id = f"plan-case-{num}" if len(num) >= 6 else ""
            if not row_id:
                continue
            if not row_id.startswith("plan-"):
                row_id = "plan-" + row_id
            src = _find_case_row(data, num) if len(num) >= 6 else None
            label = str(spec.get("label") or "").strip() or str(
                (src or {}).get("label") or ""
            ).strip()
            if not label:
                continue
            if str(data.get("daypart") or "").lower() == "eod" and (
                "plan-new-cases" in row_id or "new cases" in label.lower()
            ):
                continue
            if "plan-open" in row_id or label.lower() in ("open", "free"):
                continue
            detail = str(spec.get("detail") or "").strip()
            kind = str(spec.get("kind") or "").strip().lower()
            if kind in ("break", "pause"):
                kind = "break"
            elif kind not in ("plan", "task", "case"):
                kind = "case" if len(num) >= 6 else "plan"
            if kind == "break" and short_n >= max_short:
                continue
            meal_hit = re.search(r"\b(breakfast|dinner|lunch)\b", label, re.I)
            if meal_hit:
                have = False
                for meet in meetings:
                    blob = str(meet.get("label") or "")
                    if re.search(r"\b" + re.escape(meal_hit.group(1)) + r"\b", blob, re.I):
                        have = True
                        break
                if have:
                    continue
            minutes = (
                overrides.get(row_id)
                or _clamp_work_mins(
                    spec.get("minutes") or spec.get("durationMinutes"),
                    10,
                    15 if kind == "break" else 180,
                )
                or (12 if kind == "break" else 20)
            )
            if row_id not in overrides:
                if str(row_id).startswith("plan-follow"):
                    minutes = max(5, min(10, int(minutes or 10)))
                elif str(row_id).startswith("plan-case-"):
                    minutes = max(20, min(30, int(minutes or 25)))
            row = _work_row(
                row_id,
                kind,
                label,
                start,
                start + timedelta(minutes=minutes),
                src,
                detail=detail,
            )
            if kind == "case":
                row["importantWork"] = True
            done = _item_is_done(src or row, done_keys)
            if row_id == "plan-slack" and _is_queue_leftover_plan_row(row):
                total, open_n = _section_counts(
                    data, lambda sec: SLACK_SEC_RE.search(_section_key(sec.get("title")))
                )
                if not total:
                    continue
                done = open_n == 0
            elif row_id == "plan-mail" and _is_queue_leftover_plan_row(row):
                total, open_n = _section_counts(
                    data, lambda sec: MAIL_SEC_RE.search(_section_key(sec.get("title")))
                )
                if not total:
                    continue
                done = open_n == 0
            elif row_id == "plan-follow" and _is_queue_leftover_plan_row(row):
                total, open_n = _section_counts(
                    data,
                    lambda sec: re.search(
                        r"follow-up due|needs a touch",
                        _section_key(sec.get("title")),
                        re.I,
                    ),
                )
                done = total > 0 and open_n == 0
            after = None
            if row_id in ("plan-slack", "plan-mail", "plan-new-cases"):
                after = after_login
            elif kind == "break" or row_id.startswith("plan-break"):
                if short_n >= max_short:
                    continue
                skip_until[0] = None
                kept = False
                for _ in range(6):
                    after = _break_after(minutes)
                    if after is None:
                        break
                    if not place(minutes, row, after=after, shrink=15, done=done):
                        break
                    start_at = _wall(row.get("startStamp") or "")
                    used = int(row.get("durationMinutes") or minutes)
                    too_close = (
                        last_break_end is not None
                        and start_at is not None
                        and start_at < last_break_end + timedelta(minutes=45)
                    )
                    if start_at and (_break_against_meal(start_at, used) or too_close):
                        _release_placed(row)
                        skip_until[0] = start_at + timedelta(minutes=max(used, 45))
                        continue
                    short_n += 1
                    last_break_end = _wall(row.get("endStamp") or "") or last_break_end
                    skip_until[0] = None
                    kept = True
                    break
                if not kept:
                    continue
                continue
            elif row_id == "plan-close":
                after = max(after_login, end - timedelta(minutes=minutes + 10))
                row["promisedClose"] = True
            avoid = kind == "case" or row_id in ("plan-new-cases", "plan-close")
            place(minutes, row, after=after, avoid_buffer=avoid, shrink=15, done=done)

    def place_remaining_queue_into_holes() -> None:
        """Slack/Mail if missing. Assembled: New cases once if omitted. Rest may stay free."""
        placed_ids = {str(r.get("id") or "") for r in placed}
        if "plan-slack" not in placed_ids:
            place_slack()
        if "plan-mail" not in placed_ids:
            place_mail()
        hours = str(data.get("assembledSchedule") or "").strip()
        assembled = data.get("assembledFromCalendar") is True and hours and hours.lower() != "nothing scheduled"
        if (
            assembled
            and str(data.get("daypart") or "").lower() != "eod"
            and "plan-new-cases" not in {str(r.get("id") or "") for r in placed}
        ):
            place_new_cases()

    if data.get("aiAnalyzed") is True:
        place_ai_today_plan()
        _ensure_close_logoff(data, _promised_close_today(data))
        _ensure_tomorrow_closes(data)
    else:
        if mid_shift:
            place_needs()
            place_follow()
            place_slack()
            place_mail()
            if str(data.get("daypart") or "").lower() != "eod":
                place_new_cases()
        elif str(data.get("daypart") or "").lower() != "eod":
            place_new_cases()
            place_slack()
            place_mail()
            place_needs()
            place_follow()
        else:
            place_slack()
            place_mail()
            place_needs()
            place_follow()

        close_cases = _promised_close_today(data)
        _ensure_close_logoff(data, close_cases)
        close_cases = _promised_close_today(data)
        if close_cases:
            open_n = sum(1 for it in close_cases if it.get("done") is not True)
            close_mins = overrides.get("plan-close") or min(30, max(10, 8 * len(close_cases)))
            names = "; ".join(_one_line(it.get("label"), 70) for it in close_cases[:8])
            more = "" if len(close_cases) <= 8 else f" (+{len(close_cases) - 8} more)"
            pretty_today = _pretty_close_day(_shift_today_iso(data))
            if len(close_cases) == 1:
                src = close_cases[0]
                num = _case_num(src)
                label = "🔒 Close #" + num if num else "🔒 Case closure"
                detail = _one_line(
                    src.get("detail")
                    or f"Promised close {pretty_today}. Close if still quiet — do not invent new close language.",
                    160,
                )
                row = _work_row(
                    "plan-close",
                    "case",
                    label,
                    start,
                    start + timedelta(minutes=close_mins),
                    src,
                    detail=detail,
                )
            else:
                row = _work_row(
                    "plan-close",
                    "plan",
                    "🔒 Case closures",
                    start,
                    start + timedelta(minutes=close_mins),
                    detail=(
                        f"{len(close_cases)} promised close{'s' if len(close_cases) != 1 else ''} "
                        f"due {pretty_today}. {names}{more}. "
                        "Close if still quiet — do not invent new close language."
                    ),
                )
            row["promisedClose"] = True
            late = end - timedelta(minutes=close_mins + 10)
            close_done = open_n == 0
            ok = place(
                close_mins,
                row,
                after=max(after_login, late),
                shrink=10,
                done=close_done,
            )
            if not ok:
                place(close_mins, row, shrink=10, done=close_done)

        _ensure_tomorrow_closes(data)

    clock = meetings + placed
    clock.sort(key=lambda it: _wall(it.get("startStamp") or "") or datetime.min)
    plan["items"] = clock
    if clock:
        plan.pop("empty", None)
    if len(overrides) > 80:
        keep_ids = {str(row.get("id") or "") for row in placed}
        overrides = {k: v for k, v in overrides.items() if k in keep_ids}
    data["workDurations"] = overrides
    sync_queue_plan_blocks(data)
    dedupe_closeout_sections(data)
    return data


def sanitize(data: dict) -> dict:
    if not isinstance(data, dict):
        return data
    coerce_row_items(data)
    canonicalize_titles(data)
    fix_laptop_ist_clock(data)
    stamp_clock(data)
    inject_facts(data)
    fill_identity(data)
    fill_slack_urls(data)
    fill_mail_urls(data)
    repair_mail_labels(data)
    drop_case_comment_mail(data)
    peel_stray_inbox_rows(data)
    refuse_false_inbox_clear(data)
    refuse_false_gus_clear(data)
    normalize_inbox_buckets(data)
    relabel_slack_dms(data)
    drop_invented_meetings(data)
    organize_plan(data)
    split_bunched_cases(data)
    stamp_case_our_updates(data)
    drop_gus_notices_from_slack(data)
    ensure_gus_bot_rows(data)
    ensure_lap_in_gus(data)
    hoist_peek_fields(data)
    apply_ai_case_buckets(data)
    apply_ai_closeout(data)
    apply_ai_tomorrow(data)
    data.pop("_closeCaseRows", None)
    drop_unowned_cases(data)
    stamp_needs_when(data)
    normalize_chronology(data)
    drop_done_from_plan(data)
    drop_solution_provided_from_plan(data)
    compose_work_blocks(data)
    apply_case_holds(data)
    demote_solution_provided_from_now(data)
    drop_solution_provided_from_plan(data)
    drop_unowned_cases(data)
    ensure_required_sections(data)
    drop_empty_optional_sections(data)
    apply_notepad(data)
    sort_sections(data)
    omit_done_inbox_rows(data)
    drop_gus_notices_from_slack(data)
    ensure_gus_bot_rows(data)
    return data
