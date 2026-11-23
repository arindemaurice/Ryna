"""Modular Windows desktop control & screen vision (mss capture, active window inspection, real app launcher)."""

from __future__ import annotations

import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.store import RynaDatabase

# Safe allowlisted Windows apps & URI schemes
APP_LAUNCH_SPECS: dict[str, dict[str, Any]] = {
    "spotify": {"uri": "spotify:", "exe_candidates": [r"%APPDATA%\Spotify\Spotify.exe"]},
    "chrome": {"exe_candidates": [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"], "cmd": "chrome"},
    "edge": {"exe_candidates": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"], "cmd": "msedge"},
    "browser": {"url": "https://www.google.com"},
    "vscode": {"cmd": "code", "exe_candidates": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"]},
    "vs code": {"cmd": "code", "exe_candidates": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"]},
    "notepad": {"cmd": "notepad.exe"},
    "calculator": {"cmd": "calc.exe"},
    "explorer": {"cmd": "explorer.exe"},
    "file explorer": {"cmd": "explorer.exe"},
    "paint": {"cmd": "mspaint.exe"},
    "settings": {"uri": "ms-settings:"},
    "discord": {"uri": "discord:", "exe_candidates": [r"%LOCALAPPDATA%\Discord\Update.exe"]},
    "whatsapp": {"uri": "whatsapp:"},
    "word": {"cmd": "winword.exe"},
    "excel": {"cmd": "excel.exe"},
}


class ComputerController:
    def __init__(self, db: RynaDatabase, screenshots_dir: Path | None = None) -> None:
        self.db = db
        self.screenshots_dir = screenshots_dir or (Path.cwd() / "data" / "screenshots")

    def active_window_title(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"supported": False, "title": "", "detail": "Win32 required."}
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetForegroundWindow()
            ln = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(ln + 1)
            u32.GetWindowTextW(hwnd, buf, ln + 1)
            return {"supported": True, "title": buf.value, "hwnd": int(hwnd)}
        except Exception as exc:
            return {"supported": False, "title": "", "detail": str(exc)}

    def capture_screen(self) -> dict[str, Any]:
        if not bool(self.db.get_setting("computer.allow_screen_vision", True)):
            raise PermissionError("Screen vision is disabled in Settings (computer.allow_screen_vision = false).")
        try:
            import mss
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Screen capture requires mss and Pillow.") from exc

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = self.screenshots_dir / f"screen_{ts}.png"
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                img.save(out_path, format="PNG")
        except Exception:
            from PIL import ImageGrab

            img = ImageGrab.grab(all_screens=False)
            img.save(out_path, format="PNG")
        win = self.active_window_title()
        return {
            "captured": True,
            "path": str(out_path),
            "width": img.width,
            "height": img.height,
            "active_window": win.get("title", ""),
        }

    def launch_application(self, app_name: str) -> dict[str, Any]:
        if not bool(self.db.get_setting("computer.allow_app_launch", True)):
            raise PermissionError("Application launching is disabled in Settings (computer.allow_app_launch = false).")
        key = app_name.strip().lower()
        if key not in APP_LAUNCH_SPECS:
            raise ValueError(f"Application '{app_name}' is not in the allowlist: {sorted(APP_LAUNCH_SPECS)}.")
        spec = APP_LAUNCH_SPECS[key]

        if "url" in spec:
            webbrowser.open(spec["url"])
            return {"launched": True, "app": key, "method": "browser_url"}

        for cand in spec.get("exe_candidates", []):
            expanded = os.path.expandvars(cand)
            if os.path.isfile(expanded):
                proc = subprocess.Popen([expanded])
                return {"launched": True, "app": key, "pid": proc.pid, "path": expanded}

        if "cmd" in spec:
            found = shutil.which(spec["cmd"])
            if found:
                proc = subprocess.Popen([found])
                return {"launched": True, "app": key, "pid": proc.pid, "path": found}

        if "uri" in spec and sys.platform == "win32":
            os.startfile(spec["uri"])  # type: ignore[attr-defined]
            return {"launched": True, "app": key, "method": f"uri:{spec['uri']}"}

        raise FileNotFoundError(f"Could not find '{app_name}' installed on this Windows machine.")

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": sys.platform,
            "active_window_inspection": sys.platform == "win32",
            "app_launch_enabled": bool(self.db.get_setting("computer.allow_app_launch", True)),
            "allowed_apps": sorted(APP_LAUNCH_SPECS),
            "screen_vision_enabled": bool(self.db.get_setting("computer.allow_screen_vision", True)),
            "screen_capture_installed": importlib.util.find_spec("mss") is not None and importlib.util.find_spec("PIL") is not None,
            "input_control_enabled": bool(self.db.get_setting("computer.allow_input_control", False)),
            "detail": "Real Windows active-window inspection, mss screen capture, and allowlisted app launching active.",
        }
