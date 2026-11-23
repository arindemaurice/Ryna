"""RYNA Native Windows Desktop Presence — The Robot IS RYNA.

Renders directly on the Windows desktop (no browser website required):
1. Transparent floating RYNA robot (`app/desktop/assets/ryna_robot.png`) with hover/state glow,
   movable & resizable with persisted coordinates and edge awareness.
2. Floating cyan-bordered speech bubble right above RYNA showing live listening/speaking/status.
3. Floating translucent dark-navy glass HUD panel (matching the authoritative RYNA desktop design)
   with live streaming conversation, real Action Cards, quick real tools, native microphone button
   (sounddevice + offline faster-whisper), instant barge-in interrupt, and Memory/Tasks/Settings dialogs.
4. Native Windows System Tray icon (pystray) using RYNA's cropped head icon.

Connected directly to the single RynaCore instance (same identity, memory, tasks, local/cloud AI, and voice).
"""

from __future__ import annotations

import math
import threading
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk
from typing import Any

from PIL import Image, ImageDraw, ImageTk

from ai.orchestration.core import RynaCore

TRANSPARENT_KEY = "#010203"
BG_GLASS = "#07101f"
BG_CARD = "#0c192f"
BG_INPUT = "#0a1528"
CYAN = "#22d3ee"
CYAN_SOFT = "#38bdf8"
BLUE = "#2563eb"
TEXT_MAIN = "#eef5ff"
TEXT_MUTED = "#90a7c7"

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


