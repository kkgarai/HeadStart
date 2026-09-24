#!/usr/bin/env python3
"""Ingest a Claude Code overflow tool-result file.

Default: merge clipped CaseComment/EmailMessage rows into /tmp/case-activity.json
and print `ok parents=N records=M` only. Never dump CommentBody to stdout — that
refill is what compact-kills Run Planner.

--print keeps the old short JSON on stdout (debug).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

ACTIVITY_MERGE = "/tmp/case-activity.json"
DIGEST_FILE = "/tmp/case-digest.json"
GATHER_PATH = "/tmp/planner-gather.json"
PER_PARENT = 12
TEXT_N = 80
# Full cleaned note (quotes/signature stripped). Bomb-stop only — not an 80-char stub.
DIGEST_TEXT_N = 4000
MAX_ACTIVITY_OUT = 400_000
DIGEST_TXT = "/tmp/planner-digest.txt"
MAX_DIGEST = 80_000
OVERFLOW_STUB = '{"truncated":true,"merged":true}\n'
CARDS_FILE = "/tmp/planner-cards.txt"
STUB_AFTER = 800
SKIP_BODY = re.compile(
    r"HTIR Integration|Hyperforce\s*-",
    re.I,
)

MAX_OUT = 20_000
MAX_STR = 160
DROP_KEYS = {
    "attributes",
    "description",
    "htmlbody",
    "textbody",
    "content",
    "rawbody",
    "message",
}
CLIP_KEYS = {"commentbody", "body"}
CASE_KEYS = (
    "Id",
    "CaseNumber",
    "Subject",
    "Status",
    "Severity_Level__c",
    "CreatedDate",
    "LastModifiedDate",
)


def load_blob(path: str):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = raw.lstrip("\ufeff")
    raw = re.sub(r"^\s*\d+\s+", "", raw, count=1)
    start = raw.find("{")
    if start < 0:
        start = raw.find("[")
    if start >= 0:
        raw = raw[start:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}


def clip(val, n: int = MAX_STR):
    if val is None or isinstance(val, (int, float, bool)):
        return val
    if isinstance(val, dict):
        if "Name" in val:
            return val.get("Name")
        if "name" in val:
            return val.get("name")
        return {k: clip(v, 80) for k, v in list(val.items())[:6]}
    text = re.sub(r"\s+", " ", str(val)).strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_ci(row: dict, name: str):
    if name in row:
        return row.get(name)
    want = name.lower()
    for key, val in row.items():
        if str(key).lower() == want:
            return val
    return None


def is_case_list(recs: list) -> bool:
    if not recs:
        return False
    hits = sum(1 for row in recs if get_ci(row, "CaseNumber"))
    return hits >= max(1, (len(recs) + 1) // 2)


def slim_case(row: dict, subj_n: int) -> dict:
    out = {}
    for key in CASE_KEYS:
        val = get_ci(row, key)
        if val is None:
            continue
        if key == "Subject":
            if subj_n <= 0:
                continue
            out[key] = clip(val, subj_n)
        else:
            out[key] = clip(val, 80)
    return out


def pack_cases(recs: list, total) -> dict:
    omitted_nums = []
    for subj_n in (72, 48, 24, 0):
        rows = [slim_case(row, subj_n) for row in recs]
        payload = {
            "totalSize": total,
            "shown": len(rows),
            "omitted": 0,
            "records": rows,
        }
        if len(dump(payload)) <= MAX_OUT:
            return payload
    tiny = []
    for row in recs:
        tiny.append(
            {
                "Id": clip(get_ci(row, "Id"), 24),
                "CaseNumber": clip(get_ci(row, "CaseNumber"), 16),
                "Severity_Level__c": clip(get_ci(row, "Severity_Level__c"), 40),
                "Status": clip(get_ci(row, "Status"), 40),
                "LastModifiedDate": clip(get_ci(row, "LastModifiedDate"), 32),
            }
        )
    payload = {"totalSize": total, "shown": len(tiny), "omitted": 0, "records": tiny}
    blob = dump(payload)
    while len(blob) > MAX_OUT and payload["records"]:
        drop = payload["records"].pop()
        num = drop.get("CaseNumber") or drop.get("Id")
        if num:
            omitted_nums.append(str(num))
        payload["shown"] = len(payload["records"])
        payload["omitted"] = len(omitted_nums)
        payload["omittedIds"] = omitted_nums[-80:]
        blob = dump(payload)
    return payload


def slim_activity(row: dict) -> dict:
    out = {}
    for key, val in row.items():
        lk = str(key).lower()
        if lk in DROP_KEYS:
            continue
        if lk in CLIP_KEYS:
            if val:
                out[key] = clip(val, 100)
            continue
        if isinstance(val, dict) and ("Name" in val or "name" in val):
            out[key] = clip(val)
            continue
        if lk in {
            "id",
            "parentid",
            "casenumber",
            "createddate",
            "ispublished",
            "incoming",
            "messagedate",
            "fromaddress",
            "subject",
            "status",
            "type",
            "type__c",
            "case__c",
            "gustext__c",
            "work_status__c",
            "work_subject__c",
            "lap_global_case_number__c",
            "lap_type__c",
            "subject__c",
            "status__c",
            "due_date__c",
        }:
            out[key] = clip(val, 100)
    return out


def pack_activity(recs: list, total) -> dict:
    grouped = {}
    for row in recs:
        parent = str(get_ci(row, "ParentId") or get_ci(row, "Id") or "_")
        grouped.setdefault(parent, []).append(row)
    omitted = []
    for per in (3, 2, 1):
        picked = []
        for rows in grouped.values():
            picked.extend(rows[:per])
        payload = {
            "totalSize": total,
            "shown": len(picked),
            "parents": len(grouped),
            "omitted": 0,
            "records": [slim_activity(row) for row in picked],
        }
        if len(dump(payload)) <= MAX_OUT:
            return payload
    keys = list(grouped.keys())
    while keys:
        picked = [grouped[k][0] for k in keys]
        payload = {
            "totalSize": total,
            "shown": len(picked),
            "parents": len(grouped),
            "omitted": len(omitted),
            "omittedIds": omitted[-80:],
            "records": [slim_activity(row) for row in picked],
        }
        if len(dump(payload)) <= MAX_OUT:
            return payload
        omitted.append(keys.pop())
    return {"totalSize": total, "shown": 0, "omitted": len(omitted), "omittedIds": omitted[-80:], "records": []}


GMAIL_CHUNK = re.compile(r"(?=Message ID:)", re.I)
SLACK_RESULT = re.compile(r"(?=### Result \d+)", re.I)


def pack_gmail_text(text: str) -> dict:
    chunks = [c for c in GMAIL_CHUNK.split(text) if "Message ID:" in c]
    rows = []
    for chunk in chunks:
        mid = re.search(r"Message ID:\s*(\S+)", chunk, re.I)
        subj = re.search(r"Subject:\s*(.+)", chunk, re.I)
        frm = re.search(r"From:\s*(.+)", chunk, re.I)
        date = re.search(r"Date:\s*(.+)", chunk, re.I)
        link = re.search(r"(?:Web Link|Link):\s*(\S+)", chunk, re.I)
        rows.append(
            {
                "id": clip(mid.group(1), 24) if mid else None,
                "subject": clip(subj.group(1), 80) if subj else None,
                "from": clip(frm.group(1), 60) if frm else None,
                "date": clip(date.group(1), 40) if date else None,
                "link": clip(link.group(1), 120) if link else None,
            }
        )
    payload = {
        "kind": "gmail",
        "totalSize": len(rows),
        "shown": len(rows),
        "omitted": 0,
        "records": rows,
    }
    blob = dump(payload)
    while len(blob) > MAX_OUT and payload["records"]:
        drop = payload["records"].pop()
        payload["shown"] = len(payload["records"])
        payload["omitted"] = payload["totalSize"] - payload["shown"]
        omitted = payload.setdefault("omittedIds", [])
        if drop.get("id"):
            omitted.append(str(drop["id"]))
            payload["omittedIds"] = omitted[-80:]
        blob = dump(payload)
    return payload


def is_gmail_text(text: str) -> bool:
    if not text:
        return False
    hits = text.count("Message ID:")
    return hits >= 2 and ("Subject:" in text or "MESSAGES" in text.upper())


def is_slack_text(text: str) -> bool:
    if not text:
        return False
    if "### Result" in text and ("Channel:" in text or "Permalink:" in text):
        return True
    if "Search Results for:" in text and ("Channel:" in text or " is:dm" in text or "<@" in text):
        return True
    return False


def pack_slack_text(text: str) -> dict:
    rows = []
    chunks = [c for c in SLACK_RESULT.split(text) if "Channel:" in c or "Permalink:" in c]
    for chunk in chunks:
        ch = re.search(r"Channel:\s*(.+)", chunk)
        cid = re.search(r"\(ID:\s*([A-Z0-9]+)\)", chunk)
        frm = re.search(r"From:\s*(.+)", chunk)
        ts = re.search(r"Message_ts:\s*(\S+)", chunk)
        link = re.search(
            r"Permalink:\s*\[link\]\((https?://[^)]+)\)", chunk
        ) or re.search(r"(https://[^\s)]+slack\.com/archives/[^\s)]+)", chunk)
        body = re.search(r"Text:\s*(.*?)(?:\n---|\Z)", chunk, re.S)
        from_name = ""
        if frm:
            from_name = re.split(r"[<(]", frm.group(1), maxsplit=1)[0].strip()
        rows.append(
            {
                "channel": clip(ch.group(1).split("(ID:")[0].strip(), 80) if ch else None,
                "channelId": cid.group(1) if cid else None,
                "from": clip(from_name, 60) or None,
                "ts": ts.group(1) if ts else None,
                "permalink": clip(link.group(1), 180) if link else None,
                "text": clip(body.group(1), 120) if body else None,
            }
        )
    payload = {
        "kind": "slack",
        "totalSize": len(rows),
        "shown": len(rows),
        "omitted": 0,
        "records": rows,
    }
    blob = dump(payload)
    while len(blob) > MAX_OUT and payload["records"]:
        drop = payload["records"].pop()
        payload["shown"] = len(payload["records"])
        payload["omitted"] = payload["totalSize"] - payload["shown"]
        omitted = payload.setdefault("omittedIds", [])
        if drop.get("channelId") or drop.get("permalink"):
            omitted.append(str(drop.get("channelId") or drop.get("permalink")))
            payload["omittedIds"] = omitted[-80:]
        blob = dump(payload)
    return payload


def records_of(data):
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    rec = data.get("records")
    if isinstance(rec, list):
        return [row for row in rec if isinstance(row, dict)]
    for key in ("result", "data", "output", "content"):
        inner = data.get(key)
        if isinstance(inner, str) and inner.strip()[:1] in "{[":
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                continue
        got = records_of(inner) if inner is not None else []
        if got:
            return got
    return []


def pt_when(iso: str, tzname: str) -> str:
    """Calendar date and time in GMT. Weekday labels are not stored."""
    del tzname
    raw = str(iso or "").strip()
    if not raw:
        return ""
    raw = raw.replace(".000+0000", "+00:00").replace("+0000", "+00:00").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return clip(raw, 40)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    hour = utc.strftime("%I").lstrip("0") or "12"
    return f"{utc.strftime('%b')} {utc.day}, {utc.year}, {hour}:{utc.strftime('%M %p')} GMT"


def substance(body: str) -> str:
    text = re.sub(r"(?i)<Created By:[^>]+>", " ", body or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return clip(text, DIGEST_TEXT_N) or ""


def event_kind(row: dict) -> str | None:
    incoming = get_ci(row, "Incoming")
    is_email = incoming is not None and str(incoming).strip() != ""
    body = str(get_ci(row, "CommentBody") or get_ci(row, "TextBody") or get_ci(row, "Body") or "")
    if not is_email and SKIP_BODY.search(body):
        return None
    if incoming is True or incoming == True or str(incoming).lower() in {"true", "1"}:
        return "customer"
    if re.search(r"<Created By:", body, re.I):
        return "customer"
    published = get_ci(row, "IsPublished")
    if published is False or str(published).lower() in {"false", "0"}:
        return "internal"
    return "public"


def row_to_event(row: dict, tzname: str) -> dict | None:
    who_obj = get_ci(row, "CreatedBy")
    if isinstance(who_obj, dict):
        who = str(who_obj.get("Name") or who_obj.get("name") or "").strip()
    else:
        who = str(who_obj or "").strip()
    if not who:
        who = str(get_ci(row, "FromName") or get_ci(row, "FromAddress") or "").strip()
    body = str(
        get_ci(row, "CommentBody")
        or get_ci(row, "TextBody")
        or get_ci(row, "Body")
        or get_ci(row, "Subject")
        or ""
    )
    kind = event_kind(row)
    if not kind:
        return None
    text = substance(body)
    if not text:
        return None
    ts = str(get_ci(row, "CreatedDate") or get_ci(row, "MessageDate") or "")
    return {
        "when": pt_when(ts, tzname),
        "who": clip(who, 80) or "",
        "kind": kind,
        "text": text,
        "_ts": ts,
    }


def gather_meta() -> tuple[dict, str]:
    mapping = {}
    tzname = ""
    try:
        data = json.loads(open(GATHER_PATH, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return mapping, tzname
    if not isinstance(data, dict):
        return mapping, tzname
    tzname = str(data.get("timezone") or tzname)
    for row in data.get("cases") or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("caseNumber") or "").strip()
        cid = str(row.get("id") or "").strip()
        if cid and num:
            mapping[cid] = num
        url = str(row.get("caseUrl") or "")
        match = re.search(r"/Case/(500[A-Za-z0-9]+)/", url)
        if match and num:
            mapping[match.group(1)] = num
    return mapping, tzname


def load_activity(path: str) -> dict:
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {"byId": {}, "byNumber": {}, "relatedById": {}}
    if not isinstance(data, dict):
        return {"byId": {}, "byNumber": {}, "relatedById": {}}
    mapping, _tz = gather_meta()
    reverse = {num: cid for cid, num in mapping.items()}
    by_id = {}
    for cid, events in (data.get("byId") or {}).items():
        if isinstance(events, list):
            by_id[str(cid)] = [row for row in events if isinstance(row, dict)]
    for num, events in (data.get("byNumber") or {}).items():
        cid = reverse.get(str(num))
        if not cid or not isinstance(events, list):
            continue
        bag = by_id.setdefault(cid, [])
        keys = {
            (e.get("when"), e.get("who"), str(e.get("text") or "")[:80])
            for e in bag
            if isinstance(e, dict)
        }
        for row in events:
            if not isinstance(row, dict):
                continue
            key = (row.get("when"), row.get("who"), str(row.get("text") or "")[:80])
            if key in keys:
                continue
            bag.append(row)
            keys.add(key)
        bag.sort(key=lambda item: str(item.get("_ts") or ""), reverse=True)
        by_id[cid] = bag[:PER_PARENT]
    related = {}
    for cid, rows in (data.get("relatedById") or {}).items():
        if isinstance(rows, list):
            related[str(cid)] = [str(x) for x in rows if str(x).strip()]
    for num, rows in (data.get("relatedByNumber") or {}).items():
        cid = reverse.get(str(num))
        if cid and cid not in related and isinstance(rows, list):
            related[cid] = [str(x) for x in rows if str(x).strip()]
    return {
        "byId": by_id,
        "byNumber": data.get("byNumber") if isinstance(data.get("byNumber"), dict) else {},
        "relatedById": related,
        "workByName": data.get("workByName") if isinstance(data.get("workByName"), dict) else {},
    }


def public_events(rows: list) -> list:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({k: row[k] for k in ("when", "who", "kind", "text", "_ts") if k in row and row.get(k) not in (None, "")})
    return out


def rebuild_numbers(activity: dict, mapping: dict) -> None:
    by_num = {}
    for cid, events in (activity.get("byId") or {}).items():
        num = mapping.get(cid)
        if num:
            by_num[num] = public_events(events)
    activity["byNumber"] = by_num
    activity["parents"] = len(activity.get("byId") or {})
    activity["records"] = sum(len(v) for v in (activity.get("byId") or {}).values())


def related_by_number(activity: dict, mapping: dict) -> dict:
    out = {}
    for cid, rows in (activity.get("relatedById") or {}).items():
        num = mapping.get(cid)
        if num and rows:
            out[num] = [clip(str(x), 160) for x in rows[:6] if str(x).strip()]
    return out


def activity_payload(activity: dict, mapping: dict) -> dict:
    rebuild_numbers(activity, mapping)
    mapped = set(mapping.keys())
    leftover = {
        cid: public_events(rows)
        for cid, rows in (activity.get("byId") or {}).items()
        if cid not in mapped
    }
    related_left = {
        cid: [clip(str(x), 160) for x in rows[:6]]
        for cid, rows in (activity.get("relatedById") or {}).items()
        if cid not in mapped and rows
    }
    return {
        "byNumber": activity.get("byNumber") or {},
        "byId": leftover,
        "relatedByNumber": related_by_number(activity, mapping),
        "relatedById": related_left,
        "parents": activity.get("parents") or 0,
        "records": activity.get("records") or 0,
    }


def shrink_activity(activity: dict, mapping: dict) -> None:
    # Never drop a comment or email to fit a size cap. A long body is already
    # cut at DIGEST_TEXT_N. A large digest is split into part files later.
    return


def _push_related(activity: dict, cid: str, line: str) -> None:
    line = clip(str(line or ""), 160)
    if not cid.startswith("500") or not line:
        return
    bag = activity.setdefault("relatedById", {}).setdefault(cid, [])
    if line not in bag:
        bag.append(line)
        activity["relatedById"][cid] = bag[:6]


def merge_related_row(activity: dict, row: dict, mapping: dict) -> bool:
    case_id = str(get_ci(row, "Case__c") or "")
    typ = str(get_ci(row, "Type__c") or "").strip()
    gus = str(get_ci(row, "GUSText__c") or get_ci(row, "Name") or "").strip()
    if case_id.startswith("500") and (typ or gus or get_ci(row, "Work_Status__c")):
        status = str(get_ci(row, "Work_Status__c") or "").strip()
        subj = str(get_ci(row, "Work_Subject__c") or get_ci(row, "Name") or "").strip()
        bits = [x for x in (typ or "related", gus, status, clip(subj, 60)) if x]
        _push_related(activity, case_id, " · ".join(bits))
        wname = gus if gus.upper().startswith("W-") else ""
        if wname:
            work = activity.setdefault("workByName", {})
            work.setdefault(wname, []).append(case_id)
        return True
    lap_num = str(get_ci(row, "LAP_Global_Case_Number__c") or "").strip()
    if lap_num:
        status = str(get_ci(row, "Status") or "").strip()
        ltype = str(get_ci(row, "LAP_type__c") or "").strip()
        lap_id = str(get_ci(row, "CaseNumber") or "").strip()
        line = " · ".join(x for x in ("LAP", lap_id or lap_num, status, ltype) if x)
        reverse = {num: cid for cid, num in mapping.items()}
        cid = reverse.get(lap_num) or reverse.get(re.sub(r"\D", "", lap_num))
        if cid:
            _push_related(activity, cid, line)
        return True
    wname = str(get_ci(row, "Name") or "").strip()
    if wname.upper().startswith("W-") and (get_ci(row, "Subject__c") or get_ci(row, "Status__c")):
        status = str(get_ci(row, "Status__c") or "").strip()
        subj = str(get_ci(row, "Subject__c") or "").strip()
        rtype = ""
        rec = get_ci(row, "RecordType")
        if isinstance(rec, dict):
            rtype = str(rec.get("Name") or "")
        line = " · ".join(x for x in ("Inv", wname, rtype, status, clip(subj, 60)) if x)
        for cid in activity.get("workByName", {}).get(wname) or []:
            _push_related(activity, cid, line)
        if not activity.get("workByName", {}).get(wname):
            for cid, bag in (activity.get("relatedById") or {}).items():
                if any(wname in str(x) for x in bag or []):
                    _push_related(activity, cid, line)
        return True
    return False


def merge_records(activity: dict, recs: list, mapping: dict, tzname: str) -> None:
    by_id = activity.setdefault("byId", {})
    for row in recs:
        if not isinstance(row, dict):
            continue
        if merge_related_row(activity, row, mapping):
            continue
        pid = str(get_ci(row, "ParentId") or "")
        if not pid.startswith("500"):
            continue
        ev = row_to_event(row, tzname)
        if not ev:
            continue
        bag = by_id.setdefault(pid, [])
        key = (ev.get("when"), ev.get("who"), str(ev.get("text") or "")[:80])
        if any((e.get("when"), e.get("who"), str(e.get("text") or "")[:80]) == key for e in bag):
            continue
        bag.append(ev)
        bag.sort(key=lambda item: str(item.get("_ts") or ""), reverse=True)
        if len(bag) > PER_PARENT:
            customers = [e for e in bag if e.get("kind") == "customer"]
            others = [e for e in bag if e.get("kind") != "customer"]
            keep = (customers[:4] + others)[:PER_PARENT]
            keep.sort(key=lambda item: str(item.get("_ts") or ""), reverse=True)
            by_id[pid] = keep
        else:
            by_id[pid] = bag
    shrink_activity(activity, mapping)


def write_digest(activity: dict, mapping: dict) -> None:
    """Pointer only. Peek is `--cards` stdout. A fat digest is what autocompact re-Reads."""
    payload = activity_payload(activity, mapping)
    n = len(payload.get("byNumber") or {})
    open(DIGEST_FILE, "w", encoding="utf-8").write(dump({"use": "--cards", "n": n}) + "\n")


def gather_case_headers() -> dict[str, str]:
    try:
        data = json.loads(open(GATHER_PATH, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for row in data.get("cases") or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("caseNumber") or "").strip()
        if not num:
            continue
        live = str(row.get("status") or row.get("detail") or "").strip()
        if live:
            out[num] = clip(live, 80)
    return out


def gather_case_numbers() -> list[str]:
    try:
        data = json.loads(open(GATHER_PATH, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for row in data.get("cases") or []:
        if not isinstance(row, dict):
            continue
        num = str(row.get("caseNumber") or "").strip()
        if num:
            out.append(num)
    return out


def gather_case_ids() -> list[str]:
    try:
        data = json.loads(open(GATHER_PATH, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    seen = set()
    for row in data.get("cases") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if cid.startswith("500") and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def card_line(num: str, events: list, related: list) -> str:
    bits = []
    for ev in (events or [])[:3]:
        if not isinstance(ev, dict):
            continue
        bits.append(
            f"{ev.get('when') or ''} {clip(ev.get('who') or '', 22)} {ev.get('kind') or ''}: "
            f"{clip(ev.get('text') or '', 72)}"
        )
    if related:
        bits.append("rel " + clip(" ; ".join(str(x) for x in related[:2]), 72))
    body = " | ".join(bits) if bits else "(no activity)"
    return clip(f"{num} {body}", 140)


def _first_kind(events: list, kind: str) -> dict | None:
    for ev in events or []:
        if isinstance(ev, dict) and ev.get("kind") == kind:
            return ev
    return None


def _event_sort_key(ev: dict) -> str:
    return str(ev.get("_ts") or "")


def _latest_kind(rows: list, kind: str) -> dict | None:
    found = [ev for ev in rows if ev.get("kind") == kind]
    if not found:
        return None
    return max(found, key=_event_sort_key)


def _digest_body(ev: dict) -> str:
    text = str(ev.get("text") or "")
    if len(text) > DIGEST_TEXT_N:
        text = text[: DIGEST_TEXT_N - 1].rstrip() + "…"
    return text


def _digest_beat_line(ev: dict) -> str:
    return (
        f"- {ev.get('when') or ''} {clip(ev.get('who') or '', 80)} "
        f"({ev.get('kind') or ''}): {_digest_body(ev)}"
    )


def _digest_event_line(prefix: str, ev: dict) -> str:
    return (
        f"- {prefix}: {ev.get('when') or ''} {clip(ev.get('who') or '', 80)} "
        f"({ev.get('kind') or ''}): {_digest_body(ev)}"
    )


def digest_block(num: str, events: list, related: list, live: str = "", extras: bool = True) -> str:
    lines = [f"## {num}"]
    if live:
        lines.append(f"- live: {clip(live, 80)}")
    rows = [ev for ev in (events or []) if isinstance(ev, dict)]
    last_c = _latest_kind(rows, "customer")
    last_p = _latest_kind(rows, "public")
    last_i = _latest_kind(rows, "internal")
    if last_c:
        lines.append(_digest_event_line("last customer", last_c))
    if last_p:
        lines.append(_digest_event_line("last public", last_p))
    if last_i and last_i is not last_c and last_i is not last_p:
        lines.append(_digest_event_line("last internal", last_i))
    if extras:
        for ev in sorted(rows, key=_event_sort_key, reverse=True):
            if ev is last_c or ev is last_p or ev is last_i:
                continue
            lines.append(_digest_beat_line(ev))
    if not last_c and not last_p and not last_i:
        lines.append("- (no clipped comment/email yet)")
    if related:
        lines.append("- related: " + clip(" ; ".join(str(x) for x in related[:3]), 200))
    return "\n".join(lines)


def print_digest() -> int:
    mapping, _tz = gather_meta()
    headers = gather_case_headers()
    activity = load_activity(ACTIVITY_MERGE)
    payload = activity_payload(activity, mapping)
    by_num = payload.get("byNumber") or {}
    related = payload.get("relatedByNumber") or {}
    nums = gather_case_numbers() or list(by_num.keys())
    def build(extras: bool) -> str:
        blocks = [
            digest_block(num, by_num.get(num) or [], related.get(num) or [], headers.get(num) or "", extras)
            for num in nums
        ]
        blob = "\n\n".join(blocks).strip()
        ids = gather_case_ids()
        if ids:
            blob = "ids: " + " ".join(ids) + ("\n\n" + blob if blob else "")
        return blob
    blob = build(True)
    header = f"ok digest={len(nums)} bytes={len(blob)}\n"
    sys.stdout.write(header)
    try:
        pathlib.Path(DIGEST_TXT).write_text(blob + ("\n" if blob else ""), encoding="utf-8")
    except OSError:
        pass
    print_cards(stdout=False)
    return 0


def print_cards(stdout: bool = True) -> int:
    mapping, _tz = gather_meta()
    activity = load_activity(ACTIVITY_MERGE)
    payload = activity_payload(activity, mapping)
    by_num = payload.get("byNumber") or {}
    related = payload.get("relatedByNumber") or {}
    nums = gather_case_numbers() or list(by_num.keys())
    lines = [
        card_line(num, by_num.get(num) or [], related.get(num) or [])
        for num in nums
    ]
    blob = "\n".join(lines)
    header = (
        f"ok cards={len(lines)}\n"
        "WRITE /tmp/plan-ai.json NOW. Do not Read overflow, digest, or ~/.claude/projects.\n"
    )
    if stdout:
        sys.stdout.write(header + blob + ("\n" if blob else ""))
    try:
        pathlib.Path(CARDS_FILE).write_text(blob + ("\n" if blob else ""), encoding="utf-8")
    except OSError:
        pass
    try:
        p = pathlib.Path(DIGEST_FILE)
        if p.is_file() and p.stat().st_size > 200:
            p.write_text('{"use":"--cards"}\n', encoding="utf-8")
    except OSError:
        pass
    return 0


def stub_overflow_source(path: str) -> None:
    """Keep Claude Code autocompact from re-reading 80k+ CommentBody dumps."""
    raw = str(path or "").strip()
    if not raw.startswith("/") or ".." in raw:
        return
    try:
        p = pathlib.Path(raw)
        if p.is_file() and p.stat().st_size > STUB_AFTER:
            p.write_text(OVERFLOW_STUB, encoding="utf-8")
    except OSError:
        pass


def sweep_tool_results(anchor: str) -> None:
    """Slim+stub every large file in the same tool-results folder (parallel overflows)."""
    try:
        folder = pathlib.Path(anchor)
        if folder.is_file():
            folder = folder.parent
        if folder.name not in {"tool-results", "tool_results"}:
            return
        mapping, tzname = gather_meta()
        activity = load_activity(ACTIVITY_MERGE)
        dirty = False
        for child in folder.iterdir():
            if not child.is_file():
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if size <= STUB_AFTER:
                continue
            try:
                recs = [row for row in records_of(load_blob(str(child))) if isinstance(row, dict)]
            except Exception:
                recs = []
            if recs:
                merge_records(activity, recs, mapping, tzname)
                dirty = True
            stub_overflow_source(str(child))
        if dirty:
            write_activity(ACTIVITY_MERGE, activity, mapping)
    except OSError:
        pass


def sweep_stale_planner_overflows() -> int:
    """Slim+stub leftover tool-results from prior planner CLI sessions."""
    hint = re.compile(r"engineer-day-planner-run|engineerdayplanner", re.I)
    home = pathlib.Path.home()
    roots = [
        home / ".claude" / "projects",
        home / ".cursor" / "projects",
        home / "Library" / "Caches" / "engineer-day-planner",
    ]
    n = 0
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = [root] if hint.search(str(root)) else list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if child is not root and not hint.search(child.name) and not hint.search(str(child)):
                continue
            for name in ("tool-results", "tool_results"):
                try:
                    folders = [child] if child.name == name else list(child.rglob(name))
                except OSError:
                    continue
                for folder in folders:
                    if not folder.is_dir() or folder.name not in {"tool-results", "tool_results"}:
                        continue
                    sweep_tool_results(str(folder))
                    n += 1
    return n


def write_activity(path: str, activity: dict, mapping: dict) -> None:
    open(path, "w", encoding="utf-8").write(dump(activity_payload(activity, mapping)) + "\n")
    write_digest(activity, mapping)


def slim(data):
    recs = [row for row in records_of(data) if isinstance(row, dict)]
    total = data.get("totalSize") if isinstance(data, dict) else len(recs)
    if recs:
        if is_case_list(recs):
            return pack_cases(recs, total if total is not None else len(recs))
        if any(get_ci(row, "ParentId") for row in recs):
            return pack_activity(recs, total if total is not None else len(recs))
        payload = {
            "totalSize": total if total is not None else len(recs),
            "shown": len(recs),
            "omitted": 0,
            "records": [slim_activity(row) for row in recs],
        }
        blob = dump(payload)
        while len(blob) > MAX_OUT and payload["records"]:
            payload["records"] = payload["records"][:-8]
            payload["shown"] = len(payload["records"])
            payload["omitted"] = (total or 0) - payload["shown"]
            blob = dump(payload)
        return payload
    if isinstance(data, dict):
        for key in ("results", "result", "text"):
            blob = data.get(key)
            if not isinstance(blob, str):
                continue
            if is_gmail_text(blob):
                return pack_gmail_text(blob)
            if is_slack_text(blob):
                return pack_slack_text(blob)
        if "result" in data and isinstance(data["result"], str):
            return {"result": clip(data["result"], 4000)}
        if "results" in data and isinstance(data["results"], str):
            return {"results": clip(data["results"], 4000)}
        if "text" in data:
            return {"text": clip(data["text"], 4000)}
        return {k: clip(v, 200) for k, v in list(data.items())[:20]}
    return {"text": clip(data, 4000)}


def parse_args(argv: list[str]) -> tuple[str, str, bool, bool, bool]:
    path = ""
    merge_path = ACTIVITY_MERGE
    print_json = False
    cards = False
    digest = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--print":
            print_json = True
        elif arg == "--cards":
            cards = True
        elif arg == "--digest":
            digest = True
        elif arg in {"--quiet", "--merge-quiet"}:
            print_json = False
        elif arg == "--no-merge":
            merge_path = ""
        elif arg == "--merge":
            i += 1
            if i >= len(argv):
                raise ValueError("usage: slim-tool-result.py <path>|--cards|--digest [--merge FILE] [--print]")
            merge_path = argv[i]
        elif not arg.startswith("-"):
            path = arg
        i += 1
    if not cards and not digest and not path:
        raise ValueError("usage: slim-tool-result.py <path>|--cards|--digest [--merge FILE] [--print]")
    return path, merge_path, print_json, cards, digest


def main() -> int:
    if "--sweep-stale" in sys.argv[1:]:
        n = sweep_stale_planner_overflows()
        sys.stdout.write(f"ok swept={n}\n")
        return 0
    try:
        path, merge_path, print_json, cards, digest = parse_args(sys.argv[1:])
    except ValueError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    if digest:
        return print_digest()
    if cards:
        return print_cards()
    try:
        data = load_blob(path)
    except OSError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    if print_json:
        blob = dump(slim(data))
        if len(blob) > MAX_OUT:
            blob = dump({"error": "summary still too large", "bytes": len(blob)})
        sys.stdout.write(blob if blob.endswith("\n") else blob + "\n")
        return 0
    recs = [row for row in records_of(data) if isinstance(row, dict)]
    mapping, tzname = gather_meta()
    activity = load_activity(merge_path) if merge_path else {"byId": {}, "byNumber": {}, "relatedById": {}}
    if recs:
        merge_records(activity, recs, mapping, tzname)
        if merge_path:
            write_activity(merge_path, activity, mapping)
    stub_overflow_source(path)
    sweep_tool_results(path)
    parents = len(activity.get("byId") or {})
    records = sum(len(v) for v in (activity.get("byId") or {}).values())
    sys.stdout.write(f"ok parents={parents} records={records}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
