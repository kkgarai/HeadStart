importScripts("done-ledger.js", "omni-schedule.js");

const SERVICE = "engineer-day-planner";
const PORT_START = 8765;
const PORT_END = 8799;
const LEAD_MS = 10 * 60 * 1000;
const LOGOUT_LEAD_MS = 30 * 60 * 1000;
const LOGOUT_GRACE_MS = 12 * 60 * 60 * 1000;
const SNOOZE_MIN = 5;
const ALARM_SYNC = "edp-sync";
const ALARM_OMNI = "edp-omni";
const ALARM_OMNI_NAG = "edp-omni-nag";
const ALARM_PREFIX = "edp:";
const TAB_KEY = "edpUiTabId";
const OMNI_NOTE = "edp-omni-ooa";
const OMNI_SOUND = "omni-sound.html";
const LOGOUT_SOUND = "logout-sound.html";
const OMNI_ALERT_TEXT = "Omni Is Out Of Adherence. You should be available for this Assembled block.";

function omniAlertMessage(omniStatus, assembledNow) {
  const status = String(omniStatus || "").trim() || "Offline";
  const block = String(assembledNow || "").trim();
  if (block) return "Omni: " + status + " · Assembled now: " + block;
  return "Omni: " + status;
}

function omniStatusLabel(records) {
  if (!records || !records.length) return "Offline";
  const rec = records[0] || {};
  const status = rec.ServicePresenceStatus || {};
  return String(status.MasterLabel || rec.MasterLabel || "").trim() || "Offline";
}
const OMNI_KIND_TOKENS = {
  casework: ["case", "casework"],
  chat: ["chat", "live"],
  messaging: ["messaging", "message"],
  voice: ["voice"],
  lunch: ["lunch", "meal"],
  break: ["break"]
};
const OMNI_WORK_KINDS = { casework: true, chat: true, messaging: true, voice: true };
const OMNI_MEAL_KINDS = { lunch: true, break: true };
const OMNI_ACK_LABEL = "Got It";
const PAGE_URL = chrome.runtime.getURL("extension/panel.html");

function ensureAlarms() {
  chrome.alarms.create(ALARM_SYNC, { periodInMinutes: 1 });
  scheduleOmniAlarm();
}

function scheduleOmniAlarm() {
  const when = nextOmniCheckAt(Date.now());
  chrome.alarms.create(ALARM_OMNI, { when });
}

const UPDATE_BOOT = "edpUpdateBoot";

async function bootFromUpdate() {
  const stored = await chrome.storage.local.get([UPDATE_BOOT]);
  if (!stored[UPDATE_BOOT]) return false;
  await chrome.storage.local.remove([UPDATE_BOOT]);
  await ensureNativeBridge();
  return true;
}

chrome.runtime.onInstalled.addListener(async () => {
  ensureAlarms();
  if (await bootFromUpdate()) return;
  const tabs = await plannerTabs();
  if (tabs.length) {
    if (!(await scanBridge())) await ensureNativeBridge();
    await syncFromBridge();
  } else {
    await stopNativeBridge();
  }
});

chrome.runtime.onStartup.addListener(async () => {
  ensureAlarms();
  if (await bootFromUpdate()) return;
  const tabs = await plannerTabs();
  if (tabs.length) {
    const panel = tabs.find((tab) => String((tab && tab.url) || "").indexOf(PAGE_URL) === 0);
    if (panel && panel.id) await chrome.storage.local.set({ [TAB_KEY]: panel.id });
    if (!(await scanBridge())) await ensureNativeBridge();
    await syncFromBridge();
  } else {
    await stopNativeBridge();
  }
});

async function openPlannerTab() {
  const opened = chrome.tabs.create({ url: PAGE_URL, active: true });
  let created = null;
  try {
    created = await opened;
  } catch (_) {}
  if (created && created.id) {
    await chrome.storage.local.set({ [TAB_KEY]: created.id });
    const others = await plannerTabs(created.id);
    const prior = others.find((tab) => String((tab && tab.url) || "").indexOf(PAGE_URL) === 0);
    if (prior && prior.id) {
      try {
        await chrome.tabs.update(prior.id, { active: true });
        if (prior.windowId != null) await chrome.windows.update(prior.windowId, { focused: true });
        await chrome.tabs.remove(created.id);
        await chrome.storage.local.set({ [TAB_KEY]: prior.id });
      } catch (_) {}
    }
  }
  let found = await findBridge();
  const url = found && found.bridgeUrl;
  if (url) {
    await chrome.storage.local.set({
      bridgeUrl: url,
      bridgePort: Number(String(url).split(":").pop()) || 8765
    });
  }
  try {
    await chrome.runtime.sendMessage({ type: "edpOpened" });
  } catch (_) {}
}

