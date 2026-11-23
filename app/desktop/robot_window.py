"""RYNA Pure Walking Desktop Robot (Windows Native).

NO website. NO bloated chat application window. ONLY RYNA living directly on your desktop:
- Transparent isolated RYNA robot (`ryna_robot.png`) that ACTUALLY WALKS smoothly across your
  desktop with directional left/right stride animation, subtle tilt/step bounce, and glowing
  cyan speed trails beneath her feet.
- Real two-way voice conversation: click RYNA (or double-click / tray / Continuous mode) to
  speak via your Windows mic (`sounddevice` + `faster-whisper` 100% offline), stream responses
  from warm `qwen2.5:1.5b` (or ultra-fast Groq/Cloud), and hear RYNA speak back sentence-by-sentence
  immediately via the persistent warm Windows SAPI speaker (<20ms start, <15ms barge-in!).
- Compact glowing cyan speech bubble right above RYNA that appears while listening/speaking
  and fades away cleanly when idle so your desktop stays uncluttered.
- Native Windows System Tray icon (`pystray`) + right-click quick controls.
"""

from __future__ import annotations

import math
import random
import re
import threading
import time
import tkinter as tk
from tkinter import simpledialog
from typing import Any

from PIL import Image, ImageTk

from ai.orchestration.core import RynaCore

TRANSPARENT_KEY = "#010203"
BG_BUBBLE = "#07101f"
CYAN = "#22d3ee"
CYAN_SOFT = "#38bdf8"
TEXT_MAIN = "#eef5ff"

