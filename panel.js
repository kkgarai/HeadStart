(function () {
  try {
    document.documentElement.setAttribute(
      "data-theme",
      window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    );
  } catch (e) {}
})();
let planClockPref = "shift";
let planViewZone = "";
let planViewShort = "";

window.addEventListener("message", function (ev) {
  var d = ev.data || {};
  if (d.source !== "engineer-day-planner") return;
  if (d.type === "theme") {
    if (d.theme === "dark" || d.theme === "light") {
      document.documentElement.setAttribute("data-theme", d.theme);
    }
  }
  if (d.type === "clock") {
    if (d.clock === "local" || d.clock === "shift") planClockPref = d.clock;
    if (d.viewZone) planViewZone = String(d.viewZone);
    if (d.viewShort) planViewShort = String(d.viewShort);
    try {
      chrome.storage.local.set({
        edpPlanClock: planClockPref,
        edpViewZone: planViewZone,
        edpViewShort: planViewShort
      });
    } catch (e) {}
  }
});

const statusEl = document.getElementById("status");
const missEl = document.getElementById("miss");
const missCmd = document.getElementById("miss-cmd");
const missInstall = document.getElementById("miss-install");
const missAgain = document.getElementById("miss-again");
const missError = document.getElementById("miss-error");
const missRetry = document.getElementById("miss-retry");
const missCopy = document.getElementById("miss-copy");
const missNote = document.getElementById("miss-note");
const frame = document.getElementById("frame");
const runBtn = document.getElementById("run");
const stopBtn = document.getElementById("stop");
const reloadBtn = document.getElementById("reload");
const tokenBtn = document.getElementById("token");
const stepsBtn = document.getElementById("steps-btn");
const stepsDock = document.getElementById("steps-dock");
const stepsLog = document.getElementById("steps-log");
const stepsMode = document.getElementById("steps-mode");
const stepsTitle = document.getElementById("steps-title");
const stepsCopy = document.getElementById("steps-copy");
const stepsClose = document.getElementById("steps-close");
const tokenLayer = document.getElementById("token-layer");
const tokenInput = document.getElementById("token-input");
const modelInput = document.getElementById("model-input");
const runnerInput = document.getElementById("runner-input");
const tokenMsg = document.getElementById("token-msg");
const tokenSave = document.getElementById("token-save");
const tokenDevbar = document.getElementById("token-devbar");
const tokenCancel = document.getElementById("token-cancel");
const mcpList = document.getElementById("mcp-list");
const mcpRefresh = document.getElementById("mcp-refresh");
const askFab = document.getElementById("ask-fab");
const askDock = document.getElementById("ask-dock");
const askLog = document.getElementById("ask-log");
const askForm = document.getElementById("ask-form");
const askInput = document.getElementById("ask-q");
const askSend = document.getElementById("ask-send");
const askClose = document.getElementById("ask-close");

let bridgeUrl = "";
let pollTimer = 0;
let missTimer = 0;
let tokenRequired = true;
let modelTimer = 0;
let planSteps = [];
let planAfter = 0;
let planLogMode = "compact";
let planStartedAt = "";
let pageStampAtRun = "";
let askHistory = [];

function setRunUi(running) {
  runBtn.disabled = !!running;
  if (stopBtn) stopBtn.hidden = !running;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setTokenMsg(text) {
  tokenMsg.textContent = text || "";
}

function openAsk() {
  askDock.hidden = false;
  askDock.classList.add("is-open");
  askFab.setAttribute("aria-expanded", "true");
  askInput.focus();
}

function closeAsk() {
  askDock.hidden = true;
  askDock.classList.remove("is-open");
  askFab.setAttribute("aria-expanded", "false");
}

function clearFrame() {
  frame.hidden = true;
  frame.src = "about:blank";
}

function stopMissPoll() {
  if (missTimer) {
    clearInterval(missTimer);
    missTimer = 0;
  }
}

function startMissPoll() {
  if (missTimer) return;
  missTimer = setInterval(async () => {
    try {
      const res = await chrome.runtime.sendMessage({ type: "findBridge" });
      if (res && res.bridgeUrl) {
        bridgeUrl = res.bridgeUrl;
        stopMissPoll();
        showPage();
        loadRunners();
      }
    } catch (_) {}
  }, 2000);
}

function showPage() {
  if (!bridgeUrl) return;
  stopMissPoll();
  missEl.hidden = true;
  frame.hidden = false;
  frame.src = bridgeUrl + "/?ext=1&t=" + Date.now();
  askFab.hidden = false;
  setStatus("Bridge " + bridgeUrl.replace("http://", ""));
  chrome.runtime.sendMessage({ type: "sync" });
}

function refreshOpenPage() {
  try {
    if (frame && !frame.hidden && frame.contentWindow && frame.src && frame.src.indexOf("about:") !== 0) {
      frame.contentWindow.postMessage({ source: "engineer-day-planner", type: "page-refresh" }, "*");
      return true;
    }
  } catch (_) {}
  return false;
}

function installFileName() {
  const plat = navigator.platform || "";
  const ua = navigator.userAgent || "";
  if (/Win/i.test(plat) || /Windows/i.test(ua)) return "Install Windows.bat";
  if (/Linux/i.test(plat) || /Linux/i.test(ua)) return "Install Linux.sh";
  return "Install Mac.command";
}

function fillMissHelp() {
  const file = installFileName();
  const list = document.getElementById("miss-installers");
  if (!list) return;
  list.querySelectorAll("li").forEach((li) => {
    const label = li.getAttribute("data-label") || "";
    const here = li.getAttribute("data-file") === file;
    li.textContent = here ? label + " — this computer" : label;
    if (here) li.setAttribute("data-here", "1");
    else li.removeAttribute("data-here");
  });
}

function setMissNote(text) {
  if (!missNote) return;
  missNote.hidden = !text;
  missNote.textContent = text || "";
}

async function saveNativeHelper() {
  try {
    const url = chrome.runtime.getURL("skill/scripts/install-native-host.py");
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("helper missing");
    const blob = await resp.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = "install-native-host.py";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 4000);
    setMissNote("Saved. Run the command in Terminal, then click the toolbar icon again.");
  } catch (_) {
    setMissNote("Save failed. From a Load unpacked folder, run python3 skill/scripts/install-native-host.py");
  }
}

