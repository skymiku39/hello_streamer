"""Shared UI helpers, constants, and tooltip for the main application."""

from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageDraw

from stream_monitor import i18n
from stream_monitor.action_plan import ACTION_ORDER
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


_STATUS_NAME_MAX_LEN = 22 * 3


def _truncate_status_name(
    name: str, *, max_len: int = _STATUS_NAME_MAX_LEN
) -> str:
    name = (name or "").strip()
    if len(name) <= max_len:
        return name
    return name[: max(0, max_len - 1)] + "…"


def _status_bar_text_width() -> int:
    """Fixed width for the compact status column (main + sub line).

    Sized to the longest localized main/sub strings for the *current*
    language so status and language changes cannot clip the green
    running label inside the CompactFlowFrame acts group.
    """
    main_keys = (
        "status.idle",
        "status.trigger_running",
        "status.trigger_once_running",
        "status.watching",
        "status.watching_once",
        "status.stopped",
        "status.monitor_restarted",
    )
    main_w = max(_measure_text(tr(k)) for k in main_keys)
    sub_w = max(
        _measure_text(tr("status.awaiting_start")),
        _measure_text(tr("status.poll_waiting")),
        _measure_text(tr("status.poll_checking", name="RunRunLuna")),
        _measure_text(tr("status.poll_refreshing", name="RunRunLuna")),
    )
    return max(96, main_w, sub_w) + 16


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


def compose_monitor_mode(kind: str, *, once: bool) -> str:
    """Compose a global monitor mode from compact kind × duration selectors."""
    if kind == "watch":
        return "watch_once" if once else "watch"
    return "trigger_once" if once else "trigger"


def decompose_monitor_mode(mode: str) -> tuple[str, bool]:
    """Split a monitor mode into ``(kind, once)`` for compact selectors.

    Unknown / idle modes fall back to continuous trigger so the segments
    always show a valid selection before the user presses Start.
    """
    if mode in ("watch", "watch_once"):
        return "watch", mode.endswith("_once")
    if mode in ("trigger", "trigger_once"):
        return "trigger", mode.endswith("_once")
    return "trigger", False


def monitor_mode_button_states(mode: str) -> dict[str, str]:
    """Map a monitor mode to logical start/stop enablement.

    Pure decision separated from the Tk side-effect in
    :meth:`App._apply_monitor_mode_buttons`, so the "which action is enabled
    in which run state" contract is unit-testable without a display.

    Keys keep the historical names (``start`` / ``watch`` / ``start_once`` /
    ``watch_once`` / ``stop``) so callers can resolve the compact Start
    button from the currently selected kind×duration composition.

    - ``trigger`` / ``watch``: the selected continuous mode is disabled.
    - ``trigger_once`` / ``watch_once``: the selected one-cycle mode is
      disabled while that cycle is running.
    - ``idle``: all start actions are available and Stop is disabled.
    """
    states = {
        "start": "normal",
        "watch": "normal",
        "start_once": "normal",
        "watch_once": "normal",
        "stop": "disabled",
    }
    selected = {
        "trigger": "start",
        "watch": "watch",
        "trigger_once": "start_once",
        "watch_once": "watch_once",
    }.get(mode)
    if selected is not None:
        states[selected] = "disabled"
        states["stop"] = "normal"
    return states


def compact_run_button_key(kind: str, *, once: bool) -> str:
    """Map compact selectors to a :func:`monitor_mode_button_states` key."""
    return {
        ("trigger", False): "start",
        ("watch", False): "watch",
        ("trigger", True): "start_once",
        ("watch", True): "watch_once",
    }[(kind, once)]


def compact_control_button_states(
    mode: str, *, kind: str, once: bool
) -> dict[str, str]:
    """Enablement for the compact Start / Stop pair given current selectors."""
    states = monitor_mode_button_states(mode)
    run_key = compact_run_button_key(kind, once=once)
    return {"start": states[run_key], "stop": states["stop"]}


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


