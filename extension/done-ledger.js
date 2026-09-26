const EDP_DONE_MAX_AGE_MS = 21 * 24 * 60 * 60 * 1000;
const EDP_DONE_ID_MAX_AGE_MS = 2 * 24 * 60 * 60 * 1000;
const EDP_DONE_MAX_KEYS = 300;
const EDP_DONE_DURABLE = [
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
  "gusurl:"
];

function edpIsDurableDoneKey(key) {
  const k = String(key || "");
  return EDP_DONE_DURABLE.some((p) => k.indexOf(p) === 0);
}

function edpPackNewer(packed, have) {
  const parts = (s) => String(s || "").split(".").map((n) => parseInt(n, 10) || 0);
  const a = parts(packed);
  const b = parts(have);
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i] || 0;
    const y = b[i] || 0;
    if (x !== y) return x > y;
  }
  return false;
}

function edpPruneLedgerKeys(keys, nowMs, tzname) {
  const now = Number(nowMs) || Date.now();
  const src = keys && typeof keys === "object" ? keys : {};
  const durableCut = now - EDP_DONE_MAX_AGE_MS;
  const idCut = now - EDP_DONE_ID_MAX_AGE_MS;
  const out = {};
  Object.keys(src).forEach((k) => {
    if (!k) return;
    let ms = Number(src[k]) || now;
    if (ms < 1e12) ms = ms * 1000;
    if (/plan-new-cases/i.test(k)) {
      let zone = String(tzname || "").trim();
      if (!zone) {
        try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) {}
      }
      const opts = { year: "numeric", month: "2-digit", day: "2-digit" };
      if (zone) opts.timeZone = zone;
      const fmt = new Intl.DateTimeFormat("en-CA", opts);
      if (fmt.format(new Date(ms)) !== fmt.format(new Date(now))) return;
    }
    if (k.indexOf("id:") === 0) {
      if (ms < idCut) return;
    } else if (edpIsDurableDoneKey(k)) {
      if (ms < durableCut) return;
      const slotLabel = k.indexOf("slot:") === 0 ? k.split(":").slice(2).join(":").trim().toLowerCase() : "";
      if (/^(needs us now|follow-?ups?|take new cases|new cases|short break|open)$/.test(slotLabel)) return;
    } else {
      return;
    }
    out[k] = ms;
  });
  const names = Object.keys(out);
  if (names.length <= EDP_DONE_MAX_KEYS) return out;
  names.sort((a, b) => Number(out[b]) - Number(out[a]));
  const kept = {};
  names.slice(0, EDP_DONE_MAX_KEYS).forEach((k) => {
    kept[k] = out[k];
  });
  return kept;
}

function edpNormUrl(raw) {
  const u = String(raw || "").trim();
  if (!u) return "";
  const cut = u.split("?")[0].replace(/\/$/, "");
  return cut;
}

