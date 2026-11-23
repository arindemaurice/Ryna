"""Modular Windows desktop control & screen vision boundaries (strictly permission-gated)."""

from __future__ import annotations

import ctypes
import importlib.util
import subprocess
import sys
from typing import Any

from database.store import RynaDatabase

# Safe allowlist for Level 2 confirmed application launching
SAFE_WINDOWS_APPS: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "paint": ["mspaint.exe"],
}


class ComputerController:
    def __init__(self, db: RynaDatabase) -> None:
        self.db = db

    def active_window_title(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"supported": False, "title": "", "detail": "Windows foreground inspection requires Win32."}
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return {"supported": True, "title": buf.value, "hwnd": int(hwnd)}
        except Exception as exc:
            return {"supported": False, "title": "", "detail": str(exc)}

    def launch_application(self, app_name: str) -> dict[str, Any]:
        if not bool(self.db.get_setting("computer.allow_app_launch", False)):
            raise PermissionError("Application launching is disabled in Settings (computer.allow_app_launch = false).")
        key = app_name.strip().lower()
        if key not in SAFE_WINDOWS_APPS:
            raise ValueError(f"Application '{app_name}' is not in the safe allowlist: {sorted(SAFE_WINDOWS_APPS)}.")
        if sys.platform != "win32":
            raise RuntimeError("Windows app launching requires Windows.")
        proc = subprocess.Popen(SAFE_WINDOWS_APPS[key])
        return {"launched": True, "app": key, "pid": proc.pid}

    def capabilities(self) -> dict[str, Any]:
        has_pillow = importlib.util.find_spec("PIL") is not None
        return {
            "platform": sys.platform,
            "active_window_inspection": sys.platform == "win32",
            "app_launch_enabled": bool(self.db.get_setting("computer.allow_app_launch", False)),
            "allowed_apps": sorted(SAFE_WINDOWS_APPS),
            "screen_vision_enabled": bool(self.db.get_setting("computer.allow_screen_vision", False)),
            "screen_capture_backend_installed": has_pillow,
            "input_control_enabled": bool(self.db.get_setting("computer.allow_input_control", False)),
            "input_control_implemented": False,
            "detail": "Active window inspection and allowlisted app launching are wired; mouse/keyboard automation and OCR/VLM screen capture require explicit opt-in and optional dependencies.",
        }
