/* RYNA 0.1.0 Frontend Controller — Zero Fake Data, Real Local Engine API */

const state = {
  status: null,
  settings: {},
  conversations: [],
  activeConvId: null,
  messages: [],
  tasks: [],
  memories: [],
  activeSettingsCat: "general",
  companionState: "idle",
  animPhase: 0,
};

const SETTINGS_CATEGORIES = [
  { id: "general", label: "General" },
  { id: "appearance", label: "Appearance" },
  { id: "voice", label: "Voice" },
  { id: "ai_models", label: "AI Models" },
  { id: "offline_ai", label: "Offline AI" },
  { id: "online_ai", label: "Online AI" },
  { id: "memory", label: "Memory" },
  { id: "privacy", label: "Privacy" },
  { id: "permissions", label: "Permissions" },
  { id: "computer_control", label: "Computer Control" },
  { id: "notifications", label: "Notifications" },
  { id: "devices", label: "Devices" },
  { id: "storage", label: "Storage" },
  { id: "startup", label: "Startup" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "updates", label: "Updates" },
];

const COMPANION_STYLE = {
  idle: { accent: "#22d3ee", ring: "#0ea5e9", speed: 0.03 },
  listening: { accent: "#38bdf8", ring: "#06b6d4", speed: 0.09 },
  thinking: { accent: "#818cf8", ring: "#38bdf8", speed: 0.12 },
  speaking: { accent: "#2dd4bf", ring: "#22d3ee", speed: 0.15 },
  working: { accent: "#60a5fa", ring: "#2563eb", speed: 0.11 },
  waiting: { accent: "#fbbf24", ring: "#f59e0b", speed: 0.04 },
  alert: { accent: "#fb7185", ring: "#ef4444", speed: 0.18 },
  sleeping: { accent: "#64748b", ring: "#334155", speed: 0.015 },
};

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(window.__rynaToastTimer);
  window.__rynaToastTimer = setTimeout(() => el.classList.add("hidden"), 3000);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------- Navigation ---------- */
function switchView(viewId) {
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${viewId}`);
  });
  const titles = {
    assistant: "Assistant",
    companion: "Desktop Companion",
    tasks: "Task Center",
    memory: "Memory Center",
    settings: "Settings",
  };
  document.getElementById("topbar-title").textContent = titles[viewId] || "RYNA";
  if (viewId === "tasks") loadTasks();
  if (viewId === "memory") loadMemories();
  if (viewId === "settings") renderSettingsCategory();
}

/* ---------- Global Status & Settings ---------- */
async function refreshAll() {
  try {
    const [st, setResp] = await Promise.all([api("/api/v1/status"), api("/api/v1/settings")]);
    state.status = st;
    state.settings = setResp.settings || {};
    state.companionState = st.companion_state || "idle";

    document.getElementById("brand-version").textContent = `v${st.version}`;
    const connPill = document.getElementById("connectivity-pill");
    connPill.textContent = st.online ? "Mode: Online" : "Mode: Offline (Local)";
    connPill.className = `status-pill ${st.online ? "online" : "offline"}`;

    const aiPill = document.getElementById("ai-provider-pill");
    const availNames = Object.values(st.ai.providers || {})
      .filter((p) => p.available)
      .map((p) => `${p.name}${p.active_model ? ` (${p.active_model})` : ""}`);
    aiPill.textContent = availNames.length ? `AI: ${availNames[0]}` : "AI: Not Configured";

    document.getElementById("chip-companion-state").textContent = `State: ${state.companionState}`;
    const hw = st.hardware || {};
    document.getElementById("chip-hardware").textContent =
      `RAM: ${hw.ram_total_gb || "?"} GB • Tier: ${hw.recommended_tier || "?"}`;

    const banner = document.getElementById("emergency-banner");
    banner.classList.toggle("hidden", !st.emergency_stopped);

    const aiBanner = document.getElementById("ai-unavailable-banner");
    aiBanner.classList.toggle("hidden", !!st.ai.any_provider_available);
    if (!st.ai.any_provider_available) {
      const ollamaDetail = st.ai.providers?.ollama?.detail || "No local or cloud provider reachable.";
      document.getElementById("ai-unavailable-reason").textContent = ollamaDetail;
    }

    syncCompanionControls();
    syncMemoryControls();
    if (document.getElementById("view-settings").classList.contains("active")) {
      renderSettingsCategory();
    }
  } catch (err) {
    toast(`Status refresh failed: ${err.message}`);
  }
}

/* ---------- Conversations & Chat ---------- */
async function loadConversations() {
  const data = await api("/api/v1/conversations");
  state.conversations = data.conversations || [];
  if (!state.activeConvId && state.conversations.length > 0) {
    state.activeConvId = state.conversations[0].id;
  }
  renderConversationList();
  if (state.activeConvId) {
    await loadMessages(state.activeConvId);
  } else {
    state.messages = [];
    renderMessages();
  }
}

function renderConversationList() {
  const list = document.getElementById("conv-list");
  if (!state.conversations.length) {
    list.innerHTML = `<div class="muted" style="padding:8px;">No conversations yet. Start typing below or click + New.</div>`;
    return;
  }
  list.innerHTML = state.conversations
    .map(
      (c) => `
      <div class="conv-item ${c.id === state.activeConvId ? "active" : ""}" data-id="${c.id}">
        <span class="conv-title">${escapeHtml(c.title)}</span>
        <button class="conv-del" data-del="${c.id}" title="Delete conversation">✕</button>
      </div>`
    )
    .join("");
}

async function loadMessages(convId) {
  const data = await api(`/api/v1/conversations/${convId}/messages`);
  state.messages = data.messages || [];
  renderMessages();
}

function renderMessages() {
  const box = document.getElementById("chat-messages");
  if (!state.messages.length) {
    box.innerHTML = `<div class="muted">RYNA local conversation history is backed by SQLite. Send a message or run offline local tools (<code>/diag</code>, <code>/calc 15*24</code>, <code>/find README</code>, <code>/note ...</code>).</div>`;
    return;
  }
  box.innerHTML = state.messages
    .map((m) => {
      const metaParts = [m.role.toUpperCase()];
      if (m.provider) metaParts.push(m.provider);
      if (m.model) metaParts.push(m.model);
      metaParts.push(m.mode);
      return `
        <div class="msg ${escapeHtml(m.role)}">
          <div>${escapeHtml(m.content)}</div>
          <div class="msg-meta">${escapeHtml(metaParts.join(" • "))}</div>
        </div>`;
    })
    .join("");
  box.scrollTop = box.scrollHeight;
}

async function handleSendMessage(evt) {
  evt.preventDefault();
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;

  const btn = document.getElementById("btn-send");
  btn.disabled = true;
  input.value = "";
  try {
    const res = await api("/api/v1/chat", {
      method: "POST",
      body: { conversation_id: state.activeConvId, message: text },
    });
    state.activeConvId = res.conversation_id;
    await loadConversations();
    await refreshAll();
  } catch (err) {
    toast(`Send failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

/* ---------- Companion Canvas ---------- */
function initCompanionButtons() {
  const host = document.getElementById("companion-state-buttons");
  host.innerHTML = Object.keys(COMPANION_STYLE)
    .map((st) => `<button class="btn-secondary small" data-cstate="${st}">${st}</button>`)
    .join("");
  host.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-cstate]");
    if (!btn) return;
    const next = btn.dataset.cstate;
    await api("/api/v1/voice/state", { method: "POST", body: { state: next } });
    state.companionState = next;
    document.getElementById("chip-companion-state").textContent = `State: ${next}`;
    document.getElementById("companion-caption").textContent = `RYNA • ${next.toUpperCase()}`;
  });
}

