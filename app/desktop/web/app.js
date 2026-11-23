/**
 * RYNA Main Desktop Conversation & Control UI (Clean Rebuild).
 * Connected exclusively to real local APIs, SSE events, local/cloud AI, voice, memory, tasks, and computer controls.
 */

import { VoiceClient } from "./voice.js";

const state = {
  view: "home",
  historyOpen: true,
  settingsTab: "ai",
  status: null,
  settings: {},
  conversations: [],
  activeConvId: null,
  messages: [],
  attachments: [],
  streaming: null,
  isGenerating: false,
  robotState: "idle",
  robotSub: "Ready.",
  memories: [],
  tasks: [],
  activity: [],
};

let abortCtrl = null;

async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json", ...(opts.headers || {}) }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

const voice = new VoiceClient({
  api,
  getSettings: () => state.settings,
  onState: (st, sub) => {
    state.robotState = st;
    state.robotSub = sub;
    renderHeaderAndRobot();
  },
  onLevel: (lvl) => {
    const wrap = document.getElementById("audio-meter-wrap");
    const bar = document.getElementById("audio-meter-bar");
    wrap.classList.toggle("hidden", !voice.isListening);
    bar.style.width = Math.round(lvl * 100) + "%";
  },
  onTranscript: (txt) => {
    const el = document.getElementById("live-tx");
    el.classList.toggle("hidden", !txt);
    el.textContent = txt ? `“${txt}”` : "";
  },
  onFinal: (txt) => sendMessage(txt, { fromVoice: true }),
  onError: (msg) => showNotice(msg),
});

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function fmtTime(iso) {
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch { return ""; }
}
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(window.__rt);
  window.__rt = setTimeout(() => el.classList.add("hidden"), 2600);
}
function showNotice(msg) {
  const b = document.getElementById("notice-banner");
  document.getElementById("notice-text").textContent = msg;
  b.classList.toggle("hidden", !msg);
}
function renderMd(txt) {
  return String(txt ?? "").split(/(```[\s\S]*?```)/g).map((seg) => {
    if (seg.startsWith("```") && seg.endsWith("```")) {
      return `<pre><code>${esc(seg.slice(3, -3).replace(/^[a-zA-Z0-9_-]+\n/, ""))}</code></pre>`;
    }
    return esc(seg).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }).join("");
}

/* ---------- SSE Event Bus ---------- */
function connectSSE() {
  const es = new EventSource("/api/v1/events/stream");
  es.addEventListener("robot_state", (e) => {
    const d = JSON.parse(e.data);
    if (d.state && !voice.isListening) {
      state.robotState = d.state;
      if (d.state === "idle" && voice.isSpeaking && state.settings["voice.tts_engine"] === "windows-sapi") {
        voice.isSpeaking = false;
      }
      renderHeaderAndRobot();
    }
  });
  es.addEventListener("activity", (e) => {
    state.activity = [JSON.parse(e.data), ...state.activity].slice(0, 150);
    if (state.view === "activity") renderActivity();
  });
  es.addEventListener("task_updated", () => { if (state.view === "tasks") loadTasks(); });
  es.addEventListener("emergency_stop", () => refreshStatus());
}

/* ---------- Status & Data Loading ---------- */
async function refreshStatus() {
  try {
    const [st, setResp] = await Promise.all([api("/api/v1/status"), api("/api/v1/settings")]);
    state.status = st;
    state.settings = setResp.settings || {};
    if (!voice.isListening && !voice.isSpeaking && !state.isGenerating) {
      state.robotState = st.robot_state || "idle";
    }
    renderHeaderAndRobot();
    syncMemoryToggles();
    if (state.view === "settings") renderSettings();
  } catch (err) {
    showNotice(`Local RYNA engine unreachable: ${err.message}`);
  }
}