def _format_row_time(
    state: str, duration: str, *, ended_at_source: str = ""
) -> str:
    """Wrap a formatted duration with a row-level i18n label."""
    if not duration:
        if state == "offline":
            if ended_at_source == "pending":
                return tr("status.row.time.pending_detail")
            return tr("status.row.time.no_data")
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
ACTION_KEYS: list[str] = list(ACTION_ORDER)


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
# Compact control-panel accents (dark adaptation of the v5 HTML mock).
_CLR_PANEL_BORDER = "#3a3a55"
_CLR_SEG_BORDER = "#4a4a66"
_CLR_TEXT_SECONDARY = "#9aa0b4"
_CLR_TEXT_MUTED = "#7f8499"
_CLR_STATUS_DOT_IDLE = "#6d6b67"

_BUTTON_DEFAULTS = {
    "height": 34,
    "corner_radius": 8,
    "fg_color": "#263653",
    "hover_color": "#354866",
    "border_width": 1,
    "border_color": "#465570",
    "text_color": "#e6eaf2",
    "text_color_disabled": "#7f8499",
}


class AppButton(ctk.CTkButton):
    """Application button defaults, independent of the toolkit's theme/font.

    Callers may override colors for semantic actions (start, stop, delete),
    while close/cancel buttons share the neutral application style.
    """

    def __init__(self, master: Any, **kwargs: Any) -> None:
        options = {**_BUTTON_DEFAULTS, **kwargs}
        if "font" not in options:
            options["font"] = _font(13)
        if "width" not in options:
            options["width"] = _button_width(options.get("text", ""), min_width=88)
        super().__init__(master, **options)

_MIN_WINDOW_WIDTH = 920
_MIN_WINDOW_HEIGHT = 560
_DEFAULT_WINDOW_GEOMETRY = f"{_MIN_WINDOW_WIDTH}x580"
_COMPACT_CTRL_PAD_X = 16
_COMPACT_CTRL_PAD_Y = 6
_COMPACT_LINE_GAP = 4
_COMPACT_ACT_BTN_HEIGHT = 34
_COMPACT_SEG_HEIGHT = 26
_COMPACT_FIELD_HEIGHT = 30
_COMPACT_FLOW_HGAP = 14
_COMPACT_FLOW_VGAP = 4


def layout_flow_rows(
    widths: list[int],
    *,
    avail: int,
    hgap: int = _COMPACT_FLOW_HGAP,
) -> list[list[int]]:
    """Compute CSS flex-wrap style row membership for item widths.

    Returns a list of rows; each row is a list of item indices. An item wider
    than ``avail`` still occupies its own row (never clipped by packing).
    """
    if avail < 1:
        avail = 1
    rows: list[list[int]] = []
    row: list[int] = []
    used = 0
    for idx, width in enumerate(widths):
        w = max(int(width), 1)
        need = w if not row else used + hgap + w
        if row and need > avail:
            rows.append(row)
            row = [idx]
            used = w
        else:
            row.append(idx)
            used = need if row[:-1] else w
    if row:
        rows.append(row)
    return rows


