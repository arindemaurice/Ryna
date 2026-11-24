"""Direct standalone entrypoint for RYNA — Always-On Walking Windows Desktop Robot.

100% independent of any terminal window or external server:
- Safe under windowless `pythonw.exe` (guards None stdout/stderr, logs cleanly to logs/ryna.log).
- Ensures working directory is RYNA's project root even when launched from Windows Startup or a Desktop shortcut.
- Enforces single-instance Windows mutex so RYNA never duplicates.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from ai.config import APP_NAME, AUTHOR, IS_FROZEN, PROJECT_ROOT, RUNTIME_BASE_DIR, VERSION, RynaConfig
from ai.orchestration.core import RynaCore
from app.desktop.robot_window import run_robot_desktop
from app.desktop.single_instance import SingleInstanceGuard


def _ensure_windowless_stdio() -> None:
    """Under pythonw.exe / frozen RYNA.exe on Windows, sys.stdout/sys.stderr are None; bind safely to os.devnull."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    _ensure_windowless_stdio()
    try:
        RUNTIME_BASE_DIR.mkdir(parents=True, exist_ok=True)
        os.chdir(RUNTIME_BASE_DIR if IS_FROZEN else PROJECT_ROOT)
    except OSError:
        pass

    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} — Always-On Walking Desktop AI Robot")
    parser.add_argument("--allow-multiple", action="store_true", help="Skip single-instance mutex check")
    args = parser.parse_args(argv)

    guard = SingleInstanceGuard("RYNA_SINGLE_INSTANCE_V2")
    if not args.allow_multiple and not guard.acquire():
        print(f"{APP_NAME} is already running on your desktop.")
        return 0

    core = RynaCore(RynaConfig.load())
    try:
        signal.signal(signal.SIGINT, lambda *_: None)
    except Exception:
        pass

    core.logger.info("%s %s — Standalone Desktop Robot Active (independent of terminal/server).", APP_NAME, VERSION)
    try:
        run_robot_desktop(core)
    except Exception:
        import traceback

        crash_log = PROJECT_ROOT / "logs" / "ryna_crash.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        core.logger.exception("RYNA desktop robot crashed.")
        raise
    finally:
        core.close()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
