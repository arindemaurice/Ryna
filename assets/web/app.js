/**
 * RYNA 0.2.0 Main Application Controller.
 * Connects Home (Companion + Streaming Conversation + Voice + Attachments),
 * Memory, Tasks, Activity, Settings, and Floating Companion Dock to the real local engine.
 */

import { CompanionRenderer } from "./companion.js";
import { RynaStore, apiRequest } from "./store.js";
import { VoiceController } from "./voice.js";

const store = new RynaStore();
const companion = new CompanionRenderer(store, ["companion-canvas", "dock-canvas"]);
let activeAbortController = null;
let eventSource = null;

const voice = new VoiceController(store, companion, (transcript, opts) => {
  sendMessage(transcript, opts);
});

/* ---------- Helpers ---------- */
function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function renderMarkdownLite(text) {
  const raw = String(text ?? "");
  const segments = raw.split(/(```[\s\S]*?```)/g);
  return segments
    .map((seg) => {
      if (seg.startsWith("```") && seg.endsWith("```")) {
        const inner = seg.slice(3, -3).replace(/^[a-zA-Z0-9_-]+\n/, "");
        return `<pre><code>${esc(inner)}</code></pre>`;
      }
      return esc(seg)
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\n/g, "<br>");
    })
    .join("");
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(window.__rynaToast);
  window.__rynaToast = setTimeout(() => el.classList.add("hidden"), 2800);
}

/* ---------- Live SSE Synchronization ---------- */
function connectEventStream() {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource("/api/v1/events/stream");

  eventSource.addEventListener("companion_state", (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.state && !store.state.voice.isListening) {
        const labels = {
          idle: "Ready when you are.",
          listening: "Listening…",
          processing: "Thinking…",
          speaking: "Speaking…",
          executing: "Executing local operation…",
          waiting: "Waiting for your confirmation.",
          offline: "Operating offline.",
          error: "Attention needed — see notice.",
        };
        const patch = {
          companionState: data.state,
          companionSubtext: labels[data.state] || "Active.",
        };
        if (data.state === "idle" && store.state.voice.isSpeaking && store.state.settings["voice.tts_engine"] === "windows-sapi") {
          store.setVoice({ isSpeaking: false });
        }
        store.set(patch);
      }
    } catch {
      // ignore
    }
  });

  eventSource.addEventListener("activity", (ev) => {
    try {
      const item = JSON.parse(ev.data);
      const next = [item, ...store.state.activity].slice(0, 150);
      store.set({ activity: next });
      if (store.state.activeView === "activity") renderActivity();
    } catch {
      // ignore
    }
  });

  eventSource.addEventListener("task_updated", () => {
    if (store.state.activeView === "tasks") loadTasks();
  });

  eventSource.addEventListener("emergency_stop", (ev) => {
    try {
      const d = JSON.parse(ev.data);
      store.set({ emergencyStopped: !!d.emergency_stopped });
    } catch {
      // ignore
    }
  });

  eventSource.onerror = () => {
    store.set({ backendHealthy: false });
  };
  eventSource.addEventListener("connected", () => {
    store.set({ backendHealthy: true });
  });
}

/* ---------- Backend Sync ---------- */
async function refreshStatus() {
  try {
    const [st, setResp] = await Promise.all([apiRequest("/api/v1/status"), apiRequest("/api/v1/settings")]);
    const op = st.operating_mode || {};
    store.setVoice({ sapiVoices: st.voice?.sapi_voices || [] });

    const nextCompanion =
      store.state.voice.isListening
        ? "listening"
        : store.state.voice.isSpeaking
        ? "speaking"
        : store.state.isGenerating
        ? "processing"
        : st.companion_state || "idle";

    store.set({
      backendHealthy: true,
      status: st,
      settings: setResp.settings || {},
      emergencyStopped: !!st.emergency_stopped,
      operatingMode: op,
      companionState: nextCompanion,
    });
    if (store.state.activeView === "settings") renderSettingsPanel();
  } catch (err) {
    store.set({
      backendHealthy: false,
      companionState: "error",
      diagnosticNotice: `Local RYNA engine unreachable: ${err.message}`,
    });
  }
}

async function loadConversations() {
  const data = await apiRequest("/api/v1/conversations");
  const convs = data.conversations || [];
  let activeId = store.state.activeConvId;
  if (!activeId && convs.length > 0) activeId = convs[0].id;
  if (activeId && !convs.some((c) => c.id === activeId)) activeId = convs[0]?.id || null;
  store.set({ conversations: convs, activeConvId: activeId });
  if (activeId) {
    await loadMessages(activeId);
  } else {
    store.set({ messages: [] });
  }
}

async function loadMessages(convId) {
  const data = await apiRequest(`/api/v1/conversations/${convId}/messages`);
  store.set({ messages: data.messages || [] });
  scrollMessagesToBottom();
}

async function loadMemories() {
  const cat = document.getElementById("mem-filter-cat").value;
  const q = document.getElementById("mem-search").value.trim();
  const params = new URLSearchParams();
  if (cat) params.set("category", cat);
  if (q) params.set("q", q);
  const data = await apiRequest(`/api/v1/memories?${params.toString()}`);
  store.set({ memories: data.memories || [] });
  renderMemories();
}

async function loadTasks() {
  const status = document.getElementById("task-filter").value;
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const data = await apiRequest(`/api/v1/tasks${qs}`);
  store.set({ tasks: data.tasks || [] });
  renderTasks();
}

async function loadActivity() {
  const cat = document.getElementById("activity-filter").value;
  const qs = cat ? `?category=${encodeURIComponent(cat)}` : "";
  const data = await apiRequest(`/api/v1/activity${qs}`);
  store.set({ activity: data.events || [] });
  renderActivity();
}