function renderHeaderAndRobot() {
  const st = state.status;
  if (st) {
    document.getElementById("brand-ver").textContent = st.version;
    const op = st.operating_mode || {};
    const mp = document.getElementById("mode-pill");
    mp.className = `pill ${op.id || ""}`;
    document.getElementById("mode-text").textContent = `${op.label || "Checking…"}${op.active_model ? ` · ${op.active_model}` : ""}`;
    mp.querySelector(".dot").className = "dot " + (op.id?.startsWith("online_") ? "ok" : op.id === "offline_local_ai" ? "cyan" : "warn");
    document.getElementById("active-model-label").textContent = op.active_model ? `${op.active_provider}/${op.active_model}` : "";
    document.getElementById("emergency-banner").classList.toggle("hidden", !st.emergency_stopped);

    const hasAsset = !!st.robot_asset?.available;
    document.getElementById("robot-img").classList.toggle("hidden", !hasAsset);
    document.getElementById("robot-missing").classList.toggle("hidden", hasAsset);
  }

  const vp = document.getElementById("voice-pill");
  const vt = document.getElementById("voice-text");
  const vd = vp.querySelector(".dot");
  if (voice.isListening) { vd.className = "dot cyan"; vt.textContent = "Mic: Listening"; }
  else if (voice.isSpeaking) { vd.className = "dot ok"; vt.textContent = "Audio: Speaking"; }
  else if (!voice.sttSupported) { vd.className = "dot warn"; vt.textContent = "STT: Unsupported Browser"; }
  else { vd.className = "dot ok"; vt.textContent = voice.isWakeArmed ? "Voice: Wake Armed" : "Voice: Ready"; }

  document.getElementById("robot-frame").dataset.state = state.robotState;
  document.getElementById("robot-state-chip").textContent = state.robotState;
  document.getElementById("robot-sub").textContent = state.robotSub;
  document.getElementById("btn-mic").classList.toggle("active-mic", voice.isListening);
  document.getElementById("btn-stop").classList.toggle("hidden", !state.isGenerating);
  document.getElementById("btn-interrupt").classList.toggle("hidden", !voice.isSpeaking);
  document.getElementById("btn-top-interrupt").classList.toggle("hidden", !(voice.isSpeaking || state.isGenerating));
  document.getElementById("btn-send").disabled = state.isGenerating;
  document.getElementById("btn-wake-toggle").textContent = voice.isWakeArmed ? `Wake: ${state.settings["voice.wake_word"] || "Hey Ryna"}` : "Wake: Off";
  document.getElementById("home-layout").classList.toggle("no-history", !state.historyOpen);
}

async function loadConversations() {
  const d = await api("/api/v1/conversations");
  state.conversations = d.conversations || [];
  if (!state.activeConvId && state.conversations.length) state.activeConvId = state.conversations[0].id;
  renderConversations();
  if (state.activeConvId) await loadMessages(state.activeConvId);
  else { state.messages = []; renderMessages(); }
}

function renderConversations() {
  const list = document.getElementById("conv-list");
  list.innerHTML = state.conversations.length
    ? state.conversations.map((c) => `<div class="conv-item ${c.id === state.activeConvId ? "active" : ""}" data-cid="${c.id}"><span class="conv-title">${esc(c.title)}</span><button class="conv-del" data-del="${c.id}">✕</button></div>`).join("")
    : `<div class="muted">No conversations yet.</div>`;
}

async function loadMessages(cid) {
  const d = await api(`/api/v1/conversations/${cid}/messages`);
  state.messages = d.messages || [];
  renderMessages();
}

function renderMessages() {
  const box = document.getElementById("messages");
  if (!state.messages.length && !state.streaming) {
    box.innerHTML = `<div class="muted" style="margin:auto;text-align:center;">Talk to RYNA naturally, use the microphone, attach a file, or run local tools (<code>/diag</code>, <code>/calc</code>, <code>/find</code>, <code>/window</code>, <code>/note</code>).</div>`;
    return;
  }
  const rows = state.messages.map((m) => {
    const meta = [fmtTime(m.created_at), m.provider, m.model, m.metadata?.latency_ms ? `${m.metadata.latency_ms} ms` : ""].filter(Boolean).join(" · ");
    const acts = m.role !== "user" ? `<button class="act-link" data-copy="${m.id}">Copy</button><button class="act-link" data-speak="${m.id}">Speak</button><button class="act-link" data-retry="${m.id}">Retry</button>` : "";
    return `<div class="msg ${esc(m.role)}"><div class="bubble">${renderMd(m.content)}</div><div class="msg-foot"><span>${esc(meta)}</span>${acts}</div></div>`;
  }).join("");
  const stream = state.streaming ? `<div class="msg assistant"><div class="bubble">${state.streaming.content ? renderMd(state.streaming.content) : "<span class='muted'>Thinking…</span>"}</div><div class="msg-foot"><span>Streaming…</span></div></div>` : "";
  box.innerHTML = rows + stream;
  box.scrollTop = box.scrollHeight;
}

