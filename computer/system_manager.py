"""RYNA Windows System Management Engine (100% RYNA identity).

Provides safe, verified local system management capabilities:
- Safe user-profile file management (Desktop, Documents, Downloads, Pictures, RYNA-Workspace)
  with executable-write blocking, symlink escape prevention, and Recycle-Bin-only delete.
- Offline Windows 10/11 built-in WinRT OCR (read screen or image text locally, zero cloud).
- Live system telemetry & proactive health monitor (CPU, RAM, Disk, Battery, Uptime, Alerts).
- Rule-based RYNA Technical Doctor (diagnoses recent errors without guessing).
- Safe two-step Windows Power management (Shutdown, Restart, Sign Out + instant Cancel).
- Active-app direct typing/dictation via native Win32 SendInput (refuses to type into RYNA itself).
- Free key-less live Weather lookup via Open-Meteo (with honest offline notice).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from computer.controller import _win_type_unicode

WRITE_BLOCKED_EXT = {
    ".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".msi",
    ".lnk", ".scr", ".com", ".jar", ".reg", ".sh", ".command", ".appx",
}
READ_ALLOWED_EXT = {
    ".txt", ".md", ".csv", ".json", ".html", ".xml", ".yaml", ".yml", ".log",
    ".js", ".ts", ".css", ".py", ".sql", ".toml", ".ini", ".c", ".cpp", ".h",
    ".java", ".go", ".rs", ".rtf",
}
SKIP_DIRS = {"node_modules", ".git", "$recycle.bin", "appdata", "__pycache__", ".venv"}
MAX_READ_BYTES = 200 * 1024
MAX_WRITE_BYTES = 100 * 1024

WMO_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "heavy showers", 95: "thunderstorm", 96: "thunderstorm with hail",
}

DOCTOR_RULES: list[dict[str, Any]] = [
    {"id": "ollama-down", "re": re.compile(r"11434|ollama.*(not running|refused|unreachable)|connection refused", re.I),
     "cause": "Local Ollama engine is not running or unreachable on 127.0.0.1:11434.",
     "fix": "Start Ollama or run 'ollama serve', then confirm a model is installed with 'ollama list'."},
    {"id": "model-missing", "re": re.compile(r"model .* not found|no such model|pull the model", re.I),
     "cause": "The selected local AI model is not downloaded on this PC.",
     "fix": "Run 'ollama pull qwen2.5:1.5b' or pick an installed model in RYNA's right-click Monitor & Settings."},
    {"id": "bad-key", "re": re.compile(r"\b(401|403)\b.*(unauthori|forbidden|api key)|invalid[_ ]api[_ ]key", re.I),
     "cause": "A cloud AI provider rejected the API key.",
     "fix": "Check your API key in .env or switch RYNA to offline local mode."},
    {"id": "offline", "re": re.compile(r"getaddrinfo|ENOTFOUND|network is unreachable|timed out|urlopen error", re.I),
     "cause": "No working internet connection was detected.",
     "fix": "Check Wi-Fi or Ethernet. RYNA's local AI, voice, files, and system controls continue working offline."},
    {"id": "permission", "re": re.compile(r"PermissionError|Access is denied|EACCES|EPERM", re.I),
     "cause": "Windows denied access to a file, folder, or protected action.",
     "fix": "Make sure the file is not locked by another app and stays inside your user folders."},
    {"id": "disk-full", "re": re.compile(r"ENOSPC|no space left|disk is full", re.I),
     "cause": "System drive is out of free disk space.",
     "fix": "Empty the Recycle Bin or clean large files from Downloads."},
    {"id": "mic", "re": re.compile(r"microphone|sounddevice|PortAudio|no input device", re.I),
     "cause": "Microphone access failed or no input device was detected.",
     "fix": "Check Windows Settings -> Privacy -> Microphone and allow desktop apps."},
]

WINRT_OCR_PS = r"""
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { throw 'No Windows OCR language pack is installed.' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Text
"""


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


class RynaSystemManager:
    """Real Windows system management engine for RYNA (files, OCR, telemetry, monitor, power, weather, dictation)."""

    def __init__(self, project_root: Path, home_dir: Path | None = None) -> None:
        self.project_root = project_root
        self.home_dir = home_dir or Path.home()
        self._pending_power: dict[str, Any] | None = None
        self._last_alerts: dict[str, float] = {}
        self._alert_history: list[dict[str, Any]] = []

    def _run_ps(self, script: str, timeout: float = 12.0) -> str:
        if sys.platform != "win32":
            raise RuntimeError("Windows system actions require Windows.")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creation_flags,
        )
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "Windows command failed.").strip())
        return (res.stdout or "").strip()

    # --- 1. Safe User-Profile File Management ---
    def user_roots(self) -> dict[str, Path]:
        def _pick(name: str) -> Path:
            for cand in (self.home_dir / name, self.home_dir / "OneDrive" / name):
                if cand.exists():
                    return cand.resolve()
            return (self.home_dir / name).resolve()

        return {
            "desktop": _pick("Desktop"),
            "documents": _pick("Documents"),
            "downloads": _pick("Downloads"),
            "pictures": _pick("Pictures"),
            "workspace": (self.home_dir / "RYNA-Workspace").resolve(),
        }

    def _is_inside(self, child: Path, root: Path) -> bool:
        try:
            child.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def resolve_user_path(self, raw_path: str) -> Path:
        raw = str(raw_path or "").strip().strip("\"'")
        if not raw or len(raw) > 400 or any(ord(c) < 32 for c in raw):
            raise ValueError("Specify a valid file or folder path, such as Documents/notes.txt.")
        roots = self.user_roots()
        parts = [p for p in re.split(r"[\\/]+", raw) if p]
        first = re.sub(r"^my\s+", "", parts[0].lower()) if parts else ""
        if first in roots:
            candidate = roots[first].joinpath(*parts[1:])
        elif Path(raw).is_absolute():
            candidate = Path(raw)
        else:
            candidate = roots["workspace"].joinpath(*parts)

        abs_p = candidate.resolve()
        # Check nearest existing ancestor to defeat symlink escapes
        probe = abs_p
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        real_probe = probe.resolve()
        allowed = any(self._is_inside(real_probe, r) and self._is_inside(abs_p, r) for r in roots.values())
        if not allowed:
            raise PermissionError("RYNA only manages files inside Desktop, Documents, Downloads, Pictures, and RYNA-Workspace.")
        return abs_p

    def list_user_folder(self, folder: str = "documents") -> dict[str, Any]:
        target = self.resolve_user_path(folder or "documents")
        if not target.exists():
            raise FileNotFoundError(f"Folder not found: {folder}")
        if not target.is_dir():
            raise ValueError("That path is a file, not a folder.")
        entries = sorted(
            [p for p in target.iterdir() if not p.name.startswith(".")],
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        items = []
        for p in entries[:40]:
            try:
                sz = 0 if p.is_dir() else p.stat().st_size
            except OSError:
                sz = 0
            items.append({"name": p.name, "is_dir": p.is_dir(), "size": _fmt_size(sz) if not p.is_dir() else "folder"})
        return {"folder": str(target), "total": len(entries), "items": items}

    def find_user_files(self, query: str, folder: str | None = None, limit: int = 25) -> dict[str, Any]:
        needle = str(query or "").strip().lower()
        if not needle or len(needle) > 80:
            raise ValueError("Tell me part of the file or folder name to search for.")
        roots = [self.resolve_user_path(folder)] if folder else [r for r in self.user_roots().values() if r.exists()]
        hits: list[str] = []
        visited = 0

        def _walk(d: Path, depth: int) -> None:
            nonlocal visited
            if len(hits) >= limit or visited > 15000 or depth > 4:
                return
            try:
                for entry in d.iterdir():
                    if entry.name.startswith(".") or entry.name.lower() in SKIP_DIRS:
                        continue
                    visited += 1
                    if needle in entry.name.lower():
                        hits.append(str(entry))
                        if len(hits) >= limit:
                            return
                    if entry.is_dir() and not entry.is_symlink():
                        _walk(entry, depth + 1)
            except OSError:
                return

        for r in roots:
            _walk(r, 0)
        return {"query": query, "count": len(hits), "matches": hits}

    def find_and_open_project(self, name: str) -> dict[str, Any]:
        needle = str(name or "").strip().lower()
        if not needle or len(needle) > 60:
            raise ValueError("Tell me the project folder name to open.")
        hits: list[Path] = []
        for r in self.user_roots().values():
            if not r.exists():
                continue
            for root_dir, dirs, _files in os.walk(r):
                rel_depth = len(Path(root_dir).relative_to(r).parts)
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
                for d in dirs:
                    if needle in d.lower():
                        hits.append(Path(root_dir) / d)
                if rel_depth >= 2 or len(hits) >= 12:
                    dirs[:] = []
        if not hits:
            raise FileNotFoundError(f"No project folder matching '{name}' was found in Desktop, Documents, Downloads, or RYNA-Workspace.")
        hits.sort(key=lambda p: (p.name.lower() != needle, len(str(p))))
        chosen = hits[0]
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", str(chosen)])
        return {"opened": True, "project": chosen.name, "path": str(chosen)}

    def read_user_file(self, path_str: str) -> dict[str, Any]:
        p = self.resolve_user_path(path_str)
        if p.suffix.lower() not in READ_ALLOWED_EXT:
            raise ValueError("RYNA reads plain-text and source files only (.txt, .md, .csv, .json, .py, etc.).")
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path_str}")
        sz = p.stat().st_size
        if sz > MAX_READ_BYTES:
            raise ValueError("File exceeds the 200 KB read limit.")
        txt = p.read_text(encoding="utf-8", errors="replace")
        if "\x00" in txt:
            raise ValueError("That file appears to be binary, not text.")
        return {"path": str(p), "size": _fmt_size(sz), "content": txt[:6000], "truncated": len(txt) > 6000}

    def write_user_file(self, path_str: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        p = self.resolve_user_path(path_str)
        if p.suffix.lower() in WRITE_BLOCKED_EXT:
            raise PermissionError(f"RYNA will not write executable/script files ({p.suffix}). Use a text file like .txt or .md.")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES or "\x00" in content:
            raise ValueError("Content must be plain text under 100 KB.")
        existed = p.exists()
        if existed and not overwrite:
            raise FileExistsError(f"'{p.name}' already exists. Confirm overwrite or pick a new name.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(p), "replaced": existed, "size": _fmt_size(len(content.encode("utf-8")))}

    def copy_user_file(self, src_str: str, dst_str: str) -> dict[str, Any]:
        src = self.resolve_user_path(src_str)
        dst = self.resolve_user_path(dst_str)
        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src_str}")
        if dst.suffix.lower() in WRITE_BLOCKED_EXT:
            raise PermissionError("RYNA will not create executable or script files.")
        if dst.exists():
            raise FileExistsError(f"Destination already exists: {dst.name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return {"copied": True, "from": str(src), "to": str(dst)}

    def move_user_file(self, src_str: str, dst_str: str) -> dict[str, Any]:
        src = self.resolve_user_path(src_str)
        dst = self.resolve_user_path(dst_str)
        if not src.exists():
            raise FileNotFoundError(f"Source not found: {src_str}")
        if dst.suffix.lower() in WRITE_BLOCKED_EXT:
            raise PermissionError("RYNA will not create executable or script files.")
        if dst.exists():
            raise FileExistsError(f"Destination already exists: {dst.name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"moved": True, "from": str(src), "to": str(dst)}

    def recycle_user_file(self, path_str: str) -> dict[str, Any]:
        """Move file/folder safely to Windows Recycle Bin (NEVER permanent deletion)."""
        target = self.resolve_user_path(path_str)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path_str}")
        if target in set(self.user_roots().values()):
            raise PermissionError("RYNA will never delete a top-level user folder.")
        if sys.platform == "win32":
            safe = str(target).replace("'", "''")
            script = (
                "Add-Type -AssemblyName Microsoft.VisualBasic; "
                f"$p = '{safe}'; "
                "if (Test-Path -LiteralPath $p -PathType Container) { "
                "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin') "
                "} else { "
                "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }"
            )
            self._run_ps(script, timeout=15.0)
            return {"recycled": True, "path": str(target), "recoverable": True}
        trash_dir = self.home_dir / ".ryna_trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        dest = trash_dir / f"{int(time.time())}-{target.name}"
        shutil.move(str(target), str(dest))
        return {"recycled": True, "path": str(target), "trash_path": str(dest), "recoverable": True}

    # --- 2. Offline Windows WinRT OCR (Read Screen or Image Text) ---
    def ocr_image(self, image_path: Path) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("Windows built-in OCR requires Windows 10 or 11.")
        if not image_path.is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as tf:
            tf.write(WINRT_OCR_PS)
            ps_path = Path(tf.name)
        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(
                ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps_path), "-Path", str(image_path.resolve())],
                capture_output=True,
                text=True,
                timeout=20.0,
                creationflags=creation_flags,
            )
            if res.returncode != 0:
                raise RuntimeError((res.stderr or res.stdout or "Windows OCR failed.").strip())
            txt = (res.stdout or "").strip()
            return {"engine": "windows-winrt-ocr", "offline": True, "text": txt, "empty": not bool(txt)}
        finally:
            try:
                ps_path.unlink(missing_ok=True)
            except OSError:
                pass

    # --- 3. Live System Telemetry & Proactive Health Monitor ---
    def sample_telemetry(self, battery_info: dict[str, Any] | None = None) -> dict[str, Any]:
        """Measure real live Windows CPU %, RAM GB/%, Disk GB/%, Uptime, and Hostname."""
        disk = shutil.disk_usage(self.project_root)
        disk_pct = round((disk.used / disk.total) * 100.0, 1) if disk.total else 0.0
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_total_gb = round(disk.total / (1024**3), 1)

        ram_total_gb = 0.0
        ram_used_gb = 0.0
        ram_pct = 0.0
        uptime_min = 0
        cpu_pct: float | None = None

        if sys.platform == "win32":
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            mem = _MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                ram_total_gb = round(mem.ullTotalPhys / (1024**3), 1)
                ram_used_gb = round((mem.ullTotalPhys - mem.ullAvailPhys) / (1024**3), 1)
                ram_pct = float(mem.dwMemoryLoad)
            try:
                uptime_ms = ctypes.windll.kernel32.GetTickCount64()
                uptime_min = int(uptime_ms // 60000)
            except Exception:
                uptime_min = 0
            try:
                out = self._run_ps("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average", timeout=4.0)
                if out:
                    cpu_pct = round(float(out.strip()), 1)
            except Exception:
                cpu_pct = None

        bat = battery_info or {}
        alerts = self._evaluate_alerts(cpu_pct=cpu_pct, ram_pct=ram_pct, disk_pct=disk_pct, battery=bat)
        return {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpu_count": os.cpu_count() or 1,
            "cpu_percent": cpu_pct,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ram_percent": ram_pct,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "disk_percent": disk_pct,
            "uptime_minutes": uptime_min,
            "battery": bat,
            "alerts": alerts,
            "health": "WARNING" if alerts else "HEALTHY",
        }

    def _evaluate_alerts(
        self,
        cpu_pct: float | None,
        ram_pct: float,
        disk_pct: float,
        battery: dict[str, Any],
        cooldown_sec: float = 900.0,
    ) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        if cpu_pct is not None and cpu_pct >= 90.0:
            candidates.append({"key": "cpu", "level": "warning", "text": f"CPU usage is high at {cpu_pct:.0f}%."})
        if ram_pct >= 90.0:
            candidates.append({"key": "ram", "level": "warning", "text": f"Memory usage is high at {ram_pct:.0f}%."})
        if disk_pct >= 92.0:
            candidates.append({"key": "disk", "level": "warning", "text": f"System disk is {disk_pct:.0f}% full."})
        if battery.get("has_battery") and int(battery.get("percent", 100)) <= 15 and battery.get("state") == "on battery":
            candidates.append({"key": "battery", "level": "warning", "text": f"Battery is low at {battery['percent']}% and not charging."})

        now = time.monotonic()
        active_keys = {c["key"] for c in candidates}
        for k in list(self._last_alerts):
            if k not in active_keys:
                self._last_alerts.pop(k, None)
        for c in candidates:
            if now - self._last_alerts.get(c["key"], 0.0) >= cooldown_sec:
                self._last_alerts[c["key"]] = now
                self._alert_history.append({"at": datetime.now(timezone.utc).isoformat(), **c})
                self._alert_history = self._alert_history[-30:]
        return candidates

    def system_info_summary(self, battery_info: dict[str, Any] | None = None) -> str:
        t = self.sample_telemetry(battery_info=battery_info)
        up = t["uptime_minutes"]
        up_str = f"{up // 60} hours" if up >= 120 else f"{up} minutes"
        cpu_str = f"CPU at {t['cpu_percent']:.0f}%, " if t["cpu_percent"] is not None else ""
        bat_str = f" Battery {t['battery']['percent']}% ({t['battery']['state']})." if t["battery"].get("has_battery") else ""
        return (
            f"{t['os']} on {t['hostname']} ({t['cpu_count']} threads). "
            f"{cpu_str}Memory {t['ram_used_gb']} of {t['ram_total_gb']} GB ({t['ram_percent']:.0f}%), "
            f"Disk {t['disk_free_gb']} GB free of {t['disk_total_gb']} GB. Up for {up_str}.{bat_str}"
        )

    # --- 4. RYNA Technical Doctor ---
    def diagnose_issues(self, error_text: str = "", recent_activity: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        recent_errs = [
            f"{a.get('event_type', 'action')}: {a.get('summary', '')}"
            for a in (recent_activity or [])
            if str(a.get("status", "")).lower() in ("error", "warn")
        ][:5]
        combined = "\n".join([error_text.strip(), *recent_errs]).strip()
        findings: list[dict[str, str]] = []
        if combined:
            for rule in DOCTOR_RULES:
                if rule["re"].search(combined):
                    findings.append({"id": rule["id"], "cause": rule["cause"], "fix": rule["fix"]})
        if not findings and not recent_errs:
            summary = "RYNA's system doctor found no recent errors. Everything looks healthy."
        elif findings:
            top = findings[0]
            summary = f"Likely cause: {top['cause']} Suggested fix: {top['fix']}"
        else:
            summary = f"Recent issues recorded: {'; '.join(recent_errs[:2])}."
        return {"findings": findings, "recent_errors": recent_errs, "summary": summary}

    # --- 5. Safe Two-Step Windows Power Management ---
    def request_power_action(self, action: str) -> dict[str, Any]:
        act = action.strip().lower()
        if act not in ("shutdown", "restart", "signout"):
            raise ValueError("Supported power actions: shutdown, restart, signout.")
        self._pending_power = {"action": act, "expires_at": time.monotonic() + 60.0}
        label = {"shutdown": "shut down in 60 seconds", "restart": "restart in 60 seconds", "signout": "sign out of Windows"}[act]
        confirm_phrase = {"shutdown": "confirm shutdown", "restart": "confirm restart", "signout": "confirm sign out"}[act]
        return {
            "needs_confirmation": True,
            "action": act,
            "message": f"Say '{confirm_phrase}' within 60 seconds to {label}, or 'cancel shutdown' to stop.",
        }

    def confirm_power_action(self, action: str) -> dict[str, Any]:
        act = action.strip().lower()
        pend = self._pending_power
        if not pend or pend["action"] != act or time.monotonic() > pend["expires_at"]:
            raise ValueError(f"There is no pending {act} request to confirm. Ask to {act} first.")
        self._pending_power = None
        if sys.platform != "win32":
            raise RuntimeError("Windows power controls require Windows.")
        if act == "shutdown":
            subprocess.Popen(["shutdown.exe", "/s", "/t", "60"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"executed": True, "action": "shutdown", "message": "Shutdown scheduled in 60 seconds. Say 'cancel shutdown' to abort."}
        if act == "restart":
            subprocess.Popen(["shutdown.exe", "/r", "/t", "60"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"executed": True, "action": "restart", "message": "Restart scheduled in 60 seconds. Say 'cancel shutdown' to abort."}
        subprocess.Popen(["shutdown.exe", "/l"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"executed": True, "action": "signout", "message": "Signing out of Windows now."}

    def cancel_power_action(self) -> dict[str, Any]:
        self._pending_power = None
        if sys.platform == "win32":
            subprocess.run(["shutdown.exe", "/a"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"cancelled": True, "message": "Any scheduled shutdown or restart has been cancelled."}

    # --- 6. Active-App Direct Dictation / Typing ---
    def type_in_active_app(self, text: str, active_window_title: str = "") -> dict[str, Any]:
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("There is no text to type.")
        if len(clean) > 2000:
            raise ValueError("Keep each dictation segment under 2,000 characters.")
        if active_window_title.strip().upper().startswith("RYNA"):
            raise RuntimeError("Click into the app where you want RYNA to type (like Word or Notepad) first.")
        _win_type_unicode(clean, char_delay=0.004)
        return {"typed": True, "length": len(clean), "target_window": active_window_title or "Active App"}

    # --- 7. Free Live Weather (Open-Meteo, No API Keys) ---
    def get_weather(self, city: str) -> dict[str, Any]:
        name = str(city or "").strip()
        if not name or len(name) > 80:
            raise ValueError("Tell me which city to check the weather for.")
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(name)}&count=1&language=en&format=json"
        try:
            with urllib.request.urlopen(urllib.request.Request(geo_url, headers={"User-Agent": "RYNA/1.0"}), timeout=7.0) as r:
                geo = json.loads(r.read(65536).decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Could not reach the weather service: {exc}") from exc
        results = geo.get("results") or []
        if not results:
            raise ValueError(f"I couldn't find a city named '{name}'.")
        place = results[0]
        fc_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={place['latitude']}&longitude={place['longitude']}"
            "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=2"
        )
        with urllib.request.urlopen(urllib.request.Request(fc_url, headers={"User-Agent": "RYNA/1.0"}), timeout=7.0) as r:
            fc = json.loads(r.read(65536).decode("utf-8", errors="replace"))
        cur = fc.get("current") or {}
        daily = fc.get("daily") or {}
        cond = WMO_CODES.get(int(cur.get("weather_code", -1)), "mixed conditions")
        where = ", ".join(x for x in (place.get("name"), place.get("country")) if x)
        lo = round(daily["temperature_2m_min"][0]) if daily.get("temperature_2m_min") else None
        hi = round(daily["temperature_2m_max"][0]) if daily.get("temperature_2m_max") else None
        rain = daily["precipitation_probability_max"][0] if daily.get("precipitation_probability_max") else None
        extra = f" Today's range is {lo} to {hi}°C" + (f" with a {rain}% chance of rain." if rain is not None else ".") if lo is not None else ""
        summary = (
            f"In {where}, it's currently {round(cur.get('temperature_2m', 0))}°C with {cond}, "
            f"humidity {cur.get('relative_humidity_2m', '?')}%, and wind {round(cur.get('wind_speed_10m', 0))} km/h.{extra}"
        )
        return {"city": where, "summary": summary, "current": cur}
