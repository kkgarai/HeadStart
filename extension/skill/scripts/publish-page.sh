#!/usr/bin/env bash
# Publish the engineer day planner page into this skill folder (out/current.html).
# Usage: publish-page.sh /path/to/data.json
#    or: publish-page.sh -
# Prefers 127.0.0.1:8765. If that port is taken by something else, uses the next
# free port through 8799. Never kills a foreign listener. Override the search
# start with DAY_PLANNER_PORT.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
TEMPLATE="$ROOT/page/template.html"
OUT_DIR="${DAY_PLANNER_OUT_DIR:-${BRIEFING_OUT_DIR:-$ROOT/out}}"
DATA_IN="${1:-}"
BRIDGE="$HERE/calendar-bridge.py"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "engineer-day-planner: missing template at $TEMPLATE" >&2
  exit 1
fi
if [[ -z "$DATA_IN" ]]; then
  echo "engineer-day-planner: pass a JSON file, a JSON object, or - for stdin" >&2
  exit 1
fi

TMP="$(mktemp "${TMPDIR:-/tmp}/day-planner-data.XXXXXX.json")"
cleanup_tmp() { rm -f "$TMP"; }
trap cleanup_tmp EXIT

if [[ "$DATA_IN" == "-" ]]; then
  cat > "$TMP"
elif [[ -f "$DATA_IN" ]]; then
  cp "$DATA_IN" "$TMP"