function syncCompanionControls() {
  const size = state.settings["appearance.companion_size"] ?? 170;
  const alpha = state.settings["appearance.companion_transparency"] ?? 0.94;
  document.getElementById("ctrl-comp-size").value = size;
  document.getElementById("val-comp-size").textContent = size;
  document.getElementById("ctrl-comp-alpha").value = alpha;
  document.getElementById("val-comp-alpha").textContent = Number(alpha).toFixed(2);
  document.getElementById("ctrl-comp-topmost").checked = !!state.settings["appearance.companion_always_on_top"];
  document.getElementById("ctrl-reduced-anim").checked = !!state.settings["appearance.reduced_animations"];
  document.getElementById("companion-caption").textContent = `RYNA • ${state.companionState.toUpperCase()}`;
}

function animateCompanion() {
  const canvas = document.getElementById("companion-canvas");
  if (canvas) {
    const ctx = canvas.getContext("2d");
    const reduced = !!state.settings["appearance.reduced_animations"];
    const style = COMPANION_STYLE[state.companionState] || COMPANION_STYLE.idle;
    state.animPhase += reduced ? style.speed * 0.25 : style.speed;

    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const pulse = reduced ? 0 : Math.sin(state.animPhase) * 7;

    ctx.clearRect(0, 0, w, h);

    // Subtle radial glow
    const grad = ctx.createRadialGradient(cx, cy, 12, cx, cy, 120);
    grad.addColorStop(0, style.accent + "55");
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, 120, 0, Math.PI * 2);
    ctx.fill();

    // Dashed outer ring
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(state.animPhase * 0.35);
    ctx.strokeStyle = style.ring;
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 8]);
    ctx.beginPath();
    ctx.arc(0, 0, 92 + pulse * 0.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    // Rotating arcs
    ctx.strokeStyle = style.accent;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.arc(cx, cy, 74, state.animPhase, state.animPhase + Math.PI * 0.65);
    ctx.stroke();

    ctx.strokeStyle = style.ring;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(cx, cy, 74, state.animPhase + Math.PI, state.animPhase + Math.PI * 1.45);
    ctx.stroke();

    // Core sphere
    ctx.fillStyle = "#0e1d36";
    ctx.strokeStyle = style.accent;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(cx, cy, 52 + pulse * 0.35, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = style.accent;
    ctx.beginPath();
    ctx.arc(cx, cy, 22 + pulse * 0.2, 0, Math.PI * 2);
    ctx.fill();
  }
  requestAnimationFrame(animateCompanion);
}