async function copyNativeInstallCmd() {
  const cmd = nativeInstallCommand();
  try {
    await navigator.clipboard.writeText(cmd);
    setMissNote("Copied.");
  } catch (_) {
    if (missCmd) {
      const range = document.createRange();
      range.selectNodeContents(missCmd);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
    setMissNote("Select the command and copy it.");
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function collectImportantEventIds(data) {
  const ids = [];
  if (typeof edpWalkItems !== "function") return ids;
  edpWalkItems(data, (it) => {
    if (it && it.important && it.eventId && ids.indexOf(it.eventId) < 0) ids.push(it.eventId);
  });
  return ids;
}

function todayKey(tz) {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz || "America/Los_Angeles",
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(new Date());
    const pick = (t) => (parts.find((p) => p.type === t) || {}).value || "";
    return pick("year") + pick("month") + pick("day");
  } catch (_) {
    const d = new Date();
    return (
      String(d.getFullYear()) +
      String(d.getMonth() + 1).padStart(2, "0") +
      String(d.getDate()).padStart(2, "0")
    );
  }
}

function briefingDayKey(data) {
  const gen = String((data && data.generatedAt) || "").trim();
  if (/^\d{8}T\d{6}/.test(gen)) return gen.slice(0, 8);
  let found = "";
  if (typeof edpWalkItems === "function") {
    edpWalkItems(data, (it) => {
      if (found) return;
      const start = String((it && it.startStamp) || "");
      if (/^\d{8}T\d{6}/.test(start)) found = start.slice(0, 8);
    });
  }
  if (found) return found;
  const stamp = String((data && data.stamp) || "");
  const match = stamp.match(
    /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\b/i
  );
  if (!match) return "";
  const months = {
    jan: "01",
    feb: "02",
    mar: "03",
    apr: "04",
    may: "05",
    jun: "06",
    jul: "07",
    aug: "08",
    sep: "09",
    oct: "10",
    nov: "11",
    dec: "12"
  };
  const mon = months[match[1].slice(0, 3).toLowerCase()];
  if (!mon) return "";
  const today = todayKey((data && data.timezone) || "America/Los_Angeles");
  let year = today.slice(0, 4);
  let key = year + mon + String(match[2]).padStart(2, "0");
  if (key > today) key = String(Number(year) - 1) + mon + String(match[2]).padStart(2, "0");
  return key;
}

function briefingIsToday(data) {
  if (!data || typeof data !== "object") return false;
  const day = briefingDayKey(data);
  if (!day) return true;
  return day === todayKey(data.timezone || "America/Los_Angeles");
}

async function readPublishedPage() {
  if (!bridgeUrl) return null;
  try {
    const resp = await fetch(bridgeUrl + "/briefing.json?t=" + Date.now(), {
      signal: AbortSignal.timeout(4000)
    });
    if (resp.ok) {
      const data = await resp.json();
      if (data && data.unpublished) return null;
      if (data && typeof data === "object" && (data.sections || data.timezone || data.stamp || data.name)) {
        return data;
      }
    }
  } catch (_) {}
  try {
    const html = await fetch(bridgeUrl + "/current.html?t=" + Date.now()).then((r) => r.text());
    const start = '<script type="application/json" id="briefing-data">';
    const i = html.indexOf(start);
    if (i < 0) return null;
    const from = i + start.length;
    const j = html.indexOf("</script>", from);
    if (j < 0) return null;
    const data = JSON.parse(html.slice(from, j));
    if (!data || typeof data !== "object") return null;
    return data;
  } catch (_) {
    return null;
  }
}

async function waitForNewPublishedPage(prevStamp) {
  for (let i = 0; i < 40; i++) {
    try {
      const data = await readPublishedPage();
      if (data && data.sections && !data.unpublished) {
        const stamp = String(data.generatedAt || data.stamp || "");
        if (!prevStamp || stamp !== prevStamp) return data;
      }
    } catch (_) {}
    await sleep(250);
  }
  return readPublishedPage();
}

async function refreshCalendarThenShow() {
  const data = await waitForNewPublishedPage(pageStampAtRun);
  if (data && bridgeUrl) {
    setStatus("Published. Refreshing calendar…");
    try {
      const resp = await fetch(bridgeUrl + "/calendar/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          timezone: data.timezone || "America/Los_Angeles",
          shiftStart: data.shiftStart || "08:00",
          shiftEnd: data.shiftEnd || "17:00",
          importantEventIds: collectImportantEventIds(data)
        }),
        signal: AbortSignal.timeout(8000)
      });
      await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error("Calendar refresh failed");
    } catch (_) {}
  }
  showPage();
  setStatus("Published");
}

async function storedToken() {
  const stored = await chrome.storage.local.get(["gatewayToken"]);
  return (stored.gatewayToken || "").trim();
}

