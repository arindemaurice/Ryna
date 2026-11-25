"""RYNA Windows desktop control, fast physical Windows Search app launcher,
and full local system capability algorithms (StartApps discovery, polite close,
folders, volume, brightness, battery, settings pages, media links, clipboard, screen vision).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database.store import RynaDatabase

# Canonical app registry with spoken aliases, exe/URI/StartApps hints, and web fallbacks
APP_LAUNCH_SPECS: dict[str, dict[str, Any]] = {
    "whatsapp": {"name": "WhatsApp", "aliases": ["whatsapp", "whats app", "wa"], "uri": "whatsapp:", "web": "https://web.whatsapp.com/"},
    "spotify": {"name": "Spotify", "aliases": ["spotify", "music app"], "uri": "spotify:", "exe_candidates": [r"%APPDATA%\Spotify\Spotify.exe"], "web": "https://open.spotify.com/"},
    "chrome": {"name": "Google Chrome", "aliases": ["chrome", "google chrome"], "exe_candidates": [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"], "cmd": "chrome", "web": "https://www.google.com/"},
    "edge": {"name": "Microsoft Edge", "aliases": ["edge", "microsoft edge"], "exe_candidates": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"], "cmd": "msedge", "uri": "microsoft-edge:"},
    "browser": {"name": "Browser", "aliases": ["browser", "web browser"], "url": "https://www.google.com"},
    "vscode": {"name": "Visual Studio Code", "aliases": ["vs code", "vscode", "visual studio code", "code"], "cmd": "code", "exe_candidates": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"]},
    "vs code": {"name": "Visual Studio Code", "aliases": ["vs code"], "cmd": "code", "exe_candidates": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"]},
    "notepad": {"name": "Notepad", "aliases": ["notepad", "note pad", "text editor"], "cmd": "notepad.exe"},
    "calculator": {"name": "Calculator", "aliases": ["calculator", "calc"], "cmd": "calc.exe", "uri": "calculator:"},
    "explorer": {"name": "File Explorer", "aliases": ["file explorer", "explorer", "files", "my files"], "cmd": "explorer.exe"},
    "file explorer": {"name": "File Explorer", "aliases": ["file explorer"], "cmd": "explorer.exe"},
    "paint": {"name": "Paint", "aliases": ["paint", "ms paint"], "cmd": "mspaint.exe"},
    "settings": {"name": "Settings", "aliases": ["settings", "windows settings"], "uri": "ms-settings:"},
    "discord": {"name": "Discord", "aliases": ["discord"], "uri": "discord:", "exe_candidates": [r"%LOCALAPPDATA%\Discord\Update.exe"]},
    "telegram": {"name": "Telegram", "aliases": ["telegram", "telegram desktop"], "uri": "tg:", "web": "https://web.telegram.org/"},
    "word": {"name": "Word", "aliases": ["word", "microsoft word", "ms word", "office word"], "cmd": "winword.exe"},
    "excel": {"name": "Excel", "aliases": ["excel", "microsoft excel", "ms excel"], "cmd": "excel.exe"},
    "powerpoint": {"name": "PowerPoint", "aliases": ["powerpoint", "microsoft powerpoint", "ms powerpoint"], "cmd": "powerpnt.exe"},
    "outlook": {"name": "Outlook", "aliases": ["outlook", "microsoft outlook"], "cmd": "outlook.exe"},
    "terminal": {"name": "Windows Terminal", "aliases": ["terminal", "windows terminal", "command prompt", "cmd"], "cmd": "wt.exe"},
    "steam": {"name": "Steam", "aliases": ["steam"], "uri": "steam:"},
    "github": {"name": "GitHub Desktop", "aliases": ["github", "github desktop"], "exe_candidates": [r"%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe"]},
    "youtube": {"name": "YouTube", "aliases": ["youtube"], "url": "https://www.youtube.com/"},
    "thispc": {"name": "This PC", "aliases": ["this pc", "my pc", "my computer", "computer"], "folder": "thispc"},
    "recyclebin": {"name": "Recycle Bin", "aliases": ["recycle bin", "trash", "bin"], "folder": "recyclebin"},
}

WINDOWS_SETTINGS_PAGES: dict[str, str] = {
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "battery": "ms-settings:batterysaver",
    "power": "ms-settings:powersleep",
    "apps": "ms-settings:appsfeatures",
    "update": "ms-settings:windowsupdate",
    "updates": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy",
    "microphone": "ms-settings:privacy-microphone",
    "camera": "ms-settings:privacy-webcam",
    "notifications": "ms-settings:notifications",
    "storage": "ms-settings:storagesense",
    "network": "ms-settings:network",
    "personalization": "ms-settings:personalization",
    "defaultapps": "ms-settings:defaultapps",
    "about": "ms-settings:about",
}

PROTECTED_PROCESSES = {
    "explorer", "winlogon", "csrss", "smss", "wininit", "services", "lsass",
    "svchost", "dwm", "system", "idle", "registry", "fontdrvhost", "sihost",
    "taskhostw", "ctfmon", "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "runtimebroker", "securityhealthservice", "msmpeng",
    "powershell", "pwsh", "cmd", "conhost", "windowsterminal", "python",
    "pythonw", "taskmgr",
}

APP_NAME_SAFE_RE = re.compile(r"^[\w .&()'+\-]{1,60}$", re.UNICODE)


def _norm_app_key(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]+", " ", str(name or "").lower())).strip()


def resolve_app_spec(app_name: str) -> tuple[str | None, dict[str, Any] | None]:
    """Match a spoken app name against APP_LAUNCH_SPECS canonical keys and aliases."""
    n = _norm_app_key(app_name)
    if not n:
        return None, None
    if n in APP_LAUNCH_SPECS:
        return n, APP_LAUNCH_SPECS[n]
    for key, spec in APP_LAUNCH_SPECS.items():
        if _norm_app_key(spec.get("name", "")) == n:
            return key, spec
        for alias in spec.get("aliases", []):
            if _norm_app_key(alias) == n:
                return key, spec
    return None, None


# --- Fast Native Win32 SendInput Helpers (Zero PowerShell Startup Latency!) ---
if sys.platform == "win32":
    ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_UNICODE = 0x0004
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_RIGHTDOWN = 0x0008
    _MOUSEEVENTF_RIGHTUP = 0x0010
    _MOUSEEVENTF_MIDDLEDOWN = 0x0020
    _MOUSEEVENTF_MIDDLEUP = 0x0040
    _MOUSEEVENTF_WHEEL = 0x0800
    _MOUSEEVENTF_ABSOLUTE = 0x8000

    VK_MAP: dict[str, int] = {
        "enter": 0x0D, "return": 0x0D,
        "esc": 0x1B, "escape": 0x1B,
        "tab": 0x09,
        "space": 0x20, "spacebar": 0x20,
        "backspace": 0x08, "back": 0x08,
        "delete": 0x2E, "del": 0x2E,
        "insert": 0x2D, "ins": 0x2D,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21, "page_up": 0x21, "pgup": 0x21,
        "pagedown": 0x22, "page_down": 0x22, "pgdn": 0x22,
        "up": 0x26, "arrowup": 0x26,
        "down": 0x28, "arrowdown": 0x28,
        "left": 0x25, "arrowleft": 0x25,
        "right": 0x27, "arrowright": 0x27,
        "win": 0x5B, "windows": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
        "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
        "alt": 0x12, "menu": 0x12, "lalt": 0xA4, "ralt": 0xA5,
        "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
        "capslock": 0x14, "caps_lock": 0x14,
        "printscreen": 0x2C, "prtsc": 0x2C,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
        "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
        "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    }
    for _ch in "abcdefghijklmnopqrstuvwxyz":
        VK_MAP[_ch] = ord(_ch.upper())
    for _num in "0123456789":
        VK_MAP[_num] = ord(_num)


def _win_send_vk(vk: int, key_up: bool = False) -> None:
    if sys.platform != "win32":
        return
    flags = _KEYEVENTF_KEYUP if key_up else 0
    inp = _INPUT(type=_INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _win_tap_vk(vk: int) -> None:
    _win_send_vk(vk, False)
    _win_send_vk(vk, True)


def _win_chord(mod_vk: int, key_vk: int) -> None:
    _win_send_vk(mod_vk, False)
    _win_send_vk(key_vk, False)
    _win_send_vk(key_vk, True)
    _win_send_vk(mod_vk, True)


def _win_type_unicode(text: str, char_delay: float = 0.006) -> None:
    """Type Unicode text directly via Win32 SendInput (~5-15ms per word, blazing fast!)."""
    if sys.platform != "win32":
        return
    u32 = ctypes.windll.user32
    sz = ctypes.sizeof(_INPUT)
    for ch in text:
        code = ord(ch)
        down = _INPUT(type=_INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)))
        up = _INPUT(type=_INPUT_KEYBOARD, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
        pair = (_INPUT * 2)(down, up)
        u32.SendInput(2, pair, sz)
        if char_delay > 0:
            time.sleep(char_delay)


class ComputerController:
    def __init__(self, db: RynaDatabase, screenshots_dir: Path | None = None) -> None:
        self.db = db
        self.screenshots_dir = screenshots_dir or (Path.cwd() / "data" / "screenshots")
        self._start_apps_cache: list[dict[str, str]] | None = None
        self._start_apps_cached_at: float = 0.0

    def _run_ps(self, script: str, timeout: float = 10.0) -> str:
        if sys.platform != "win32":
            raise RuntimeError("Windows desktop actions require Windows.")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creation_flags,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "Windows command failed.").strip()
            raise RuntimeError(err)
        return (res.stdout or "").strip()

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

    # --- Windows StartApps Discovery (Find ANY Installed Windows App) ---
    def list_installed_apps(self, refresh: bool = False) -> list[dict[str, str]]:
        if sys.platform != "win32":
            return []
        now = time.monotonic()
        if not refresh and self._start_apps_cache is not None and (now - self._start_apps_cached_at) < 120.0:
            return list(self._start_apps_cache)
        try:
            out = self._run_ps(
                "Get-StartApps | Sort-Object Name -Unique | ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }",
                timeout=8.0,
            )
            apps: list[dict[str, str]] = []
            for line in out.splitlines():
                if "\t" in line:
                    n, aid = line.split("\t", 1)
                    if n.strip() and aid.strip():
                        apps.append({"name": n.strip(), "app_id": aid.strip()})
            self._start_apps_cache = apps
            self._start_apps_cached_at = now
            return list(apps)
        except Exception:
            return list(self._start_apps_cache or [])

    def _find_start_app(self, query: str) -> dict[str, str] | None:
        apps = self.list_installed_apps()
        q = query.strip().lower()
        for a in apps:
            if a["name"].lower() == q:
                return a
        starts = [a for a in apps if a["name"].lower().startswith(q)]
        if starts:
            return min(starts, key=lambda x: len(x["name"]))
        contains = [a for a in apps if q in a["name"].lower()]
        if contains:
            return min(contains, key=lambda x: len(x["name"]))
        return None

    # --- Fast Physical Windows Search Bar Launch ("goes to search bar, searches, opens — fast!") ---
    def search_bar_launch(self, app_name: str) -> dict[str, Any]:
        """Physically open Windows Search (Win+S), rapidly type the app name via Win32 SendInput,
        and press Enter to launch it (~0.55s total, visibly opening via the Windows Search bar).
        """
        if not bool(self.db.get_setting("computer.allow_app_launch", True)):
            raise PermissionError("Application launching is disabled in Settings (computer.allow_app_launch = false).")
        clean = str(app_name or "").strip()
        if not clean or not APP_NAME_SAFE_RE.match(clean):
            raise ValueError("Tell me the name of an installed app.")
        if sys.platform != "win32":
            raise RuntimeError("Windows Search bar launching requires Windows.")

        # Resolve friendly display name for best Windows Search ranking (e.g. "word" -> "Word", "vscode" -> "Visual Studio Code")
        _key, spec = resolve_app_spec(clean)
        query = spec["name"] if spec and spec.get("name") else clean

        # Folders & pure web links are faster/cleaner via direct folder/browser open
        if spec and "folder" in spec:
            return self.open_folder(spec["folder"])
        if spec and "url" in spec and "cmd" not in spec and "exe_candidates" not in spec and "uri" not in spec:
            webbrowser.open(spec["url"])
            return {"launched": True, "app": query, "method": "browser_url"}

        # 1. Open Windows Search bar immediately (Win + S)
        _win_chord(0x5B, 0x53)  # VK_LWIN + 'S'
        time.sleep(0.24)
        # 2. Type app name cleanly & fast via native Unicode SendInput (~15ms!)
        _win_type_unicode(query, char_delay=0.005)
        # 3. Brief pause for top Windows Search match to settle, then press Enter!
        time.sleep(0.32)
        _win_tap_vk(0x0D)  # VK_RETURN

        return {"launched": True, "app": query, "method": "windows_search_bar", "query": query}

    def launch_application(self, app_name: str, prefer_search_bar: bool | None = None) -> dict[str, Any]:
        """Launch any Windows application:
        - Uses direct registry/StartApps/URI/exe/web fallback, OR
        - Fast physical Windows Search bar launch when `prefer_search_bar=True`.
        """
        if not bool(self.db.get_setting("computer.allow_app_launch", True)):
            raise PermissionError("Application launching is disabled in Settings (computer.allow_app_launch = false).")
        raw = str(app_name or "").strip()
        if not raw or not APP_NAME_SAFE_RE.match(raw):
            raise ValueError(f"Invalid application name: '{app_name}'.")

        if prefer_search_bar is True and sys.platform == "win32":
            return self.search_bar_launch(raw)

        key, spec = resolve_app_spec(raw)

        if spec is not None:
            if "folder" in spec:
                return self.open_folder(spec["folder"])
            if "url" in spec:
                webbrowser.open(spec["url"])
                return {"launched": True, "app": spec.get("name", key), "method": "browser_url"}
            for cand in spec.get("exe_candidates", []):
                expanded = os.path.expandvars(cand)
                if os.path.isfile(expanded):
                    proc = subprocess.Popen([expanded])
                    return {"launched": True, "app": spec.get("name", key), "pid": proc.pid, "path": expanded, "method": "exe"}
            if "cmd" in spec:
                found = shutil.which(spec["cmd"])
                if found:
                    proc = subprocess.Popen([found])
                    return {"launched": True, "app": spec.get("name", key), "pid": proc.pid, "path": found, "method": "cmd"}
            if "uri" in spec and sys.platform == "win32":
                try:
                    os.startfile(spec["uri"])  # type: ignore[attr-defined]
                    return {"launched": True, "app": spec.get("name", key), "method": f"uri:{spec['uri']}"}
                except Exception:
                    pass

        # Dynamic Start Menu Apps lookup
        search_term = spec["name"] if spec and spec.get("name") else raw
        if sys.platform == "win32":
            start_app = self._find_start_app(search_term)
            if start_app is not None:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{start_app['app_id']}"])
                return {"launched": True, "app": start_app["name"], "method": "start_apps", "app_id": start_app["app_id"]}

        # Web fallback from registry if desktop app isn't installed
        if spec and "web" in spec:
            webbrowser.open(spec["web"])
            return {"launched": True, "app": spec.get("name", key), "method": "web_fallback", "url": spec["web"]}

        raise ValueError(f"Application '{app_name}' could not be found in installed apps or allowlist.")

    def close_application(self, app_name: str) -> dict[str, Any]:
        """Politely ask a running application window to close via CloseMainWindow() (never force-kills)."""
        clean = str(app_name or "").strip()
        if not clean or not APP_NAME_SAFE_RE.match(clean):
            raise ValueError("Tell me the name of the app to close.")
        needle = clean.lower()
        if needle in PROTECTED_PROCESSES:
            raise PermissionError(f"I will not close '{clean}' because Windows or RYNA needs it.")
        safe_ps = needle.replace("'", "''")
        prot = ",".join(f"'{p}'" for p in sorted(PROTECTED_PROCESSES))
        script = (
            f"$n='{safe_ps}'; $prot=@({prot}); "
            "$m=@(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ($_.ProcessName.ToLower().Contains($n) -or $_.MainWindowTitle.ToLower().Contains($n)) }); "
            "if ($m.Count -eq 0) { throw \"No open window found for '$n'.\" }; "
            "$t=@($m | Where-Object { $prot -notcontains $_.ProcessName.ToLower() }); "
            "if ($t.Count -eq 0) { throw 'Cannot close protected system process.' }; "
            "foreach ($p in $t) { [void]$p.CloseMainWindow() }; "
            "Write-Output (($t | ForEach-Object { $_.ProcessName } | Select-Object -Unique) -join ', ')"
        )
        closed = self._run_ps(script, timeout=8.0)
        return {"closed": True, "app": closed or clean}

    def open_folder(self, folder_name: str) -> dict[str, Any]:
        """Open common user folders (Desktop, Documents, Downloads, Pictures, Workspace, This PC, Recycle Bin)."""
        key = folder_name.strip().lower()
        if sys.platform != "win32":
            raise RuntimeError("Folder opening requires Windows.")
        if key in ("thispc", "this pc", "my pc", "my computer", "computer"):
            subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
            return {"opened": True, "folder": "This PC"}
        if key in ("recyclebin", "recycle bin", "trash", "bin"):
            subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
            return {"opened": True, "folder": "Recycle Bin"}
        home = Path.home()
        folder_map = {
            "desktop": home / "Desktop",
            "documents": home / "Documents",
            "downloads": home / "Downloads",
            "pictures": home / "Pictures",
            "workspace": Path.cwd(),
        }
        # Support OneDrive Desktop/Documents on Windows if standard folder doesn't exist
        target = folder_map.get(key)
        if target is None:
            raise ValueError(f"Unknown folder '{folder_name}'.")
        if not target.exists():
            od = home / "OneDrive" / target.name
            if od.exists():
                target = od
        if not target.exists():
            raise FileNotFoundError(f"Folder not found: {target}")
        subprocess.Popen(["explorer.exe", str(target)])
        return {"opened": True, "folder": target.name, "path": str(target)}

    # --- Instant Win32 System Controls (Volume, Brightness, Battery, Shortcuts, Settings, Media) ---
    def volume_control(self, action: str, steps: int = 5) -> dict[str, Any]:
        act = action.strip().lower()
        if act not in ("up", "down", "mute"):
            raise ValueError("Volume action must be 'up', 'down', or 'mute'.")
        count = 1 if act == "mute" else max(1, min(25, int(steps)))
        vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}[act]
        for _ in range(count):
            _win_tap_vk(vk)
        return {"volume_action": act, "steps": count}

    def set_volume(self, level: int) -> dict[str, Any]:
        lvl = max(0, min(100, int(level)))
        for _ in range(50):
            _win_tap_vk(0xAE)  # VK_VOLUME_DOWN to 0
        ups = int(round(lvl / 2.0))
        for _ in range(ups):
            _win_tap_vk(0xAF)  # VK_VOLUME_UP to target
        return {"volume_level": lvl}

    def brightness_control(self, mode: str, value: int = 0) -> dict[str, Any]:
        m = mode.strip().lower()
        if m not in ("up", "down", "set"):
            raise ValueError("Brightness mode must be 'up', 'down', or 'set'.")
        val = max(0, min(100, int(value)))
        script = (
            "$cur = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "$meth = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if (-not $cur -or -not $meth) { throw 'This display does not support software brightness control.' }; "
            "$now = [int]$cur.CurrentBrightness; "
            f"$new = switch ('{m}') {{ 'up' {{ $now + 10 }} 'down' {{ $now - 10 }} 'set' {{ {val} }} }}; "
            "$new = [math]::Max(0, [math]::Min(100, $new)); "
            "[void](Invoke-CimMethod -InputObject $meth -MethodName WmiSetBrightness -Arguments @{ Timeout = [uint32]1; Brightness = [byte]$new }); "
            "Write-Output \"$now -> $new\""
        )
        out = self._run_ps(script, timeout=8.0)
        return {"brightness": out, "mode": m}

    def battery_status(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {"supported": False, "detail": "Windows required."}
        st = _SYSTEM_POWER_STATUS()
        ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st))
        if not ok or st.BatteryFlag == 128 or st.BatteryLifePercent == 255:
            return {"has_battery": False, "detail": "No battery detected (desktop PC or unknown)."}
        plugged = st.ACLineStatus == 1
        charging = bool(st.BatteryFlag & 8)
        pct = int(st.BatteryLifePercent)
        mins = int(st.BatteryLifeTime // 60) if st.BatteryLifeTime not in (0, 0xFFFFFFFF) else None
        state = "charging" if charging else ("plugged in" if plugged else "on battery")
        detail = f"Battery is at {pct} percent, {state}." + (f" About {mins} minutes remaining." if mins else "")
        return {"has_battery": True, "percent": pct, "state": state, "minutes_remaining": mins, "detail": detail}

    def computer_shortcut(self, action: str) -> dict[str, Any]:
        act = action.strip().lower()
        if act == "show_desktop":
            _win_chord(0x5B, 0x44)  # Win+D
            return {"shortcut": "show_desktop", "detail": "Showing the desktop."}
        if act == "switch_window":
            _win_chord(0x12, 0x09)  # Alt+Tab
            return {"shortcut": "switch_window", "detail": "Switching windows."}
        if act == "lock_screen":
            if sys.platform == "win32":
                ctypes.windll.user32.LockWorkStation()
            return {"shortcut": "lock_screen", "detail": "Locking your screen."}
        raise ValueError(f"Unsupported shortcut: '{action}'.")

    def open_settings_page(self, page: str) -> dict[str, Any]:
        key = re.sub(r"[^a-z]", "", page.lower())
        uri = WINDOWS_SETTINGS_PAGES.get(key)
        if not uri:
            raise ValueError(f"Unknown Windows settings page '{page}'. Supported: {', '.join(sorted(WINDOWS_SETTINGS_PAGES))}.")
        if sys.platform == "win32":
            os.startfile(uri)  # type: ignore[attr-defined]
        return {"opened": True, "page": key, "uri": uri}

    def open_media_search(self, platform: str, query: str) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q or len(q) > 150:
            raise ValueError("Provide a short search query.")
        enc = urllib.parse.quote_plus(q)
        plat = platform.strip().lower()
        if plat == "spotify":
            if sys.platform == "win32":
                try:
                    os.startfile(f"spotify:search:{urllib.parse.quote(q)}")  # type: ignore[attr-defined]
                    return {"opened": True, "platform": "spotify", "query": q, "method": "spotify_uri"}
                except Exception:
                    pass
            webbrowser.open(f"https://open.spotify.com/search/{urllib.parse.quote(q)}")
            return {"opened": True, "platform": "spotify", "query": q, "method": "spotify_web"}
        if plat == "youtube":
            url = f"https://www.youtube.com/results?search_query={enc}"
            webbrowser.open(url)
            return {"opened": True, "platform": "youtube", "query": q, "url": url}
        if plat == "maps":
            url = f"https://www.google.com/maps/search/?api=1&query={enc}"
            webbrowser.open(url)
            return {"opened": True, "platform": "maps", "query": q, "url": url}
        raise ValueError(f"Unsupported media platform: '{platform}'.")

    def clipboard_get(self) -> dict[str, Any]:
        text = self._run_ps("Get-Clipboard -Raw -ErrorAction SilentlyContinue", timeout=4.0)
        return {"text": text[:2000], "empty": not bool(text)}

    def clipboard_set(self, text: str) -> dict[str, Any]:
        clean = str(text or "")
        if not clean or len(clean) > 5000:
            raise ValueError("Clipboard text must be between 1 and 5000 characters.")
        safe = clean.replace("'", "''")
        self._run_ps(f"Set-Clipboard -Value '{safe}'", timeout=4.0)
        return {"copied": True, "length": len(clean)}

    # --- Complete Windows Input & OS Command Execution ---
    def get_mouse_position(self) -> dict[str, int]:
        """Get the current cursor coordinates on screen."""
        if sys.platform != "win32":
            return {"x": 0, "y": 0}
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return {"x": int(pt.x), "y": int(pt.y)}

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        """Move the mouse cursor directly to (x, y) coordinates on screen."""
        if sys.platform != "win32":
            return {"moved": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
        return {"moved": True, "x": int(x), "y": int(y)}

    def mouse_click(self, x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        """Perform a left, right, middle, or double click at (x, y) or current cursor position."""
        if sys.platform != "win32":
            return {"clicked": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        u32 = ctypes.windll.user32
        if x is not None and y is not None:
            u32.SetCursorPos(int(x), int(y))
            time.sleep(0.02)
        btn = button.lower().strip()
        down_flag = _MOUSEEVENTF_RIGHTDOWN if "right" in btn else (_MOUSEEVENTF_MIDDLEDOWN if "middle" in btn else _MOUSEEVENTF_LEFTDOWN)
        up_flag = _MOUSEEVENTF_RIGHTUP if "right" in btn else (_MOUSEEVENTF_MIDDLEUP if "middle" in btn else _MOUSEEVENTF_LEFTUP)
        total_clicks = 2 if "double" in btn or clicks == 2 else max(1, clicks)
        sz = ctypes.sizeof(_INPUT)
        for c in range(total_clicks):
            down_inp = _INPUT(type=_INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=down_flag, time=0, dwExtraInfo=0)))
            up_inp = _INPUT(type=_INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=up_flag, time=0, dwExtraInfo=0)))
            u32.SendInput(1, ctypes.byref(down_inp), sz)
            time.sleep(0.02)
            u32.SendInput(1, ctypes.byref(up_inp), sz)
            if c < total_clicks - 1:
                time.sleep(0.06)
        pos = self.get_mouse_position()
        return {"clicked": True, "button": btn, "clicks": total_clicks, "x": pos["x"], "y": pos["y"]}

    def mouse_scroll(self, clicks: int = 1) -> dict[str, Any]:
        """Scroll mouse wheel vertically (positive = up, negative = down)."""
        if sys.platform != "win32":
            return {"scrolled": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        delta = int(clicks) * 120
        inp = _INPUT(type=_INPUT_MOUSE, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=delta, dwFlags=_MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        return {"scrolled": True, "clicks": clicks}

    def type_text(self, text: str) -> dict[str, Any]:
        """Type Unicode text directly into the focused window/input."""
        if sys.platform != "win32":
            return {"typed": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        _win_type_unicode(text)
        win = self.active_window_title().get("title", "")
        return {"typed": True, "length": len(text), "target_window": win}

    def press_key(self, key: str) -> dict[str, Any]:
        """Press and release a keyboard key (enter, esc, tab, space, win, f1-f12, arrows, etc.)."""
        if sys.platform != "win32":
            return {"pressed": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        k = key.lower().strip()
        vk = VK_MAP.get(k)
        if vk is None:
            raise ValueError(f"Unknown key '{key}'. Supported: enter, esc, tab, space, win, ctrl, alt, shift, f1-f12, arrows, letters.")
        _win_tap_vk(vk)
        return {"pressed": True, "key": k}

    def hotkey(self, combo: str | list[str]) -> dict[str, Any]:
        """Press and release a combination of keys (e.g. 'ctrl+c', 'alt+f4', 'win+d', 'ctrl+shift+esc')."""
        if sys.platform != "win32":
            return {"pressed": False, "detail": "Windows required."}
        if not bool(self.db.get_setting("computer.allow_input_control", True)):
            raise PermissionError("Input control is disabled in Settings (computer.allow_input_control = false).")
        if isinstance(combo, str):
            keys = [k.strip().lower() for k in re.split(r"[\+\s\-]+", combo) if k.strip()]
        else:
            keys = [str(k).strip().lower() for k in combo if str(k).strip()]
        vks = []
        for k in keys:
            vk = VK_MAP.get(k)
            if vk is None:
                raise ValueError(f"Unknown key '{k}' in hotkey '{combo}'.")
            vks.append(vk)
        for vk in vks:
            _win_send_vk(vk, key_up=False)
            time.sleep(0.01)
        time.sleep(0.03)
        for vk in reversed(vks):
            _win_send_vk(vk, key_up=True)
            time.sleep(0.01)
        return {"hotkey": "+".join(keys), "success": True}

    def execute_command(self, command: str, cwd: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
        """Execute arbitrary PowerShell command on Windows with complete system access."""
        clean = str(command or "").strip()
        if not clean:
            raise ValueError("Missing command to execute.")
        if sys.platform != "win32":
            raise RuntimeError("Windows command execution requires Windows.")
        t0 = time.perf_counter()
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        work_dir = cwd if cwd and os.path.isdir(cwd) else None
        proc = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", clean],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
            creationflags=creation_flags,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = out if not err else (f"{out}\n{err}".strip() if out else err)
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": combined,
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
            "command": clean,
        }

    def list_processes(self, limit: int = 25) -> dict[str, Any]:
        """List running applications and processes on the system with memory usage."""
        if sys.platform != "win32":
            return {"processes": [], "count": 0}
        script = (
            f"Get-Process | Where-Object {{ $_.MainWindowHandle -ne 0 -or $_.WorkingSet64 -gt 50MB }} "
            f"| Sort-Object WorkingSet64 -Descending | Select-Object -First {limit} "
            f"| ForEach-Object {{ \"$($_.Id)`t$($_.ProcessName)`t$([math]::Round($_.WorkingSet64/1MB,1))`t$($_.MainWindowTitle)\" }}"
        )
        raw = self._run_ps(script, timeout=8.0)
        procs: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                procs.append({
                    "pid": int(parts[0]),
                    "name": parts[1],
                    "memory_mb": float(parts[2]),
                    "title": parts[3] if len(parts) > 3 else "",
                })
        return {"processes": procs, "count": len(procs)}

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": sys.platform,
            "active_window_inspection": sys.platform == "win32",
            "app_launch_enabled": bool(self.db.get_setting("computer.allow_app_launch", True)),
            "physical_search_launch": bool(self.db.get_setting("desktop.physical_app_search", True)),
            "allowed_apps": sorted(APP_LAUNCH_SPECS),
            "settings_pages": sorted(WINDOWS_SETTINGS_PAGES),
            "screen_vision_enabled": bool(self.db.get_setting("computer.allow_screen_vision", True)),
            "screen_capture_installed": importlib.util.find_spec("mss") is not None and importlib.util.find_spec("PIL") is not None,
            "input_control_enabled": bool(self.db.get_setting("computer.allow_input_control", True)),
            "command_execution_enabled": True,
            "mouse_control_enabled": True,
            "keyboard_control_enabled": True,
            "full_device_access": bool(self.db.get_setting("computer.allow_full_device_access", True)),
            "detail": "Complete Windows device access active: PowerShell commands, StartApps, physical Search launch, mouse/keyboard input, volume/brightness/battery, and mss screen vision.",
        }