/* ---------- Streaming Chat, Attachments, Stop & Retry ---------- */
async function sendMessage(textOverride = null, { fromVoice = false, skipUserInsert = false } = {}) {
  const input = document.getElementById("chat-input");
  const text = (textOverride !== null ? textOverride : input.value).trim();
  const attachments = [...store.state.attachments];

  if (!text && attachments.length === 0) return;

  // Interrupt any active speech before sending a new turn
  if (store.state.voice.isSpeaking) {
    await voice.interrupt("User sent new message");
  }

  if (textOverride === null) {
    input.value = "";
    autoSizeTextarea(input);
  }
  store.set({ attachments: [], isGenerating: true, companionState: "processing", companionSubtext: "Thinking…" });
  renderAttachments();

  // Optimistically render user message immediately so the UI feels instant
  if (!skipUserInsert) {
    const tempUser = {
      id: "temp_user_" + Date.now(),
      role: "user",
      content: text || `[Attached ${attachments.length} file(s)]`,
      mode: store.state.operatingMode.online ? "online" : "offline",
      metadata: attachments.length ? { attachments: attachments.map((a) => ({ name: a.name, size: a.size })) } : {},
      created_at: new Date().toISOString(),
    };
    store.set({
      messages: [...store.state.messages, tempUser],
      streamingMessage: { content: "", provider: "", model: "" },
    });
    scrollMessagesToBottom();
  } else {
    store.set({ streamingMessage: { content: "", provider: "", model: "" } });
  }

  activeAbortController = new AbortController();
  let finalAssistantText = "";
  let aiAvailable = true;

  try {
    const resp = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: store.state.activeConvId,
        message: text,
        attachments,
        skip_user_insert: skipUserInsert,
      }),
      signal: activeAbortController.signal,
    });

    if (!resp.ok || !resp.body) {
      throw new Error(`Stream request failed (HTTP ${resp.status})`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIdx;
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const evt = parseSseBlock(block);
        if (!evt) continue;

        if (evt.event === "meta") {
          if (evt.data.conversation_id) {
            store.set({ activeConvId: evt.data.conversation_id });
          }
        } else if (evt.event === "delta") {
          const cur = store.state.streamingMessage || { content: "", provider: "", model: "" };
          const updated = {
            content: cur.content + (evt.data.delta || ""),
            provider: evt.data.provider || cur.provider,
            model: evt.data.model || cur.model,
          };
          store.set({ streamingMessage: updated });
          scrollMessagesToBottom();
        } else if (evt.event === "done") {
          finalAssistantText = evt.data.assistant_message?.content || "";
          aiAvailable = evt.data.ai_available !== false;
        } else if (evt.event === "error") {
          aiAvailable = false;
          store.set({ companionState: "error", companionSubtext: "AI provider unavailable." });
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      toast("Generation stopped.");
    } else {
      store.set({
        companionState: "error",
        diagnosticNotice: `Chat error: ${err.message}`,
      });
    }
  } finally {
    activeAbortController = null;
    store.set({ isGenerating: false, streamingMessage: null });
    await loadConversations();
    await refreshStatus();

    const shouldSpeak =
      aiAvailable &&
      finalAssistantText &&
      !finalAssistantText.startsWith("[Local Tool:") &&
      (fromVoice || (store.state.settings["voice.enabled"] && store.state.settings["voice.auto_speak"]));

    if (shouldSpeak) {
      voice.speak(finalAssistantText);
    } else if (!store.state.voice.isListening && store.state.companionState === "processing") {
      store.set({ companionState: "idle", companionSubtext: "Ready when you are." });
    }
  }
}