async function storedModel() {
  const stored = await chrome.storage.local.get(["gatewayModel"]);
  return (stored.gatewayModel || "").trim();
}

async function storedRunner() {
  const stored = await chrome.storage.local.get(["planRunner"]);
  return (stored.planRunner || "").trim();
}

const PLANNER_MCP_ROWS = [
  { id: "orgcs", label: "OrgCS" },
  { id: "gus", label: "GUS" },
  { id: "slack", label: "Slack" },
  { id: "gmail", label: "Gmail" },
  { id: "calendar", label: "Calendar" }
];

function mcpStatusLabel(status, note) {
  if (status === "connected") return ["Connected", "mcp-ok"];
  if (status === "checking") return ["Checking…", "mcp-checking"];
  const why = String(note || "").trim();
  return [why ? "Disconnected — " + why : "Disconnected", "mcp-bad"];
}

function selectedRunner() {
  return (runnerInput && runnerInput.value) || "";
}

function renderMcps(rows) {
  if (!mcpList) return;
  mcpList.replaceChildren();
  const list = rows && rows.length ? rows : PLANNER_MCP_ROWS;
  list.forEach((row) => {
    const [text, cls] = mcpStatusLabel(row && row.status, row && row.note);
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "mcp-name";
    name.textContent = (row && (row.label || row.id)) || "MCP";
    const st = document.createElement("span");
    st.className = cls;
    st.textContent = text;
    const side = document.createElement("span");
    side.className = "mcp-side";
    side.appendChild(st);
    if (row && row.id === "orgcs" && row.status !== "connected" && row.status !== "checking") {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn";
      btn.textContent = "Use browser session";
      btn.addEventListener("click", useOrgcsBrowserSession);
      side.appendChild(btn);
    }
    if (row && row.id === "gmail" && googleNeedsSignIn(list)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn";
      btn.textContent = "Sign in to Google (Gmail & Calendar)";
      btn.addEventListener("click", startDxGoogleAuth);
      side.appendChild(btn);
    }
    if (row && row.id === "gus" && row.status !== "connected" && row.status !== "checking") {
      const gusBtn = document.createElement("button");
      gusBtn.type = "button";
      gusBtn.className = "btn";
      gusBtn.textContent = "Sign in to GUS";
      gusBtn.addEventListener("click", startDxGusAuth);
      side.appendChild(gusBtn);
    }
    li.appendChild(name);
    li.appendChild(side);
    mcpList.appendChild(li);
  });
}

async function startDxGusAuth() {
  setTokenMsg("Opening the GUS sign-in…");
  if (!bridgeUrl && !(await pingBridge())) {
    setTokenMsg("The local bridge is not running.");
    return;
  }
  try {
    const resp = await fetch(bridgeUrl + "/mcp/gus-auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not start the GUS sign-in");
    setTokenMsg("Finish the GUS sign-in in the browser, then this list refreshes.");
    setTimeout(loadMcps, 8000);
  } catch (err) {
    setTokenMsg(err && err.message ? err.message : "Could not start the GUS sign-in");
  }
}

function googleNeedsSignIn(list) {
  return (list || []).some((row) => {
    return row && (row.id === "gmail" || row.id === "calendar") && row.status !== "connected" && row.status !== "checking";
  });
}

let googleAuthBusy = false;

async function startDxGoogleAuth() {
  if (googleAuthBusy) return;
  googleAuthBusy = true;
  setTokenMsg("Opening the Google sign-in…");
  if (!bridgeUrl && !(await pingBridge())) {
    setTokenMsg("The local bridge is not running.");
    return;
  }
  try {
    const resp = await fetch(bridgeUrl + "/mcp/google-auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not start the Google sign-in");
    setTokenMsg("Finish the Google sign-in in the browser, then this list refreshes.");
    setTimeout(loadMcps, 8000);
  } catch (err) {
    setTokenMsg(err && err.message ? err.message : "Could not start the Google sign-in");
  } finally {
    setTimeout(() => { googleAuthBusy = false; }, 15000);
  }
}

async function orgcsBrowserSid() {
  if (!chrome.cookies || !chrome.cookies.get) return "";
  const urls = ["https://orgcs.my.salesforce.com/", "https://orgcs.lightning.force.com/"];
  for (let i = 0; i < urls.length; i++) {
    try {
      const cookie = await chrome.cookies.get({ url: urls[i], name: "sid" });
      if (cookie && cookie.value) return String(cookie.value);
    } catch (_) {}
  }
  return "";
}

async function pushOrgcsBrowserSession() {
  if (!bridgeUrl && !(await pingBridge())) return false;
  const sid = await orgcsBrowserSid();
  if (!sid) return false;
  const resp = await fetch(bridgeUrl + "/orgcs/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sid })
  });
  return resp.ok;
}

async function useOrgcsBrowserSession() {
  setTokenMsg("Checking the OrgCS session in this browser…");
  const ok = await pushOrgcsBrowserSession();
  if (!ok) {
    setTokenMsg("Sign in to OrgCS in this browser, then try again.");
    return;
  }
  setTokenMsg("");
  await loadMcps();
}

