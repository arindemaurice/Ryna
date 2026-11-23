"""Main entrypoint for running the RYNA local engine and optional Windows desktop companion."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import webbrowser

from apps.windows_companion.single_instance import SingleInstanceGuard
from core.python.config import RynaConfig
from core.python.engine import RynaEngine
from core.python.server import create_server
from core.python.version import APP_NAME, VERSION


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} Local Engine")
    parser.add_argument("--host", default=None, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8765)")
    parser.add_argument("--open-ui", action="store_true", help="Open RYNA interface in default browser")
    parser.add_argument("--companion", action="store_true", help="Launch native desktop companion window")
    parser.add_argument("--allow-multiple", action="store_true", help="Skip single-instance mutex check")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = RynaConfig.load()
    host = args.host or config.host
    port = args.port if args.port is not None else config.port
    url = f"http://{host}:{port}"

    guard = SingleInstanceGuard("RYNA_LOCAL_ENGINE_SINGLE_INSTANCE")
    if not args.allow_multiple and not guard.acquire():
        print(f"{APP_NAME} is already running. Opening existing instance at {url}")
        if args.open_ui:
            webbrowser.open(url)
        return 0

    engine = RynaEngine(config)
    server = create_server(engine, host=host, port=port)

    def _shutdown(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"==========================================")
    print(f" {APP_NAME} {VERSION} Local Engine Running")
    print(f" UI & API : {url}")
    print(f" Database : {engine.config.db_path}")
    print(f" Press Ctrl+C to stop gracefully.")
    print(f"==========================================")

    if args.open_ui:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    if args.companion:
        # Run HTTP server in background thread while Tkinter runs on the main thread (required on Windows)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        try:
            from apps.windows_companion.companion_window import run_companion

            run_companion(api_base_url=url)
        finally:
            server.shutdown()
            server.server_close()
            engine.close()
            guard.release()
        return 0

    try:
        server.serve_forever()
    finally:
        server.server_close()
        engine.close()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