/* ---------- Tasks ---------- */
async function loadTasks() {
  const filter = document.getElementById("task-filter").value;
  const qs = filter ? `?status=${encodeURIComponent(filter)}` : "";
  const data = await api(`/api/v1/tasks${qs}`);
  state.tasks = data.tasks || [];
  renderTasks();
}

function renderTasks() {
  const list = document.getElementById("task-list");
  if (!state.tasks.length) {
    list.innerHTML = `<div class="muted">No tasks found. Create a task on the left.</div>`;
    return;
  }
  list.innerHTML = state.tasks
    .map((t) => {
      const badgeClass =
        t.status === "completed" ? "ok" :
        t.status === "failed" ? "danger" :
        t.status === "waiting_for_approval" || t.status === "paused" ? "warn" : "";
      return `
        <div class="list-card">
          <div class="list-card-head">
            <strong>${escapeHtml(t.title)}</strong>
            <span class="badge ${badgeClass}">${escapeHtml(t.status)} • ${escapeHtml(t.outcome_status)}</span>
          </div>
          <div class="muted">Goal: ${escapeHtml(t.goal)} | Level ${t.permission_level}${t.requires_internet ? " | Online" : " | Offline-ready"}</div>
          ${t.result_summary ? `<pre>${escapeHtml(t.result_summary)}</pre>` : ""}
          ${t.error_message ? `<div class="inline-error">${escapeHtml(t.error_message)}</div>` : ""}
          <div class="btn-row">
            ${t.status === "pending" || t.status === "paused" ? `<button class="btn-primary small" data-task-act="run" data-id="${t.id}">Run / Resume</button>` : ""}
            ${t.status === "waiting_for_approval" ? `<button class="btn-primary small" data-task-act="approve" data-id="${t.id}">Approve & Execute</button>` : ""}
            ${t.status === "running" ? `<button class="btn-warn small" data-task-act="pause" data-id="${t.id}">Pause</button>` : ""}
            ${!["completed", "cancelled"].includes(t.status) ? `<button class="btn-danger small" data-task-act="cancel" data-id="${t.id}">Cancel</button>` : ""}
          </div>
        </div>`;
    })
    .join("");
}

async function createTask(autoRun) {
  const title = document.getElementById("task-title").value.trim();
  const goal = document.getElementById("task-goal").value.trim();
  if (!title || !goal) return;
  await api("/api/v1/tasks", {
    method: "POST",
    body: {
      title,
      goal,
      permission_level: Number(document.getElementById("task-perm").value),
      requires_internet: document.getElementById("task-internet").checked,
      auto_run: autoRun,
    },
  });
  document.getElementById("task-form").reset();
  toast(autoRun ? "Task created and executed." : "Task created.");
  await loadTasks();
  await refreshAll();
}

/* ---------- Memory Center ---------- */
function syncMemoryControls() {
  document.getElementById("mem-ctrl-enabled").checked = !!state.settings["memory.enabled"];
  document.getElementById("mem-ctrl-paused").checked = !!state.settings["memory.learning_paused"];
}

async function loadMemories() {
  const cat = document.getElementById("mem-filter-cat").value;
  const q = document.getElementById("mem-search").value.trim();
  const params = new URLSearchParams();
  if (cat) params.set("category", cat);
  if (q) params.set("q", q);
  const data = await api(`/api/v1/memories?${params.toString()}`);
  state.memories = data.memories || [];
  renderMemories();
}

function renderMemories() {
  const list = document.getElementById("memory-list");
  if (!state.memories.length) {
    list.innerHTML = `<div class="muted">No stored memories matching current filter.</div>`;
    return;
  }
  list.innerHTML = state.memories
    .map(
      (m) => `
      <div class="list-card">
        <div class="list-card-head">
          <span class="badge">${escapeHtml(m.category)} • ${escapeHtml(m.memory_type)}</span>
          <span class="muted">Confidence: ${Math.round((m.confidence || 0) * 100)}%</span>
        </div>
        <div>${escapeHtml(m.content)}</div>
        <div class="muted">Source: ${escapeHtml(m.source)}${m.tags?.length ? ` • Tags: ${escapeHtml(m.tags.join(", "))}` : ""}</div>
        <div class="btn-row">
          <button class="btn-secondary small" data-mem-edit="${m.id}">Edit</button>
          <button class="btn-danger small" data-mem-del="${m.id}">Delete</button>
        </div>
      </div>`
    )
    .join("");
}