/* ---------- Streaming Chat ---------- */
async function sendMessage(override = null, { fromVoice = false } = {}) {
  const inp = document.getElementById("chat-input");
  const text = (override !== null ? override : inp.value).trim();
  const atts = [...state.attachments];
  if (!text && !atts.length) return;
  if (voice.isSpeaking) await voice.interrupt("User sent new message");
  if (override === null) inp.value = "";
  state.attachments = [];
  renderAttachments();
  state.isGenerating = true;
  state.robotState = "thinking";
  state.robotSub = "Thinking…";
  state.streaming = { content: "" };
  state.messages.push({ id: "tmp_" + Date.now(), role: "user", content: text || `[Attached ${atts.length} file(s)]`, created_at: new Date().toISOString(), metadata: {} });
  renderHeaderAndRobot();
  renderMessages();

  abortCtrl = new AbortController();
  let finalReply = "", aiOk = true;
  try {
    const resp = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: state.activeConvId, message: text, attachments: atts }),
      signal: abortCtrl.signal,
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const blk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let ev = "message", dataLines = [];
        for (const ln of blk.split("\n")) {
          if (ln.startsWith("event:")) ev = ln.slice(6).trim();
          else if (ln.startsWith("data:")) dataLines.push(ln.slice(5).trim());
        }
        if (!dataLines.length) continue;
        const data = JSON.parse(dataLines.join("\n"));
        if (ev === "meta" && data.conversation_id) state.activeConvId = data.conversation_id;
        else if (ev === "delta") { state.streaming.content += data.delta || ""; renderMessages(); }
        else if (ev === "done") { finalReply = data.assistant_message?.content || ""; aiOk = data.ai_available !== false; }
        else if (ev === "error") { aiOk = false; }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") showNotice(`Chat error: ${err.message}`);
  } finally {
    abortCtrl = null;
    state.isGenerating = false;
    state.streaming = null;
    await loadConversations();
    await refreshStatus();
    if (aiOk && finalReply && !finalReply.startsWith("[Tool:") && (fromVoice || (state.settings["voice.enabled"] && state.settings["voice.auto_speak"]))) {
      voice.speak(finalReply);
    }
  }
}

function renderAttachments() {
  const row = document.getElementById("att-row");
  row.classList.toggle("hidden", !state.attachments.length);
  row.innerHTML = state.attachments.map((a, i) => `<span class="att-chip">${esc(a.name)} <button data-rm="${i}" class="conv-del">✕</button></span>`).join("");
}

