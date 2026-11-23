/**
 * Real-time event-driven RYNA Companion Canvas renderer.
 * Responds to actual application states and live microphone audio amplitude / TTS speaking cadence.
 */

const STATE_PALETTE = {
  idle:       { core: "#38bdf8", ring: "#0284c7", glow: "rgba(56,189,248,0.22)",  speed: 0.018 },
  listening:  { core: "#22d3ee", ring: "#38bdf8", glow: "rgba(34,211,238,0.34)",  speed: 0.055 },
  processing: { core: "#818cf8", ring: "#38bdf8", glow: "rgba(129,140,248,0.32)", speed: 0.085 },
  speaking:   { core: "#34d399", ring: "#22d3ee", glow: "rgba(52,211,153,0.32)",  speed: 0.075 },
  executing:  { core: "#60a5fa", ring: "#818cf8", glow: "rgba(96,165,250,0.30)",  speed: 0.095 },
  waiting:    { core: "#fbbf24", ring: "#f59e0b", glow: "rgba(251,191,36,0.28)",  speed: 0.032 },
  offline:    { core: "#64748b", ring: "#475569", glow: "rgba(100,116,139,0.16)", speed: 0.010 },
  error:      { core: "#f87171", ring: "#ef4444", glow: "rgba(248,113,113,0.30)", speed: 0.065 },
};

export class CompanionRenderer {
  constructor(store, canvasIds) {
    this.store = store;
    this.canvasIds = canvasIds;
    this.phase = 0;
    this.speakEnergy = 0;
    this._raf = null;
  }

  /** Called on real SpeechSynthesis word boundaries to drive organic speaking cadence. */
  pulseSpeech() {
    this.speakEnergy = 1.0;
  }

  start() {
    if (this._raf) return;
    const loop = () => {
      this._renderFrame();
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _renderFrame() {
    const st = this.store.state;
    const mode = st.companionState || "idle";
    const pal = STATE_PALETTE[mode] || STATE_PALETTE.idle;
    const reduced = !!st.settings["appearance.reduced_animations"];

    this.phase += reduced ? pal.speed * 0.25 : pal.speed;
    this.speakEnergy = Math.max(0, this.speakEnergy * 0.91);

    const micLevel = st.voice.isListening ? Math.min(1, st.voice.audioLevel * 2.4) : 0;
    const speakLevel = mode === "speaking" ? Math.max(0.28 + Math.abs(Math.sin(this.phase * 2.6)) * 0.42, this.speakEnergy) : 0;

    for (const id of this.canvasIds) {
      const canvas = document.getElementById(id);
      if (!canvas || canvas.offsetParent === null) continue;
      this._draw(canvas, mode, pal, reduced, micLevel, speakLevel);
    }
  }

  _draw(canvas, mode, pal, reduced, micLevel, speakLevel) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const baseR = w * 0.21;

    ctx.clearRect(0, 0, w, h);

    // Subtle ambient halo
    const haloR = baseR * (2.15 + micLevel * 0.35 + speakLevel * 0.25);
    const grad = ctx.createRadialGradient(cx, cy, baseR * 0.2, cx, cy, haloR);
    grad.addColorStop(0, pal.glow);
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, haloR, 0, Math.PI * 2);
    ctx.fill();

    // Listening: live microphone radial waveform ring
    if (mode === "listening") {
      const bars = 32;
      ctx.strokeStyle = pal.core;
      ctx.lineWidth = 2;
      for (let i = 0; i < bars; i++) {
        const angle = (i / bars) * Math.PI * 2 + this.phase * 0.35;
        const mod = reduced ? 0.2 : (0.18 + micLevel * 0.95) * (0.65 + 0.35 * Math.sin(i * 1.9 + this.phase * 4));
        const r1 = baseR * 1.25;
        const r2 = r1 + baseR * 0.55 * mod;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(angle) * r1, cy + Math.sin(angle) * r1);
        ctx.lineTo(cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
        ctx.stroke();
      }
    }

    // Processing / Executing: crisp kinetic orbital arcs
    if (mode === "processing" || mode === "executing") {
      ctx.strokeStyle = pal.core;
      ctx.lineWidth = 2.6;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 1.48, this.phase * 1.4, this.phase * 1.4 + Math.PI * 0.72);
      ctx.stroke();

      ctx.strokeStyle = pal.ring;
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 1.72, -this.phase * 1.1, -this.phase * 1.1 + Math.PI * 0.55);
      ctx.stroke();
    }

    // Speaking: acoustic concentric wave pulses
    if (mode === "speaking") {
      for (let i = 0; i < 2; i++) {
        const wave = ((this.phase * 0.65 + i * 0.5) % 1);
        const r = baseR * (1.15 + wave * (0.95 + speakLevel * 0.35));
        ctx.strokeStyle = pal.core;
        ctx.globalAlpha = (1 - wave) * 0.55;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Idle / Waiting / Offline / Error outer geometric ring
    if (mode === "idle" || mode === "waiting" || mode === "offline" || mode === "error") {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(this.phase * 0.25);
      ctx.strokeStyle = pal.ring;
      ctx.globalAlpha = mode === "offline" ? 0.45 : 0.7;
      ctx.lineWidth = 1.5;
      if (mode === "waiting" || mode === "offline") ctx.setLineDash([6, 6]);
      ctx.beginPath();
      ctx.arc(0, 0, baseR * 1.42, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      ctx.globalAlpha = 1;
    }

    // Core breathing sphere
    const breathe = reduced ? 0 : Math.sin(this.phase * 1.5) * 2.5 + micLevel * 6 + speakLevel * 5;
    const coreR = baseR + breathe * 0.45;
    ctx.fillStyle = "#0b1322";
    ctx.strokeStyle = pal.core;
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Inner nucleus
    const innerR = coreR * (0.42 + speakLevel * 0.14 + micLevel * 0.14);
    ctx.fillStyle = pal.core;
    ctx.beginPath();
    ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
    ctx.fill();
  }
}
