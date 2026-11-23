"""Native animated Windows Desktop Companion for RYNA 0.2.0 (Tkinter + Live Local Engine Session).

Features:
- Minimal obsidian/cyan floating orb matching the redesigned RYNA UI
- Movable, resizable, edge-aware, configurable transparency & always-on-top
- Synchronized to all 8 canonical application states:
  idle, listening, processing, speaking, executing, waiting, offline, error
- Click-to-interact inline prompt bar wired directly to the SAME local AI engine & SQLite session
  (with optional Windows SAPI spoken reply and instant Interrupt)
- Double-click to expand into the full RYNA interface
"""

from __future__ import annotations

import json
import math
import threading
import tkinter as tk
import urllib.request
import webbrowser
from typing import Any

STATE_STYLES: dict[str, dict[str, Any]] = {
    "idle":       {"accent": "#38bdf8", "ring": "#0284c7", "speed": 0.035, "label": "IDLE"},
    "listening":  {"accent": "#22d3ee", "ring": "#38bdf8", "speed": 0.10,  "label": "LISTENING"},
    "processing": {"accent": "#818cf8", "ring": "#38bdf8", "speed": 0.14,  "label": "PROCESSING"},
    "speaking":   {"accent": "#34d399", "ring": "#22d3ee", "speed": 0.15,  "label": "SPEAKING"},
    "executing":  {"accent": "#60a5fa", "ring": "#818cf8", "speed": 0.13,  "label": "EXECUTING"},
    "waiting":    {"accent": "#fbbf24", "ring": "#f59e0b", "speed": 0.05,  "label": "WAITING"},
    "offline":    {"accent": "#64748b", "ring": "#334155", "speed": 0.02,  "label": "OFFLINE"},
    "error":      {"accent": "#f87171", "ring": "#ef4444", "speed": 0.16,  "label": "ERROR"},
}