chrome.action.onClicked.addListener(() => {
  openPlannerTab();
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.local.get([TAB_KEY]).then(async (stored) => {
    if (stored[TAB_KEY] === tabId) await chrome.storage.local.remove([TAB_KEY]);
    await stopBridgeIfIdle(tabId);
  });
});

chrome.windows.onRemoved.addListener(() => {
  stopBridgeIfIdle();
});

const NATIVE_HOST = "com.kgarai.engineerdayplanner.bridge";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const NEWER_THAN_BRIDGE = "Run the setup command once from the folder you loaded.";

function extensionVersion() {
  return String((chrome.runtime.getManifest() || {}).version || "");
}

function bridgeIsOlder(packed) {
  const have = extensionVersion();
  if (!have || typeof edpPackNewer !== "function") return false;
  if (!packed) return true;
  return edpPackNewer(have, String(packed));
}

async function shutdownBridgePort(port) {
  try {
    await fetch("http://127.0.0.1:" + port + "/__shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: AbortSignal.timeout(1200)
    });
  } catch (_) {}
}

async function readPlannerHealth(url, timeoutMs) {
  const resp = await fetch(String(url).replace(/\/$/, "") + "/health", {
    signal: AbortSignal.timeout(timeoutMs || 400)
  });
  if (!resp.ok) return null;
  const body = await resp.json();
  if (!body || !body.ok || body.service !== SERVICE) return null;
  return body;
}

async function scanBridge() {
  const have = extensionVersion();
  const stored = await chrome.storage.local.get(["bridgePort"]);
  const preferred = Number(stored.bridgePort) || 0;
  const ports = [];
  if (preferred >= PORT_START && preferred <= PORT_END) ports.push(preferred);
  for (let port = PORT_START; port <= PORT_END; port++) {
    if (port !== preferred) ports.push(port);
  }
  const hits = await Promise.all(
    ports.map(async (port) => {
      try {
        const body = await readPlannerHealth("http://127.0.0.1:" + port, 400);
        return body ? { port, body } : null;
      } catch (_) {
        return null;
      }
    })
  );
  let chosen = "";
  for (const hit of hits) {
    if (!hit) continue;
    const packed = String(hit.body.version || "");
    if (have && packed && packed !== have) continue;
    if (chosen) continue;
    chosen = "http://127.0.0.1:" + hit.port;
    await chrome.storage.local.set({ bridgeUrl: chosen, bridgePort: hit.port });
  }
  return chosen;
}

async function usableBridge(url) {
  try {
    const body = await readPlannerHealth(url);
    if (!body) return "";
    const have = extensionVersion();
    const packed = String(body.version || "");
    if (have && packed && packed !== have) return "";
    return String(url).replace(/\/$/, "");
  } catch (_) {
    return "";
  }
}

async function stopNativeBridge() {
  const ports = [];
  for (let port = PORT_START; port <= PORT_END; port++) ports.push(port);
  await Promise.all(
    ports.map(async (port) => {
      try {
        const body = await readPlannerHealth("http://127.0.0.1:" + port, 400);
        if (body) await shutdownBridgePort(port);
      } catch (_) {}
    })
  );
  if (chrome.runtime.sendNativeMessage) {
    try {
      await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
        cmd: "stop",
        version: extensionVersion()
      });
    } catch (_) {}
  }
  await chrome.storage.local.remove(["bridgeUrl", "bridgePort"]);
}

async function stopBridgeIfIdle(exceptId) {
  const tabs = await plannerTabs(exceptId);
  if (tabs.length) return;
  await stopNativeBridge();
}

let lastHostError = "";
let lastHostMissing = false;

function hostLooksMissing(message) {
  const text = String(message || "");
  return /specified native messaging host not found|native messaging host not found|setup command once|forbidden/i.test(text);
}

const PACK_FILES = [
  "manifest.json",
  "extension/panel.html",
  "extension/panel.js",
  "extension/panel.css",
  "extension/background.js",
  "extension/content.js",
  "extension/done-ledger.js",
  "extension/welcome.html",
  "extension/skill/SKILL.md",
  "extension/skill/page/template.html",
  "extension/skill/scripts/FETCH_SYSTEM.md",
  "extension/skill/scripts/PLAN_SYSTEM.md",
  "extension/skill/scripts/calendar-bridge.py",
  "extension/skill/scripts/edp-native-host-main.py",
  "extension/skill/scripts/edp-native-host.py",
  "extension/skill/scripts/install-native-host.py",
  "extension/skill/scripts/publish-page.sh",
  "extension/skill/scripts/sanitize-briefing.py",
  "extension/skill/scripts/self-check-plan.py",
  "extension/skill/scripts/slim-tool-result.py"
];

async function loadedExtensionFiles() {
  const files = [];
  for (let i = 0; i < PACK_FILES.length; i++) {
    const path = PACK_FILES[i];
    try {
      const resp = await fetch(chrome.runtime.getURL(path));
      if (!resp.ok) continue;
      files.push({ path, text: await resp.text() });
    } catch (_) {}
  }
  return files;
}