/* ---------- Settings (All 16 Categories) ---------- */
function renderSettingsNav() {
  const nav = document.getElementById("settings-nav");
  nav.innerHTML = SETTINGS_CATEGORIES.map(
    (c) => `<button class="nav-btn ${c.id === state.activeSettingsCat ? "active" : ""}" data-scat="${c.id}">${c.label}</button>`
  ).join("");
}

async function saveSettingsPatch(patch) {
  const resp = await api("/api/v1/settings", { method: "PUT", body: { settings: patch } });
  state.settings = resp.settings;
  toast("Settings saved to SQLite.");
  await refreshAll();
}

function renderSettingsCategory() {
  renderSettingsNav();
  const body = document.getElementById("settings-body");
  const s = state.settings;
  const st = state.status || {};
  const cat = state.activeSettingsCat;

  if (cat === "general") {
    body.innerHTML = `
      <h3>General Settings</h3>
      <div class="form-group"><label>Assistant Identity</label><input id="set-name" type="text" value="${escapeHtml(s["general.assistant_name"] || "RYNA")}" /></div>
      <div class="form-group"><label>Language</label><input id="set-lang" type="text" value="${escapeHtml(s["general.language"] || "en")}" /></div>
      <button class="btn-primary" id="save-general">Save General Settings</button>`;
    document.getElementById("save-general").onclick = () =>
      saveSettingsPatch({
        "general.assistant_name": document.getElementById("set-name").value.trim() || "RYNA",
        "general.language": document.getElementById("set-lang").value.trim() || "en",
      });
  } else if (cat === "appearance") {
    body.innerHTML = `
      <h3>Appearance</h3>
      <div class="form-group"><label>Theme</label>
        <select id="set-theme"><option value="midnight_cyan" selected>Midnight Cyan (Default)</option></select>
      </div>
      <label class="toggle-row"><input type="checkbox" id="set-red-anim" ${s["appearance.reduced_animations"] ? "checked" : ""}/><span>Reduced Animations</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-topmost" ${s["appearance.companion_always_on_top"] ? "checked" : ""}/><span>Companion Always on Top</span></label>
      <button class="btn-primary" id="save-app">Save Appearance</button>`;
    document.getElementById("save-app").onclick = () =>
      saveSettingsPatch({
        "appearance.theme": document.getElementById("set-theme").value,
        "appearance.reduced_animations": document.getElementById("set-red-anim").checked,
        "appearance.companion_always_on_top": document.getElementById("set-topmost").checked,
      });
  } else if (cat === "voice") {
    const backends = st.voice?.backends || [];
    body.innerHTML = `
      <h3>Voice Pipeline</h3>
      <label class="toggle-row"><input type="checkbox" id="set-v-en" ${s["voice.enabled"] ? "checked" : ""}/><span>Enable Voice Pipeline</span></label>
      <div class="form-row">
        <div class="form-group"><label>Mode</label>
          <select id="set-v-mode">
            <option value="push_to_talk" ${s["voice.mode"] === "push_to_talk" ? "selected" : ""}>Push-to-Talk</option>
            <option value="wake_word" ${s["voice.mode"] === "wake_word" ? "selected" : ""}>Wake Word ("Hey Ryna")</option>
            <option value="continuous" ${s["voice.mode"] === "continuous" ? "selected" : ""}>Continuous Conversation</option>
          </select>
        </div>
        <div class="form-group"><label>Wake Word</label><input id="set-v-wake" type="text" value="${escapeHtml(s["voice.wake_word"] || "Hey Ryna")}" /></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Sensitivity (0.1 - 1.0)</label><input id="set-v-sens" type="number" step="0.05" min="0.1" max="1" value="${s["voice.sensitivity"] ?? 0.65}" /></div>
        <div class="form-group"><label>Speech Speed (0.5 - 2.0)</label><input id="set-v-speed" type="number" step="0.1" min="0.5" max="2" value="${s["voice.speech_speed"] ?? 1.0}" /></div>
      </div>
      <button class="btn-primary" id="save-voice">Save Voice Settings</button>
      <hr class="divider"/>
      <h4>Detected Local Voice Engines (Live Probe)</h4>
      ${backends.map((b) => `<div class="list-card"><div class="list-card-head"><strong>${escapeHtml(b.name)} (${escapeHtml(b.role)})</strong><span class="badge ${b.available ? "ok" : "planned"}">${b.available ? "Installed" : "Not Installed"}</span></div><div class="muted">${escapeHtml(b.detail)}</div></div>`).join("")}`;
    document.getElementById("save-voice").onclick = () =>
      saveSettingsPatch({
        "voice.enabled": document.getElementById("set-v-en").checked,
        "voice.mode": document.getElementById("set-v-mode").value,
        "voice.wake_word": document.getElementById("set-v-wake").value.trim() || "Hey Ryna",
        "voice.sensitivity": Number(document.getElementById("set-v-sens").value),
        "voice.speech_speed": Number(document.getElementById("set-v-speed").value),
      });
  } else if (cat === "ai_models") {
    const provs = Object.values(st.ai?.providers || {});
    body.innerHTML = `
      <h3>AI Models & Routing</h3>
      <div class="form-row">
        <div class="form-group"><label>Preferred Provider</label>
          <select id="set-ai-pref">
            ${["auto", "ollama", "openai", "anthropic", "gemini"].map((p) => `<option value="${p}" ${s["ai.preferred_provider"] === p ? "selected" : ""}>${p}</option>`).join("")}
          </select>
        </div>
        <div class="form-group"><label>Fallback Provider</label>
          <select id="set-ai-fb">
            ${["ollama", "openai", "anthropic", "gemini"].map((p) => `<option value="${p}" ${s["ai.fallback_provider"] === p ? "selected" : ""}>${p}</option>`).join("")}
          </select>
        </div>
      </div>
      <button class="btn-primary" id="save-ai-route">Save Routing</button>
      <hr class="divider"/>
      <h4>Live Provider Status</h4>
      ${provs.map((p) => `<div class="list-card"><div class="list-card-head"><strong>${escapeHtml(p.name)} (${p.is_local ? "Local" : "Cloud"})</strong><span class="badge ${p.available ? "ok" : "warn"}">${p.available ? "Ready" : "Unavailable"}</span></div><div class="muted">${escapeHtml(p.detail)}</div></div>`).join("")}`;
    document.getElementById("save-ai-route").onclick = () =>
      saveSettingsPatch({
        "ai.preferred_provider": document.getElementById("set-ai-pref").value,
        "ai.fallback_provider": document.getElementById("set-ai-fb").value,
      });
  } else if (cat === "offline_ai") {
    const hw = st.hardware || {};
    const installed = st.ai?.providers?.ollama?.models || [];
    body.innerHTML = `
      <h3>Offline AI (Local Models)</h3>
      <p class="muted">Detected hardware tier: <strong>${escapeHtml(hw.recommended_tier || "unknown")}</strong> (${hw.ram_total_gb || "?"} GB RAM). Recommended models: <code>${escapeHtml((hw.recommended_local_models || []).join(", "))}</code>.</p>
      <div class="form-group"><label>Ollama Base URL</label><input id="set-ollama-url" type="text" value="${escapeHtml(s["ai.ollama_base_url"] || "http://127.0.0.1:11434")}" /></div>
      <div class="form-group"><label>Local Model Name (leave blank for first installed model)</label><input id="set-local-model" type="text" value="${escapeHtml(s["ai.local_model"] || "")}" placeholder="${installed[0] || "e.g., qwen2.5:3b"}" /></div>
      <div class="muted">Installed Ollama models detected right now: ${installed.length ? escapeHtml(installed.join(", ")) : "None detected (ensure Ollama is running and pull a model)."}</div>
      <button class="btn-primary" id="save-offline">Save Offline AI Settings</button>`;
    document.getElementById("save-offline").onclick = () =>
      saveSettingsPatch({
        "ai.ollama_base_url": document.getElementById("set-ollama-url").value.trim(),
        "ai.local_model": document.getElementById("set-local-model").value.trim(),
      });
  } else if (cat === "online_ai") {
    body.innerHTML = `
      <h3>Online / Cloud AI</h3>
      <p class="muted">API keys are read only from <code>.env</code> environment variables and are never written to ordinary memory.</p>
      <label class="toggle-row"><input type="checkbox" id="set-allow-cloud" ${s["ai.allow_cloud"] ? "checked" : ""}/><span>Allow Cloud AI Providers when Online</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-sens-cloud" ${s["ai.send_sensitive_to_cloud"] ? "checked" : ""}/><span>Allow sending prompts with sensitive terms to cloud (Not Recommended)</span></label>
      <div class="form-group"><label>OpenAI-Compatible Base URL</label><input id="set-oai-url" type="text" value="${escapeHtml(s["ai.openai_base_url"] || "https://api.openai.com/v1")}" /></div>
      <div class="form-row">
        <div class="form-group"><label>OpenAI Model</label><input id="set-oai-mod" type="text" value="${escapeHtml(s["ai.openai_model"] || "gpt-4o-mini")}" /></div>
        <div class="form-group"><label>Anthropic Model</label><input id="set-ant-mod" type="text" value="${escapeHtml(s["ai.anthropic_model"] || "claude-3-5-sonnet-latest")}" /></div>
      </div>
      <div class="form-group"><label>Gemini Model</label><input id="set-gem-mod" type="text" value="${escapeHtml(s["ai.gemini_model"] || "gemini-2.0-flash")}" /></div>
      <button class="btn-primary" id="save-online">Save Online AI Settings</button>`;
    document.getElementById("save-online").onclick = () =>
      saveSettingsPatch({
        "ai.allow_cloud": document.getElementById("set-allow-cloud").checked,
        "ai.send_sensitive_to_cloud": document.getElementById("set-sens-cloud").checked,
        "ai.openai_base_url": document.getElementById("set-oai-url").value.trim(),
        "ai.openai_model": document.getElementById("set-oai-mod").value.trim(),
        "ai.anthropic_model": document.getElementById("set-ant-mod").value.trim(),
        "ai.gemini_model": document.getElementById("set-gem-mod").value.trim(),
      });
  } else if (cat === "memory") {
    body.innerHTML = `
      <h3>Memory Settings</h3>
      <label class="toggle-row"><input type="checkbox" id="set-m-en" ${s["memory.enabled"] ? "checked" : ""}/><span>Enable Persistent Memory</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-m-pause" ${s["memory.learning_paused"] ? "checked" : ""}/><span>Pause Automatic Learning</span></label>
      <div class="form-group"><label>Retention Period (days)</label><input id="set-m-ret" type="number" min="1" max="3650" value="${s["memory.retention_days"] ?? 365}" /></div>
      <label class="toggle-row"><input type="checkbox" disabled /><span>Encrypted Cloud Memory Sync <span class="badge planned">Planned</span></span></label>
      <button class="btn-primary" id="save-mem-set">Save Memory Settings</button>`;
    document.getElementById("save-mem-set").onclick = () =>
      saveSettingsPatch({
        "memory.enabled": document.getElementById("set-m-en").checked,
        "memory.learning_paused": document.getElementById("set-m-pause").checked,
        "memory.retention_days": Number(document.getElementById("set-m-ret").value),
      });
  } else if (cat === "privacy") {
    body.innerHTML = `
      <h3>Privacy</h3>
      <label class="toggle-row"><input type="checkbox" id="set-p-redact" ${s["privacy.redact_secrets"] ? "checked" : ""}/><span>Reject passwords, tokens, and API keys from ordinary memory</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-p-tel" ${s["privacy.telemetry"] ? "checked" : ""}/><span>Diagnostic Telemetry (off by default; no telemetry server exists in 0.1.0)</span></label>
      <button class="btn-primary" id="save-priv">Save Privacy Settings</button>`;
    document.getElementById("save-priv").onclick = () =>
      saveSettingsPatch({
        "privacy.redact_secrets": document.getElementById("set-p-redact").checked,
        "privacy.telemetry": document.getElementById("set-p-tel").checked,
      });
  } else if (cat === "permissions") {
    body.innerHTML = `
      <h3>Permission & Security Levels</h3>
      <label class="toggle-row"><input type="checkbox" id="set-perm-1" ${s["permissions.level1_auto"] ? "checked" : ""}/><span>Level 1 (Automatic): Allow read-only local tools without prompt</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-perm-2" ${s["permissions.level2_require_confirm"] ? "checked" : ""}/><span>Level 2 (Confirmation): Require explicit user approval</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-perm-3" ${s["permissions.level3_block_destructive"] ? "checked" : ""}/><span>Level 3 (Restricted): Block destructive & security-critical actions</span></label>
      <button class="btn-primary" id="save-perms">Save Permission Policy</button>`;
    document.getElementById("save-perms").onclick = () =>
      saveSettingsPatch({
        "permissions.level1_auto": document.getElementById("set-perm-1").checked,
        "permissions.level2_require_confirm": document.getElementById("set-perm-2").checked,
        "permissions.level3_block_destructive": document.getElementById("set-perm-3").checked,
      });
  } else if (cat === "computer_control") {
    body.innerHTML = `
      <h3>Computer Control</h3>
      <div class="form-group"><label>Allowed Local Workspace Root (sandboxed file search & read)</label><input id="set-cc-root" type="text" value="${escapeHtml(s["computer_control.allowed_root"] || ".")}" /></div>
      <label class="toggle-row"><input type="checkbox" disabled /><span>Desktop GUI Automation / Mouse & Keyboard Injection <span class="badge planned">Planned (Phase 5)</span></span></label>
      <label class="toggle-row"><input type="checkbox" disabled /><span>Screen Capture & OCR Pipeline <span class="badge planned">Planned (Phase 5)</span></span></label>
      <button class="btn-primary" id="save-cc">Save Computer Control Scope</button>`;
    document.getElementById("save-cc").onclick = () =>
      saveSettingsPatch({ "computer_control.allowed_root": document.getElementById("set-cc-root").value.trim() || "." });
  } else if (cat === "notifications") {
    body.innerHTML = `
      <h3>Notifications & Proactivity</h3>
      <label class="toggle-row"><input type="checkbox" id="set-n-en" ${s["notifications.enabled"] ? "checked" : ""}/><span>Enable Task & Reminder Notifications</span></label>
      <label class="toggle-row"><input type="checkbox" id="set-n-pro" ${s["notifications.proactive_suggestions"] ? "checked" : ""}/><span>Allow Optional Proactive Suggestions</span></label>
      <button class="btn-primary" id="save-notif">Save Notification Settings</button>`;
    document.getElementById("save-notif").onclick = () =>
      saveSettingsPatch({
        "notifications.enabled": document.getElementById("set-n-en").checked,
        "notifications.proactive_suggestions": document.getElementById("set-n-pro").checked,
      });
  } else if (cat === "devices") {
    body.innerHTML = `
      <h3>Devices</h3>
      <p class="muted">Current device: <strong>${escapeHtml(st.hardware?.os_name || "Windows")} (${escapeHtml(st.hardware?.architecture || "")})</strong>.</p>
      <div class="list-card"><div class="list-card-head"><strong>Multi-Device Pairing (Android / iOS / macOS / Linux)</strong><span class="badge planned">Planned (Phase 7)</span></div><div class="muted">Cross-device discovery and encrypted sync are not yet implemented in 0.1.0.</div></div>`;
  } else if (cat === "storage") {
    const db = st.database || {};
    body.innerHTML = `
      <h3>Local Storage (SQLite)</h3>
      <pre>${escapeHtml(JSON.stringify(db, null, 2))}</pre>`;
  } else if (cat === "startup") {
    body.innerHTML = `
      <h3>Windows Startup & Lifecycle</h3>
      <label class="toggle-row"><input type="checkbox" id="set-st-comp" ${s["startup.show_companion_on_start"] ? "checked" : ""}/><span>Show Desktop Companion on Launch</span></label>
      <p class="muted">To register or remove Windows login startup safely (HKCU Run registry key), run <code>powershell -File scripts/startup_windows.ps1 -Action enable|disable|status</code>.</p>
      <button class="btn-primary" id="save-start">Save Startup Preference</button>`;
    document.getElementById("save-start").onclick = () =>
      saveSettingsPatch({ "startup.show_companion_on_start": document.getElementById("set-st-comp").checked });
  } else if (cat === "diagnostics") {
    body.innerHTML = `<h3>Live System Diagnostics & Audit Log</h3><pre id="diag-json">Loading...</pre><div id="audit-list" class="item-list"></div>`;
    document.getElementById("diag-json").textContent = JSON.stringify(st.hardware || {}, null, 2);
    api("/api/v1/audit").then((d) => {
      const box = document.getElementById("audit-list");
      const entries = d.entries || [];
      box.innerHTML = entries.length
        ? entries.slice(0, 20).map((e) => `<div class="list-card"><div class="list-card-head"><strong>${escapeHtml(e.action_type)}</strong><span class="badge">${escapeHtml(e.status)} (L${e.permission_level})</span></div><div class="muted">${escapeHtml(e.target)} • ${escapeHtml(e.created_at)}</div></div>`).join("")
        : `<div class="muted">No audit entries recorded yet.</div>`;
    });
  } else if (cat === "updates") {
    body.innerHTML = `
      <h3>Updates</h3>
      <p>Installed Version: <strong>RYNA ${escapeHtml(st.version || "0.1.0")}</strong></p>
      <button class="btn-secondary" disabled>Check for Updates (Unavailable — Planned Phase 8)</button>`;
  }
}