function edpCaseNums(item) {
  const nums = [];
  const fromNum = String((item && item.caseNumber) || "").replace(/\D/g, "");
  if (fromNum.length >= 6) nums.push(fromNum);
  const lab = String((item && item.label) || "").match(/#(\d{6,})/);
  if (lab) nums.push(lab[1]);
  const idm = String((item && item.id) || "").match(/(?:need|follow|watch|meet|qw|case)-(\d{6,})/i);
  if (idm) nums.push(idm[1]);
  return nums;
}

function edpSlackChannel(blob) {
  const s = String(blob || "");
  let m = s.match(/\/archives\/([CGD][A-Z0-9]+)/i);
  if (m) return m[1];
  m = s.match(/slack\.com\/client\/([CGD][A-Z0-9]+)/i);
  if (m) return m[1];
  m = s.match(/\b(D[A-Z0-9]{8,})\b/);
  return m ? m[1] : "";
}

function edpMailId(blob) {
  const s = String(blob || "");
  const m = s.match(/#(?:all|inbox|sent|important|search|spam)\/([0-9a-f]{10,})/i);
  return m ? m[1].toLowerCase() : "";
}

function edpItemKeys(item) {
  const keys = [];
  if (!item || typeof item !== "object") return keys;
  if (item.id) keys.push("id:" + String(item.id));
  edpCaseNums(item).forEach((n) => keys.push("case:" + n));
  const slackFields = [
    item.slackUrl,
    item.slackPermalink,
    item.threadUrl,
    item.channelUrl,
    item.permalink
  ];
  const cid = String(item.channelId || item.slackChannel || "").trim();
  const ts = String(item.ts || item.threadTs || item.message_ts || "").trim();
  if (/^D[A-Z0-9]{8,}$/i.test(cid)) keys.push("slackch:" + cid);
  else if (/^[CG][A-Z0-9]{8,}$/i.test(cid) && ts) keys.push("slackth:" + cid + ":" + ts);
  for (let i = 0; i < slackFields.length; i++) {
    const u = String(slackFields[i] || "");
    if (/slack\.com/i.test(u) || /^slack:\/\//i.test(u)) {
      const n = edpNormUrl(u);
      if (n) keys.push("slack:" + n);
      const arch = n.match(/^(https:\/\/[^\s/]+)\/archives\/([CGD][A-Z0-9]+)(?:\/p(\d{10,}))?/i);
      if (arch) {
        const host = arch[1];
        const ch = arch[2];
        const stamp = arch[3] || "";
        if (ch.charAt(0) === "D") {
          keys.push("slackch:" + ch);
          keys.push("slack:" + host + "/archives/" + ch);
        } else if (stamp) {
          keys.push("slackth:" + ch + ":" + (stamp.indexOf(".") >= 0 ? stamp : stamp.slice(0, 10) + "." + stamp.slice(10)));
        }
      }
      const client = n.match(/slack\.com\/client\/([CGD][A-Z0-9]+)/i);
      if (client && client[1].charAt(0) === "D") keys.push("slackch:" + client[1]);
      break;
    }
  }
  const ident = String(item.id || "");
  const dm = ident.match(/^slack-(D[A-Z0-9]{8,})$/i);
  if (dm) keys.push("slackch:" + dm[1]);
  const mailFields = [item.mailUrl, item.gmailUrl, item.messageUrl, item.url, item.link, item.htmlLink];
  for (let i = 0; i < mailFields.length; i++) {
    const u = String(mailFields[i] || "");
    if (/mail\.google\.com/i.test(u) || /(^|\.)gmail\.com/i.test(u)) {
      const n = edpNormUrl(u);
      if (n) keys.push("mail:" + n);
      const mid = edpMailId(n);
      if (mid) keys.push("mailid:" + mid);
      break;
    }
  }
  if (item.workId) keys.push("gus:" + String(item.workId));
  if (item.gusUrl) keys.push("gusurl:" + edpNormUrl(item.gusUrl));
  if (item.eventId) keys.push("event:" + String(item.eventId));
  const cal = edpNormUrl(item.htmlLink);
  if (cal && /calendar\.google\.com/i.test(cal)) keys.push("calurl:" + cal);
  const start = String(item.startStamp || "").trim();
  const lab = String(item.label || "").trim().toLowerCase().replace(/\s+/g, " ");
  const hasCase = keys.some((k) => k.indexOf("case:") === 0);
  const genericSlot = /^(needs us now|follow-?ups?|take new cases|new cases|short break|open)$/.test(lab);
  if (start && lab && !hasCase && !genericSlot) keys.push("slot:" + start + ":" + lab);
  const mid = String(item.messageId || item.gmailId || "").trim();
  if (/^[0-9a-f]{10,}$/i.test(mid)) keys.push("mailid:" + mid.toLowerCase());
  const mailRow = ident.match(/^mail-([0-9a-f]{10,})$/i);
  if (mailRow) keys.push("mailid:" + mailRow[1].toLowerCase());
  return keys.filter((k, i, all) => k && all.indexOf(k) === i);
}

function edpItemMatchesLedger(item, keySet) {
  if (!item || !keySet || !keySet.size) return false;
  const keys = edpItemKeys(item);
  if (keys.some((k) => keySet.has(k) && k.indexOf("slackch:") !== 0 && !/^id:slack-D[A-Z0-9]{8,}$/i.test(k))) return true;
  const cases = new Set(edpCaseNums(item));
  const mails = new Set();
  keys.forEach((k) => {
    if (k.indexOf("mailid:") === 0) mails.add(k.slice(7));
  });
  for (const k of keySet) {
    if (k.indexOf("case:") === 0 && cases.has(k.slice(5))) return true;
    const cm = String(k).match(/(?:need|follow|watch|meet|qw|case)-(\d{6,})/i);
    if (cm && cases.has(cm[1])) return true;
    if (k.indexOf("mailid:") === 0 && mails.has(k.slice(7))) return true;
    const mid = edpMailId(k);
    if (mid && mails.has(mid)) return true;
  }
  return false;
}

function edpWalkItems(data, fn) {
  (data && data.sections ? data.sections : []).forEach((sec) => {
    if (!sec) return;
    (sec.items || []).forEach((it) => {
      if (it) fn(it, sec);
    });
    (sec.groups || []).forEach((g) => {
      ((g && g.items) || []).forEach((it) => {
        if (it) fn(it, sec);
      });
    });
  });
}

function edpActiveKeys(ledger, tzname) {
  const pruned = edpPruneLedgerKeys(ledger && ledger.keys, Date.now(), tzname);
  return new Set(Object.keys(pruned));
}

function edpApplyLedger(data, keySet) {
  if (!data || !keySet || !keySet.size) return false;
  let changed = false;
  const ids = [];
  edpWalkItems(data, (item) => {
    if (!item || item.autoBreak || item.kind === "break") return;
    if (item.done === false) return;
    if (!edpItemMatchesLedger(item, keySet)) return;
    if (item.done !== true) {
      item.done = true;
      changed = true;
    }
    if (item.id) ids.push(String(item.id));
  });
  data.doneIds = Array.from(new Set(ids));
  return changed;
}

function edpSectionLooksLike(sec, re) {
  const t = String((sec && sec.title) || "")
    .replace(/^[^\w#]+/, "")
    .replace(/\s*\([^)]*\)\s*$/, "")
    .trim();
  return re.test(t);
}

function edpLeftoverItems(data, kind) {
  if (kind === "close") {
    const out = [];
    edpWalkItems(data, (it, sec) => {
      if (!it || String(it.id || "").indexOf("plan-") === 0) return;
      if (it.promisedClose === true || it.promisedCloseOn) {
        out.push(it);
        return;
      }
      const blob = String(it.label || "") + " " + String(it.detail || "");
      if (/\bgeo\b|handover|handoff/i.test(blob) && !/\bclose\b/i.test(blob)) return;
      if (edpSectionLooksLike(sec, /^before you log off$/i) && /\b(promised close|case closures?|close this case|close if still quiet|closure)\b/i.test(blob)) {
        out.push(it);
      }
    });
    return out;
  }
  const re = kind === "slack"
    ? /^slack\b/i
    : kind === "follow"
      ? /^(follow-up due|needs a touch|follow up due)$/i
      : /^(mail|email|gmail)\b/i;
  const out = [];
  (data && data.sections ? data.sections : []).forEach((sec) => {
    if (!edpSectionLooksLike(sec, re)) return;
    const take = (it) => {
      if (!it || it.kind === "break" || it.autoBreak) return;
      if (String(it.id || "").indexOf("plan-") === 0) return;
      out.push(it);
    };
    (sec.items || []).forEach(take);
    (sec.groups || []).forEach((g) => ((g && g.items) || []).forEach(take));
  });
  return out;
}

function edpSyncQueuePlanBlocks(data) {
  if (!data) return false;
  let changed = false;
  [
    ["plan-slack", "slack", false],
    ["plan-mail", "mail", false],
    ["plan-follow-", "follow", true],
    ["plan-close", "close", false]
  ].forEach(([id, kind, prefix]) => {
    const rows = edpLeftoverItems(data, kind);
    if (!rows.length) return;
    const allDone = rows.every((it) => it.done === true);
    edpWalkItems(data, (it) => {
      if (!it) return;
      const hit = prefix ? String(it.id || "").indexOf(id) === 0 : it.id === id;
      if (!hit) return;
      if (it.done !== allDone) changed = true;
      it.done = allDone;
    });
  });
  return changed;
}

function edpStampDoneIds(data) {
  if (!data) return;
  const ids = [];
  edpWalkItems(data, (it) => {
    if (it && it.done === true && it.id) ids.push(String(it.id));
  });
  data.doneIds = Array.from(new Set(ids));
}

function edpApplyQueueClick(data, id, on) {
  if (!data || !id) return;
  if (id === "plan-slack" || id === "plan-mail") {
    edpLeftoverItems(data, id === "plan-slack" ? "slack" : "mail").forEach((it) => {
      it.done = !!on;
    });
  }
  if (String(id).indexOf("plan-follow-") === 0) {
    edpLeftoverItems(data, "follow").forEach((it) => {
      it.done = !!on;
    });
  }
  if (id === "plan-close") {
    edpLeftoverItems(data, "close").forEach((it) => {
      it.done = !!on;
    });
  }
  edpSyncQueuePlanBlocks(data);
  edpStampDoneIds(data);
}

function edpIsPlannerHold(item) {
  if (!item || !item.eventId) return false;
  if (item.assembled === true || String(item.kind || "").toLowerCase() === "assembled") return false;
  if (item.invite === true || item.rsvp) return false;
  if (item.joinUrl || item.hangoutLink || item.meetLink) return false;
  if (/^case discussion:/i.test(String(item.label || ""))) return false;
  const k = String(item.kind || "").toLowerCase();
  if (k === "slack" || k === "mail" || k === "email" || k === "gmail") return false;
  if (k === "plan" || k === "task" || k === "case" || item.asTask === true) return true;
  return false;
}
