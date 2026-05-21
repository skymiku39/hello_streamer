"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import queue
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import customtkinter as ctk
from PIL import Image, ImageDraw

from stream_monitor import base_dir, config_manager, i18n
from stream_monitor.config_manager import DEFAULT_BROWSER_SETTINGS
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.notifier import (
    action_for_stream_status,
    close_browser_window_for_url,
    detect_browser_family,
    execute_action,
    open_url,
)
from stream_monitor.single_instance import SingleInstance
from stream_monitor.startup import disable_startup, enable_startup, is_startup_enabled
from stream_monitor.tray import TrayIcon
from stream_monitor.url_parser import parse_url

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------
_FONT_FAMILY = "Microsoft JhengHei UI"
if platform.system() != "Windows":
    _FONT_FAMILY = "sans-serif"


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_FONT_FAMILY, size=size, weight=weight)


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


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_minutes_delta(total_seconds: float) -> str:
    minutes = max(0, int(total_seconds // 60))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _format_countdown(target: str) -> str:
    dt = _parse_iso_datetime(target)
    if dt is None:
        return ""
    return _format_minutes_delta((dt - datetime.now(timezone.utc)).total_seconds())


def _format_elapsed(started_at: str) -> str:
    dt = _parse_iso_datetime(started_at)
    if dt is None:
        return ""
    return _format_minutes_delta((datetime.now(timezone.utc) - dt).total_seconds())


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

_MIN_WINDOW_WIDTH = 860
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


# ═══════════════════════════════════════════════════════════════════════════
# Add Channel Dialog
# ═══════════════════════════════════════════════════════════════════════════
class AddChannelDialog(ctk.CTkToplevel):
    """Modal dialog — supports both URL paste and manual input."""

    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title(tr("add.title"))
        self.geometry("680x430")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self.result: dict[str, str] | None = None

        self._heading_label = ctk.CTkLabel(
            self,
            text=tr("add.heading"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._heading_label.pack(padx=24, pady=(20, 4), fill="x")

        url_frame = ctk.CTkFrame(self, fg_color="transparent")
        url_frame.pack(padx=24, fill="x")

        self.url_entry = ctk.CTkEntry(
            url_frame,
            placeholder_text=tr("add.url.placeholder"),
            font=_font(13),
            height=38,
        )
        self.url_entry.pack(fill="x")
        self.url_entry.bind("<KeyRelease>", self._on_url_change)

        self._url_hint_label = ctk.CTkLabel(
            self,
            text=tr("add.url.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=620,
        )
        self._url_hint_label.pack(padx=24, pady=(6, 0), fill="x")

        self._url_warning_label = ctk.CTkLabel(
            self,
            text=tr("add.url.warning"),
            font=_font(12, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=620,
        )
        self._url_warning_label.pack(padx=24, pady=(4, 0), fill="x")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=24, anchor="w", wraplength=620
        )
        self.message_label.pack(padx=24, pady=(4, 0), fill="x")
        self._message_key: tuple[str, dict[str, Any]] | None = None

        sep = ctk.CTkFrame(self, height=1, fg_color="#333355")
        sep.pack(padx=24, pady=12, fill="x")

        self._manual_heading_label = ctk.CTkLabel(
            self,
            text=tr("add.manual.heading"),
            font=_font(12),
            text_color="#888899",
            anchor="w",
        )
        self._manual_heading_label.pack(padx=24, fill="x")

        manual_frame = ctk.CTkFrame(self, fg_color="transparent")
        manual_frame.pack(padx=24, pady=(6, 0), fill="x")
        manual_frame.grid_columnconfigure(1, weight=1)

        self._platform_label = ctk.CTkLabel(
            manual_frame, text=tr("add.manual.platform"), font=_font(13), anchor="w"
        )
        self._platform_label.grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.platform_var = ctk.StringVar(value="twitch")
        self.platform_menu = ctk.CTkOptionMenu(
            manual_frame,
            variable=self.platform_var,
            values=PLATFORM_OPTIONS,
            font=_font(13),
            dropdown_font=_font(13),
            width=120,
            height=34,
        )
        self.platform_menu.grid(row=0, column=1, sticky="w")

        self._name_label = ctk.CTkLabel(
            manual_frame, text=tr("add.manual.name"), font=_font(13), anchor="w"
        )
        self._name_label.grid(row=1, column=0, padx=(0, 10), pady=(8, 0), sticky="w")
        self.name_entry = ctk.CTkEntry(
            manual_frame,
            placeholder_text=tr("add.manual.name.placeholder"),
            font=_font(13),
            height=34,
        )
        self.name_entry.grid(row=1, column=1, pady=(8, 0), sticky="ew")

        self._manual_hint_label = ctk.CTkLabel(
            self,
            text=tr("add.manual.hint"),
            font=_font(12),
            text_color="#888899",
            anchor="w",
            wraplength=620,
        )
        self._manual_hint_label.pack(padx=24, pady=(6, 0), fill="x")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=48)
        btn_frame.pack(padx=24, pady=(14, 22), fill="x")
        btn_frame.pack_propagate(False)

        self._cancel_btn = ctk.CTkButton(
            btn_frame,
            text=tr("add.btn.cancel"),
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self.destroy,
        )
        self._cancel_btn.pack(side="right", padx=(8, 0), pady=4)

        self._add_btn = ctk.CTkButton(
            btn_frame,
            text=tr("add.btn.add"),
            width=104,
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_add,
        )
        self._add_btn.pack(side="right", pady=4)

        self.url_entry.bind("<Return>", lambda _: self._on_add())
        self.name_entry.bind("<Return>", lambda _: self._on_add())

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _retranslate(self) -> None:
        try:
            self.title(tr("add.title"))
        except Exception:  # noqa: BLE001
            return
        self._heading_label.configure(text=tr("add.heading"))
        self.url_entry.configure(placeholder_text=tr("add.url.placeholder"))
        self._url_hint_label.configure(text=tr("add.url.hint"))
        self._url_warning_label.configure(text=tr("add.url.warning"))
        self._manual_heading_label.configure(text=tr("add.manual.heading"))
        self._platform_label.configure(text=tr("add.manual.platform"))
        self._name_label.configure(text=tr("add.manual.name"))
        self.name_entry.configure(placeholder_text=tr("add.manual.name.placeholder"))
        self._manual_hint_label.configure(text=tr("add.manual.hint"))
        self._cancel_btn.configure(text=tr("add.btn.cancel"))
        self._add_btn.configure(text=tr("add.btn.add"))
        if self._message_key is not None:
            key, kwargs = self._message_key
            self.message_label.configure(text=tr(key, **kwargs))

    def _on_destroy(self, _event: Any = None) -> None:
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None

    def _set_message(
        self, key: str | None, *, color: str = "gray", **kwargs: Any
    ) -> None:
        if key is None:
            self._message_key = None
            self.message_label.configure(text="", text_color=color)
            return
        self._message_key = (key, dict(kwargs))
        self.message_label.configure(text=tr(key, **kwargs), text_color=color)

    def _on_url_change(self, _event: Any = None) -> None:
        text = self.url_entry.get()
        parsed = parse_url(text)
        if parsed:
            self._set_message(
                "add.msg.parsed",
                color=_CLR_LIVE,
                platform_upper=parsed.platform.upper(),
                name=parsed.name,
            )
            self.platform_var.set(parsed.platform)
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, parsed.name)
        else:
            if text.strip():
                self._set_message("add.msg.unparseable", color="#ffb74d")
            else:
                self._set_message(None)

    def _on_add(self) -> None:
        url_text = self.url_entry.get().strip()
        parsed = parse_url(url_text)

        if parsed:
            plat, name = parsed.platform, parsed.name
        elif url_text:
            self._set_message("add.msg.invalid_url", color="#ffb74d")
            self.url_entry.focus_set()
            return
        else:
            name = self.name_entry.get().strip()
            plat = self.platform_var.get()
            if not name:
                self._set_message("add.msg.empty", color="#ffb74d")
                self.name_entry.focus_set()
                return

        self._set_message("add.msg.validating", color="#64b5f6")
        self._set_inputs_enabled(False)
        self._pending_platform = plat
        self._pending_name = name

        threading.Thread(
            target=self._validate_channel, args=(plat, name), daemon=True
        ).start()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.url_entry.configure(state=state)
        self.name_entry.configure(state=state)
        self.platform_menu.configure(state=state)
        for w in self.winfo_children():
            if isinstance(w, ctk.CTkFrame):
                for child in w.winfo_children():
                    if isinstance(child, ctk.CTkButton):
                        child.configure(state=state)

    def _validate_channel(self, plat: str, name: str) -> None:
        try:
            fetcher = get_fetcher(plat)
            info = fetcher.get_stream_info(name)
        except Exception:
            info = None
        self.after(0, self._on_validate_done, info)

    def _on_validate_done(self, info: StreamInfo | None) -> None:
        plat = self._pending_platform
        name = self._pending_name

        if info is None:
            self._set_message(
                "add.msg.not_found",
                color="#ef5350",
                platform_upper=plat.upper(),
                name=name,
            )
            self._set_inputs_enabled(True)
            return

        self.result = {"platform": plat, "name": name}
        if info.display_name:
            self.result["display_name"] = info.display_name
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Language Dialog
# ═══════════════════════════════════════════════════════════════════════════
class LanguageDialog(ctk.CTkToplevel):
    """Modal picker for switching the active UI language at runtime."""

    def __init__(
        self,
        parent: ctk.CTk,
        on_apply: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(tr("lang.title"))
        self.geometry("420x420")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()

        self._on_apply = on_apply
        self._initial_lang = i18n.current_language()
        self._selected_lang = ctk.StringVar(value=self._initial_lang)
        self._row_widgets: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel]] = {}

        self._heading_label = ctk.CTkLabel(
            self,
            text=tr("lang.heading"),
            font=_font(16, "bold"),
            anchor="w",
        )
        self._heading_label.pack(padx=22, pady=(22, 4), fill="x")

        self._description_label = ctk.CTkLabel(
            self,
            text=tr("lang.description"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self._description_label.pack(padx=22, pady=(0, 14), fill="x")

        for code, native_label, english_label in i18n.available_languages():
            row = ctk.CTkFrame(
                self,
                fg_color=_CLR_CARD,
                border_width=1,
                border_color="#333355",
                corner_radius=8,
                height=48,
                cursor="hand2",
            )
            row.pack(padx=22, pady=(0, 8), fill="x")
            row.pack_propagate(False)

            name_label = ctk.CTkLabel(
                row,
                text=native_label,
                font=_font(13, "bold"),
                anchor="w",
            )
            name_label.pack(side="left", padx=(14, 6), fill="x", expand=True)

            status_label = ctk.CTkLabel(
                row,
                text=english_label,
                font=_font(11),
                text_color="#888899",
                anchor="e",
            )
            status_label.pack(side="right", padx=14)

            for widget in (row, name_label, status_label):
                widget.bind("<Button-1>", lambda _e, c=code: self._select(c))

            self._row_widgets[code] = (row, name_label, status_label)

        footer = ctk.CTkFrame(self, fg_color="transparent", height=52)
        footer.pack(padx=22, pady=(6, 18), fill="x", side="bottom")
        footer.pack_propagate(False)

        self._close_btn = ctk.CTkButton(
            footer,
            text=tr("lang.btn.close"),
            width=96,
            height=36,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self._on_close,
        )
        self._close_btn.pack(side="right", padx=(8, 0), pady=8)

        self._apply_btn = ctk.CTkButton(
            footer,
            text=tr("lang.btn.apply"),
            width=96,
            height=36,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_apply_btn,
        )
        self._apply_btn.pack(side="right", pady=8)

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._update_row_visuals()

    def _select(self, code: str) -> None:
        if code not in i18n.LANGUAGE_CODES:
            return
        self._selected_lang.set(code)
        self._update_row_visuals()

    def _update_row_visuals(self) -> None:
        current = i18n.current_language()
        selected = self._selected_lang.get()
        for code, (row, _name, status) in self._row_widgets.items():
            is_selected = code == selected
            is_current = code == current
            border = _CLR_LINK if is_selected else "#333355"
            row.configure(border_color=border)
            if is_current and is_selected:
                status.configure(text=tr("lang.option.current"), text_color=_CLR_LIVE)
            elif is_selected:
                status.configure(text=tr("lang.option.selected"), text_color=_CLR_LINK)
            elif is_current:
                status.configure(text=tr("lang.option.current"), text_color=_CLR_LINK)
            else:
                _native, english = self._labels_for(code)
                status.configure(text=english, text_color="#888899")

    @staticmethod
    def _labels_for(code: str) -> tuple[str, str]:
        for c, native, english in i18n.available_languages():
            if c == code:
                return native, english
        return code, code

    def _on_apply_btn(self) -> None:
        new_code = self._selected_lang.get()
        if self._on_apply is not None:
            self._on_apply(new_code)
        else:
            i18n.set_language(new_code)
        self.destroy()

    def _on_close(self) -> None:
        self.destroy()

    def _retranslate(self) -> None:
        try:
            self.title(tr("lang.title"))
        except Exception:  # noqa: BLE001
            return
        self._heading_label.configure(text=tr("lang.heading"))
        self._description_label.configure(text=tr("lang.description"))
        self._close_btn.configure(text=tr("lang.btn.close"))
        self._apply_btn.configure(text=tr("lang.btn.apply"))
        self._update_row_visuals()

    def _on_destroy(self, _event: Any = None) -> None:
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None


# ═══════════════════════════════════════════════════════════════════════════
# Browser Settings Dialog
# ═══════════════════════════════════════════════════════════════════════════
class BrowserSettingsDialog(ctk.CTkToplevel):
    """Modal dialog for configuring how stream pages are opened in the browser."""

    def __init__(self, parent: ctk.CTk, current: dict[str, Any]) -> None:
        super().__init__(parent)
        self.title(tr("browser.title"))
        self.geometry("600x940")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=_CLR_BG_DARK)

        if sys.platform != "win32":
            self.update()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self.result: dict[str, Any] | None = None

        settings = {**DEFAULT_BROWSER_SETTINGS, **(current or {})}
        self._new_window_before_app_mode = bool(settings.get("new_window", True))
        profile_stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        self._test_profile_dir = Path(tempfile.gettempdir()) / (
            f"hello_streamer_app_mode_test_profile_{profile_stamp}"
        )

        self._section_open_label = ctk.CTkLabel(
            self,
            text=tr("browser.section.open"),
            font=_font(13, "bold"),
            anchor="w",
        )
        self._section_open_label.pack(padx=24, pady=(20, 4), fill="x")

        self._section_open_hint = ctk.CTkLabel(
            self,
            text=tr("browser.section.open.hint"),
            font=_font(12),
            text_color="#aaaabb",
            anchor="w",
            wraplength=512,
        )
        self._section_open_hint.pack(padx=24, pady=(0, 8), fill="x")

        self.enabled_var = ctk.BooleanVar(value=bool(settings.get("enabled", False)))
        self.enabled_switch = ctk.CTkSwitch(
            self,
            text=tr("browser.enable"),
            variable=self.enabled_var,
            command=self._refresh_enabled_state,
            font=_font(12),
        )
        self.enabled_switch.pack(padx=24, anchor="w")

        # ── Browser path
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.pack(padx=24, pady=(12, 0), fill="x")
        path_frame.grid_columnconfigure(1, weight=1)

        self._path_label = ctk.CTkLabel(
            path_frame,
            text=tr("browser.path.label"),
            font=_font(13),
            anchor="w",
            width=104,
        )
        self._path_label.grid(row=0, column=0, sticky="w")

        self.path_entry = ctk.CTkEntry(
            path_frame,
            placeholder_text=tr("browser.path.placeholder"),
            font=_font(13),
            height=34,
        )
        self.path_entry.insert(0, settings.get("browser_path", "chrome"))
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<KeyRelease>", self._on_path_change)

        self._path_hint = ctk.CTkLabel(
            self,
            text=tr("browser.path.hint"),
            font=_font(11),
            text_color="#888899",
            anchor="w",
            wraplength=512,
        )
        self._path_hint.pack(padx=24, pady=(4, 0), fill="x")

        self.compat_label = ctk.CTkLabel(
            self,
            text="",
            font=_font(11, "bold"),
            text_color="#ffb74d",
            anchor="w",
            wraplength=512,
            height=20,
        )
        self.compat_label.pack(padx=24, pady=(2, 0), fill="x")
        self._compat_key: tuple[str, str] | None = None

        # ── Window mode toggles
        toggle_frame = ctk.CTkFrame(self, fg_color="transparent")
        toggle_frame.pack(padx=24, pady=(10, 0), fill="x")

        self.new_window_var = ctk.BooleanVar(
            value=bool(settings.get("new_window", True))
        )
        self.new_window_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.new_window"),
            variable=self.new_window_var,
            font=_font(12),
        )
        self.new_window_cb.pack(anchor="w", pady=(0, 4))

        self.app_mode_var = ctk.BooleanVar(value=bool(settings.get("app_mode", False)))
        self.app_mode_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.app_mode"),
            variable=self.app_mode_var,
            command=self._on_app_mode_toggle,
            font=_font(12),
        )
        self.app_mode_cb.pack(anchor="w", pady=(0, 4))

        self.minimized_var = ctk.BooleanVar(
            value=bool(settings.get("minimized", False))
        )
        self.minimized_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.minimized"),
            variable=self.minimized_var,
            font=_font(12),
        )
        self.minimized_cb.pack(anchor="w")

        self.close_on_offline_var = ctk.BooleanVar(
            value=bool(settings.get("close_on_offline", False))
        )
        self.close_on_offline_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.close_on_offline"),
            variable=self.close_on_offline_var,
            font=_font(12),
        )
        self.close_on_offline_cb.pack(anchor="w", pady=(4, 0))
        self._close_on_offline_hint = ctk.CTkLabel(
            toggle_frame,
            text=tr("browser.toggle.close_on_offline.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._close_on_offline_hint.pack(anchor="w", pady=(0, 2))

        self.hide_from_taskbar_var = ctk.BooleanVar(
            value=bool(settings.get("hide_from_taskbar", False))
        )
        self.hide_from_taskbar_cb = ctk.CTkCheckBox(
            toggle_frame,
            text=tr("browser.toggle.hide_taskbar"),
            variable=self.hide_from_taskbar_var,
            font=_font(12),
        )
        self.hide_from_taskbar_cb.pack(anchor="w", pady=(4, 0))
        self._hide_taskbar_hint = ctk.CTkLabel(
            toggle_frame,
            text=tr("browser.toggle.hide_taskbar.hint"),
            font=_font(10),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self._hide_taskbar_hint.pack(anchor="w", pady=(0, 2))

        # ── Isolated profile (forces a fresh Chrome master process so
        # --app= / --window-position actually take effect)
        profile_frame = ctk.CTkFrame(self, fg_color=_CLR_CARD, corner_radius=10)
        profile_frame.pack(padx=24, pady=(14, 0), fill="x")
        profile_frame.grid_columnconfigure(1, weight=1)

        self._profile_title = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.title"),
            font=_font(12, "bold"),
            anchor="w",
        )
        self._profile_title.grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 2), sticky="w")

        self._profile_desc = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.desc"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=500,
            justify="left",
        )
        self._profile_desc.grid(row=1, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="w")

        try:
            default_profile_dir = str(base_dir() / "browser_profile")
        except Exception:
            default_profile_dir = ""

        saved_profile_dir = (settings.get("user_data_dir") or "").strip()
        self.user_data_dir_enabled_var = ctk.BooleanVar(value=bool(saved_profile_dir))

        self.user_data_dir_cb = ctk.CTkCheckBox(
            profile_frame,
            text=tr("browser.profile.enable"),
            variable=self.user_data_dir_enabled_var,
            command=self._refresh_user_data_dir_state,
            font=_font(12),
        )
        self.user_data_dir_cb.grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")

        self.user_data_dir_entry = ctk.CTkEntry(
            profile_frame,
            placeholder_text=default_profile_dir or "C:\\Path\\To\\Profile\\Folder",
            font=_font(12),
            height=30,
        )
        self.user_data_dir_entry.insert(0, saved_profile_dir or default_profile_dir)
        self.user_data_dir_entry.grid(
            row=2, column=1, columnspan=2, padx=(0, 12), pady=(0, 8), sticky="ew"
        )

        self.per_channel_profile_var = ctk.BooleanVar(
            value=bool(settings.get("per_channel_profile", True))
        )
        self.per_channel_profile_cb = ctk.CTkCheckBox(
            profile_frame,
            text=tr("browser.profile.per_channel"),
            variable=self.per_channel_profile_var,
            command=self._refresh_user_data_dir_state,
            font=_font(12),
        )
        self.per_channel_profile_cb.grid(
            row=3, column=0, columnspan=3, padx=12, pady=(0, 4), sticky="w"
        )
        self._profile_per_channel_hint = ctk.CTkLabel(
            profile_frame,
            text=tr("browser.profile.per_channel.hint"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            wraplength=500,
            justify="left",
        )
        self._profile_per_channel_hint.grid(
            row=4, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w"
        )

        # ── Position / size
        pos_frame = ctk.CTkFrame(self, fg_color=_CLR_CARD, corner_radius=10)
        pos_frame.pack(padx=24, pady=(14, 0), fill="x")
        pos_frame.grid_columnconfigure((1, 3), weight=1)

        header_frame = ctk.CTkFrame(pos_frame, fg_color="transparent")
        header_frame.grid(row=0, column=0, columnspan=4, padx=12, pady=(10, 4), sticky="ew")
        header_frame.grid_columnconfigure(1, weight=1)

        self.apply_geometry_var = ctk.BooleanVar(
            value=bool(settings.get("apply_geometry", True))
        )
        self.apply_geometry_cb = ctk.CTkCheckBox(
            header_frame,
            text=tr("browser.geometry.apply"),
            variable=self.apply_geometry_var,
            command=self._refresh_geometry_state,
            font=_font(12, "bold"),
        )
        self.apply_geometry_cb.grid(row=0, column=0, sticky="w")

        self.reset_geometry_btn = ctk.CTkButton(
            header_frame,
            text=tr("browser.geometry.reset"),
            width=82,
            height=26,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(11),
            command=self._on_reset_geometry,
        )
        self.reset_geometry_btn.grid(row=0, column=2, sticky="e")

        _tooltip_tr(self.apply_geometry_cb, "browser.geometry.apply.tooltip")
        _tooltip_tr(self.reset_geometry_btn, "browser.geometry.reset.tooltip")

        def _make_int_entry(parent: ctk.CTkFrame, value: int) -> ctk.CTkEntry:
            entry = ctk.CTkEntry(parent, width=84, height=30, font=_font(13), justify="center")
            entry.insert(0, str(value))
            return entry

        self._x_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.x"), font=_font(12))
        self._x_label.grid(row=1, column=0, padx=(14, 4), pady=4, sticky="e")
        self.x_entry = _make_int_entry(pos_frame, int(settings.get("x", 0)))
        self.x_entry.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="w")

        self._y_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.y"), font=_font(12))
        self._y_label.grid(row=1, column=2, padx=(14, 4), pady=4, sticky="e")
        self.y_entry = _make_int_entry(pos_frame, int(settings.get("y", 0)))
        self.y_entry.grid(row=1, column=3, padx=(0, 14), pady=4, sticky="w")

        self._w_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.width"), font=_font(12))
        self._w_label.grid(row=2, column=0, padx=(14, 4), pady=(4, 10), sticky="e")
        self.w_entry = _make_int_entry(pos_frame, int(settings.get("width", 1280)))
        self.w_entry.grid(row=2, column=1, padx=(0, 14), pady=(4, 10), sticky="w")

        self._h_label = ctk.CTkLabel(pos_frame, text=tr("browser.geometry.height"), font=_font(12))
        self._h_label.grid(row=2, column=2, padx=(14, 4), pady=(4, 10), sticky="e")
        self.h_entry = _make_int_entry(pos_frame, int(settings.get("height", 720)))
        self.h_entry.grid(row=2, column=3, padx=(0, 14), pady=(4, 10), sticky="w")

        self.message_label = ctk.CTkLabel(
            self, text="", font=_font(12), height=22, anchor="w", wraplength=512
        )
        self.message_label.pack(padx=24, pady=(8, 0), fill="x")
        self._message_key: tuple[str, dict[str, Any]] | None = None

        # ── Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=52)
        btn_frame.pack(padx=24, pady=(8, 18), fill="x")
        btn_frame.pack_propagate(False)

        self._cancel_btn = ctk.CTkButton(
            btn_frame,
            text=tr("browser.btn.cancel"),
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13),
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="right", padx=(8, 0), pady=4)

        self._test_btn = ctk.CTkButton(
            btn_frame,
            text=tr("browser.btn.test"),
            width=104,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR_LINK,
            hover_color=_CLR_LINK_HOVER,
            text_color=_CLR_LINK,
            font=_font(13),
            command=self._on_test,
        )
        self._test_btn.pack(side="right", padx=(8, 0), pady=4)

        self._save_btn = ctk.CTkButton(
            btn_frame,
            text=tr("browser.btn.save"),
            width=104,
            height=40,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(13, "bold"),
            command=self._on_save,
        )
        self._save_btn.pack(side="right", pady=4)

        self._all_inputs: list[Any] = [
            self.path_entry,
            self.x_entry,
            self.y_entry,
            self.w_entry,
            self.h_entry,
        ]
        self._family_dependent: list[Any] = [
            self.x_entry,
            self.y_entry,
            self.w_entry,
            self.h_entry,
            self.app_mode_cb,
        ]
        self._refresh_enabled_state()
        self._refresh_user_data_dir_state()
        self._on_path_change()
        self._refresh_app_mode_state()
        self._initial_snapshot = self._snapshot_browser_settings()

        self._unsub_i18n = i18n.subscribe(self._retranslate)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _retranslate(self) -> None:
        try:
            self.title(tr("browser.title"))
        except Exception:  # noqa: BLE001
            return
        self._section_open_label.configure(text=tr("browser.section.open"))
        self._section_open_hint.configure(text=tr("browser.section.open.hint"))
        self.enabled_switch.configure(text=tr("browser.enable"))
        self._path_label.configure(text=tr("browser.path.label"))
        self.path_entry.configure(placeholder_text=tr("browser.path.placeholder"))
        self._path_hint.configure(text=tr("browser.path.hint"))
        self.new_window_cb.configure(text=tr("browser.toggle.new_window"))
        self.app_mode_cb.configure(text=tr("browser.toggle.app_mode"))
        self.minimized_cb.configure(text=tr("browser.toggle.minimized"))
        self.close_on_offline_cb.configure(text=tr("browser.toggle.close_on_offline"))
        self._close_on_offline_hint.configure(text=tr("browser.toggle.close_on_offline.hint"))
        self.hide_from_taskbar_cb.configure(text=tr("browser.toggle.hide_taskbar"))
        self._hide_taskbar_hint.configure(text=tr("browser.toggle.hide_taskbar.hint"))
        self._profile_title.configure(text=tr("browser.profile.title"))
        self._profile_desc.configure(text=tr("browser.profile.desc"))
        self.user_data_dir_cb.configure(text=tr("browser.profile.enable"))
        self.per_channel_profile_cb.configure(text=tr("browser.profile.per_channel"))
        self._profile_per_channel_hint.configure(text=tr("browser.profile.per_channel.hint"))
        self.apply_geometry_cb.configure(text=tr("browser.geometry.apply"))
        self.reset_geometry_btn.configure(text=tr("browser.geometry.reset"))
        self._x_label.configure(text=tr("browser.geometry.x"))
        self._y_label.configure(text=tr("browser.geometry.y"))
        self._w_label.configure(text=tr("browser.geometry.width"))
        self._h_label.configure(text=tr("browser.geometry.height"))
        self._cancel_btn.configure(text=tr("browser.btn.cancel"))
        self._test_btn.configure(text=tr("browser.btn.test"))
        self._save_btn.configure(text=tr("browser.btn.save"))
        if self._compat_key is not None:
            key, color = self._compat_key
            self.compat_label.configure(text=tr(key), text_color=color)
        if self._message_key is not None:
            key, kwargs = self._message_key
            self.message_label.configure(text=tr(key, **kwargs))

    def _on_destroy(self, _event: Any = None) -> None:
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None

    def _set_compat(self, key: str, color: str) -> None:
        self._compat_key = (key, color)
        self.compat_label.configure(text=tr(key), text_color=color)

    def _set_message(
        self, key: str | None, *, color: str = "#9aa0b4", **kwargs: Any
    ) -> None:
        if key is None:
            self._message_key = None
            self.message_label.configure(text="", text_color=color)
            return
        self._message_key = (key, dict(kwargs))
        self.message_label.configure(text=tr(key, **kwargs), text_color=color)

    def _refresh_enabled_state(self) -> None:
        state = "normal" if self.enabled_var.get() else "disabled"
        for widget in self._all_inputs:
            widget.configure(state=state)
        for widget in (
            self.new_window_cb,
            self.app_mode_cb,
            self.minimized_cb,
            self.close_on_offline_cb,
            self.hide_from_taskbar_cb,
            self.user_data_dir_cb,
            self.per_channel_profile_cb,
            self.apply_geometry_cb,
            self.reset_geometry_btn,
        ):
            widget.configure(state=state)
        self._refresh_user_data_dir_state()
        self._refresh_geometry_state()
        self._on_path_change()
        self._refresh_app_mode_state()

    def _on_app_mode_toggle(self) -> None:
        if self.app_mode_var.get():
            self._new_window_before_app_mode = bool(self.new_window_var.get())
            self.new_window_var.set(True)
        else:
            self.new_window_var.set(self._new_window_before_app_mode)
        self._refresh_app_mode_state()

    def _refresh_app_mode_state(self) -> None:
        if self.app_mode_var.get():
            self.new_window_var.set(True)

        state = "normal"
        if not self.enabled_var.get() or self.app_mode_var.get():
            state = "disabled"
        self.new_window_cb.configure(state=state)

    def _refresh_user_data_dir_state(self) -> None:
        if not self.enabled_var.get():
            self.user_data_dir_entry.configure(state="disabled")
            self.per_channel_profile_cb.configure(state="disabled")
            return
        profile_enabled = self.user_data_dir_enabled_var.get()
        self.user_data_dir_entry.configure(
            state="normal" if profile_enabled else "disabled"
        )
        self.per_channel_profile_cb.configure(
            state="normal" if profile_enabled else "disabled"
        )

    def _refresh_geometry_state(self) -> None:
        """Enable / disable X/Y/W/H entries based on the apply_geometry checkbox."""
        if not self.enabled_var.get():
            for entry in (self.x_entry, self.y_entry, self.w_entry, self.h_entry):
                entry.configure(state="disabled")
            self.reset_geometry_btn.configure(state="disabled")
            return

        # Path-family override (Firefox) is applied separately in _on_path_change;
        # don't undo that disable here when apply_geometry is on.
        geometry_state = "normal" if self.apply_geometry_var.get() else "disabled"
        for entry in (self.x_entry, self.y_entry, self.w_entry, self.h_entry):
            entry.configure(state=geometry_state)
        self.reset_geometry_btn.configure(
            state="normal" if self.apply_geometry_var.get() else "disabled"
        )

    def _on_reset_geometry(self) -> None:
        """Reset X/Y/W/H to the system-default values."""
        defaults = {
            self.x_entry: 0,
            self.y_entry: 0,
            self.w_entry: 1280,
            self.h_entry: 720,
        }
        for entry, value in defaults.items():
            current_state = entry.cget("state")
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, str(value))
            entry.configure(state=current_state)
        self._set_message("browser.msg.reset_done", color="#64b5f6")

    def _on_path_change(self, _event: Any = None) -> None:
        if not self.enabled_var.get():
            self._set_compat("browser.compat.disabled", "#ffb74d")
            return

        executable = self.path_entry.get().strip() or "chrome"
        family = detect_browser_family(executable)

        if family == "firefox":
            self._set_compat("browser.compat.firefox", "#ffb74d")
            for widget in self._family_dependent:
                widget.configure(state="disabled")
        elif family == "chromium":
            self._set_compat("browser.compat.chromium", "#81c784")
            # app_mode checkbox is always allowed; X/Y/W/H respect the user's
            # apply_geometry choice — restored via _refresh_geometry_state().
            self.app_mode_cb.configure(state="normal")
            self._refresh_geometry_state()
        else:
            self._set_compat("browser.compat.unknown", "#90caf9")
            self.app_mode_cb.configure(state="normal")
            self._refresh_geometry_state()
        self._refresh_app_mode_state()

    def _collect(self) -> dict[str, Any] | None:
        apply_geometry = bool(self.apply_geometry_var.get())

        try:
            x = int(self.x_entry.get())
            y = int(self.y_entry.get())
            width = int(self.w_entry.get())
            height = int(self.h_entry.get())
        except ValueError:
            if apply_geometry:
                self._set_message("browser.msg.invalid_int", color="#ef5350")
                return None
            # When apply_geometry is off the fields aren't used, so silently
            # fall back to defaults so the user can save without filling them.
            x, y, width, height = 0, 0, 1280, 720

        if apply_geometry and (width < 100 or height < 100):
            self._set_message("browser.msg.min_size", color="#ef5350")
            return None

        browser_path = self.path_entry.get().strip() or "chrome"

        if self.user_data_dir_enabled_var.get():
            user_data_dir = self.user_data_dir_entry.get().strip()
            if not user_data_dir:
                self._set_message("browser.msg.empty_profile", color="#ef5350")
                return None
        else:
            user_data_dir = ""

        app_mode = bool(self.app_mode_var.get())
        new_window = True if app_mode else bool(self.new_window_var.get())

        return {
            "enabled": bool(self.enabled_var.get()),
            "browser_path": browser_path,
            "new_window": new_window,
            "app_mode": app_mode,
            "apply_geometry": apply_geometry,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "minimized": bool(self.minimized_var.get()),
            "user_data_dir": user_data_dir,
            "per_channel_profile": bool(self.per_channel_profile_var.get()),
            "close_on_offline": bool(self.close_on_offline_var.get()),
            "hide_from_taskbar": bool(self.hide_from_taskbar_var.get()),
        }

    def _snapshot_browser_settings(self) -> dict[str, Any]:
        app_mode = bool(self.app_mode_var.get())
        return {
            "enabled": bool(self.enabled_var.get()),
            "browser_path": self.path_entry.get().strip(),
            "new_window": True if app_mode else bool(self.new_window_var.get()),
            "app_mode": app_mode,
            "apply_geometry": bool(self.apply_geometry_var.get()),
            "x": self.x_entry.get().strip(),
            "y": self.y_entry.get().strip(),
            "width": self.w_entry.get().strip(),
            "height": self.h_entry.get().strip(),
            "minimized": bool(self.minimized_var.get()),
            "user_data_dir_enabled": bool(self.user_data_dir_enabled_var.get()),
            "user_data_dir": self.user_data_dir_entry.get().strip(),
            "per_channel_profile": bool(self.per_channel_profile_var.get()),
            "close_on_offline": bool(self.close_on_offline_var.get()),
            "hide_from_taskbar": bool(self.hide_from_taskbar_var.get()),
        }

    def _has_unsaved_changes(self) -> bool:
        return self._snapshot_browser_settings() != self._initial_snapshot

    def _on_cancel(self) -> None:
        self.destroy()

    def _on_window_close(self) -> None:
        if not self._has_unsaved_changes():
            self.destroy()
            return

        from tkinter import messagebox

        choice = messagebox.askyesnocancel(
            tr("browser.close.title"),
            tr("browser.close.body"),
            parent=self,
        )
        if choice is None:
            return
        if choice:
            self._on_save()
            return
        self.destroy()

    def _browser_test_url(self) -> str:
        html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hello Streamer Browser Test</title>
  <style>
    :root { color-scheme: dark; font-family: Segoe UI, sans-serif; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #111827;
      color: #f8fafc;
    }
    main {
      width: min(560px, calc(100vw - 48px));
      padding: 32px;
      border: 1px solid #334155;
      border-radius: 8px;
      background: #1f2937;
    }
    h1 { margin: 0 0 12px; font-size: 28px; }
    p { margin: 8px 0 0; color: #cbd5e1; line-height: 1.55; }
    code { color: #93c5fd; }
  </style>
</head>
<body>
  <main>
    <h1>Hello Streamer Browser Test</h1>
    <p>This local page is used to verify browser launch settings.</p>
    <p>When App Mode is enabled, this should open without the browser address bar.</p>
    <p><code id="stamp"></code></p>
  </main>
  <script>
    document.getElementById("stamp").textContent =
      new Date().toLocaleString();
  </script>
</body>
</html>
"""
        try:
            path = Path(tempfile.gettempdir()) / "hello_streamer_browser_test.html"
            path.write_text(html, encoding="utf-8")
            return path.as_uri()
        except OSError:
            logger.exception("Failed to write browser test page")
            return "data:text/html;charset=utf-8," + quote(html)

    def _browser_test_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        test_settings = dict(data)
        if test_settings.get("app_mode"):
            test_settings["user_data_dir"] = str(self._test_profile_dir)
            test_settings["per_channel_profile"] = False
        return test_settings

    def _on_test(self) -> None:
        data = self._collect()
        if data is None:
            return
        test_url = self._browser_test_url()
        test_settings = self._browser_test_settings(data)
        self._set_message("browser.msg.test_opened", color="#64b5f6")
        open_url(test_url, test_settings if test_settings["enabled"] else None)

    def _on_save(self) -> None:
        data = self._collect()
        if data is None:
            return
        self.result = data
        self.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Channel Row
# ═══════════════════════════════════════════════════════════════════════════
class ChannelRow(ctk.CTkFrame):
    """Single row in the channel list."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        channel: dict[str, str],
        on_delete: callable,
        on_move_up: callable,
        on_move_down: callable,
        on_toggle_enabled: callable,
        get_browser_settings: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        super().__init__(parent, corner_radius=10, fg_color=_CLR_CARD, height=58)
        self.channel = channel
        self._on_toggle_enabled = on_toggle_enabled
        self._get_browser_settings = get_browser_settings or (lambda: None)
        self._active_url = ""
        self._status_title = ""
        # Cached state so retranslate-on-language-change can rebuild tooltips
        # without losing the live data (countdown / elapsed / title).
        self._status_state: str | None = None
        self._status_countdown: str = ""
        self._status_elapsed: str = ""

        color = _CLR_TWITCH if channel["platform"] == "twitch" else _CLR_YOUTUBE
        self._platform_color = color

        move_frame = ctk.CTkFrame(self, fg_color="transparent", width=30, height=42)
        move_frame.pack(side="left", padx=(6, 0), pady=8)
        move_frame.pack_propagate(False)

        self.up_btn = ctk.CTkButton(
            move_frame,
            text="▲",
            width=30,
            height=20,
            corner_radius=4,
            fg_color="transparent",
            hover_color="#243052",
            font=_font(10),
            command=on_move_up,
        )
        self.up_btn.pack(anchor="n")

        self.down_btn = ctk.CTkButton(
            move_frame,
            text="▼",
            width=30,
            height=20,
            corner_radius=4,
            fg_color="transparent",
            hover_color="#243052",
            font=_font(10),
            command=on_move_down,
        )
        self.down_btn.pack(anchor="s", side="bottom")

        self.platform_label = ctk.CTkLabel(
            self,
            text=channel["platform"].upper(),
            width=78,
            fg_color=color,
            corner_radius=6,
            text_color="white",
            font=_font(11, "bold"),
            cursor="hand2",
        )
        self.platform_label.pack(side="left", padx=(4, 6), pady=8)
        self.platform_label.bind("<Button-1>", lambda _e: self._open_channel_page())

        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.pack(side="left", padx=6, pady=7, fill="x", expand=True)

        self.name_label = ctk.CTkLabel(
            name_frame,
            text="",
            anchor="w",
            font=_font(15, "bold"),
        )
        self.name_label.pack(anchor="w", fill="x")

        self.id_label = ctk.CTkLabel(
            name_frame,
            text="",
            anchor="w",
            font=_font(11),
            text_color="#9aa0b4",
        )
        self.id_label.pack(anchor="w", fill="x", pady=(1, 0))
        self._refresh_name_labels()

        self.time_label = ctk.CTkLabel(
            self,
            text="",
            width=72,
            anchor="e",
            font=_font(12, "bold"),
            text_color="#aab3d5",
        )
        self.time_label.pack(side="left", padx=(6, 0), pady=8)

        self.status_label = ctk.CTkLabel(
            self,
            text=tr("status.row.placeholder"),
            width=80,
            font=_font(12, "bold"),
            corner_radius=6,
        )
        self.status_label.pack(side="left", padx=6, pady=8)
        self.status_label.bind("<Button-1>", lambda _event: self._open_active_page())

        self.delete_btn = ctk.CTkButton(
            self,
            text="✕",
            width=32,
            height=32,
            corner_radius=6,
            fg_color="transparent",
            hover_color=_CLR_DELETE_HOVER,
            font=_font(14),
            command=on_delete,
        )
        self.delete_btn.pack(side="right", padx=(0, 10), pady=8)

        self.toggle_btn = ctk.CTkButton(
            self,
            text="⏸",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#3c4566",
            hover_color="#243052",
            font=_font(13, "bold"),
            command=self._on_toggle_click,
        )
        self.toggle_btn.pack(side="right", padx=(0, 4), pady=8)

        # Monitor-only ("eye") button. Sits next to the pause/resume toggle.
        # When enabled, the row keeps polling and updating the UI but the
        # app suppresses notifications / browser open / close_on_offline for
        # this channel. Coupled to the pause/resume toggle:
        #   • clicking the eye while paused → unpauses straight into monitor-only
        #   • clicking the eye while triggering → switches to monitor-only
        #   • clicking the eye while monitor-only → switches back to triggering
        #   • clicking pause/resume always clears monitor-only (resume = full)
        self.monitor_only_btn = ctk.CTkButton(
            self,
            text="👁",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            border_width=1,
            border_color="#3c4566",
            hover_color="#243052",
            font=_font(13),
            command=self._on_monitor_only_click,
        )
        self.monitor_only_btn.pack(side="right", padx=(0, 4), pady=8)

        self.link_btn = ctk.CTkButton(
            self,
            text="🔗",
            width=30,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            hover_color=_CLR_LINK_HOVER,
            font=_font(12),
            command=self._open_current_page,
        )
        self.link_btn.pack(side="right", padx=(0, 4), pady=8)

        # Tooltips. The static ones use _tooltip_tr to auto-follow language
        # changes; the link/toggle/status tips are state-driven, so they use
        # plain _tooltip and are rebuilt by _retranslate_dynamic_tips below.
        self._link_tip = _tooltip(self.link_btn, tr("tooltip.row.link.default"))
        self._toggle_tip = _tooltip(self.toggle_btn, "")
        self._monitor_only_tip = _tooltip(self.monitor_only_btn, "")
        _tooltip_tr(self.up_btn, "tooltip.row.up")
        _tooltip_tr(self.down_btn, "tooltip.row.down")
        _tooltip_tr(self.delete_btn, "tooltip.row.delete")
        self._platform_tip = _tooltip_tr(self.platform_label, "tooltip.row.link.default")
        self._status_tip = _tooltip(self.status_label, "")

        self._apply_enabled_visual()

        self._unsub_i18n = i18n.subscribe(self._on_language_changed)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event: Any = None) -> None:
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None

    def _on_language_changed(self) -> None:
        """Rebuild any text the row computed manually (status, dynamic tips)."""
        try:
            self._retranslate_dynamic_text()
        except Exception:  # noqa: BLE001
            logger.exception("ChannelRow retranslate failed")

    def _retranslate_dynamic_text(self) -> None:
        # Status label (non-static rows) — _render_status_visuals rebuilds it.
        self._render_status_visuals()
        # The toggle / monitor-only buttons are icon-only but the tooltips
        # they own are state-driven, so re-text both.
        self._refresh_toggle_tip()
        self._refresh_monitor_only_tip()
        # Channel ID prefix ("ID: ..." / "ID：..." etc.) follows language too.
        self._refresh_name_labels()
        # If currently paused / idle, the status label string is static text
        # set by _apply_enabled_visual — re-apply so it picks up the new lang.
        if not self.channel.get("enabled", True):
            try:
                self.status_label.configure(text=tr("status.row.paused"))
            except Exception:  # noqa: BLE001
                pass
        elif self._status_state is None:
            try:
                self.status_label.configure(text=tr("status.row.placeholder"))
            except Exception:  # noqa: BLE001
                pass

    def _channel_url(self) -> str:
        plat = self.channel["platform"]
        name = self.channel["name"]
        if plat == "twitch":
            return f"https://www.twitch.tv/{name}"
        if name.startswith("UC"):
            return f"https://www.youtube.com/channel/{name}"
        return f"https://www.youtube.com/@{name}"

    def _open_channel_page(self) -> None:
        open_url(self._channel_url(), self._get_browser_settings())

    def _open_current_page(self) -> None:
        open_url(self._active_url or self._channel_url(), self._get_browser_settings())

    def _open_active_page(self) -> None:
        if self._active_url:
            open_url(self._active_url, self._get_browser_settings())

    def _set_link_tip_key(self, key: str) -> None:
        if hasattr(self, "_link_tip"):
            self._link_tip.set_text(key=key)

    def _set_link_tip_with_title(self, base_key: str) -> None:
        """Suffix the link tip with the current stream title when available."""
        if not hasattr(self, "_link_tip"):
            return
        if self._status_title:
            self._link_tip.set_text(
                key="tooltip.row.link.with_title",
                link_text=tr(base_key),
                title=self._status_title,
            )
        else:
            self._link_tip.set_text(key=base_key)

    def _refresh_toggle_tip(self) -> None:
        if not hasattr(self, "_toggle_tip"):
            return
        if self.channel.get("enabled", True):
            self._toggle_tip.set_text(key="tooltip.row.toggle.pause")
        else:
            self._toggle_tip.set_text(key="tooltip.row.toggle.resume")

    def _refresh_monitor_only_tip(self) -> None:
        if not hasattr(self, "_monitor_only_tip"):
            return
        if self.channel.get("monitor_only", False) and self.channel.get(
            "enabled", True
        ):
            self._monitor_only_tip.set_text(key="tooltip.row.monitor_only.disable")
        else:
            self._monitor_only_tip.set_text(key="tooltip.row.monitor_only.enable")

    def _on_toggle_click(self) -> None:
        enabled = not self.channel.get("enabled", True)
        self.channel["enabled"] = enabled
        # Resume always restores the "full triggering" mode — if the user
        # wanted to come back into monitor-only, they'd click the eye instead.
        if enabled:
            self.channel["monitor_only"] = False
        else:
            # Pausing clears monitor-only too, so the next resume is clean.
            self.channel["monitor_only"] = False
        self._apply_enabled_visual()
        self._on_toggle_enabled()

    def _on_monitor_only_click(self) -> None:
        # Toggling the eye always implies the channel must be enabled — if
        # the user clicks it from a paused state, they're effectively
        # un-pausing into monitor-only mode.
        currently_monitor_only = (
            self.channel.get("enabled", True)
            and self.channel.get("monitor_only", False)
        )
        if currently_monitor_only:
            # Eye-off → back to full triggering (still enabled).
            self.channel["enabled"] = True
            self.channel["monitor_only"] = False
        else:
            # Eye-on → enable monitor-only (force enabled).
            self.channel["enabled"] = True
            self.channel["monitor_only"] = True
        self._apply_enabled_visual()
        self._on_toggle_enabled()

    def _apply_enabled_visual(self) -> None:
        enabled = self.channel.get("enabled", True)
        monitor_only = bool(self.channel.get("monitor_only", False)) and enabled
        self._active_url = ""
        self._status_title = ""
        self._status_state = None
        self._status_countdown = ""
        self._status_elapsed = ""
        self.time_label.configure(text="")
        if enabled:
            self.configure(fg_color=_CLR_CARD)
            self.platform_label.configure(
                fg_color=self._platform_color, text_color="white"
            )
            self.name_label.configure(text_color=("gray10", "gray90"))
            self.id_label.configure(text_color="#9aa0b4")
            self.status_label.configure(
                text=tr("status.row.placeholder"),
                text_color="#666677",
                fg_color="transparent",
            )
            self.toggle_btn.configure(text="⏸")
            self._set_link_tip_key("tooltip.row.link.default")
        else:
            self.configure(fg_color=_CLR_CARD_DISABLED)
            self.platform_label.configure(
                fg_color="#2a2a3a", text_color=_CLR_TEXT_DISABLED
            )
            self.name_label.configure(text_color=_CLR_TEXT_DISABLED)
            self.id_label.configure(text_color=_CLR_TEXT_DISABLED)
            self.status_label.configure(
                text=tr("status.row.paused"),
                text_color=_CLR_TEXT_DISABLED,
                fg_color="transparent",
            )
            self.toggle_btn.configure(text="▶")
            self._set_link_tip_key("tooltip.row.link.paused")
        self._apply_monitor_only_visual(monitor_only, enabled)
        self._refresh_toggle_tip()
        self._refresh_monitor_only_tip()
        if hasattr(self, "_status_tip"):
            self._status_tip.set_text("")

    def _apply_monitor_only_visual(self, monitor_only: bool, enabled: bool) -> None:
        """Color the eye button so it stands out when monitor-only is active."""
        if not hasattr(self, "monitor_only_btn"):
            return
        if monitor_only:
            self.monitor_only_btn.configure(
                fg_color="#1565c0",
                text_color="white",
                border_color="#1565c0",
                hover_color="#1976d2",
            )
        elif enabled:
            self.monitor_only_btn.configure(
                fg_color="transparent",
                text_color=("gray14", "gray86"),
                border_color="#3c4566",
                hover_color="#243052",
            )
        else:
            # Paused — keep the button reachable (the user can click it to
            # jump straight into monitor-only) but visually dim.
            self.monitor_only_btn.configure(
                fg_color="transparent",
                text_color=_CLR_TEXT_DISABLED,
                border_color="#2a2a3a",
                hover_color="#243052",
            )

    def set_status(self, status: bool | str | ChannelStatus | None) -> None:
        if not self.channel.get("enabled", True):
            return

        detail = status if isinstance(status, ChannelStatus) else None
        state = detail.status if detail else status
        self._active_url = detail.url if detail else ""
        self._status_title = detail.title if detail else ""

        if state is None:
            self._status_state = None
            self._status_countdown = ""
            self._status_elapsed = ""
        elif state == "upcoming":
            self._status_state = "upcoming"
            self._status_countdown = _format_countdown(
                detail.scheduled_start if detail else ""
            )
            self._status_elapsed = ""
        elif state is True or state == "live":
            self._status_state = "live"
            self._status_elapsed = _format_elapsed(detail.started_at if detail else "")
            self._status_countdown = ""
        else:
            self._status_state = "offline"
            self._status_title = ""
            self._active_url = ""
            self._status_countdown = ""
            self._status_elapsed = ""

        self._render_status_visuals()

    def _render_status_visuals(self) -> None:
        """Apply the cached status data onto the visible widgets (i18n-aware)."""
        if not hasattr(self, "status_label"):
            return
        if not self.channel.get("enabled", True):
            return

        state = self._status_state
        if state is None:
            self.time_label.configure(text="")
            self.status_label.configure(
                text=tr("status.row.placeholder"),
                text_color="#666677",
                fg_color="transparent",
                cursor="",
            )
            self._status_tip.set_text("")
            self._set_link_tip_key("tooltip.row.link.idle")
            return

        if state == "upcoming":
            self.time_label.configure(text=self._status_countdown)
            self.status_label.configure(
                text=tr("status.row.upcoming"),
                text_color="white",
                fg_color="#e65100",
                cursor="hand2",
            )
            self._status_tip.set_text(self._compose_status_tip(state))
            self._set_link_tip_with_title("tooltip.row.link.upcoming")
            return

        if state == "live":
            self.time_label.configure(text=self._status_elapsed)
            self.status_label.configure(
                text=tr("status.row.live"),
                text_color="white",
                fg_color="#1b5e20",
                cursor="hand2",
            )
            self._status_tip.set_text(self._compose_status_tip(state))
            self._set_link_tip_with_title("tooltip.row.link.live")
            return

        # offline
        self.time_label.configure(text="")
        self.status_label.configure(
            text=tr("status.row.offline"),
            text_color="#999999",
            fg_color="transparent",
            cursor="",
        )
        self._status_tip.set_text("")
        self._set_link_tip_key("tooltip.row.link.offline")

    def _compose_status_tip(self, state: str) -> str:
        parts: list[str] = []
        if self._status_title:
            parts.append(tr("tooltip.row.status.title", title=self._status_title))
        if state == "upcoming" and self._status_countdown:
            parts.append(
                tr("tooltip.row.status.starts_in", countdown=self._status_countdown)
            )
        elif state == "live" and self._status_elapsed:
            parts.append(
                tr("tooltip.row.status.live_elapsed", elapsed=self._status_elapsed)
            )
        if parts:
            return "\n".join(parts)
        return tr(
            "tooltip.row.status.upcoming"
            if state == "upcoming"
            else "tooltip.row.status.live"
        )

    @property
    def key(self) -> str:
        return f"{self.channel['platform']}:{self.channel['name']}"

    def set_display_name(self, display_name: str | None) -> bool:
        display_name = (display_name or "").strip()
        if not display_name or display_name == self.channel.get("display_name"):
            return False
        self.channel["display_name"] = display_name
        self._refresh_name_labels()
        return True

    def _refresh_name_labels(self) -> None:
        channel_id = self.channel["name"]
        display_name = self.channel.get("display_name", "").strip()
        if display_name and display_name != channel_id:
            self.name_label.configure(text=display_name)
            self.id_label.configure(text=tr("channel.id.prefix", id=channel_id))
        else:
            self.name_label.configure(text=channel_id)
            self.id_label.configure(text="")

    def set_move_state(self, can_move_up: bool, can_move_down: bool) -> None:
        self.up_btn.configure(state="normal" if can_move_up else "disabled")
        self.down_btn.configure(state="normal" if can_move_down else "disabled")


# ═══════════════════════════════════════════════════════════════════════════
# Main App Window
# ═══════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    """Main application window with system tray integration."""

    def __init__(self, silent: bool = False) -> None:
        super().__init__()

        self.config = config_manager.load()
        self._event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._db = SeenVideoDB()
        self._monitor: Monitor | None = None
        self._channel_rows: list[ChannelRow] = []
        self._silent = silent
        self._truly_quitting = False
        # monitor mode: "idle" | "trigger" | "watch"
        self._monitor_mode: str = "idle"

        # Restore the saved language *before* any widget creation so all
        # labels/buttons are constructed in the user's chosen language.
        saved_language = i18n.normalize(self.config.get("language"))
        i18n.set_language(saved_language, notify=False)

        self.title(tr("app.title"))
        self.minsize(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT)
        self.geometry(_clamped_window_geometry(self.config.get("window_geometry")))
        self.configure(fg_color=_CLR_BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self._on_close_button)

        self._build_ui()
        self._populate_channels()
        self._poll_events()

        self._unsub_i18n = i18n.subscribe(self._on_language_changed)

        self._tray = TrayIcon(
            on_show=self._show_window,
            on_toggle_monitor=self._tray_toggle_monitor,
            on_watch_only=lambda: self.after(0, self._on_watch),
            on_stop=lambda: self.after(0, self._on_stop),
            on_quit=self._quit_app,
            get_mode=lambda: self._monitor_mode,
        )
        self._tray.start()

        if silent:
            self.withdraw()
            channels = self.config.get("channels", [])
            if channels:
                saved_mode = self.config.get("monitor_mode", "trigger")
                starter = self._on_watch if saved_mode == "watch" else self._on_start
                self.after(500, starter)

    # ------------------------------------------------------------------
    # Window visibility
    # ------------------------------------------------------------------
    def _show_window(self) -> None:
        self.after(0, self._do_show)

    def _do_show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _hide_window(self) -> None:
        self._save_config()
        self.withdraw()

    def _on_close_button(self) -> None:
        """X button: hide to tray or quit based on user preference."""
        if self.minimize_to_tray_var.get():
            self._hide_window()
        else:
            self._quit_app()

    def _quit_app(self) -> None:
        """Full exit — called from tray menu or explicit quit."""
        self._truly_quitting = True
        if self._monitor:
            self._monitor.stop()
        self._tray.stop()
        self._save_config()
        self._db.close()
        if getattr(self, "_unsub_i18n", None):
            self._unsub_i18n()
            self._unsub_i18n = None
        self.after(0, self.destroy)

    # ------------------------------------------------------------------
    # Tray callbacks
    # ------------------------------------------------------------------
    def _tray_toggle_monitor(self) -> None:
        if self._monitor and self._monitor.is_running:
            self.after(0, self._on_stop)
        else:
            self.after(0, self._on_start)

    def _current_browser_settings(self) -> dict[str, Any] | None:
        settings = self.config.get("browser_settings")
        if isinstance(settings, dict) and settings.get("enabled"):
            return settings
        return None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        # ── Title bar ──
        title_bar = ctk.CTkFrame(outer, fg_color="transparent")
        title_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.language_icon = _language_icon()
        self.language_btn = ctk.CTkButton(
            title_bar,
            text="",
            image=self.language_icon,
            width=38,
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            command=self._on_language_picker,
        )
        self.language_btn.pack(side="left", padx=(0, 10), pady=(2, 0))
        _tooltip_tr(self.language_btn, "tooltip.language")

        self._title_cn_label = ctk.CTkLabel(
            title_bar,
            text=tr("app.title.cn"),
            font=_font(22, "bold"),
            anchor="w",
        )
        self._title_cn_label.pack(side="left")

        self._title_en_label = ctk.CTkLabel(
            title_bar,
            text=tr("app.title.en"),
            font=_font(13),
            text_color="#777788",
            anchor="w",
        )
        self._title_en_label.pack(side="left", padx=(10, 0), pady=(6, 0))

        self.add_btn = ctk.CTkButton(
            title_bar,
            text=tr("toolbar.add_channel"),
            width=130,
            height=36,
            corner_radius=8,
            fg_color=_CLR_ADD,
            hover_color=_CLR_ADD_HOVER,
            font=_font(14, "bold"),
            command=self._on_add_channel,
        )
        self.add_btn.pack(side="right")
        _tooltip_tr(self.add_btn, "tooltip.add_channel")

        self.browser_settings_btn = ctk.CTkButton(
            title_bar,
            text=tr("toolbar.browser_settings"),
            width=130,
            height=36,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR_LINK,
            hover_color=_CLR_LINK_HOVER,
            text_color=_CLR_LINK,
            font=_font(13, "bold"),
            command=self._on_browser_settings,
        )
        self.browser_settings_btn.pack(side="right", padx=(0, 8))
        _tooltip_tr(self.browser_settings_btn, "tooltip.browser_settings")

        self.startup_var = ctk.BooleanVar(value=is_startup_enabled())
        self.startup_switch = ctk.CTkSwitch(
            title_bar,
            text=tr("toolbar.startup"),
            variable=self.startup_var,
            command=self._on_startup_toggle,
            font=_font(12),
        )
        self.startup_switch.pack(side="right", padx=(0, 14))
        _tooltip_tr(self.startup_switch, "tooltip.startup")

        self.minimize_to_tray_var = ctk.BooleanVar(
            value=self.config.get("minimize_to_tray", True)
        )
        self.tray_switch = ctk.CTkSwitch(
            title_bar,
            text=tr("toolbar.minimize_to_tray"),
            variable=self.minimize_to_tray_var,
            command=self._on_tray_switch_toggle,
            font=_font(12),
        )
        self.tray_switch.pack(side="right", padx=(0, 14))
        _tooltip_tr(self.tray_switch, "tooltip.minimize_to_tray")

        # ── Channel list ──
        list_container = ctk.CTkFrame(outer, corner_radius=12, fg_color=_CLR_ACCENT)
        list_container.grid(row=1, column=0, sticky="nsew")
        list_container.grid_rowconfigure(0, weight=1)
        list_container.grid_columnconfigure(0, weight=1)

        self.scroll_frame = ctk.CTkScrollableFrame(
            list_container,
            corner_radius=0,
            fg_color="transparent",
            scrollbar_button_color="#333355",
            scrollbar_button_hover_color="#444466",
        )
        self.scroll_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.scroll_frame.grid_columnconfigure(0, weight=1)

        self.empty_label = ctk.CTkLabel(
            self.scroll_frame,
            text=tr("status.empty_hint"),
            font=_font(14),
            text_color="#555566",
        )

        # ── Bottom control bar ──
        ctrl = ctk.CTkFrame(outer, corner_radius=12, fg_color=_CLR_CARD)
        ctrl.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        toolbar = ctk.CTkFrame(ctrl, fg_color="transparent")
        toolbar.pack(fill="x", padx=14, pady=10)
        toolbar.grid_columnconfigure(2, weight=1)

        left = ctk.CTkFrame(toolbar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        self.start_btn = ctk.CTkButton(
            left,
            text=tr("toolbar.start"),
            width=132,
            height=38,
            corner_radius=8,
            fg_color=_CLR_START,
            hover_color=_CLR_START_HOVER,
            font=_font(14, "bold"),
            command=self._on_start,
        )
        self.start_btn.pack(side="left", padx=(0, 6))
        _tooltip_tr(self.start_btn, "tooltip.start")

        self.watch_btn = ctk.CTkButton(
            left,
            text=tr("toolbar.watch"),
            width=108,
            height=38,
            corner_radius=8,
            fg_color="#1565c0",
            hover_color="#0d47a1",
            font=_font(14, "bold"),
            command=self._on_watch,
        )
        self.watch_btn.pack(side="left", padx=(0, 6))
        _tooltip_tr(self.watch_btn, "tooltip.watch")

        self.stop_btn = ctk.CTkButton(
            left,
            text=tr("toolbar.stop"),
            width=80,
            height=38,
            corner_radius=8,
            fg_color=_CLR_STOP,
            hover_color=_CLR_STOP_HOVER,
            state="disabled",
            font=_font(14, "bold"),
            command=self._on_stop,
        )
        self.stop_btn.pack(side="left")
        _tooltip_tr(self.stop_btn, "tooltip.stop")

        self.status_text = ctk.CTkLabel(
            toolbar,
            text=tr("status.idle"),
            font=_font(13),
            text_color=_CLR_OFFLINE,
            width=86,
            anchor="w",
        )
        self.status_text.grid(row=0, column=1, sticky="w", padx=(14, 8))
        # Cache for the status-text key so language switches can refresh it.
        self._status_text_key = "status.idle"
        self._status_text_color = _CLR_OFFLINE

        interval_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        interval_group.grid(row=0, column=3, sticky="w", padx=(12, 0))
        self._interval_caption = ctk.CTkLabel(
            interval_group,
            text=tr("toolbar.check_interval"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
        )
        self._interval_caption.pack(anchor="w")

        self.interval_var = ctk.StringVar(
            value=str(self.config.get("check_interval", 60))
        )
        interval_line = ctk.CTkFrame(interval_group, fg_color="transparent")
        interval_line.pack(anchor="w", pady=(2, 0))
        self.interval_entry = ctk.CTkEntry(
            interval_line,
            width=78,
            height=32,
            textvariable=self.interval_var,
            font=_font(14, "bold"),
            justify="center",
        )
        self.interval_entry.pack(side="left")
        _tooltip_tr(self.interval_entry, "tooltip.interval_entry")

        self._interval_unit = ctk.CTkLabel(
            interval_line, text=tr("toolbar.seconds"), font=_font(12), text_color="#d8d8e5"
        )
        self._interval_unit.pack(side="left", padx=(6, 0))

        action_group = ctk.CTkFrame(toolbar, fg_color="transparent")
        action_group.grid(row=0, column=4, sticky="w", padx=(18, 0))
        self._action_caption = ctk.CTkLabel(
            action_group,
            text=tr("toolbar.action_label"),
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
        )
        self._action_caption.pack(anchor="w")

        current_action = self.config.get("action", "open_and_stop")
        action_displays = _action_displays()
        display = _action_labels().get(current_action, action_displays[0])
        self.action_var = ctk.StringVar(value=display)
        self.action_menu = ctk.CTkOptionMenu(
            action_group,
            variable=self.action_var,
            values=action_displays,
            width=218,
            height=32,
            font=_font(12),
            dropdown_font=_font(12),
        )
        self.action_menu.pack(anchor="w", pady=(2, 0))
        _tooltip_tr(self.action_menu, "tooltip.action_menu")


    # ------------------------------------------------------------------
    # Channel list operations
    # ------------------------------------------------------------------
    def _populate_channels(self) -> None:
        channels = self.config.get("channels", [])
        if not channels:
            self.empty_label.pack(pady=40)
        for ch in channels:
            self._add_channel_row(ch)
        self._refresh_move_buttons()

    def _refresh_empty_hint(self) -> None:
        if self._channel_rows:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(pady=40)

    def _add_channel_row(self, channel: dict[str, str]) -> None:
        self.empty_label.pack_forget()

        def on_delete(ch=channel):
            self._remove_channel(ch)

        def on_move_up(ch=channel):
            self._move_channel(ch, -1)

        def on_move_down(ch=channel):
            self._move_channel(ch, 1)

        def on_toggle_enabled(ch=channel):
            self._on_channel_toggle_enabled(ch)

        row = ChannelRow(
            self.scroll_frame,
            channel,
            on_delete=on_delete,
            on_move_up=on_move_up,
            on_move_down=on_move_down,
            on_toggle_enabled=on_toggle_enabled,
            get_browser_settings=self._current_browser_settings,
        )
        row.pack(fill="x", pady=3)
        self._channel_rows.append(row)
        self._refresh_move_buttons()

    def _remove_channel(self, channel: dict[str, str]) -> None:
        for row in self._channel_rows:
            if row.channel == channel:
                row.destroy()
                self._channel_rows.remove(row)
                break
        channels = self.config.get("channels", [])
        if channel in channels:
            channels.remove(channel)
        self._save_config()
        self._refresh_empty_hint()
        self._refresh_move_buttons()
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _move_channel(self, channel: dict[str, str], offset: int) -> None:
        channels = self.config.get("channels", [])
        try:
            index = channels.index(channel)
        except ValueError:
            return

        new_index = index + offset
        if new_index < 0 or new_index >= len(channels):
            return

        channels[index], channels[new_index] = channels[new_index], channels[index]
        self._channel_rows[index], self._channel_rows[new_index] = (
            self._channel_rows[new_index],
            self._channel_rows[index],
        )

        for row in self._channel_rows:
            row.pack_forget()
        for row in self._channel_rows:
            row.pack(fill="x", pady=3)

        self._save_config()
        self._refresh_move_buttons()
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _refresh_move_buttons(self) -> None:
        last_index = len(self._channel_rows) - 1
        for index, row in enumerate(self._channel_rows):
            row.set_move_state(can_move_up=index > 0, can_move_down=index < last_index)

    def _on_channel_toggle_enabled(self, channel: dict[str, str]) -> None:
        self._save_config()
        channels = self.config.get("channels", [])
        if self._monitor and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def _apply_display_names(self, display_names: dict[str, str]) -> None:
        changed = False
        for row in self._channel_rows:
            changed = row.set_display_name(display_names.get(row.key)) or changed
        if changed:
            self._save_config()

    def _on_add_channel(self) -> None:
        dialog = AddChannelDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            ch = dialog.result
            channels = self.config.setdefault("channels", [])
            if ch not in channels:
                channels.append(ch)
                self._add_channel_row(ch)
                self._save_config()
                if self._monitor and self._monitor.is_running:
                    self._monitor.update_channels(channels)

    def _on_language_picker(self) -> None:
        dialog = LanguageDialog(self, on_apply=self._apply_language)
        self.wait_window(dialog)

    def _apply_language(self, code: str) -> None:
        code = i18n.normalize(code)
        self.config["language"] = code
        self._save_config()
        i18n.set_language(code)

    def _on_language_changed(self) -> None:
        """Re-translate every widget that lives directly on the main window."""
        try:
            self.title(tr("app.title"))
        except Exception:  # noqa: BLE001
            return
        self._title_cn_label.configure(text=tr("app.title.cn"))
        self._title_en_label.configure(text=tr("app.title.en"))
        self.add_btn.configure(text=tr("toolbar.add_channel"))
        self.browser_settings_btn.configure(text=tr("toolbar.browser_settings"))
        self.startup_switch.configure(text=tr("toolbar.startup"))
        self.tray_switch.configure(text=tr("toolbar.minimize_to_tray"))
        self.start_btn.configure(text=tr("toolbar.start"))
        self.watch_btn.configure(text=tr("toolbar.watch"))
        self.stop_btn.configure(text=tr("toolbar.stop"))
        self.empty_label.configure(text=tr("status.empty_hint"))
        self.status_text.configure(
            text=tr(self._status_text_key), text_color=self._status_text_color
        )
        self._interval_caption.configure(text=tr("toolbar.check_interval"))
        self._interval_unit.configure(text=tr("toolbar.seconds"))
        self._action_caption.configure(text=tr("toolbar.action_label"))

        # Re-build the action OptionMenu with translated labels, keeping the
        # current logical selection (action key) intact.
        current_display = self.action_var.get()
        current_key = _action_key_for_display(current_display)
        labels = _action_labels()
        new_values = list(labels.values())
        self.action_menu.configure(values=new_values)
        self.action_var.set(labels.get(current_key, new_values[0]))

    # ------------------------------------------------------------------
    # Monitor control
    # ------------------------------------------------------------------
    def _ensure_monitor_running(self) -> bool:
        """Start (or keep) the background monitor. Returns False if no channels."""
        channels = self.config.get("channels", [])
        if not channels:
            return False

        try:
            interval = int(self.interval_var.get())
        except (TypeError, ValueError):
            interval = 60
        interval = max(10, interval)
        self.interval_var.set(str(interval))
        self.config["check_interval"] = interval

        action_display = self.action_var.get()
        action_key = _action_key_for_display(action_display)
        self.config["action"] = action_key

        if self._monitor and self._monitor.is_running:
            self._monitor.update_interval(interval)
            self._monitor.update_channels(channels)
        else:
            self._monitor = Monitor(
                channels=channels,
                interval=interval,
                on_status_change=self._on_channel_live,
                on_poll_complete=self._on_poll_done,
                on_went_offline=self._on_channel_offline,
                db=self._db,
            )
            self._monitor.start()
        return True

    def _set_status_text(self, key: str, color: str) -> None:
        """Update the bottom-toolbar status text + cache for retranslation."""
        self._status_text_key = key
        self._status_text_color = color
        self.status_text.configure(text=tr(key), text_color=color)

    def _on_start(self) -> None:
        if not self._ensure_monitor_running():
            return
        self._monitor_mode = "trigger"
        self.config["monitor_mode"] = "trigger"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.trigger_running", _CLR_LIVE)
        self._tray.update_tooltip_key("tray.tooltip.trigger")

    def _on_watch(self) -> None:
        if not self._ensure_monitor_running():
            return
        self._monitor_mode = "watch"
        self.config["monitor_mode"] = "watch"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.watching", "#64b5f6")
        self._tray.update_tooltip_key("tray.tooltip.watch")

    def _on_stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
            if not self._monitor.is_running:
                self._monitor = None
        try:
            while True:
                self._event_queue.get_nowait()
        except queue.Empty:
            pass
        self._monitor_mode = "idle"
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.stopped", _CLR_OFFLINE)
        self._tray.update_tooltip_key("tray.tooltip.stopped")

    def _apply_monitor_mode_buttons(self) -> None:
        mode = self._monitor_mode
        if mode == "trigger":
            self.start_btn.configure(state="disabled")
            self.watch_btn.configure(state="normal")
            self.stop_btn.configure(state="normal")
        elif mode == "watch":
            self.start_btn.configure(state="normal")
            self.watch_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
        else:
            self.start_btn.configure(state="normal")
            self.watch_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    def _on_browser_settings(self) -> None:
        dialog = BrowserSettingsDialog(
            self, self.config.get("browser_settings", {}) or {}
        )
        self.wait_window(dialog)
        if dialog.result is not None:
            self.config["browser_settings"] = dialog.result
            self._save_config()

    # ------------------------------------------------------------------
    # Event bridge (monitor thread -> UI thread)
    # ------------------------------------------------------------------
    def _on_channel_live(self, entry: ChannelEntry, info: StreamInfo) -> None:
        self._event_queue.put(("live", (entry, info)))

    def _on_channel_offline(self, entry: ChannelEntry, offline_info: Any) -> None:
        # Forward to the UI thread; the actual close call needs ctypes/Win32
        # which we don't want to invoke from the monitor's polling thread.
        self._event_queue.put(("offline", (entry, offline_info)))

    def _on_poll_done(self) -> None:
        if self._monitor:
            statuses = self._monitor.snapshot_statuses()
            display_names = self._monitor.snapshot_display_names()
            self._event_queue.put(("status_update", (statuses, display_names)))

    def _poll_events(self) -> None:
        live_events: list[tuple[ChannelEntry, StreamInfo]] = []
        offline_events: list[tuple[ChannelEntry, Any]] = []
        latest_status_update: tuple[dict, dict] | None = None

        try:
            while True:
                kind, data = self._event_queue.get_nowait()
                if kind == "live":
                    live_events.append(data)
                elif kind == "offline":
                    offline_events.append(data)
                elif kind == "status_update":
                    latest_status_update = data
        except queue.Empty:
            pass

        if latest_status_update is not None:
            statuses, display_names = latest_status_update
            self._apply_display_names(display_names)
            for row in self._channel_rows:
                row.set_status(statuses.get(row.key))

        configured_action = self.config.get("action", "open_and_stop")
        browser_settings = self._current_browser_settings()
        should_stop = False
        should_exit = False
        trigger_enabled = self._monitor_mode == "trigger"

        for entry, info in live_events:
            if info.display_name:
                self._apply_display_names({entry.key: info.display_name})

            if not trigger_enabled:
                continue

            # Monitor-only channels: keep the LIVE label / status_update flow
            # but suppress every downstream side-effect (toast, browser open,
            # stop/exit-after-trigger). The user explicitly asked us to look
            # but not act on this channel.
            if getattr(entry, "monitor_only", False):
                continue

            action = action_for_stream_status(configured_action, info)
            if action is None:
                continue

            noop = lambda: None  # noqa: E731
            execute_action(
                action,
                info,
                stop_fn=noop,
                exit_fn=noop,
                browser_settings=browser_settings,
            )

            if action == "open_and_stop":
                should_stop = True
            elif action == "open_and_exit":
                should_exit = True

        if offline_events and browser_settings.get("close_on_offline"):
            for entry, offline_info in offline_events:
                # close_on_offline must respect monitor-only too — we never
                # opened a window for this channel, so we shouldn't try to
                # hunt for one to close (which could match an unrelated tab
                # the user opened themselves).
                if getattr(entry, "monitor_only", False):
                    continue
                self._handle_channel_offline(entry, offline_info)

        if should_stop:
            self._on_stop()
        elif should_exit:
            self._quit_app()

        self.after(500, self._poll_events)

    def _handle_channel_offline(
        self, entry: ChannelEntry, offline_info: Any
    ) -> None:
        """Close any browser window we opened for this channel."""
        url = getattr(offline_info, "url", "") or ""
        if not url:
            return
        # Build a small list of keywords for the title-fallback path:
        # the channel slug (e.g. "Kaicenat") and the display name help when
        # the URL was opened via webbrowser (no HWND tracking).
        keywords: list[str] = []
        if entry.name:
            keywords.append(entry.name)
        display_name = getattr(offline_info, "display_name", "") or ""
        if display_name and display_name not in keywords:
            keywords.append(display_name)
        try:
            closed = close_browser_window_for_url(url, title_keywords=keywords)
        except Exception:
            logger.exception("close_browser_window_for_url failed for %s", url)
            return
        if closed:
            logger.info(
                "Closed %d browser window(s) for %s (%s)", closed, entry.key, url
            )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _on_tray_switch_toggle(self) -> None:
        self.config["minimize_to_tray"] = self.minimize_to_tray_var.get()
        self._save_config()

    def _on_startup_toggle(self) -> None:
        requested = self.startup_var.get()
        success = enable_startup() if requested else disable_startup()
        if not success:
            self.startup_var.set(not requested)
            logger.warning("Failed to update startup setting")
        self.config["run_on_startup"] = self.startup_var.get()
        self._save_config()

    def _save_config(self) -> None:
        try:
            self.config["window_geometry"] = self.geometry()
        except Exception:
            pass
        config_manager.save(self.config)

    def _on_close(self) -> None:
        if self._monitor:
            self._monitor.stop()
        self._tray.stop()
        self._save_config()
        self._db.close()
        self.destroy()


def _fix_linux_frozen_env() -> None:
    """Restore LD_LIBRARY_PATH for PyInstaller --onefile on Linux.

    PyInstaller overrides LD_LIBRARY_PATH to its temp extraction dir, which
    breaks DNS resolution (glibc NSS dlopen) and subprocess calls (browser,
    xdg-open).  Restoring the original value after Python is fully loaded is
    safe because all bundled .so files are already mapped into memory.
    """
    import os

    lp_key = "LD_LIBRARY_PATH"
    lp_orig = os.environ.get(lp_key + "_ORIG")
    if lp_orig is not None:
        os.environ[lp_key] = lp_orig
    elif lp_key in os.environ:
        del os.environ[lp_key]


def _check_writable(directory: Path) -> None:
    """Abort early with a user-friendly dialog if *directory* is not writable."""
    probe = directory / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            tr("boot.write_fail.title"),
            tr("boot.write_fail.body", directory=directory),
        )
        root.destroy()
        sys.exit(1)


def main() -> None:
    if getattr(sys, "frozen", False) and sys.platform != "win32":
        _fix_linux_frozen_env()

    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Apply the saved language as early as possible so the writable-check
    # error dialog (and any boot-time messages) also respect the user choice.
    try:
        _preloaded_config = config_manager.load()
        i18n.set_language(
            i18n.normalize(_preloaded_config.get("language")),
            notify=False,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to preload language; falling back to default")

    data_dir = base_dir()
    _check_writable(data_dir)

    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "stream_monitor.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_fmt))

    logging.basicConfig(
        level=logging.INFO,
        format=log_fmt,
        handlers=[logging.StreamHandler(), file_handler],
    )

    silent = "--silent" in sys.argv

    app: App | None = None

    lock = SingleInstance()

    def on_show_request() -> None:
        if app is not None:
            app._show_window()

    lock._on_show = on_show_request

    if not lock.try_lock():
        logger.info("Another instance is already running — activating it")
        sys.exit(0)

    app = App(silent=silent)

    try:
        app.mainloop()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
