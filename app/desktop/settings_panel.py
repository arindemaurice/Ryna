"""Right-click RYNA Settings & Live Monitoring Panel (dark futuristic Windows Tk window).

Opens when the user right-clicks RYNA so they can monitor live status, activity log,
time-of-day routine, AI/voice state, and adjust every real setting in place.
No fake stats. No dead buttons. Strict integer Tkinter font sizes.
"""

from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.desktop.robot_window import RynaRobotDesktop

BG_ROOT = "#060d19"
BG_CARD = "#0b1629"
BG_INPUT = "#10203a"
BORDER = "#1e3a5f"
CYAN = "#22d3ee"
CYAN_SOFT = "#38bdf8"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"
TEXT_MAIN = "#eef5ff"
TEXT_MUTED = "#94a3b8"


class RynaSettingsMonitorWindow:
    """Live monitoring & settings panel opened on right-clicking RYNA."""

    def __init__(self, robot: "RynaRobotDesktop") -> None:
        self.robot = robot
        self.core = robot.core
        self._closed = False
        self._refresh_job: str | None = None

        self.win = tk.Toplevel(robot.root)
        self.win.title("RYNA — Monitor & Settings")
        self.win.configure(bg=BG_ROOT)
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        # Position cleanly near RYNA without going off-screen
        pw, ph = 460, 620
        sx = int(max(20, min(robot.sw - pw - 20, robot.pos_x - pw - 16 if robot.pos_x > pw + 40 else robot.pos_x + robot.win_w + 16)))
        sy = int(max(20, min(robot.sh - ph - 60, robot.pos_y + robot.win_h - ph)))
        self.win.geometry(f"{pw}x{ph}+{sx}+{sy}")

        # Style ttk comboboxes subtly for dark theme
        style = ttk.Style(self.win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Ryna.TCombobox", fieldbackground=BG_INPUT, background=BG_CARD, foreground=TEXT_MAIN, arrowcolor=CYAN)

        self._build_ui()
        self._refresh_live()

    def _section(self, parent: tk.Widget, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1, padx=10, pady=8)
        outer.pack(fill="x", pady=(0, 8))
        tk.Label(outer, text=title, fg=CYAN, bg=BG_CARD, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(0, 5))
        return outer

    def _build_ui(self) -> None:
        container = tk.Frame(self.win, bg=BG_ROOT, padx=12, pady=10)
        container.pack(fill="both", expand=True)

        # --- Header ---
        hdr = tk.Frame(container, bg=BG_ROOT)
        hdr.pack(fill="x", pady=(0, 8))
        tk.Label(hdr, text="RYNA MONITOR & SETTINGS", fg=TEXT_MAIN, bg=BG_ROOT, font=("Segoe UI", 11, "bold")).pack(side="left")
        self._live_pill = tk.Label(hdr, text="● READY", fg=CYAN, bg=BG_CARD, padx=8, pady=2, font=("Segoe UI", 8, "bold"))
        self._live_pill.pack(side="right")

        # --- 1. Live System Monitor ---
        mon = self._section(container, "LIVE SYSTEM MONITOR")
        self._mon_vars: dict[str, tk.StringVar] = {
            "state": tk.StringVar(value="—"),
            "schedule": tk.StringVar(value="—"),
            "ai": tk.StringVar(value="—"),
            "voice": tk.StringVar(value="—"),
            "battery": tk.StringVar(value="—"),
            "stats": tk.StringVar(value="—"),
        }
        rows = [
            ("Action / State", "state"),
            ("Time Routine", "schedule"),
            ("AI Engine", "ai"),
            ("Voice & Mic", "voice"),
            ("Power", "battery"),
            ("Memory & Logs", "stats"),
        ]
        grid = tk.Frame(mon, bg=BG_CARD)
        grid.pack(fill="x")
        for idx, (lbl, key) in enumerate(rows):
            tk.Label(grid, text=lbl, fg=TEXT_MUTED, bg=BG_CARD, font=("Segoe UI", 8), width=14, anchor="w").grid(row=idx, column=0, sticky="w", pady=1)
            tk.Label(grid, textvariable=self._mon_vars[key], fg=TEXT_MAIN, bg=BG_CARD, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=idx, column=1, sticky="w", pady=1)

        # --- 2. Pose & Routine Controls ---
        pose_box = self._section(container, "POSE & AUTONOMOUS ACTIONS")
        btn_row = tk.Frame(pose_box, bg=BG_CARD)
        btn_row.pack(fill="x")
        for label, mode in (("Auto Time", None), ("Walk", "walk"), ("Stand", "stand"), ("Sit", "sit"), ("Sleep", "sleep")):
            b = tk.Button(
                btn_row,
                text=label,
                command=lambda m=mode: self._set_pose(m),
                bg=BG_INPUT,
                fg=TEXT_MAIN,
                activebackground=CYAN,
                activeforeground="#04101f",
                relief="flat",
                padx=8,
                pady=4,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            )
            b.pack(side="left", expand=True, fill="x", padx=2)

        # --- 3. Real Behavior & Capability Toggles ---
        toggles = self._section(container, "BEHAVIOR & CAPABILITIES")
        s = self.core.db.get_all_settings()

        self._var_walk = tk.BooleanVar(value=bool(self.robot.walk_enabled))
        self._var_phys_search = tk.BooleanVar(value=bool(s.get("desktop.physical_app_search", True)))
        self._var_topmost = tk.BooleanVar(value=bool(self.robot.always_on_top))
        self._var_cont = tk.BooleanVar(value=bool(self.robot.continuous_voice))
        self._var_cloud = tk.BooleanVar(value=bool(s.get("ai.allow_cloud", False)))
        self._var_apps = tk.BooleanVar(value=bool(s.get("computer.allow_app_launch", True)))
        self._var_vision = tk.BooleanVar(value=bool(s.get("computer.allow_screen_vision", True)))

        chk_grid = tk.Frame(toggles, bg=BG_CARD)
        chk_grid.pack(fill="x")
        check_items = [
            ("Autonomous Walking", self._var_walk, self._on_toggle_walk),
            ("Search-Bar App Launch", self._var_phys_search, lambda: self.core.db.set_setting("desktop.physical_app_search", bool(self._var_phys_search.get()))),
            ("Always On Top", self._var_topmost, self._on_toggle_topmost),
            ("Continuous Listening", self._var_cont, self._on_toggle_continuous),
            ("Allow Cloud AI", self._var_cloud, lambda: self.core.db.set_setting("ai.allow_cloud", bool(self._var_cloud.get()))),
            ("Allow App Launching", self._var_apps, lambda: self.core.db.set_setting("computer.allow_app_launch", bool(self._var_apps.get()))),
            ("Allow Screen Vision", self._var_vision, lambda: self.core.db.set_setting("computer.allow_screen_vision", bool(self._var_vision.get()))),
        ]
        for idx, (text, var, cmd) in enumerate(check_items):
            cb = tk.Checkbutton(
                chk_grid,
                text=text,
                variable=var,
                command=cmd,
                bg=BG_CARD,
                fg=TEXT_MAIN,
                selectcolor=BG_INPUT,
                activebackground=BG_CARD,
                activeforeground=CYAN,
                font=("Segoe UI", 8),
                anchor="w",
                cursor="hand2",
            )
            cb.grid(row=idx // 2, column=idx % 2, sticky="w", padx=(0, 8), pady=1)

        # --- 4. Size, Speed, Voice & Model ---
        tune = self._section(container, "ROBOT SIZE, VOICE & LOCAL MODEL")

        # Robot size slider
        r1 = tk.Frame(tune, bg=BG_CARD)
        r1.pack(fill="x", pady=1)
        tk.Label(r1, text="Robot Size", fg=TEXT_MUTED, bg=BG_CARD, width=13, anchor="w", font=("Segoe UI", 8)).pack(side="left")
        self._size_lbl = tk.Label(r1, text=f"{self.robot.robot_size}px", fg=CYAN_SOFT, bg=BG_CARD, width=6, anchor="e", font=("Segoe UI", 8, "bold"))
        self._size_lbl.pack(side="right")
        self._size_scale = tk.Scale(
            r1, from_=125, to=250, orient="horizontal", showvalue=False, bg=BG_CARD, troughcolor=BG_INPUT,
            highlightthickness=0, activebackground=CYAN, command=self._on_size_change,
        )
        self._size_scale.set(self.robot.robot_size)
        self._size_scale.pack(side="left", fill="x", expand=True, padx=4)

        # Speech speed slider
        r2 = tk.Frame(tune, bg=BG_CARD)
        r2.pack(fill="x", pady=1)
        cur_spd = float(s.get("voice.speech_speed", 1.0))
        tk.Label(r2, text="Speech Speed", fg=TEXT_MUTED, bg=BG_CARD, width=13, anchor="w", font=("Segoe UI", 8)).pack(side="left")
        self._spd_lbl = tk.Label(r2, text=f"{cur_spd:.1f}x", fg=CYAN_SOFT, bg=BG_CARD, width=6, anchor="e", font=("Segoe UI", 8, "bold"))
        self._spd_lbl.pack(side="right")
        self._spd_scale = tk.Scale(
            r2, from_=6, to=16, orient="horizontal", showvalue=False, bg=BG_CARD, troughcolor=BG_INPUT,
            highlightthickness=0, activebackground=CYAN, command=self._on_speed_change,
        )
        self._spd_scale.set(int(round(cur_spd * 10)))
        self._spd_scale.pack(side="left", fill="x", expand=True, padx=4)

        # Voice selector
        r3 = tk.Frame(tune, bg=BG_CARD)
        r3.pack(fill="x", pady=(4, 2))
        tk.Label(r3, text="Voice (SAPI)", fg=TEXT_MUTED, bg=BG_CARD, width=13, anchor="w", font=("Segoe UI", 8)).pack(side="left")
        voices = self.core.voice.list_sapi_voices() or [str(s.get("voice.selected_voice", "Microsoft Zira Desktop"))]
        self._voice_var = tk.StringVar(value=str(s.get("voice.selected_voice", voices[0])))
        v_cb = ttk.Combobox(r3, textvariable=self._voice_var, values=voices, state="readonly", style="Ryna.TCombobox", font=("Segoe UI", 8))
        v_cb.pack(side="left", fill="x", expand=True)
        v_cb.bind("<<ComboboxSelected>>", lambda _e: self.core.db.set_setting("voice.selected_voice", self._voice_var.get()))

        # Local model selector
        r4 = tk.Frame(tune, bg=BG_CARD)
        r4.pack(fill="x", pady=2)
        tk.Label(r4, text="Local Model", fg=TEXT_MUTED, bg=BG_CARD, width=13, anchor="w", font=("Segoe UI", 8)).pack(side="left")
        models = self._detect_local_models()
        cur_model = str(s.get("ai.local_model", "")).strip() or (models[0] if models else "auto")
        if cur_model not in models:
            models.insert(0, cur_model)
        self._model_var = tk.StringVar(value=cur_model)
        m_cb = ttk.Combobox(r4, textvariable=self._model_var, values=models, state="readonly", style="Ryna.TCombobox", font=("Segoe UI", 8))
        m_cb.pack(side="left", fill="x", expand=True)
        m_cb.bind("<<ComboboxSelected>>", self._on_model_change)

        # --- 5. Recent Real Activity Monitor ---
        log_box = self._section(container, "RECENT ACTIVITY LOG")
        self._activity_text = tk.Text(
            log_box,
            height=6,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            relief="flat",
            wrap="word",
            font=("Consolas", 8),
            state="disabled",
            padx=6,
            pady=4,
        )
        self._activity_text.pack(fill="both", expand=True)

        # --- Footer Controls ---
        foot = tk.Frame(container, bg=BG_ROOT)
        foot.pack(fill="x", pady=(2, 0))
        for text, bg_c, fg_c, cmd in (
            ("Stop / Interrupt", "#3b1d28", RED, self.robot.interrupt_now),
            ("Hide to Tray", BG_CARD, TEXT_MAIN, self._hide_all),
            ("Exit RYNA", "#3b1d28", RED, self.robot.quit_app),
            ("Close", BG_INPUT, CYAN, self.close),
        ):
            tk.Button(
                foot,
                text=text,
                command=cmd,
                bg=bg_c,
                fg=fg_c,
                activebackground=CYAN,
                activeforeground="#04101f",
                relief="flat",
                padx=8,
                pady=5,
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            ).pack(side="left", expand=True, fill="x", padx=2)

    def _detect_local_models(self) -> list[str]:
        try:
            provs = self.core.ai.build_providers()
            ollama = provs.get("ollama")
            if ollama is not None:
                st = ollama.status()
                if st.models:
                    return list(st.models)
        except Exception:
            pass
        return ["qwen2.5:1.5b", "qwen2.5:3b"]

    def _set_pose(self, pose: str | None) -> None:
        if pose is None:
            self.robot.manual_pose = None
            self.robot.action_until = 0.0
            self.robot._show_bubble("Following automatic time-of-day routine.", dictation_text="", duration=3.5)
        else:
            self.robot.manual_pose = pose
            self.robot.current_action = pose
            if pose == "walk":
                self.robot.walk_enabled = True
                self._var_walk.set(True)
                self.core.db.set_setting("desktop.robot_walk_enabled", True)
            self.robot._show_bubble(f"Switched pose to {pose}.", dictation_text="", duration=3.5)
        self._refresh_live(schedule_next=False)

    def _on_toggle_walk(self) -> None:
        val = bool(self._var_walk.get())
        self.robot.walk_enabled = val
        if not val and self.robot.current_action == "walk":
            self.robot.current_action = "stand"
        self.core.db.set_setting("desktop.robot_walk_enabled", val)

    def _on_toggle_topmost(self) -> None:
        val = bool(self._var_topmost.get())
        self.robot.always_on_top = val
        self.robot.root.attributes("-topmost", val)
        self.core.db.set_setting("desktop.robot_always_on_top", val)

    def _on_toggle_continuous(self) -> None:
        val = bool(self._var_cont.get())
        self.robot.continuous_voice = val
        self.core.db.set_setting("voice.mode", "continuous" if val else "push_to_talk")

    def _on_size_change(self, val: str) -> None:
        px = int(float(val))
        self._size_lbl.config(text=f"{px}px")
        if abs(px - self.robot.robot_size) >= 6:
            self.robot._resize(px)

    def _on_speed_change(self, val: str) -> None:
        spd = round(int(float(val)) / 10.0, 1)
        self._spd_lbl.config(text=f"{spd:.1f}x")
        self.core.db.set_setting("voice.speech_speed", spd)

    def _on_model_change(self, _e: Any = None) -> None:
        m = self._model_var.get().strip()
        if m:
            self.core.db.set_setting("ai.local_model", m)
            self.core.warm_up_async()

    def _hide_all(self) -> None:
        self.close()
        self.robot.hide_to_tray()

    def _refresh_live(self, schedule_next: bool = True) -> None:
        if self._closed or not self.win.winfo_exists():
            return
        try:
            from app.desktop.robot_window import time_of_day_weights

            now_dt = datetime.now()
            period, _ = time_of_day_weights(now_dt.hour)
            act = self.robot.current_action.upper()
            st = self.core.voice.get_state().upper()
            mode_tag = "MANUAL" if self.robot.manual_pose else "AUTO"
            self._live_pill.config(text=f"● {st if st != 'IDLE' else act}")
            self._mon_vars["state"].set(f"{st}  |  Pose: {act} ({mode_tag})")
            self._mon_vars["schedule"].set(f"{period}  ({now_dt.strftime('%I:%M %p').lstrip('0')})")

            model_cfg = str(self.core.db.get_setting("ai.local_model", "")).strip() or "qwen2.5:1.5b (auto)"
            cloud_on = bool(self.core.db.get_setting("ai.allow_cloud", False))
            self._mon_vars["ai"].set(f"Local: {model_cfg}  |  Cloud: {'ON' if cloud_on else 'OFF'}")

            stt_ok, _ = self.core.voice.recognizer.is_available()
            v_name = str(self.core.db.get_setting("voice.selected_voice", "Microsoft Zira Desktop")).replace("Microsoft ", "").replace(" Desktop", "")
            self._mon_vars["voice"].set(f"SAPI ({v_name})  |  Whisper STT: {'READY' if stt_ok else 'OFF'}")

            bat = self.core.computer.battery_status()
            self._mon_vars["battery"].set(f"{bat['percent']}% ({bat['state']})" if bat.get("has_battery") else "AC / Desktop Power")

            db_stats = self.core.db.stats()
            self._mon_vars["stats"].set(f"{db_stats.get('memories', 0)} memories  |  {db_stats.get('messages', 0)} msgs  |  {db_stats.get('activity_logs', 0)} events")

            # Refresh real activity feed
            logs = self.core.db.list_activity(limit=10)
            lines = []
            for item in logs:
                ts = str(item.get("created_at", ""))[11:19]
                cat = str(item.get("category", "sys")).upper()[:5]
                summ = str(item.get("summary", ""))
                lines.append(f"[{ts}] {cat:5s}  {summ}")
            self._activity_text.config(state="normal")
            self._activity_text.delete("1.0", "end")
            self._activity_text.insert("1.0", "\n".join(lines) if lines else "No activity recorded yet.")
            self._activity_text.config(state="disabled")
        except Exception:
            pass

        if schedule_next and not self._closed:
            self._refresh_job = self.win.after(2000, self._refresh_live)

    def focus(self) -> None:
        if not self._closed and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self._refresh_live(schedule_next=False)

    def close(self) -> None:
        self._closed = True
        if self._refresh_job:
            try:
                self.win.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        try:
            self.win.destroy()
        except Exception:
            pass