STATE_COLORS: dict[str, str] = {
    "idle": "#22d3ee",
    "listening": "#38bdf8",
    "thinking": "#818cf8",
    "speaking": "#34d399",
    "working": "#60a5fa",
    "waiting": "#fbbf24",
    "curious": "#38bdf8",
    "happy": "#34d399",
    "confused": "#a78bfa",
    "concerned": "#fbbf24",
    "sleeping": "#64748b",
    "interrupted": "#fb923c",
    "error": "#f87171",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class RynaRobotDesktop:
    """Pure walking transparent desktop robot with instant streaming voice & floating bubble."""

    def __init__(self, core: RynaCore) -> None:
        self.core = core
        self.assets_dir = core.config.project_root / "app" / "desktop" / "assets"
        self.robot_png = self.assets_dir / "ryna_robot.png"
        self.head_png = self.assets_dir / "ryna_head.png"

        s = core.db.get_all_settings()
        self.robot_size = max(120, min(260, int(s.get("desktop.robot_size", 170))))
        self.alpha = float(s.get("desktop.robot_transparency", 0.98))
        self.always_on_top = bool(s.get("desktop.robot_always_on_top", True))
        self.walk_enabled = bool(s.get("desktop.robot_walk_enabled", True))
        self.continuous_voice = str(s.get("voice.mode", "push_to_talk")) == "continuous"

        # Runtime animation & walking physics
        self.running = True
        self.state = "idle"
        self.phase = 0.0
        self.step_phase = 0.0
        self.mic_level = 0.0
        self.facing = 1  # +1 = right, -1 = left
        self.walk_speed = 2.6
        self.patrol_mode = "walk"  # "walk" | "pause"
        self.patrol_until = time.monotonic() + random.uniform(4.0, 8.0)

        self.is_listening = False
        self.is_generating = False
        self.active_conv_id: str | None = None
        self._bubble_hide_at = time.monotonic() + 7.0
        self._tray_icon: Any = None
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False
        self._dragging_now = False

        # --- 1. Root Transparent Robot Window ---
        self.root = tk.Tk()
        self.root.title("RYNA")
        self.root.overrideredirect(True)
        self.root.configure(bg=TRANSPARENT_KEY)
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", self.alpha)

        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        self.pos_x = float(int(s.get("desktop.robot_x", -1)))
        self.pos_y = float(int(s.get("desktop.robot_y", -1)))
        if not (0 <= self.pos_x <= self.sw - self.robot_size):
            self.pos_x = float(self.sw - self.robot_size - 120)
        if not (0 <= self.pos_y <= self.sh - self.robot_size - 48):
            self.pos_y = float(self.sh - self.robot_size - 72)

        self.win_w = self.robot_size + 44  # room for speed trails behind heels
        self.win_h = self.robot_size + 26
        self.root.geometry(f"{self.win_w}x{self.win_h}+{int(self.pos_x)}+{int(self.pos_y)}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.win_w,
            height=self.win_h,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

        # Pre-baked directional stride frames (right & left + subtle stride tilt)
        self._frames_right: list[ImageTk.PhotoImage] = []
        self._frames_left: list[ImageTk.PhotoImage] = []
        self._load_stride_frames()
        self._build_context_menu()
        self._bind_events()

        # --- 2. Compact Floating Cyan Speech Bubble Above RYNA ---
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.configure(bg=CYAN)
        self.bubble.attributes("-topmost", True)
        self.bubble.attributes("-alpha", 0.94)
        self._bubble_frame = tk.Frame(self.bubble, bg=BG_BUBBLE, padx=11, pady=7)
        self._bubble_frame.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(self._bubble_frame, bg=BG_BUBBLE)
        top.pack(fill="x")
        self._wave_lbl = tk.Label(top, text="●", fg=CYAN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold"))
        self._wave_lbl.pack(side="left", padx=(0, 5))
        tk.Label(top, text="RYNA", fg=TEXT_MAIN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._state_lbl = tk.Label(top, text="READY", fg=CYAN_SOFT, bg=BG_BUBBLE, font=("Segoe UI", 8))
        self._state_lbl.pack(side="right")

        self._bubble_var = tk.StringVar(value="I'm here! Click me to speak — right-click for options.")
        tk.Label(
            self._bubble_frame,
            textvariable=self._bubble_var,
            fg="#dbeafe",
            bg=BG_BUBBLE,
            wraplength=240,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(3, 0))
        self.bubble.bind("<Button-1>", lambda _e: self.interrupt_now() if self.state == "speaking" else self.toggle_mic())
        self._position_bubble()

        # --- 3. Tray & Background Warm-Up ---
        self._start_tray()
        self.core.warm_up_async()

        # Speak a brief warm greeting once SAPI is ready (async, non-blocking)
        def _greet() -> None:
            time.sleep(0.9)
            if self.running:
                try:
                    self.core.voice.speak_native("I'm here. Click me anytime to talk.")
                except Exception:
                    pass

        threading.Thread(target=_greet, daemon=True).start()
        self._tick()

    def _load_stride_frames(self) -> None:
        self._frames_right.clear()
        self._frames_left.clear()
        if not self.robot_png.is_file():
            return
        base = Image.open(self.robot_png).convert("RGBA")
        base.thumbnail((self.robot_size - 14, self.robot_size - 14), Image.Resampling.LANCZOS)
        flipped = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        # 6 smooth stride tilt angles for lifelike walking motion
        angles = (-4.5, -2.0, 0.5, 3.0, 1.0, -1.5)
        for deg in angles:
            r_rot = base.rotate(deg, resample=Image.Resampling.BICUBIC, expand=True)
            l_rot = flipped.rotate(-deg, resample=Image.Resampling.BICUBIC, expand=True)
            self._frames_right.append(ImageTk.PhotoImage(r_rot))
            self._frames_left.append(ImageTk.PhotoImage(l_rot))

    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg=BG_BUBBLE, fg=TEXT_MAIN, activebackground="#162b4a")
        self.menu.add_command(label="🎤 Speak to RYNA", command=self.toggle_mic)
        self.menu.add_command(label="■ Interrupt Speaking", command=self.interrupt_now)
        self.menu.add_command(label="⌨ Quick Type Command…", command=self._quick_type_prompt)
        self.menu.add_separator()
        self.menu.add_command(label="🚶 Toggle Walking / Stay Put", command=self.toggle_walking)
        self.menu.add_command(label="🔁 Toggle Continuous Voice", command=self.toggle_continuous_voice)

        size_m = tk.Menu(self.menu, tearoff=0, bg=BG_BUBBLE, fg=TEXT_MAIN)
        for lbl, px in (("Small (135px)", 135), ("Medium (170px)", 170), ("Large (220px)", 220)):
            size_m.add_command(label=lbl, command=lambda p=px: self._resize(p))
        self.menu.add_cascade(label="Robot Size", menu=size_m)

        # Quick local model switcher (fast vs stronger)
        mod_m = tk.Menu(self.menu, tearoff=0, bg=BG_BUBBLE, fg=TEXT_MAIN)
        for m in ("qwen2.5:1.5b", "qwen2.5:3b", "qwen3.5:4b"):
            mod_m.add_command(label=f"Use {m}", command=lambda mn=m: self._select_local_model(mn))
        self.menu.add_cascade(label="Local AI Model", menu=mod_m)

        self.menu.add_command(label="Toggle Always On Top", command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="Hide to System Tray", command=self.hide_to_tray)
        self.menu.add_command(label="Exit RYNA", command=self.quit_app)

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.root.bind("<Escape>", lambda _e: self.interrupt_now())

    def _on_press(self, e: tk.Event) -> None:
        self._drag_x, self._drag_y = e.x, e.y
        self._dragged = False
        self._dragging_now = True

    def _on_drag(self, e: tk.Event) -> None:
        if abs(e.x - self._drag_x) > 4 or abs(e.y - self._drag_y) > 4:
            self._dragged = True
        nx = self.root.winfo_x() + (e.x - self._drag_x)
        ny = self.root.winfo_y() + (e.y - self._drag_y)
        self.pos_x, self.pos_y = float(nx), float(ny)
        self.root.geometry(f"+{nx}+{ny}")
        self._position_bubble()

    def _on_release(self, _e: tk.Event) -> None:
        self._dragging_now = False
        if not self._dragged:
            # Clicking RYNA directly talks to her (or interrupts if she's speaking!)
            self.toggle_mic()
            return
        self.pos_x = float(max(6, min(self.sw - self.win_w - 6, int(self.pos_x))))
        self.pos_y = float(max(6, min(self.sh - self.win_h - 46, int(self.pos_y))))
        self.root.geometry(f"+{int(self.pos_x)}+{int(self.pos_y)}")
        self._position_bubble()
        self.core.db.update_settings({"desktop.robot_x": int(self.pos_x), "desktop.robot_y": int(self.pos_y)})

    def _position_bubble(self) -> None:
        try:
            bx = int(self.pos_x) - 210 if self.pos_x > 240 else int(self.pos_x) + self.win_w + 6
            by = max(12, int(self.pos_y) - 52)
            self.bubble.geometry(f"+{bx}+{by}")
        except Exception:
            pass

    def _show_bubble(self, text: str, duration: float = 8.0) -> None:
        short = text.strip()
        if len(short) > 190:
            short = short[:187] + "…"
        self._bubble_var.set(short)
        self._bubble_hide_at = time.monotonic() + duration
        try:
            self.bubble.deiconify()
            self._position_bubble()
        except Exception:
            pass

    def _resize(self, px: int) -> None:
        self.robot_size = px
        self.win_w = px + 44
        self.win_h = px + 26
        self.root.geometry(f"{self.win_w}x{self.win_h}+{int(self.pos_x)}+{int(self.pos_y)}")
        self.canvas.config(width=self.win_w, height=self.win_h)
        self._load_stride_frames()
        self._position_bubble()
        self.core.db.set_setting("desktop.robot_size", px)

    def _select_local_model(self, model_name: str) -> None:
        self.core.db.set_setting("ai.local_model", model_name)
        self._show_bubble(f"Switched local model to {model_name}. Warming up…", 5.0)
        self.core.warm_up_async()

    def _toggle_topmost(self) -> None:
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.core.db.set_setting("desktop.robot_always_on_top", self.always_on_top)

    def toggle_walking(self) -> None:
        self.walk_enabled = not self.walk_enabled
        self.core.db.set_setting("desktop.robot_walk_enabled", self.walk_enabled)
        self._show_bubble("Walking enabled!" if self.walk_enabled else "Staying right here.", 3.5)

    def toggle_continuous_voice(self) -> None:
        self.continuous_voice = not self.continuous_voice
        self.core.db.set_setting("voice.mode", "continuous" if self.continuous_voice else "push_to_talk")
        self._show_bubble(
            "Continuous voice ON — I'll keep listening after I reply." if self.continuous_voice else "Push-to-talk mode — click me when you want to speak.",
            4.5,
        )

    def _quick_type_prompt(self) -> None:
        txt = simpledialog.askstring("Ask RYNA", "Type a message or command for RYNA:", parent=self.root)
        if txt and txt.strip():
            self.handle_user_input(txt.strip())

    # --- Real Two-Way Streaming Voice Interaction ---
    def toggle_mic(self) -> None:
        # Instant barge-in: if RYNA is speaking right now, cut her off immediately and listen!
        if self.core.voice.get_state() == "speaking" or self.is_generating:
            self.interrupt_now()

        if self.is_listening:
            self.core.voice.recognizer.stop_recording()
            return

        self.is_listening = True
        self._show_bubble("Listening… speak naturally.", duration=18.0)

        def _worker() -> None:
            try:
                transcript = self.core.voice.listen_native(on_level=lambda lvl: setattr(self, "mic_level", lvl))
                self.is_listening = False
                self.mic_level = 0.0
                if transcript:
                    self.core.record_activity("voice", "voice_transcript", f'Heard: "{transcript[:90]}"', "ok")
                    self.root.after(0, lambda t=transcript: self.handle_user_input(t))
                else:
                    self.root.after(0, lambda: self._show_bubble("Didn't hear anything — click me to try again.", 3.5))
            except Exception as exc:
                self.is_listening = False
                self.mic_level = 0.0
                self.root.after(0, lambda e=str(exc): self._show_bubble(f"Mic error: {e}", 6.0))

        threading.Thread(target=_worker, daemon=True).start()

    def handle_user_input(self, text: str) -> None:
        """Stream response from fast Ollama/Cloud/Tools and speak sentence-by-sentence immediately."""
        self.is_generating = True
        self.root.after(0, lambda: self._show_bubble(f'"{text}" — thinking…', 20.0))

        def _worker() -> None:
            sentence_buf = ""
            full_acc = ""
            spoke_any = False

            def _after_spoken() -> None:
                if self.continuous_voice and self.running and not self.core.voice.is_interrupted():
                    self.root.after(280, self.toggle_mic)

            try:
                for ev in self.core.stream_chat(self.active_conv_id, text):
                    if ev["event"] == "meta":
                        self.active_conv_id = ev["data"]["conversation_id"]
                    elif ev["event"] == "delta":
                        delta = ev["data"].get("delta", "")
                        if not delta:
                            continue
                        full_acc += delta
                        sentence_buf += delta
                        self.root.after(0, lambda p=full_acc: self._show_bubble(p, 15.0))

                        # Stream-to-speech: speak each completed sentence immediately while next tokens stream!
                        parts = SENTENCE_SPLIT_RE.split(sentence_buf)
                        if len(parts) > 1:
                            for complete_sent in parts[:-1]:
                                if complete_sent.strip():
                                    self.core.voice.enqueue_sentence(complete_sent.strip())
                                    spoke_any = True
                            sentence_buf = parts[-1]
                    elif ev["event"] in ("done", "error"):
                        msg = ev["data"].get("assistant_message") or {}
                        content = msg.get("content", "")
                        ai_ok = ev["data"].get("ai_available", False)
                        if content:
                            self.root.after(0, lambda c=content: self._show_bubble(c, 10.0))
                        # Speak remaining tail sentence or direct tool response
                        if ai_ok and not self.core.voice.is_interrupted():
                            tail = sentence_buf.strip() if spoke_any else content
                            if tail:
                                self.core.voice.enqueue_sentence(tail, on_all_done=_after_spoken)
                            elif spoke_any:
                                # Attach completion callback if everything already queued
                                self.core.voice._on_all_spoken = _after_spoken
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._show_bubble(f"Error: {e}", 7.0))
            finally:
                self.is_generating = False

        threading.Thread(target=_worker, daemon=True).start()

    def interrupt_now(self) -> None:
        self.core.voice.interrupt("User interrupted RYNA")
        self._show_bubble("Interrupted — go ahead.", 3.5)

    # --- System Tray ---
    def _start_tray(self) -> None:
        def _run() -> None:
            try:
                import pystray

                icon_img = Image.open(self.head_png).convert("RGBA") if self.head_png.is_file() else Image.new("RGBA", (64, 64), (34, 211, 238, 255))
                menu = pystray.Menu(
                    pystray.MenuItem("🎤 Talk to RYNA", lambda: self.root.after(0, self.toggle_mic)),
                    pystray.MenuItem("■ Interrupt", lambda: self.root.after(0, self.interrupt_now)),
                    pystray.MenuItem("🚶 Toggle Walking", lambda: self.root.after(0, self.toggle_walking)),
                    pystray.MenuItem("🔁 Toggle Continuous Voice", lambda: self.root.after(0, self.toggle_continuous_voice)),
                    pystray.MenuItem("Show RYNA", lambda: self.root.after(0, self.show_from_tray)),
                    pystray.MenuItem("Hide to Tray", lambda: self.root.after(0, self.hide_to_tray)),
                    pystray.MenuItem("Exit RYNA", lambda: self.root.after(0, self.quit_app)),
                )
                self._tray_icon = pystray.Icon("RYNA", icon_img, "RYNA — Desktop AI Robot", menu)
                self._tray_icon.run()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def hide_to_tray(self) -> None:
        self.root.withdraw()
        self.bubble.withdraw()

    def show_from_tray(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self._show_bubble("I'm back!", 3.5)

    # --- Main 30fps Walking + Expressive Animation Loop ---
    def _tick(self) -> None:
        if not self.running:
            return
        now = time.monotonic()
        self.state = self.core.voice.get_state()
        accent = STATE_COLORS.get(self.state, CYAN)
        busy = self.state in ("listening", "thinking", "speaking", "working") or self.is_listening or self.is_generating or self._dragging_now

        # Autonomous desktop walking patrol when idle!
        is_walking = False
        if self.walk_enabled and not busy:
            if now >= self.patrol_until:
                if self.patrol_mode == "walk":
                    self.patrol_mode = "pause"
                    self.patrol_until = now + random.uniform(2.5, 5.5)
                else:
                    self.patrol_mode = "walk"
                    if random.random() < 0.35:
                        self.facing *= -1
                    self.patrol_until = now + random.uniform(4.5, 9.5)

            if self.patrol_mode == "walk":
                is_walking = True
                self.pos_x += self.facing * self.walk_speed
                margin = 20
                if self.pos_x <= margin:
                    self.pos_x = float(margin)
                    self.facing = 1
                elif self.pos_x >= self.sw - self.win_w - margin:
                    self.pos_x = float(self.sw - self.win_w - margin)
                    self.facing = -1
                self.root.geometry(f"+{int(self.pos_x)}+{int(self.pos_y)}")
                self._position_bubble()

        # Auto-hide speech bubble cleanly when idle so desktop stays minimal
        if not busy and now > self._bubble_hide_at:
            try:
                if self.bubble.state() != "withdrawn":
                    self.bubble.withdraw()
            except Exception:
                pass

        # Advance stride & breathing animation
        self.phase += 0.14 if busy else 0.06
        if is_walking:
            self.step_phase += 0.28
        else:
            self.step_phase = 0.0

        c = self.canvas
        c.delete("all")
        cx = self.win_w / 2
        foot_y = self.win_h - 12

        # Glowing cyan speed streaks trailing behind heels while walking!
        if is_walking:
            trail_dir = -self.facing
            for i in range(3):
                y_line = foot_y - 4 - i * 8
                x0 = cx + trail_dir * (18 + i * 8)
                x1 = x0 + trail_dir * (26 - i * 5 + math.sin(self.step_phase + i) * 6)
                c.create_line(x0, y_line, x1, y_line, fill=accent, width=2 if i == 0 else 1)

        # Ground energy halo beneath RYNA's feet
        halo_w = self.robot_size * (0.28 + 0.04 * math.sin(self.phase * 1.5) + self.mic_level * 0.10)
        c.create_oval(cx - halo_w, foot_y - 5, cx + halo_w, foot_y + 5, outline=accent, width=2)
        c.create_oval(cx - halo_w * 0.55, foot_y - 2, cx + halo_w * 0.55, foot_y + 2, fill=accent, outline="")

        # Draw directional RYNA robot frame with walking footstep bounce or gentle breathing hover
        frames = self._frames_right if self.facing >= 0 else self._frames_left
        if frames:
            if is_walking:
                idx = int(self.step_phase * 2.2) % len(frames)
                y_offset = -abs(math.sin(self.step_phase)) * 7.5
            else:
                idx = 2  # upright neutral frame
                y_offset = math.sin(self.phase) * (4.0 if busy else 2.2)
            c.create_image(cx, self.win_h * 0.46 + y_offset, image=frames[idx])

        # Update bubble state indicator
        wave = "◉" if self.state == "listening" else ("◈" if self.state == "speaking" else ("🚶" if is_walking else "●"))
        self._wave_lbl.config(text=wave, fg=accent)
        self._state_lbl.config(text=("WALKING" if is_walking else self.state.upper()), fg=accent)

        self.root.after(33, self._tick)

    def quit_app(self) -> None:
        self.running = False
        self.core.voice.interrupt("Exiting RYNA")
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def run_robot_desktop(core: RynaCore) -> None:
    RynaRobotDesktop(core).run()
