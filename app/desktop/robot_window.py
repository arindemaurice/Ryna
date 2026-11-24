"""RYNA Pure Voice-Controlled Walking Desktop Robot (Windows Native).

NO dropdown menus on RYNA. NO website. NO bloated chat app.
Features:
1. Genuine interchanging walking legs (4-phase alternating stride cycle facing right & left).
2. Thinking pose: stops walking, stands upright, looks STRAIGHT forward, and touches her head.
3. Autonomous time-of-day behavior:
   - Night (22:00-05:59): sleeps peacefully with gentle breathing & floating cyan Zzz
   - Morning (06:00-11:59): energetic walking patrols with brief curious stands
   - Afternoon (12:00-17:59): balanced mix of walking, sitting, and standing
   - Evening (18:00-21:59): relaxed sitting with gentle strolls and short naps
4. Click RYNA -> wakes/stops, calmly says:
   "I'm here. What would you like me to help you with?"
   and automatically opens the microphone.
5. Clear floating text box right above RYNA shows a live audio meter + your exact dictated words
   while you speak, while she thinks (touching her head), and while she responds calmly.
6. 100% voice-controlled settings & poses ("sit down", "stand up", "go to sleep", "wake up",
   "walk around", "stop walking", "speak slower", "speak faster", "be bigger", "be smaller",
   "keep listening", "use cloud", "stay offline", "goodbye ryna").
"""

from __future__ import annotations

from datetime import datetime
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
BG_DICTATION = "#0c203d"
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
    "sleeping": "#60a5fa",
    "interrupted": "#fb923c",
    "error": "#f87171",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
GREETING_PROMPT = "I'm here. What would you like me to help you with?"


def time_of_day_weights(hour: int) -> tuple[str, list[tuple[str, float]]]:
    """Return (period_label, weighted idle actions) based on local hour (0-23)."""
    h = int(hour) % 24
    if h >= 22 or h < 6:
        return "NIGHT", [("sleep", 0.85), ("sit", 0.15)]
    if 6 <= h < 12:
        return "MORNING", [("walk", 0.62), ("stand", 0.25), ("sit", 0.13)]
    if 12 <= h < 18:
        return "AFTERNOON", [("walk", 0.44), ("sit", 0.36), ("stand", 0.20)]
    return "EVENING", [("sit", 0.48), ("walk", 0.24), ("sleep", 0.16), ("stand", 0.12)]


def choose_timed_action(hour: int, walk_enabled: bool = True, rng: random.Random | None = None) -> tuple[str, float]:
    """Pick the next autonomous action ('walk' | 'stand' | 'sit' | 'sleep') and duration (seconds)."""
    r = rng or random
    _label, weights = time_of_day_weights(hour)
    filtered = [(act, w) for act, w in weights if walk_enabled or act != "walk"]
    if not filtered:
        filtered = [("stand", 1.0)]
    total = sum(w for _, w in filtered)
    pick = r.random() * total
    acc = 0.0
    chosen = filtered[0][0]
    for act, w in filtered:
        acc += w
        if pick <= acc:
            chosen = act
            break

    if chosen == "sleep":
        return "sleep", r.uniform(22.0, 45.0)
    if chosen == "sit":
        return "sit", r.uniform(9.0, 18.0)
    if chosen == "walk":
        return "walk", r.uniform(5.0, 10.0)
    return "stand", r.uniform(3.5, 6.5)


