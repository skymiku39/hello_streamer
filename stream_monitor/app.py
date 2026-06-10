"""CustomTkinter 主視窗 — 開播監聽器 GUI + 系統匣常駐 + 單一執行個體。"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
from pathlib import Path
from typing import Any, Callable

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
    _CLR_CARD_DISABLED,
    _CLR_DELETE_HOVER,
    _CLR_LINK,
    _CLR_LINK_HOVER,
    _CLR_LIVE,
    _CLR_OFFLINE,
    _CLR_START,
    _CLR_START_HOVER,
    _CLR_STOP,
    _CLR_STOP_HOVER,
    _CLR_TEXT_DISABLED,
    _CLR_TWITCH,
    _CLR_YOUTUBE,
    _MIN_WINDOW_HEIGHT,
    _MIN_WINDOW_WIDTH,
    _action_displays,
    _action_key_for_display,
    _action_labels,
    _button_width,
    _clamped_window_geometry,
    _fit_button,
    _fit_label_width,
    _fit_option_menu,
    _font,
    _format_countdown,
    _format_elapsed,
    _format_row_time,
    _language_icon,
    _status_bar_text_width,
    _status_row_label_width,
    _tooltip,
    _tooltip_tr,
)
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.notifier import (
    action_for_stream_status,
    browser_window_tracking_available,
    close_all_tracked_windows,
    close_browser_window_for_url,
    execute_action,
    open_url,
    prune_off_topic_tracked_windows,
)
from stream_monitor.single_instance import SingleInstance
from stream_monitor.startup import disable_startup, enable_startup, is_startup_enabled
from stream_monitor.tray import TrayIcon
from stream_monitor.util import channel_key, channel_page_url

logger = logging.getLogger(__name__)


def _is_live_state(state: bool | str | None) -> bool:
    return state is True or state == "live"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


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
        self._status_timestamp: str = ""
        self._status_scheduled_start: str = ""
        self._vod_url: str = ""
        self._upcoming_url: str = ""
        self._ended_at_source: str = ""

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
            width=120,
            anchor="e",
            font=_font(11, "bold"),
            text_color="#aab3d5",
        )
        self.time_label.pack(side="left", padx=(6, 0), pady=8)

        self.status_label = ctk.CTkLabel(
            self,
            text=tr("status.row.placeholder"),
            width=_status_row_label_width(),
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

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self:
            return
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
        self.status_label.configure(width=_status_row_label_width())
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
        return channel_page_url(self.channel["platform"], self.channel["name"])

    def _open_channel_page(self) -> None:
        open_url(self._channel_url(), self._get_browser_settings())

    def _offline_link_url(self) -> str:
        """Offline link priority — platform-specific."""
        if self.channel["platform"] == "youtube" and self._upcoming_url:
            return self._upcoming_url
        if self._vod_url:
            return self._vod_url
        return self._channel_url()

    def _open_current_page(self) -> None:
        if self._status_state == "offline":
            open_url(self._offline_link_url(), self._get_browser_settings())
            return
        open_url(self._active_url or self._channel_url(), self._get_browser_settings())

    def _open_active_page(self) -> None:
        if (
            self._status_state == "offline"
            and self.channel["platform"] == "youtube"
            and self._upcoming_url
        ):
            open_url(self._upcoming_url, self._get_browser_settings())
            return
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
        # Pause/resume always clears monitor-only — resume goes back into
        # "full triggering" mode and pause resets the next-resume baseline.
        self.channel["monitor_only"] = False
        # enabled really changed → we want a clean visual (and the polling
        # backend is going to give us a fresh status reading anyway).
        self._apply_enabled_visual(reset_status=True)
        self._on_toggle_enabled()

    def _on_monitor_only_click(self) -> None:
        # Toggling the eye always implies the channel must be enabled — if
        # the user clicks it from a paused state, they're effectively
        # un-pausing into monitor-only mode.
        was_enabled = self.channel.get("enabled", True)
        currently_monitor_only = was_enabled and self.channel.get(
            "monitor_only", False
        )
        if currently_monitor_only:
            self.channel["enabled"] = True
            self.channel["monitor_only"] = False
        else:
            self.channel["enabled"] = True
            self.channel["monitor_only"] = True
        # Crucial: when the channel was *already* enabled, we are only
        # flipping the trigger-suppression flag — the live/upcoming/offline
        # display the user is currently watching (and especially the
        # "live since N min" elapsed clock) is still valid and should NOT
        # be wiped. Only reset when we just un-paused.
        self._apply_enabled_visual(reset_status=not was_enabled)
        self._on_toggle_enabled()

    def _reset_status_cache(self) -> None:
        """Forget every cached status value so the next paint starts blank."""
        self._active_url = ""
        self._vod_url = ""
        self._upcoming_url = ""
        self._ended_at_source = ""
        self._status_title = ""
        self._status_state = None
        self._status_countdown = ""
        self._status_elapsed = ""
        self.time_label.configure(text="")

    def _apply_enabled_visual(self, reset_status: bool = True) -> None:
        enabled = self.channel.get("enabled", True)
        monitor_only = bool(self.channel.get("monitor_only", False)) and enabled
        if reset_status:
            self._reset_status_cache()
        if enabled:
            self.configure(fg_color=_CLR_CARD)
            self.platform_label.configure(
                fg_color=self._platform_color, text_color="white"
            )
            self.name_label.configure(text_color=("gray10", "gray90"))
            self.id_label.configure(text_color="#9aa0b4")
            if reset_status or self._status_state is None:
                # No live data to preserve → fall back to the placeholder text.
                self.status_label.configure(
                    text=tr("status.row.placeholder"),
                    text_color="#666677",
                    fg_color="transparent",
                )
                self._set_link_tip_key("tooltip.row.link.default")
            else:
                # Preserve the existing live/upcoming/offline display so the
                # "live since" clock and stream title don't reset when the
                # user only flipped monitor-only on/off.
                self._render_status_visuals()
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
            self._set_link_tip_key("tooltip.row.link.paused")
        self._apply_toggle_visual(enabled, monitor_only)
        self._apply_monitor_only_visual(monitor_only, enabled)
        self._refresh_toggle_tip()
        self._refresh_monitor_only_tip()
        if reset_status and hasattr(self, "_status_tip"):
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

    def _apply_toggle_visual(self, enabled: bool, monitor_only: bool) -> None:
        """Pick the pause/resume button's icon + accent so the user can see
        at a glance that this channel is being "watched but not triggered".

        We keep the regular ⏸ glyph (the channel *is* still monitored) but
        tint the border with the same blue accent the eye uses. That makes
        the visual association explicit: blue border on ⏸ ↔ blue eye-fill.
        """
        if not hasattr(self, "toggle_btn"):
            return
        if not enabled:
            self.toggle_btn.configure(
                text="▶",
                border_color="#3c4566",
                hover_color="#243052",
            )
            return
        if monitor_only:
            self.toggle_btn.configure(
                text="⏸",
                border_color="#1565c0",
                hover_color="#1d4d80",
            )
        else:
            self.toggle_btn.configure(
                text="⏸",
                border_color="#3c4566",
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
            self._vod_url = ""
            self._upcoming_url = ""
            self._ended_at_source = ""
        elif state == "upcoming":
            self._status_state = "upcoming"
            self._status_timestamp = detail.scheduled_start if detail else ""
            self._status_countdown = _format_countdown(self._status_timestamp)
            self._status_elapsed = ""
        elif _is_live_state(state):
            self._status_state = "live"
            self._status_timestamp = detail.started_at if detail else ""
            self._status_elapsed = _format_elapsed(self._status_timestamp)
            self._status_countdown = ""
        else:
            self._status_state = "offline"
            self._status_title = detail.title if detail else ""
            self._vod_url = (detail.vod_url if detail else "") or ""
            self._upcoming_url = (detail.upcoming_url if detail else "") or ""
            self._active_url = ""
            self._ended_at_source = (
                detail.ended_at_source if detail else ""
            ) or "confirmed"
            sched = (detail.scheduled_start if detail else "") or ""
            self._status_scheduled_start = sched
            self._status_countdown = (
                _format_countdown(sched) if self._upcoming_url and sched else ""
            )
            self._status_timestamp = detail.ended_at if detail else ""
            self._status_elapsed = _format_elapsed(self._status_timestamp)

        self._render_status_visuals()

    def _compose_time_label_text(self) -> str:
        """Build i18n time label from cached status timestamps."""
        state = self._status_state
        if state == "live":
            self._status_elapsed = _format_elapsed(self._status_timestamp)
            return _format_row_time("live", self._status_elapsed)
        if state == "upcoming":
            self._status_countdown = _format_countdown(self._status_timestamp)
            return _format_row_time("upcoming", self._status_countdown)
        if state == "offline":
            if self._upcoming_url and self._status_scheduled_start:
                self._status_countdown = _format_countdown(
                    self._status_scheduled_start
                )
                if self._status_countdown:
                    return _format_row_time("countdown", self._status_countdown)
            self._status_elapsed = _format_elapsed(self._status_timestamp)
            return _format_row_time("offline", self._status_elapsed)
        return ""

    def refresh_elapsed_display(self) -> None:
        """Recompute time_label from cached ISO timestamp (live/offline/upcoming)."""
        if not self.channel.get("enabled", True):
            return
        self.time_label.configure(text=self._compose_time_label_text())

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
            self.time_label.configure(text=self._compose_time_label_text())
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
            self.time_label.configure(text=self._compose_time_label_text())
            self.status_label.configure(
                text=tr("status.row.live"),
                text_color="white",
                fg_color="#1b5e20",
                cursor="hand2",
            )
            self._status_tip.set_text(self._compose_status_tip(state))
            self._set_link_tip_with_title("tooltip.row.link.live")
            return

        # offline — elapsed since end, or countdown when waiting room is linked
        self.time_label.configure(text=self._compose_time_label_text())
        self.status_label.configure(
            text=tr("status.row.offline"),
            text_color="#999999",
            fg_color="transparent",
            cursor=(
                "hand2"
                if self.channel["platform"] == "youtube" and self._upcoming_url
                else ""
            ),
        )
        self._status_tip.set_text(self._compose_status_tip(state))
        if self._upcoming_url:
            self._set_link_tip_with_title("tooltip.row.link.upcoming")
        elif self._vod_url:
            self._set_link_tip_with_title("tooltip.row.link.vod")
        else:
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
        if state == "offline" and self._upcoming_url and self._status_countdown:
            parts.append(
                tr(
                    "tooltip.row.status.starts_in",
                    countdown=self._status_countdown,
                )
            )
        elif state == "offline" and self._status_elapsed:
            if self._ended_at_source == "vod":
                parts.append(
                    tr(
                        "tooltip.row.status.offline_elapsed_vod",
                        elapsed=self._status_elapsed,
                    )
                )
            else:
                parts.append(
                    tr(
                        "tooltip.row.status.offline_elapsed_confirmed",
                        elapsed=self._status_elapsed,
                    )
                )
        if parts:
            return "\n".join(parts)
        if state == "upcoming":
            return tr("tooltip.row.status.upcoming")
        if state == "offline":
            return tr("tooltip.row.status.offline")
        return tr("tooltip.row.status.live")

    @property
    def key(self) -> str:
        return channel_key(self.channel["platform"], self.channel["name"])

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
        toolbar.grid_columnconfigure(2, weight=1)

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
            command=self._on_stop,
        )
        self.stop_btn.pack(side="left")
        _tooltip_tr(self.stop_btn, "tooltip.stop")

        self.status_text = ctk.CTkLabel(
            toolbar,
            text=tr("status.idle"),
            font=_font(13),
            text_color=_CLR_OFFLINE,
            width=_status_bar_text_width(),
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
        status_text = tr(self._status_text_key)
        _fit_label_width(self.status_text, status_text, min_width=96)
        self.status_text.configure(text_color=self._status_text_color)
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
            get_browser_settings=self._current_browser_settings,
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
        elif self._monitor is not None:
            self._monitor.update_interval(interval)
            self._monitor.update_channels(channels)
            self._monitor.restart_thread()
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
        _fit_label_width(self.status_text, tr(key), min_width=96)
        self.status_text.configure(text_color=color)

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

    def _on_stop(self, *, is_user_action: bool = True) -> None:
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

        # close_on_stop fires only when the user explicitly hit Stop — never
        # on the auto-stop produced by open_and_stop, because that just
        # opened the very player window the user wants to keep watching.
        if is_user_action:
            browser_settings = self.config.get("browser_settings") or {}
            if browser_settings.get("close_on_stop"):
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

    def _apply_live_row_status(self, entry: ChannelEntry, info: StreamInfo) -> None:
        for row in self._channel_rows:
            if row.key == entry.key:
                row.set_status(self._channel_status_from_stream_info(info))
                break

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

    def _tick_elapsed_labels(self) -> None:
        for row in self._channel_rows:
            row.refresh_elapsed_display()
        self.after(30_000, self._tick_elapsed_labels)

    def _monitor_health_check(self) -> None:
        """Restart the background monitor if its thread died unexpectedly."""
        self._maybe_restart_dead_monitor()
        self.after(10_000, self._monitor_health_check)

    def _maybe_restart_dead_monitor(self) -> None:
        if self._monitor_mode not in ("trigger", "watch"):
            return
        if self._monitor is None or self._monitor.is_running:
            return
        logger.warning(
            "Monitor thread died unexpectedly (mode=%s), restarting",
            self._monitor_mode,
        )
        if not self._ensure_monitor_running():
            return
        if self._monitor_mode == "trigger":
            self._set_status_text("status.monitor_restarted", _CLR_LIVE)
            self._tray.update_tooltip_key("tray.tooltip.trigger")
        else:
            self._set_status_text("status.monitor_restarted", "#64b5f6")
            self._tray.update_tooltip_key("tray.tooltip.watch")

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

        # 只監測 (watch) mode is observe-only: it refreshes the UI status but
        # must never open *or* close player windows. Every window-closing side
        # effect below — the off-topic prune and the close_on_offline sweep —
        # is therefore gated on trigger mode, matching the documented contract
        # that 只監測 "不執行離線關閉". Without this gate, switching to 只監測
        # while a player window was open would still let a later offline edge
        # close the very window the user switched modes to keep watching.
        trigger_enabled = self._monitor_mode == "trigger"

        if latest_status_update is not None:
            statuses, display_names = latest_status_update
            self._apply_display_names(display_names)
            for row in self._channel_rows:
                status = statuses.get(row.key)
                if status is None:
                    if row._status_state in ("live", "offline", "upcoming"):
                        logger.warning(
                            "status_update missing snapshot for %s (row state=%s)",
                            row.key,
                            row._status_state,
                        )
                        continue
                    row.set_status(None)
                else:
                    row.set_status(status)

            # A monitor poll just completed — this is the natural cadence at
            # which to sweep tracked browser windows for "off-topic" drift.
            # We only run when the user opted in, and we tolerate the call
            # being a no-op on non-Windows (the helper returns 0).
            raw_browser_settings = self.config.get("browser_settings") or {}
            if (
                trigger_enabled
                and raw_browser_settings.get("enabled")
                and raw_browser_settings.get("close_off_topic_pages")
                and browser_window_tracking_available(raw_browser_settings)
            ):
                try:
                    closed = prune_off_topic_tracked_windows()
                    if closed:
                        logger.info(
                            "off-topic prune closed %d window(s)", closed
                        )
                except Exception:
                    logger.exception("off-topic prune failed")
            elif (
                trigger_enabled
                and raw_browser_settings.get("close_off_topic_pages")
                and not browser_window_tracking_available(raw_browser_settings)
            ):
                logger.debug(
                    "Skipped off-topic prune: HWND window tracking unavailable "
                    "(need dedicated profile and app mode or separate window)"
                )

        configured_action = self.config.get("action", "open_and_stop")
        browser_settings = self._current_browser_settings()
        should_stop = False
        should_exit = False

        for entry, info in live_events:
            if info.display_name:
                self._apply_display_names({entry.key: info.display_name})
            # Tier-2 snapshot (status_update) is authoritative when present —
            # it carries upcoming_url / vod_url that StreamInfo edges lack.
            # Applying a coarse StreamInfo mapping after the snapshot was
            # overwriting YouTube UPCOMING/OFFLINE rows as LIVE.
            if latest_status_update is None:
                self._apply_live_row_status(entry, info)

            if not trigger_enabled:
                continue

            # Monitor-only channels: keep the LIVE label / status_update flow
            # but suppress every downstream side-effect (toast, browser open,
            # stop/exit-after-trigger). The user explicitly asked us to look
            # but not act on this channel.
            if getattr(entry, "monitor_only", False):
                logger.info(
                    "Skipped action for %s (monitor_only)", entry.key
                )
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

        skip_close_on_offline = (
            self._monitor is not None and self._monitor.wake_verify_active
        )
        if (
            trigger_enabled
            and offline_events
            and browser_settings
            and browser_settings.get("close_on_offline")
            and browser_window_tracking_available(browser_settings)
            and not skip_close_on_offline
        ):
            for entry, offline_info in offline_events:
                # close_on_offline must respect monitor-only too — we never
                # opened a window for this channel, so we shouldn't try to
                # hunt for one to close (which could match an unrelated tab
                # the user opened themselves).
                if getattr(entry, "monitor_only", False):
                    continue
                self._handle_channel_offline(entry, offline_info)

        if should_stop:
            # auto-stop fired by open_and_stop — we just opened a player
            # window the user wants to keep watching, so don't fire the
            # close_on_stop sweep here.
            self._on_stop(is_user_action=False)
        elif should_exit:
            self._quit_app()

        self._maybe_restart_dead_monitor()
        self.after(500, self._poll_events)

    def _handle_channel_offline(
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
        settings = self._current_browser_settings()
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
