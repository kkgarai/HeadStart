const input = document.getElementById("token");
const modelInput = document.getElementById("model");
const runnerInput = document.getElementById("runner");
const msg = document.getElementById("msg");
let modelTimer = 0;

function setMsg(text) {
  msg.textContent = text;
}

function packNewerThanHave(packed, have) {
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
  const base = await bridgeBase();
  if (!base) {
    resetRunnerSelect("Starting local planner…");
    return "";
  }
  try {
    const resp = await fetch(base + "/runners");
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not list runners");
    const stored = await chrome.storage.local.get(["planRunner"]);
    const saved = stored.planRunner || "";
    const chosen = fillRunnerSelect(body.runners || [], saved);
    if (chosen && chosen !== saved) {
      await chrome.storage.local.set({ planRunner: chosen });
    }
    return chosen;
  } catch (_) {
    resetRunnerSelect("Could not list runners");
    return "";
  }
}

async function bridgeBase() {
  const stored = await chrome.storage.local.get(["bridgeUrl"]);
  let base = stored.bridgeUrl || "";
  if (!base) {
    const found = await chrome.runtime.sendMessage({ type: "findBridge" });
    base = (found && found.bridgeUrl) || "";
  }
  if (base) {
    try {
      const resp = await fetch(base + "/health", { signal: AbortSignal.timeout(1500) });
      const body = await resp.json();
      const packed = String((body && body.version) || "");
      const have = String((chrome.runtime.getManifest() || {}).version || "");
      if (packed && have && packNewerThanHave(packed, have)) {
        const tabs = await chrome.tabs.query({});
        const page = chrome.runtime.getURL("extension/panel.html");
        const busy = (tabs || []).some((tab) => String((tab && tab.url) || "").indexOf(page) === 0);
        if (!busy) chrome.runtime.reload();
      }
    } catch (_) {}
  }
  return base;
}

const PLANNER_MCP_ROWS = [
  { id: "orgcs", label: "OrgCS" },
  { id: "gus", label: "GUS" },
  { id: "slack", label: "Slack" },
  { id: "google", label: "Gmail & Calendar" }
];

function mcpStatusLabel(status) {
  if (status === "connected") return ["Connected", "mcp-ok"];
  if (status === "checking") return ["Checking…", "mcp-checking"];
  return ["Disconnected", "mcp-bad"];
}

function selectedRunner() {
  return (runnerInput && runnerInput.value) || "";
}

function renderMcps(rows) {
  const list = document.getElementById("mcp-list");
  if (!list) return;
  list.replaceChildren();
  (rows && rows.length ? rows : PLANNER_MCP_ROWS).forEach((row) => {
    const [text, cls] = mcpStatusLabel(row && row.status);
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = (row && (row.label || row.id)) || "MCP";
    const st = document.createElement("span");
    st.className = cls;
    st.textContent = text;
    li.appendChild(name);
    li.appendChild(st);
    list.appendChild(li);
  });
}

async function loadMcps() {
  renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "checking" })));
  const base = await bridgeBase();
  if (!base) {
    renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "disconnected" })));
    return;
  }
  try {
    const runner = selectedRunner();
    const resp = await fetch(base + "/mcp/status?runner=" + encodeURIComponent(runner));
    const body = await resp.json().catch(() => ({}));
    const rows = body.mcps || [];
    if ((!resp.ok || body.error) && !rows.length) throw new Error(body.error || "Could not read MCP status");
    renderMcps(rows);
  } catch (_) {
    renderMcps(PLANNER_MCP_ROWS.map((row) => ({ ...row, status: "disconnected" })));
  }
}

async function loadModels(token) {
  const tok = (token || "").trim();
  if (!tok) {
    resetModelSelect("Save a token to load models");
    return;
  }
  const base = await bridgeBase();
  if (!base) {
    resetModelSelect("Starting local planner…");
    setMsg("Could not start the local planner. Reload this tab in a moment.");
    return;
  }
  modelInput.disabled = true;
  setMsg("Loading models…");
  try {
    const resp = await fetch(base + "/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: tok })
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok || body.error) throw new Error(body.error || "Could not list models");
    const stored = await chrome.storage.local.get(["gatewayModel"]);
    const chosen = fillModelSelect(body.models || [], stored.gatewayModel || "", body.default || "");
    if (chosen) await chrome.storage.local.set({ gatewayModel: chosen });
    setMsg("");
  } catch (err) {
    resetModelSelect("Could not load models");
    setMsg((err && err.message) || "Could not load models.");
  }
}

chrome.storage.local.get(["gatewayToken"], (res) => {
  loadRunners().then(() => loadMcps());
  if (res.gatewayToken) {
    input.placeholder = "Token saved — paste to replace";
    loadModels(res.gatewayToken);
  }
});

document.getElementById("mcp-refresh").addEventListener("click", () => loadMcps());

document.getElementById("save").addEventListener("click", async () => {
  const pasted = input.value.trim();
  const stored = await chrome.storage.local.get(["gatewayToken"]);
  const tok = pasted || (stored.gatewayToken || "").trim();
  if (!tok) {
    setMsg("Paste a token, or use DevBar.");
    return;
  }
  const model = (modelInput.value || "").trim();
  if (!model) {
    if (pasted) await chrome.storage.local.set({ gatewayToken: pasted });
    await loadModels(tok);
    setMsg("Pick a model, then Save.");
    return;
  }
  const runner = "claude";
  await chrome.storage.local.set({ gatewayToken: tok, gatewayModel: model, planRunner: runner });
  input.value = "";
  input.placeholder = "Token saved — paste to replace";
  setMsg("Saved in this Chrome profile.");
});

document.getElementById("clear").addEventListener("click", () => {
  chrome.storage.local.remove(["gatewayToken", "gatewayModel", "planRunner"], () => {
    input.value = "";
    input.placeholder = "Paste your token";
    resetModelSelect("Save a token to load models");
    resetRunnerSelect("Pick a runner");
    loadRunners();
    setMsg("Cleared.");
  });
});

document.getElementById("discover").addEventListener("click", async () => {
  setMsg("Asking the local bridge for DevBar…");
  const base = await bridgeBase();
  if (!base) {
    setMsg("Could not start the local planner. Reload this tab in a moment.");
    return;
  }
  try {
    const resp = await fetch(base + "/gateway-token");
    const body = await resp.json();
    if (!resp.ok || !body.token) {
      setMsg(body.error || "DevBar did not return a token. Paste one instead.");
      return;
    }
    await chrome.storage.local.set({ gatewayToken: body.token });
    input.value = "";
    input.placeholder = "Token saved — paste to replace";
    await loadModels(body.token);
    await loadRunners();
    setMsg("Token saved. Pick a model and a runner, then Save.");
  } catch (_) {
    setMsg("Could not reach the bridge.");
  }
});

input.addEventListener("input", () => {
  const tok = input.value.trim();
  if (modelTimer) clearTimeout(modelTimer);
  if (tok.length < 20) return;
  modelTimer = setTimeout(() => loadModels(tok), 400);
});

modelInput.addEventListener("change", async () => {
  const model = (modelInput.value || "").trim();
  if (!model) return;
  await chrome.storage.local.set({ gatewayModel: model });
});
if (runnerInput) {
  runnerInput.addEventListener("change", async () => {
    const runner = (runnerInput.value || "").trim();
    if (!runner) return;
    await chrome.storage.local.set({ planRunner: runner });
    loadMcps();
  });
}
