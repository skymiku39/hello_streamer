"""系統匣常駐 — pystray 整合，提供右鍵選單與最小化功能。"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_ICON_SIZE = 64

_FONT_CANDIDATES = (
    ["arial.ttf"]
    if sys.platform == "win32"
    else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
)


def _create_icon_image() -> Image.Image:
    """Generate a simple tray icon programmatically."""
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [4, 4, 60, 60], radius=14, fill="#0f3460", outline="#00e676", width=3
    )
    font = None
    for candidate in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(candidate, 28)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((16, 14), "H", fill="white", font=font)
    return img


class TrayIcon:
    """System tray icon with right-click menu."""

    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle_monitor: Callable[[], None],
        on_quit: Callable[[], None],
        is_monitoring: Callable[[], bool],
    ) -> None:
        self._on_show = on_show
        self._on_toggle_monitor = on_toggle_monitor
        self._on_quit = on_quit
        self._is_monitoring = is_monitoring
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import pystray
        from pystray import MenuItem

        image = _create_icon_image()

        def _monitor_text(_item: MenuItem) -> str:
            return "暫停監聽" if self._is_monitoring() else "開始監聽"

        menu = pystray.Menu(
            MenuItem("顯示主畫面", lambda: self._on_show(), default=True),
            pystray.Menu.SEPARATOR,
            MenuItem(_monitor_text, lambda: self._on_toggle_monitor()),
            pystray.Menu.SEPARATOR,
            MenuItem("完全退出", lambda: self._on_quit()),
        )

        self._icon = pystray.Icon(
            name="HelloStreamer",
            icon=image,
            title="哈嘍主播  Hello Streamer",
            menu=menu,
        )

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def update_tooltip(self, text: str) -> None:
        if self._icon:
            self._icon.title = text