class RynaDesktopApp:
    """Native Windows desktop robot, floating speech bubble, glass HUD, and system tray."""

    def __init__(self, core: RynaCore) -> None:
        self.core = core
        self.assets_dir = core.config.project_root / "app" / "desktop" / "assets"
        self.robot_png = self.assets_dir / "ryna_robot.png"
        self.head_png = self.assets_dir / "ryna_head.png"

        s = core.db.get_all_settings()
        self.robot_size = max(130, min(280, int(s.get("desktop.robot_size", 190))))
        self.alpha = float(s.get("desktop.robot_transparency", 0.98))
        self.always_on_top = bool(s.get("desktop.robot_always_on_top", True))
        self.continuous_voice = str(s.get("voice.mode", "push_to_talk")) == "continuous"

        self.phase = 0.0
        self.mic_level = 0.0
        self.state = "idle"
        self.running = True
        self.visible = True
        self.hud_open = True
        self.active_conv_id: str | None = None
        self.is_generating = False
        self.is_listening = False
        self._tray_icon: Any = None
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False

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

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        saved_x = int(s.get("desktop.robot_x", -1))
        saved_y = int(s.get("desktop.robot_y", -1))
        rx = saved_x if 0 <= saved_x <= sw - 80 else (sw - self.robot_size - 160)
        ry = saved_y if 0 <= saved_y <= sh - 80 else 110
        self.root.geometry(f"{self.robot_size}x{self.robot_size + 28}+{rx}+{ry}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.robot_size,
            height=self.robot_size + 28,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._robot_photo: ImageTk.PhotoImage | None = None
        self._head_photo_sm: ImageTk.PhotoImage | None = None
        self._load_assets()
        self._build_robot_menu()
        self._bind_robot_events()

        # --- 2. Floating Speech Bubble Above/Beside RYNA ---
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.configure(bg=CYAN)
        self.bubble.attributes("-topmost", True)
        self.bubble.attributes("-alpha", 0.94)
        self._bubble_inner = tk.Frame(self.bubble, bg=BG_GLASS, padx=12, pady=8)
        self._bubble_inner.pack(fill="both", expand=True, padx=1, pady=1)

        b_head = tk.Frame(self._bubble_inner, bg=BG_GLASS)
        b_head.pack(fill="x")
        self._bubble_wave = tk.Label(b_head, text="●", fg=CYAN, bg=BG_GLASS, font=("Segoe UI", 9, "bold"))
        self._bubble_wave.pack(side="left", padx=(0, 6))
        tk.Label(b_head, text="RYNA", fg=TEXT_MAIN, bg=BG_GLASS, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._bubble_state_lbl = tk.Label(b_head, text="IDLE", fg=CYAN_SOFT, bg=BG_GLASS, font=("Segoe UI", 8))
        self._bubble_state_lbl.pack(side="right")

        self._bubble_text = tk.StringVar(value="I'm here. What would you like me to do?")
        self._bubble_lbl = tk.Label(
            self._bubble_inner,
            textvariable=self._bubble_text,
            fg="#dbeafe",
            bg=BG_GLASS,
            wraplength=235,
            justify="left",
            font=("Segoe UI", 9),
        )
        self._bubble_lbl.pack(fill="x", pady=(4, 0))
        self.bubble.bind("<Button-1>", lambda _e: self.interrupt_now() if self.state == "speaking" else self.toggle_hud())
        self._position_bubble()

        # --- 3. Floating Translucent Dark Glass HUD Panel ---
        self._build_hud_panel(sw, sh)

        # --- 4. System Tray Icon ---
        self._start_tray()

        # Subscribe to real RynaCore state updates
        self._event_sub = self.core.events.subscribe()
        threading.Thread(target=self._event_loop, daemon=True).start()

        # Refresh real live operating mode & greet naturally
        self._refresh_hud_status()
        self._load_recent_conversation()
        self._animate()

    def _load_assets(self) -> None:
        if self.robot_png.is_file():
            img = Image.open(self.robot_png).convert("RGBA")
            img.thumbnail((self.robot_size - 16, self.robot_size - 16), Image.Resampling.LANCZOS)
            self._robot_photo = ImageTk.PhotoImage(img)
        if self.head_png.is_file():
            himg = Image.open(self.head_png).convert("RGBA").resize((30, 30), Image.Resampling.LANCZOS)
            self._head_photo_sm = ImageTk.PhotoImage(himg)

    def _build_robot_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg=BG_GLASS, fg=TEXT_MAIN, activebackground="#162847")
        self.menu.add_command(label="🎤 Speak to RYNA", command=self.toggle_mic)
        self.menu.add_command(label="💬 Show / Hide Chat HUD", command=self.toggle_hud)
        self.menu.add_command(label="■ Interrupt Speaking", command=self.interrupt_now)
        self.menu.add_separator()
        size_m = tk.Menu(self.menu, tearoff=0, bg=BG_GLASS, fg=TEXT_MAIN)
        for label, px in (("Compact (150px)", 150), ("Standard (190px)", 190), ("Large (240px)", 240)):
            size_m.add_command(label=label, command=lambda p=px: self._resize_robot(p))
        self.menu.add_cascade(label="Robot Size", menu=size_m)
        self.menu.add_command(label="Toggle Continuous Voice", command=self._toggle_continuous_voice)
        self.menu.add_command(label="Toggle Always On Top", command=self._toggle_topmost)
        self.menu.add_command(label="Memory / Tasks / Settings…", command=self._open_control_center)
        self.menu.add_separator()
        self.menu.add_command(label="Hide to System Tray", command=self.hide_to_tray)
        self.menu.add_command(label="Exit RYNA", command=self.quit_app)

    def _bind_robot_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.toggle_mic())
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _on_press(self, e: tk.Event) -> None:
        self._drag_x, self._drag_y, self._dragged = e.x, e.y, False

    def _on_drag(self, e: tk.Event) -> None:
        if abs(e.x - self._drag_x) > 3 or abs(e.y - self._drag_y) > 3:
            self._dragged = True
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")
        self._position_bubble()

    def _on_release(self, _e: tk.Event) -> None:
        if not self._dragged:
            self.toggle_hud()
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(8, min(sw - self.robot_size - 8, self.root.winfo_x()))
        y = max(8, min(sh - self.robot_size - 56, self.root.winfo_y()))
        if x < 24:
            x = 12
        elif sw - (x + self.robot_size) < 24:
            x = sw - self.robot_size - 12
        self.root.geometry(f"+{x}+{y}")
        self._position_bubble()
        self.core.db.update_settings({"desktop.robot_x": x, "desktop.robot_y": y})

    def _position_bubble(self) -> None:
        try:
            self.root.update_idletasks()
            rx = self.root.winfo_x()
            ry = self.root.winfo_y()
            bx = max(12, rx - 255)
            by = max(12, ry + 8)
            self.bubble.geometry(f"+{bx}+{by}")
        except Exception:
            pass

    def _resize_robot(self, px: int) -> None:
        self.robot_size = px
        self.root.geometry(f"{px}x{px + 28}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self.canvas.config(width=px, height=px + 28)
        self._load_assets()
        self._position_bubble()
        self.core.db.set_setting("desktop.robot_size", px)

    def _toggle_topmost(self) -> None:
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.hud.attributes("-topmost", self.always_on_top)
        self.core.db.set_setting("desktop.robot_always_on_top", self.always_on_top)

    def _toggle_continuous_voice(self) -> None:
        self.continuous_voice = not self.continuous_voice
        mode = "continuous" if self.continuous_voice else "push_to_talk"
        self.core.db.set_setting("voice.mode", mode)
        self._set_bubble(f"Voice mode: {'Continuous conversation' if self.continuous_voice else 'Push-to-talk'}.")

    # --- 3. Floating Glass HUD Panel ---
    def _build_hud_panel(self, sw: int, sh: int) -> None:
        self.hud = tk.Toplevel(self.root)
        self.hud.title("RYNA")
        self.hud.overrideredirect(True)
        self.hud.configure(bg=CYAN)
        self.hud.attributes("-topmost", self.always_on_top)
        self.hud.attributes("-alpha", 0.95)
        w, h = 420, 460
        hx = max(20, sw - w - 28)
        hy = max(60, sh - h - 72)
        self.hud.geometry(f"{w}x{h}+{hx}+{hy}")

        outer = tk.Frame(self.hud, bg=BG_GLASS)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        # Draggable HUD header
        hdr = tk.Frame(outer, bg="#0a162b", padx=12, pady=9)
        hdr.pack(fill="x")
        self._hud_drag = {"x": 0, "y": 0}
        hdr.bind("<ButtonPress-1>", lambda e: self._hud_drag.update(x=e.x, y=e.y))
        hdr.bind("<B1-Motion>", lambda e: self.hud.geometry(f"+{self.hud.winfo_x() + e.x - self._hud_drag['x']}+{self.hud.winfo_y() + e.y - self._hud_drag['y']}"))

        if self._head_photo_sm:
            tk.Label(hdr, image=self._head_photo_sm, bg="#0a162b").pack(side="left", padx=(0, 8))
        title_box = tk.Frame(hdr, bg="#0a162b")
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text="RYNA", fg=TEXT_MAIN, bg="#0a162b", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._hud_status_var = tk.StringVar(value="● Detecting mode…")
        self._hud_status_lbl = tk.Label(title_box, textvariable=self._hud_status_var, fg="#34d399", bg="#0a162b", font=("Segoe UI", 8.5))
        self._hud_status_lbl.pack(anchor="w")

        tk.Button(hdr, text="⚙", command=self._open_control_center, bg="#0a162b", fg=TEXT_MUTED, relief="flat", font=("Segoe UI", 10), cursor="hand2").pack(side="right", padx=2)
        tk.Button(hdr, text="—", command=self.toggle_hud, bg="#0a162b", fg=TEXT_MUTED, relief="flat", font=("Segoe UI", 10), cursor="hand2").pack(side="right", padx=2)

        # Scrollable Chat Stream
        chat_wrap = tk.Frame(outer, bg=BG_GLASS)
        chat_wrap.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        self.chat_text = tk.Text(
            chat_wrap,
            bg=BG_GLASS,
            fg=TEXT_MAIN,
            wrap="word",
            relief="flat",
            font=("Segoe UI", 9.5),
            padx=6,
            pady=6,
            state="disabled",
            cursor="arrow",
        )
        sb = ttk.Scrollbar(chat_wrap, orient="vertical", command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=sb.set)
        self.chat_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.chat_text.tag_configure("user_lbl", foreground="#7dd3fc", font=("Segoe UI", 8.5, "bold"), justify="right")
        self.chat_text.tag_configure("user_msg", foreground="#ffffff", background="#15325c", lmargin1=55, lmargin2=55, rmargin=6, spacing1=2, spacing3=8)
        self.chat_text.tag_configure("ryna_lbl", foreground=CYAN, font=("Segoe UI", 8.5, "bold"))
        self.chat_text.tag_configure("ryna_msg", foreground=TEXT_MAIN, background=BG_CARD, lmargin1=6, lmargin2=6, rmargin=28, spacing1=2, spacing3=8)
        self.chat_text.tag_configure("card_msg", foreground="#a7f3d0", background="#0c2430", lmargin1=10, lmargin2=10, rmargin=20, spacing1=2, spacing3=8, font=("Consolas", 8.5))
        self.chat_text.tag_configure("sys_msg", foreground="#fde68a", lmargin1=6, spacing3=8, font=("Segoe UI", 8.5))

        # Quick Real Action Pills (matching reference screenshot!)
        pill_row = tk.Frame(outer, bg=BG_GLASS, padx=10, pady=4)
        pill_row.pack(fill="x")
        for label, cmd in (
            ("🌐 Open Browser", lambda: self.send_user_text("open browser")),
            ("🔎 Search Web", self._prompt_web_search),
            ("🕒 Check Time", lambda: self.send_user_text("what time is it")),
            ("🖥 Screen", lambda: self.send_user_text("look at my screen")),
            ("More ▾", self._open_control_center),
        ):
            tk.Button(
                pill_row,
                text=label,
                command=cmd,
                bg=BG_CARD,
                fg="#cbd5e1",
                activebackground="#173055",
                activeforeground="#ffffff",
                relief="flat",
                font=("Segoe UI", 8),
                padx=7,
                pady=4,
                cursor="hand2",
            ).pack(side="left", padx=(0, 4))

        # Bottom Voice + Text Input Bar
        inp_bar = tk.Frame(outer, bg="#091326", padx=10, pady=9)
        inp_bar.pack(fill="x")

        self.mic_btn = tk.Button(
            inp_bar,
            text="🎤",
            command=self.toggle_mic,
            bg="#0e2545",
            fg=CYAN,
            activebackground="#153c6b",
            relief="flat",
            font=("Segoe UI", 11),
            width=3,
            cursor="hand2",
        )
        self.mic_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            inp_bar,
            text="■",
            command=self.interrupt_now,
            bg="#3b1822",
            fg="#fca5a5",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            width=3,
            cursor="hand2",
        )
        self.stop_btn.pack(side="left", padx=(0, 6))

        self.entry = tk.Entry(
            inp_bar,
            bg=BG_INPUT,
            fg=TEXT_MAIN,
            insertbackground=CYAN,
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        self.entry.bind("<Return>", lambda _e: self._on_submit_entry())
        self.entry.bind("<Escape>", lambda _e: self.interrupt_now())

        tk.Button(
            inp_bar,
            text="➤",
            command=self._on_submit_entry,
            bg=BLUE,
            fg="#ffffff",
            activebackground="#0284c7",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            width=3,
            cursor="hand2",
        ).pack(side="right")

    def _prompt_web_search(self) -> None:
        q = simpledialog.askstring("Search Web via RYNA", "What should RYNA search for?", parent=self.hud)
        if q and q.strip():
            self.send_user_text(f"search web for {q.strip()}")

    def _append_chat(self, role: str, text: str, card: dict[str, Any] | None = None, meta: str = "") -> None:
        self.chat_text.configure(state="normal")
        if role == "user":
            self.chat_text.insert("end", "You\n", "user_lbl")
            self.chat_text.insert("end", f" {text.strip()} \n\n", "user_msg")
        elif role == "assistant":
            hdr = f"RYNA{f' · {meta}' if meta else ''}\n"
            self.chat_text.insert("end", hdr, "ryna_lbl")
            if card:
                self.chat_text.insert("end", f" [{card.get('title','')}] {card.get('value','')} — {card.get('subtitle','')}\n", "card_msg")
            self.chat_text.insert("end", f"{text.strip()}\n\n", "ryna_msg")
        else:
            self.chat_text.insert("end", f"{text.strip()}\n\n", "sys_msg")
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def _load_recent_conversation(self) -> None:
        convs = self.core.db.list_conversations(limit=1)
        if convs:
            self.active_conv_id = convs[0]["id"]
            for m in self.core.db.list_messages(self.active_conv_id, limit=20):
                self._append_chat(m["role"], m["content"], m.get("metadata", {}).get("action_card"), m.get("model") or "")
        else:
            self._append_chat("assistant", "I'm here. Click the microphone to speak to me, or type anything below.")

    def _refresh_hud_status(self) -> None:
        def _work() -> None:
            try:
                st = self.core.status_summary()
                op = st["operating_mode"]
                lbl = f"● {op['label']}" + (f" ({op['active_model']})" if op.get("active_model") else "")
                color = "#34d399" if op["id"].startswith("online_") else ("#38bdf8" if op["id"] == "offline_local_ai" else "#fbbf24")
                self.root.after(0, lambda: (self._hud_status_var.set(lbl), self._hud_status_lbl.config(fg=color)))
            except Exception:
                pass
            if self.running:
                self.root.after(15000, self._refresh_hud_status)

        threading.Thread(target=_work, daemon=True).start()

    def _set_bubble(self, text: str) -> None:
        short = text.strip()
        if len(short) > 165:
            short = short[:162] + "…"
        self._bubble_text.set(short)
        self._position_bubble()

    def _on_submit_entry(self) -> None:
        txt = self.entry.get().strip()
        if not txt:
            return
        self.entry.delete(0, "end")
        self.send_user_text(txt)

    def send_user_text(self, text: str, from_voice: bool = False) -> None:
        if self.is_generating:
            self.interrupt_now()
        self._append_chat("user", text)
        self._set_bubble("Thinking…")
        self.is_generating = True

        def _worker() -> None:
            acc: list[str] = []
            final_msg: dict[str, Any] | None = None
            ai_ok = True
            try:
                for ev in self.core.stream_chat(self.active_conv_id, text):
                    if ev["event"] == "meta":
                        self.active_conv_id = ev["data"]["conversation_id"]
                    elif ev["event"] == "delta":
                        acc.append(ev["data"].get("delta", ""))
                        preview = "".join(acc).strip()
                        if preview:
                            self.root.after(0, lambda p=preview: self._set_bubble(p[-150:]))
                    elif ev["event"] in ("done", "error"):
                        final_msg = ev["data"].get("assistant_message")
                        ai_ok = ev["data"].get("ai_available", False)
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._append_chat("system", f"Error: {e}"))
            finally:
                self.is_generating = False
                if final_msg:
                    content = final_msg.get("content", "")
                    card = final_msg.get("metadata", {}).get("action_card")
                    model_lbl = final_msg.get("model") or ""
                    role = final_msg.get("role", "assistant")
                    self.root.after(0, lambda: (self._append_chat(role, content, card, model_lbl), self._set_bubble(content)))

                    auto_speak = bool(self.core.db.get_setting("voice.auto_speak", True))
                    if ai_ok and content and (from_voice or auto_speak):
                        def _after_speak() -> None:
                            if self.continuous_voice and self.running:
                                self.root.after(250, self.toggle_mic)

                        try:
                            self.core.voice.speak_native(content, on_finished=_after_speak)
                        except Exception as exc:
                            self.root.after(0, lambda e=str(exc): self._set_bubble(f"Voice output error: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

    def toggle_mic(self) -> None:
        # Instant barge-in: if RYNA is speaking right now, cut her off immediately and listen!
        if self.core.voice.get_state() == "speaking":
            self.interrupt_now()

        if self.is_listening:
            self.core.voice.recognizer.stop_recording()
            return

        self.is_listening = True
        self.mic_btn.config(bg="#0284c7", fg="#ffffff")
        self._set_bubble("Listening… speak naturally.")

        def _work() -> None:
            try:
                transcript = self.core.voice.listen_native(on_level=self._on_mic_level)
                self.is_listening = False
                self.root.after(0, lambda: self.mic_btn.config(bg="#0e2545", fg=CYAN))
                if transcript:
                    self.core.record_activity("voice", "voice_transcript", f'Heard: "{transcript[:90]}"', "ok")
                    self.root.after(0, lambda t=transcript: self.send_user_text(t, from_voice=True))
                else:
                    self.root.after(0, lambda: self._set_bubble("I didn't catch that. Click mic to speak again."))
            except Exception as exc:
                self.is_listening = False
                self.root.after(0, lambda: self.mic_btn.config(bg="#0e2545", fg=CYAN))
                self.root.after(0, lambda e=str(exc): self._set_bubble(f"Mic error: {e}"))

        threading.Thread(target=_work, daemon=True).start()

    def _on_mic_level(self, lvl: float) -> None:
        self.mic_level = lvl

    def interrupt_now(self) -> None:
        res = self.core.voice.interrupt("User interrupted via desktop UI")
        self.core.events.publish("robot_state", {"state": res["new_state"]})
        self._set_bubble("Interrupted — I'm listening.")

    def toggle_hud(self) -> None:
        self.hud_open = not self.hud_open
        if self.hud_open:
            self.hud.deiconify()
            self.hud.lift()
            self.entry.focus_set()
        else:
            self.hud.withdraw()

    def hide_to_tray(self) -> None:
        self.visible = False
        self.root.withdraw()
        self.bubble.withdraw()
        self.hud.withdraw()

    def show_from_tray(self) -> None:
        self.visible = True
        self.root.deiconify()
        self.bubble.deiconify()
        if self.hud_open:
            self.hud.deiconify()
        self.root.lift()

    def _open_control_center(self) -> None:
        """Native compact Control Center window for Memory, Tasks, and Settings (no browser needed)."""
        win = tk.Toplevel(self.root)
        win.title("RYNA Control Center — Memory · Tasks · Settings")
        win.configure(bg=BG_GLASS)
        win.geometry("620x460")
        win.attributes("-topmost", True)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # Tab 1: Memory
        mem_tab = tk.Frame(nb, bg=BG_GLASS, padx=10, pady=10)
        nb.add(mem_tab, text="Memory")
        mem_list = tk.Listbox(mem_tab, bg=BG_CARD, fg=TEXT_MAIN, relief="flat", font=("Segoe UI", 9))
        mem_list.pack(fill="both", expand=True)

        def _reload_mem() -> None:
            mem_list.delete(0, "end")
            for m in self.core.memory.list(limit=100):
                mem_list.insert("end", f"[{m['category']}/{m['memory_type']}] {m['content']}")

        _reload_mem()
        m_row = tk.Frame(mem_tab, bg=BG_GLASS, pady=6)
        m_row.pack(fill="x")
        m_entry = tk.Entry(m_row, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=CYAN, relief="flat")
        m_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        def _add_mem() -> None:
            txt = m_entry.get().strip()
            if txt:
                try:
                    self.core.memory.add("personal", txt, "confirmed_fact")
                    m_entry.delete(0, "end")
                    _reload_mem()
                except Exception as exc:
                    self._set_bubble(str(exc))

        tk.Button(m_row, text="Save Memory", command=_add_mem, bg=BLUE, fg="#fff", relief="flat").pack(side="right")

        # Tab 2: Tasks
        task_tab = tk.Frame(nb, bg=BG_GLASS, padx=10, pady=10)
        nb.add(task_tab, text="Tasks")
        t_list = tk.Listbox(task_tab, bg=BG_CARD, fg=TEXT_MAIN, relief="flat", font=("Segoe UI", 9))
        t_list.pack(fill="both", expand=True)

        def _reload_tasks() -> None:
            t_list.delete(0, "end")
            for t in self.core.tasks.list_tasks(limit=80):
                t_list.insert("end", f"[{t['status']}] {t['title']} — {t['goal']}")

        _reload_tasks()
        t_row = tk.Frame(task_tab, bg=BG_GLASS, pady=6)
        t_row.pack(fill="x")
        t_entry = tk.Entry(t_row, bg=BG_INPUT, fg=TEXT_MAIN, insertbackground=CYAN, relief="flat")
        t_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))

        def _run_new_task() -> None:
            g = t_entry.get().strip()
            if g:
                t = self.core.tasks.create_task(g[:44], g)
                self.core.tasks.run_task(t["id"])
                t_entry.delete(0, "end")
                _reload_tasks()

        tk.Button(t_row, text="Run Task", command=_run_new_task, bg=BLUE, fg="#fff", relief="flat").pack(side="right")

        # Tab 3: Settings (AI, Voice, Computer Control, Backup)
        set_tab = tk.Frame(nb, bg=BG_GLASS, padx=12, pady=12)
        nb.add(set_tab, text="Settings")
        s = self.core.db.get_all_settings()

        v_auto = tk.BooleanVar(value=bool(s.get("voice.auto_speak", True)))
        v_cloud = tk.BooleanVar(value=bool(s.get("ai.allow_cloud", False)))
        v_apps = tk.BooleanVar(value=bool(s.get("computer.allow_app_launch", True)))
        v_screen = tk.BooleanVar(value=bool(s.get("computer.allow_screen_vision", True)))

        for txt, var in (
            ("Automatically speak RYNA's replies aloud (Windows SAPI offline)", v_auto),
            ("Allow Cloud AI providers when Online (keys from .env)", v_cloud),
            ("Allow RYNA to launch allowlisted Windows applications", v_apps),
            ("Allow RYNA to capture screen when asked (mss)", v_screen),
        ):
            tk.Checkbutton(set_tab, text=txt, variable=var, bg=BG_GLASS, fg=TEXT_MAIN, selectcolor=BG_CARD, activebackground=BG_GLASS, activeforeground=CYAN).pack(anchor="w", pady=4)

        tk.Label(set_tab, text="Active Local Ollama Model:", fg=TEXT_MUTED, bg=BG_GLASS).pack(anchor="w", pady=(10, 2))
        st = self.core.status_summary()
        models = st["ai"]["providers"]["ollama"].get("models", [])
        mod_var = tk.StringVar(value=str(s.get("ai.local_model", "")) or (models[0] if models else ""))
        ttk.Combobox(set_tab, textvariable=mod_var, values=models, state="readonly" if models else "normal").pack(fill="x", pady=(0, 8))

        tk.Label(set_tab, text="Windows SAPI Voice:", fg=TEXT_MUTED, bg=BG_GLASS).pack(anchor="w", pady=(4, 2))
        sapi_voices = st["voice"].get("sapi_voices", [])
        voice_var = tk.StringVar(value=str(s.get("voice.selected_voice", "Microsoft Zira Desktop")))
        ttk.Combobox(set_tab, textvariable=voice_var, values=sapi_voices, state="readonly" if sapi_voices else "normal").pack(fill="x", pady=(0, 10))

        def _save_all() -> None:
            self.core.db.update_settings({
                "voice.auto_speak": v_auto.get(),
                "ai.allow_cloud": v_cloud.get(),
                "computer.allow_app_launch": v_apps.get(),
                "computer.allow_screen_vision": v_screen.get(),
                "ai.local_model": mod_var.get().strip(),
                "voice.selected_voice": voice_var.get().strip(),
            })
            self._refresh_hud_status()
            self._set_bubble("Settings saved.")

        btn_row = tk.Frame(set_tab, bg=BG_GLASS)
        btn_row.pack(fill="x", pady=6)
        tk.Button(btn_row, text="Save Settings", command=_save_all, bg=BLUE, fg="#fff", relief="flat", padx=12, pady=5).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Test Voice", command=lambda: self.core.voice.speak_native("Hello. I am RYNA, and my voice is working."), bg=BG_CARD, fg=CYAN, relief="flat", padx=12, pady=5).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Backup DB", command=lambda: self._set_bubble(f"Saved backup: {Path(self.core.create_backup()['backup_path']).name}"), bg=BG_CARD, fg=TEXT_MAIN, relief="flat", padx=12, pady=5).pack(side="left")

    # --- 4. System Tray ---
    def _start_tray(self) -> None:
        def _run_tray() -> None:
            try:
                import pystray

                icon_img = Image.open(self.head_png).convert("RGBA") if self.head_png.is_file() else Image.new("RGBA", (64, 64), (34, 211, 238, 255))
                menu = pystray.Menu(
                    pystray.MenuItem("Show RYNA", lambda: self.root.after(0, self.show_from_tray)),
                    pystray.MenuItem("Speak to RYNA (Mic)", lambda: self.root.after(0, self.toggle_mic)),
                    pystray.MenuItem("Toggle Chat HUD", lambda: self.root.after(0, self.toggle_hud)),
                    pystray.MenuItem("Interrupt Speech", lambda: self.root.after(0, self.interrupt_now)),
                    pystray.MenuItem("Hide to Tray", lambda: self.root.after(0, self.hide_to_tray)),
                    pystray.MenuItem("Exit RYNA", lambda: self.root.after(0, self.quit_app)),
                )
                self._tray_icon = pystray.Icon("RYNA", icon_img, "RYNA — Desktop AI Robot", menu)
                self._tray_icon.run()
            except Exception:
                pass

        threading.Thread(target=_run_tray, daemon=True).start()

    def _event_loop(self) -> None:
        while self.running:
            try:
                msg = self._event_sub.get(timeout=1.0)
                if msg["type"] == "robot_state":
                    self.state = str(msg["payload"].get("state", "idle"))
            except Exception:
                continue

    def _animate(self) -> None:
        if not self.running:
            return
        self.state = self.core.voice.get_state()
        accent = STATE_COLORS.get(self.state, CYAN)
        self.phase += 0.11 if self.state in ("listening", "thinking", "speaking", "working") else 0.045
        bob = math.sin(self.phase) * (5.0 if self.state != "sleeping" else 1.5)

        c = self.canvas
        c.delete("all")
        s = self.robot_size
        cx = s / 2

        # Subtle cyan energy hover halo beneath RYNA's feet (matching reference screenshot!)
        halo_w = s * (0.30 + (0.06 * math.sin(self.phase * 1.4)) + self.mic_level * 0.08)
        halo_y = s - 8
        c.create_oval(cx - halo_w, halo_y - 6, cx + halo_w, halo_y + 6, outline=accent, width=2.2)
        c.create_oval(cx - halo_w * 0.65, halo_y - 3, cx + halo_w * 0.65, halo_y + 3, fill=accent, outline="")

        if self._robot_photo is not None:
            c.create_image(cx, s * 0.48 + bob, image=self._robot_photo)
        else:
            c.create_rectangle(12, 12, s - 12, s - 12, fill=BG_GLASS, outline=accent, width=1.6)
            c.create_text(cx, s / 2, text="RYNA\nMissing Asset", fill=TEXT_MAIN, font=("Segoe UI", 9, "bold"), justify="center")

        # Update bubble wave indicator
        wave_char = "◉" if self.state == "listening" else ("◈" if self.state == "speaking" else "●")
        self._bubble_wave.config(text=wave_char, fg=accent)
        self._bubble_state_lbl.config(text=self.state.upper(), fg=accent)

        self.root.after(42, self._animate)

    def quit_app(self) -> None:
        self.running = False
        self.core.voice.interrupt("Application exiting")
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
    RynaDesktopApp(core).run()