/* ---------- Memory, Tasks, Activity, Settings ---------- */
function syncMemoryToggles() {
  document.getElementById("mem-enabled").checked = !!state.settings["memory.enabled"];
  document.getElementById("mem-paused").checked = !!state.settings["memory.learning_paused"];
}
async function loadMemories() {
  const p = new URLSearchParams();
  const c = document.getElementById("mem-filter").value;
  const q = document.getElementById("mem-search").value.trim();
  if (c) p.set("category", c);
  if (q) p.set("q", q);
  state.memories = (await api(`/api/v1/memories?${p}`)).memories || [];
  document.getElementById("mem-list").innerHTML = state.memories.length
    ? state.memories.map((m) => `<div class="card"><div class="card-head"><span class="badge">${esc(m.category)} · ${esc(m.memory_type)}</span><span class="mono-dim">${Math.round(m.confidence * 100)}%</span></div><div>${esc(m.content)}</div><div class="btn-row"><button class="btn-secondary small" data-medit="${m.id}">Edit</button><button class="btn-danger small" data-mdel="${m.id}">Delete</button></div></div>`).join("")
    : `<div class="muted">No saved memories.</div>`;
}
async function loadTasks() {
  const f = document.getElementById("task-filter").value;
  state.tasks = (await api(`/api/v1/tasks${f ? `?status=${f}` : ""}`)).tasks || [];
  document.getElementById("task-list").innerHTML = state.tasks.length
    ? state.tasks.map((t) => {
        const cls = t.status === "completed" ? "ok" : t.status === "failed" ? "err" : (t.status === "waiting" || t.status === "paused") ? "warn" : "";
        return `<div class="card"><div class="card-head"><strong>${esc(t.title)}</strong><span class="badge ${cls}">${esc(t.status)} · ${esc(t.outcome_status)}</span></div><div class="muted">${esc(t.goal)} · L${t.permission_level}</div>${t.result_summary ? `<pre style="font-size:11.5px;overflow:auto;">${esc(t.result_summary)}</pre>` : ""}${t.error_message ? `<div class="err-text">${esc(t.error_message)}</div>` : ""}<div class="btn-row">${["planned","paused"].includes(t.status) ? `<button class="btn-primary small" data-ta="run" data-tid="${t.id}">Run</button>` : ""}${t.status === "waiting" ? `<button class="btn-primary small" data-ta="approve" data-tid="${t.id}">Approve</button>` : ""}${["failed","cancelled"].includes(t.status) ? `<button class="btn-secondary small" data-ta="retry" data-tid="${t.id}">Retry</button>` : ""}${!["completed","cancelled"].includes(t.status) ? `<button class="btn-danger small" data-ta="cancel" data-tid="${t.id}">Cancel</button>` : ""}</div></div>`;
      }).join("")
    : `<div class="muted">No tasks in queue.</div>`;
}
async function loadActivity() {
  const f = document.getElementById("act-filter").value;
  state.activity = (await api(`/api/v1/activity${f ? `?category=${f}` : ""}`)).events || [];
  renderActivity();
}
function renderActivity() {
  document.getElementById("act-list").innerHTML = state.activity.length
    ? state.activity.map((e) => `<div class="card"><div class="card-head"><span class="badge ${e.status === "error" ? "err" : e.status === "warn" ? "warn" : "ok"}">${esc(e.category)} · ${esc(e.event_type)}</span><span class="mono-dim">${fmtTime(e.created_at)}</span></div><div>${esc(e.summary)}</div></div>`).join("")
    : `<div class="muted">No activity recorded yet.</div>`;
}

const SETTING_TABS = [
  ["ai", "AI & Models"],
  ["voice", "Voice & Audio"],
  ["desktop", "Desktop Robot & Startup"],
  ["computer", "Computer Control & Vision"],
  ["permissions", "Permissions & Privacy"],
  ["integrations", "Integrations & Backup"],
];

async function saveSettings(patch) {
  const r = await api("/api/v1/settings", { method: "PUT", body: { settings: patch } });
  state.settings = r.settings;
  toast("Settings saved.");
  await refreshStatus();
}

