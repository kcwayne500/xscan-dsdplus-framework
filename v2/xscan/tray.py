from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from .api import AppContext


def _icon_image(context: AppContext) -> Image.Image:
    candidates = [context.paths.bundle / "app.ico", context.paths.bundle.parent / "app.ico"]
    for path in candidates:
        if path.is_file():
            try:
                return Image.open(path).convert("RGBA")
            except OSError:
                pass
    image = Image.new("RGBA", (64, 64), "#08111a")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 8, 59, 56), radius=8, fill="#dce9ad", outline="#7e9160", width=3)
    draw.line((15, 23, 49, 23), fill="#243019", width=4)
    draw.line((15, 34, 42, 34), fill="#243019", width=4)
    draw.ellipse((44, 41, 51, 48), fill="#27c46b")
    return image


def run_tray(context: AppContext, request_exit: Callable[[], None]) -> None:
    port = context.settings.section("server")["port"]

    def background(action):
        return lambda _icon=None, _item=None: threading.Thread(target=action, daemon=True).start()

    def open_dashboard():
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def quit_app(icon, _item):
        request_exit()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open XScan", background(open_dashboard), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start system", background(context.runtime.start)),
        pystray.MenuItem("Stop system", background(context.runtime.stop)),
        pystray.MenuItem("Restart system", background(context.runtime.restart)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Show DSDPlus/FMP24 windows", background(lambda: context.supervisor.set_native_windows_visible(True))),
        pystray.MenuItem("Hide DSDPlus/FMP24 windows", background(lambda: context.supervisor.set_native_windows_visible(False))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit XScan", quit_app),
    )
    pystray.Icon("xscan-v2", _icon_image(context), "XScan V2", menu).run()
