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
    """System tray icon with right-click menu.

    Monitor states surfaced by ``get_mode``:
      - ``"idle"``   — not monitoring
      - ``"trigger"`` — monitoring with trigger actions enabled
      - ``"watch"``   — monitoring without triggers (read-only)
    """

    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle_monitor: Callable[[], None],
        on_quit: Callable[[], None],
        is_monitoring: Callable[[], bool] | None = None,
        on_watch_only: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        get_mode: Callable[[], str] | None = None,
    ) -> None:
        self._on_show = on_show
        self._on_toggle_monitor = on_toggle_monitor
        self._on_watch_only = on_watch_only
        self._on_stop = on_stop
        self._on_quit = on_quit
        self._is_monitoring = is_monitoring or (
            lambda: (get_mode() if get_mode else "idle") != "idle"
        )
        self._get_mode = get_mode or (
            lambda: "trigger" if (is_monitoring and is_monitoring()) else "idle"
        )
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import pystray
        from pystray import MenuItem

        image = _create_icon_image()

        def _trigger_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            if mode == "trigger":
                return "✓ 監聽+觸發中"
            return "開始監聽+觸發"

        def _watch_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            if mode == "watch":
                return "✓ 只監測中"
            return "切換為只監測"

        def _stop_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            return "已停止" if mode == "idle" else "停止監聽"

        menu_items = [
            MenuItem("顯示主畫面", lambda: self._on_show(), default=True),
            pystray.Menu.SEPARATOR,
            MenuItem(_trigger_text, lambda: self._on_toggle_monitor()),
        ]

        if self._on_watch_only is not None:
            menu_items.append(MenuItem(_watch_text, lambda: self._on_watch_only()))

        if self._on_stop is not None:
            menu_items.append(
                MenuItem(
                    _stop_text,
                    lambda: self._on_stop(),
                    enabled=lambda _item: self._get_mode() != "idle",
                )
            )

        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                MenuItem("完全退出", lambda: self._on_quit()),
            ]
        )

        menu = pystray.Menu(*menu_items)

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