async function stageLoadedExtension() {
  const version = extensionVersion();
  const files = await loadedExtensionFiles();
  let batch = [];
  let size = 0;
  async function flush() {
    if (!batch.length) return;
    const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      cmd: "stage",
      version,
      files: batch
    });
    if (!res || res.ok === false) throw new Error((res && res.error) || "Could not store this version");
    batch = [];
    size = 0;
  }
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const n = String(file.text || "").length;
    if (batch.length && size + n > 600000) await flush();
    batch.push(file);
    size += n;
  }
  await flush();
  const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, { cmd: "commit", version });
  if (!res || !res.url) return "";
  for (let i = 0; i < 25; i++) {
    const found = await scanBridge();
    if (found) return found;
    await sleep(400);
  }
  return String(res.url).replace(/\/$/, "");
}

async function adoptLoadedExtension(url) {
  const base = String(url || "").replace(/\/$/, "");
  if (!base) return "";
  const files = await loadedExtensionFiles();
  try {
    const resp = await fetch(base + "/skill/adopt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: extensionVersion(), files })
    });
    if (!resp.ok) return "";
  } catch (_) {
    return "";
  }
  for (let i = 0; i < 25; i++) {
    await sleep(400);
    const found = await scanBridge();
    if (found) return found;
  }
  return "";
}

async function ensureNativeBridge() {
  lastHostError = "";
  lastHostMissing = false;
  if (!chrome.runtime.sendNativeMessage) {
    lastHostMissing = true;
    lastHostError = "Native messaging is not available.";
    return "";
  }
  try {
    const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      cmd: "ensure",
      version: chrome.runtime.getManifest().version
    });
    if (res && res.url) {
      await chrome.storage.local.set({ edpNativeHostOk: true });
      let url = await scanBridge();
      if (!url) url = await usableBridge(String(res.url));
      if (!url) {
        try {
          url = await stageLoadedExtension();
        } catch (err) {
          lastHostError = String((err && err.message) || err || "");
          url = "";
        }
      }
      if (url) {
        const port = Number(String(url).split(":").pop()) || 0;
        await chrome.storage.local.set({ bridgeUrl: url, bridgePort: port });
        return url;
      }
      lastHostError = "Bridge did not switch to this version.";
      lastHostMissing = false;
      return "";
    }
    lastHostError = (res && res.error) || "Bridge did not start.";
  } catch (err) {
    lastHostError = String((err && err.message) || err || "");
  }
  lastHostMissing = hostLooksMissing(lastHostError);
  return "";
}

async function restartNativeBridge() {
  if (!chrome.runtime.sendNativeMessage) return "";
  try {
    const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      cmd: "restart",
      version: chrome.runtime.getManifest().version
    });
    if (res && res.url) return usableBridge(String(res.url));
  } catch (_) {}
  return "";
}

async function bouncePackedBridge() {
  if (!(await scanBridge())) await ensureNativeBridge();
  await syncFromBridge();
}

async function findBridge() {
  let url = await scanBridge();
  if (url) return { bridgeUrl: url, hostMissing: false, hostError: "" };
  url = await ensureNativeBridge();
  if (!url) url = await scanBridge();
  if (url) return { bridgeUrl: url, hostMissing: false, hostError: "" };
  await chrome.storage.local.remove(["bridgeUrl", "bridgePort"]);
  if (lastHostMissing) await chrome.storage.local.remove(["edpNativeHostOk"]);
  return {
    bridgeUrl: "",
    hostMissing: lastHostMissing || !lastHostError,
    hostError: lastHostError || "Bridge did not start."
  };
}

async function currentBridge() {
  const stored = await chrome.storage.local.get(["bridgeUrl"]);
  if (stored.bridgeUrl) {
    const url = await usableBridge(stored.bridgeUrl);
    if (url) return url;
  }
  return scanBridge();
}

async function offSet() {
  const stored = await chrome.storage.local.get(["edpOff"]);
  const rec = stored.edpOff || { day: "", ids: [] };
  const day = new Date().toISOString().slice(0, 10);
  if (rec.day !== day) return { day, ids: [] };
  return rec;
}

async function markOff(id) {
  const rec = await offSet();
  if (rec.ids.indexOf(id) < 0) rec.ids.push(id);
  await chrome.storage.local.set({ edpOff: rec });
}

