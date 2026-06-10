"""Shared UI helpers, constants, and tooltip for the main application."""

from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageDraw

from stream_monitor import i18n
from stream_monitor.i18n import tr
from stream_monitor.util import parse_iso_datetime

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
_FONT_FAMILY = "Microsoft JhengHei UI"
if platform.system() != "Windows":
    _FONT_FAMILY = "sans-serif"


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=size, weight=weight)


_BTN_PAD_X = 32


def _measure_text(text: str, *, size: int = 13, weight: str = "normal") -> int:
    return int(_font(size, weight).measure(text))


def _button_width(
    text: str,
    *,
    min_width: int,
    size: int = 13,
    weight: str = "normal",
    padding: int = _BTN_PAD_X,
) -> int:
    return max(min_width, _measure_text(text, size=size, weight=weight) + padding)


def _fit_button(
    button: ctk.CTkButton,
    text: str,
    *,
    min_width: int,
    size: int = 13,
    weight: str = "normal",
) -> None:
    button.configure(
        text=text,
        width=_button_width(text, min_width=min_width, size=size, weight=weight),
    )


def _fit_option_menu(
    menu: ctk.CTkOptionMenu,
    values: list[str],
    *,
    min_width: int,
    size: int = 12,
) -> None:
    if values:
        text_w = max(_measure_text(v, size=size) for v in values)
        menu.configure(width=max(min_width, text_w + 48))
    else:
        menu.configure(width=min_width)


def _fit_label_width(
    label: ctk.CTkLabel,
    text: str,
    *,
    min_width: int,
    size: int = 13,
    weight: str = "normal",
    padding: int = 14,
) -> None:
    label.configure(
        text=text,
        width=max(min_width, _measure_text(text, size=size, weight=weight) + padding),
    )


def _status_row_label_width() -> int:
    keys = (
        "status.row.placeholder",
        "status.row.paused",
        "status.row.upcoming",
        "status.row.live",
        "status.row.offline",
    )
    return max(
        72,
        max(_measure_text(tr(k), size=12, weight="bold") for k in keys) + 16,
    )


def _status_bar_text_width() -> int:
    keys = (
        "status.idle",
        "status.trigger_running",
        "status.watching",
        "status.stopped",
        "status.monitor_restarted",
    )
    return max(96, max(_measure_text(tr(k)) for k in keys) + 16)


def _language_icon(size: int = 20) -> ctk.CTkImage:
    """Create a crisp globe icon without relying on emoji font rendering."""
    scale = 4
    canvas = size * scale
    pad = 2 * scale
    stroke = 2 * scale
    color = "#d8d8e5"

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (pad, pad, canvas - pad - 1, canvas - pad - 1)

    draw.ellipse(box, outline=color, width=stroke)
    draw.arc(
        (pad + 5 * scale, pad, canvas - pad - 5 * scale - 1, canvas - pad - 1),
        88,
        272,
        fill=color,
        width=stroke,
    )
    draw.arc(
        (pad + 5 * scale, pad, canvas - pad - 5 * scale - 1, canvas - pad - 1),
        -92,
        92,
        fill=color,
        width=stroke,
    )
    draw.arc(
        (pad, pad + 4 * scale, canvas - pad - 1, canvas - pad - 4 * scale - 1),
        18,
        162,
        fill=color,
        width=stroke,
    )
    draw.arc(
        (pad, pad + 4 * scale, canvas - pad - 1, canvas - pad - 4 * scale - 1),
        198,
        342,
        fill=color,
        width=stroke,
    )

    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