/* ---------- Event Wiring ---------- */
function bindEvents() {
  document.querySelectorAll(".nav-btn[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  document.getElementById("btn-refresh").onclick = () => {
    refreshAll();
    loadConversations();
    toast("Refreshed live engine status.");
  };

  document.getElementById("btn-emergency-stop").onclick = async () => {
    await api("/api/v1/emergency-stop", { method: "POST", body: { reason: "User clicked Emergency Stop" } });
    toast("EMERGENCY STOP triggered.");
    await refreshAll();
    await loadTasks();
  };

  document.getElementById("btn-reset-emergency").onclick = async () => {
    await api("/api/v1/emergency-stop/reset", { method: "POST", body: {} });
    toast("Emergency Stop reset.");
    await refreshAll();
  };

  document.getElementById("btn-new-conv").onclick = async () => {
    const res = await api("/api/v1/conversations", { method: "POST", body: { title: "New Conversation" } });
    state.activeConvId = res.conversation.id;
    await loadConversations();
  };

  document.getElementById("conv-list").addEventListener("click", async (e) => {
    const delBtn = e.target.closest("button[data-del]");
    if (delBtn) {
      e.stopPropagation();
      const id = delBtn.dataset.del;
      await api(`/api/v1/conversations/${id}`, { method: "DELETE" });
      if (state.activeConvId === id) state.activeConvId = null;
      await loadConversations();
      return;
    }
    const item = e.target.closest(".conv-item");
    if (item) {
      state.activeConvId = item.dataset.id;
      renderConversationList();
      await loadMessages(state.activeConvId);
    }
  });

  document.getElementById("chat-form").addEventListener("submit", handleSendMessage);
  document.getElementById("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("chat-form").requestSubmit();
    }
  });

  document.querySelectorAll(".chip-btn[data-insert]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const inp = document.getElementById("chat-input");
      inp.value = btn.dataset.insert;
      inp.focus();
    });
  });

  const doInterrupt = async () => {
    const res = await api("/api/v1/voice/interrupt", { method: "POST", body: {} });
    toast(`Interrupted (${res.previous_state} → ${res.new_state}).`);
    await refreshAll();
  };
  document.getElementById("btn-interrupt").onclick = doInterrupt;
  document.getElementById("btn-companion-interrupt").onclick = doInterrupt;

  document.getElementById("ctrl-comp-size").oninput = (e) => {
    document.getElementById("val-comp-size").textContent = e.target.value;
  };
  document.getElementById("ctrl-comp-alpha").oninput = (e) => {
    document.getElementById("val-comp-alpha").textContent = Number(e.target.value).toFixed(2);
  };
  document.getElementById("btn-save-companion").onclick = () =>
    saveSettingsPatch({
      "appearance.companion_size": Number(document.getElementById("ctrl-comp-size").value),
      "appearance.companion_transparency": Number(document.getElementById("ctrl-comp-alpha").value),
      "appearance.companion_always_on_top": document.getElementById("ctrl-comp-topmost").checked,
      "appearance.reduced_animations": document.getElementById("ctrl-reduced-anim").checked,
    });

  document.getElementById("task-form").addEventListener("submit", (e) => {
    e.preventDefault();
    createTask(false);
  });
  document.getElementById("btn-create-run-task").onclick = () => createTask(true);
  document.getElementById("task-filter").onchange = loadTasks;

  document.getElementById("task-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-task-act]");
    if (!btn) return;
    const id = btn.dataset.id;
    const act = btn.dataset.taskAct;
    if (act === "run") await api(`/api/v1/tasks/${id}/run`, { method: "POST", body: { approved: false } });
    if (act === "approve") await api(`/api/v1/tasks/${id}/run`, { method: "POST", body: { approved: true } });
    if (act === "pause") await api(`/api/v1/tasks/${id}/pause`, { method: "POST", body: {} });
    if (act === "cancel") await api(`/api/v1/tasks/${id}/cancel`, { method: "POST", body: {} });
    await loadTasks();
    await refreshAll();
  });

  document.getElementById("memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("mem-form-error");
    errEl.classList.add("hidden");
    try {
      const tags = document
        .getElementById("mem-tags")
        .value.split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await api("/api/v1/memories", {
        method: "POST",
        body: {
          category: document.getElementById("mem-category").value,
          memory_type: document.getElementById("mem-type").value,
          content: document.getElementById("mem-content").value,
          tags,
        },
      });
      document.getElementById("memory-form").reset();
      toast("Memory stored in SQLite.");
      await loadMemories();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  });

  document.getElementById("mem-filter-cat").onchange = loadMemories;
  document.getElementById("mem-search").oninput = loadMemories;
  document.getElementById("mem-ctrl-enabled").onchange = (e) =>
    saveSettingsPatch({ "memory.enabled": e.target.checked });
  document.getElementById("mem-ctrl-paused").onchange = (e) =>
    saveSettingsPatch({ "memory.learning_paused": e.target.checked });

  document.getElementById("btn-export-mem").onclick = async () => {
    const data = await api("/api/v1/memories/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ryna-memories-export.json";
    a.click();
    toast(`Exported ${data.count} memories.`);
  };

  document.getElementById("btn-clear-mem").onclick = async () => {
    if (!confirm("Permanently clear all stored RYNA memories?")) return;
    const res = await api("/api/v1/memories", { method: "DELETE" });
    toast(`Cleared ${res.deleted_count} memories.`);
    await loadMemories();
  };

  document.getElementById("memory-list").addEventListener("click", async (e) => {
    const delBtn = e.target.closest("button[data-mem-del]");
    if (delBtn) {
      await api(`/api/v1/memories/${delBtn.dataset.memDel}`, { method: "DELETE" });
      toast("Memory deleted.");
      await loadMemories();
      return;
    }
    const editBtn = e.target.closest("button[data-mem-edit]");
    if (editBtn) {
      const mem = state.memories.find((m) => m.id === editBtn.dataset.memEdit);
      if (!mem) return;
      const next = prompt("Edit memory content:", mem.content);
      if (next !== null && next.trim()) {
        try {
          await api(`/api/v1/memories/${mem.id}`, { method: "PATCH", body: { content: next.trim() } });
          toast("Memory updated.");
          await loadMemories();
        } catch (err) {
          toast(`Update rejected: ${err.message}`);
        }
      }
    }
  });

  document.getElementById("settings-nav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-scat]");
    if (!btn) return;
    state.activeSettingsCat = btn.dataset.scat;
    renderSettingsCategory();
  });
}

/* ---------- Boot ---------- */
async function boot() {
  initCompanionButtons();
  bindEvents();
  await refreshAll();
  await loadConversations();
  animateCompanion();
  setInterval(refreshAll, 15000);
}

boot();
