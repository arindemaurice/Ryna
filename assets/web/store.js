/**
 * Centralized reactive state store for RYNA 0.2.0.
 * Every UI component subscribes to real state updates sourced from the local engine & audio pipeline.
 */

export class RynaStore {
  constructor() {
    this._listeners = new Set();
    this.state = {
      activeView: "home",
      historyOpen: true,
      companionCompact: false,
      dockOpen: false,
      activeSettingsTab: "ai",

      // Live backend & connectivity state
      backendHealthy: true,
      status: null,
      settings: {},
      emergencyStopped: false,
      operatingMode: {
        id: "ai_unavailable",
        label: "Checking…",
        local_ai_available: false,
        cloud_ai_available: false,
        active_provider: "",
        active_model: "",
      },

      // Companion state: idle | listening | processing | speaking | executing | waiting | offline | error
      companionState: "idle",
      companionSubtext: "Ready when you are.",

      // Conversation & streaming generation
      conversations: [],
      activeConvId: null,
      messages: [],
      isGenerating: false,
      streamingMessage: null, // { content, provider, model }
      attachments: [], // [{ name, size, mime, content }]

      // Real voice & audio pipeline state
      voice: {
        sttSupported: false,
        ttsSupported: false,
        micAvailable: false,
        micDevices: [],
        browserVoices: [],
        sapiVoices: [],
        isListening: false,
        isWakeArmed: false,
        isSpeaking: false,
        liveTranscript: "",
        audioLevel: 0,
        lastError: "",
      },

      // Persistent collections
      tasks: [],
      memories: [],
      activity: [],
      diagnosticNotice: "",
    };
  }

  subscribe(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  set(patch) {
    Object.assign(this.state, patch);
    this._emit();
  }

  setVoice(patch) {
    this.state.voice = { ...this.state.voice, ...patch };
    this._emit();
  }

  _emit() {
    for (const fn of this._listeners) {
      try {
        fn(this.state);
      } catch (err) {
        console.error("Store listener error:", err);
      }
    }
  }
}

export async function apiRequest(path, options = {}) {
  const opts = {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
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
