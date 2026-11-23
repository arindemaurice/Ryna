/**
 * Real Voice Controller for RYNA 0.2.0.
 *
 * Implements:
 * - Real microphone enumeration & Web Audio AnalyserNode live RMS waveform level
 * - Real SpeechRecognition (interim + final transcripts, Push-to-Talk, Wake Word "Hey Ryna", Continuous mode)
 * - Real Text-to-Speech via Browser speechSynthesis (instant, word-boundary events) OR Windows native SAPI backend
 * - Instant barge-in interruption (halts playback immediately and transitions to listening)
 * - Honest capability & error diagnostics (never pretends voice works when unsupported or blocked)
 */

import { apiRequest } from "./store.js";

function cleanTextForSpeech(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " Code block omitted. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

export class VoiceController {
  constructor(store, companion, onFinalTranscript) {
    this.store = store;
    this.companion = companion;
    this.onFinalTranscript = onFinalTranscript;

    this._SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    this._recognition = null;
    this._wakeRecognition = null;
    this._mediaStream = null;
    this._audioCtx = null;
    this._analyser = null;
    this._meterRaf = null;
    this._activeUtterance = null;
  }

  async init() {
    const sttSupported = !!this._SpeechRec;
    const ttsSupported = "speechSynthesis" in window;

    let micDevices = [];
    let micAvailable = false;
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        micDevices = devices
          .filter((d) => d.kind === "audioinput")
          .map((d, idx) => ({
            deviceId: d.deviceId,
            label: d.label || `Microphone ${idx + 1}`,
          }));
        micAvailable = micDevices.length > 0;
      } catch {
        micAvailable = false;
      }
    }

    const loadVoices = () => {
      if (!ttsSupported) return;
      const voices = window.speechSynthesis.getVoices().map((v) => ({
        name: v.name,
        lang: v.lang,
        localService: v.localService,
      }));
      this.store.setVoice({ browserVoices: voices });
    };

    if (ttsSupported) {
      loadVoices();
      window.speechSynthesis.onvoiceschanged = loadVoices;
    }

    this.store.setVoice({
      sttSupported,
      ttsSupported,
      micAvailable,
      micDevices,
    });
  }

  async _startMicMeter() {
    this._stopMicMeter();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    try {
      const selectedMic = this.store.state.settings["voice.microphone"];
      const audioConstraints =
        selectedMic && selectedMic !== "default"
          ? { deviceId: { ideal: selectedMic }, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
          : { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

      this._mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
      // Refresh real microphone labels now that permission was granted
      const devices = await navigator.mediaDevices.enumerateDevices();
      const micDevices = devices
        .filter((d) => d.kind === "audioinput")
        .map((d, idx) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${idx + 1}` }));
      this.store.setVoice({ micAvailable: micDevices.length > 0, micDevices });

      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this._audioCtx = new AudioCtx();
      const source = this._audioCtx.createMediaStreamSource(this._mediaStream);
      this._analyser = this._audioCtx.createAnalyser();
      this._analyser.fftSize = 256;
      source.connect(this._analyser);

      const buf = new Uint8Array(this._analyser.fftSize);
      const tick = () => {
        if (!this._analyser) return;
        this._analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        this.store.setVoice({ audioLevel: rms });
        this._meterRaf = requestAnimationFrame(tick);
      };
      this._meterRaf = requestAnimationFrame(tick);
    } catch (err) {
      this._reportVoiceError(`Microphone access failed: ${err.message || err.name}`);
      throw err;
    }
  }

  _stopMicMeter() {
    if (this._meterRaf) cancelAnimationFrame(this._meterRaf);
    this._meterRaf = null;
    if (this._audioCtx) {
      this._audioCtx.close().catch(() => {});
      this._audioCtx = null;
    }
    this._analyser = null;
    if (this._mediaStream) {
      for (const track of this._mediaStream.getTracks()) track.stop();
      this._mediaStream = null;
    }
    this.store.setVoice({ audioLevel: 0 });
  }

  async _logVoiceEvent(eventType, summary, status = "ok", state = null, details = {}) {
    try {
      await apiRequest("/api/v1/voice/event", {
        method: "POST",
        body: { event_type: eventType, summary, status, state, details },
      });
    } catch {
      // Non-fatal logging failure
    }
  }

  _reportVoiceError(message) {
    this.store.setVoice({ lastError: message, isListening: false });
    this.store.set({ diagnosticNotice: message, companionState: "error", companionSubtext: message });
    this._logVoiceEvent("voice_error", message, "error", "error");
  }

  async toggleListening() {
    // Barge-in: if RYNA is speaking right now, interrupt immediately and begin listening!
    if (this.store.state.voice.isSpeaking) {
      await this.interrupt("User activated microphone during speech");
      await this.startListening();
      return;
    }
    if (this.store.state.voice.isListening) {
      this.stopListening();
    } else {
      await this.startListening();
    }
  }

  async startListening() {
    if (!this._SpeechRec) {
      this._reportVoiceError(
        "Browser SpeechRecognition API is unavailable in this browser. Open RYNA in Microsoft Edge or Google Chrome on Windows for live speech-to-text, or type your message."
      );
      return;
    }

    // Pause wake-word listener while active command listening runs
    this._pauseWakeListener();

    try {
      await this._startMicMeter();
    } catch {
      return;
    }

    const rec = new this._SpeechRec();
    rec.lang = this.store.state.settings["general.language"] === "en" ? "en-US" : (this.store.state.settings["general.language"] || "en-US");
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;

    let finalText = "";

    rec.onstart = () => {
      this.store.setVoice({ isListening: true, liveTranscript: "", lastError: "" });
      this.store.set({
        companionState: "listening",
        companionSubtext: "Listening… speak naturally.",
        diagnosticNotice: "",
      });
      this._logVoiceEvent("voice_listen", "Microphone activated; listening for speech.", "info", "listening");
    };

    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const txt = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalText += txt + " ";
        } else {
          interim += txt;
        }
      }
      const preview = (finalText + interim).trim();
      this.store.setVoice({ liveTranscript: preview });
    };

    rec.onerror = (event) => {
      const errCode = event.error || "unknown";
      this._stopMicMeter();
      this.store.setVoice({ isListening: false });
      if (errCode === "aborted") {
        this.store.set({ companionState: "idle", companionSubtext: "Listening cancelled." });
        return;
      }
      if (errCode === "no-speech") {
        this.store.set({ companionState: "idle", companionSubtext: "No speech detected. Click mic to try again." });
        this._resumeWakeIfArmed();
        return;
      }
      const friendly =
        errCode === "not-allowed"
          ? "Microphone permission was denied by the browser. Allow microphone access for 127.0.0.1 in browser site settings."
          : errCode === "network"
          ? "Browser speech recognition requires network access in Chromium/Edge; offline local STT (faster-whisper) is not installed yet."
          : `Speech recognition error: ${errCode}`;
      this._reportVoiceError(friendly);
    };

    rec.onend = () => {
      this._recognition = null;
      this._stopMicMeter();
      this.store.setVoice({ isListening: false });
      const transcript = finalText.trim() || this.store.state.voice.liveTranscript.trim();
      this.store.setVoice({ liveTranscript: "" });

      if (transcript) {
        this._logVoiceEvent("voice_transcript", `Transcribed: "${transcript.slice(0, 100)}"`, "ok");
        this.onFinalTranscript(transcript, { fromVoice: true });
      } else {
        if (this.store.state.companionState === "listening") {
          this.store.set({ companionState: "idle", companionSubtext: "Ready when you are." });
        }
        this._resumeWakeIfArmed();
      }
    };

    this._recognition = rec;
    try {
      rec.start();
    } catch (err) {
      this._stopMicMeter();
      this._reportVoiceError(`Could not start speech recognition: ${err.message}`);
    }
  }

  stopListening() {
    if (this._recognition) {
      try {
        this._recognition.stop();
      } catch {
        // ignore
      }
    }
    this._stopMicMeter();
    this.store.setVoice({ isListening: false, liveTranscript: "" });
    if (this.store.state.companionState === "listening") {
      this.store.set({ companionState: "idle", companionSubtext: "Ready when you are." });
    }
    this._resumeWakeIfArmed();
  }

  /**
   * Real Wake Word ("Hey Ryna") continuous phrase monitor via Web Speech API.
   */
  toggleWakeWord() {
    if (this.store.state.voice.isWakeArmed) {
      this.disarmWakeWord();
    } else {
      this.armWakeWord();
    }
  }

  armWakeWord() {
    if (!this._SpeechRec) {
      this._reportVoiceError("Wake word listening requires SpeechRecognition support (Edge/Chrome).");
      return;
    }
    this.store.setVoice({ isWakeArmed: true });
    this._startWakeListener();
    const wakePhrase = this.store.state.settings["voice.wake_word"] || "Hey Ryna";
    this._logVoiceEvent("wake_word_armed", `Wake word listener armed for "${wakePhrase}".`, "info");
  }

  disarmWakeWord() {
    this.store.setVoice({ isWakeArmed: false });
    this._pauseWakeListener();
    this._logVoiceEvent("wake_word_disarmed", "Wake word listener disabled.", "info");
  }

  _pauseWakeListener() {
    if (this._wakeRecognition) {
      try {
        this._wakeRecognition.onend = null;
        this._wakeRecognition.stop();
      } catch {
        // ignore
      }
      this._wakeRecognition = null;
    }
  }

  _resumeWakeIfArmed() {
    if (
      this.store.state.voice.isWakeArmed &&
      !this.store.state.voice.isListening &&
      !this.store.state.voice.isSpeaking &&
      !this.store.state.isGenerating
    ) {
      setTimeout(() => this._startWakeListener(), 350);
    }
  }

  _startWakeListener() {
    if (!this._SpeechRec || !this.store.state.voice.isWakeArmed || this._wakeRecognition) return;
    const wakePhrase = String(this.store.state.settings["voice.wake_word"] || "Hey Ryna").toLowerCase().trim();

    const rec = new this._SpeechRec();
    rec.lang = "en-US";
    rec.continuous = true;
    rec.interimResults = true;

    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const spoken = event.results[i][0].transcript.toLowerCase().trim();
        // Match configured wake phrase or common phonetic variants ("hey rina", "hey reina")
        const patterns = [wakePhrase, "hey rina", "hey reina", "hey ryna"];
        const matched = patterns.find((p) => p && spoken.includes(p));
        if (matched) {
          const idx = spoken.indexOf(matched);
          const remainder = spoken.slice(idx + matched.length).trim();
          this._pauseWakeListener();
          this._logVoiceEvent("wake_word_triggered", `Detected wake phrase "${matched}".`, "ok");
          if (remainder.length > 2 && event.results[i].isFinal) {
            this.onFinalTranscript(remainder, { fromVoice: true });
          } else {
            this.startListening();
          }
          return;
        }
      }
    };

    rec.onerror = (e) => {
      if (e.error === "not-allowed") {
        this.disarmWakeWord();
        this._reportVoiceError("Wake word disabled: microphone permission denied.");
      }
    };

    rec.onend = () => {
      this._wakeRecognition = null;
      this._resumeWakeIfArmed();
    };

    this._wakeRecognition = rec;
    try {
      rec.start();
    } catch {
      this._wakeRecognition = null;
    }
  }

  /**
   * Speak RYNA's response aloud using either Browser SpeechSynthesis or Windows native offline SAPI.
   */
  async speak(text, { onComplete } = {}) {
    const cleaned = cleanTextForSpeech(text);
    if (!cleaned) return;

    this._pauseWakeListener();
    const s = this.store.state.settings;
    const engine = s["voice.tts_engine"] || "browser";
    const speed = Number(s["voice.speech_speed"] ?? 1.0);
    const chosenVoice = s["voice.selected_voice"] || "Microsoft Zira Desktop";

    // Option 1: Offline Windows native SAPI backend
    if (engine === "windows-sapi") {
      try {
        this.store.setVoice({ isSpeaking: true });
        this.store.set({ companionState: "speaking", companionSubtext: "Speaking (Windows SAPI offline)…" });
        await apiRequest("/api/v1/voice/speak", {
          method: "POST",
          body: { text: cleaned, voice: chosenVoice, speed },
        });
        return;
      } catch (err) {
        this._reportVoiceError(`Windows SAPI speech failed: ${err.message}. Falling back to browser speech.`);
      }
    }

    // Option 2: Browser SpeechSynthesis (instant + word-boundary animation sync)
    if (!("speechSynthesis" in window)) {
      this._reportVoiceError("Text-to-speech is unavailable in this browser and Windows SAPI is not enabled.");
      return;
    }

    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(cleaned);
    utter.rate = Math.max(0.5, Math.min(2.0, speed));

    const voices = window.speechSynthesis.getVoices();
    const match =
      voices.find((v) => v.name === chosenVoice) ||
      voices.find((v) => v.name.toLowerCase().includes("zira") || v.name.toLowerCase().includes("aria")) ||
      voices.find((v) => v.lang && v.lang.toLowerCase().startsWith("en")) ||
      voices[0];
    if (match) utter.voice = match;

    utter.onstart = () => {
      this.store.setVoice({ isSpeaking: true });
      this.store.set({ companionState: "speaking", companionSubtext: "Speaking…" });
      this._logVoiceEvent("voice_speak", `Speaking response (${match ? match.name : "system voice"}).`, "ok", "speaking");
    };

    utter.onboundary = () => {
      this.companion.pulseSpeech();
    };

    utter.onend = () => {
      if (this._activeUtterance !== utter) return;
      this._activeUtterance = null;
      this.store.setVoice({ isSpeaking: false });
      this.store.set({ companionState: "idle", companionSubtext: "Ready when you are." });
      apiRequest("/api/v1/voice/state", { method: "POST", body: { state: "idle" } }).catch(() => {});
      if (typeof onComplete === "function") onComplete();

      // Natural continuous turn-taking if user selected continuous voice mode
      if (this.store.state.settings["voice.mode"] === "continuous" && this.store.state.settings["voice.enabled"]) {
        setTimeout(() => this.startListening(), 280);
      } else {
        this._resumeWakeIfArmed();
      }
    };

    utter.onerror = (ev) => {
      if (ev.error === "interrupted" || ev.error === "canceled") return;
      this._activeUtterance = null;
      this.store.setVoice({ isSpeaking: false });
      this._reportVoiceError(`Speech playback failed: ${ev.error}`);
    };

    this._activeUtterance = utter;
    window.speechSynthesis.speak(utter);
  }

  /**
   * Immediate barge-in interruption: stops browser TTS, stops native SAPI subprocess, and notifies engine.
   */
  async interrupt(reason = "User interrupted speech") {
    this._activeUtterance = null;
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    this.store.setVoice({ isSpeaking: false });
    try {
      await apiRequest("/api/v1/voice/interrupt", {
        method: "POST",
        body: { reason },
      });
    } catch {
      // ignore if engine unreachable
    }
    this.store.set({ companionState: "idle", companionSubtext: "Interrupted — ready." });
  }
}
