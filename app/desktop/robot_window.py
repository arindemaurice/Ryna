"""Native transparent Windows Desktop Robot Host for RYNA.

Strictly honors Section 4 ("THE ROBOT IS RYNA"):
- Loads ONLY the isolated user-supplied robot asset (`app/desktop/assets/ryna_robot.png`).
- NEVER fabricates a replacement sphere or generic AI mascot if the asset is missing.
- Supports transparent background on Windows, drag/reposition with persisted coordinates in SQLite,
  resizing, edge awareness, always-on-top toggle, all 13 live robot states, click-to-interact
  wired to the exact same RYNA AI session & Windows SAPI voice, and double-click to open full UI.
"""

from __future__ import annotations

import json
import math
import threading
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

STATE_ACCENT: dict[str, str] = {
    "idle": "#38bdf8",
    "listening": "#22d3ee",
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

TRANSPARENT_KEY = "#010203"


class RynaRobotWindow:
    def __init__(self, api_base_url: str, asset_path: Path) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.asset_path = Path(asset_path)
        self.size = 180
        self.alpha = 0.96
        self.always_on_top = True
        self.state = "idle"
        self.phase = 0.0
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False
        self._running = True
        self._conv_id: str | None = None
        self._prompt_win: tk.Toplevel | None = None
        self._photo: tk.PhotoImage | None = None

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

        x, y = self._load_saved_geometry()
        self.root.geometry(f"{self.size}x{self.size + 32}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.size,
            height=self.size + 32,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._load_robot_image()
        self._bind_events()
        self._build_menu()
        self._poll_status()
        self._animate()

    def _load_saved_geometry(self) -> tuple[int, int]:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        default_x, default_y = sw - self.size - 32, sh - self.size - 96
        try:
            with urllib.request.urlopen(f"{self.api_base_url}/api/v1/settings", timeout=1.5) as resp:
                s = json.loads(resp.read().decode("utf-8")).get("settings", {})
            self.size = max(120, min(320, int(s.get("desktop.robot_size", 180))))
            self.alpha = float(s.get("desktop.robot_transparency", 0.96))
            self.always_on_top = bool(s.get("desktop.robot_always_on_top", True))
            self.root.attributes("-topmost", self.always_on_top)
            self.root.attributes("-alpha", self.alpha)
            sx, sy = int(s.get("desktop.robot_x", -1)), int(s.get("desktop.robot_y", -1))
            if 0 <= sx <= sw - 60 and 0 <= sy <= sh - 60:
                return sx, sy
        except Exception:
            pass
        return default_x, default_y

    def _save_settings_async(self, patch: dict[str, Any]) -> None:
        def _run() -> None:
            try:
                req = urllib.request.Request(
                    f"{self.api_base_url}/api/v1/settings",
                    data=json.dumps({"settings": patch}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                urllib.request.urlopen(req, timeout=1.5).close()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _load_robot_image(self) -> None:
        self._photo = None
        if not self.asset_path.is_file():
            return
        try:
            # Prefer Pillow for crisp antialias resize if installed; fall back to Tk native PhotoImage
            from PIL import Image, ImageTk  # type: ignore[import-not-found]

            img = Image.open(self.asset_path).convert("RGBA")
            img.thumbnail((self.size - 16, self.size - 16))
            self._photo = ImageTk.PhotoImage(img)
        except Exception:
            try:
                raw = tk.PhotoImage(file=str(self.asset_path))
                factor = max(1, max(raw.width(), raw.height()) // max(1, self.size - 16))
                self._photo = raw.subsample(factor, factor) if factor > 1 else raw
            except Exception:
                self._photo = None

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _e: webbrowser.open(self.api_base_url))
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0, bg="#0a0e17", fg="#eef4ff", activebackground="#162238")
        self.menu.add_command(label="Talk to RYNA…", command=self._toggle_quick_prompt)
        self.menu.add_command(label="Open Full Conversation Interface", command=lambda: webbrowser.open(self.api_base_url))
        self.menu.add_separator()

        size_menu = tk.Menu(self.menu, tearoff=0, bg="#0a0e17", fg="#eef4ff")
        for lbl, px in (("Compact (140px)", 140), ("Standard (180px)", 180), ("Large (240px)", 240)):
            size_menu.add_command(label=lbl, command=lambda p=px: self._resize(p))
        self.menu.add_cascade(label="Robot Size", menu=size_menu)

        self.menu.add_command(label="Toggle Always On Top", command=self._toggle_topmost)
        self.menu.add_command(label="Reload Robot Image", command=self._load_robot_image)
        self.menu.add_separator()
        self.menu.add_command(label="Interrupt Speaking / Generation", command=self._interrupt)
        self.menu.add_command(label="Close Desktop Robot", command=self.close)

    def _on_press(self, e: tk.Event) -> None:
        self._drag_x, self._drag_y, self._dragged = e.x, e.y, False

    def _on_drag(self, e: tk.Event) -> None:
        if abs(e.x - self._drag_x) > 3 or abs(e.y - self._drag_y) > 3:
            self._dragged = True
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, _e: tk.Event) -> None:
        if not self._dragged:
            self._toggle_quick_prompt()
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(8, min(sw - self.size - 8, self.root.winfo_x()))
        y = max(8, min(sh - self.size - 56, self.root.winfo_y()))
        if x < 24:
            x = 12
        elif sw - (x + self.size) < 24:
            x = sw - self.size - 12
        self.root.geometry(f"+{x}+{y}")
        self._save_settings_async({"desktop.robot_x": x, "desktop.robot_y": y})

    def _resize(self, px: int) -> None:
        self.size = px
        self.root.geometry(f"{px}x{px + 32}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self.canvas.config(width=px, height=px + 32)
        self._load_robot_image()
        self._save_settings_async({"desktop.robot_size": px})

    def _toggle_topmost(self) -> None:
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self._save_settings_async({"desktop.robot_always_on_top": self.always_on_top})

    def _interrupt(self) -> None:
        def _post() -> None:
            try:
                req = urllib.request.Request(f"{self.api_base_url}/api/v1/voice/interrupt", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=1.5).close()
            except Exception:
                pass

        threading.Thread(target=_post, daemon=True).start()

    def _toggle_quick_prompt(self) -> None:
        if self._prompt_win and self._prompt_win.winfo_exists():
            self._prompt_win.destroy()
            self._prompt_win = None
            return
        win = tk.Toplevel(self.root)
        win.title("RYNA")
        win.configure(bg="#0a0e17")
        win.attributes("-topmost", True)
        win.geometry(f"350x155+{max(12, self.root.winfo_x() - 180)}+{max(12, self.root.winfo_y() - 165)}")

        out_var = tk.StringVar(value="Connected to your RYNA session.")
        tk.Label(win, textvariable=out_var, bg="#0a0e17", fg="#9aa9c3", wraplength=324, justify="left", font=("Segoe UI", 9)).pack(fill="both", expand=True, padx=10, pady=(10, 4))

        row = tk.Frame(win, bg="#0a0e17")
        row.pack(fill="x", padx=10, pady=(0, 10))
        entry = tk.Entry(row, bg="#101726", fg="#eef4ff", insertbackground="#38bdf8", relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        entry.focus_set()

        def _send(_evt: Any = None) -> None:
            txt = entry.get().strip()
            if not txt:
                return
            entry.delete(0, "end")
            out_var.set("Thinking…")

            def _work() -> None:
                try:
                    req = urllib.request.Request(
                        f"{self.api_base_url}/api/v1/chat",
                        data=json.dumps({"conversation_id": self._conv_id, "message": txt}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=120.0) as r:
                        data = json.loads(r.read().decode("utf-8"))
                    self._conv_id = data.get("conversation_id")
                    content = data.get("assistant_message", {}).get("content", "")
                    win.after(0, lambda: out_var.set(content[:250] + ("…" if len(content) > 250 else "")))
                    if data.get("ai_available") and content and not content.startswith("[Tool:"):
                        sreq = urllib.request.Request(f"{self.api_base_url}/api/v1/voice/speak", data=json.dumps({"text": content}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
                        urllib.request.urlopen(sreq, timeout=5.0).close()
                except Exception as exc:
                    win.after(0, lambda: out_var.set(f"Error: {exc}"))

            threading.Thread(target=_work, daemon=True).start()

        tk.Button(row, text="Send", command=_send, bg="#0284c7", fg="#ffffff", relief="flat", padx=10).pack(side="right")
        entry.bind("<Return>", _send)
        self._prompt_win = win

    def _poll_status(self) -> None:
        def _work() -> None:
            try:
                with urllib.request.urlopen(f"{self.api_base_url}/api/v1/status", timeout=1.5) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    self.state = str(d.get("robot_state", "idle"))
            except Exception:
                self.state = "waiting"
            if self._running:
                self.root.after(1200, self._poll_status)

        threading.Thread(target=_work, daemon=True).start()

    def _animate(self) -> None:
        if not self._running:
            return
        self.phase += 0.08 if self.state in ("listening", "thinking", "speaking", "working") else 0.03
        bob = math.sin(self.phase) * (4.0 if self.state != "sleeping" else 1.0)
        accent = STATE_ACCENT.get(self.state, "#38bdf8")

        c = self.canvas
        c.delete("all")
        cx = self.size / 2

        if self._photo is not None:
            c.create_image(cx, self.size / 2 + bob, image=self._photo)
        else:
            # Honest missing-reference notice (strictly NO fabricated robot or sphere)
            c.create_rectangle(10, 10, self.size - 10, self.size - 10, fill="#0a0e17", outline=accent, width=1.6)
            c.create_text(cx, self.size * 0.38, text="RYNA", fill="#eef4ff", font=("Segoe UI", 12, "bold"))
            c.create_text(cx, self.size * 0.58, text="Awaiting Robot\nReference PNG", fill="#9aa9c3", font=("Segoe UI", 8.5), justify="center")

        # Clean state status pill beneath robot
        c.create_rectangle(18, self.size + 4, self.size - 18, self.size + 26, fill="#0a0e17", outline=accent, width=1.2)
        c.create_text(cx, self.size + 15, text=f"RYNA · {self.state.upper()}", fill=accent, font=("Segoe UI", 8, "bold"))
        self.root.after(45, self._animate)

    def close(self) -> None:
        self._running = False
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_robot_host(api_base_url: str, asset_path: Path) -> None:
    RynaRobotWindow(api_base_url=api_base_url, asset_path=asset_path).run()
