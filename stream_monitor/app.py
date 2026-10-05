"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
import time
from pathlib import Path
from typing import Any

import customtkinter as ctk

from stream_monitor import __version__, config_manager, i18n, status_cache
from stream_monitor.action_coordinator import ActionCoordinator
from stream_monitor.action_plan import (
    ActionPlan,
    LifecycleEffect,
    TriggerSettings,
    action_plan_for,
)
from stream_monitor.app_dialogs import (
    AddChannelDialog,
    AppSettingsDialog,
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
    _CLR_PANEL_BORDER,
    _CLR_SEG_BORDER,
    _CLR_START,
    _CLR_START_HOVER,
    _CLR_STATUS_DOT_IDLE,
    _CLR_STOP,
    _CLR_STOP_HOVER,
    _CLR_TEXT_MUTED,
    _CLR_TEXT_SECONDARY,
    _COMPACT_ACT_BTN_HEIGHT,
    _COMPACT_CTRL_PAD_X,
    _COMPACT_CTRL_PAD_Y,
    _COMPACT_FIELD_HEIGHT,
    _COMPACT_FLOW_HGAP,
    _COMPACT_LINE_GAP,
    _COMPACT_SEG_HEIGHT,
    _MIN_WINDOW_HEIGHT,
    _MIN_WINDOW_WIDTH,
    CompactFlowFrame,
    _button_width,
    _clamped_window_geometry,
    _fit_button,
    _fit_option_menu,
    _font,
    _language_icon,
    _tooltip_tr,
    _truncate_status_name,
    compact_control_button_states,
    compose_monitor_mode,
    decompose_monitor_mode,
)
from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.canvas_channel_list import (
    CanvasChannelList,
    CanvasChannelRowAdapter,
)
from stream_monitor.channel_policy import (
    TRIGGER_MODE,
    TRIGGER_ONCE_MODE,
    WATCH_MODE,
    WATCH_ONCE_MODE,
    base_monitor_mode,
    is_one_shot_monitor_mode,
    mode_for_silent_start,
)
from stream_monitor.channel_reorder import apply_list_move
from stream_monitor.channel_reorder_ui import ChannelReorderMode
from stream_monitor.channel_row import ChannelRow
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.launch_records import reset_launch_records
from stream_monitor.monitor import ChannelEntry, ChannelStatus
from stream_monitor.monitor_controller import MonitorController
from stream_monitor.notifier import (
    configure_viewer_engagement,
    execute_action_plan,
    platform_services,
)
from stream_monitor.platform_ports import PlatformServices
from stream_monitor.portable_storage import portable_paths
from stream_monitor.scroll_guard import ScrollRepaintGuard
from stream_monitor.single_instance import SingleInstance
from stream_monitor.startup import (
    disable_startup,
    enable_startup,
    heal_startup_command_if_enabled,
    is_startup_enabled,
)
from stream_monitor.tray import TrayIcon
from stream_monitor.util import channel_key
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

logger = logging.getLogger(__name__)

_REORDER_DEBUG_LOG = portable_paths().diagnostics_log
_REORDER_DEBUG_MAX_BYTES = 2 * 1024 * 1024
_REORDER_DEBUG_BACKUP = portable_paths().diagnostics_backup


def _reorder_debug(event: str, **data: Any) -> None:
    try:
        if _REORDER_DEBUG_LOG.exists() and (
            _REORDER_DEBUG_LOG.stat().st_size >= _REORDER_DEBUG_MAX_BYTES
        ):
            try:
                _REORDER_DEBUG_LOG.replace(_REORDER_DEBUG_BACKUP)
            except OSError:
                # Diagnostics must never interfere with the UI operation.
                pass
        payload = {"event": event, "t": time.time(), **data}
        with _REORDER_DEBUG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


_AFTER_OPEN_EFFECTS = (
    LifecycleEffect.NONE,
    LifecycleEffect.STOP_MONITOR,
    LifecycleEffect.EXIT_APP,
)


def _after_open_labels() -> dict[LifecycleEffect, str]:
    return {
        LifecycleEffect.NONE: tr("trigger.after_open.continue"),
        LifecycleEffect.STOP_MONITOR: tr("trigger.after_open.stop"),
        LifecycleEffect.EXIT_APP: tr("trigger.after_open.exit"),
    }


