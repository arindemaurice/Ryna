"""Main entrypoint for RYNA local engine and optional native Windows desktop robot host."""

from __future__ import annotations

import argparse
import signal
import threading
import webbrowser

from ai.config import APP_NAME, VERSION, RynaConfig
from ai.orchestration.core import RynaCore
from ai.orchestration.server import create_server
from app.desktop.single_instance import SingleInstanceGuard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} {VERSION} Local Engine")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--open-ui", action="store_true", help="Open RYNA conversation UI in default browser")
    parser.add_argument("--robot", action="store_true", help="Launch native transparent Windows desktop robot host")
    parser.add_argument("--allow-multiple", action="store_true", help="Skip single-instance mutex check")
    args = parser.parse_args(argv)

    cfg = RynaConfig.load()
    host = args.host or cfg.host
    port = args.port if args.port is not None else cfg.port
    url = f"http://{host}:{port}"

    guard = SingleInstanceGuard("RYNA_SINGLE_INSTANCE_V2")
    if not args.allow_multiple and not guard.acquire():
        print(f"{APP_NAME} is already running at {url}. Bringing existing instance forward.")
        if args.open_ui:
            webbrowser.open(url)
        return 0

    core = RynaCore(cfg)
    server = create_server(core, host=host, port=port)

    def _stop(_sig: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"==========================================")
    print(f" {APP_NAME} {VERSION} — Clean Architecture Engine")
    print(f" Local UI : {url}")
    print(f" Database : {core.config.db_path}")
    print(f" Robot PNG: {'LOADED' if core.config.robot_asset_path.is_file() else 'AWAITING REFERENCE IMAGE'}")
    print(f"==========================================")

    if args.open_ui:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    if args.robot:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            from app.desktop.robot_window import run_robot_host

            run_robot_host(api_base_url=url, asset_path=core.config.robot_asset_path)
        finally:
            server.shutdown()
            server.server_close()
            core.close()
            guard.release()
        return 0

    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()
        guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
