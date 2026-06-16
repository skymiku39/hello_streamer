"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import customtkinter as ctk

from stream_monitor import __version__, base_dir, config_manager, i18n
from stream_monitor.app_dialogs import (
    AddChannelDialog,
    BrowserSettingsDialog,
    LanguageDialog,
)
from stream_monitor.app_ui import (
    _CLR_ACCENT,
    _CLR_ADD,
    _CLR_ADD_HOVER,
    _CLR_BG_DARK,
    _CLR_CARD,
    _CLR_LINK,
    _CLR_LINK_HOVER,
    _CLR_LIVE,
    _CLR_OFFLINE,
    _CLR_START,
    _CLR_START_HOVER,
    _CLR_STOP,
    _CLR_STOP_HOVER,
    _MIN_WINDOW_HEIGHT,
    _MIN_WINDOW_WIDTH,
    _action_displays,
    _action_key_for_display,
    _action_labels,
    _button_width,
    _clamped_window_geometry,
    _fit_button,
    _fit_option_menu,
    _font,
    _language_icon,
    _tooltip_tr,
    _truncate_status_name,
)
from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.channel_row import ChannelRow
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelEntry, ChannelStatus
from stream_monitor.monitor_controller import MonitorController
from stream_monitor.notifier import (
    browser_window_tracking_available,
    close_all_tracked_windows,
    close_browser_window_for_url,
    execute_action,
)
from stream_monitor.single_instance import SingleInstance
from stream_monitor.startup import disable_startup, enable_startup, is_startup_enabled
from stream_monitor.tray import TrayIcon
from stream_monitor.util import channel_key