def _after_open_key_for_display(display: str) -> LifecycleEffect:
    for effect, label in _after_open_labels().items():
        if label == display:
            return effect
    return LifecycleEffect.NONE


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
        self._channel_rows: list[CanvasChannelRowAdapter] = []
        self._silent = silent
        self._truly_quitting = False
        # The persisted snapshot's saved_at is only meaningful for the very
        # first monitor start (a long gap there means a stale cache that should
        # be wake-verified); later restarts seed from live row state instead.
        self._status_cache_consumed = False
        # Keep Run key / XDG Exec pointing at this build (versioned .exe names)
        # and reconcile the persisted switch with the actual OS entry.
        startup_config_needs_save = False
        if getattr(sys, "frozen", False):
            startup_enabled = is_startup_enabled()
            if startup_enabled:
                heal_startup_command_if_enabled()
            if self.config.get("run_on_startup") != startup_enabled:
                self.config["run_on_startup"] = startup_enabled
                startup_config_needs_save = True
        self._reorder_mode: ChannelReorderMode | None = None
        self._preview_pack_order: list[int] | None = None
        self._pending_preview_order: list[int] | None = None
        self._preview_repack_after: str | None = None
        self._config_save_after: str | None = None
        # Owns the event bus, bridge, monitor thread, and idle/trigger/watch mode.
        self._controller = MonitorController(self, self._db)
        self._platform = platform_services()
        self._action_coordinator = ActionCoordinator(
            runner=execute_action_plan,
            schedule_ui=lambda callback: self.after(0, callback),
            generation_is_current=lambda generation: (
                generation == self._controller.generation
            ),
            on_stop=lambda: self.on_stop(is_user_action=False),
            on_exit=self.quit_app,
        )
        configure_viewer_engagement(
            ViewerEngagementSettings.from_dict(
                self.config.get("viewer_engagement")
            )
        )

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
        self._restore_status_cache()
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

        if startup_config_needs_save:
            self._save_config()

        if silent:
            self.withdraw()
            channels = self.config.get("channels", [])
            if channels:
                saved_mode = self.config.get("monitor_mode", TRIGGER_MODE)
                if not isinstance(saved_mode, str):
                    saved_mode = TRIGGER_MODE
                recovered_mode = mode_for_silent_start(saved_mode)
                if recovered_mode != saved_mode:
                    # A one-shot mode can survive a crash before its completion
                    # callback persists the reusable mode. Do not replay it on
                    # every silent launch; recover to the reusable mode once.
                    self.config["monitor_mode"] = recovered_mode
                    self._save_config()
                    saved_mode = recovered_mode
                starter = {
                    WATCH_MODE: self._on_watch,
                    TRIGGER_ONCE_MODE: self._on_start_once,
                    WATCH_ONCE_MODE: self._on_watch_once,
                }.get(saved_mode, self._on_start)
                self.after(500, starter)

    def report_callback_exception(self, exc, value, tb) -> None:
        """Keep Tk callback failures in the application log.

        Tk normally prints callback exceptions to stderr only. A windowed
        build has no visible stderr, which can make a callback failure look
        like a spontaneous close or a frozen tray app.
        """
        if exc is KeyboardInterrupt:
            logger.info("Application interrupted by user")
            return
        logger.critical(
            "Unhandled Tk callback exception",
            exc_info=(exc, value, tb),
        )

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
        self._save_status_cache()
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
        if self._truly_quitting:
            return
        self._truly_quitting = True
        self._action_coordinator.shutdown()
        self._scroll_guard.destroy()
        self._save_status_cache()
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
    def platform_services(self) -> PlatformServices:
        return self._platform

    @property
    def monitor_generation(self) -> int:
        return self._controller.generation

    @property
    def wake_verify_active(self) -> bool:
        return self._controller.wake_verify_active

    @property
    def defer_channel_row_repaints(self) -> bool:
        reorder = self._reorder_mode is not None and self._reorder_mode.active
        return self._scroll_guard.repaints_deferred or reorder

    def iter_channel_rows(self) -> list[CanvasChannelRowAdapter]:
        return self._channel_rows

    def is_channel_active(self, entry: ChannelEntry) -> bool:
        """Return whether a queued monitor event still targets a live row.

        A background poll may finish after the user removes or disables a
        channel.  The event bridge checks this on the UI thread immediately
        before dispatching browser/notification side effects, so an in-flight
        stale probe cannot act on a channel that is no longer configured.
        """
        for channel in self.config.get("channels", []):
            if not isinstance(channel, dict):
                continue
            try:
                same_key = channel_key(channel["platform"], channel["name"]) == entry.key
            except (KeyError, TypeError):
                continue
            if same_key:
                return bool(channel.get("enabled", True))
        return False

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

        self.settings_btn = ctk.CTkButton(
            title_bar,
            text=tr("toolbar.settings"),
            width=_button_width(
                tr("toolbar.settings"), min_width=88, size=13, weight="bold"
            ),
            height=36,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color="#555566",
            hover_color="#333344",
            font=_font(13, "bold"),
            command=self._on_app_settings,
        )
        self.settings_btn.pack(side="right", padx=(0, 8))
        _tooltip_tr(self.settings_btn, "tooltip.settings.reset")

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

        self.scroll_frame = CanvasChannelList(
            list_container,
            corner_radius=0,
            fg_color=_CLR_ACCENT,
            scrollbar_button_color="#333355",
            scrollbar_button_hover_color="#444466",
            on_delete=self._remove_channel,
            on_move=self._move_channel,
            on_reorder_begin=lambda row, y_root: self._begin_channel_reorder(
                row, y_root=y_root
            ),
        )
        self.scroll_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self._scroll_guard = ScrollRepaintGuard(
            self.scroll_frame,
            self,
            on_idle=self._on_scroll_repaint_idle,
        )
        self._reorder_mode = ChannelReorderMode(
            self.scroll_frame._parent_canvas,
            schedule_preview_repack=self._schedule_preview_repack,
            on_debug=_reorder_debug,
        )
        # Persistent drag handlers — never unbind_all (that breaks list scroll).
        self.bind_all("<B1-Motion>", self._on_channel_reorder_motion_global, add="+")
        self.bind_all(
            "<ButtonRelease-1>", self._on_channel_reorder_release_global, add="+"
        )
        for wheel_seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(wheel_seq, self._on_channel_reorder_wheel_global, add="+")
        self.bind_all("<Escape>", self._on_channel_reorder_escape_global, add="+")
        self.bind("<FocusOut>", self._on_focus_out_cancel_reorder)

        self.empty_label = ctk.CTkLabel(
            list_container,
            text=tr("status.empty_hint"),
            font=_font(14),
            text_color="#555566",
        )

        # ── Compact bottom control panel (v5 HTML arrangement + flex-wrap) ──
        self._compact_ctrl = ctk.CTkFrame(
            outer,
            corner_radius=12,
            fg_color=_CLR_CARD,
            border_width=1,
            border_color=_CLR_PANEL_BORDER,
        )
        self._compact_ctrl.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        panel = ctk.CTkFrame(self._compact_ctrl, fg_color="transparent")
        panel.pack(
            fill="x",
            padx=_COMPACT_CTRL_PAD_X,
            pady=_COMPACT_CTRL_PAD_Y,
        )

        saved_mode = self.config.get("monitor_mode", TRIGGER_MODE)
        if not isinstance(saved_mode, str):
            saved_mode = TRIGGER_MODE
        kind, once = decompose_monitor_mode(saved_mode)
        self._monitor_kind = kind
        self._monitor_once = once

        self._run_flow = CompactFlowFrame(
            panel, hgap=_COMPACT_FLOW_HGAP, vgap=_COMPACT_LINE_GAP
        )
        self._run_flow.pack(fill="x")

        acts = ctk.CTkFrame(self._run_flow, fg_color="transparent")
        self._run_flow.add(acts)

        self.start_btn = ctk.CTkButton(
            acts,
            text=tr("toolbar.run"),
            width=_button_width(
                tr("toolbar.run"), min_width=96, size=13, weight="bold"
            ),
            height=_COMPACT_ACT_BTN_HEIGHT,
            corner_radius=8,
            fg_color=_CLR_START,
            hover_color=_CLR_START_HOVER,
            font=_font(13, "bold"),
            command=self._on_compact_run,
        )
        self.start_btn.pack(side="left", padx=(0, 8))
        _tooltip_tr(self.start_btn, "tooltip.run")

        self.stop_btn = ctk.CTkButton(
            acts,
            text=tr("toolbar.stop"),
            width=_button_width(
                tr("toolbar.stop"), min_width=80, size=13, weight="bold"
            ),
            height=_COMPACT_ACT_BTN_HEIGHT,
            corner_radius=8,
            fg_color=_CLR_STOP,
            hover_color=_CLR_STOP_HOVER,
            state="disabled",
            font=_font(13, "bold"),
            command=self.on_stop,
        )
        self.stop_btn.pack(side="left", padx=(0, 8))
        _tooltip_tr(self.stop_btn, "tooltip.stop")

        self._status_frame = ctk.CTkFrame(acts, fg_color="transparent")
        self._status_frame.pack(side="left", padx=(4, 0))
        status_row = ctk.CTkFrame(self._status_frame, fg_color="transparent")
        status_row.pack(anchor="w")
        self.status_dot = ctk.CTkLabel(
            status_row,
            text="●",
            font=_font(11),
            text_color=_CLR_STATUS_DOT_IDLE,
            width=14,
        )
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_text = ctk.CTkLabel(
            status_row,
            text=tr("status.idle"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
            anchor="w",
            justify="left",
        )
        self.status_text.pack(side="left")
        self.status_sub_text = ctk.CTkLabel(
            self._status_frame,
            text="",
            font=_font(11),
            text_color=_CLR_TEXT_MUTED,
            anchor="w",
            justify="left",
        )
        self.status_sub_text.pack(anchor="w")
        self._status_text_key = "status.idle"
        self._status_text_color = _CLR_OFFLINE
        self._status_subline_key = "status.awaiting_start"
        self._status_subline_kwargs: dict[str, str] = {}
        self._render_status_text()

        mode_fld = ctk.CTkFrame(self._run_flow, fg_color="transparent")
        self._run_flow.add(mode_fld)
        self._mode_caption = ctk.CTkLabel(
            mode_fld,
            text=tr("toolbar.mode"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._mode_caption.pack(side="left", padx=(0, 8))
        self.mode_seg = ctk.CTkSegmentedButton(
            mode_fld,
            values=self._mode_segment_values(),
            command=self._on_mode_segment,
            height=_COMPACT_SEG_HEIGHT,
            font=_font(13),
            selected_color=_CLR_ACCENT,
            selected_hover_color=_CLR_ADD_HOVER,
            unselected_color=_CLR_CARD,
            unselected_hover_color="#2a2a40",
            fg_color=_CLR_SEG_BORDER,
        )
        self.mode_seg.pack(side="left")
        self.mode_seg.set(self._mode_segment_label(self._monitor_kind))

        duration_fld = ctk.CTkFrame(self._run_flow, fg_color="transparent")
        self._run_flow.add(duration_fld)
        self._duration_caption = ctk.CTkLabel(
            duration_fld,
            text=tr("toolbar.duration"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._duration_caption.pack(side="left", padx=(0, 8))
        self.duration_seg = ctk.CTkSegmentedButton(
            duration_fld,
            values=self._duration_segment_values(),
            command=self._on_duration_segment,
            height=_COMPACT_SEG_HEIGHT,
            font=_font(13),
            selected_color=_CLR_ACCENT,
            selected_hover_color=_CLR_ADD_HOVER,
            unselected_color=_CLR_CARD,
            unselected_hover_color="#2a2a40",
            fg_color=_CLR_SEG_BORDER,
        )
        self.duration_seg.pack(side="left")
        self.duration_seg.set(self._duration_segment_label(self._monitor_once))

        interval_fld = ctk.CTkFrame(self._run_flow, fg_color="transparent")
        self._run_flow.add(interval_fld)
        self._interval_caption = ctk.CTkLabel(
            interval_fld,
            text=tr("toolbar.interval_short"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._interval_caption.pack(side="left", padx=(0, 8))
        self.interval_var = ctk.StringVar(
            value=str(self.config.get("check_interval", 60))
        )
        self.interval_entry = ctk.CTkEntry(
            interval_fld,
            width=60,
            height=_COMPACT_FIELD_HEIGHT,
            textvariable=self.interval_var,
            font=_font(13),
            justify="center",
        )
        self.interval_entry.pack(side="left")
        _tooltip_tr(self.interval_entry, "tooltip.interval_entry")
        self._interval_unit = ctk.CTkLabel(
            interval_fld,
            text=tr("toolbar.seconds"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._interval_unit.pack(side="left", padx=(8, 0))

        settings_block = ctk.CTkFrame(panel, fg_color="transparent")
        settings_block.pack(fill="x", pady=(_COMPACT_LINE_GAP, 0))
        line2_rule = ctk.CTkFrame(
            settings_block, height=1, fg_color=_CLR_PANEL_BORDER
        )
        line2_rule.pack(fill="x", pady=(0, _COMPACT_LINE_GAP))

        self._settings_flow = CompactFlowFrame(
            settings_block, hgap=_COMPACT_FLOW_HGAP, vgap=_COMPACT_LINE_GAP
        )
        self._settings_flow.pack(fill="x")

        trigger_settings = TriggerSettings.from_mapping(
            self.config.get("trigger_settings")
        )

        # Atomic wrap units so long locales reflow instead of clipping the
        # trailing info button / option menu (HTML flex-wrap behaviour).
        self._notify_group_label = ctk.CTkLabel(
            self._settings_flow,
            text=tr("toolbar.group.notify"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._settings_flow.add(self._notify_group_label)

        self.notify_on_live_var = ctk.BooleanVar(
            value=trigger_settings.notify_on_live
        )
        self.notify_on_live_switch = ctk.CTkSwitch(
            self._settings_flow,
            text=tr("toolbar.notify_on_live"),
            variable=self.notify_on_live_var,
            command=self._persist_trigger_settings,
            font=_font(14),
            switch_width=32,
            switch_height=18,
        )
        self._settings_flow.add(self.notify_on_live_switch)
        _tooltip_tr(self.notify_on_live_switch, "tooltip.notify_on_live")

        self.notify_on_upcoming_var = ctk.BooleanVar(
            value=trigger_settings.notify_on_upcoming
        )
        self.notify_on_upcoming_switch = ctk.CTkSwitch(
            self._settings_flow,
            text=tr("toolbar.notify_on_upcoming"),
            variable=self.notify_on_upcoming_var,
            command=self._persist_trigger_settings,
            font=_font(14),
            switch_width=32,
            switch_height=18,
        )
        self._settings_flow.add(self.notify_on_upcoming_switch)
        _tooltip_tr(
            self.notify_on_upcoming_switch, "tooltip.notify_on_upcoming"
        )

        self.notify_on_open_failure_var = ctk.BooleanVar(
            value=trigger_settings.notify_on_open_failure
        )
        self.notify_on_open_failure_switch = ctk.CTkSwitch(
            self._settings_flow,
            text=tr("toolbar.notify_on_open_failure"),
            variable=self.notify_on_open_failure_var,
            command=self._persist_trigger_settings,
            font=_font(14),
            switch_width=32,
            switch_height=18,
        )
        self._settings_flow.add(self.notify_on_open_failure_switch)
        _tooltip_tr(
            self.notify_on_open_failure_switch, "tooltip.notify_on_open_failure"
        )

        self._open_group_label = ctk.CTkLabel(
            self._settings_flow,
            text=tr("toolbar.group.open"),
            font=_font(13),
            text_color=_CLR_TEXT_SECONDARY,
        )
        self._settings_flow.add(self._open_group_label)

        self.open_on_live_var = ctk.BooleanVar(value=trigger_settings.open_on_live)
        self.open_on_live_switch = ctk.CTkSwitch(
            self._settings_flow,
            text=tr("toolbar.open_on_live"),
            variable=self.open_on_live_var,
            command=self._persist_trigger_settings,
            font=_font(14),
            switch_width=32,
            switch_height=18,
        )
        self._settings_flow.add(self.open_on_live_switch)
        _tooltip_tr(self.open_on_live_switch, "tooltip.open_on_live")

        self.open_on_upcoming_var = ctk.BooleanVar(
            value=trigger_settings.open_on_upcoming
        )
        self.open_on_upcoming_switch = ctk.CTkSwitch(
            self._settings_flow,
            text=tr("toolbar.open_on_upcoming"),
            variable=self.open_on_upcoming_var,
            command=self._persist_trigger_settings,
            font=_font(14),
            switch_width=32,
            switch_height=18,
        )
        self._settings_flow.add(self.open_on_upcoming_switch)
        _tooltip_tr(self.open_on_upcoming_switch, "tooltip.open_on_upcoming")

        after_fld = ctk.CTkFrame(self._settings_flow, fg_color="transparent")
        self._settings_flow.add(after_fld)
        self._after_open_caption = ctk.CTkLabel(
            after_fld,
            text=tr("toolbar.after_open"),
            font=_font(14),
            text_color="white",
        )
        self._after_open_caption.pack(side="left", padx=(0, 8))
        labels = _after_open_labels()
        self.after_open_var = ctk.StringVar(value=labels[trigger_settings.after_open])
        self.after_open_menu = ctk.CTkOptionMenu(
            after_fld,
            variable=self.after_open_var,
            values=[labels[effect] for effect in _AFTER_OPEN_EFFECTS],
            command=lambda _value: self._persist_trigger_settings(),
            width=104,
            height=_COMPACT_FIELD_HEIGHT,
            font=_font(13),
            dropdown_font=_font(13),
        )
        self.after_open_menu.pack(side="left")
        _tooltip_tr(self.after_open_menu, "tooltip.after_open")
        _fit_option_menu(
            self.after_open_menu,
            [labels[effect] for effect in _AFTER_OPEN_EFFECTS],
            min_width=104,
            size=13,
        )

        self._trigger_hint_btn = ctk.CTkButton(
            after_fld,
            text="i",
            width=28,
            height=28,
            corner_radius=8,
            fg_color="transparent",
            hover_color="#2a2a40",
            text_color=_CLR_TEXT_MUTED,
            font=_font(14, "bold"),
        )
        self._trigger_hint_btn.pack(side="left", padx=(6, 0))
        _tooltip_tr(self._trigger_hint_btn, "toolbar.trigger_hint")

        self._compact_ctrl.bind(
            "<Configure>", self._on_compact_ctrl_configure, add="+"
        )
        self._refresh_trigger_controls()
        self._apply_monitor_mode_buttons()
        self._reflow_compact_panel()


    def _mode_segment_values(self) -> list[str]:
        return [tr("toolbar.mode.trigger"), tr("toolbar.mode.watch")]

    def _duration_segment_values(self) -> list[str]:
        return [
            tr("toolbar.duration.continuous"),
            tr("toolbar.duration.once"),
        ]

    def _mode_segment_label(self, kind: str) -> str:
        return (
            tr("toolbar.mode.watch")
            if kind == "watch"
            else tr("toolbar.mode.trigger")
        )

    def _duration_segment_label(self, once: bool) -> str:
        return (
            tr("toolbar.duration.once")
            if once
            else tr("toolbar.duration.continuous")
        )

    def _kind_from_segment_label(self, label: str) -> str:
        return "watch" if label == tr("toolbar.mode.watch") else "trigger"

    def _once_from_segment_label(self, label: str) -> bool:
        return label == tr("toolbar.duration.once")

    def _sync_monitor_segments(self) -> None:
        """Refresh segmented-button labels/selection after language or mode changes."""
        if not hasattr(self, "mode_seg"):
            return
        self._segment_syncing = True
        try:
            self.mode_seg.configure(values=self._mode_segment_values())
            self.duration_seg.configure(values=self._duration_segment_values())
            self.mode_seg.set(self._mode_segment_label(self._monitor_kind))
            self.duration_seg.set(self._duration_segment_label(self._monitor_once))
        finally:
            self._segment_syncing = False

    def _on_mode_segment(self, value: str) -> None:
        if getattr(self, "_segment_syncing", False):
            return
        self._monitor_kind = self._kind_from_segment_label(value)
        self._apply_monitor_mode_buttons()
        if self._controller.mode != "idle":
            self._on_compact_run()

    def _on_duration_segment(self, value: str) -> None:
        if getattr(self, "_segment_syncing", False):
            return
        self._monitor_once = self._once_from_segment_label(value)
        self._apply_monitor_mode_buttons()
        if self._controller.mode != "idle":
            self._on_compact_run()

    def _on_compact_run(self) -> None:
        mode = compose_monitor_mode(self._monitor_kind, once=self._monitor_once)
        {
            TRIGGER_MODE: self._on_start,
            WATCH_MODE: self._on_watch,
            TRIGGER_ONCE_MODE: self._on_start_once,
            WATCH_ONCE_MODE: self._on_watch_once,
        }[mode]()

    def _on_compact_ctrl_configure(self, _event: Any = None) -> None:
        self._reflow_compact_panel()

    def _reflow_compact_panel(self) -> None:
        """Reflow compact wrap rows after resize / i18n text width changes."""
        if not hasattr(self, "_run_flow"):
            return
        self.update_idletasks()
        self._run_flow.reflow()
        self._settings_flow.reflow()

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
            tr("toolbar.run"),
            min_width=96,
            size=13,
            weight="bold",
        )
        _fit_button(
            self.stop_btn,
            tr("toolbar.stop"),
            min_width=80,
            size=13,
            weight="bold",
        )
        labels = _after_open_labels()
        _fit_option_menu(
            self.after_open_menu,
            [labels[effect] for effect in _AFTER_OPEN_EFFECTS],
            min_width=104,
            size=13,
        )
        self._sync_monitor_segments()
        self._render_status_text()
        self._refresh_trigger_controls()
        self._reflow_compact_panel()

    # ------------------------------------------------------------------
    # Channel list operations
    # ------------------------------------------------------------------
    def _populate_channels(self) -> None:
        channels = self.config.get("channels", [])
        for ch in channels:
            self._add_channel_row(ch)
        self._refresh_move_buttons()
        self._refresh_empty_hint()

    # ------------------------------------------------------------------
    # Channel status persistence (restore on launch, save on quit/hide)
    # ------------------------------------------------------------------
    def _restore_status_cache(self) -> None:
        """Repaint rows from the previous session's snapshot, marked pending."""
        cache = self.config.get("channel_status_cache")
        statuses = status_cache.restore_statuses(cache)
        if not statuses:
            return
        names = status_cache.restore_display_names(cache)
        for row in self._channel_rows:
            if names.get(row.key):
                row.set_display_name(names[row.key])
            status = statuses.get(row.key)
            if status is not None:
                row.set_status(status, pending=True)

    def _collect_statuses_for_cache(self) -> dict[str, ChannelStatus]:
        """Merge persisted, row UI, and monitor snapshots (monitor wins)."""
        existing = status_cache.restore_statuses(
            self.config.get("channel_status_cache")
        )
        from_rows = {
            row.key: snapshot
            for row in self._channel_rows
            if (snapshot := row.status_snapshot()) is not None
        }
        from_monitor = {
            key: status
            for key, status in self._controller.snapshot_statuses().items()
            if status_cache.serialize_status(status) is not None
        }
        return status_cache.merge_status_maps(existing, from_rows, from_monitor)

    def _save_status_cache(self) -> None:
        """Snapshot confirmed statuses into config for the next launch.

        Never overwrite a non-empty cache with an empty snapshot — that would
        erase the last good session when the monitor was already stopped.
        """
        statuses = self._collect_statuses_for_cache()
        if not statuses:
            return
        names = self._controller.snapshot_display_names()
        for row in self._channel_rows:
            display = (row.channel.get("display_name") or "").strip()
            if display and row.key not in names:
                names[row.key] = display
        self.config["channel_status_cache"] = status_cache.build_cache(
            statuses, names
        )

    def save_status_cache(self) -> None:
        """AppEventSink hook: refresh in-memory cache after each poll cycle."""
        self._save_status_cache()
        # Keep abnormal termination from discarding the newest completed poll.
        # The existing coalescer limits this to one atomic write per burst.
        self._schedule_config_save(delay_ms=500)

    def _monitor_seed_args(self) -> tuple[dict[str, Any], float]:
        """Build (initial_statuses, last_activity_epoch) for a monitor start.

        Statuses come from the live rows so a stream that was already live is
        not re-triggered as a fresh edge. The persisted ``saved_at`` gap only
        feeds wake-verification on the first start after launch.
        """
        initial_statuses = {
            row.key: snapshot
            for row in self._channel_rows
            if (snapshot := row.status_snapshot()) is not None
        }
        epoch = 0.0
        if not self._status_cache_consumed:
            epoch = status_cache.saved_at_epoch(
                self.config.get("channel_status_cache")
            )
            self._status_cache_consumed = True
        return initial_statuses, epoch

    def _refresh_empty_hint(self) -> None:
        if self._channel_rows:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _add_channel_row(self, channel: dict[str, str]) -> None:
        self.empty_label.place_forget()

        def on_delete(ch=channel):
            self._remove_channel(ch)

        def on_move_up(ch=channel):
            self._move_channel(ch, -1)

        def on_move_down(ch=channel):
            self._move_channel(ch, 1)

        def on_toggle_enabled(ch=channel):
            self._on_channel_toggle_enabled(ch)

        row_ref: list[ChannelRow] = []

        def on_reorder_begin(y_root: int) -> None:
            self._begin_channel_reorder(row_ref[0], y_root=y_root)

        def on_reorder_motion(y_root: int) -> None:
            self._update_channel_reorder(y_root)

        def on_reorder_release() -> None:
            self._end_channel_reorder(commit=True)

        row = ChannelRow(
            self.scroll_frame.state_host,
            channel,
            on_delete=on_delete,
            on_move_up=on_move_up,
            on_move_down=on_move_down,
            on_toggle_enabled=on_toggle_enabled,
            on_reorder_begin=on_reorder_begin,
            on_reorder_motion=on_reorder_motion,
            on_reorder_release=on_reorder_release,
            get_browser_settings=self.current_browser_settings,
        )
        row_ref.append(row)
        adapter = self.scroll_frame.add_state_row(row)
        self._channel_rows.append(adapter)
        # add_state_row renders the current list before the adapter is exposed
        # to App. Re-render once with the shared identity list used by reorder
        # and status persistence.
        self.scroll_frame.set_rows(self._channel_rows)
        self._refresh_move_buttons()

    def _remove_channel(self, channel: dict[str, str]) -> None:
        if self._reorder_mode.active:
            self._end_channel_reorder(commit=False)
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
        self.scroll_frame.set_rows(self._channel_rows)
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

        to_index = new_index + 1 if offset > 0 else new_index
        self._move_channel_to_index(channel, to_index)

    def _move_channel_to_index(self, channel: dict[str, str], to_index: int) -> None:
        channels = self.config.get("channels", [])
        try:
            from_index = channels.index(channel)
        except ValueError:
            self._repack_channel_rows()
            return

        insert_at = apply_list_move(from_index, to_index, len(channels))
        if insert_at is None:
            self._repack_channel_rows()
            return

        item = channels.pop(from_index)
        channels.insert(insert_at, item)
        row = self._channel_rows.pop(from_index)
        self._channel_rows.insert(insert_at, row)

        self._repack_channel_rows()
        self._save_config()
        self._refresh_move_buttons()
        self._controller.update_channels(channels)


    def _schedule_preview_repack(self, order: list[int]) -> None:
        self._pending_preview_order = list(order)
        if self._preview_repack_after is not None:
            if self._reorder_mode.active:
                self.after_cancel(self._preview_repack_after)
            else:
                return
        if self._reorder_mode.active:
            self._preview_repack_after = self.after(
                0, self._flush_scheduled_preview_repack
            )
        else:
            self._preview_repack_after = self.after_idle(
                self._flush_scheduled_preview_repack
            )

    def _flush_scheduled_preview_repack(self) -> None:
        self._preview_repack_after = None
        order = self._pending_preview_order
        self._pending_preview_order = None
        if order is None:
            return
        flush = self._reorder_mode.active
        self._repack_channel_rows_preview(order, flush=flush)

    def _cancel_scheduled_preview_repack(self) -> None:
        if self._preview_repack_after is not None:
            self.after_cancel(self._preview_repack_after)
            self._preview_repack_after = None
        self._pending_preview_order = None

    def _repack_channel_rows(self) -> None:
        self._cancel_scheduled_preview_repack()
        self._preview_pack_order = None
        self._repack_channel_rows_preview(
            list(range(len(self._channel_rows))), flush=True
        )

    def _repack_channel_rows_preview(
        self, order: list[int], *, flush: bool = False
    ) -> None:
        if order == self._preview_pack_order:
            return
        canvas = self.scroll_frame._parent_canvas
        yview = canvas.yview()
        previous = self._preview_pack_order
        self.scroll_frame.set_visual_order(order)
        self._preview_pack_order = list(order)
        canvas.yview_moveto(yview[0])
        if flush:
            canvas.update_idletasks()

        _reorder_debug(
            "repack",
            mode="canvas",
            order=order,
            previous=previous,
            visual=order,
        )

    def _full_repack_preview_rows(
        self, rows: list[CanvasChannelRowAdapter], order: list[int]
    ) -> None:
        if rows is self._channel_rows:
            self.scroll_frame.set_visual_order(order)

    def _begin_channel_reorder(
        self, row: CanvasChannelRowAdapter, *, y_root: int
    ) -> None:
        if self._reorder_mode.active:
            return
        try:
            source_index = self._channel_rows.index(row)
        except ValueError:
            return
        self.update_idletasks()
        self._preview_pack_order = list(range(len(self._channel_rows)))
        self._reorder_mode.begin(
            row,
            source_index=source_index,
            num_rows=len(self._channel_rows),
            y_root=y_root,
        )

    def _update_channel_reorder(self, y_root: int) -> None:
        if not self._reorder_mode.active:
            return
        self._autoscroll_channel_list_during_reorder(y_root)
        self._reorder_mode.track_pointer(
            y_root,
            rows=self._channel_rows,
            num_rows=len(self._channel_rows),
        )

    def _autoscroll_channel_list_during_reorder(self, y_root: int) -> None:
        canvas = self.scroll_frame._parent_canvas
        try:
            top = canvas.winfo_rooty()
            height = canvas.winfo_height()
        except Exception:
            return
        margin = 48
        if y_root < top + margin:
            canvas.yview_scroll(-2, "units")
        elif y_root > top + height - margin:
            canvas.yview_scroll(2, "units")

    def _on_channel_reorder_motion_global(self, event: Any) -> None:
        if self._reorder_mode.active:
            if not (getattr(event, "state", 0) & 0x100):
                self._end_channel_reorder(commit=False)
                return
            self._update_channel_reorder(event.y_root)

    @staticmethod
    def _wheel_nudge_steps(event: Any) -> int:
        num = getattr(event, "num", None)
        if num == 4:
            return -1
        if num == 5:
            return 1
        delta = getattr(event, "delta", 0)
        if delta > 0:
            return -1
        if delta < 0:
            return 1
        return 0

    def _on_channel_reorder_wheel_global(self, event: Any) -> str | None:
        if not self._reorder_mode.active:
            return None
        if not (getattr(event, "state", 0) & 0x100):
            self._end_channel_reorder(commit=False)
            return None
        steps = self._wheel_nudge_steps(event)
        if steps != 0:
            self._reorder_mode.nudge_target(
                steps, num_rows=len(self._channel_rows)
            )
        return "break"

    def _on_channel_reorder_escape_global(self, _event: Any) -> str | None:
        if self._reorder_mode.active:
            self._end_channel_reorder(commit=False)
            return "break"
        return None

    def _on_focus_out_cancel_reorder(self, _event: Any) -> None:
        if self._reorder_mode.active:
            self._end_channel_reorder(commit=False)

    def _on_channel_reorder_release_global(self, _event: Any) -> None:
        if self._reorder_mode.active:
            self._end_channel_reorder(commit=True)

    def _end_channel_reorder(self, *, commit: bool) -> None:
        if not self._reorder_mode.active:
            return
        row = self._reorder_mode.source_row
        target = self._reorder_mode.target_index
        channel = row.channel if row is not None else None
        insert_at = self._reorder_mode.finish(
            commit=commit, num_rows=len(self._channel_rows)
        )
        if row is not None:
            row.cancel_reorder_drag()
        self._cancel_scheduled_preview_repack()
        self._preview_pack_order = None
        if commit and insert_at is not None and channel is not None:
            self._move_channel_to_index(channel, target)
        else:
            self._repack_channel_rows()
        self._controller.tick()

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
        self._interval_caption.configure(text=tr("toolbar.interval_short"))
        self._interval_unit.configure(text=tr("toolbar.seconds"))
        self._mode_caption.configure(text=tr("toolbar.mode"))
        self._duration_caption.configure(text=tr("toolbar.duration"))
        self._notify_group_label.configure(text=tr("toolbar.group.notify"))
        self._open_group_label.configure(text=tr("toolbar.group.open"))
        self.notify_on_live_switch.configure(text=tr("toolbar.notify_on_live"))
        self.open_on_live_switch.configure(text=tr("toolbar.open_on_live"))
        self.notify_on_upcoming_switch.configure(
            text=tr("toolbar.notify_on_upcoming")
        )
        self.open_on_upcoming_switch.configure(
            text=tr("toolbar.open_on_upcoming")
        )
        self.notify_on_open_failure_switch.configure(
            text=tr("toolbar.notify_on_open_failure")
        )
        self._after_open_caption.configure(text=tr("toolbar.after_open"))
        current_effect = _after_open_key_for_display(self.after_open_var.get())
        labels = _after_open_labels()
        self.after_open_menu.configure(
            values=[labels[effect] for effect in _AFTER_OPEN_EFFECTS]
        )
        self.after_open_var.set(labels[current_effect])
        self._fit_main_toolbar_i18n()

    # ------------------------------------------------------------------
    # Monitor control
    # ------------------------------------------------------------------
    def _collect_monitor_config(self) -> tuple[list[dict[str, str]], int]:
        """Read interval/trigger widgets into config; return (channels, interval)."""
        channels = self.config.get("channels", [])
        try:
            interval = int(self.interval_var.get())
        except (TypeError, ValueError):
            interval = 60
        interval = max(10, interval)
        self.interval_var.set(str(interval))
        self.config["check_interval"] = interval

        self._persist_trigger_settings(save=False)
        return channels, interval

    def _trigger_settings_from_widgets(self) -> TriggerSettings:
        open_on_live = bool(self.open_on_live_var.get())
        return TriggerSettings(
            notify_on_live=bool(self.notify_on_live_var.get()),
            open_on_live=open_on_live,
            after_open=(
                _after_open_key_for_display(self.after_open_var.get())
                if open_on_live
                else LifecycleEffect.NONE
            ),
            notify_on_upcoming=bool(self.notify_on_upcoming_var.get()),
            open_on_upcoming=bool(self.open_on_upcoming_var.get()),
            notify_on_open_failure=bool(self.notify_on_open_failure_var.get()),
        )

    def _persist_trigger_settings(self, *, save: bool = True) -> None:
        settings = self._trigger_settings_from_widgets()
        self.config["trigger_settings"] = settings.as_dict()
        if save:
            # Switches can be toggled in quick succession.  Keep the in-memory
            # state immediate, but move the synchronous portable-config write
            # (including fsync) out of the click callback and coalesce bursts.
            self._schedule_config_save()
        self._refresh_trigger_controls()

    def _refresh_trigger_controls(self) -> None:
        """Keep the lifecycle selector dependent on browser launch."""
        if not hasattr(self, "after_open_menu"):
            return
        self.after_open_menu.configure(
            state="normal" if self.open_on_live_var.get() else "disabled"
        )
        self.notify_on_open_failure_switch.configure(
            state="normal" if self.open_on_live_var.get() else "disabled"
        )

    def _render_status_text(self) -> None:
        main = tr(self._status_text_key)
        self.status_text.configure(text=main, text_color=self._status_text_color)
        if hasattr(self, "status_dot"):
            self.status_dot.configure(text_color=self._status_text_color)
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
        if self._controller.mode not in (
            TRIGGER_MODE,
            WATCH_MODE,
            TRIGGER_ONCE_MODE,
            WATCH_ONCE_MODE,
        ):
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
        if self._controller.mode not in (
            TRIGGER_MODE,
            WATCH_MODE,
            TRIGGER_ONCE_MODE,
            WATCH_ONCE_MODE,
        ):
            return
        self._status_subline_key = "status.poll_waiting"
        self._status_subline_kwargs = {}
        self._render_status_text()

    def _set_awaiting_start_subline(self) -> None:
        self._status_subline_key = "status.awaiting_start"
        self._status_subline_kwargs = {}
        self._render_status_text()

    def _on_start(self) -> None:
        self._start_monitor_mode(
            TRIGGER_MODE, "status.trigger_running", _CLR_LIVE, "tray.tooltip.trigger"
        )

    def _on_watch(self) -> None:
        self._start_monitor_mode(
            WATCH_MODE, "status.watching", "#64b5f6", "tray.tooltip.watch"
        )

    def _on_start_once(self) -> None:
        self._start_monitor_mode(
            TRIGGER_ONCE_MODE,
            "status.trigger_once_running",
            _CLR_LIVE,
            "tray.tooltip.trigger",
        )

    def _on_watch_once(self) -> None:
        self._start_monitor_mode(
            WATCH_ONCE_MODE,
            "status.watching_once",
            "#64b5f6",
            "tray.tooltip.watch",
        )

    def _start_monitor_mode(
        self, mode: str, status_key: str, color: str, tray_tooltip_key: str
    ) -> None:
        """Start or switch the global monitor mode from a bottom-bar action."""
        channels, interval = self._collect_monitor_config()
        initial_statuses, epoch = self._monitor_seed_args()
        if not self._controller.start(
            mode,
            channels,
            interval,
            initial_statuses=initial_statuses,
            last_activity_epoch=epoch,
        ):
            return
        self.config["monitor_mode"] = mode
        self._save_config()
        self._sync_selectors_from_mode(mode)
        self._apply_monitor_mode_buttons()
        self._set_status_text(status_key, color)
        self._tray.update_tooltip_key(tray_tooltip_key)

    def on_monitor_cycle_complete(self) -> None:
        """Return to idle after a global one-cycle monitor run."""
        mode = self._controller.mode
        if not is_one_shot_monitor_mode(mode):
            return
        persistent_mode = base_monitor_mode(mode)
        self._save_status_cache()
        self._controller.finish_one_shot()
        # Do not persist a one-shot mode after it has been consumed; otherwise
        # silent startup would unexpectedly run another cycle on every launch.
        self.config["monitor_mode"] = persistent_mode
        self._save_config()
        self._apply_monitor_mode_buttons()
        self._set_status_text("status.stopped", _CLR_OFFLINE)
        self._set_awaiting_start_subline()
        self._tray.update_tooltip_key("tray.tooltip.stopped")

    def on_stop(self, *, is_user_action: bool = True) -> None:
        self._save_status_cache()
        self._save_config()
        self._action_coordinator.cancel_generation(self._controller.generation)
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
                    closed = self._platform.window.close_all()
                    if closed:
                        logger.info(
                            "close_on_stop: WM_CLOSEd %d tracked window(s)",
                            closed,
                        )
                except Exception:
                    logger.exception("close_on_stop sweep failed")

    def _apply_monitor_mode_buttons(self) -> None:
        states = compact_control_button_states(
            self._controller.mode,
            kind=self._monitor_kind,
            once=self._monitor_once,
        )
        self.start_btn.configure(state=states["start"])
        self.stop_btn.configure(state=states["stop"])

    def _sync_selectors_from_mode(self, mode: str) -> None:
        if mode == "idle":
            return
        kind, once = decompose_monitor_mode(mode)
        self._monitor_kind = kind
        self._monitor_once = once
        self._sync_monitor_segments()

    def _on_app_settings(self) -> None:
        dialog = AppSettingsDialog(
            self,
            minimize_to_tray=bool(self.minimize_to_tray_var.get()),
            run_on_startup=bool(self.startup_var.get()),
            on_tray_changed=self._set_minimize_to_tray,
            on_startup_changed=self._set_run_on_startup,
            on_reset_launch_records=self._reset_launch_records_action,
        )
        self.wait_window(dialog)

    def _set_minimize_to_tray(self, enabled: bool) -> None:
        self.minimize_to_tray_var.set(enabled)
        self._on_tray_switch_toggle()

    def _set_run_on_startup(self, enabled: bool) -> bool:
        self.startup_var.set(enabled)
        self._on_startup_toggle()
        return bool(self.startup_var.get()) == bool(enabled)

    def _reset_launch_records_action(self) -> tuple[bool, str]:
        """UI callback for Settings → reset launch records."""
        # Cancel any coalesced save so a stale pre-reset callback cannot
        # rewrite channel_status_cache after a successful clear.
        self._cancel_scheduled_config_save()

        def after_guard() -> None:
            self._cancel_scheduled_config_save()
            self._controller.purge_pending_for_reset()

        def persist_config(config: dict[str, Any]) -> None:
            # Must propagate OSError — never report success if disk still
            # holds the old channel_status_cache.
            try:
                config["window_geometry"] = self.geometry()
            except Exception:
                pass
            self.config = config_manager.save(config)

        result = reset_launch_records(
            db=self._db,
            config=self.config,
            paths=portable_paths(),
            controller=self._controller,
            coordinator=self._action_coordinator,
            persist_config=persist_config,
            after_guard=after_guard,
        )
        if not result.ok:
            return False, result.error_key or "settings.reset.fail"
        # Clear row snapshots only after durable persistence + DB clear.
        for row in self._channel_rows:
            row.raw.clear_launch_status()
        self.scroll_frame.redraw_all()
        return True, "settings.reset.ok"

    def _on_browser_settings(self) -> None:
        dialog = BrowserSettingsDialog(
            self,
            self.config.get("browser_settings", {}) or {},
            self.config.get("viewer_engagement", {}) or {},
        )
        self.wait_window(dialog)
        if dialog.result is not None:
            self.config["browser_settings"] = dialog.result
            if dialog.viewer_engagement_result is not None:
                self.config["viewer_engagement"] = dialog.viewer_engagement_result
                configure_viewer_engagement(
                    ViewerEngagementSettings.from_dict(
                        dialog.viewer_engagement_result
                    )
                )
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
        action: ActionPlan | str,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
        generation: int | None = None,
    ) -> None:
        """Submit a validated live-event action to the coordinator."""
        plan = action if isinstance(action, ActionPlan) else action_plan_for(action)
        if plan is None:
            logger.warning("Ignoring unknown live action: %s", action)
            return
        if generation is None:
            generation = self._controller.generation
        self._action_coordinator.submit(
            plan,
            info,
            browser_settings,
            generation,
        )

    def _on_scroll_repaint_idle(self) -> None:
        """Flush queued row updates once scrolling has settled."""
        self._controller.tick()

    def _tick_elapsed_labels(self) -> None:
        if self._truly_quitting:
            return
        if not self.defer_channel_row_repaints:
            for row in self._channel_rows:
                row.refresh_elapsed_display()
        self.after(30_000, self._tick_elapsed_labels)

    def _monitor_health_check(self) -> None:
        """Restart the background monitor if its thread died unexpectedly."""
        if self._truly_quitting:
            return
        try:
            self.maybe_restart_dead_monitor()
        except Exception:
            logger.exception("Monitor health check failed; continuing")
        self.after(10_000, self._monitor_health_check)

    def maybe_restart_dead_monitor(self) -> None:
        if self._controller.mode not in (
            TRIGGER_MODE,
            WATCH_MODE,
            TRIGGER_ONCE_MODE,
            WATCH_ONCE_MODE,
        ):
            return
        channels, interval = self._collect_monitor_config()
        if not self._controller.restart_if_dead(channels, interval):
            return
        if self._controller.mode in (TRIGGER_MODE, TRIGGER_ONCE_MODE):
            self._set_status_text("status.monitor_restarted", _CLR_LIVE)
            self._tray.update_tooltip_key("tray.tooltip.trigger")
        else:
            self._set_status_text("status.monitor_restarted", "#64b5f6")
            self._tray.update_tooltip_key("tray.tooltip.watch")

    def _poll_events(self) -> None:
        if self._truly_quitting:
            return
        self.after(80, self._poll_events)
        try:
            self._controller.tick()
        except Exception:
            logger.exception("UI event bridge tick failed; continuing")

    def handle_channel_offline(
        self, entry: ChannelEntry, offline_info: Any
    ) -> None:
        """Close any browser window we opened for this channel."""
        url = getattr(offline_info, "url", "") or ""
        if not url:
            logger.warning(
                "close_on_offline skipped for %s: offline payload has empty url",
                entry.key,
            )
            return
        # Title-keyword fallback is only safe when we launched with a dedicated
        # profile (HWND tracking). Shared-profile / webbrowser opens register
        # a one-shot block instead; still pass keywords only when isolation is
        # available so a stale block cannot be bypassed by config drift.
        settings = self.current_browser_settings()
        keywords: list[str] | None = None
        if settings and self._platform.window.tracking_available(settings, url):
            keywords = []
            if entry.name:
                keywords.append(entry.name)
            display_name = getattr(offline_info, "display_name", "") or ""
            if not display_name:
                display_name = self._controller.snapshot_display_names().get(
                    entry.key, ""
                )
            if display_name and display_name not in keywords:
                keywords.append(display_name)
        try:
            closed = self._platform.window.close_for_url(
                url, title_keywords=keywords
            )
        except Exception:
            logger.exception("close_browser_window_for_url failed for %s", url)
            return
        if closed:
            logger.info(
                "Closed %d browser window(s) for %s (%s)", closed, entry.key, url
            )
        else:
            logger.warning(
                "close_on_offline found no window for %s (%s) keywords=%r",
                entry.key,
                url,
                keywords,
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

    def _schedule_config_save(self, *, delay_ms: int = 250) -> None:
        """Coalesce settings writes triggered by rapid UI changes."""
        if self._truly_quitting:
            return
        if self._config_save_after is not None:
            try:
                self.after_cancel(self._config_save_after)
            except Exception:
                pass
        self._config_save_after = self.after(delay_ms, self._flush_config_save)

    def _flush_config_save(self) -> None:
        self._config_save_after = None
        if not self._truly_quitting:
            self._save_config()

    def _cancel_scheduled_config_save(self) -> None:
        if self._config_save_after is None:
            return
        try:
            self.after_cancel(self._config_save_after)
        except Exception:
            pass
        self._config_save_after = None

    def _save_config(self) -> None:
        # A synchronous save is still required for explicit lifecycle actions
        # and shutdown.  Cancel the coalesced callback so it cannot write a
        # second time after this call.
        self._cancel_scheduled_config_save()
        try:
            self.config["window_geometry"] = self.geometry()
        except Exception:
            pass
        try:
            self.config = config_manager.save(self.config)
        except OSError:
            # A transient Windows file lock must not terminate a Tk callback
            # or leave the application half-closed. The atomic temp file is
            # retained for recovery on the next launch.
            logger.exception("Config save failed; keeping application alive")

    def _on_close(self) -> None:
        # Legacy entry point kept for callers outside the WM_DELETE_WINDOW
        # protocol; route it through the idempotent full-quit path.
        self.quit_app()


def _fix_linux_frozen_env() -> None:
    """Restore LD_LIBRARY_PATH for PyInstaller frozen builds on Linux.

    PyInstaller overrides LD_LIBRARY_PATH to its bundle dir (``_internal`` /
    temp extraction), which breaks DNS resolution (glibc NSS dlopen) and
    subprocess calls (browser, xdg-open).  Restoring the original value after
    Python is fully loaded is safe because all bundled .so files are already
    mapped into memory.
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


def _run_frozen_self_check() -> None:
    """Import platform-specific frozen dependencies without opening the UI."""
    if not getattr(sys, "frozen", False):
        return
    if sys.platform == "win32":
        # These imports are intentionally lazy in normal runtime paths.  The
        # release smoke command makes missing PyInstaller hidden imports fail
        # at build time instead of on the first toast/tray action.
        __import__("pystray._win32")
        __import__("winotify")


def main() -> None:
    if "--self-check" in sys.argv:
        _run_frozen_self_check()
        return

    if getattr(sys, "frozen", False) and sys.platform != "win32":
        _fix_linux_frozen_env()

    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Install logging before config preload. A corrupt config or interrupted
    # atomic save must be diagnosable in a windowed build as well.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(log_fmt))
        root_logger.addHandler(stream_handler)

    data_dir = portable_paths().root
    log_dir = portable_paths().logs_dir
    log_file = portable_paths().application_log
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_fmt))
        root_logger.addHandler(file_handler)
    except OSError:
        root_logger.exception("Failed to initialize application file logging")

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

    _check_writable(data_dir)

    previous_thread_excepthook = threading.excepthook

    def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is KeyboardInterrupt:
            previous_thread_excepthook(args)
            return
        logger.critical(
            "Unhandled exception in thread %s",
            args.thread.name if args.thread is not None else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _log_thread_exception

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

    try:
        app = App(silent=silent)
        app.mainloop()
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception:
        logger.critical("Fatal application exception", exc_info=True)
        raise
    finally:
        lock.release()


if __name__ == "__main__":
    main()
