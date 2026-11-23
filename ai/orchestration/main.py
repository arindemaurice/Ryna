"""Direct entrypoint for RYNA — Pure Walking Windows Desktop Robot (no web server, no browser)."""

from __future__ import annotations

import argparse
import signal

from ai.config import APP_NAME, VERSION, RynaConfig
from ai.orchestration.core import RynaCore
from app.desktop.robot_window import run_robot_desktop
from app.desktop.single_instance import SingleInstanceGuard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} — Walking Desktop AI Robot")
    parser.add_argument("--allow-multiple", action="store_true", help="Skip single-instance mutex check")
    args = parser.parse_args(argv)

    guard = SingleInstanceGuard("RYNA_SINGLE_INSTANCE_V2")
    if not args.allow_multiple and not guard.acquire():
        print(f"{APP_NAME} is already running on your desktop.")
        return 0

    core = RynaCore(RynaConfig.load())
    signal.signal(signal.SIGINT, lambda *_: None)

    print(f"{APP_NAME} {VERSION} — Pure Desktop Robot Active (Model: auto fast local, Voice: persistent Windows SAPI + offline faster-whisper)")
    try:
        run_robot_desktop(core)
    finally:
        core.close()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