async function loadMcps() {
  renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "checking" })));
  if (!bridgeUrl && !(await pingBridge())) {
    renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "disconnected" })));
    return;
  }
  try {
    const runner = selectedRunner();
    const resp = await fetch(bridgeUrl + "/mcp/status?runner=" + encodeURIComponent(runner));
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not read MCP status");
    let rows = body.mcps || [];
    const orgcsDown = rows.some((row) => row && row.id === "orgcs" && row.status !== "connected");
    if (orgcsDown && (await pushOrgcsBrowserSession())) {
      const again = await fetch(bridgeUrl + "/mcp/status?runner=" + encodeURIComponent(runner));
      const retry = await again.json().catch(() => ({}));
      if (again.ok && !retry.error) rows = retry.mcps || rows;
    }
    renderMcps(rows);
  } catch (_) {
    renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "disconnected" })));
  }
}

function resetModelSelect(label) {
  modelInput.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = label || "Save a token to load models";
  modelInput.appendChild(opt);
  modelInput.disabled = true;
}

function usablePlannerModel(id) {
  const low = String(id || "").toLowerCase();
  if (!low) return false;
  if (
    /(bedrock|vertex|embedding|whisper|tts|dall-e|dalle|imagen|moderation|transcri|codec|auto-router|auto-model|-1m\b|codex)/.test(
      low
    )
  ) {
    return false;
  }
  if (low.startsWith("us.") || low.startsWith("global.") || low.startsWith("anthropic.")) return false;
  if (low.indexOf("anthropic.claude") >= 0) return false;
  return true;
}

function fillModelSelect(models, pick, fallback) {
  const rows = (Array.isArray(models) ? models : []).filter((row) => row && usablePlannerModel(row.id));
  modelInput.innerHTML = "";
  if (!rows.length) {
    resetModelSelect("No usable models");
    return "";
  }
  let chosen = pick || "";
  if (!rows.some((row) => row && row.id === chosen)) chosen = fallback || rows[0].id || "";
  if (chosen && !usablePlannerModel(chosen)) chosen = fallback || rows[0].id || "";
  if (chosen && !rows.some((row) => row && row.id === chosen)) chosen = rows[0].id || "";
  rows.forEach((row) => {
    if (!row || !row.id) return;
    const opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.label || row.id;
    modelInput.appendChild(opt);
  });
  modelInput.value = chosen;
  modelInput.disabled = false;
  return chosen;
}

async function persistModel(model) {
  const id = (model || "").trim();
  if (!id) return;
  await chrome.storage.local.set({ gatewayModel: id });
  if (modelInput && [...modelInput.options].some((opt) => opt.value === id)) modelInput.value = id;
}

function resetRunnerSelect(label) {
  if (!runnerInput) return;
  runnerInput.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = label || "Pick a runner";
  runnerInput.appendChild(opt);
  runnerInput.disabled = true;
}

function preferredRunner(installed) {
  const ids = (installed || []).map((row) => row && row.id).filter(Boolean);
  if (ids.includes("claude")) return "claude";
  return ids[0] || "";
}

function fillRunnerSelect(runners, pick) {
  if (!runnerInput) return "";
  const rows = Array.isArray(runners) ? runners : [];
  runnerInput.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = rows.length ? "Pick a runner" : "No coding-agent CLI found";
  runnerInput.appendChild(placeholder);
  const installed = rows.filter((row) => row && row.id && row.installed);
  const chosen = preferredRunner(installed);
  rows.forEach((row) => {
    if (!row || !row.id) return;
    const opt = document.createElement("option");
    opt.value = row.id;
    opt.disabled = !row.installed;
    opt.textContent = row.installed
      ? (row.note ? (row.label || row.id) + " — " + row.note : (row.label || row.id))
      : (row.label || row.id) + " — not installed";
    runnerInput.appendChild(opt);
  });
  runnerInput.value = chosen;
  runnerInput.disabled = false;
  return chosen;
}

async function loadRunners() {
  if (!runnerInput) return "Runner list is missing.";
  if (!bridgeUrl && !(await pingBridge())) {
    resetRunnerSelect("Starting local planner…");
    return "Could not reach the local planner.";
  }
  try {
    const resp = await fetch(bridgeUrl + "/runners");
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not list runners");
    const saved = await storedRunner();
    const chosen = fillRunnerSelect(body.runners || [], saved);
    if (chosen && chosen !== saved) {
      await chrome.storage.local.set({ planRunner: chosen });
    }
    if (!chosen) {
      const listed = Array.isArray(body.runners) ? body.runners : [];
      return listed.length ? "" : "No coding-agent CLI found. Install one, then Reload.";
    }
    return "";
  } catch (err) {
    const msg = (err && err.message) || "Could not list runners";
    resetRunnerSelect(msg);
    return msg;
  }
}