def _format_minutes_delta(total_seconds: float) -> str:
    if 0 < total_seconds < 60:
        return tr("status.row.elapsed.under_one_min")
    minutes = max(0, int(total_seconds // 60))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _format_countdown(target: str) -> str:
    dt = parse_iso_datetime(target)
    if dt is None:
        return ""
    return _format_minutes_delta((dt - datetime.now(timezone.utc)).total_seconds())


def _format_elapsed(started_at: str) -> str:
    dt = parse_iso_datetime(started_at)
    if dt is None:
        return ""
    return _format_minutes_delta((datetime.now(timezone.utc) - dt).total_seconds())


def _format_row_time(state: str, duration: str) -> str:
    """Wrap a formatted duration with a row-level i18n label."""
    if not duration:
        return ""
    if state == "live":
        return tr("status.row.time.live", elapsed=duration)
    if state == "offline":
        return tr("status.row.time.offline", elapsed=duration)
    if state in ("upcoming", "countdown"):
        return tr("status.row.time.starts_in", countdown=duration)
    return duration


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PLATFORM_OPTIONS = ["twitch", "youtube"]
ACTION_KEYS: list[str] = [
    "open_and_stop",
    "open_and_keep",
    "notify_only",
    "open_and_exit",
]


def _action_labels() -> dict[str, str]:
    """Return ``{key: localized_label}`` for every supported action."""
    return {key: tr(f"action.{key}") for key in ACTION_KEYS}


def _action_displays() -> list[str]:
    """Localized labels in canonical order — used by the OptionMenu."""
    labels = _action_labels()
    return [labels[k] for k in ACTION_KEYS]


def _action_key_for_display(display: str) -> str:
    """Reverse-lookup the action key for a localized display string.

    Falls back to ``open_and_stop`` when nothing matches, so a stale display
    string after a language switch never breaks ``_ensure_monitor_running``.
    """
    for key, label in _action_labels().items():
        if label == display:
            return key
    return "open_and_stop"

_CLR_BG_DARK = "#1a1a2e"
_CLR_CARD = "#16213e"
_CLR_ACCENT = "#0f3460"
_CLR_LIVE = "#00e676"
_CLR_OFFLINE = "#666677"
_CLR_TWITCH = "#9146FF"
_CLR_YOUTUBE = "#FF0000"
_CLR_START = "#2e7d32"
_CLR_START_HOVER = "#1b5e20"
_CLR_STOP = "#c62828"
_CLR_STOP_HOVER = "#8e0000"
_CLR_ADD = "#0f3460"
_CLR_ADD_HOVER = "#1a4a7a"
_CLR_DELETE_HOVER = "#c62828"
_CLR_CARD_DISABLED = "#0e1528"
_CLR_TEXT_DISABLED = "#3a3a4a"
_CLR_LINK = "#2196F3"
_CLR_LINK_HOVER = "#1769aa"

_MIN_WINDOW_WIDTH = 920
_MIN_WINDOW_HEIGHT = 560
_DEFAULT_WINDOW_GEOMETRY = f"{_MIN_WINDOW_WIDTH}x580"


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------
class _Tooltip:
    """Lightweight hover tooltip for any tkinter/CTk widget.

    Supports two modes:
      - **Static text** — provide ``text``. Use ``set_text(...)`` to change it
        (e.g. status-driven tooltips that depend on live stream state).
      - **i18n key** — provide ``key`` (with optional format kwargs). The
        tooltip automatically re-fetches its text whenever the active language
        changes, so hover popups stay localized without any caller plumbing.

    Both modes can be swapped at runtime via :meth:`set_text`.
    """

    _DELAY_MS = 400
    _BG = "#2a2a3e"
    _FG = "#e0e0ee"
    _BORDER = "#555566"

    def __init__(
        self,
        widget: Any,
        text: str = "",
        *,
        key: str | None = None,
        format_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._widget = widget
        self.text = text
        self._key = key
        self._format_kwargs: dict[str, Any] = dict(format_kwargs or {})
        self._tip_window: Any | None = None
        self._after_id: str | None = None
        self._unsub: Callable[[], None] | None = None

        if key is not None:
            self._refresh_from_key()
            self._unsub = i18n.subscribe(self._refresh_from_key)

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")
        widget.bind("<ButtonPress>", self._cancel, add="+")
        widget.bind("<Destroy>", self._on_widget_destroy, add="+")

    def _refresh_from_key(self) -> None:
        if self._key is None:
            return
        new_text = tr(self._key, **self._format_kwargs)
        if new_text == self.text:
            return
        self.text = new_text
        if self._tip_window is not None:
            for child in self._tip_window.winfo_children():
                try:
                    child.configure(text=new_text)
                except Exception:  # noqa: BLE001
                    pass

    def set_text(
        self,
        text: str = "",
        *,
        key: str | None = None,
        **format_kwargs: Any,
    ) -> None:
        """Reassign the tooltip content (static text or i18n key)."""
        if key is not None:
            self._key = key
            self._format_kwargs = dict(format_kwargs)
            self._refresh_from_key()
            if self._unsub is None:
                self._unsub = i18n.subscribe(self._refresh_from_key)
        else:
            if self._unsub:
                self._unsub()
                self._unsub = None
            self._key = None
            self._format_kwargs = {}
            self.text = text

    def _on_widget_destroy(self, _event: Any = None) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _cancel(self, _event: Any = None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip_window or not self.text:
            return
        import tkinter as tk

        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4

        tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)

        label = tk.Label(
            tw,
            text=self.text,
            background=self._BG,
            foreground=self._FG,
            relief="solid",
            borderwidth=1,
            highlightbackground=self._BORDER,
            font=(_FONT_FAMILY, 10),
            padx=8,
            pady=4,
        )
        label.pack()

        tw.update_idletasks()
        tip_w = tw.winfo_width()
        screen_w = tw.winfo_screenwidth()
        if x + tip_w > screen_w - 8:
            x = screen_w - tip_w - 8
        if x < 8:
            x = 8
        tw.wm_geometry(f"+{x}+{y}")

        self._tip_window = tw

    def _hide(self) -> None:
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None


def _tooltip(widget: Any, text: str) -> _Tooltip:
    """Attach a static (non-translated) hover tooltip."""
    return _Tooltip(widget, text)


def _tooltip_tr(widget: Any, key: str, **kwargs: Any) -> _Tooltip:
    """Attach a translated tooltip that updates on language change."""
    return _Tooltip(widget, key=key, format_kwargs=kwargs)


def _clamped_window_geometry(saved_geometry: str | None) -> str:
    """Keep older saved window sizes from squeezing fixed-width controls."""
    if not saved_geometry:
        return _DEFAULT_WINDOW_GEOMETRY

    match = re.match(r"^(\d+)x(\d+)((?:[+-]\d+){2})?$", saved_geometry)
    if not match:
        return _DEFAULT_WINDOW_GEOMETRY

    width = max(int(match.group(1)), _MIN_WINDOW_WIDTH)
    height = max(int(match.group(2)), _MIN_WINDOW_HEIGHT)
    position = match.group(3) or ""
    return f"{width}x{height}{position}"