function parseSseBlock(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

async function stopGeneration() {
  if (activeAbortController) {
    activeAbortController.abort();
    activeAbortController = null;
  }
  await voice.interrupt("User stopped generation");
  store.set({ isGenerating: false, streamingMessage: null });
}

async function retryAssistantMessage(msgId) {
  const convId = store.state.activeConvId;
  if (!convId || store.state.isGenerating) return;
  const msgs = store.state.messages;
  const lastUser = [...msgs].reverse().find((m) => m.role === "user");
  if (!lastUser) return;

  try {
    // Ask backend to retry cleanly using streaming when possible
    if (msgId) {
      await apiRequest("/api/v1/chat/retry", {
        method: "POST",
        body: { conversation_id: convId, message_id: msgId },
      });
      await loadMessages(convId);
      await refreshStatus();
      toast("Retried response.");
      return;
    }
    await sendMessage(lastUser.content, { skipUserInsert: true });
  } catch (err) {
    toast(`Retry failed: ${err.message}`);
  }
}

/* ---------- Rendering ---------- */
function renderState(st) {
  // Brand & operating mode pill
  document.getElementById("brand-ver").textContent = st.status?.version || "0.2.0";
  const modePill = document.getElementById("operating-mode-pill");
  const op = st.operatingMode || {};
  modePill.className = `mode-pill ${op.id || "ai_unavailable"}`;
  const modelSuffix = op.active_model ? ` · ${op.active_model}` : "";
  document.getElementById("operating-mode-text").textContent = `${op.label || "Checking…"}${modelSuffix}`;
  const modeDot = modePill.querySelector(".dot");
  modeDot.className =
    "dot " +
    (op.id === "online_cloud_ai" || op.id === "online_local_ai"
      ? "ok"
      : op.id === "offline_local_ai"
      ? "cyan"
      : "warn");

  // Voice & Audio chip (honest live status)
  const vChip = document.getElementById("voice-status-chip");
  const vDot = vChip.querySelector(".dot");
  const vText = document.getElementById("voice-status-text");
  if (st.voice.isListening) {
    vDot.className = "dot cyan";
    vText.textContent = "Mic: Listening";
  } else if (st.voice.isSpeaking) {
    vDot.className = "dot ok";
    vText.textContent = "Audio: Speaking";
  } else if (!st.voice.sttSupported) {
    vDot.className = "dot warn";
    vText.textContent = "STT: Browser Unsupported";
  } else if (!st.voice.micAvailable) {
    vDot.className = "dot warn";
    vText.textContent = "Mic: Not Detected";
  } else {
    vDot.className = "dot ok";
    vText.textContent = st.voice.isWakeArmed ? "Voice: Wake Armed" : "Voice: Ready";
  }

  // Backend health chip
  const hDot = document.getElementById("backend-health-chip").querySelector(".dot");
  hDot.className = `dot ${st.backendHealthy ? "ok" : "error"}`;
  document.getElementById("backend-health-text").textContent = st.backendHealthy ? "Engine OK" : "Engine Offline";

  // Banners
  document.getElementById("emergency-banner").classList.toggle("hidden", !st.emergencyStopped);
  const diagBanner = document.getElementById("diagnostic-banner");
  diagBanner.classList.toggle("hidden", !st.diagnosticNotice);
  document.getElementById("diagnostic-banner-text").textContent = st.diagnosticNotice;

  // Companion header & controls
  const badge = document.getElementById("companion-state-badge");
  badge.textContent = st.companionState;
  badge.dataset.state = st.companionState;
  document.getElementById("companion-subtext").textContent = st.companionSubtext;
  document.getElementById("active-model-tag").textContent = op.active_model ? `${op.active_provider}/${op.active_model}` : "";
  document.getElementById("companion-presence").classList.toggle("compact", st.companionCompact);

  const liveTx = document.getElementById("live-transcript");
  liveTx.classList.toggle("hidden", !st.voice.liveTranscript);
  liveTx.textContent = st.voice.liveTranscript ? `“${st.voice.liveTranscript}”` : "";

  const wakeDot = document.getElementById("wake-dot");
  wakeDot.className = `dot ${st.voice.isWakeArmed ? "cyan" : ""}`;
  document.getElementById("wake-btn-label").textContent = st.voice.isWakeArmed
    ? `Wake: ${st.settings["voice.wake_word"] || "Hey Ryna"}`
    : "Wake Word Off";

  // Input action buttons synchronized with live state
  document.getElementById("btn-mic").classList.toggle("listening", st.voice.isListening);
  document.getElementById("btn-stop-gen").classList.toggle("hidden", !st.isGenerating);
  document.getElementById("btn-interrupt-speech").classList.toggle("hidden", !st.voice.isSpeaking);
  document.getElementById("btn-header-interrupt").classList.toggle("hidden", !(st.voice.isSpeaking || st.isGenerating));
  document.getElementById("btn-send").disabled = st.isGenerating;
  document.getElementById("auto-speak-indicator").textContent =
    st.settings["voice.enabled"] && st.settings["voice.auto_speak"] ? "Voice replies: On" : "Voice replies: Off";

  // History drawer & floating dock
  document.getElementById("home-grid").classList.toggle("history-collapsed", !st.historyOpen);
  document.getElementById("floating-dock").classList.toggle("hidden", !st.dockOpen);
  document.getElementById("dock-state-text").textContent = st.companionState;
  if (st.voice.liveTranscript) {
    document.getElementById("dock-transcript").textContent = `“${st.voice.liveTranscript}”`;
  } else if (st.streamingMessage?.content) {
    document.getElementById("dock-transcript").textContent = st.streamingMessage.content.slice(-160);
  } else {
    document.getElementById("dock-transcript").textContent = st.companionSubtext;
  }

  renderConversations();
  renderMessages();
  syncMemoryControls();
}

function renderConversations() {
  const list = document.getElementById("conv-list");
  const convs = store.state.conversations;
  if (!convs.length) {
    list.innerHTML = `<div class="muted" style="padding:8px;">No conversations yet.</div>`;
    return;
  }
  list.innerHTML = convs
    .map(
      (c) => `
      <div class="conv-item ${c.id === store.state.activeConvId ? "active" : ""}" data-conv="${c.id}">
        <span class="conv-title">${esc(c.title)}</span>
        <button class="conv-del" data-del-conv="${c.id}" title="Delete">✕</button>
      </div>`
    )
    .join("");
}

function renderMessages() {
  const box = document.getElementById("messages-viewport");
  const msgs = store.state.messages;
  const streaming = store.state.streamingMessage;

  if (!msgs.length && !streaming) {
    const op = store.state.operatingMode;
    const hint = op.active_model
      ? `Connected to ${op.active_provider} (${op.active_model}). Type a message, attach a file, or click the microphone to speak.`
      : `Local tools (/diag, /calc, /find, /note) work offline right now. Configure Ollama or Cloud AI in Settings to enable AI replies.`;
    box.innerHTML = `
      <div class="empty-state">
        <h2>Hello, I'm RYNA</h2>
        <p>${esc(hint)}</p>
      </div>`;
    return;
  }

  const html = msgs
    .map((m) => {
      const atts = m.metadata?.attachments || [];
      const attHtml = atts.length
        ? `<div class="msg-attachments">${atts.map((a) => `<span class="att-badge">📎 ${esc(a.name)}</span>`).join("")}</div>`
        : "";
      const meta = [formatTime(m.created_at)];
      if (m.provider) meta.push(m.provider);
      if (m.model) meta.push(m.model);
      if (m.metadata?.latency_ms) meta.push(`${m.metadata.latency_ms} ms`);

      const actions =
        m.role !== "user"
          ? `<span class="msg-actions">
               <button class="msg-act-btn" data-copy-msg="${m.id}">Copy</button>
               <button class="msg-act-btn" data-speak-msg="${m.id}">Speak</button>
               <button class="msg-act-btn" data-retry-msg="${m.id}">Retry</button>
             </span>`
          : "";

      return `
        <article class="msg-row ${esc(m.role)}">
          ${attHtml}
          <div class="msg-bubble">${renderMarkdownLite(m.content)}</div>
          <div class="msg-footer">
            <span>${esc(meta.filter(Boolean).join(" · "))}</span>
            ${actions}
          </div>
        </article>`;
    })
    .join("");

  const streamHtml = streaming
    ? `<article class="msg-row assistant">
         <div class="msg-bubble">${streaming.content ? renderMarkdownLite(streaming.content) : '<span class="muted">Thinking…</span>'}</div>
         <div class="msg-footer"><span>Streaming${streaming.model ? ` · ${esc(streaming.model)}` : ""}…</span></div>
       </article>`
    : "";

  box.innerHTML = html + streamHtml;
}

function scrollMessagesToBottom() {
  const box = document.getElementById("messages-viewport");
  if (box) box.scrollTop = box.scrollHeight;
}

function renderAttachments() {
  const bar = document.getElementById("attachment-bar");
  const atts = store.state.attachments;
  bar.classList.toggle("hidden", !atts.length);
  bar.innerHTML = atts
    .map(
      (a, idx) => `
      <span class="attachment-chip">
        <span>📎 ${esc(a.name)} (${Math.max(1, Math.round(a.size / 1024))} KB)</span>
        <button type="button" data-rm-att="${idx}" aria-label="Remove attachment">✕</button>
      </span>`
    )
    .join("");
}

function syncMemoryControls() {
  const en = document.getElementById("mem-ctrl-enabled");
  const pa = document.getElementById("mem-ctrl-paused");
  if (en) en.checked = !!store.state.settings["memory.enabled"];
  if (pa) pa.checked = !!store.state.settings["memory.learning_paused"];
}

function renderMemories() {
  const list = document.getElementById("memory-list");
  const items = store.state.memories;
  if (!items.length) {
    list.innerHTML = `<div class="muted">No memories stored matching your filter.</div>`;
    return;
  }
  list.innerHTML = items
    .map(
      (m) => `
      <div class="record-card">
        <div class="record-head">
          <span class="badge">${esc(m.category)} · ${esc(m.memory_type)}</span>
          <span class="muted">${Math.round((m.confidence || 0) * 100)}% confidence</span>
        </div>
        <div>${esc(m.content)}</div>
        <div class="muted">${m.tags?.length ? `Tags: ${esc(m.tags.join(", "))} · ` : ""}Updated ${formatTime(m.updated_at)}</div>
        <div class="action-row">
          <button class="btn-secondary small" data-mem-edit="${m.id}">Edit</button>
          <button class="btn-danger small" data-mem-del="${m.id}">Delete</button>
        </div>
      </div>`
    )
    .join("");
}

function renderTasks() {
  const list = document.getElementById("task-list");
  const tasks = store.state.tasks;
  if (!tasks.length) {
    list.innerHTML = `<div class="muted">No tasks in queue. Create one to test persistent execution and approvals.</div>`;
    return;
  }
  list.innerHTML = tasks
    .map((t) => {
      const steps = t.steps || [];
      const doneSteps = steps.filter((s) => s.status === "completed").length;
      const pct = steps.length ? Math.round((doneSteps / steps.length) * 100) : 0;
      const badgeCls =
        t.status === "completed" ? "ok" :
        t.status === "failed" ? "error" :
        t.status === "waiting_for_approval" || t.status === "paused" ? "warn" : "";

      return `
        <div class="record-card">
          <div class="record-head">
            <strong>${esc(t.title)}</strong>
            <span class="badge ${badgeCls}">${esc(t.status)} · ${esc(t.outcome_status)}</span>
          </div>
          <div class="muted">${esc(t.goal)} · Level ${t.permission_level}${t.requires_internet ? " · Online" : " · Offline"}</div>
          <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
          <div class="muted">Progress: ${doneSteps}/${steps.length} step(s) (${pct}%)</div>
          ${t.result_summary ? `<pre style="margin:4px 0;font-size:11.5px;overflow:auto;">${esc(t.result_summary)}</pre>` : ""}
          ${t.error_message ? `<div class="inline-error">${esc(t.error_message)}</div>` : ""}
          <div class="action-row">
            ${t.status === "pending" || t.status === "paused" ? `<button class="btn-primary small" data-task-act="run" data-id="${t.id}">Run / Resume</button>` : ""}
            ${t.status === "waiting_for_approval" ? `<button class="btn-primary small" data-task-act="approve" data-id="${t.id}">Approve & Execute</button>` : ""}
            ${t.status === "failed" || t.status === "cancelled" ? `<button class="btn-secondary small" data-task-act="retry" data-id="${t.id}">Retry</button>` : ""}
            ${t.status === "running" ? `<button class="btn-warn small" data-task-act="pause" data-id="${t.id}">Pause</button>` : ""}
            ${!["completed", "cancelled"].includes(t.status) ? `<button class="btn-danger small" data-task-act="cancel" data-id="${t.id}">Cancel</button>` : ""}
          </div>
        </div>`;
    })
    .join("");
}

function renderActivity() {
  const list = document.getElementById("activity-list");
  const events = store.state.activity;
  if (!events.length) {
    list.innerHTML = `<div class="muted">No application events recorded yet. Send a message, run a tool, or use voice to see real events here.</div>`;
    return;
  }
  list.innerHTML = events
    .map((e) => {
      const cls = e.status === "error" ? "error" : e.status === "warn" ? "warn" : e.status === "ok" ? "ok" : "muted";
      return `
        <div class="record-card">
          <div class="record-head">
            <div><span class="badge ${cls}">${esc(e.category)} · ${esc(e.event_type)}</span></div>
            <span class="muted">${formatTime(e.created_at)}</span>
          </div>
          <div>${esc(e.summary)}</div>
        </div>`;
    })
    .join("");
}

/* ---------- Settings ---------- */
const SETTINGS_TABS = [
  { id: "ai", label: "AI Providers & Models" },
  { id: "voice", label: "Voice, Mic & Speaker" },
  { id: "memory", label: "Memory & Privacy" },
  { id: "permissions", label: "Permissions & Safety" },
  { id: "companion", label: "Companion & Startup" },
  { id: "system", label: "Notifications & Diagnostics" },
];

async function saveSettings(patch) {
  const resp = await apiRequest("/api/v1/settings", { method: "PUT", body: { settings: patch } });
  store.set({ settings: resp.settings });
  toast("Settings saved.");
  await refreshStatus();
}

function renderSettingsPanel() {
  const nav = document.getElementById("settings-nav");
  const body = document.getElementById("settings-body");
  const tab = store.state.activeSettingsTab;
  const s = store.state.settings;
  const st = store.state.status || {};

  nav.innerHTML = SETTINGS_TABS.map(
    (t) => `<button class="nav-item ${t.id === tab ? "active" : ""}" data-stab="${t.id}"><span>${t.label}</span></button>`
  ).join("");

  if (tab === "ai") {
    const provs = st.ai?.providers || {};
    const ollamaModels = provs.ollama?.models || [];
    const hw = st.hardware || {};
    body.innerHTML = `
      <h2>AI Providers — Local & Cloud</h2>
      <p class="muted">Detected hardware: ${hw.ram_total_gb || "?"} GB RAM (${esc(hw.recommended_tier || "unknown")} tier). Recommended local models: <code>${esc((hw.recommended_local_models || []).join(", "))}</code>.</p>
      <div class="field-row">
        <label class="field"><span>Preferred Provider</span>
          <select id="s-ai-pref">${["auto", "ollama", "openai", "anthropic", "gemini"].map((p) => `<option value="${p}" ${s["ai.preferred_provider"] === p ? "selected" : ""}>${p}</option>`).join("")}</select>
        </label>
        <label class="field"><span>Fallback Provider</span>
          <select id="s-ai-fb">${["ollama", "openai", "anthropic", "gemini"].map((p) => `<option value="${p}" ${s["ai.fallback_provider"] === p ? "selected" : ""}>${p}</option>`).join("")}</select>
        </label>
      </div>
      <div class="divider"></div>
      <h3>Local AI (Ollama)</h3>
      <div class="field-row">
        <label class="field"><span>Ollama URL</span><input id="s-ollama-url" type="text" value="${esc(s["ai.ollama_base_url"] || "http://127.0.0.1:11434")}" /></label>
        <label class="field"><span>Active Local Model</span>
          ${ollamaModels.length
            ? `<select id="s-local-model"><option value="">Auto (${esc(ollamaModels[0])})</option>${ollamaModels.map((m) => `<option value="${esc(m)}" ${s["ai.local_model"] === m ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`
            : `<input id="s-local-model" type="text" value="${esc(s["ai.local_model"] || "")}" placeholder="e.g., qwen2.5:3b" />`}
        </label>
      </div>
      <div class="divider"></div>
      <h3>Cloud AI</h3>
      <label class="switch-row"><input type="checkbox" id="s-allow-cloud" ${s["ai.allow_cloud"] ? "checked" : ""}/><span>Allow Cloud AI when Online (keys loaded from .env)</span></label>
      <label class="switch-row"><input type="checkbox" id="s-sens-cloud" ${s["ai.send_sensitive_to_cloud"] ? "checked" : ""}/><span>Permit sending sensitive terms to cloud (Not Recommended)</span></label>
      <div class="field-row">
        <label class="field"><span>OpenAI Model</span><input id="s-oai-mod" type="text" value="${esc(s["ai.openai_model"] || "gpt-4o-mini")}" /></label>
        <label class="field"><span>Gemini Model</span><input id="s-gem-mod" type="text" value="${esc(s["ai.gemini_model"] || "gemini-2.0-flash")}" /></label>
      </div>
      <div class="action-row"><button class="btn-primary" id="btn-save-ai">Save AI Configuration</button></div>
      <div class="divider"></div>
      <h3>Live Provider Status</h3>
      ${Object.values(provs).map((p) => `<div class="record-card"><div class="record-head"><strong>${esc(p.name)} (${p.is_local ? "Local" : "Cloud"})</strong><span class="badge ${p.available ? "ok" : "warn"}">${p.available ? "Ready" : "Unavailable"}</span></div><div class="muted">${esc(p.detail)}</div></div>`).join("")}`;

    document.getElementById("btn-save-ai").onclick = () =>
      saveSettings({
        "ai.preferred_provider": document.getElementById("s-ai-pref").value,
        "ai.fallback_provider": document.getElementById("s-ai-fb").value,
        "ai.ollama_base_url": document.getElementById("s-ollama-url").value.trim(),
        "ai.local_model": document.getElementById("s-local-model").value.trim(),
        "ai.allow_cloud": document.getElementById("s-allow-cloud").checked,
        "ai.send_sensitive_to_cloud": document.getElementById("s-sens-cloud").checked,
        "ai.openai_model": document.getElementById("s-oai-mod").value.trim(),
        "ai.gemini_model": document.getElementById("s-gem-mod").value.trim(),
      });
  } else if (tab === "voice") {
    const mics = store.state.voice.micDevices;
    const allVoices = [
      ...store.state.voice.sapiVoices.map((v) => ({ name: v, tag: "Windows SAPI Offline" })),
      ...store.state.voice.browserVoices.map((v) => ({ name: v.name, tag: `Browser · ${v.lang}` })),
    ];
    const seen = new Set();
    const uniqueVoices = allVoices.filter((v) => (seen.has(v.name) ? false : (seen.add(v.name), true)));

    body.innerHTML = `
      <h2>Voice, Wake Word, Microphone & Speaker</h2>
      <label class="switch-row"><input type="checkbox" id="s-v-en" ${s["voice.enabled"] ? "checked" : ""}/><span>Enable Voice Interaction</span></label>
      <label class="switch-row"><input type="checkbox" id="s-v-auto" ${s["voice.auto_speak"] ? "checked" : ""}/><span>Automatically speak RYNA's replies aloud</span></label>
      <div class="field-row">
        <label class="field"><span>Conversation Mode</span>
          <select id="s-v-mode">
            <option value="push_to_talk" ${s["voice.mode"] === "push_to_talk" ? "selected" : ""}>Push-to-Talk</option>
            <option value="wake_word" ${s["voice.mode"] === "wake_word" ? "selected" : ""}>Wake Word ("Hey Ryna")</option>
            <option value="continuous" ${s["voice.mode"] === "continuous" ? "selected" : ""}>Continuous Turn-Taking</option>
          </select>
        </label>
        <label class="field"><span>Wake Word Phrase</span><input id="s-v-wake" type="text" value="${esc(s["voice.wake_word"] || "Hey Ryna")}" /></label>
      </div>
      <div class="field-row">
        <label class="field"><span>Speech Synthesis Engine</span>
          <select id="s-v-tts">
            <option value="browser" ${s["voice.tts_engine"] !== "windows-sapi" ? "selected" : ""}>Browser SpeechSynthesis (Instant + Word Sync)</option>
            <option value="windows-sapi" ${s["voice.tts_engine"] === "windows-sapi" ? "selected" : ""}>Windows Native SAPI (100% Offline OS Engine)</option>
          </select>
        </label>
        <label class="field"><span>Speech Speed (${Number(s["voice.speech_speed"] ?? 1.0).toFixed(1)}x)</span>
          <input id="s-v-speed" type="range" min="0.6" max="1.8" step="0.1" value="${s["voice.speech_speed"] ?? 1.0}" />
        </label>
      </div>
      <div class="field-row">
        <label class="field"><span>Voice Selection</span>
          <select id="s-v-voice">
            ${uniqueVoices.length
              ? uniqueVoices.map((v) => `<option value="${esc(v.name)}" ${s["voice.selected_voice"] === v.name ? "selected" : ""}>${esc(v.name)} (${esc(v.tag)})</option>`).join("")
              : `<option value="Microsoft Zira Desktop">System Default</option>`}
          </select>
        </label>
        <label class="field"><span>Microphone Input</span>
          <select id="s-v-mic">
            <option value="default">System Default Microphone</option>
            ${mics.map((m) => `<option value="${esc(m.deviceId)}" ${s["voice.microphone"] === m.deviceId ? "selected" : ""}>${esc(m.label)}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="action-row">
        <button class="btn-primary" id="btn-save-voice">Save Voice Settings</button>
        <button class="btn-secondary" id="btn-test-voice">Test Voice Output</button>
      </div>
      <div class="divider"></div>
      <h3>Detected Audio & Voice Backends</h3>
      ${(st.voice?.backends || []).map((b) => `<div class="record-card"><div class="record-head"><strong>${esc(b.name)} (${esc(b.role)})</strong><span class="badge ${b.available ? "ok" : "muted"}">${b.available ? "Available" : "Not Installed"}</span></div><div class="muted">${esc(b.detail)}</div></div>`).join("")}`;

    document.getElementById("btn-save-voice").onclick = () =>
      saveSettings({
        "voice.enabled": document.getElementById("s-v-en").checked,
        "voice.auto_speak": document.getElementById("s-v-auto").checked,
        "voice.mode": document.getElementById("s-v-mode").value,
        "voice.wake_word": document.getElementById("s-v-wake").value.trim() || "Hey Ryna",
        "voice.tts_engine": document.getElementById("s-v-tts").value,
        "voice.speech_speed": Number(document.getElementById("s-v-speed").value),
        "voice.selected_voice": document.getElementById("s-v-voice").value,
        "voice.microphone": document.getElementById("s-v-mic").value,
      });

    document.getElementById("btn-test-voice").onclick = () => {
      voice.speak("Hello. RYNA voice output is active and ready.");
    };
  } else if (tab === "memory") {
    body.innerHTML = `
      <h2>Memory & Privacy</h2>
      <label class="switch-row"><input type="checkbox" id="s-m-en" ${s["memory.enabled"] ? "checked" : ""}/><span>Enable Persistent Memory</span></label>
      <label class="switch-row"><input type="checkbox" id="s-m-pause" ${s["memory.learning_paused"] ? "checked" : ""}/><span>Pause Automatic Learning</span></label>
      <label class="switch-row"><input type="checkbox" id="s-p-redact" ${s["privacy.redact_secrets"] ? "checked" : ""}/><span>Block passwords, tokens, and API keys from ordinary memory</span></label>
      <label class="field"><span>Memory Retention (days)</span><input id="s-m-ret" type="number" min="1" max="3650" value="${s["memory.retention_days"] ?? 365}" /></label>
      <div class="action-row"><button class="btn-primary" id="btn-save-mem">Save Memory & Privacy</button></div>`;
    document.getElementById("btn-save-mem").onclick = () =>
      saveSettings({
        "memory.enabled": document.getElementById("s-m-en").checked,
        "memory.learning_paused": document.getElementById("s-m-pause").checked,
        "privacy.redact_secrets": document.getElementById("s-p-redact").checked,
        "memory.retention_days": Number(document.getElementById("s-m-ret").value),
      });
  } else if (tab === "permissions") {
    body.innerHTML = `
      <h2>Permissions & Computer Control</h2>
      <label class="switch-row"><input type="checkbox" id="s-p1" ${s["permissions.level1_auto"] ? "checked" : ""}/><span>Level 1 (Automatic): Allow read-only local tools without prompt</span></label>
      <label class="switch-row"><input type="checkbox" id="s-p2" ${s["permissions.level2_require_confirm"] ? "checked" : ""}/><span>Level 2 (Confirmation): Require explicit approval before execution</span></label>
      <label class="switch-row"><input type="checkbox" id="s-p3" ${s["permissions.level3_block_destructive"] ? "checked" : ""}/><span>Level 3 (Restricted): Block destructive commands and security modifications</span></label>
      <label class="field"><span>Allowed Workspace File Root</span><input id="s-cc-root" type="text" value="${esc(s["computer_control.allowed_root"] || ".")}" /></label>
      <div class="action-row"><button class="btn-primary" id="btn-save-perm">Save Permissions</button></div>`;
    document.getElementById("btn-save-perm").onclick = () =>
      saveSettings({
        "permissions.level1_auto": document.getElementById("s-p1").checked,
        "permissions.level2_require_confirm": document.getElementById("s-p2").checked,
        "permissions.level3_block_destructive": document.getElementById("s-p3").checked,
        "computer_control.allowed_root": document.getElementById("s-cc-root").value.trim() || ".",
      });
  } else if (tab === "companion") {
    body.innerHTML = `
      <h2>Companion Appearance & Windows Startup</h2>
      <div class="field-row">
        <label class="field"><span>Native Companion Size (${s["appearance.companion_size"] ?? 176}px)</span>
          <input id="s-c-size" type="range" min="120" max="260" step="8" value="${s["appearance.companion_size"] ?? 176}" />
        </label>
        <label class="field"><span>Transparency (${Number(s["appearance.companion_transparency"] ?? 0.94).toFixed(2)})</span>
          <input id="s-c-alpha" type="range" min="0.55" max="1.0" step="0.03" value="${s["appearance.companion_transparency"] ?? 0.94}" />
        </label>
      </div>
      <label class="switch-row"><input type="checkbox" id="s-c-top" ${s["appearance.companion_always_on_top"] ? "checked" : ""}/><span>Native Companion Always on Top</span></label>
      <label class="switch-row"><input type="checkbox" id="s-c-red" ${s["appearance.reduced_animations"] ? "checked" : ""}/><span>Reduced Animations (low-resource mode)</span></label>
      <label class="switch-row"><input type="checkbox" id="s-st-comp" ${s["startup.show_companion_on_start"] ? "checked" : ""}/><span>Show Desktop Companion on Launch</span></label>
      <p class="muted">Manage Windows user-login startup safely via <code>scripts/startup_windows.ps1 -Action status|enable|disable</code>.</p>
      <div class="action-row"><button class="btn-primary" id="btn-save-comp">Save Appearance & Startup</button></div>`;
    document.getElementById("btn-save-comp").onclick = () =>
      saveSettings({
        "appearance.companion_size": Number(document.getElementById("s-c-size").value),
        "appearance.companion_transparency": Number(document.getElementById("s-c-alpha").value),
        "appearance.companion_always_on_top": document.getElementById("s-c-top").checked,
        "appearance.reduced_animations": document.getElementById("s-c-red").checked,
        "startup.show_companion_on_start": document.getElementById("s-st-comp").checked,
      });
  } else {
    body.innerHTML = `
      <h2>Notifications & Live System Diagnostics</h2>
      <label class="switch-row"><input type="checkbox" id="s-n-en" ${s["notifications.enabled"] ? "checked" : ""}/><span>Enable Task & System Notifications</span></label>
      <label class="switch-row"><input type="checkbox" id="s-n-pro" ${s["notifications.proactive_suggestions"] ? "checked" : ""}/><span>Allow Optional Proactive Suggestions</span></label>
      <div class="action-row"><button class="btn-primary" id="btn-save-notif">Save Notification Preferences</button></div>
      <div class="divider"></div>
      <h3>Hardware & Database Telemetry (Live Local Readout)</h3>
      <pre style="background:var(--bg-obsidian);padding:12px;border-radius:10px;border:1px solid var(--border-subtle);overflow:auto;font-size:12px;">${esc(JSON.stringify({ hardware: st.hardware, database: st.database }, null, 2))}</pre>`;
    document.getElementById("btn-save-notif").onclick = () =>
      saveSettings({
        "notifications.enabled": document.getElementById("s-n-en").checked,
        "notifications.proactive_suggestions": document.getElementById("s-n-pro").checked,
      });
  }
}

/* ---------- Navigation & Events ---------- */
function switchView(viewId) {
  store.set({ activeView: viewId });
  document.querySelectorAll(".nav-item[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${viewId}`);
  });
  const titles = { home: "Home", memory: "Memory", tasks: "Tasks", activity: "Activity", settings: "Settings" };
  document.getElementById("view-title").textContent = titles[viewId] || "RYNA";
  if (viewId === "memory") loadMemories();
  if (viewId === "tasks") loadTasks();
  if (viewId === "activity") loadActivity();
  if (viewId === "settings") renderSettingsPanel();
}

function autoSizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(140, el.scrollHeight) + "px";
}

function setupFloatingDockDrag() {
  const dock = document.getElementById("floating-dock");
  const handle = document.getElementById("dock-drag-handle");
  let dragging = false, ox = 0, oy = 0;

  handle.addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) return;
    dragging = true;
    const rect = dock.getBoundingClientRect();
    ox = e.clientX - rect.left;
    oy = e.clientY - rect.top;
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    dock.style.right = "auto";
    dock.style.bottom = "auto";
    dock.style.left = Math.max(8, Math.min(window.innerWidth - dock.offsetWidth - 8, e.clientX - ox)) + "px";
    dock.style.top = Math.max(8, Math.min(window.innerHeight - dock.offsetHeight - 8, e.clientY - oy)) + "px";
  });
  window.addEventListener("mouseup", () => { dragging = false; });
}

