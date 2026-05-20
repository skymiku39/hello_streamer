"""系統匣常駐 — pystray 整合，提供右鍵選單與最小化功能。"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from stream_monitor import i18n
from stream_monitor.i18n import tr

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
        # Track the *key* (not the resolved string) for the current tooltip so
        # we can re-render it when the active language changes.
        self._tooltip_key: str = "tray.tooltip.default"
        self._unsub_i18n: Callable[[], None] | None = None

    def start(self) -> None:
        import pystray
        from pystray import MenuItem

        image = _create_icon_image()

        def _trigger_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            if mode == "trigger":
                return tr("tray.trigger_active")
            return tr("tray.start_trigger")

        def _watch_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            if mode == "watch":
                return tr("tray.watch_active")
            return tr("tray.start_watch")

        def _stop_text(_item: MenuItem) -> str:
            mode = self._get_mode()
            return tr("tray.stopped") if mode == "idle" else tr("tray.stop")

        menu_items = [
            MenuItem(lambda _i: tr("tray.show"), lambda: self._on_show(), default=True),
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
                MenuItem(lambda _i: tr("tray.quit"), lambda: self._on_quit()),
            ]
        )

        menu = pystray.Menu(*menu_items)

        self._icon = pystray.Icon(
            name="HelloStreamer",
            icon=image,
            title=tr(self._tooltip_key),
            menu=menu,
        )

        self._unsub_i18n = i18n.subscribe(self._on_language_changed)

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _on_language_changed(self) -> None:
        """Refresh tooltip + menu when the language switches at runtime."""
        if not self._icon:
            return
        try:
            self._icon.title = tr(self._tooltip_key)
            # pystray re-evaluates callable labels each time the menu is
            # shown, so update_menu() forces a redraw of the static items
            # (show / quit) and any cached label state.
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to refresh tray icon for language change")

    def stop(self) -> None:
        if self._unsub_i18n:
            try:
                self._unsub_i18n()
            except Exception:  # noqa: BLE001
                pass
            self._unsub_i18n = None
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def update_tooltip(self, text: str) -> None:
        """Set the tray tooltip to a literal (non-translated) string."""
        self._tooltip_key = "tray.tooltip.default"  # marker: literal in use
        if self._icon:
            self._icon.title = text

    def update_tooltip_key(self, key: str) -> None:
        """Set the tray tooltip via an i18n key (auto-updates on lang change)."""
        self._tooltip_key = key
        if self._icon:
            self._icon.title = tr(key)