async function syncFromBridge() {
  const tabs = await plannerTabs();
  if (!tabs.length) {
    await stopNativeBridge();
    chrome.action.setBadgeText({ text: "" });
    return "";
  }
  const bridge = await currentBridge();
  if (!bridge) {
    chrome.action.setBadgeText({ text: "" });
    return bridge;
  }
  try {
    const resp = await fetch(bridge + "/snapshot", { signal: AbortSignal.timeout(4000) });
    if (!resp.ok) return bridge;
    const snap = await resp.json();
    await chrome.storage.local.set({ snapshot: snap });
    const storedDone = await chrome.storage.local.get(["edpDone"]);
    const doneKeys = edpActiveKeys(storedDone.edpDone || {}, snap.timezone);
    const n = snap.needYou;
    chrome.action.setBadgeBackgroundColor({ color: "#ba0517" });
    chrome.action.setBadgeText({ text: n > 0 ? String(n) : "" });
    const reminders = (snap.reminders || []).filter((row) => {
      if (!row || !row.id) return false;
      if (doneKeys.has("id:" + row.id)) return false;
      if (row.eventId && doneKeys.has("event:" + String(row.eventId))) return false;
      return true;
    });
    await scheduleReminders(reminders);
    await scheduleLogoutPending(snap);
  } catch (_) {}
  return bridge;
}

const BLINK_ALARM = "edp-tab-blink";
let blinkLoopOn = false;
let blinkWanted = false;

async function readBlink() {
  try {
    const rec = await chrome.storage.local.get("edpTabBlink");
    const state = rec && rec.edpTabBlink;
    if (state && typeof state === "object" && state.saved && typeof state.saved === "object") return state;
  } catch (_) {}
  return { shown: false, saved: {} };
}

async function writeBlink(state) {
  try {
    await chrome.storage.local.set({ edpTabBlink: state });
  } catch (_) {}
}

async function restorePlannerGroup(tabId, saved) {
  const original = Number(saved && saved.original);
  try {
    const tab = await chrome.tabs.get(tabId);
    if (original >= 0) {
      const group = await chrome.tabGroups.get(original);
      if (group && tab && group.windowId === tab.windowId) {
        await chrome.tabs.group({ tabIds: [tabId], groupId: original });
        return;
      }
    }
  } catch (_) {}
  try { await chrome.tabs.ungroup(tabId); } catch (_) {}
}

async function flashPlannerTabs(on, msg) {
  const tabs = await plannerTabs();
  for (const tab of tabs) {
    if (!tab || tab.id == null) continue;
    chrome.tabs.sendMessage(tab.id, { type: "edpTabFlash", on: !!on, msg: msg || "" }).catch(() => {});
  }
}

async function clearLeftoverBlinkGroups() {
  const tabs = await plannerTabs();
  for (const tab of tabs) {
    if (!tab || tab.id == null || typeof tab.groupId !== "number" || tab.groupId < 0) continue;
    try {
      const group = await chrome.tabGroups.get(tab.groupId);
      if (group && group.title === "!" && group.windowId === tab.windowId) {
        await chrome.tabs.ungroup(tab.id);
      }
    } catch (_) {}
  }
}

async function runTabBlink() {
  if (blinkWanted) await flashPlannerTabs(true);
}

function rememberBlink(on, msg) {
  blinkWanted = !!on;
  const payload = { edpTabBlinkOn: !!on };
  if (on && msg) payload.edpTabBlinkMsg = String(msg).slice(0, 80);
  chrome.storage.local.set(payload);
}

async function stopTabBlink() {
  rememberBlink(false);
  await flashPlannerTabs(false);
  await clearLeftoverBlinkGroups();
  try { await chrome.alarms.clear(BLINK_ALARM); } catch (_) {}
}

async function flagPlannerTabs(msg) {
  const tabs = await plannerTabs();
  if (!tabs.length) return;
  rememberBlink(true, msg);
  await flashPlannerTabs(true, msg);
}

function noteIcon() {
  return chrome.runtime.getURL("extension/icons/icon128.png");
}

function createNote(id, options) {
  return new Promise((resolve) => {
    chrome.notifications.create(id, options, (created) => {
      const err = chrome.runtime.lastError;
      resolve(!err && !!created);
    });
  });
}

function plannerNote(noteId) {
  return noteId === OMNI_NOTE || (noteId && (noteId.indexOf("edp-note-") === 0 || noteId.indexOf("edp-logout-pending-") === 0));
}

async function fireLogoutPending(end) {
  const key = String(end || "");
  const now = Date.now();
  if (end && now >= end + LOGOUT_GRACE_MS) return;
  const stored = await chrome.storage.local.get(["snapshot", "edpLogoutShown"]);
  if (key && stored.edpLogoutShown === key) return;
  const pending = (stored.snapshot && stored.snapshot.pending) || {};
  const created = await createNote("edp-logout-pending-" + (key || Date.now()), {
    type: "basic",
    iconUrl: noteIcon(),
    title: "30 Minutes Before Logout",
    message: pendingLine(pending),
    silent: true,
    requireInteraction: true,
    buttons: [{ title: "OK" }],
    priority: 2
  });
  if (!created) return;
  await chrome.storage.local.set({ edpLogoutShown: key || String(now) });
  await flagPlannerTabs("30 Minutes Before Logout");
  await startLogoutSound();
}