function bindUI() {
  // Primary nav
  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  // Topbar & companion controls
  document.getElementById("btn-toggle-history").onclick = () => store.set({ historyOpen: !store.state.historyOpen });
  document.getElementById("btn-compact-companion").onclick = () => store.set({ companionCompact: !store.state.companionCompact });
  document.getElementById("btn-toggle-companion-dock").onclick = () => store.set({ dockOpen: !store.state.dockOpen });
  document.getElementById("btn-dock-hide").onclick = () => store.set({ dockOpen: false });
  document.getElementById("btn-dock-expand").onclick = () => {
    store.set({ dockOpen: false });
    switchView("home");
    document.getElementById("chat-input").focus();
  };
  document.getElementById("btn-dismiss-diagnostic").onclick = () => store.set({ diagnosticNotice: "" });

  // Emergency stop
  document.getElementById("btn-emergency-stop").onclick = async () => {
    await stopGeneration();
    await apiRequest("/api/v1/emergency-stop", { method: "POST", body: { reason: "User clicked Stop All" } });
    toast("Emergency Stop activated.");
    await refreshStatus();
  };
  document.getElementById("btn-reset-emergency").onclick = async () => {
    await apiRequest("/api/v1/emergency-stop/reset", { method: "POST", body: {} });
    toast("Normal operation resumed.");
    await refreshStatus();
  };

  // Conversations
  document.getElementById("btn-new-conv").onclick = async () => {
    const res = await apiRequest("/api/v1/conversations", { method: "POST", body: { title: "New Conversation" } });
    store.set({ activeConvId: res.conversation.id });
    await loadConversations();
    document.getElementById("chat-input").focus();
  };

  document.getElementById("conv-list").addEventListener("click", async (e) => {
    const del = e.target.closest("button[data-del-conv]");
    if (del) {
      e.stopPropagation();
      const id = del.dataset.delConv;
      await apiRequest(`/api/v1/conversations/${id}`, { method: "DELETE" });
      if (store.state.activeConvId === id) store.set({ activeConvId: null });
      await loadConversations();
      return;
    }
    const item = e.target.closest(".conv-item[data-conv]");
    if (item) {
      store.set({ activeConvId: item.dataset.conv });
      await loadMessages(item.dataset.conv);
    }
  });

  // Message actions (Copy, Speak, Retry)
  document.getElementById("messages-viewport").addEventListener("click", async (e) => {
    const copyBtn = e.target.closest("button[data-copy-msg]");
    if (copyBtn) {
      const m = store.state.messages.find((x) => x.id === copyBtn.dataset.copyMsg);
      if (m && navigator.clipboard) {
        await navigator.clipboard.writeText(m.content);
        toast("Response copied.");
      }
      return;
    }
    const speakBtn = e.target.closest("button[data-speak-msg]");
    if (speakBtn) {
      const m = store.state.messages.find((x) => x.id === speakBtn.dataset.speakMsg);
      if (m) voice.speak(m.content);
      return;
    }
    const retryBtn = e.target.closest("button[data-retry-msg]");
    if (retryBtn) {
      await retryAssistantMessage(retryBtn.dataset.retryMsg);
    }
  });

  // Composer input & attachments
  const chatInput = document.getElementById("chat-input");
  chatInput.addEventListener("input", () => autoSizeTextarea(chatInput));
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  document.getElementById("btn-send").onclick = () => sendMessage();
  document.getElementById("btn-stop-gen").onclick = () => stopGeneration();
  document.getElementById("btn-interrupt-speech").onclick = () => voice.interrupt("User clicked Interrupt");
  document.getElementById("btn-header-interrupt").onclick = async () => {
    if (store.state.isGenerating) await stopGeneration();
    else await voice.interrupt("User clicked header Interrupt");
  };

  // Voice buttons
  document.getElementById("btn-mic").onclick = () => voice.toggleListening();
  document.getElementById("btn-dock-mic").onclick = () => voice.toggleListening();
  document.getElementById("btn-wake-toggle").onclick = () => voice.toggleWakeWord();

  // Dock input connected to exact same session
  const dockInput = document.getElementById("dock-input");
  const sendFromDock = () => {
    const txt = dockInput.value.trim();
    if (!txt) return;
    dockInput.value = "";
    sendMessage(txt);
  };
  document.getElementById("btn-dock-send").onclick = sendFromDock;
  dockInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      sendFromDock();
    }
  });

  // File attachment reader (real content attached to prompt)
  const fileInput = document.getElementById("file-input");
  document.getElementById("btn-attach").onclick = () => fileInput.click();
  fileInput.addEventListener("change", async () => {
    const next = [...store.state.attachments];
    for (const file of Array.from(fileInput.files || []).slice(0, 5)) {
      if (file.size > 256 * 1024) {
        toast(`Skipped ${file.name} (max 256 KB for direct context attachment).`);
        continue;
      }
      const content = await file.text();
      next.push({ name: file.name, size: file.size, mime: file.type || "text/plain", content });
    }
    fileInput.value = "";
    store.set({ attachments: next });
    renderAttachments();
  });

  document.getElementById("attachment-bar").addEventListener("click", (e) => {
    const rm = e.target.closest("button[data-rm-att]");
    if (!rm) return;
    const idx = Number(rm.dataset.rmAtt);
    const next = store.state.attachments.filter((_, i) => i !== idx);
    store.set({ attachments: next });
    renderAttachments();
  });

  document.querySelectorAll(".tool-chip[data-insert]").forEach((chip) => {
    chip.addEventListener("click", () => {
      chatInput.value = chip.dataset.insert;
      chatInput.focus();
    });
  });

  // Memory view
  document.getElementById("memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("mem-form-error");
    errEl.classList.add("hidden");
    try {
      const tags = document.getElementById("mem-tags").value.split(",").map((x) => x.trim()).filter(Boolean);
      await apiRequest("/api/v1/memories", {
        method: "POST",
        body: {
          category: document.getElementById("mem-category").value,
          memory_type: document.getElementById("mem-type").value,
          content: document.getElementById("mem-content").value,
          tags,
        },
      });
      e.target.reset();
      toast("Memory saved.");
      await loadMemories();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  });
  document.getElementById("mem-filter-cat").onchange = loadMemories;
  document.getElementById("mem-search").oninput = loadMemories;
  document.getElementById("mem-ctrl-enabled").onchange = (e) => saveSettings({ "memory.enabled": e.target.checked });
  document.getElementById("mem-ctrl-paused").onchange = (e) => saveSettings({ "memory.learning_paused": e.target.checked });
  document.getElementById("btn-export-mem").onclick = async () => {
    const data = await apiRequest("/api/v1/memories/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ryna-memories.json";
    a.click();
    toast(`Exported ${data.count} memories.`);
  };
  document.getElementById("btn-clear-mem").onclick = async () => {
    if (!confirm("Permanently clear all saved memories?")) return;
    const res = await apiRequest("/api/v1/memories", { method: "DELETE" });
    toast(`Cleared ${res.deleted_count} memories.`);
    await loadMemories();
  };
  document.getElementById("memory-list").addEventListener("click", async (e) => {
    const del = e.target.closest("button[data-mem-del]");
    if (del) {
      await apiRequest(`/api/v1/memories/${del.dataset.memDel}`, { method: "DELETE" });
      await loadMemories();
      return;
    }
    const edit = e.target.closest("button[data-mem-edit]");
    if (edit) {
      const m = store.state.memories.find((x) => x.id === edit.dataset.memEdit);
      if (!m) return;
      const next = prompt("Edit memory:", m.content);
      if (next && next.trim()) {
        try {
          await apiRequest(`/api/v1/memories/${m.id}`, { method: "PATCH", body: { content: next.trim() } });
          await loadMemories();
        } catch (err) {
          toast(`Rejected: ${err.message}`);
        }
      }
    }
  });

  // Tasks view
  const submitTask = async (autoRun) => {
    const title = document.getElementById("task-title").value.trim();
    const goal = document.getElementById("task-goal").value.trim();
    if (!title || !goal) return;
    await apiRequest("/api/v1/tasks", {
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
    toast(autoRun ? "Task created and run." : "Task queued.");
    await loadTasks();
  };
  document.getElementById("task-form").addEventListener("submit", (e) => { e.preventDefault(); submitTask(false); });
  document.getElementById("btn-create-run-task").onclick = () => submitTask(true);
  document.getElementById("task-filter").onchange = loadTasks;
  document.getElementById("task-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-task-act]");
    if (!btn) return;
    const { id, taskAct } = btn.dataset;
    if (taskAct === "run") await apiRequest(`/api/v1/tasks/${id}/run`, { method: "POST", body: { approved: false } });
    if (taskAct === "approve") await apiRequest(`/api/v1/tasks/${id}/run`, { method: "POST", body: { approved: true } });
    if (taskAct === "retry") await apiRequest(`/api/v1/tasks/${id}/retry`, { method: "POST", body: { approved: false } });
    if (taskAct === "pause") await apiRequest(`/api/v1/tasks/${id}/pause`, { method: "POST", body: {} });
    if (taskAct === "cancel") await apiRequest(`/api/v1/tasks/${id}/cancel`, { method: "POST", body: {} });
    await loadTasks();
    await refreshStatus();
  });

  // Activity view
  document.getElementById("activity-filter").onchange = loadActivity;
  document.getElementById("btn-refresh-activity").onclick = loadActivity;
  document.getElementById("btn-clear-activity").onclick = async () => {
    await apiRequest("/api/v1/activity", { method: "DELETE" });
    await loadActivity();
    toast("Activity log cleared.");
  };

  // Settings tab switching
  document.getElementById("settings-nav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-stab]");
    if (!btn) return;
    store.set({ activeSettingsTab: btn.dataset.stab });
    renderSettingsPanel();
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (store.state.isGenerating) stopGeneration();
      else if (store.state.voice.isSpeaking) voice.interrupt("Pressed Escape");
      else if (store.state.voice.isListening) voice.stopListening();
    } else if (e.ctrlKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      document.getElementById("btn-new-conv").click();
    } else if (e.ctrlKey && e.key.toLowerCase() === "m") {
      e.preventDefault();
      voice.toggleListening();
    } else if (e.altKey && ["1", "2", "3", "4", "5"].includes(e.key)) {
      e.preventDefault();
      switchView(["home", "memory", "tasks", "activity", "settings"][Number(e.key) - 1]);
    }
  });

  setupFloatingDockDrag();
}

/* ---------- Boot ---------- */
async function boot() {
  store.subscribe(renderState);
  bindUI();
  companion.start();
  await voice.init();
  await refreshStatus();
  await loadConversations();
  connectEventStream();
  setInterval(refreshStatus, 20000);
}

boot();