async function loadModels(token) {
  const tok = (token || "").trim();
  if (!tok) {
    resetModelSelect("Save a token to load models");
    return;
  }
  if (!bridgeUrl && !(await pingBridge())) {
    resetModelSelect("Starting local planner…");
    return;
  }
  modelInput.disabled = true;
  setTokenMsg("Loading models…");
  try {
    const resp = await fetch(bridgeUrl + "/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: tok })
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not list models");
    const saved = await storedModel();
    const chosen = fillModelSelect(body.models || [], saved, body.default || "");
    if (chosen) await persistModel(chosen);
    setTokenMsg("");
    return "";
  } catch (err) {
    const msg = (err && err.message) || "Could not load models";
    resetModelSelect(msg);
    setTokenMsg(msg);
    return msg;
  }
}

function openTokenPopup() {
  tokenRequired = false;
  if (tokenCancel) tokenCancel.hidden = false;
  tokenLayer.hidden = false;
  setTokenMsg("");
  tokenInput.value = "";
  loadRunners().then(() => loadMcps());
  storedToken().then((tok) => {
    tokenInput.placeholder = tok ? "Token saved — paste to replace" : "Paste your token";
    if (tok) loadModels(tok);
    else resetModelSelect("Save a token to load models");
  });
  setTimeout(() => tokenInput.focus(), 0);
}

function closeTokenPopup() {
  tokenRequired = false;
  if (tokenCancel) tokenCancel.hidden = false;
  tokenLayer.hidden = true;
  tokenInput.value = "";
}

async function saveSettings() {
  const pasted = (tokenInput.value || "").trim();
  const tok = pasted || (await storedToken());
  if (!tok) {
    setTokenMsg("Paste a token, or use DevBar.");
    tokenInput.focus();
    return false;
  }
  const model = (modelInput.value || "").trim();
  if (!model) {
    if (pasted) await chrome.storage.local.set({ gatewayToken: pasted });
    const modelErr = await loadModels(tok);
    setTokenMsg(modelErr || "Pick a model, then Save.");
    return false;
  }
  const rec = { gatewayToken: tok, gatewayModel: model, planRunner: "claude" };
  await chrome.storage.local.set(rec);
  await persistModel(model);
  tokenRequired = false;
  tokenCancel.hidden = false;
  tokenLayer.hidden = true;
  tokenInput.value = "";
  tokenInput.placeholder = "Token saved — paste to replace";
  setStatus("Using " + model);
  return true;
}

async function saveFromDevBar() {
  setTokenMsg("Asking the local bridge for DevBar…");
  if (!bridgeUrl) await pingBridge();
  if (!bridgeUrl) {
    setTokenMsg("Could not start the local planner. Reload this tab in a moment.");
    return;
  }
  try {
    const resp = await fetch(bridgeUrl + "/gateway-token");
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || !body.token) {
      setTokenMsg(body.error || "DevBar did not return a token. Paste one instead.");
      return;
    }
    await chrome.storage.local.set({ gatewayToken: body.token });
    tokenInput.value = "";
    tokenInput.placeholder = "Token saved — paste to replace";
    tokenRequired = false;
    tokenCancel.hidden = false;
    const modelErr = await loadModels(body.token);
    const runnerErr = await loadRunners();
    await loadMcps();
    if (modelErr || runnerErr) {
      setTokenMsg([modelErr, runnerErr].filter(Boolean).join(" "));
      return;
    }
    setTokenMsg("Token saved. Pick a model and a runner, then Save.");
  } catch (_) {
    setTokenMsg("Could not reach the bridge.");
  }
}

async function ensureToken() {
  const tok = await storedToken();
  if (tok) {
    tokenRequired = false;
    tokenCancel.hidden = false;
    return tok;
  }
  if (!bridgeUrl && !(await pingBridge())) return "";
  openTokenPopup();
  return "";
}

function showBridgeMiss(detail) {
  closeTokenPopup();
  missEl.hidden = false;
  const firstTime = !detail || detail.hostMissing !== false;
  if (missInstall) missInstall.hidden = !firstTime;
  if (missAgain) missAgain.hidden = firstTime;
  if (missError) missError.textContent = firstTime ? "" : String((detail && detail.hostError) || "");
  clearFrame();
  askFab.hidden = true;
  closeAsk();
  setStatus(firstTime ? "Bridge did not start" : "Bridge stopped");
  startMissPoll();
}

async function pingBridge() {
  setStatus("Looking for the local bridge…");
  try {
    const res = await chrome.runtime.sendMessage({ type: "findBridge" });
    bridgeUrl = (res && res.bridgeUrl) || "";
    if (!bridgeUrl) {
      showBridgeMiss(res || { hostMissing: true });
      return "";
    }
    showPage();
    return bridgeUrl;
  } catch (err) {
    showBridgeMiss({ hostMissing: false, hostError: String((err && err.message) || err || "") });
    return "";
  }
}

async function runPlanner() {
  const token = await ensureToken();
  if (!token) return;
  const model = await storedModel();
  if (!model) {
    openTokenPopup();
    setTokenMsg("Pick a model, then Save.");
    return;
  }
  const runner = "claude";
  if (!bridgeUrl && !(await pingBridge())) return;
  await pushOrgcsBrowserSession();
  try {
    const before = await readPublishedPage();
    pageStampAtRun = (before && (before.generatedAt || before.stamp)) || "";
  } catch (_) {
    pageStampAtRun = "";
  }
  setRunUi(true);
  resetSteps();
  setStatus("Starting planner run…");
  try {
    const resp = await fetch(bridgeUrl + "/plan/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, model, runner })
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.status === 409) {
      setStatus("A run is already in progress");
      setRunUi(true);
      applyPlanStatus(body, true);
    } else if (!resp.ok) {
      setStatus(body.error || "Could not start the run");
      setRunUi(false);
      return;
    } else {
      planStartedAt = body.startedAt || "";
      setStatus("Gathering with " + (body.model || model) + (body.runnerLabel ? " via " + body.runnerLabel : ""));
      applyPlanStatus(body, true);
    }
  } catch (err) {
    setStatus("Bridge did not accept the run");
    setRunUi(false);
    return;
  }
  pollStatus();
}