async function scheduleLogoutPending(snap) {
  const end = Number(snap && snap.shiftEndMs) || 0;
  if (!end) return;
  const when = end - LOGOUT_LEAD_MS;
  const now = Date.now();
  const stored = await chrome.storage.local.get(["edpLogoutShown"]);
  if (stored.edpLogoutShown === String(end) || now >= end + LOGOUT_GRACE_MS) {
    await chrome.alarms.clear("edp-logout-pending");
    return;
  }
  if (when <= now) {
    await chrome.alarms.clear("edp-logout-pending");
    await fireLogoutPending(end);
    return;
  }
  chrome.alarms.create("edp-logout-pending", { when });
}

function pendingLine(pending) {
  const p = pending || {};
  const lines = ["30 Minutes Until Logout"];
  if (p.needsNow) lines.push(p.needsNow + " Needs Us Now");
  if (p.followUps) lines.push(p.followUps + (Number(p.followUps) === 1 ? " Follow-Up" : " Follow-Ups"));
  if (p.slack) lines.push(p.slack + " Slack");
  if (p.mail) lines.push(p.mail + " Email");
  if (p.gus) lines.push(p.gus + " GUS");
  if (lines.length === 1) lines.push("Nothing Still Open");
  else lines[lines.length - 1] += " Still Open";
  return lines.join("\n");
}

async function startLogoutSound() {
  try {
    if (!chrome.offscreen || !chrome.offscreen.createDocument) return;
    const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
    if (contexts && contexts.length) {
      const url = String(contexts[0].documentUrl || "");
      if (url.indexOf(LOGOUT_SOUND) >= 0) return;
      await chrome.offscreen.closeDocument();
    }
    await chrome.offscreen.createDocument({
      url: LOGOUT_SOUND,
      reasons: ["AUDIO_PLAYBACK"],
      justification: "Logout reminder sound until the notification is dismissed"
    });
  } catch (_) {}
}

async function stopLogoutSound() {
  try {
    if (!chrome.offscreen || !chrome.offscreen.closeDocument) return;
    const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
    if (!contexts || !contexts.length) return;
    if (String(contexts[0].documentUrl || "").indexOf(LOGOUT_SOUND) < 0) return;
    await chrome.offscreen.closeDocument();
  } catch (_) {}
}

async function scheduleReminders(items) {
  const rec = await offSet();
  const existing = await chrome.alarms.getAll();
  await Promise.all(
    existing
      .filter((a) => a.name && a.name.indexOf(ALARM_PREFIX) === 0)
      .map((a) => chrome.alarms.clear(a.name))
  );
  const now = Date.now();
  for (const item of items) {
    if (!item || !item.id || rec.ids.indexOf(item.id) >= 0) continue;
    const start = Number(item.startMs) || 0;
    if (!start || start <= now) continue;
    let when = start - LEAD_MS;
    if (when < now + 1000) when = now + 1000;
    chrome.alarms.create(ALARM_PREFIX + item.id, { when });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm || !alarm.name) return;
  if (alarm.name === ALARM_SYNC) {
    await syncFromBridge();
    return;
  }
  if (alarm.name === ALARM_OMNI) {
    scheduleOmniAlarm();
    await checkOmni();
    return;
  }
  if (alarm.name === ALARM_OMNI_NAG) {
    await nagOmniSound();
    return;
  }
  if (alarm.name === "edp-tab-blink") {
    await runTabBlink();
    return;
  }
  if (alarm.name === "edp-logout-pending") {
    const stored = await chrome.storage.local.get(["snapshot"]);
    const end = Number(stored.snapshot && stored.snapshot.shiftEndMs) || 0;
    await fireLogoutPending(end);
    return;
  }
  if (alarm.name.indexOf(ALARM_PREFIX) !== 0) return;
  const id = alarm.name.slice(ALARM_PREFIX.length);
  const rec = await offSet();
  if (rec.ids.indexOf(id) >= 0) return;
  const stored = await chrome.storage.local.get(["snapshot"]);
  const item = ((stored.snapshot && stored.snapshot.reminders) || []).find((row) => row.id === id);
  const label = (item && item.label) || "Upcoming event";
  const start = item && item.startMs ? Number(item.startMs) : 0;
  const mins = start ? Math.max(0, Math.round((start - Date.now()) / 60000)) : 10;
  chrome.notifications.create("edp-note-" + id, {
    type: "basic",
    iconUrl: noteIcon(),
    title: label,
    message: mins <= 0 ? "Starting now" : "Starts in " + mins + " min",
    requireInteraction: true,
    buttons: [{ title: "Snooze 5 Min" }, { title: "Turn Off" }],
    priority: 2
  });
  await flagPlannerTabs(label);
});