class RynaRobotDesktop:
    """Pure voice-controlled walking desktop robot with interchanging legs, thinking/sit/sleep poses,
    time-of-day autonomous routine, and live dictation box above her.
    """

    def __init__(self, core: RynaCore) -> None:
        self.core = core
        self.assets_dir = core.config.project_root / "app" / "desktop" / "assets"
        self.robot_png = self.assets_dir / "ryna_robot.png"
        self.head_png = self.assets_dir / "ryna_head.png"

        s = core.db.get_all_settings()
        self.robot_size = max(120, min(260, int(s.get("desktop.robot_size", 176))))
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
        self.walk_speed = 2.5

        # Autonomous time-of-day action state ('walk' | 'stand' | 'sit' | 'sleep')
        self.manual_pose: str | None = None  # Set when user says "sit down", "go to sleep", "stand up", etc.
        init_act, init_dur = choose_timed_action(datetime.now().hour, walk_enabled=self.walk_enabled)
        self.current_action = init_act
        self.action_until = time.monotonic() + init_dur
        if self.current_action == "sleep":
            self.core.voice.set_state("sleeping")

        self.is_listening = False
        self.is_generating = False
        self.is_greeting = False
        self.active_conv_id: str | None = None
        self._last_dictation = ""
        self._bubble_hide_at = time.monotonic() + 7.0
        self._tray_icon: Any = None
        self._settings_panel: Any = None
        self._nav_target: tuple[float, float] | None = None
        self._nav_done_event: threading.Event | None = None
        self._nav_speed = 32.0
        self._next_reminder_check = time.monotonic() + 3.0
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False
        self._dragging_now = False

        # Register RYNA's physical Search-bar app launch choreography on core
        self.core.physical_app_launcher = self._launch_app_physically

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

        self.win_w = self.robot_size + 48
        self.win_h = self.robot_size + 32
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

        self._walk_right: list[ImageTk.PhotoImage] = []
        self._walk_left: list[ImageTk.PhotoImage] = []
        self._pose_stand: ImageTk.PhotoImage | None = None
        self._pose_think: ImageTk.PhotoImage | None = None
        self._pose_sit: ImageTk.PhotoImage | None = None
        self._pose_sleep: ImageTk.PhotoImage | None = None
        self._load_pose_sprites()
        self._bind_events()

        # --- 2. Clean Floating Dictation & Response Box Directly Above RYNA ---
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.configure(bg=CYAN)
        self.bubble.attributes("-topmost", True)
        self.bubble.attributes("-alpha", 0.95)
        self._bubble_frame = tk.Frame(self.bubble, bg=BG_BUBBLE, padx=12, pady=8)
        self._bubble_frame.pack(fill="both", expand=True, padx=1, pady=1)

        top = tk.Frame(self._bubble_frame, bg=BG_BUBBLE)
        top.pack(fill="x")
        self._wave_lbl = tk.Label(top, text="●", fg=CYAN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold"))
        self._wave_lbl.pack(side="left", padx=(0, 5))
        tk.Label(top, text="RYNA", fg=TEXT_MAIN, bg=BG_BUBBLE, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._state_lbl = tk.Label(top, text="READY", fg=CYAN_SOFT, bg=BG_BUBBLE, font=("Segoe UI", 8, "bold"))
        self._state_lbl.pack(side="right")

        # Dedicated Dictation Box (ALWAYS shows clearly what you are speaking / spoke!)
        self._dict_container = tk.Frame(self._bubble_frame, bg=BG_DICTATION, highlightbackground="#1e4976", highlightthickness=1, padx=7, pady=5)
        self._dict_header = tk.Label(
            self._dict_container,
            text="YOU SPOKE",
            fg="#38bdf8",
            bg=BG_DICTATION,
            anchor="w",
            font=("Segoe UI", 7, "bold"),
        )
        self._dict_header.pack(fill="x")
        self._dictation_var = tk.StringVar(value="")
        self._dictation_lbl = tk.Label(
            self._dict_container,
            textvariable=self._dictation_var,
            fg="#e0f2fe",
            bg=BG_DICTATION,
            wraplength=265,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        )
        self._dictation_lbl.pack(fill="x", pady=(1, 2))
        self._mic_bar = tk.Canvas(self._dict_container, width=250, height=5, bg="#071324", highlightthickness=0)

        # RYNA's calm response text
        self._reply_var = tk.StringVar(value=self._initial_greeting())
        self._reply_lbl = tk.Label(
            self._bubble_frame,
            textvariable=self._reply_var,
            fg="#e2e8f0",
            bg=BG_BUBBLE,
            wraplength=275,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        )
        self._reply_lbl.pack(fill="x", pady=(5, 0))

        # Clicking anywhere on the bubble triggers left-click flow; right-click opens Settings & Monitor
        for w in (self.bubble, self._bubble_frame, self._dict_container, self._dictation_lbl, self._reply_lbl):
            w.bind("<Button-1>", lambda _e: self._on_click_ryna())
            w.bind("<Button-3>", lambda _e: self.open_settings_panel())
        self._position_bubble()

        # --- 3. Minimal Tray Icon (only Show / Exit — all settings are voice-controlled) ---
        self._start_tray()
        self.core.warm_up_async()
        self._tick()

    def _initial_greeting(self) -> str:
        period, _ = time_of_day_weights(datetime.now().hour)
        if period == "NIGHT":
            return "Resting for the night. Click me anytime you need help."
        return "Click me whenever you want to talk."

    def _load_single_sprite(self, filename: str) -> Image.Image | None:
        path = self.assets_dir / filename
        if not path.is_file():
            return None
        with Image.open(path) as raw:
            img = raw.convert("RGBA")
        max_dim = self.robot_size - 10
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        return img

    def _load_pose_sprites(self) -> None:
        """Load RYNA's interchanging walk cycle + stand, think (touching head), sit, and sleep poses."""
        self._walk_right.clear()
        self._walk_left.clear()

        # 4-phase genuine interchanging stride cycle: [walk_a, walk_mid, walk_b, walk_mid]
        walk_a = self._load_single_sprite("ryna_walk_a.png") or self._load_single_sprite("ryna_robot.png")
        walk_mid = self._load_single_sprite("ryna_walk_mid.png") or walk_a
        walk_b = self._load_single_sprite("ryna_walk_b.png") or walk_a

        if walk_a is not None:
            cycle_imgs = [
                (walk_a, -1.8),
                (walk_mid, 0.0),
                (walk_b, 1.8),
                (walk_mid, 0.0),
            ]
            for img, deg in cycle_imgs:
                assert img is not None
                r_img = img.rotate(deg, resample=Image.Resampling.BICUBIC, expand=True)
                l_img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT).rotate(-deg, resample=Image.Resampling.BICUBIC, expand=True)
                self._walk_right.append(ImageTk.PhotoImage(r_img))
                self._walk_left.append(ImageTk.PhotoImage(l_img))

        stand = self._load_single_sprite("ryna_stand.png") or walk_a
        think = self._load_single_sprite("ryna_think.png") or stand
        sit = self._load_single_sprite("ryna_sit.png") or stand
        sleep = self._load_single_sprite("ryna_sleep.png") or sit

        self._pose_stand = ImageTk.PhotoImage(stand) if stand is not None else None
        self._pose_think = ImageTk.PhotoImage(think) if think is not None else None
        self._pose_sit = ImageTk.PhotoImage(sit) if sit is not None else None
        self._pose_sleep = ImageTk.PhotoImage(sleep) if sleep is not None else None

    def _bind_events(self) -> None:
        # Left click = greet & listen (or barge-in interrupt); drag = move RYNA;
        # Right click = open RYNA Monitor & Settings panel!
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda _e: self.open_settings_panel())
        self.root.bind("<Escape>", lambda _e: self.interrupt_now())

    def open_settings_panel(self) -> None:
        """Open (or focus) the right-click RYNA Monitor & Settings window."""
        from app.desktop.settings_panel import RynaSettingsMonitorWindow

        if self._settings_panel is not None:
            try:
                if not self._settings_panel._closed and self._settings_panel.win.winfo_exists():
                    self._settings_panel.focus()
                    return
            except Exception:
                pass
        self._settings_panel = RynaSettingsMonitorWindow(self)

    def _launch_app_physically(self, app_name: str) -> dict[str, Any]:
        """Fast physical Search-bar launch:
        1. RYNA briskly runs down to the Windows Search bar (~0.35s, interchanging legs + speed trails)
        2. Pops Windows Search (Win+S), rapidly types the app name (~15ms), and hits Enter to open it!
        """
        from computer.controller import resolve_app_spec

        _key, spec = resolve_app_spec(app_name)
        shown = spec["name"] if spec and spec.get("name") else app_name.strip().title()

        # Pure folders/web URLs open immediately without needing the taskbar search bar
        if spec and ("folder" in spec or ("url" in spec and "cmd" not in spec and "exe_candidates" not in spec and "uri" not in spec)):
            return self.core.computer.launch_application(app_name, prefer_search_bar=False)

        # Target right above Windows Search bar (centered-left taskbar area)
        target_x = float(max(24, min(self.sw - self.win_w - 24, int(self.sw * 0.42 - self.win_w / 2))))
        target_y = float(max(24, self.sh - self.win_h - 44))
        done_ev = threading.Event()

        def _begin_dash() -> None:
            if not self.running:
                done_ev.set()
                return
            dist = math.hypot(target_x - self.pos_x, target_y - self.pos_y)
            # Scale speed so RYNA always reaches the Search bar quickly (~0.30-0.45s!)
            self._nav_speed = max(28.0, min(68.0, dist / 11.0))
            self._nav_done_event = done_ev
            self._nav_target = (target_x, target_y)
            self._show_bubble(f"Going to Search bar for {shown}…", dictation_text=self._last_dictation, show_mic_bar=False, duration=8.0)

        self.root.after(0, _begin_dash)
        done_ev.wait(timeout=0.75)

        self.root.after(
            0,
            lambda: self._show_bubble(f"Searching for {shown}…", dictation_text=self._last_dictation, show_mic_bar=False, duration=6.0),
        )
        return self.core.computer.search_bar_launch(app_name)

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
            bw = max(270, self.bubble.winfo_reqwidth())
            bh = max(68, self.bubble.winfo_reqheight())
            bx = int(self.pos_x + (self.win_w - bw) / 2)
            bx = max(12, min(self.sw - bw - 12, bx))
            by = int(self.pos_y - bh - 10)
            if by < 12:
                by = int(self.pos_y + self.win_h + 6)
            self.bubble.geometry(f"+{bx}+{by}")
        except Exception:
            pass

    def _show_bubble(
        self,
        reply_text: str,
        dictation_text: str | None = None,
        show_mic_bar: bool = False,
        duration: float = 12.0,
    ) -> None:
        if dictation_text is not None:
            self._last_dictation = dictation_text.strip()
            if self._last_dictation or show_mic_bar:
                header = "LISTENING — SPEAK NOW" if show_mic_bar else "YOU SPOKE"
                self._dict_header.config(text=header, fg="#38bdf8" if show_mic_bar else "#7dd3fc")
                self._dictation_var.set(self._last_dictation if self._last_dictation else "Listening for your voice…")
                if not self._dict_container.winfo_ismapped():
                    self._dict_container.pack(fill="x", pady=(6, 2), before=self._reply_lbl)
                if show_mic_bar:
                    if not self._mic_bar.winfo_ismapped():
                        self._mic_bar.pack(fill="x", pady=(3, 1))
                else:
                    if self._mic_bar.winfo_ismapped():
                        self._mic_bar.pack_forget()
            else:
                self._dictation_var.set("")
                if self._mic_bar.winfo_ismapped():
                    self._mic_bar.pack_forget()
                if self._dict_container.winfo_ismapped():
                    self._dict_container.pack_forget()

        clean = reply_text.strip()
        if len(clean) > 240:
            clean = clean[:237] + "…"
        self._reply_var.set(clean)
        self._bubble_hide_at = time.monotonic() + duration
        try:
            self.bubble.deiconify()
            self._position_bubble()
        except Exception:
            pass

    # --- Exact Click-to-Greet -> Dictate -> Think (Touch Head) -> Calm Plain Response Flow ---
    def _on_click_ryna(self) -> None:
        # Wake RYNA up if she was sleeping
        if self.current_action == "sleep" or self.state == "sleeping":
            self.current_action = "stand"
            if self.manual_pose == "sleep":
                self.manual_pose = None
            self.core.voice.set_state("idle")

        # If speaking or thinking -> instant barge-in and start listening right away!
        if self.core.voice.get_state() == "speaking" or self.is_generating or self.is_greeting:
            self.is_greeting = False
            self.interrupt_now()
            self._start_listening_now()
            return

        # If already listening -> finish recording immediately and transcribe what was spoken
        if self.is_listening:
            self.core.voice.recognizer.stop_recording()
            return

        # Fresh click -> stand straight, greet calmly, then automatically listen!
        self.is_greeting = True
        self._show_bubble(GREETING_PROMPT, dictation_text="", show_mic_bar=False, duration=25.0)

        def _after_greeting() -> None:
            self.is_greeting = False
            if self.running and not self.core.voice.is_interrupted():
                self.root.after(100, self._start_listening_now)

        try:
            self.core.voice.speak_native(GREETING_PROMPT, on_finished=_after_greeting)
        except Exception:
            self.is_greeting = False
            self._start_listening_now()

    def _start_listening_now(self) -> None:
        if self.is_listening or not self.running:
            return
        self.is_listening = True
        self._show_bubble("Speak whenever you're ready.", dictation_text="", show_mic_bar=True, duration=25.0)

        def _worker() -> None:
            try:
                transcript = self.core.voice.listen_native(
                    on_level=lambda lvl: setattr(self, "mic_level", lvl),
                    on_partial=lambda partial: self.root.after(
                        0,
                        lambda p=partial: self._show_bubble(
                            "Listening…",
                            dictation_text=p,
                            show_mic_bar=True,
                            duration=25.0,
                        ),
                    ),
                )
                self.is_listening = False
                self.mic_level = 0.0
                if transcript:
                    self.core.record_activity("voice", "voice_transcript", f'Dictated: "{transcript[:100]}"', "ok")
                    self.root.after(0, lambda t=transcript: self._handle_dictated_speech(t))
                else:
                    self.root.after(
                        0,
                        lambda: self._show_bubble(
                            "I didn't hear anything. Click me whenever you're ready.",
                            dictation_text="",
                            show_mic_bar=False,
                            duration=5.0,
                        ),
                    )
            except Exception as exc:
                self.is_listening = False
                self.mic_level = 0.0
                self.root.after(
                    0,
                    lambda e=str(exc): self._show_bubble(f"Microphone error: {e}", dictation_text="", show_mic_bar=False, duration=7.0),
                )

        threading.Thread(target=_worker, daemon=True).start()

    # --- 100% Voice-Controlled Settings & Poses ---
    def _try_voice_setting(self, text: str) -> str | None:
        low = text.strip().lower().rstrip(".!?")

        # Pose & activity commands
        if any(p in low for p in ("sit down", "take a seat", "have a seat", "sit here", "please sit")):
            self.manual_pose = "sit"
            self.current_action = "sit"
            self.core.voice.set_state("idle")
            return "Okay, I'll sit right here."

        if any(p in low for p in ("stand up", "stand straight", "stop sitting")):
            self.manual_pose = "stand"
            self.current_action = "stand"
            self.core.voice.set_state("idle")
            return "Standing up and ready."

        if any(p in low for p in ("go to sleep", "take a nap", "sleep now", "time for bed", "good night")):
            self.manual_pose = "sleep"
            self.current_action = "sleep"
            return "Good night. I'll sleep right here until you click me."

        if any(p in low for p in ("wake up", "get up", "resume schedule", "auto mode", "be natural")):
            self.manual_pose = None
            act, dur = choose_timed_action(datetime.now().hour, walk_enabled=self.walk_enabled)
            self.current_action = "stand" if act == "sleep" else act
            self.action_until = time.monotonic() + dur
            self.core.voice.set_state("idle")
            return "I'm awake and ready to help."

        if any(p in low for p in ("stop walking", "stay still", "stay here", "don't walk", "stand still")):
            self.walk_enabled = False
            self.manual_pose = "stand"
            self.current_action = "stand"
            self.core.db.set_setting("desktop.robot_walk_enabled", False)
            return "Okay, I'll stay right here."

        if any(p in low for p in ("start walking", "walk around", "keep walking", "you can walk")):
            self.walk_enabled = True
            self.manual_pose = None
            self.current_action = "walk"
            self.action_until = time.monotonic() + random.uniform(6.0, 11.0)
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

        if any(p in low for p in ("hide yourself", "go to tray")):
            self.root.after(2200, self.hide_to_tray)
            return "Okay, I'll wait in the system tray until you need me."

        if any(p in low for p in ("start with windows", "enable auto start", "turn on auto start", "enable autostart")):
            from app.desktop.autostart import enable_autostart

            enable_autostart(self.core.config.project_root)
            self.core.db.set_setting("desktop.startup_enabled", True)
            return "Done. I'll start automatically every time Windows boots."

        if any(p in low for p in ("don't start with windows", "disable auto start", "turn off auto start", "disable autostart")):
            from app.desktop.autostart import disable_autostart

            disable_autostart()
            self.core.db.set_setting("desktop.startup_enabled", False)
            return "Okay, I removed myself from Windows startup."

        if any(p in low for p in ("shut down ryna", "goodbye ryna", "exit ryna", "close ryna")):
            self.root.after(2400, self.quit_app)
            return "Goodbye. Call me whenever you need me."

        return None

    def _resize(self, px: int) -> None:
        self.robot_size = px
        self.win_w = px + 48
        self.win_h = px + 32
        self.root.geometry(f"{self.win_w}x{self.win_h}+{int(self.pos_x)}+{int(self.pos_y)}")
        self.canvas.config(width=self.win_w, height=self.win_h)
        self._load_pose_sprites()
        self._position_bubble()
        self.core.db.set_setting("desktop.robot_size", px)

    def _handle_dictated_speech(self, text: str) -> None:
        """Keep exact dictated text visible above RYNA, enter thinking pose (touching head), and reply plainly & calmly."""
        self.core.voice.set_state("thinking")
        self._show_bubble("Thinking…", dictation_text=text, show_mic_bar=False, duration=25.0)

        def _after_spoken() -> None:
            if self.manual_pose == "sleep":
                self.core.voice.set_state("sleeping")
                return
            if self.continuous_voice and self.running and not self.core.voice.is_interrupted():
                self.root.after(300, self._start_listening_now)

        # 1. Check voice-controlled settings & pose commands first
        setting_reply = self._try_voice_setting(text)
        if setting_reply is not None:
            self._show_bubble(setting_reply, dictation_text=text, show_mic_bar=False, duration=9.0)
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
                        self.root.after(
                            0,
                            lambda p=full_acc, t=text: self._show_bubble(p, dictation_text=t, show_mic_bar=False, duration=18.0),
                        )
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
                            self.root.after(
                                0,
                                lambda c=content, t=text: self._show_bubble(c, dictation_text=t, show_mic_bar=False, duration=12.0),
                            )
                        if ai_ok and not self.core.voice.is_interrupted():
                            tail = sentence_buf.strip() if spoke_any else content
                            if tail:
                                self.core.voice.enqueue_sentence(tail, on_all_done=_after_spoken)
                            elif spoke_any:
                                self.core.voice._on_all_spoken = _after_spoken
            except Exception as exc:
                self.root.after(
                    0,
                    lambda e=str(exc), t=text: self._show_bubble(f"Sorry, something went wrong: {e}", dictation_text=t, show_mic_bar=False, duration=8.0),
                )
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
                    pystray.MenuItem("Monitor & Settings", lambda: self.root.after(0, self.open_settings_panel)),
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
        self._show_bubble("I'm here.", dictation_text="", show_mic_bar=False, duration=4.5)

    # --- Expressive Interchanging Walk + Thinking + Sit + Sleep + Time-of-Day Animation Loop ---
    def _tick(self) -> None:
        if not self.running:
            return
        now = time.monotonic()
        self.state = self.core.voice.get_state()
        is_thinking = (self.state == "thinking") or (self.is_generating and self.state != "speaking")
        is_speaking = (self.state == "speaking") or self.is_greeting
        busy = self.is_listening or is_thinking or is_speaking or self.state == "working" or self._dragging_now

        # Update time-of-day autonomous action when RYNA is idle (unless locked by a voice pose command)
        if not busy:
            if now >= self._next_reminder_check:
                self._next_reminder_check = now + 3.0
                try:
                    due = self.core.check_due_reminders()
                    if due:
                        msg = due[0].get("content") or due[0].get("title") or "Your reminder is due."
                        self.current_action = "stand"
                        self._show_bubble(f"Reminder: {msg}", dictation_text="", show_mic_bar=False, duration=12.0)
                        self.core.voice.enqueue_sentence(f"Reminder. {msg}")
                except Exception:
                    pass
            if self.manual_pose is not None:
                self.current_action = self.manual_pose
            elif now >= self.action_until:
                next_act, dur = choose_timed_action(datetime.now().hour, walk_enabled=self.walk_enabled)
                self.current_action = next_act
                self.action_until = now + dur
                if next_act == "walk" and random.random() < 0.4:
                    self.facing *= -1

        is_dashing = self._nav_target is not None and not self._dragging_now
        is_walking = is_dashing or ((not busy) and self.walk_enabled and (self.current_action == "walk"))
        is_sleeping = (not busy) and (not is_dashing) and (self.current_action == "sleep")
        is_sitting = (not busy) and (not is_dashing) and (self.current_action == "sit")

        if is_dashing and self._nav_target is not None:
            tx, ty = self._nav_target
            dx, dy = tx - self.pos_x, ty - self.pos_y
            dist = math.hypot(dx, dy)
            if abs(dx) > 2:
                self.facing = 1 if dx >= 0 else -1
            if dist <= self._nav_speed + 4.0:
                self.pos_x, self.pos_y = tx, ty
                self._nav_target = None
                if self._nav_done_event is not None:
                    self._nav_done_event.set()
                    self._nav_done_event = None
            else:
                self.pos_x += (dx / dist) * self._nav_speed
                self.pos_y += (dy / dist) * self._nav_speed
            self.root.geometry(f"+{int(self.pos_x)}+{int(self.pos_y)}")
            self._position_bubble()
        elif is_walking:
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

        # Live mic volume meter inside the dictation box while listening
        if self.is_listening and self._mic_bar.winfo_ismapped():
            self._mic_bar.delete("all")
            bar_w = max(8, int(250 * min(1.0, max(0.04, self.mic_level))))
            self._mic_bar.create_rectangle(0, 0, bar_w, 5, fill=CYAN_SOFT, outline="")

        self.phase += 0.12 if busy else (0.032 if is_sleeping else 0.055)
        self.step_phase = (self.step_phase + (0.48 if is_dashing else 0.24)) if is_walking else 0.0

        accent = STATE_COLORS.get("working" if is_dashing else ("thinking" if is_thinking else ("sleeping" if is_sleeping else self.state)), CYAN)

        c = self.canvas
        c.delete("all")
        cx = self.win_w / 2
        foot_y = self.win_h - 12

        # Motion speed trails when walking or dashing to Search bar
        if is_walking:
            trail_dir = -self.facing
            for i in range(4 if is_dashing else 3):
                y_line = foot_y - 5 - i * 8
                x0 = cx + trail_dir * (18 + i * 8)
                x1 = x0 + trail_dir * ((38 if is_dashing else 26) - i * 5 + math.sin(self.step_phase + i) * 6)
                c.create_line(x0, y_line, x1, y_line, fill=accent, width=2 if i <= 1 else 1)

        # Ground neon halo (softer when sitting/sleeping, brighter when listening/thinking)
        halo_scale = 0.22 if is_sleeping else (0.26 if is_sitting else 0.29)
        halo_w = self.robot_size * (halo_scale + 0.03 * math.sin(self.phase * 1.4) + self.mic_level * 0.11)
        c.create_oval(cx - halo_w, foot_y - 5, cx + halo_w, foot_y + 5, outline=accent, width=2)
        c.create_oval(cx - halo_w * 0.52, foot_y - 2, cx + halo_w * 0.52, foot_y + 2, fill=accent, outline="")

        # Select the exact sprite for RYNA's current state/action!
        sprite: ImageTk.PhotoImage | None = None
        y_offset = 0.0
        if is_dashing:
            # Briskly dashing toward the Windows Search bar with interchanging legs!
            frames = self._walk_right if self.facing >= 0 else self._walk_left
            if frames:
                idx = int(self.step_phase * 1.55) % len(frames)
                sprite = frames[idx]
                y_offset = -abs(math.sin(self.step_phase * 1.55 * math.pi / 2.0)) * 7.0
        elif is_thinking:
            # STANDING UPRIGHT, LOOKING STRAIGHT FORWARD, TOUCHING HER HEAD!
            sprite = self._pose_think
            y_offset = math.sin(self.phase * 0.8) * 1.8
        elif self.is_listening or is_speaking:
            # Standing straight, attentively looking at the user
            sprite = self._pose_stand
            y_offset = math.sin(self.phase) * (2.6 + self.mic_level * 3.0)
        elif is_walking:
            # Genuine 4-phase interchanging leg walk cycle!
            frames = self._walk_right if self.facing >= 0 else self._walk_left
            if frames:
                idx = int(self.step_phase * 1.35) % len(frames)
                sprite = frames[idx]
                y_offset = -abs(math.sin(self.step_phase * 1.35 * math.pi / 2.0)) * 5.5
        elif is_sleeping:
            sprite = self._pose_sleep
            y_offset = math.sin(self.phase) * 1.6 + 6.0
        elif is_sitting:
            sprite = self._pose_sit
            y_offset = math.sin(self.phase) * 1.4 + 6.0
        else:
            sprite = self._pose_stand
            y_offset = math.sin(self.phase) * 2.0

        if sprite is not None:
            c.create_image(cx, self.win_h * 0.47 + y_offset, image=sprite)

        # Floating peaceful "z Z" animation when sleeping
        if is_sleeping:
            for k, ch in enumerate(("z", "Z", "z")):
                t = (self.phase * 0.35 + k * 0.95) % 2.8
                zx = cx + 26 + k * 11 + math.sin(t * 2.2) * 5
                zy = self.win_h * 0.24 - t * 14
                c.create_text(zx, zy, text=ch, fill="#7dd3fc", font=("Segoe UI", 9 + k, "bold"))

        # Subtle thoughtful floating cyan dots near head when thinking
        if is_thinking:
            for k in range(3):
                bob = math.sin(self.phase * 1.8 - k * 0.9) * 3.0
                dx = cx + 38 + k * 9
                dy = self.win_h * 0.16 + bob
                c.create_oval(dx - 2, dy - 2, dx + 2, dy + 2, fill="#a5b4fc", outline="")

        wave = "◉" if self.is_listening else ("◌" if is_thinking else ("◈" if is_speaking else ("☾" if is_sleeping else "●")))
        self._wave_lbl.config(text=wave, fg=accent)
        clock_str = datetime.now().strftime("%I:%M %p").lstrip("0")
        if is_dashing:
            badge = f"SEARCHING · {clock_str}"
        elif self.is_listening:
            badge = f"LISTENING · {clock_str}"
        elif is_thinking:
            badge = f"THINKING · {clock_str}"
        elif is_speaking:
            badge = f"SPEAKING · {clock_str}"
        elif is_walking:
            badge = f"WALKING · {clock_str}"
        elif is_sleeping:
            badge = f"SLEEPING · {clock_str}"
        elif is_sitting:
            badge = f"SITTING · {clock_str}"
        else:
            badge = f"STANDING · {clock_str}"
        self._state_lbl.config(text=badge, fg=accent)

        self.root.after(33, self._tick)

    def quit_app(self) -> None:
        self.running = False
        self.core.physical_app_launcher = None
        self.core.voice.interrupt("Exiting RYNA")
        if self._settings_panel is not None:
            try:
                self._settings_panel.close()
            except Exception:
                pass
            self._settings_panel = None
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