async function stopPlanner() {
  if (!bridgeUrl && !(await pingBridge())) return;
  if (stopBtn) stopBtn.disabled = true;
  setStatus("Stopping…");
  try {
    const resp = await fetch(bridgeUrl + "/plan/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const body = await resp.json().catch(() => ({}));
    applyPlanStatus(body, false);
    setStatus(body.error || "Stopped.");
  } catch (_) {
    setStatus("Could not stop the run");
  }
  if (stopBtn) stopBtn.disabled = false;
  setRunUi(false);
}

function elapsedLabel(startedAt) {
  const raw = startedAt || planStartedAt;
  if (!raw) return "";
  const t = Date.parse(raw);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  const m = Math.floor(s / 60);
  return m + ":" + String(s % 60).padStart(2, "0");
}

function runningStatus(body) {
  const clock = elapsedLabel(body && body.startedAt);
  return (clock ? clock + " · " : "") + "Gathering";
}

function resetSteps() {
  planSteps = [];
  planAfter = 0;
  planStartedAt = "";
  renderSteps();
}

function stepVisible(step) {
  if (!step || planLogMode === "off") return false;
  const kind = step.kind || "log";
  if (planLogMode === "compact") {
    return kind === "init" || kind === "tool" || kind === "done" || kind === "error" || kind === "log";
  }
  return true;
}

function renderSteps() {
  if (!stepsLog) return;
  const clock = elapsedLabel(planStartedAt);
  if (stepsTitle) stepsTitle.textContent = clock ? "Logs · " + clock : "Logs";
  stepsLog.replaceChildren();
  planSteps.forEach((step) => {
    if (!stepVisible(step)) return;
    const li = document.createElement("li");
    li.className = "kind-" + (step.kind || "log");
    const label = document.createElement("span");
    label.textContent = step.label || "";
    li.appendChild(label);
    if (planLogMode === "verbose" && step.detail) {
      const detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = step.detail;
      li.appendChild(detail);
    }
    stepsLog.appendChild(li);
  });
  stepsLog.scrollTop = stepsLog.scrollHeight;
}

function openSteps() {
  if (!stepsDock) return;
  stepsDock.hidden = false;
}

function closeSteps() {
  if (!stepsDock) return;
  stepsDock.hidden = true;
}

function applyPlanStatus(body, replace) {
  if (!body || typeof body !== "object") return;
  if (body.startedAt) planStartedAt = body.startedAt;
  const incoming = Array.isArray(body.steps) ? body.steps : [];
  if (replace) {
    planSteps = incoming.slice();
  } else {
    incoming.forEach((step) => planSteps.push(step));
  }
  if (typeof body.stepCount === "number") planAfter = body.stepCount;
  else planAfter = planSteps.length;
  renderSteps();
}

async function pollStatus() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = 0;
  }
  let misses = 0;
  const tick = async () => {
    if (!bridgeUrl) return;
    try {
      const resp = await fetch(
        bridgeUrl + "/plan/status?after=" + encodeURIComponent(String(planAfter)),
        { signal: AbortSignal.timeout(8000) }
      );
      const body = await resp.json();
      misses = 0;
      const state = body.state || "idle";
      applyPlanStatus(body, false);
      if (state === "running") {
        setRunUi(true);
        setStatus(runningStatus(body));
        pollTimer = setTimeout(tick, 800);
        return;
      }
      pollTimer = 0;
      setRunUi(false);
      if (state === "ok") {
        setStatus("Published. Refreshing calendar…");
        await refreshCalendarThenShow();
      } else if (state === "error") {
        setStatus(body.error || "Planner run failed");
      } else {
        setStatus("Idle");
      }
    } catch (_) {
      misses += 1;
      if (misses < 5) {
        setStatus("Gathering… reconnecting to the local bridge");
        try {
          const res = await chrome.runtime.sendMessage({ type: "findBridge" });
          if (res && res.bridgeUrl) bridgeUrl = res.bridgeUrl;
        } catch (_) {}
        pollTimer = setTimeout(tick, 800);
        return;
      }
      pollTimer = 0;
      setRunUi(false);
      setStatus("Lost the bridge while gathering");
    }
  };
  await tick();
}

async function resumePlanIfRunning() {
  resetSteps();
  closeSteps();
  if (!bridgeUrl) return;
  try {
    const resp = await fetch(bridgeUrl + "/plan/status?after=0");
    const body = await resp.json();
    if ((body.state || "idle") !== "running") return;
    applyPlanStatus(body, true);
    setRunUi(true);
    setStatus(runningStatus(body));
    pollStatus();
  } catch (_) {}
}

function appendAsk(role, text, meta) {
  const el = document.createElement("div");
  el.className = "ask-msg " + role;
  el.textContent = text;
  if (meta) {
    const m = document.createElement("p");
    m.className = "ask-meta";
    m.textContent = meta;
    el.appendChild(m);
  }
  askLog.appendChild(el);
  askLog.scrollTop = askLog.scrollHeight;
}

async function pageJsonWithDone() {
  if (!bridgeUrl) return null;
  try {
    const data = await readPublishedPage();
    if (!data || typeof data !== "object" || !briefingIsToday(data)) return null;
    const stored = await chrome.storage.local.get(["edpDone"]);
    edpApplyLedger(data, edpActiveKeys(stored.edpDone || {}));
    return data;
  } catch (_) {
    return null;
  }
}