chrome.notifications.onButtonClicked.addListener(async (noteId, button) => {
  rememberBlink(false);
  if (noteId && noteId.indexOf("edp-logout-pending-") === 0) await stopLogoutSound();
  await stopTabBlink();
  if (noteId === OMNI_NOTE) {
    await omniAck();
    return;
  }
  if (noteId && noteId.indexOf("edp-logout-pending-") === 0) {
    chrome.notifications.clear(noteId);
    return;
  }
  if (!noteId || noteId.indexOf("edp-note-") !== 0) return;
  const id = noteId.slice("edp-note-".length);
  chrome.notifications.clear(noteId);
  if (button === 1) {
    await markOff(id);
    chrome.alarms.clear(ALARM_PREFIX + id);
    return;
  }
  chrome.alarms.create(ALARM_PREFIX + id, { delayInMinutes: SNOOZE_MIN });
});

chrome.notifications.onClicked.addListener(async (noteId) => {
  rememberBlink(false);
  if (noteId && noteId.indexOf("edp-logout-pending-") === 0) await stopLogoutSound();
  await stopTabBlink();
  if (noteId === OMNI_NOTE) await omniAck();
});

chrome.notifications.onClosed.addListener((noteId) => {
  if (!plannerNote(noteId)) return;
  rememberBlink(false);
  if (noteId && noteId.indexOf("edp-logout-pending-") === 0) stopLogoutSound();
  stopTabBlink();
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;
  if (msg.type === "edpTabFlashStop") {
    rememberBlink(false);
    stopTabBlink();
    return;
  }
  if (msg.type === "edpPageClosed") {
    const exceptId = sender && sender.tab && sender.tab.id;
    stopBridgeIfIdle(exceptId);
    return;
  }
  if (msg.type === "updateCheck") {
    checkExtensionUpdate().then((res) => sendResponse(res));
    return true;
  }
  if (msg.type === "updateApply") {
    applyExtensionUpdate().then((res) => sendResponse(res));
    return true;
  }
  if (msg.type === "findBridge") {
    findBridge().then((res) => sendResponse(res && res.bridgeUrl !== undefined ? res : { bridgeUrl: res || "" }));
    return true;
  }
  if (msg.type === "sync") {
    syncFromBridge().then((url) => sendResponse({ bridgeUrl: url }));
    return true;
  }
  if (msg.type === "omniAck" || msg.type === "omniSnooze" || msg.type === "omniOff") {
    omniAck().then(() => sendResponse({ ok: true }));
    return true;
  }
});

async function startOmniNagAlarm() {
  chrome.alarms.create(ALARM_OMNI_NAG, { delayInMinutes: 1, periodInMinutes: 1 });
}

async function stopOmniNagAlarm() {
  chrome.alarms.clear(ALARM_OMNI_NAG);
}

async function omniAck() {
  const until = nextOmniCheckAt(Date.now());
  await chrome.storage.local.set({
    edpOmniAcked: true,
    edpOmniAckUntil: until,
    edpOmniAlerting: false
  });
  await hideOmniAlert();
}

async function omniStillSuppressed() {
  const stored = await chrome.storage.local.get(["edpOmniAckUntil"]);
  const until = Number(stored.edpOmniAckUntil) || 0;
  if (omniAckActive(until, Date.now())) return true;
  if (until) await chrome.storage.local.set({ edpOmniAcked: false, edpOmniAckUntil: 0 });
  return false;
}

async function nagOmniSound() {
  const stored = await chrome.storage.local.get(["edpOmniAcked", "edpOmniAlerting"]);
  if (stored.edpOmniAcked || !stored.edpOmniAlerting) {
    await stopOmniNagAlarm();
    await stopOmniSound();
    return;
  }
  const tabs = await plannerTabs();
  if (tabs.length) {
    await stopOmniSound();
    return;
  }
  await playOmniSound();
}

function omniLabelTokens(label) {
  return String(label || "").toLowerCase().match(/[a-z0-9]+/g) || [];
}

function omniTokenHit(tokens, kind) {
  const need = OMNI_KIND_TOKENS[kind] || [];
  for (let i = 0; i < need.length; i++) {
    if (tokens.indexOf(need[i]) >= 0) return true;
  }
  return false;
}

function omniMealOnly(kinds) {
  const list = Array.isArray(kinds) ? kinds.filter(Boolean) : [];
  if (!list.length) return false;
  return list.every((kind) => OMNI_MEAL_KINDS[kind]);
}

function omniIsOfflineStatus(label) {
  const low = String(label || "").trim().toLowerCase();
  return !low || low.indexOf("offline") >= 0 || low === "end of work";
}

