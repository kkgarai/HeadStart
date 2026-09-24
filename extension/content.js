function briefingNode() {
  return document.getElementById("briefing-data");
}

function readBriefing() {
  const node = briefingNode();
  if (!node) return null;
  try {
    const data = JSON.parse(node.textContent || "null");
    return data && typeof data === "object" ? data : null;
  } catch (_) {
    return null;
  }
}

function writeBriefing(data) {
  const node = briefingNode();
  if (!node || !data) return;
  node.textContent = JSON.stringify(data);
}

async function loadLedger() {
  const stored = await chrome.storage.local.get(["edpDone"]);
  const rec = stored.edpDone;
  if (rec && rec.keys && typeof rec.keys === "object") return rec;
  return { keys: {}, updatedAt: 0 };
}

async function saveLedger(ledger) {
  const page = readBriefing();
  const next = {
    keys: edpPruneLedgerKeys((ledger && ledger.keys) || {}, Date.now(), page && page.timezone),
    updatedAt: Date.now()
  };
  await chrome.storage.local.set({ edpDone: next });
  try {
    localStorage.setItem("edp-done-ledger", JSON.stringify(next));
  } catch (_) {}
  return next;
}

function migrateLegacyKeys(into, data) {
  const now = Date.now();
  try {
    const pageRec = JSON.parse(localStorage.getItem("edp-done-ledger") || "{}");
    if (pageRec && pageRec.keys && typeof pageRec.keys === "object") {
      Object.keys(pageRec.keys).forEach((k) => {
        if (k) into[k] = into[k] || pageRec.keys[k] || now;
      });
    }
  } catch (_) {}
  try {
    const names = [];
    for (let i = 0; i < localStorage.length; i++) {
      const name = localStorage.key(i);
      if (name && name.indexOf("edp-done:") === 0) names.push(name);
    }
    names.forEach((name) => {
      let ids = [];
      try { ids = JSON.parse(localStorage.getItem(name) || "[]"); } catch (_) { ids = []; }
      (Array.isArray(ids) ? ids : []).forEach((id) => {
        if (!id) return;
        let found = null;
        if (data) {
          edpWalkItems(data, (it) => {
            if (it && it.id === id) found = it;
          });
        }
        const keys = found ? edpItemKeys(found).filter(edpIsDurableDoneKey) : [];
        if (keys.length) keys.forEach((k) => { into[k] = into[k] || now; });
        else into["id:" + String(id)] = into["id:" + String(id)] || now;
      });
      try { localStorage.removeItem(name); } catch (_) {}
    });
  } catch (_) {}
  return edpPruneLedgerKeys(into, now, data && data.timezone);
}

function paintDone(ids) {
  const on = {};
  (ids || []).forEach((id) => {
    on[String(id)] = true;
  });
  document.querySelectorAll(".js-done").forEach((btn) => {
    const id = btn.getAttribute("data-id");
    const isOn = !!(id && on[id]);
    btn.classList.toggle("on", isOn);
    btn.setAttribute("aria-pressed", isOn ? "true" : "false");
    btn.textContent = isOn ? "Completed" : "Done";
    const row = btn.closest(".row");
    if (row) row.classList.toggle("done", isOn);
  });
}

async function syncPage(data) {
  const resp = await fetch(location.origin + "/page/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data })
  });
  if (!resp.ok) throw new Error("sync failed");
}

async function applyOnLoad() {
  const data = readBriefing();
  if (!data) return;
  const ledger = await loadLedger();
  ledger.keys = migrateLegacyKeys(ledger.keys || {}, data);
  edpWalkItems(data, (it) => {
    if (it && it.done === false) {
      edpItemKeys(it).forEach((k) => {
        delete ledger.keys[k];
      });
    }
  });
  await saveLedger(ledger);
  const keys = edpActiveKeys(ledger, data.timezone);
  const changed = edpApplyLedger(data, keys);
  const queued = edpSyncQueuePlanBlocks(data);
  if (changed || queued) {
    edpStampDoneIds(data);
    writeBriefing(data);
    await saveLedger(ledger);
    try {
      await syncPage(data);
    } catch (_) {}
  }
  paintDone(data.doneIds || []);
}