logger = logging.getLogger(__name__)


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ═══════════════════════════════════════════════════════════════════════════
# Main App Window
# ═══════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    """Main application window with system tray integration."""

    def __init__(self, silent: bool = False) -> None:
        super().__init__()

        self.config = config_manager.load()
        self._db = SeenVideoDB()
        self._channel_rows: list[ChannelRow] = []
        self._silent = silent
        self._truly_quitting = False
        # Owns the event bus, bridge, monitor thread, and idle/trigger/watch mode.
        self._controller = MonitorController(self, self._db)

        # Restore the saved language *before* any widget creation so all
        # labels/buttons are constructed in the user's chosen language.
        saved_language = i18n.normalize(self.config.get("language"))
        i18n.set_language(saved_language, notify=False)

        self.title(f"{tr('app.title')} v{__version__}")
        self.minsize(_MIN_WINDOW_WIDTH, _MIN_WINDOW_HEIGHT)
        self.geometry(_clamped_window_geometry(self.config.get("window_geometry")))
        self.configure(fg_color=_CLR_BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self._on_close_button)

        self._build_ui()
        self._populate_channels()
        self._poll_events()
        self._tick_elapsed_labels()
        self.after(10_000, self._monitor_health_check)

        self._unsub_i18n = i18n.subscribe(self._on_language_changed)

        self._tray = TrayIcon(
            on_show=self._show_window,
            on_toggle_monitor=self._tray_toggle_monitor,
            on_watch_only=lambda: self.after(0, self._on_watch),
            on_stop=lambda: self.after(0, self.on_stop),
            on_quit=self.quit_app,
            get_mode=lambda: self._controller.mode,
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
            self.quit_app()

    def quit_app(self) -> None:
        """Full exit — called from tray menu or explicit quit."""
        self._truly_quitting = True
        self._controller.shutdown()
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
        if self._controller.is_running:
            self.after(0, self.on_stop)
        else:
            self.after(0, self._on_start)

    def current_browser_settings(self) -> BrowserSettings | None:
        raw = self.config.get("browser_settings")
        if not isinstance(raw, dict):
            return None
        settings = BrowserSettings.from_dict(raw)
        return settings if settings.enabled else None

    # ------------------------------------------------------------------
    # AppEventSink read-only views (consumed by MonitorEventBridge)
    # ------------------------------------------------------------------
    @property
    def monitor_mode(self) -> str:
        return self._controller.mode

    @property
    def wake_verify_active(self) -> bool:
        return self._controller.wake_verify_active

    def iter_channel_rows(self) -> list[ChannelRow]:
        return self._channel_rows

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
            width=_button_width(
                tr("toolbar.add_channel"), min_width=110, size=14, weight="bold"
            ),
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
            width=_button_width(
                tr("toolbar.browser_settings"),
                min_width=110,
                size=13,
                weight="bold",
            ),
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
        toolbar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(toolbar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        self.start_btn = ctk.CTkButton(
            left,
            text=tr("toolbar.start"),
            width=_button_width(
                tr("toolbar.start"), min_width=108, size=14, weight="bold"
            ),
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
            width=_button_width(
                tr("toolbar.watch"), min_width=88, size=14, weight="bold"
            ),
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
            width=_button_width(
                tr("toolbar.stop"), min_width=72, size=14, weight="bold"
            ),
            height=38,
            corner_radius=8,
            fg_color=_CLR_STOP,
            hover_color=_CLR_STOP_HOVER,
            state="disabled",
            font=_font(14, "bold"),
            command=self.on_stop,
        )
        self.stop_btn.pack(side="left")
        _tooltip_tr(self.stop_btn, "tooltip.stop")

        self._status_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        self._status_frame.grid(row=0, column=1, sticky="ew", padx=(14, 8))
        self.status_text = ctk.CTkLabel(
            self._status_frame,
            text=tr("status.idle"),
            font=_font(13),
            text_color=_CLR_OFFLINE,
            anchor="w",
            justify="left",
        )
        self.status_text.pack(anchor="w", fill="x")
        self.status_sub_text = ctk.CTkLabel(
            self._status_frame,
            text="",
            font=_font(11),
            text_color="#9aa0b4",
            anchor="w",
            justify="left",
        )
        self.status_sub_text.pack(anchor="w", fill="x")
        # Cache for the status-text key so language switches can refresh it.
        self._status_text_key = "status.idle"
        self._status_text_color = _CLR_OFFLINE
        self._status_subline_key = "status.awaiting_start"
        self._status_subline_kwargs: dict[str, str] = {}
        self._render_status_text()

        right_toolbar = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_toolbar.grid(row=0, column=2, sticky="e")

        interval_group = ctk.CTkFrame(right_toolbar, fg_color="transparent")
        interval_group.pack(side="left", padx=(12, 18))
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

        action_group = ctk.CTkFrame(right_toolbar, fg_color="transparent")
        action_group.pack(side="left")
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
            width=_button_width(
                max(action_displays, key=len),
                min_width=200,
                size=12,
                padding=48,
            ),
            height=32,
            font=_font(12),
            dropdown_font=_font(12),
        )
        self.action_menu.pack(anchor="w", pady=(2, 0))
        _tooltip_tr(self.action_menu, "tooltip.action_menu")

    def _fit_main_toolbar_i18n(self) -> None:
        """Resize toolbar widgets so localized labels are not clipped."""
        _fit_button(
            self.add_btn,
            tr("toolbar.add_channel"),
            min_width=110,
            size=14,
            weight="bold",
        )
        _fit_button(
            self.browser_settings_btn,
            tr("toolbar.browser_settings"),
            min_width=110,
            size=13,
            weight="bold",
        )
        _fit_button(
            self.start_btn,
            tr("toolbar.start"),
            min_width=108,
            size=14,
            weight="bold",
        )
        _fit_button(
            self.watch_btn,
            tr("toolbar.watch"),
            min_width=88,
            size=14,
            weight="bold",
        )
        _fit_button(
            self.stop_btn,
            tr("toolbar.stop"),
            min_width=72,
            size=14,
            weight="bold",
        )
        self._render_status_text()
        _fit_option_menu(self.action_menu, _action_displays(), min_width=200)

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
            get_browser_settings=self.current_browser_settings,
        )
        row.pack(fill="x", pady=3)
        self._channel_rows.append(row)
        self._refresh_move_buttons()

    def _remove_channel(self, channel: dict[str, str]) -> None:
        # Confirm before destructive action — a single misclick on the [×]
        # button in a long channel list used to silently lose the channel
        # plus all its monitor-only / pause state.
        from tkinter import messagebox

        display = (
            (channel.get("display_name") or "").strip()
            or channel.get("name")
            or ""
        )
        confirm = messagebox.askyesno(
            tr("confirm.delete_channel.title"),
            tr("confirm.delete_channel.body", name=display),
            parent=self,
        )
        if not confirm:
            return

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
        self._controller.update_channels(channels)

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
        self._controller.update_channels(channels)

    def _refresh_move_buttons(self) -> None:
        last_index = len(self._channel_rows) - 1
        for index, row in enumerate(self._channel_rows):
            row.set_move_state(can_move_up=index > 0, can_move_down=index < last_index)

    def _on_channel_toggle_enabled(self, channel: dict[str, str]) -> None:
        self._save_config()
        channels = self.config.get("channels", [])
        self._controller.update_channels(channels)

    def apply_display_names(self, display_names: dict[str, str]) -> None:
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
                self._controller.update_channels(channels)

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
            self.title(f"{tr('app.title')} v{__version__}")
        except Exception:  # noqa: BLE001
            return
        self._title_cn_label.configure(text=tr("app.title.cn"))
        self._title_en_label.configure(text=tr("app.title.en"))
        self.startup_switch.configure(text=tr("toolbar.startup"))
        self.tray_switch.configure(text=tr("toolbar.minimize_to_tray"))
        self.empty_label.configure(text=tr("status.empty_hint"))
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
        self._fit_main_toolbar_i18n()

    # ------------------------------------------------------------------
    # Monitor control
    # ------------------------------------------------------------------
    def _collect_monitor_config(self) -> tuple[list[dict[str, str]], int]:
        """Read interval/action widgets into config; return (channels, interval)."""
        channels = self.config.get("channels", [])
        try:
            interval = int(self.interval_var.get())
        except (TypeError, ValueError):
            interval = 60
        interval = max(10, interval)
        self.interval_var.set(str(interval))
        self.config["check_interval"] = interval

        action_key = _action_key_for_display(self.action_var.get())
        self.config["action"] = action_key
        return channels, interval

    def _render_status_text(self) -> None:
        main = tr(self._status_text_key)
        self.status_text.configure(text=main, text_color=self._status_text_color)
        if self._status_subline_key:
            sub = tr(self._status_subline_key, **self._status_subline_kwargs)
            self.status_sub_text.configure(text=sub)
        else:
            self.status_sub_text.configure(text="")

    def _set_status_text(self, key: str, color: str) -> None:
        """Update the bottom-toolbar status text + cache for retranslation."""
        self._status_text_key = key
        self._status_text_color = color
        self._render_status_text()

    def _channel_display_name(self, entry: ChannelEntry) -> str:
        names = self._controller.snapshot_display_names()
        display = (names.get(entry.key) or "").strip()
        if display:
            return display
        for ch in self.config.get("channels", []):
            if channel_key(ch["platform"], ch["name"]) == entry.key:
                display = (ch.get("display_name") or "").strip()
                if display:
                    return display
        return entry.name

    def update_poll_subline(
        self, entry: ChannelEntry, phase: str, display_name: str = ""
    ) -> None:
        if self._controller.mode not in ("trigger", "watch"):
            return
        name = _truncate_status_name(display_name or entry.name)
        sub_key = (
            "status.poll_refreshing"
            if phase == "refresh"
            else "status.poll_checking"
        )
        self._status_subline_key = sub_key
        self._status_subline_kwargs = {"name": name}
        self._render_status_text()

    def set_poll_waiting(self) -> None:
        if self._controller.mode not in ("trigger", "watch"):
            return
        self._status_subline_key = "status.poll_waiting"
        self._status_subline_kwargs = {}
        self._render_status_text()

    def _set_awaiting_start_subline(self) -> None:
        self._status_subline_key = "status.awaiting_start"
        self._status_subline_kwargs = {}
        self._render_status_text()

    def _on_start(self) -> None:
        channels, interval = self._collect_monitor_config()
        if not self._controller.start("trigger", channels, interval):
            return
        self.config["monitor_mode"] = "trigger"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.trigger_running", _CLR_LIVE)
        self._tray.update_tooltip_key("tray.tooltip.trigger")

    def _on_watch(self) -> None:
        channels, interval = self._collect_monitor_config()
        if not self._controller.start("watch", channels, interval):
            return
        self.config["monitor_mode"] = "watch"
        self._save_config()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.watching", "#64b5f6")
        self._tray.update_tooltip_key("tray.tooltip.watch")

    def on_stop(self, *, is_user_action: bool = True) -> None:
        self._controller.stop()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.stopped", _CLR_OFFLINE)
        self._set_awaiting_start_subline()
        self._tray.update_tooltip_key("tray.tooltip.stopped")

        # close_on_stop fires only when the user explicitly hit Stop — never
        # on the auto-stop produced by open_and_stop, because that just
        # opened the very player window the user wants to keep watching.
        if is_user_action:
            browser_settings = BrowserSettings.from_dict(
                self.config.get("browser_settings") or {}
            )
            if browser_settings.close_on_stop:
                try:
                    closed = close_all_tracked_windows()
                    if closed:
                        logger.info(
                            "close_on_stop: WM_CLOSEd %d tracked window(s)",
                            closed,
                        )
                except Exception:
                    logger.exception("close_on_stop sweep failed")

    def _apply_monitor_mode_buttons(self) -> None:
        mode = self._controller.mode
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
    @staticmethod
    def _channel_status_from_stream_info(info: StreamInfo) -> ChannelStatus:
        stream_status = info.stream_status or ("live" if info.is_live else "offline")
        if stream_status == "upcoming":
            return ChannelStatus(
                status="upcoming",
                url=info.url,
                title=info.title,
                scheduled_start=info.scheduled_start or "",
            )
        if stream_status == "live" or info.is_live:
            return ChannelStatus(
                status=True,
                url=info.url,
                title=info.title,
                started_at=info.started_at or "",
            )
        return ChannelStatus(
            status=False,
            url=info.url,
            title=info.title,
            vod_url=info.url if stream_status == "video" else "",
        )

    def apply_live_row_status(self, entry: ChannelEntry, info: StreamInfo) -> None:
        for row in self._channel_rows:
            if row.key == entry.key:
                row.set_status(self._channel_status_from_stream_info(info))
                break

    def execute_live_action(
        self,
        action: str,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
    ) -> None:
        """Run notify/open side-effects off the UI thread."""
        noop = lambda: None  # noqa: E731
        try:
            execute_action(
                action,
                info,
                stop_fn=noop,
                exit_fn=noop,
                browser_settings=browser_settings,
            )
        except Exception:
            logger.exception(
                "execute_live_action failed: action=%s url=%s",
                action,
                info.url,
            )

    def _tick_elapsed_labels(self) -> None:
        for row in self._channel_rows:
            row.refresh_elapsed_display()
        self.after(30_000, self._tick_elapsed_labels)

    def _monitor_health_check(self) -> None:
        """Restart the background monitor if its thread died unexpectedly."""
        self.maybe_restart_dead_monitor()
        self.after(10_000, self._monitor_health_check)

    def maybe_restart_dead_monitor(self) -> None:
        if self._controller.mode not in ("trigger", "watch"):
            return
        channels, interval = self._collect_monitor_config()
        if not self._controller.restart_if_dead(channels, interval):
            return
        if self._controller.mode == "trigger":
            self._set_status_text("status.monitor_restarted", _CLR_LIVE)
            self._tray.update_tooltip_key("tray.tooltip.trigger")
        else:
            self._set_status_text("status.monitor_restarted", "#64b5f6")
            self._tray.update_tooltip_key("tray.tooltip.watch")

    def _poll_events(self) -> None:
        self.after(80, self._poll_events)
        self._controller.tick()

    def handle_channel_offline(
        self, entry: ChannelEntry, offline_info: Any
    ) -> None:
        """Close any browser window we opened for this channel."""
        url = getattr(offline_info, "url", "") or ""
        if not url:
            return
        # Title-keyword fallback is only safe when we launched with a dedicated
        # profile (HWND tracking). Shared-profile / webbrowser opens register
        # a one-shot block instead; still pass keywords only when isolation is
        # available so a stale block cannot be bypassed by config drift.
        settings = self.current_browser_settings()
        keywords: list[str] | None = None
        if settings and browser_window_tracking_available(settings, url):
            keywords = []
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
            # Without surface-level feedback the switch silently snaps back
            # and the user can't tell whether the click actually registered.
            # A modal messagebox is the lowest-risk way to communicate this
            # since the bottom status bar is dynamically overwritten by the
            # monitor loop and may be hidden if the user already minimised.
            from tkinter import messagebox

            messagebox.showwarning(
                tr("toolbar.startup"),
                tr("status.startup.write_failed"),
                parent=self,
            )
        self.config["run_on_startup"] = self.startup_var.get()
        self._save_config()

    def _save_config(self) -> None:
        try:
            self.config["window_geometry"] = self.geometry()
        except Exception:
            pass
        config_manager.save(self.config)

    def _on_close(self) -> None:
        self._controller.shutdown()
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