function omniInAdherence(label, kinds) {
  const list = Array.isArray(kinds) ? kinds : [];
  const low = String(label || "").trim().toLowerCase();
  if (/screen[\s-]*sharing/.test(low)) return true;
  if (omniMealOnly(list) && omniIsOfflineStatus(label)) return true;
  if (!low) return false;
  if (low === "busy" || low.indexOf("busy ") === 0 || low.indexOf("busy-") === 0) return false;
  if (!list.length) return false;
  const tokens = omniLabelTokens(low);
  const meal = list.filter((kind) => OMNI_MEAL_KINDS[kind]);
  const work = list.filter((kind) => OMNI_WORK_KINDS[kind]);
  if (meal.length && !work.length) {
    for (let i = 0; i < meal.length; i++) {
      if (omniTokenHit(tokens, meal[i])) return true;
    }
    return false;
  }
  const available = low.indexOf("available") === 0 || low.indexOf("chat online") >= 0;
  if (!work.length || !available) return false;
  for (let i = 0; i < work.length; i++) {
    if (!omniTokenHit(tokens, work[i])) return false;
  }
  return true;
}

function omniOutOfAdherence(records, kinds) {
  if (omniMealOnly(kinds)) {
    if (!records || !records.length) return false;
    const rec = records[0] || {};
    const status = rec.ServicePresenceStatus || {};
    const label = String(status.MasterLabel || rec.MasterLabel || "").trim();
    if (omniIsOfflineStatus(label)) return false;
    return !omniInAdherence(label, kinds);
  }
  if (!records || !records.length) return true;
  const rec = records[0] || {};
  const status = rec.ServicePresenceStatus || {};
  const label = String(status.MasterLabel || rec.MasterLabel || "").trim();
  return !omniInAdherence(label, kinds);
}

async function orgcsSid() {
  if (!chrome.cookies || !chrome.cookies.get) return "";
  const urls = ["https://orgcs.my.salesforce.com/", "https://orgcs.lightning.force.com/"];
  for (let i = 0; i < urls.length; i++) {
    try {
      const c = await chrome.cookies.get({ url: urls[i], name: "sid" });
      if (c && c.value) return String(c.value);
    } catch (_) {}
  }
  return "";
}