elif [[ "$DATA_IN" == \{* ]]; then
  printf '%s\n' "$DATA_IN" > "$TMP"
else
  echo "engineer-day-planner: pass a JSON file, a JSON object, or - for stdin" >&2
  exit 1
fi

python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
if not isinstance(d, dict):
    sys.exit(1)
if d.get('notThePage') or d.get('kind') == 'planner-evidence':
    print('engineer-day-planner: that file is gather evidence, not the page', file=sys.stderr)
    sys.exit(2)
" "$TMP"

mkdir -p "$OUT_DIR"

PORT="8765"
PAGE_URL="http://127.0.0.1:8765/"
if [[ "${DAY_PLANNER_NO_BRIDGE:-}" == "1" ]]; then
  if [[ -f "$BRIDGE" ]] && command -v python3 >/dev/null 2>&1; then
    PORT="$(python3 "$BRIDGE" --current-port 2>/dev/null || echo "$PORT")"
    PAGE_URL="http://127.0.0.1:${PORT}/"
  fi
elif [[ -f "$BRIDGE" ]] && command -v python3 >/dev/null 2>&1; then
  ENSURED="$(python3 "$BRIDGE" --ensure 2>/dev/null || true)"
  if [[ "$ENSURED" == http://127.0.0.1:* ]]; then
    PAGE_URL="${ENSURED%/}/"
    PORT="${PAGE_URL#http://127.0.0.1:}"
    PORT="${PORT%/}"
  fi
fi

OUT="$OUT_DIR/current.html"
if [[ -z "${DAY_PLANNER_RUNNER:-}" && -f "$BRIDGE" ]] && command -v python3 >/dev/null 2>&1; then
  DETECTED="$(python3 "$BRIDGE" --detect-runner 2>/dev/null || true)"
  if [[ -n "${DETECTED}" ]]; then
    export DAY_PLANNER_RUNNER="$DETECTED"
  fi
fi
python3 - "$TEMPLATE" "$TMP" "$OUT" "$PAGE_URL" "$OUT_DIR" "${DATA_IN}" <<'PY'
import json, os, pathlib, re, sys
template = pathlib.Path(sys.argv[1]).read_text()
raw = pathlib.Path(sys.argv[2]).read_text()
data = json.loads(raw)
out_dir = pathlib.Path(sys.argv[5])
src_name = pathlib.Path(str(sys.argv[6] if len(sys.argv) > 6 else "")).name.lower()
data["bridgeUrl"] = sys.argv[4].rstrip("/")
data.setdefault("copyright", "© 2026 Kiran Kumar Garai <kgarai@salesforce.com>. Skill authored by Kiran Kumar Garai.")
EMPTY = os.environ.get("DAY_PLANNER_EMPTY") == "1"
if EMPTY:
    for k in ("name", "title", "manager"):
        data[k] = ""
    data.pop("shiftStart", None)
    data.pop("shiftEnd", None)
    data["assembledFromCalendar"] = False
    data["assembledSchedule"] = "Nothing Scheduled"
    data["openCases"] = 0
    data["needYou"] = 0
out_path = pathlib.Path(sys.argv[3])
START = '<script type="application/json" id="briefing-data">'
prev = {}
json_prev = out_dir / "briefing.json"
if json_prev.is_file():
    try:
        loaded = json.loads(json_prev.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            prev = loaded
    except Exception:
        prev = {}
if not prev and out_path.is_file():
    try:
        old_html = out_path.read_text(encoding="utf-8")
        i = old_html.find(START)
        if i >= 0:
            i += len(START)
            j = old_html.find("</script>", i)
            if j > i:
                loaded = json.loads(old_html[i:j])
                if isinstance(loaded, dict):
                    prev = loaded
    except Exception:
        prev = {}

import importlib.util
_san = pathlib.Path(sys.argv[1]).resolve().parent.parent / "scripts" / "sanitize-briefing.py"
_spec = importlib.util.spec_from_file_location("edp_sanitize", _san)
sanitize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sanitize)
avoid = ""
pq = prev.get("quote") if isinstance(prev, dict) else None
if isinstance(pq, dict):
    avoid = str(pq.get("text") or "")
elif isinstance(pq, str):
    avoid = pq
fresh = None if EMPTY else sanitize.fetch_fresh_quote(avoid)
if fresh and not EMPTY:
    data["quote"] = fresh
else:
    data.pop("quote", None)
sanitize.canonicalize_titles(data)
sanitize.fix_laptop_ist_clock(data)
sanitize.stamp_clock(data)
if EMPTY:
    ident = {}
    prev = {}
else:
    ident = {}
    try:
        ident_path = out_dir / ".identity.json"
        if ident_path.is_file():
            loaded_ident = json.loads(ident_path.read_text(encoding="utf-8"))
            if isinstance(loaded_ident, dict):
                ident = loaded_ident
    except Exception:
        ident = {}
    sanitize.fill_identity(data, prev if isinstance(prev, dict) else None, ident)

OMNI_OFFLINE_NOTE = "Omni Is Out Of Adherence. You should be available for this Assembled block."
OFFLINE_DEFAULTS = {
    "orgcs": ("OrgCS", "OrgCS MCP is unreachable. Last-run cases are shown."),
    "slack": ("Slack", "Slack MCP is unreachable. DMs and mentions were not read."),
    "gmail": ("Gmail", "Gmail MCP is unreachable."),
    "calendar": ("Calendar", "Calendar MCP is unreachable."),
    "gus": ("GUS", "GUS MCP is unreachable."),
    "assembled": ("Assembled", "Assembled is unreachable. Working-day is unknown."),
    "omni": ("Omni", OMNI_OFFLINE_NOTE),
}

def canon_tool(name):
    k = str(name or "").strip().lower()
    if k in ("mail", "email"):
        return "gmail"
    if k == "google calendar":
        return "calendar"
    return k

def stamp_offline(data):
    seen = {}
    items = []

    def add(source, text=""):
        key = canon_tool(source)
        if not key or key in seen:
            return
        label, default = OFFLINE_DEFAULTS.get(key, (str(source or "").strip(), ""))
        note = str(text or "").strip() or default or (label + " is unreachable.")
        if key == "omni" and not re.search(r"unreachable", note, re.I):
            return
        seen[key] = True
        items.append({"source": label or str(source).strip(), "text": note})

    for raw in data.get("offline") or []:
        if isinstance(raw, str):
            add(raw)
        elif isinstance(raw, dict):
            add(
                raw.get("source") or raw.get("tool") or raw.get("name"),
                raw.get("text") or raw.get("message") or raw.get("note"),
            )
    for flag, source, note_key in (
        ("orgcsOffline", "OrgCS", "orgcsOfflineNote"),
        ("slackOffline", "Slack", "slackOfflineNote"),
        ("gmailOffline", "Gmail", "gmailOfflineNote"),
        ("calendarOffline", "Calendar", "calendarOfflineNote"),
        ("gusOffline", "GUS", "gusOfflineNote"),
        ("assembledOffline", "Assembled", "assembledOfflineNote"),
    ):
        if data.get(flag) is True:
            add(source, data.get(note_key))
    for line in list(data.get("facts") or []) + list(data.get("status") or []):
        s = str(line or "")
        m = re.search(r"\b(OrgCS|Slack|Gmail|Calendar|GUS|Assembled|Omni)\b", s, re.I)
        if m and re.search(r"unreachable|offline|disconnect", s, re.I):
            if m.group(1).lower() == "omni" and not re.search(r"unreachable", s, re.I):
                continue
            add(m.group(1), re.sub(r"^[^\w#]+", "", s).strip())

    data["sections"] = [
        s for s in (data.get("sections") or [])
        if not re.search(r"sample orgcs", str((s or {}).get("title") or ""), re.I)
    ]
    data.pop("orgcsOffline", None)
    data.pop("orgcsOfflineNote", None)
    data.pop("orgcsOfflineSource", None)

    if not items:
        data.pop("offline", None)
        data.pop("omniOffline", None)
        data.pop("omniOfflineNote", None)
        return

    data["offline"] = items
    if seen.get("omni"):
        data["omniOffline"] = True
        data["omniOfflineNote"] = next((i["text"] for i in items if i["source"] == "Omni"), OMNI_OFFLINE_NOTE)
        facts = [f for f in (data.get("facts") or []) if not re.search(r"\bomni\b", str(f), re.I)]
        data["facts"] = facts
    else:
        data.pop("omniOffline", None)
        data.pop("omniOfflineNote", None)

    drop_tool = re.compile(r"\b(orgcs|slack|gmail|calendar|gus)\b", re.I)
    data["facts"] = [
        f for f in (data.get("facts") or [])
        if not (drop_tool.search(str(f)) and re.search(r"unreachable|offline", str(f), re.I))
    ]
    data["status"] = [
        s for s in (data.get("status") or [])
        if not re.search(r"unreachable|offline", str(s), re.I)
    ]

stamp_offline(data)

def stamp_header_facts(data):
    keep = []
    for line in data.get("facts") or []:
        s = str(line or "")
        if re.search(r"\b(shift|working day|assembled schedule)\b", s, re.I) and not re.search(
            r"\b(omni|orgcs|slack|gmail|calendar|gus|out:|\bwoc\b)\b", s, re.I
        ):
            keep.append(line)
    data["facts"] = keep

stamp_header_facts(data)
sanitize.inject_facts(data)
sanitize.coerce_row_items(data)
sanitize.normalize_chronology(data)
sanitize.peel_stray_inbox_rows(data)
sanitize.normalize_inbox_buckets(data)
sanitize.fill_slack_urls(data)
sanitize.fill_mail_urls(data)
sanitize.organize_plan(data)
sanitize.hoist_peek_fields(data)
sanitize.apply_ai_case_buckets(data)
sanitize.apply_ai_closeout(data)
sanitize.apply_ai_tomorrow(data)
sanitize.dedupe_closeout_sections(data)
sanitize.stamp_needs_when(data)
sanitize.normalize_chronology(data)
sanitize.restore_last_good_inbox(data, prev)
sanitize.refuse_false_inbox_clear(data)
sanitize.refuse_false_gus_clear(data)
sanitize.normalize_inbox_buckets(data)
sanitize.relabel_slack_dms(data)
extra = {}
try:
    extra_path = out_dir / ".done-keys.json"
    if extra_path.is_file():
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
except Exception:
    extra = {}
sanitize.apply_persisted_done(data, prev if isinstance(prev, dict) else None, extra if isinstance(extra, dict) else None)
sanitize.omit_done_inbox_rows(data)
sanitize.drop_done_from_plan(data)
sanitize.drop_solution_provided_from_plan(data)
sanitize.compose_work_blocks(data)
sanitize.apply_case_holds(data)
sanitize.demote_solution_provided_from_now(data)
sanitize.drop_solution_provided_from_plan(data)
try:
    rec = extra if isinstance(extra, dict) else {}
    keys = rec.get("keys") if isinstance(rec.get("keys"), dict) else {}
    now_ms = int(__import__("time").time() * 1000)
    keys = dict(keys)
    for k in sanitize.collect_done_keys(data):
        keys[k] = keys.get(k) or now_ms
    for k in sanitize.undone_item_keys(data):
        keys.pop(k, None)
    rec["keys"] = sanitize.drop_google_done_keys(sanitize.prune_done_key_map(keys, now_ms))
    rec["updatedAt"] = now_ms
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".done-keys.json").write_text(json.dumps(rec), encoding="utf-8")
except Exception:
    pass
try:
    ident_path = out_dir / ".identity.json"
    if EMPTY:
        ident_path.unlink(missing_ok=True)
    else:
        blob = sanitize.identity_blob(data)
        if blob.get("name"):
            out_dir.mkdir(parents=True, exist_ok=True)
            ident_path.write_text(json.dumps(blob) + "\n", encoding="utf-8")
except Exception:
    pass
sanitize.ensure_required_sections(data)
sanitize.drop_empty_optional_sections(data)
sanitize.relabel_slack_dms(data)

def prune_false_offline(data):
    delivered = set()
    if data.get("openCases"):
        delivered.add("orgcs")
    if data.get("shiftStart") and data.get("timezone"):
        delivered.add("assembled")
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        rows = list(sec.get("items") or [])
        for g in sec.get("groups") or []:
            if isinstance(g, dict):
                rows.extend(g.get("items") or [])
        for it in rows:
            if not isinstance(it, dict):
                continue
            kind = str(it.get("kind") or "").lower()
            if it.get("caseNumber") or it.get("caseUrl"):
                delivered.add("orgcs")
            if it.get("workId") or it.get("gusUrl") or kind == "gus":
                delivered.add("gus")
            if kind in ("mail", "email", "gmail") or it.get("mailUrl"):
                delivered.add("gmail")
            if kind == "slack" or it.get("slackUrl"):
                delivered.add("slack")
            if it.get("eventId"):
                delivered.add("calendar")
    items = []
    for row in data.get("offline") or []:
        if isinstance(row, str):
            key, text = canon_tool(row), ""
            source = row
        elif isinstance(row, dict):
            source = row.get("source") or row.get("tool") or row.get("name") or ""
            key = canon_tool(source)
            text = row.get("text") or row.get("message") or row.get("note") or ""
        else:
            continue
        if key == "omni" or key not in delivered:
            items.append({"source": source if isinstance(row, dict) and row.get("source") else (OFFLINE_DEFAULTS.get(key, (source, ""))[0] or source), "text": text})
    # keep pretty names
    pretty = []
    seen = {}
    for row in items:
        key = canon_tool(row.get("source"))
        if not key or key in seen:
            continue
        seen[key] = True
        label, default = OFFLINE_DEFAULTS.get(key, (str(row.get("source") or "").strip(), ""))
        pretty.append({"source": label or row.get("source"), "text": row.get("text") or default})
    if pretty:
        data["offline"] = pretty
    else:
        data.pop("offline", None)
    for key in delivered:
        data.pop(key + "Offline", None)
        data.pop(key + "OfflineNote", None)

prune_false_offline(data)
sanitize.sort_sections(data)
sanitize.drop_empty_optional_sections(data)

def norm(raw):
    return re.sub(r"[^a-z0-9._-]+", "", (raw or "").strip().lower())[:40]
runner = "" if EMPTY else (norm(os.environ.get("DAY_PLANNER_RUNNER", "")) or norm(str(data.get("runner") or "")))
if runner and not EMPTY:
    data["runner"] = runner
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ".runner").write_text(runner + "\n", encoding="utf-8")
elif EMPTY:
    data.pop("runner", None)
    for key in (
        "quote", "doneLedger", "doneKeys", "facts", "needsUsNow", "stamp",
        "generatedAt", "peeks", "todayPlan", "meetings", "primaryEvents",
        "followUpDue", "stillWatching", "quickWins", "customerAskedMeeting",
        "beforeYouLogOff", "slackCandidates", "mailCandidates", "gusCandidates",
    ):
        data.pop(key, None)
    data["name"] = ""
    data["title"] = ""
    data["manager"] = ""
raw = json.dumps(data, ensure_ascii=False)
if "__BRIEFING_DATA__" not in template:
    raise SystemExit("engineer-day-planner: template missing __BRIEFING_DATA__ placeholder")
tmp_out = out_path.with_name(out_path.name + ".tmp")
tmp_out.write_text(template.replace("__BRIEFING_DATA__", raw), encoding="utf-8")
tmp_out.replace(out_path)
json_out = out_dir / "briefing.json"
json_tmp = json_out.with_name("briefing.json.tmp")
json_tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
json_tmp.replace(json_out)
print(str(out_path))
PY

if [[ "${DAY_PLANNER_NO_BRIDGE:-}" != "1" ]] && [[ -f "$BRIDGE" ]] && command -v python3 >/dev/null 2>&1; then
  export DAY_PLANNER_PAGE="$OUT"
  export DAY_PLANNER_PORT="$PORT"
  export DAY_PLANNER_PORT_FILE="$OUT_DIR/.bridge-port"
  if ! curl -fsS --max-time 1 "${PAGE_URL}health" >/dev/null 2>&1; then
    if command -v perl >/dev/null 2>&1; then
      perl -e 'use POSIX; POSIX::setsid(); exec @ARGV' python3 "$BRIDGE" >"$OUT_DIR/.bridge.log" 2>&1 &
    else
      nohup python3 "$BRIDGE" >"$OUT_DIR/.bridge.log" 2>&1 </dev/null &
      disown $! 2>/dev/null || true
    fi
    echo $! > "$OUT_DIR/.bridge-pid"
    echo $! > "${TMPDIR:-/tmp}/day-planner-bridge.pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10 12 14 16 18 20; do
      curl -fsS --max-time 1 "${PAGE_URL}health" >/dev/null 2>&1 && break
      if [[ -f "$DAY_PLANNER_PORT_FILE" ]]; then
        BOUND="$(tr -d '[:space:]' < "$DAY_PLANNER_PORT_FILE")"
        if [[ -n "$BOUND" && "$BOUND" != "$PORT" ]]; then
          PORT="$BOUND"
          PAGE_URL="http://127.0.0.1:${PORT}/"
        fi
      fi
      sleep 0.15
    done
  fi
fi

echo "engineer-day-planner: $PAGE_URL" >&2

if [[ "${DAY_PLANNER_NO_OPEN:-${BRIEFING_NO_OPEN:-}}" == "1" ]]; then
  exit 0
fi

if curl -fsS --max-time 1 "${PAGE_URL}health" >/dev/null 2>&1 && command -v open >/dev/null 2>&1; then
  open "$PAGE_URL"
elif command -v open >/dev/null 2>&1; then
  open "$OUT"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$PAGE_URL" >/dev/null 2>&1 || xdg-open "$OUT" >/dev/null 2>&1 &
else
  echo "engineer-day-planner: wrote $OUT" >&2
fi