function itemFromButton(btn) {
  const id = btn.getAttribute("data-id") || "";
  const data = readBriefing();
  let found = null;
  if (data) {
    edpWalkItems(data, (it) => {
      if (it && it.id === id) found = it;
    });
  }
  if (found) return found;
  const row = btn.closest(".row");
  const label = row && row.querySelector(".label") ? row.querySelector(".label").textContent : "";
  const hrefs = row ? Array.from(row.querySelectorAll("a")).map((a) => a.href) : [];
  return {
    id,
    label,
    slackUrl: hrefs.find((h) => /slack/i.test(h)) || "",
    mailUrl: hrefs.find((h) => /mail\.google|gmail/i.test(h)) || ""
  };
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest && ev.target.closest(".js-done");
  if (!btn) return;
  const id = btn.getAttribute("data-id");
  if (!id) return;
  setTimeout(async () => {
    const data = readBriefing();
    let found = null;
    if (data) {
      edpWalkItems(data, (it) => {
        if (it && it.id === id) found = it;
      });
    }
    const live = document.querySelector('.js-done[data-id="' + String(id).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"]');
    const on = found
      ? found.done === true
      : !!(live && (live.getAttribute("aria-pressed") === "true" || live.classList.contains("on")));
    const item = found || itemFromButton(btn);
    const keys = edpItemKeys(item);
    const store = keys.length ? keys.slice() : ["id:" + id];
    const ledger = await loadLedger();
    ledger.keys = ledger.keys || {};
    const now = Date.now();
    store.forEach((k) => {
      if (on) ledger.keys[k] = now;
      else delete ledger.keys[k];
    });
    if (!data) {
      await saveLedger(ledger);
      return;
    }
    const keySet = new Set(keys.concat(store));
    edpWalkItems(data, (it) => {
      if (!it) return;
      const hit = it.id === id || edpItemMatchesLedger(it, keySet);
      if (hit) {
        it.done = on;
        edpItemKeys(it).forEach((k) => {
          if (on) ledger.keys[k] = now;
          else delete ledger.keys[k];
        });
      }
    });
    edpApplyQueueClick(data, id, on);
    const extra = [];
    if (id === "plan-slack" || id === "plan-mail") {
      extra.push(...edpLeftoverItems(data, id === "plan-slack" ? "slack" : "mail"));
    }
    if (String(id).indexOf("plan-follow-") === 0) {
      extra.push(...edpLeftoverItems(data, "follow"));
    }
    if (id === "plan-close") {
      extra.push(...edpLeftoverItems(data, "close"));
    }
    edpWalkItems(data, (it) => {
      if (!it) return;
      if (it.id === "plan-slack" || it.id === "plan-mail" || it.id === "plan-close") extra.push(it);
      if (String(it.id || "").indexOf("plan-follow-") === 0) extra.push(it);
    });
    extra.forEach((it) => {
      edpItemKeys(it).forEach((k) => {
        if (it.done === true) ledger.keys[k] = now;
        else delete ledger.keys[k];
      });
    });
    await saveLedger(ledger);
    writeBriefing(data);
    try {
      await syncPage(data);
    } catch (_) {}
  }, 80);
});

applyOnLoad();

window.addEventListener("message", (ev) => {
  const d = ev.data || {};
  if (d.source !== "engineer-day-planner" || d.type !== "clock") return;
  const payload = {};
  if (d.clock === "local" || d.clock === "shift") payload.edpPlanClock = d.clock;
  if (d.viewZone) payload.edpViewZone = String(d.viewZone);
  if (d.viewShort) payload.edpViewShort = String(d.viewShort);
  if (!Object.keys(payload).length) return;
  try {
    chrome.storage.local.set(payload);
  } catch (_) {}
});

try {
  document.documentElement.setAttribute("data-edp-ext", "1");
} catch (_) {}

(function tabFlash() {
  if (window.top !== window) return;
  let timer = null;
  let REAL_TITLE = "Engineer Day Planner";
  let flashMsg = "Notification";
  function isFlashTitle(t) {
    t = String(t || "").trim();
    return t === flashMsg || /^(?:[●⚠]\s*)+/.test(t) || /^(?:●+\s*)*logout(?:\s*●+)*$/i.test(t);
  }
  function clearAlertIcon() {
    document.querySelectorAll("link[rel~='icon']").forEach((link) => {
      if (String(link.href || "").indexOf("data:image") === 0) link.remove();
    });
  }
  try {
    const kept = sessionStorage.getItem("edpRealTabTitle");
    if (kept && !isFlashTitle(kept)) REAL_TITLE = kept;
    else if (document.title && !isFlashTitle(document.title)) {
      REAL_TITLE = document.title;
      sessionStorage.setItem("edpRealTabTitle", REAL_TITLE);
    }
  } catch (e) {}
  clearAlertIcon();
  if (isFlashTitle(document.title)) document.title = REAL_TITLE;
  function stopFlash() {
    if (timer) clearInterval(timer);
    timer = null;
    document.title = REAL_TITLE;
    clearAlertIcon();
  }
  function startFlash(msg) {
    if (msg) flashMsg = String(msg);
    if (timer) return;
    if (document.title && !isFlashTitle(document.title)) REAL_TITLE = document.title;
    timer = setInterval(() => {
      document.title = document.title === REAL_TITLE ? flashMsg : REAL_TITLE;
    }, 1000);
  }
  function paint(on, msg) {
    if (on) startFlash(msg);
    else stopFlash();
  }
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "edpTabFlash") return;
    paint(!!msg.on, msg.msg);
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.edpTabBlinkOn) return;
    chrome.storage.local.get(["edpTabBlinkOn", "edpTabBlinkMsg"], (rec) => {
      paint(!!(rec && rec.edpTabBlinkOn), rec && rec.edpTabBlinkMsg);
    });
  });
  chrome.storage.local.get(["edpTabBlinkOn", "edpTabBlinkMsg"], (rec) => {
    if (rec && rec.edpTabBlinkOn) paint(true, rec.edpTabBlinkMsg);
    else if (isFlashTitle(document.title)) stopFlash();
  });
})();

if (chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || !msg.type) return;
    if (msg.type === "omniAlert") {
      window.dispatchEvent(new CustomEvent("edp-omni-alert", { detail: msg }));
    }
    if (msg.type === "omniClear") {
      window.dispatchEvent(new CustomEvent("edp-omni-clear"));
    }
  });
}

window.addEventListener("edp-omni-control", (ev) => {
  forwardOmniControl((ev && ev.detail) || {});
});

window.addEventListener("message", (ev) => {
  if (ev.source !== window) return;
  const d = ev.data || {};
  if (d.source !== "engineer-day-planner" || d.type !== "omni-control") return;
  forwardOmniControl(d.payload || {});
});

function forwardOmniControl(d) {
  if (!d || !d.type) return;
  const payload = { type: d.type, shiftEndMs: d.shiftEndMs };
  let tries = 0;
  const send = () => {
    tries += 1;
    try {
      chrome.runtime.sendMessage(payload, () => {
        if (chrome.runtime.lastError && tries < 6) setTimeout(send, 300);
      });
    } catch (_) {
      if (tries < 6) setTimeout(send, 300);
    }
  };
  send();
}