async function sendAsk(question) {
  const q = String(question || "").trim();
  if (!q) return;
  const token = await ensureToken();
  if (!token) return;
  const model = await storedModel();
  if (!model) {
    openTokenPopup();
    setTokenMsg("Pick a model, then Save.");
    return;
  }
  if (!bridgeUrl && !(await pingBridge())) {
    appendAsk("err", "Could not start the local planner.");
    return;
  }
  askInput.value = "";
  appendAsk("you", q);
  askSend.disabled = true;
  const t0 = performance.now();
  try {
    const page = await pageJsonWithDone();
    const payload = { question: q, token, model, history: askHistory.slice(-6) };
    if (page) payload.page = page;
    const clockStored = await chrome.storage.local.get(["edpPlanClock", "edpViewZone", "edpViewShort"]);
    payload.clock = clockStored.edpPlanClock || planClockPref || "shift";
    const viewZone = clockStored.edpViewZone || planViewZone;
    const viewShort = clockStored.edpViewShort || planViewShort;
    if (viewZone) payload.viewZone = viewZone;
    if (viewShort) payload.viewShort = viewShort;
    const resp = await fetch(bridgeUrl + "/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Planner Buddy failed");
    const rt = Math.round(performance.now() - t0);
    const bits = [];
    if (body.model) bits.push(body.model);
    if (body.ms != null) bits.push(body.ms + " ms");
    bits.push(rt + " ms round trip");
    const answer = body.answer || "No answer.";
    appendAsk("bot", answer, bits.join(" · "));
    askHistory.push({ role: "user", text: q });
    askHistory.push({ role: "assistant", text: answer });
    if (askHistory.length > 12) askHistory = askHistory.slice(-12);
    if (body.done && Array.isArray(body.done.keys) && body.done.keys.length) {
      const stored = await chrome.storage.local.get(["edpDone"]);
      const rec = stored.edpDone && stored.edpDone.keys ? stored.edpDone : { keys: {}, updatedAt: 0 };
      rec.keys = rec.keys || {};
      const now = Date.now();
      body.done.keys.forEach((k) => {
        if (!k) return;
        if (body.done.on) rec.keys[k] = now;
        else delete rec.keys[k];
      });
      rec.updatedAt = now;
      await chrome.storage.local.set({ edpDone: rec });
    }
    if (body.reload) showPage();
  } catch (err) {
    appendAsk("err", (err && err.message) || "Could not reach Planner Buddy.");
  } finally {
    askSend.disabled = false;
  }
}

runBtn.addEventListener("click", runPlanner);
if (stopBtn) stopBtn.addEventListener("click", stopPlanner);
reloadBtn.addEventListener("click", pingBridge);
stepsBtn.addEventListener("click", () => {
  if (!stepsDock.hidden) closeSteps();
  else openSteps();
});
stepsClose.addEventListener("click", closeSteps);
stepsCopy.addEventListener("click", async () => {
  const lines = planSteps
    .filter(stepVisible)
    .map((step) => {
      const line = step.label || "";
      return planLogMode === "verbose" && step.detail ? line + "\n  " + step.detail : line;
    })
    .filter(Boolean);
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
  } catch (_) {}
});
stepsMode.addEventListener("change", async () => {
  planLogMode = stepsMode.value || "compact";
  await chrome.storage.local.set({ planLogMode });
  if (planLogMode === "off") closeSteps();
  else openSteps();
  renderSteps();
});
tokenBtn.addEventListener("click", async () => {
  if (!bridgeUrl && !(await pingBridge())) return;
  openTokenPopup();
});
tokenSave.addEventListener("click", () => saveSettings());
tokenDevbar.addEventListener("click", saveFromDevBar);
tokenCancel.addEventListener("click", closeTokenPopup);
if (tokenLayer) {
  tokenLayer.addEventListener("click", (ev) => {
    if (ev.target === tokenLayer) closeTokenPopup();
  });
}
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && tokenLayer && !tokenLayer.hidden) closeTokenPopup();
});
if (mcpRefresh) mcpRefresh.addEventListener("click", () => loadMcps());
tokenInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") saveSettings();
});
tokenInput.addEventListener("input", () => {
  const tok = tokenInput.value.trim();
  if (modelTimer) clearTimeout(modelTimer);
  if (tok.length < 20) return;
  modelTimer = setTimeout(() => loadModels(tok), 400);
});
modelInput.addEventListener("change", async () => {
  const model = (modelInput.value || "").trim();
  if (!model) return;
  await persistModel(model);
});
if (runnerInput) {
  runnerInput.addEventListener("change", async () => {
    const runner = (runnerInput.value || "").trim();
    if (!runner) return;
    await chrome.storage.local.set({ planRunner: runner });
    loadMcps();
  });
}
askFab.addEventListener("click", () => {
  if (!askDock.hidden) closeAsk();
  else openAsk();
});
askClose.addEventListener("click", closeAsk);
const askChips = document.getElementById("ask-chips");
if (askChips) {
  askChips.addEventListener("click", (ev) => {
    const chip = ev.target.closest && ev.target.closest("[data-ask]");
    if (!chip) return;
    sendAsk(chip.getAttribute("data-ask"));
  });
}
askForm.addEventListener("submit", (ev) => {
  ev.preventDefault();
  sendAsk(askInput.value);
});

const HELP_URL = "https://salesforce.enterprise.slack.com/docs/T01G0063H29/F0C3D408Y4V";
const helpBtn = document.getElementById("help-btn");
if (helpBtn) helpBtn.addEventListener("click", () => {
  window.open(HELP_URL, "_blank", "noopener");
});

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || !msg.type) return;
  if (msg.type !== "edpOpened") return;
  resumePlanIfRunning();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.edpPlanClock) {
    const clock = String(changes.edpPlanClock.newValue || "").trim();
    if (clock === "local" || clock === "shift") planClockPref = clock;
  }
  if (changes.edpViewZone && changes.edpViewZone.newValue) {
    planViewZone = String(changes.edpViewZone.newValue);
  }
  if (changes.edpViewShort && changes.edpViewShort.newValue) {
    planViewShort = String(changes.edpViewShort.newValue);
  }
  if (!changes.gatewayModel) return;
  const id = String(changes.gatewayModel.newValue || "").trim();
  if (!id) return;
  if (modelInput && [...modelInput.options].some((opt) => opt.value === id)) modelInput.value = id;
});

