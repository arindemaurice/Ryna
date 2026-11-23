"""RYNA Pure Voice-Controlled Walking Desktop Robot (Windows Native).

NO dropdown menus on RYNA. NO website. NO bloated chat app.
1. Click RYNA -> she stops walking, calmly says:
   "I'm here. What would you like me to help you with?"
   and automatically listens to your microphone.
2. Clean floating text box right above RYNA shows what you dictate in real time.
3. When you finish speaking, she responds plainly in a calm spoken voice.
4. Configure RYNA purely by speech ("stop walking", "start walking", "speak slower",
   "speak faster", "be smaller", "be bigger", "keep listening", "use cloud",
   "stay offline", "use fast model", "goodbye Ryna").
5. Click RYNA while she is speaking for instant barge-in interruption.
"""

from __future__ import annotations

import math
import random
import re
import threading
import time
import tkinter as tk
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
GREETING_PROMPT = "I'm here. What would you like me to help you with?"


class RynaRobotDesktop:
    """Pure voice-controlled walking desktop robot with live dictation box above her."""

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

        self.running = True
        self.state = "idle"
        self.phase = 0.0
        self.step_phase = 0.0
        self.mic_level = 0.0
        self.facing = 1
        self.walk_speed = 2.4
        self.patrol_mode = "walk"
        self.patrol_until = time.monotonic() + random.uniform(4.0, 8.0)

        self.is_listening = False
        self.is_generating = False
        self.active_conv_id: str | None = None
        self._bubble_hide_at = time.monotonic() + 6.0
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

        self.win_w = self.robot_size + 44
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

        self._frames_right: list[ImageTk.PhotoImage] = []
        self._frames_left: list[ImageTk.PhotoImage] = []
        self._load_stride_frames()
        self._bind_events()

        # --- 2. Clean Floating Dictation & Response Box Directly Above RYNA ---
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.configure(bg=CYAN)
        self.bubble.attributes("-topmost", True)
        self.bubble.attributes("-alpha", 0.94)
        self._bubble_frame = tk.Frame(self.bubble, bg=BG_BUBBLE, padx=12, pady=8)
        self._bubble_frame.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(self._bubble_frame, bg=BG_BUBBLE)
        top.pack(fill="x")
        self._wave_lbl = tk.Label(top, text="●", fg=CYAN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold"))
        self._wave_lbl.pack(side="left", padx=(0, 5))
        tk.Label(top, text="RYNA", fg=TEXT_MAIN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._state_lbl = tk.Label(top, text="READY", fg=CYAN_SOFT, bg=BG_BUBBLE, font=("Segoe UI", 8))
        self._state_lbl.pack(side="right")

        # Live dictated text row (shows clearly what the user is dictating!)
        self._dictation_var = tk.StringVar(value="")
        self._dictation_lbl = tk.Label(
            self._bubble_frame,
            textvariable=self._dictation_var,
            fg="#7dd3fc",
            bg="#0b1a30",
            wraplength=260,
            justify="left",
            padx=6,
            pady=4,
            font=("Segoe UI", 9),
        )

        # RYNA's calm response text
        self._reply_var = tk.StringVar(value=GREETING_PROMPT)
        self._reply_lbl = tk.Label(
            self._bubble_frame,
            textvariable=self._reply_var,
            fg="#e2e8f0",
            bg=BG_BUBBLE,
            wraplength=260,
            justify="left",
            font=("Segoe UI", 9),
        )
        self._reply_lbl.pack(fill="x", pady=(4, 0))

        self.bubble.bind("<Button-1>", lambda _e: self._on_click_ryna())
        self._position_bubble()

        # --- 3. Minimal Tray Icon (only for Show/Hide/Exit — settings are voice-controlled) ---
        self._start_tray()
        self.core.warm_up_async()
        self._tick()

    def _load_stride_frames(self) -> None:
        self._frames_right.clear()
        self._frames_left.clear()
        if not self.robot_png.is_file():
            return
        base = Image.open(self.robot_png).convert("RGBA")
        base.thumbnail((self.robot_size - 14, self.robot_size - 14), Image.Resampling.LANCZOS)
        flipped = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        for deg in (-4.5, -2.0, 0.5, 3.0, 1.0, -1.5):
            self._frames_right.append(ImageTk.PhotoImage(base.rotate(deg, resample=Image.Resampling.BICUBIC, expand=True)))
            self._frames_left.append(ImageTk.PhotoImage(flipped.rotate(-deg, resample=Image.Resampling.BICUBIC, expand=True)))

    def _bind_events(self) -> None:
        # Left click = greet & listen (or barge-in interrupt); drag = move RYNA. NO dropdown menus!
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
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
            self._on_click_ryna()
            return
        self.pos_x = float(max(6, min(self.sw - self.win_w - 6, int(self.pos_x))))
        self.pos_y = float(max(6, min(self.sh - self.win_h - 46, int(self.pos_y))))
        self.root.geometry(f"+{int(self.pos_x)}+{int(self.pos_y)}")
        self._position_bubble()
        self.core.db.update_settings({"desktop.robot_x": int(self.pos_x), "desktop.robot_y": int(self.pos_y)})

    def _position_bubble(self) -> None:
        try:
            self.bubble.update_idletasks()
            bw = max(250, self.bubble.winfo_reqwidth())
            bh = max(62, self.bubble.winfo_reqheight())
            # Center cleanly right above RYNA's head
            bx = int(self.pos_x + (self.win_w - bw) / 2)
            bx = max(12, min(self.sw - bw - 12, bx))
            by = int(self.pos_y - bh - 10)
            if by < 12:
                by = int(self.pos_y + self.win_h + 6)
            self.bubble.geometry(f"+{bx}+{by}")
        except Exception:
            pass

    def _show_bubble(self, reply_text: str, dictation_text: str | None = None, duration: float = 10.0) -> None:
        if dictation_text is not None:
            if dictation_text.strip():
                self._dictation_var.set(f"You: {dictation_text.strip()}")
                if not self._dictation_lbl.winfo_ismapped():
                    self._dictation_lbl.pack(fill="x", pady=(5, 2), before=self._reply_lbl)
            else:
                self._dictation_var.set("")
                if self._dictation_lbl.winfo_ismapped():
                    self._dictation_lbl.pack_forget()

        clean = reply_text.strip()
        if len(clean) > 220:
            clean = clean[:217] + "…"
        self._reply_var.set(clean)
        self._bubble_hide_at = time.monotonic() + duration
        try:
            self.bubble.deiconify()
            self._position_bubble()
        except Exception:
            pass

    # --- Exact Click-to-Greet -> Dictate -> Calm Plain Response Flow ---
    def _on_click_ryna(self) -> None:
        # If she is currently speaking or generating -> instant barge-in and start listening right away!
        if self.core.voice.get_state() == "speaking" or self.is_generating:
            self.interrupt_now()
            self._start_listening_now()
            return

        # If she is already listening -> finish recording immediately and process what was dictated
        if self.is_listening:
            self.core.voice.recognizer.stop_recording()
            return

        # Fresh click -> greet calmly, then automatically open microphone to listen!
        self._show_bubble(GREETING_PROMPT, dictation_text="", duration=20.0)
        try:
            self.core.voice.speak_native(
                GREETING_PROMPT,
                on_finished=lambda: self.root.after(120, self._start_listening_now) if self.running and not self.core.voice.is_interrupted() else None,
            )
        except Exception:
            self._start_listening_now()

    def _start_listening_now(self) -> None:
        if self.is_listening or not self.running:
            return
        self.is_listening = True
        self._show_bubble("Listening…", dictation_text="…", duration=20.0)

        def _worker() -> None:
            try:
                transcript = self.core.voice.listen_native(
                    on_level=lambda lvl: setattr(self, "mic_level", lvl),
                    on_partial=lambda partial: self.root.after(
                        0, lambda p=partial: self._show_bubble("Listening…", dictation_text=p, duration=20.0)
                    ),
                )
                self.is_listening = False
                self.mic_level = 0.0
                if transcript:
                    self.core.record_activity("voice", "voice_transcript", f'Dictated: "{transcript[:100]}"', "ok")
                    self.root.after(0, lambda t=transcript: self._handle_dictated_speech(t))
                else:
                    self.root.after(0, lambda: self._show_bubble("I didn't hear anything. Click me whenever you're ready.", dictation_text="", duration=4.5))
            except Exception as exc:
                self.is_listening = False
                self.mic_level = 0.0
                self.root.after(0, lambda e=str(exc): self._show_bubble(f"Microphone error: {e}", dictation_text="", duration=6.0))

        threading.Thread(target=_worker, daemon=True).start()

    # --- 100% Voice-Controlled Settings ---
    def _try_voice_setting(self, text: str) -> str | None:
        low = text.strip().lower().rstrip(".!?")

        if any(p in low for p in ("stop walking", "stay still", "stay here", "don't walk", "stand still")):
            self.walk_enabled = False
            self.core.db.set_setting("desktop.robot_walk_enabled", False)
            return "Okay, I'll stay right here."

        if any(p in low for p in ("start walking", "walk around", "keep walking", "you can walk")):
            self.walk_enabled = True
            self.core.db.set_setting("desktop.robot_walk_enabled", True)
            return "Sure, I'll walk around your desktop."

        if any(p in low for p in ("keep listening", "continuous mode", "continuous voice", "hands free")):
            self.continuous_voice = True
            self.core.db.set_setting("voice.mode", "continuous")
            return "Continuous listening is on. I'll keep listening after I answer."

        if any(p in low for p in ("push to talk", "stop continuous", "click to talk")):
            self.continuous_voice = False
            self.core.db.set_setting("voice.mode", "push_to_talk")
            return "Okay, just click me whenever you want to talk."

        if any(p in low for p in ("speak faster", "talk faster")):
            cur = float(self.core.db.get_setting("voice.speech_speed", 1.0))
            nxt = min(1.6, round(cur + 0.2, 1))
            self.core.db.set_setting("voice.speech_speed", nxt)
            return "Got it, I'll speak a little faster."

        if any(p in low for p in ("speak slower", "talk slower")):
            cur = float(self.core.db.get_setting("voice.speech_speed", 1.0))
            nxt = max(0.6, round(cur - 0.2, 1))
            self.core.db.set_setting("voice.speech_speed", nxt)
            return "Sure, I'll speak more slowly."

        if "normal speed" in low:
            self.core.db.set_setting("voice.speech_speed", 1.0)
            return "Back to my normal calm pace."

        if any(p in low for p in ("be bigger", "grow bigger", "make yourself bigger", "get bigger")):
            self._resize(min(250, self.robot_size + 35))
            return "There, a bit bigger."

        if any(p in low for p in ("be smaller", "get smaller", "make yourself smaller", "shrink")):
            self._resize(max(125, self.robot_size - 35))
            return "Okay, I've made myself smaller."

        if any(p in low for p in ("use cloud", "enable cloud", "turn on cloud")):
            self.core.db.set_setting("ai.allow_cloud", True)
            return "Cloud AI is now enabled when you're online."

        if any(p in low for p in ("stay offline", "offline only", "disable cloud", "local only")):
            self.core.db.set_setting("ai.allow_cloud", False)
            return "Okay, I'll use only local offline intelligence."

        if any(p in low for p in ("use fast model", "faster model")):
            self.core.db.set_setting("ai.local_model", "qwen2.5:1.5b")
            self.core.warm_up_async()
            return "Switched to my fastest local model."

        if any(p in low for p in ("use smarter model", "stronger model")):
            self.core.db.set_setting("ai.local_model", "qwen2.5:3b")
            self.core.warm_up_async()
            return "Switched to the stronger local model."

        if any(p in low for p in ("go to sleep", "hide yourself")):
            self.root.after(2200, self.hide_to_tray)
            return "Okay, I'll rest in the system tray until you need me."

        if any(p in low for p in ("shut down ryna", "goodbye ryna", "exit ryna", "close ryna")):
            self.root.after(2400, self.quit_app)
            return "Goodbye. Call me whenever you need me."

        return None

    def _resize(self, px: int) -> None:
        self.robot_size = px
        self.win_w = px + 44
        self.win_h = px + 26
        self.root.geometry(f"{self.win_w}x{self.win_h}+{int(self.pos_x)}+{int(self.pos_y)}")
        self.canvas.config(width=self.win_w, height=self.win_h)
        self._load_stride_frames()
        self._position_bubble()
        self.core.db.set_setting("desktop.robot_size", px)

    def _handle_dictated_speech(self, text: str) -> None:
        """Show exact dictated text above RYNA, execute voice setting/action/AI, and reply plainly & calmly."""
        self._show_bubble("Thinking…", dictation_text=text, duration=20.0)

        def _after_spoken() -> None:
            if self.continuous_voice and self.running and not self.core.voice.is_interrupted():
                self.root.after(300, self._start_listening_now)

        # 1. Check voice-controlled settings first
        setting_reply = self._try_voice_setting(text)
        if setting_reply is not None:
            self._show_bubble(setting_reply, dictation_text=text, duration=8.0)
            self.core.voice.enqueue_sentence(setting_reply, on_all_done=_after_spoken)
            return

        # 2. Stream real AI / tool response and speak sentence-by-sentence calmly
        self.is_generating = True

        def _worker() -> None:
            sentence_buf = ""
            full_acc = ""
            spoke_any = False
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
                        self.root.after(0, lambda p=full_acc, t=text: self._show_bubble(p, dictation_text=t, duration=15.0))
                        parts = SENTENCE_SPLIT_RE.split(sentence_buf)
                        if len(parts) > 1:
                            for sent in parts[:-1]:
                                if sent.strip():
                                    self.core.voice.enqueue_sentence(sent.strip())
                                    spoke_any = True
                            sentence_buf = parts[-1]
                    elif ev["event"] in ("done", "error"):
                        msg = ev["data"].get("assistant_message") or {}
                        content = msg.get("content", "")
                        ai_ok = ev["data"].get("ai_available", False)
                        if content:
                            self.root.after(0, lambda c=content, t=text: self._show_bubble(c, dictation_text=t, duration=10.0))
                        if ai_ok and not self.core.voice.is_interrupted():
                            tail = sentence_buf.strip() if spoke_any else content
                            if tail:
                                self.core.voice.enqueue_sentence(tail, on_all_done=_after_spoken)
                            elif spoke_any:
                                self.core.voice._on_all_spoken = _after_spoken
            except Exception as exc:
                self.root.after(0, lambda e=str(exc), t=text: self._show_bubble(f"Sorry, something went wrong: {e}", dictation_text=t, duration=7.0))
            finally:
                self.is_generating = False

        threading.Thread(target=_worker, daemon=True).start()

    def interrupt_now(self) -> None:
        self.core.voice.interrupt("User interrupted RYNA")

    # --- Minimal System Tray (Show / Exit) ---
    def _start_tray(self) -> None:
        def _run() -> None:
            try:
                import pystray

                icon_img = Image.open(self.head_png).convert("RGBA") if self.head_png.is_file() else Image.new("RGBA", (64, 64), (34, 211, 238, 255))
                menu = pystray.Menu(
                    pystray.MenuItem("Show RYNA", lambda: self.root.after(0, self.show_from_tray)),
                    pystray.MenuItem("Exit RYNA", lambda: self.root.after(0, self.quit_app)),
                )
                self._tray_icon = pystray.Icon("RYNA", icon_img, "RYNA", menu)
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
        self._show_bubble("I'm here.", dictation_text="", duration=4.0)

    # --- Walking + Expressive Animation Loop ---
    def _tick(self) -> None:
        if not self.running:
            return
        now = time.monotonic()
        self.state = self.core.voice.get_state()
        accent = STATE_COLORS.get(self.state, CYAN)
        busy = self.state in ("listening", "thinking", "speaking", "working") or self.is_listening or self.is_generating or self._dragging_now

        is_walking = False
        if self.walk_enabled and not busy:
            if now >= self.patrol_until:
                if self.patrol_mode == "walk":
                    self.patrol_mode = "pause"
                    self.patrol_until = now + random.uniform(2.5, 5.0)
                else:
                    self.patrol_mode = "walk"
                    if random.random() < 0.35:
                        self.facing *= -1
                    self.patrol_until = now + random.uniform(4.5, 9.0)

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

        if not busy and now > self._bubble_hide_at:
            try:
                if self.bubble.state() != "withdrawn":
                    self.bubble.withdraw()
            except Exception:
                pass

        self.phase += 0.13 if busy else 0.055
        self.step_phase = (self.step_phase + 0.27) if is_walking else 0.0

        c = self.canvas
        c.delete("all")
        cx = self.win_w / 2
        foot_y = self.win_h - 12

        if is_walking:
            trail_dir = -self.facing
            for i in range(3):
                y_line = foot_y - 4 - i * 8
                x0 = cx + trail_dir * (18 + i * 8)
                x1 = x0 + trail_dir * (26 - i * 5 + math.sin(self.step_phase + i) * 6)
                c.create_line(x0, y_line, x1, y_line, fill=accent, width=2 if i == 0 else 1)

        halo_w = self.robot_size * (0.28 + 0.04 * math.sin(self.phase * 1.5) + self.mic_level * 0.10)
        c.create_oval(cx - halo_w, foot_y - 5, cx + halo_w, foot_y + 5, outline=accent, width=2)
        c.create_oval(cx - halo_w * 0.55, foot_y - 2, cx + halo_w * 0.55, foot_y + 2, fill=accent, outline="")

        frames = self._frames_right if self.facing >= 0 else self._frames_left
        if frames:
            if is_walking:
                idx = int(self.step_phase * 2.2) % len(frames)
                y_offset = -abs(math.sin(self.step_phase)) * 7.5
            else:
                idx = 2
                y_offset = math.sin(self.phase) * (4.0 if busy else 2.2)
            c.create_image(cx, self.win_h * 0.46 + y_offset, image=frames[idx])

        wave = "◉" if self.state == "listening" else ("◈" if self.state == "speaking" else "●")
        self._wave_lbl.config(text=wave, fg=accent)
        self._state_lbl.config(text=("LISTENING" if self.is_listening else ("WALKING" if is_walking else self.state.upper())), fg=accent)

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