function renderSettings() {
  document.getElementById("settings-nav").innerHTML = SETTING_TABS.map(([id, lbl]) => `<button class="nav-btn ${state.settingsTab === id ? "active" : ""}" data-stab="${id}">${lbl}</button>`).join("");
  const b = document.getElementById("settings-body");
  const s = state.settings;
  const st = state.status || {};

  if (state.settingsTab === "ai") {
    const models = st.ai?.providers?.ollama?.models || [];
    const hw = st.hardware || {};
    b.innerHTML = `<h2>AI Providers & Local Models</h2>
      <p class="muted">Hardware: ${hw.ram_total_gb || "?"} GB RAM · GPU: ${esc(hw.gpu_name || "?")} · Tier: ${esc(hw.recommended_tier || "?")}</p>
      <div class="row-2">
        <label class="field"><span>Preferred Provider</span><select id="s-pref">${["auto","ollama","openai","anthropic","gemini"].map((p)=>`<option ${s["ai.preferred_provider"]===p?"selected":""}>${p}</option>`).join("")}</select></label>
        <label class="field"><span>Active Local Model</span>${models.length ? `<select id="s-lmod"><option value="">Auto (${esc(models[0])})</option>${models.map((m)=>`<option value="${esc(m)}" ${s["ai.local_model"]===m?"selected":""}>${esc(m)}</option>`).join("")}</select>` : `<input id="s-lmod" type="text" value="${esc(s["ai.local_model"]||"")}" />`}</label>
      </div>
      <label class="check-row"><input type="checkbox" id="s-cloud" ${s["ai.allow_cloud"]?"checked":""}/><span>Allow Cloud AI when Online (.env credentials)</span></label>
      <label class="check-row"><input type="checkbox" id="s-sens" ${s["ai.send_sensitive_to_cloud"]?"checked":""}/><span>Allow sensitive terms to cloud (Not Recommended)</span></label>
      <button class="btn-primary" id="save-ai">Save AI Settings</button>`;
    document.getElementById("save-ai").onclick = () => saveSettings({
      "ai.preferred_provider": document.getElementById("s-pref").value,
      "ai.local_model": document.getElementById("s-lmod").value,
      "ai.allow_cloud": document.getElementById("s-cloud").checked,
      "ai.send_sensitive_to_cloud": document.getElementById("s-sens").checked,
    });
  } else if (state.settingsTab === "voice") {
    const voices = [...new Set([...(st.voice?.sapi_voices || []), ...voice.browserVoices.map((v) => v.name)])];
    b.innerHTML = `<h2>Voice, Wake Word, Microphone & Speaker</h2>
      <label class="check-row"><input type="checkbox" id="s-ven" ${s["voice.enabled"]?"checked":""}/><span>Enable Voice</span></label>
      <label class="check-row"><input type="checkbox" id="s-vauto" ${s["voice.auto_speak"]?"checked":""}/><span>Auto-speak RYNA responses</span></label>
      <div class="row-2">
        <label class="field"><span>Mode</span><select id="s-vmode">${[["push_to_talk","Push-to-Talk"],["wake_word","Wake Word"],["continuous","Continuous"]].map(([v,l])=>`<option value="${v}" ${s["voice.mode"]===v?"selected":""}>${l}</option>`).join("")}</select></label>
        <label class="field"><span>Wake Phrase</span><input id="s-vwake" type="text" value="${esc(s["voice.wake_word"]||"Hey Ryna")}" /></label>
      </div>
      <div class="row-2">
        <label class="field"><span>TTS Engine</span><select id="s-vtts"><option value="browser" ${s["voice.tts_engine"]!=="windows-sapi"?"selected":""}>Browser SpeechSynthesis</option><option value="windows-sapi" ${s["voice.tts_engine"]==="windows-sapi"?"selected":""}>Windows Native SAPI (Offline)</option></select></label>
        <label class="field"><span>Voice</span><select id="s-vvoice">${voices.length ? voices.map((v)=>`<option ${s["voice.selected_voice"]===v?"selected":""}>${esc(v)}</option>`).join("") : `<option>Microsoft Zira Desktop</option>`}</select></label>
      </div>
      <div class="btn-row"><button class="btn-primary" id="save-v">Save Voice</button><button class="btn-secondary" id="test-v">Test Voice</button></div>`;
    document.getElementById("save-v").onclick = () => saveSettings({
      "voice.enabled": document.getElementById("s-ven").checked,
      "voice.auto_speak": document.getElementById("s-vauto").checked,
      "voice.mode": document.getElementById("s-vmode").value,
      "voice.wake_word": document.getElementById("s-vwake").value.trim() || "Hey Ryna",
      "voice.tts_engine": document.getElementById("s-vtts").value,
      "voice.selected_voice": document.getElementById("s-vvoice").value,
    });
    document.getElementById("test-v").onclick = () => voice.speak("Hello. RYNA voice synthesis is active.");
  } else if (state.settingsTab === "desktop") {
    b.innerHTML = `<h2>Desktop Robot & Windows Startup</h2>
      <p class="muted">Robot asset status: <strong>${esc(st.robot_asset?.detail || "")}</strong></p>
      <div class="row-2">
        <label class="field"><span>Robot Size (px)</span><input id="s-rsize" type="number" min="120" max="320" value="${s["desktop.robot_size"]||180}" /></label>
        <label class="field"><span>Transparency (0.5-1.0)</span><input id="s-ralpha" type="number" step="0.02" min="0.5" max="1" value="${s["desktop.robot_transparency"]||0.96}" /></label>
      </div>
      <label class="check-row"><input type="checkbox" id="s-rtop" ${s["desktop.robot_always_on_top"]?"checked":""}/><span>Always on Top</span></label>
      <label class="check-row"><input type="checkbox" id="s-rred" ${s["desktop.reduced_animations"]?"checked":""}/><span>Reduced Animations</span></label>
      <p class="muted">Manage Windows auto-start via <code>scripts/startup_windows.ps1 -Action status|enable|disable</code>.</p>
      <button class="btn-primary" id="save-desk">Save Desktop Settings</button>`;
    document.getElementById("save-desk").onclick = () => saveSettings({
      "desktop.robot_size": Number(document.getElementById("s-rsize").value),
      "desktop.robot_transparency": Number(document.getElementById("s-ralpha").value),
      "desktop.robot_always_on_top": document.getElementById("s-rtop").checked,
      "desktop.reduced_animations": document.getElementById("s-rred").checked,
    });
  } else if (state.settingsTab === "computer") {
    b.innerHTML = `<h2>Computer Control & Screen Vision</h2>
      <label class="field"><span>Allowed File Workspace Root</span><input id="s-croot" type="text" value="${esc(s["computer.allowed_root"]||".")}" /></label>
      <label class="check-row"><input type="checkbox" id="s-capp" ${s["computer.allow_app_launch"]?"checked":""}/><span>Allow launching allowlisted Windows apps (Level 2 confirmation still enforced)</span></label>
      <label class="check-row"><input type="checkbox" id="s-cvis" ${s["computer.allow_screen_vision"]?"checked":""}/><span>Permit Screen Vision when optional Pillow/vision model is installed</span></label>
      <p class="muted">${esc(st.computer?.detail || "")}</p>
      <button class="btn-primary" id="save-comp">Save Computer Permissions</button>`;
    document.getElementById("save-comp").onclick = () => saveSettings({
      "computer.allowed_root": document.getElementById("s-croot").value.trim() || ".",
      "computer.allow_app_launch": document.getElementById("s-capp").checked,
      "computer.allow_screen_vision": document.getElementById("s-cvis").checked,
    });
  } else if (state.settingsTab === "permissions") {
    b.innerHTML = `<h2>Permissions & Privacy</h2>
      <label class="check-row"><input type="checkbox" id="s-p1" ${s["permissions.level1_auto"]?"checked":""}/><span>Level 1 Automatic (read-only tools & diagnostics)</span></label>
      <label class="check-row"><input type="checkbox" id="s-p2" ${s["permissions.level2_require_confirm"]?"checked":""}/><span>Level 2 Require Confirmation</span></label>
      <label class="check-row"><input type="checkbox" id="s-p3" ${s["permissions.level3_block_destructive"]?"checked":""}/><span>Level 3 Block Destructive Actions</span></label>
      <label class="check-row"><input type="checkbox" id="s-pred" ${s["privacy.redact_secrets"]?"checked":""}/><span>Reject passwords, tokens & API keys from ordinary memory</span></label>
      <button class="btn-primary" id="save-p">Save Security Settings</button>`;
    document.getElementById("save-p").onclick = () => saveSettings({
      "permissions.level1_auto": document.getElementById("s-p1").checked,
      "permissions.level2_require_confirm": document.getElementById("s-p2").checked,
      "permissions.level3_block_destructive": document.getElementById("s-p3").checked,
      "privacy.redact_secrets": document.getElementById("s-pred").checked,
    });
  } else {
    const ints = st.integrations || [];
    b.innerHTML = `<h2>Active Integrations & SQLite Backup</h2>
      <div class="btn-row" style="margin-bottom:12px;"><button class="btn-primary small" id="btn-backup">Create Hot SQLite Backup</button></div>
      ${ints.map((i)=>`<div class="card"><div class="card-head"><strong>${esc(i.name)} (${esc(i.category)})</strong><span class="badge ${i.connected?"ok":"warn"}">${i.connected?"Active":"Inactive"}</span></div><div class="muted">${esc(i.detail)}</div></div>`).join("")}`;
    document.getElementById("btn-backup").onclick = async () => {
      const r = await api("/api/v1/database/backup", { method: "POST", body: {} });
      toast(`Backup created (${r.size_bytes} bytes).`);
    };
  }
}

