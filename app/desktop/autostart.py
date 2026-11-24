"""Windows Always-On Startup & Desktop Shortcut manager for RYNA (100% per-user, zero admin required, zero terminal window)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def find_pythonw() -> str:
    """Locate pythonw.exe beside the active Python interpreter so RYNA runs completely windowless."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe)
    sibling = exe.with_name("pythonw.exe")
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("pythonw")
    return found or str(exe)


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_launcher_path(custom_startup_dir: Path | None = None) -> Path:
    return (custom_startup_dir or startup_dir()) / "RYNA.vbs"


def _build_vbs(project_root: Path, pythonw_path: str | None = None) -> str:
    pyw = (pythonw_path or find_pythonw()).replace('"', '""')
    root = str(project_root.resolve()).replace('"', '""')
    return (
        "' RYNA Always-On Windowless Launcher (starts RYNA with zero terminal window)\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{root}"\r\n'
        f'sh.Run """{pyw}"" -m ai.orchestration.main", 0, False\r\n'
    )


def is_autostart_enabled(custom_startup_dir: Path | None = None) -> bool:
    return startup_launcher_path(custom_startup_dir).is_file()


def enable_autostart(project_root: Path, custom_startup_dir: Path | None = None) -> Path:
    dest = startup_launcher_path(custom_startup_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_build_vbs(project_root), encoding="utf-8")
    return dest


def disable_autostart(custom_startup_dir: Path | None = None) -> bool:
    dest = startup_launcher_path(custom_startup_dir)
    if dest.is_file():
        dest.unlink()
        return True
    return False


def create_desktop_shortcut(project_root: Path, desktop_dir: Path | None = None) -> Path:
    """Create a native RYNA.lnk shortcut on the user's Windows Desktop with RYNA's robot icon."""
    if sys.platform != "win32":
        raise RuntimeError("Windows Desktop shortcut creation requires Windows.")
    if desktop_dir is None:
        home = Path.home()
        desktop_dir = (home / "OneDrive" / "Desktop") if (home / "OneDrive" / "Desktop").is_dir() else (home / "Desktop")
    desktop_dir.mkdir(parents=True, exist_ok=True)
    lnk_path = desktop_dir / "RYNA.lnk"
    pyw = find_pythonw()
    icon_path = project_root / "app" / "desktop" / "assets" / "ryna_icon.ico"

    def _ps_esc(s: str) -> str:
        return s.replace("'", "''")

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_esc(str(lnk_path))}'); "
        f"$s.TargetPath = '{_ps_esc(pyw)}'; "
        "$s.Arguments = '-m ai.orchestration.main'; "
        f"$s.WorkingDirectory = '{_ps_esc(str(project_root.resolve()))}'; "
        "$s.Description = 'RYNA - Always-On Desktop AI Robot'; "
        + (f"$s.IconLocation = '{_ps_esc(str(icon_path))}'; " if icon_path.is_file() else "")
        + "$s.Save()"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(["powershell", "-NoLogo", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=10.0, creationflags=creation_flags)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "Failed to create RYNA Desktop shortcut.").strip())
    return lnk_path