(function tabFlash() {
  var timer = null;
  var REAL_TITLE = "Engineer Day Planner";
  var flashMsg = "Notification";
  function isFlashTitle(t) {
    t = String(t || "").trim();
    return t === flashMsg || /^(?:[●⚠]\s*)+/.test(t) || /^(?:●+\s*)*logout(?:\s*●+)*$/i.test(t);
  }
  function clearAlertIcon() {
    var links = document.querySelectorAll("link[rel~='icon']");
    for (var i = 0; i < links.length; i++) {
      if (String(links[i].href || "").indexOf("data:image") === 0) links[i].remove();
    }
  }
  try {
    var kept = sessionStorage.getItem("edpRealTabTitle");
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
    timer = setInterval(function () {
      document.title = document.title === REAL_TITLE ? flashMsg : REAL_TITLE;
    }, 1000);
  }
  function paint(on, msg) {
    if (on) startFlash(msg);
    else stopFlash();
  }
  chrome.runtime.onMessage.addListener(function (msg) {
    if (!msg || msg.type !== "edpTabFlash") return;
    paint(!!msg.on, msg.msg);
  });
  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== "local" || !changes.edpTabBlinkOn) return;
    chrome.storage.local.get(["edpTabBlinkOn", "edpTabBlinkMsg"], function (rec) {
      paint(!!(rec && rec.edpTabBlinkOn), rec && rec.edpTabBlinkMsg);
    });
  });
  chrome.storage.local.get(["edpTabBlinkOn", "edpTabBlinkMsg"], function (rec) {
    if (rec && rec.edpTabBlinkOn) paint(true, rec.edpTabBlinkMsg);
    else if (isFlashTitle(document.title)) stopFlash();
  });
})();

if (missCopy) missCopy.addEventListener("click", copyNativeInstallCmd);
if (missRetry) missRetry.addEventListener("click", () => pingBridge());

const updateBtn = document.getElementById("update-btn");

async function refreshUpdateButton() {
  if (!updateBtn) return;
  try {
    const res = await chrome.runtime.sendMessage({ type: "updateCheck" });
    updateBtn.hidden = !(res && res.update === true);
  } catch (_) {
    updateBtn.hidden = true;
  }
}

if (updateBtn) {
  updateBtn.addEventListener("click", async () => {
    updateBtn.disabled = true;
    updateBtn.textContent = "Updating";
    let res = null;
    try {
      res = await chrome.runtime.sendMessage({ type: "updateApply" });
    } catch (err) {
      res = { ok: false, error: String((err && err.message) || err || "Update failed.") };
    }
    if (!res || res.ok === false) {
      updateBtn.disabled = false;
      updateBtn.textContent = "Update";
      setStatus((res && res.error) || "Update failed.");
    }
  });
}

(async function boot() {
  const verEl = document.getElementById("ext-version");
  if (verEl) {
    try {
      const manifest = chrome.runtime.getManifest() || {};
      const ver = manifest.version_name || manifest.version || "";
      verEl.textContent = ver ? "Version: " + ver : "";
    } catch (_) {}
  }
  refreshUpdateButton();
  const welcomed = await chrome.storage.local.get(["edpWelcomed"]);
  if (!welcomed.edpWelcomed) {
    await chrome.storage.local.set({ edpWelcomed: true });
    chrome.tabs.create({ url: chrome.runtime.getURL("welcome.html") });
  }
  fillMissHelp();
  const stored = await chrome.storage.local.get(["gatewayToken", "planLogMode", "edpPlanClock", "edpViewZone", "edpViewShort"]);
  planLogMode = stored.planLogMode || "compact";
  if (stored.edpPlanClock === "local" || stored.edpPlanClock === "shift") planClockPref = stored.edpPlanClock;
  if (stored.edpViewZone) planViewZone = stored.edpViewZone;
  if (stored.edpViewShort) planViewShort = stored.edpViewShort;
  if (stepsMode) stepsMode.value = planLogMode;
  const tok = (stored.gatewayToken || "").trim();
  await pingBridge();
  if (bridgeUrl && !tok) openTokenPopup();
  await loadRunners();
  if (tok) await loadModels(tok);
  await resumePlanIfRunning();
})();

(function closeGuard() {
  let allowUnload = false;
  function allowNextUnload() {
    allowUnload = true;
  }
  if (reloadBtn) reloadBtn.addEventListener("click", allowNextUnload);
  window.addEventListener(
    "keydown",
    (ev) => {
      const key = ev.key;
      if (key === "F5") allowNextUnload();
      if ((ev.metaKey || ev.ctrlKey) && (key === "r" || key === "R")) allowNextUnload();
    },
    true
  );
  window.addEventListener("beforeunload", (ev) => {
    if (allowUnload) {
      allowUnload = false;
      return;
    }
    ev.preventDefault();
    ev.returnValue = "";
  });
  window.addEventListener("pagehide", () => {
    const url = (bridgeUrl || "http://127.0.0.1:8765").replace(/\/$/, "");
    try {
      navigator.sendBeacon(url + "/__bye", "{}");
    } catch (_) {}
    try {
      chrome.runtime.sendMessage({ type: "edpPageClosed" });
    } catch (_) {}
  });
})();