/* ---------- Wire Events ---------- */
function switchView(v) {
  state.view = v;
  document.querySelectorAll(".nav-btn[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${v}`));
  document.getElementById("view-title").textContent = { home: "Conversation", memory: "Memory", tasks: "Tasks", activity: "Activity", settings: "Settings" }[v] || "RYNA";
  if (v === "memory") loadMemories();
  if (v === "tasks") loadTasks();
  if (v === "activity") loadActivity();
  if (v === "settings") renderSettings();
}

function bind() {
  document.querySelectorAll(".nav-btn[data-view]").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  document.getElementById("btn-toggle-history").onclick = () => { state.historyOpen = !state.historyOpen; renderHeaderAndRobot(); };
  document.getElementById("btn-dismiss-notice").onclick = () => showNotice("");
  document.getElementById("btn-emergency").onclick = async () => {
    if (abortCtrl) abortCtrl.abort();
    await voice.interrupt("Emergency stop");
    await api("/api/v1/emergency-stop", { method: "POST", body: {} });
    await refreshStatus();
  };
  document.getElementById("btn-reset-emergency").onclick = async () => { await api("/api/v1/emergency-stop/reset", { method: "POST", body: {} }); await refreshStatus(); };
  document.getElementById("btn-new-conv").onclick = async () => { const r = await api("/api/v1/conversations", { method: "POST", body: {} }); state.activeConvId = r.conversation.id; await loadConversations(); };
  document.getElementById("conv-list").addEventListener("click", async (e) => {
    const d = e.target.closest("button[data-del]");
    if (d) { e.stopPropagation(); await api(`/api/v1/conversations/${d.dataset.del}`, { method: "DELETE" }); if (state.activeConvId === d.dataset.del) state.activeConvId = null; await loadConversations(); return; }
    const item = e.target.closest(".conv-item[data-cid]");
    if (item) { state.activeConvId = item.dataset.cid; renderConversations(); await loadMessages(item.dataset.cid); }
  });
  document.getElementById("messages").addEventListener("click", async (e) => {
    const c = e.target.closest("button[data-copy]");
    if (c) { const m = state.messages.find((x) => x.id === c.dataset.copy); if (m) { await navigator.clipboard.writeText(m.content); toast("Copied."); } return; }
    const s = e.target.closest("button[data-speak]");
    if (s) { const m = state.messages.find((x) => x.id === s.dataset.speak); if (m) voice.speak(m.content); return; }
    const r = e.target.closest("button[data-retry]");
    if (r && state.activeConvId) { await api("/api/v1/chat/retry", { method: "POST", body: { conversation_id: state.activeConvId, message_id: r.dataset.retry } }); await loadMessages(state.activeConvId); }
  });

  const inp = document.getElementById("chat-input");
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  document.getElementById("btn-send").onclick = () => sendMessage();
  document.getElementById("btn-stop").onclick = async () => { if (abortCtrl) abortCtrl.abort(); await voice.interrupt("Stopped generation"); };
  document.getElementById("btn-interrupt").onclick = () => voice.interrupt("User clicked Interrupt");
  document.getElementById("btn-top-interrupt").onclick = async () => { if (abortCtrl) abortCtrl.abort(); await voice.interrupt("User clicked Interrupt"); };
  document.getElementById("btn-mic").onclick = () => voice.toggleListen();
  document.getElementById("btn-wake-toggle").onclick = () => { voice.toggleWake(); renderHeaderAndRobot(); };

  const fi = document.getElementById("file-input");
  document.getElementById("btn-attach").onclick = () => fi.click();
  fi.addEventListener("change", async () => {
    for (const f of Array.from(fi.files || []).slice(0, 5)) {
      if (f.size <= 256 * 1024) state.attachments.push({ name: f.name, size: f.size, mime: f.type || "text/plain", content: await f.text() });
    }
    fi.value = "";
    renderAttachments();
  });
  document.getElementById("att-row").addEventListener("click", (e) => {
    const rm = e.target.closest("button[data-rm]");
    if (rm) { state.attachments.splice(Number(rm.dataset.rm), 1); renderAttachments(); }
  });
  document.querySelectorAll(".tool-chip[data-cmd]").forEach((b) => b.addEventListener("click", () => { inp.value = b.dataset.cmd; inp.focus(); }));

  // Memory
  document.getElementById("mem-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("mem-err"); err.classList.add("hidden");
    try {
      await api("/api/v1/memories", { method: "POST", body: { category: document.getElementById("mem-cat").value, memory_type: document.getElementById("mem-type").value, subject_key: document.getElementById("mem-subj").value, content: document.getElementById("mem-content").value, tags: document.getElementById("mem-tags").value.split(",").map((x) => x.trim()).filter(Boolean) } });
      e.target.reset(); toast("Memory saved."); await loadMemories();
    } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
  document.getElementById("mem-filter").onchange = loadMemories;
  document.getElementById("mem-search").oninput = loadMemories;
  document.getElementById("mem-enabled").onchange = (e) => saveSettings({ "memory.enabled": e.target.checked });
  document.getElementById("mem-paused").onchange = (e) => saveSettings({ "memory.learning_paused": e.target.checked });
  document.getElementById("btn-export-mem").onclick = async () => { const d = await api("/api/v1/memories/export"); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" })); a.download = "ryna-memories.json"; a.click(); };
  document.getElementById("btn-clear-mem").onclick = async () => { if (confirm("Clear all memory?")) { await api("/api/v1/memories", { method: "DELETE" }); await loadMemories(); } };
  document.getElementById("mem-list").addEventListener("click", async (e) => {
    const del = e.target.closest("button[data-mdel]"); if (del) { await api(`/api/v1/memories/${del.dataset.mdel}`, { method: "DELETE" }); await loadMemories(); return; }
    const ed = e.target.closest("button[data-medit]"); if (ed) { const m = state.memories.find((x) => x.id === ed.dataset.medit); const n = m && prompt("Edit memory:", m.content); if (n) { await api(`/api/v1/memories/${m.id}`, { method: "PATCH", body: { content: n } }); await loadMemories(); } }
  });

  // Tasks
  const mkTask = async (run) => {
    const title = document.getElementById("task-title").value.trim(), goal = document.getElementById("task-goal").value.trim();
    if (!title || !goal) return;
    await api("/api/v1/tasks", { method: "POST", body: { title, goal, permission_level: Number(document.getElementById("task-perm").value), requires_internet: document.getElementById("task-net").checked, auto_run: run } });
    document.getElementById("task-form").reset(); await loadTasks();
  };
  document.getElementById("task-form").addEventListener("submit", (e) => { e.preventDefault(); mkTask(false); });
  document.getElementById("btn-task-run").onclick = () => mkTask(true);
  document.getElementById("task-filter").onchange = loadTasks;
  document.getElementById("task-list").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-ta]"); if (!b) return;
    const { ta, tid } = b.dataset;
    if (ta === "run") await api(`/api/v1/tasks/${tid}/run`, { method: "POST", body: { approved: false } });
    if (ta === "approve") await api(`/api/v1/tasks/${tid}/run`, { method: "POST", body: { approved: true } });
    if (ta === "retry") await api(`/api/v1/tasks/${tid}/retry`, { method: "POST", body: { approved: false } });
    if (ta === "cancel") await api(`/api/v1/tasks/${tid}/cancel`, { method: "POST", body: {} });
    await loadTasks();
  });

  // Activity & Settings
  document.getElementById("act-filter").onchange = loadActivity;
  document.getElementById("btn-act-refresh").onclick = loadActivity;
  document.getElementById("btn-act-clear").onclick = async () => { await api("/api/v1/activity", { method: "DELETE" }); await loadActivity(); };
  document.getElementById("settings-nav").addEventListener("click", (e) => { const b = e.target.closest("button[data-stab]"); if (b) { state.settingsTab = b.dataset.stab; renderSettings(); } });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (abortCtrl) abortCtrl.abort(); voice.interrupt("Escape pressed"); }
    else if (e.ctrlKey && e.key.toLowerCase() === "m") { e.preventDefault(); voice.toggleListen(); }
  });
}

(async function boot() {
  bind();
  await voice.init();
  await refreshStatus();
  await loadConversations();
  connectSSE();
  setInterval(refreshStatus, 20000);
})();
