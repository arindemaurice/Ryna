"""Native animated Windows Desktop Companion for RYNA (Tkinter + Local Engine sync).

Features:
- Movable, resizable, frameless dark futuristic orb window
- Desktop edge awareness (snaps gently within 18px of screen edges)
- Configurable transparency and always-on-top mode
- All 8 required visual states: idle, listening, thinking, speaking, working, waiting, alert, sleeping
- Syncs live state with RYNA local HTTP API
- Double-click to open full RYNA interface; right-click context menu for controls
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
    "idle": {"accent": "#22d3ee", "ring": "#0ea5e9", "speed": 0.04, "label": "IDLE"},
    "listening": {"accent": "#38bdf8", "ring": "#06b6d4", "speed": 0.11, "label": "LISTENING"},
    "thinking": {"accent": "#818cf8", "ring": "#38bdf8", "speed": 0.15, "label": "THINKING"},
    "speaking": {"accent": "#2dd4bf", "ring": "#22d3ee", "speed": 0.18, "label": "SPEAKING"},
    "working": {"accent": "#60a5fa", "ring": "#2563eb", "speed": 0.14, "label": "WORKING"},
    "waiting": {"accent": "#fbbf24", "ring": "#f59e0b", "speed": 0.05, "label": "WAITING"},
    "alert": {"accent": "#fb7185", "ring": "#ef4444", "speed": 0.22, "label": "ALERT"},
    "sleeping": {"accent": "#64748b", "ring": "#334155", "speed": 0.02, "label": "SLEEPING"},
}


class RynaCompanionWindow:
    def __init__(self, api_base_url: str = "http://127.0.0.1:8765", size: int = 176) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.size = max(120, min(320, size))
        self.alpha = 0.94
        self.always_on_top = True
        self.state = "idle"
        self.online = False
        self.phase = 0.0
        self._drag_x = 0
        self._drag_y = 0
        self._running = True

        self.root = tk.Tk()
        self.root.title("RYNA Companion")
        self.root.overrideredirect(True)
        self.root.configure(bg="#09111f")
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", self.alpha)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - self.size - 32
        y = sh - self.size - 84
        self.root.geometry(f"{self.size}x{self.size}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.size,
            height=self.size,
            bg="#09111f",
            highlightthickness=1,
            highlightbackground="#1e3a5f",
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
        self.menu = tk.Menu(self.root, tearoff=0, bg="#0f172a", fg="#e2e8f0")
        self.menu.add_command(label="Open Full RYNA Assistant", command=lambda: webbrowser.open(self.api_base_url))
        self.menu.add_separator()

        state_menu = tk.Menu(self.menu, tearoff=0, bg="#0f172a", fg="#e2e8f0")
        for st in STATE_STYLES:
            state_menu.add_command(label=st.capitalize(), command=lambda s=st: self._push_state(s))
        self.menu.add_cascade(label="Set Companion State", menu=state_menu)

        size_menu = tk.Menu(self.menu, tearoff=0, bg="#0f172a", fg="#e2e8f0")
        for label, px in (("Compact (136px)", 136), ("Standard (176px)", 176), ("Expanded (224px)", 224)):
            size_menu.add_command(label=label, command=lambda p=px: self._resize(p))
        self.menu.add_cascade(label="Size", menu=size_menu)

        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#0f172a", fg="#e2e8f0")
        for label, val in (("100%", 1.0), ("94%", 0.94), ("80%", 0.80), ("65%", 0.65)):
            alpha_menu.add_command(label=label, command=lambda v=val: self._set_alpha(v))
        self.menu.add_cascade(label="Transparency", menu=alpha_menu)

        self.menu.add_command(label="Toggle Always On Top", command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="Interrupt / Stop Speech", command=self._send_interrupt)
        self.menu.add_command(label="Hide / Close Companion", command=self.close)

    def _show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_press(self, event: tk.Event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, _event: tk.Event) -> None:
        # Desktop edge awareness: keep inside bounds & snap cleanly near edges
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

    def _push_state(self, state: str) -> None:
        self.state = state

        def _post() -> None:
            try:
                req = urllib.request.Request(
                    f"{self.api_base_url}/api/v1/voice/state",
                    data=json.dumps({"state": state}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1.5).close()
            except Exception:
                pass

        threading.Thread(target=_post, daemon=True).start()

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
                with urllib.request.urlopen(f"{self.api_base_url}/api/v1/status", timeout=1.2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.state = str(data.get("companion_state", "idle"))
                    self.online = bool(data.get("online", False))
            except Exception:
                pass
            if self._running:
                self.root.after(1500, self._start_polling)

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
        cy = s / 2 - 8
        pulse = math.sin(self.phase) * 4.5
        base_r = s * 0.27

        # Outer orbital rings
        r_outer = base_r + 14 + pulse * 0.6
        c.create_oval(
            cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
            outline=style["ring"], width=2, dash=(6, 4)
        )

        # Rotating energy arc
        start_deg = (self.phase * 48) % 360
        r_arc = base_r + 7
        c.create_arc(
            cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc,
            start=start_deg, extent=115, style="arc", outline=style["accent"], width=3
        )
        c.create_arc(
            cx - r_arc, cy - r_arc, cx + r_arc, cy + r_arc,
            start=(start_deg + 180) % 360, extent=75, style="arc", outline=style["ring"], width=2
        )

        # Core sphere
        r_core = base_r + pulse * 0.35
        c.create_oval(
            cx - r_core, cy - r_core, cx + r_core, cy + r_core,
            fill="#0f1e36", outline=style["accent"], width=2.5
        )
        r_inner = r_core * 0.45
        c.create_oval(
            cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
            fill=style["accent"], outline=""
        )

        # Identity & state caption
        c.create_text(
            cx, s - 22, text=f"RYNA • {style['label']}",
            fill="#e2e8f0", font=("Segoe UI", 9, "bold")
        )
        mode_color = "#22c55e" if self.online else "#f59e0b"
        c.create_oval(10, 10, 18, 18, fill=mode_color, outline="")

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
