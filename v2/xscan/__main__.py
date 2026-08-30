from __future__ import annotations

import argparse
import threading

import uvicorn

from .api import create_app
from .paths import AppPaths
from .tray import run_tray


def main() -> None:
    parser = argparse.ArgumentParser(description="XScan V2 background host")
    parser.add_argument("--no-tray", action="store_true", help="Run without a Windows tray icon")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--state-dir", help="XScan state directory (or set XSCAN_STATE_DIR)")
    parser.add_argument("--dsdplus-root", help="Directory containing DSDPlus.exe and FMP24.exe")
    arguments = parser.parse_args()
    paths = AppPaths.discover(state_dir=arguments.state_dir, dsdplus_root=arguments.dsdplus_root)
    app = create_app(paths, arguments.verbose)
    context = app.state.context
    server_settings = context.settings.section("server")
    host = arguments.host or server_settings["host"]
    port = arguments.port or int(server_settings["port"])
    # Frozen builds cannot rely on Uvicorn's dynamic "auto" imports. XScan uses
    # HTTP/SSE (not WebSockets), so pin the small, deterministic asyncio/H11
    # stack and explicitly disable WebSocket protocol negotiation.
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="debug" if arguments.verbose else "info",
        access_log=False,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        # Windowed PyInstaller applications deliberately have no stderr. The
        # default Uvicorn formatter probes stderr.isatty(), so retain XScan's
        # rotating file logger instead of installing Uvicorn's console config.
        log_config=None,
    )
    server = uvicorn.Server(config)
    if arguments.no_tray:
        server.run()
        return
    server_thread = threading.Thread(target=server.run, name="xscan-web", daemon=True)
    server_thread.start()
    run_tray(context, lambda: setattr(server, "should_exit", True))
    server.should_exit = True
    server_thread.join(timeout=10)


if __name__ == "__main__":
    main()
