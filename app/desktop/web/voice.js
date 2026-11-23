/**
 * Real Voice Controller for RYNA (Mic RMS meter, SpeechRecognition STT, "Hey Ryna" wake word,
 * Dual Browser/Windows-SAPI TTS, and immediate barge-in interruption).
 */

function cleanSpeech(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " Code omitted. ")
    .replace(/[`*#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export class VoiceClient {
  constructor(hooks) {
    this.hooks = hooks; // { api, onState, onLevel, onTranscript, onFinal, onError, getSettings }
    this.SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    this.sttSupported = !!this.SpeechRec;
    this.ttsSupported = "speechSynthesis" in window;
    this.micDevices = [];
    this.browserVoices = [];
    this.isListening = false;
    this.isSpeaking = false;
    this.isWakeArmed = false;

    this._rec = null;
    this._wakeRec = null;
    this._stream = null;
    this._ctx = null;
    this._analyser = null;
    this._raf = null;
    this._utter = null;
  }

  async init() {
    if (navigator.mediaDevices?.enumerateDevices) {
      try {
        const devs = await navigator.mediaDevices.enumerateDevices();
        this.micDevices = devs.filter((d) => d.kind === "audioinput").map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
      } catch {
        this.micDevices = [];
      }
    }
    if (this.ttsSupported) {
      const load = () => {
        this.browserVoices = window.speechSynthesis.getVoices().map((v) => ({ name: v.name, lang: v.lang }));
      };
      load();
      window.speechSynthesis.onvoiceschanged = load;
    }
  }

  async _startMeter() {
    this._stopMeter();
    if (!navigator.mediaDevices?.getUserMedia) return;
    const micId = this.hooks.getSettings()["voice.microphone"];
    const constraints = micId && micId !== "default" ? { deviceId: { ideal: micId }, echoCancellation: true, noiseSuppression: true } : { echoCancellation: true, noiseSuppression: true };
    this._stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    const AC = window.AudioContext || window.webkitAudioContext;
    this._ctx = new AC();
    const src = this._ctx.createMediaStreamSource(this._stream);
    this._analyser = this._ctx.createAnalyser();
    this._analyser.fftSize = 256;
    src.connect(this._analyser);
    const buf = new Uint8Array(this._analyser.fftSize);
    const tick = () => {
      if (!this._analyser) return;
      this._analyser.getByteTimeDomainData(buf);
      let s = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        s += v * v;
      }
      this.hooks.onLevel(Math.min(1, Math.sqrt(s / buf.length) * 3.0));
      this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  }

  _stopMeter() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    if (this._ctx) { this._ctx.close().catch(() => {}); this._ctx = null; }
    this._analyser = null;
    if (this._stream) { this._stream.getTracks().forEach((t) => t.stop()); this._stream = null; }
    this.hooks.onLevel(0);
  }

  async toggleListen() {
    if (this.isSpeaking) {
      await this.interrupt("User barge-in via microphone");
      await this.startListening();
      return;
    }
    if (this.isListening) this.stopListening();
    else await this.startListening();
  }

  async startListening() {
    if (!this.SpeechRec) {
      this.hooks.onError("SpeechRecognition is unavailable in this browser. Open RYNA in Edge or Chrome on Windows for voice input.");
      return;
    }
    this._pauseWake();
    try {
      await this._startMeter();
    } catch (err) {
      this.hooks.onError(`Microphone access failed: ${err.message || err.name}`);
      return;
    }

    const rec = new this.SpeechRec();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let finalBuf = "";

    rec.onstart = () => {
      this.isListening = true;
      this.hooks.onState("listening", "Listening… speak naturally.");
      this.hooks.api("/api/v1/voice/event", { method: "POST", body: { event_type: "voice_listen", summary: "Microphone listening.", state: "listening" } }).catch(() => {});
    };

    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalBuf += t + " ";
        else interim += t;
      }
      this.hooks.onTranscript((finalBuf + interim).trim());
    };

    rec.onerror = (ev) => {
      this._stopMeter();
      this.isListening = false;
      if (ev.error === "aborted" || ev.error === "no-speech") {
        this.hooks.onState("idle", "Ready.");
        this._resumeWake();
        return;
      }
      this.hooks.onError(
        ev.error === "not-allowed"
          ? "Microphone permission denied. Allow microphone for 127.0.0.1 in browser settings."
          : `Speech recognition error: ${ev.error}`
      );
    };

    rec.onend = () => {
      this._rec = null;
      this._stopMeter();
      this.isListening = false;
      const out = finalBuf.trim();
      this.hooks.onTranscript("");
      if (out) {
        this.hooks.api("/api/v1/voice/event", { method: "POST", body: { event_type: "voice_transcript", summary: `Transcribed: "${out.slice(0, 90)}"`, status: "ok" } }).catch(() => {});
        this.hooks.onFinal(out);
      } else {
        this.hooks.onState("idle", "Ready.");
        this._resumeWake();
      }
    };

    this._rec = rec;
    rec.start();
  }

  stopListening() {
    if (this._rec) { try { this._rec.stop(); } catch {} }
    this._stopMeter();
    this.isListening = false;
    this.hooks.onTranscript("");
    this.hooks.onState("idle", "Ready.");
    this._resumeWake();
  }

  toggleWake() {
    if (!this.SpeechRec) {
      this.hooks.onError("Wake-word listening requires SpeechRecognition (Edge/Chrome).");
      return;
    }
    this.isWakeArmed = !this.isWakeArmed;
    if (this.isWakeArmed) this._startWake();
    else this._pauseWake();
  }

  _pauseWake() {
    if (this._wakeRec) {
      try { this._wakeRec.onend = null; this._wakeRec.stop(); } catch {}
      this._wakeRec = null;
    }
  }

  _resumeWake() {
    if (this.isWakeArmed && !this.isListening && !this.isSpeaking) {
      setTimeout(() => this._startWake(), 350);
    }
  }

  _startWake() {
    if (!this.isWakeArmed || !this.SpeechRec || this._wakeRec) return;
    const phrase = String(this.hooks.getSettings()["voice.wake_word"] || "Hey Ryna").toLowerCase().trim();
    const rec = new this.SpeechRec();
    rec.lang = "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const txt = ev.results[i][0].transcript.toLowerCase();
        const hit = [phrase, "hey ryna", "hey rina", "hey reina"].find((p) => p && txt.includes(p));
        if (hit) {
          const rest = txt.slice(txt.indexOf(hit) + hit.length).trim();
          this._pauseWake();
          if (rest.length > 2 && ev.results[i].isFinal) this.hooks.onFinal(rest);
          else this.startListening();
          return;
        }
      }
    };
    rec.onend = () => { this._wakeRec = null; this._resumeWake(); };
    this._wakeRec = rec;
    try { rec.start(); } catch { this._wakeRec = null; }
  }

  async speak(text) {
    const cleaned = cleanSpeech(text);
    if (!cleaned) return;
    this._pauseWake();
    const s = this.hooks.getSettings();
    const engine = s["voice.tts_engine"] || "browser";
    const speed = Number(s["voice.speech_speed"] ?? 1.0);
    const chosen = s["voice.selected_voice"] || "Microsoft Zira Desktop";

    if (engine === "windows-sapi") {
      try {
        this.isSpeaking = true;
        this.hooks.onState("speaking", "Speaking (Windows SAPI offline)…");
        await this.hooks.api("/api/v1/voice/speak", { method: "POST", body: { text: cleaned, voice: chosen, speed } });
        return;
      } catch (err) {
        this.hooks.onError(`Windows SAPI error: ${err.message}`);
      }
    }

    if (!this.ttsSupported) {
      this.hooks.onError("Text-to-speech unavailable in this browser.");
      return;
    }

    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(cleaned);
    u.rate = Math.max(0.5, Math.min(2.0, speed));
    const voices = window.speechSynthesis.getVoices();
    const match = voices.find((v) => v.name === chosen) || voices.find((v) => v.name.toLowerCase().includes("zira")) || voices[0];
    if (match) u.voice = match;

    u.onstart = () => {
      this.isSpeaking = true;
      this.hooks.onState("speaking", "Speaking…");
      this.hooks.api("/api/v1/voice/event", { method: "POST", body: { event_type: "voice_speak", summary: `Speaking via ${match ? match.name : "browser TTS"}.`, status: "ok", state: "speaking" } }).catch(() => {});
    };
    u.onend = () => {
      if (this._utter !== u) return;
      this._utter = null;
      this.isSpeaking = false;
      this.hooks.onState("idle", "Ready.");
      this.hooks.api("/api/v1/voice/state", { method: "POST", body: { state: "idle" } }).catch(() => {});
      if (s["voice.mode"] === "continuous" && s["voice.enabled"]) setTimeout(() => this.startListening(), 280);
      else this._resumeWake();
    };
    this._utter = u;
    window.speechSynthesis.speak(u);
  }

  async interrupt(reason = "User interrupted") {
    this._utter = null;
    if (this.ttsSupported) window.speechSynthesis.cancel();
    this.isSpeaking = false;
    await this.hooks.api("/api/v1/voice/interrupt", { method: "POST", body: { reason } }).catch(() => {});
    this.hooks.onState("interrupted", "Interrupted — ready.");
  }
}