class CompactFlowFrame(ctk.CTkFrame):
    """Left-to-right wrapping container (Tk analogue of CSS ``flex-wrap``)."""

    def __init__(
        self,
        master: Any,
        *,
        hgap: int = _COMPACT_FLOW_HGAP,
        vgap: int = _COMPACT_FLOW_VGAP,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("fg_color", "transparent")
        kwargs.setdefault("height", 1)
        super().__init__(master, **kwargs)
        self._hgap = hgap
        self._vgap = vgap
        self._items: list[Any] = []
        self._reflowing = False
        self._reflow_after: str | None = None
        self._last_key: tuple[Any, ...] | None = None
        self.pack_propagate(False)
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Map>", self._on_configure, add="+")

    def add(self, widget: Any) -> None:
        """Register ``widget`` as a wrap unit (caller must not pack it)."""
        self._items.append(widget)
        widget.bind("<Configure>", lambda _event: self.request_reflow(), add="+")
        self.request_reflow()

    def _on_configure(self, _event: Any = None) -> None:
        # CTkFrame.bind forwards to its internal canvas. Its Configure/Map
        # event therefore names that canvas, not this public frame. Filtering
        # on event.widget is self would discard the first usable size.
        self.request_reflow()

    def request_reflow(self) -> None:
        """Coalesce layout changes after Tk has negotiated natural sizes."""
        if self._reflow_after is None:
            self._reflow_after = self.after_idle(self._flush_reflow)

    def _flush_reflow(self) -> None:
        self._reflow_after = None
        self.reflow()

    def destroy(self) -> None:
        if self._reflow_after is not None:
            self.after_cancel(self._reflow_after)
            self._reflow_after = None
        super().destroy()

    def reflow(self) -> None:
        """Place children into wrapping rows for the current width."""
        if self._reflowing or not self._items:
            return
        self._reflowing = True
        try:
            avail = int(self.winfo_width())
            if avail <= 2:
                # Not mapped / not sized yet — wait for the next Configure.
                return
            sizes: list[tuple[int, int]] = []
            for widget in self._items:
                sizes.append(
                    (
                        max(int(widget.winfo_reqwidth()), 1),
                        max(int(widget.winfo_reqheight()), 1),
                    )
                )
            widths = [w for w, _h in sizes]
            # winfo dimensions are physical pixels; CTk configure/place accept
            # logical dimensions. Never feed a scaled request back as a new
            # widget size, which compounds scaling on every Configure event.
            hgap = round(self._apply_widget_scaling(self._hgap))
            vgap = round(self._apply_widget_scaling(self._vgap))
            key = (avail, tuple(sizes), hgap, vgap, self._get_widget_scaling())
            if key == self._last_key:
                return
            rows = layout_flow_rows(widths, avail=avail, hgap=hgap)
            y = 0
            total_h = 0
            for row in rows:
                x = 0
                row_h = 0
                for idx in row:
                    ww, hh = sizes[idx]
                    widget = self._items[idx]
                    # Keep each child's natural request, including later font
                    # and locale changes. Child Configure schedules a new pass.
                    widget.place(
                        x=self._reverse_widget_scaling(x),
                        y=self._reverse_widget_scaling(y),
                    )
                    x += ww + hgap
                    row_h = max(row_h, hh)
                total_h = y + row_h
                y += row_h + vgap
            self._last_key = key
            self.configure(height=max(1, ceil(self._reverse_widget_scaling(total_h))))
        finally:
            self._reflowing = False


# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------
def _clamp_tooltip_position(
    x: int,
    y: int,
    *,
    tip_w: int,
    tip_h: int,
    vroot_x: int,
    vroot_y: int,
    vroot_w: int,
    vroot_h: int,
    margin: int = 8,
) -> tuple[int, int]:
    """Keep tooltip within the virtual desktop (multi-monitor safe).

    ``winfo_screenwidth()`` only covers the primary monitor and starts at 0.
    On setups with monitors to the left or above the primary, widget root
    coordinates can be negative; clamping against ``0..screenwidth`` would
    pull the tooltip onto the primary monitor.
    """
    min_x = vroot_x + margin
    min_y = vroot_y + margin
    max_x = vroot_x + vroot_w - tip_w - margin
    max_y = vroot_y + vroot_h - tip_h - margin

    if max_x < min_x:
        x = min_x
    elif x > max_x:
        x = max_x
    elif x < min_x:
        x = min_x

    if max_y < min_y:
        y = min_y
    elif y > max_y:
        y = max_y
    elif y < min_y:
        y = min_y

    return x, y


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
        tip_h = tw.winfo_height()
        x, y = _clamp_tooltip_position(
            x,
            y,
            tip_w=tip_w,
            tip_h=tip_h,
            vroot_x=tw.winfo_vrootx(),
            vroot_y=tw.winfo_vrooty(),
            vroot_w=tw.winfo_vrootwidth(),
            vroot_h=tw.winfo_vrootheight(),
        )
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