async function omniFromSalesforceSession(kinds) {
  const sid = await orgcsSid();
  if (!sid) return { action: "skip", reason: "omni_session_unavailable" };
  const headers = { Authorization: "Bearer " + sid, Accept: "application/json" };
  const meResp = await fetch("https://orgcs.my.salesforce.com/services/data/v68.0/chatter/users/me", {
    headers,
    signal: AbortSignal.timeout(12000)
  });
  if (!meResp.ok) return { action: "skip", reason: "omni_session_unavailable" };
  const me = await meResp.json();
  const userId = String((me && (me.id || me.userId)) || "");
  if (userId.indexOf("005") !== 0) return { action: "skip", reason: "omni_session_unavailable" };
  const soql =
    "SELECT Id, UserId, IsCurrentState, ServicePresenceStatus.MasterLabel, ServicePresenceStatus.DeveloperName " +
    "FROM UserServicePresence WHERE UserId = '" +
    userId.replace(/'/g, "") +
    "' AND IsCurrentState = true LIMIT 5";
  const qResp = await fetch(
    "https://orgcs.my.salesforce.com/services/data/v68.0/query?q=" + encodeURIComponent(soql),
    { headers, signal: AbortSignal.timeout(12000) }
  );
  if (!qResp.ok) return { action: "skip", reason: "omni_session_unavailable" };
  const body = await qResp.json().catch(() => null);
  const records = body && Array.isArray(body.records) ? body.records : null;
  if (!records) return { action: "skip", reason: "omni_session_unavailable" };
  const omniStatus = omniStatusLabel(records);
  if (omniOutOfAdherence(records, kinds)) {
    return {
      action: "alert",
      reason: "out_of_adherence",
      title: "Omni Is Out Of Adherence",
      omniStatus,
      message: omniAlertMessage(omniStatus, "")
    };
  }
  return { action: "ok", reason: "in_adherence", omniStatus };
}

async function stopOmniSound() {
  try {
    if (!chrome.offscreen || !chrome.offscreen.closeDocument) return;
    const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
    if (contexts && contexts.length) await chrome.offscreen.closeDocument();
  } catch (_) {}
}

async function playOmniSound() {
  try {
    if (chrome.offscreen && chrome.offscreen.createDocument) {
      const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
      if (contexts && contexts.length) return;
      await chrome.offscreen.createDocument({
        url: OMNI_SOUND,
        reasons: ["AUDIO_PLAYBACK"],
        justification: "Omni out-of-adherence alert"
      });
    }
  } catch (_) {}
}

async function plannerTabs(exceptId) {
  const tabs = await chrome.tabs.query({});
  return (tabs || []).filter((tab) => {
    if (exceptId && tab.id === exceptId) return false;
    return isPlannerUrl(String((tab && tab.url) || ""));
  });
}

function isPlannerUrl(url) {
  url = String(url || "");
  if (url.indexOf(PAGE_URL) === 0) return true;
  return /^http:\/\/127\.0\.0\.1:(876[5-9]|87[7-9][0-9])(?:\/|$)/.test(url);
}

async function hideOmniAlert() {
  await stopOmniNagAlarm();
  await stopOmniSound();
  await chrome.storage.local.set({ edpOmniAlerting: false });
  chrome.notifications.clear(OMNI_NOTE);
  const tabs = await plannerTabs();
  tabs.forEach((tab) => {
    if (!tab.id) return;
    chrome.tabs.sendMessage(tab.id, { type: "omniClear" }).catch(() => {});
  });
}

async function showOmniAlert(body) {
  if (await omniStillSuppressed()) return;
  const omniStatus = String((body && body.omniStatus) || "").trim() || "Offline";
  const assembledNow = String((body && body.assembledNow) || "").trim();
  const title = (body && body.title) || "Omni Is Out Of Adherence";
  const message = (body && body.message) || omniAlertMessage(omniStatus, assembledNow) || OMNI_ALERT_TEXT;
  const shiftEndMs = Number(body && body.shiftEndMs) || 0;
  if (shiftEndMs) await chrome.storage.local.set({ edpOmniShiftEnd: shiftEndMs });
  await chrome.storage.local.set({ edpOmniAlerting: true });
  const tabs = await plannerTabs();
  let pageOpen = false;
  tabs.forEach((tab) => {
    if (!tab.id) return;
    pageOpen = true;
    chrome.tabs.sendMessage(tab.id, {
      type: "omniAlert",
      title,
      message,
      omniStatus,
      assembledNow,
      shiftEndMs
    }).catch(() => {});
  });
  if (!pageOpen) {
    await playOmniSound();
    await startOmniNagAlarm();
  } else {
    await stopOmniNagAlarm();
  }
  chrome.notifications.create(OMNI_NOTE, {
    type: "basic",
    iconUrl: noteIcon(),
    title,
    message,
    requireInteraction: true,
    buttons: [{ title: OMNI_ACK_LABEL }],
    priority: 2
  });
  await flagPlannerTabs(title);
}

async function checkOmni() {
  const bridge = await currentBridge();
  if (!bridge) return;
  let body;
  try {
    const resp = await fetch(bridge + "/omni/check", { signal: AbortSignal.timeout(25000) });
    if (!resp.ok) return;
    body = await resp.json();
  } catch (_) {
    return;
  }
  if (!body || body.ok === false) return;
  if (body.shiftEndMs) await chrome.storage.local.set({ edpOmniShiftEnd: Number(body.shiftEndMs) || 0 });
  if (body.action === "need_omni") {
    try {
      const live = await omniFromSalesforceSession(body.scheduleKinds || []);
      body = Object.assign({}, body, live);
    } catch (_) {
      body = Object.assign({}, body, { action: "skip", reason: "omni_session_unavailable" });
    }
  }
  if (body.action === "alert") {
    if (!body.omniStatus) body.omniStatus = "Offline";
    body.message = omniAlertMessage(body.omniStatus, body.assembledNow);
    if (await omniStillSuppressed()) return;
    await showOmniAlert(body);
    return;
  }
  if (body.action === "ok") {
    await hideOmniAlert();
  }
}

async function checkExtensionUpdate() {
  if (!chrome.runtime.sendNativeMessage) return { ok: false, update: false };
  try {
    const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      cmd: "update-check",
      version: extensionVersion()
    });
    if (!res || res.update !== true) return { ok: true, update: false };
    return { ok: true, update: true, remote: res.remote || "", local: res.local || extensionVersion() };
  } catch (_) {
    return { ok: false, update: false };
  }
}

async function stopRunningModel() {
  const stored = await chrome.storage.local.get(["bridgeUrl"]);
  const urls = [];
  if (stored.bridgeUrl) urls.push(String(stored.bridgeUrl).replace(/\/$/, ""));
  for (let port = PORT_START; port <= PORT_END; port++) urls.push("http://127.0.0.1:" + port);
  const seen = {};
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    if (!url || seen[url]) continue;
    seen[url] = true;
    try {
      const body = await readPlannerHealth(url, 400);
      if (!body) continue;
      await fetch(url + "/plan/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal: AbortSignal.timeout(8000)
      });
    } catch (_) {}
  }
}

async function applyExtensionUpdate() {
  if (!chrome.runtime.sendNativeMessage) return { ok: false, error: "Native messaging is not available." };
  try {
    await stopRunningModel();
    const res = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
      cmd: "update-apply",
      version: extensionVersion()
    });
    if (!res || res.ok === false) return { ok: false, error: (res && res.error) || "Update failed." };
    await chrome.storage.local.set({ [UPDATE_BOOT]: true });
    chrome.runtime.reload();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err || "Update failed.") };
  }
}

ensureAlarms();
bootFromUpdate().then((updated) => {
  if (!updated) syncFromBridge();
});