class RynaCompanionWindow:
    def __init__(self, api_base_url: str = "http://127.0.0.1:8765", size: int = 176) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.size = max(120, min(280, size))
        self.alpha = 0.94
        self.always_on_top = True
        self.state = "idle"
        self.mode_label = "checking"
        self.phase = 0.0
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False
        self._running = True
        self._prompt_win: tk.Toplevel | None = None
        self._active_conv_id: str | None = None

        self.root = tk.Tk()
        self.root.title("RYNA Companion")
        self.root.overrideredirect(True)
        self.root.configure(bg="#05070c")
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", self.alpha)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - self.size - 28
        y = sh - self.size - 80
        self.root.geometry(f"{self.size}x{self.size}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.size,
            height=self.size,
            bg="#05070c",
            highlightthickness=1,
            highlightbackground="#1e2d44",
        )
        self.canvas.pack(fill="both", expand=True)

        self._bind_events()
        self._build_menu()
        self._start_polling()
        self._animate()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: webbrowser.open(self.api_base_url))
        self.canvas.bind("<Button-3>", self._show_menu)

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg="#0a0e17", fg="#eef4ff", activebackground="#162136")
        self.menu.add_command(label="Quick Ask RYNA…", command=self._toggle_quick_prompt)
        self.menu.add_command(label="Expand Full Interface", command=lambda: webbrowser.open(self.api_base_url))
        self.menu.add_separator()

        size_menu = tk.Menu(self.menu, tearoff=0, bg="#0a0e17", fg="#eef4ff")
        for label, px in (("Compact (136px)", 136), ("Standard (176px)", 176), ("Expanded (224px)", 224)):
            size_menu.add_command(label=label, command=lambda p=px: self._resize(p))
        self.menu.add_cascade(label="Size", menu=size_menu)

        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#0a0e17", fg="#eef4ff")
        for label, val in (("100%", 1.0), ("94%", 0.94), ("82%", 0.82), ("68%", 0.68)):
            alpha_menu.add_command(label=label, command=lambda v=val: self._set_alpha(v))
        self.menu.add_cascade(label="Transparency", menu=alpha_menu)

        self.menu.add_command(label="Toggle Always On Top", command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="Interrupt Speech / Generation", command=self._send_interrupt)
        self.menu.add_command(label="Close Companion", command=self.close)

    def _show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_press(self, event: tk.Event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y
        self._dragged = False

    def _on_drag(self, event: tk.Event) -> None:
        if abs(event.x - self._drag_x) > 3 or abs(event.y - self._drag_y) > 3:
            self._dragged = True
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, _event: tk.Event) -> None:
        if not self._dragged:
            self._toggle_quick_prompt()
            return
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(8, min(sw - self.size - 8, self.root.winfo_x()))
        y = max(8, min(sh - self.size - 48, self.root.winfo_y()))
        if x < 24:
            x = 12
        elif sw - (x + self.size) < 24:
            x = sw - self.size - 12
        if y < 24:
            y = 12
        self.root.geometry(f"+{x}+{y}")

    def _toggle_quick_prompt(self) -> None:
        """Open a real desktop quick-interact box wired to the same RYNA engine session."""
        if self._prompt_win and self._prompt_win.winfo_exists():
            self._prompt_win.destroy()
            self._prompt_win = None
            return

        win = tk.Toplevel(self.root)
        win.title("Ask RYNA")
        win.configure(bg="#0a0e17")
        win.attributes("-topmost", True)
        px = max(16, self.root.winfo_x() - 180)
        py = max(16, self.root.winfo_y() - 150)
        win.geometry(f"340x150+{px}+{py}")

        reply_var = tk.StringVar(value="Connected to local RYNA session.")
        lbl = tk.Label(
            win,
            textvariable=reply_var,
            bg="#0a0e17",
            fg="#9aa9c3",
            wraplength=316,
            justify="left",
            font=("Segoe UI", 9),
        )
        lbl.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        row = tk.Frame(win, bg="#0a0e17")
        row.pack(fill="x", padx=10, pady=(0, 10))
        entry = tk.Entry(row, bg="#0f1522", fg="#eef4ff", insertbackground="#38bdf8", relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        entry.focus_set()

        def _submit(_evt: Any = None) -> None:
            txt = entry.get().strip()
            if not txt:
                return
            entry.delete(0, "end")
            reply_var.set("Processing…")

            def _worker() -> None:
                try:
                    req = urllib.request.Request(
                        f"{self.api_base_url}/api/v1/chat",
                        data=json.dumps({"conversation_id": self._active_conv_id, "message": txt}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=120.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    self._active_conv_id = data.get("conversation_id")
                    content = data.get("assistant_message", {}).get("content", "")
                    win.after(0, lambda: reply_var.set(content[:240] + ("…" if len(content) > 240 else "")))
                    if data.get("ai_available") and content and not content.startswith("[Local Tool:"):
                        # Speak aloud offline via Windows SAPI
                        sreq = urllib.request.Request(
                            f"{self.api_base_url}/api/v1/voice/speak",
                            data=json.dumps({"text": content}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        urllib.request.urlopen(sreq, timeout=5.0).close()
                except Exception as exc:
                    win.after(0, lambda: reply_var.set(f"Error: {exc}"))

            threading.Thread(target=_worker, daemon=True).start()

        btn = tk.Button(row, text="Send", command=_submit, bg="#0284c7", fg="#ffffff", relief="flat", padx=10)
        btn.pack(side="right")
        entry.bind("<Return>", _submit)
        self._prompt_win = win

    def _resize(self, px: int) -> None:
        self.size = px
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self.root.geometry(f"{px}x{px}+{x}+{y}")
        self.canvas.config(width=px, height=px)

    def _set_alpha(self, val: float) -> None:
        self.alpha = val
        self.root.attributes("-alpha", val)

    def _toggle_topmost(self) -> None:
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)

    def _send_interrupt(self) -> None:
        def _post() -> None:
            try:
                req = urllib.request.Request(
                    f"{self.api_base_url}/api/v1/voice/interrupt",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1.5).close()
            except Exception:
                pass

        threading.Thread(target=_post, daemon=True).start()

    def _start_polling(self) -> None:
        def _poll() -> None:
            try:
                with urllib.request.urlopen(f"{self.api_base_url}/api/v1/status", timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.state = str(data.get("companion_state", "idle"))
                    self.mode_label = str(data.get("operating_mode", {}).get("label", ""))
            except Exception:
                self.state = "offline"
            if self._running:
                self.root.after(1200, self._start_polling)

        threading.Thread(target=_poll, daemon=True).start()

    def _animate(self) -> None:
        if not self._running:
            return
        style = STATE_STYLES.get(self.state, STATE_STYLES["idle"])
        self.phase += style["speed"]

        c = self.canvas
        c.delete("all")
        s = self.size
        cx = s / 2
        cy = s / 2 - 10
        pulse = math.sin(self.phase) * 4.0
        base_r = s * 0.25

        r_outer = base_r + 14 + pulse * 0.5
        c.create_oval(
            cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
            outline=style["ring"], width=1.6, dash=(5, 5)
        )

        start_deg = (self.phase * 48) % 360
        r_arc = base_r + 7
        c.create_arc(
            cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc,
            start=start_deg, extent=115, style="arc", outline=style["accent"], width=2.6
        )

        r_core = base_r + pulse * 0.3
        c.create_oval(
            cx - r_core, cy - r_core, cx + r_core, cy + r_core,
            fill="#0b1322", outline=style["accent"], width=2.2
        )
        r_inner = r_core * 0.42
        c.create_oval(
            cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
            fill=style["accent"], outline=""
        )

        c.create_text(
            cx, s - 22, text=f"RYNA · {style['label']}",
            fill="#eef4ff", font=("Segoe UI", 8.5, "bold")
        )
        self.root.after(40, self._animate)

    def close(self) -> None:
        self._running = False
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_companion(api_base_url: str = "http://127.0.0.1:8765") -> None:
    app = RynaCompanionWindow(api_base_url=api_base_url)
    app.run()


if __name__ == "__main__":
    run_companion()
