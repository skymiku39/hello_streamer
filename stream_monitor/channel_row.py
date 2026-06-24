"""Single channel row widget for the main channel list."""

from __future__ import annotations

import logging
from typing import Any, Callable

import customtkinter as ctk

from stream_monitor import i18n
from stream_monitor.app_ui import (
    _CLR_CARD,
    _CLR_CARD_DISABLED,
    _CLR_DELETE_HOVER,
    _CLR_LINK_HOVER,
    _CLR_TEXT_DISABLED,
    _CLR_TWITCH,
    _CLR_YOUTUBE,
    _font,
    _format_countdown,
    _format_elapsed,
    _format_row_time,
    _status_row_label_width,
    _tooltip,
    _tooltip_tr,
)
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelStatus
from stream_monitor.notifier import open_url
from stream_monitor.channel_reorder import LONG_PRESS_MS
from stream_monitor.util import channel_key, channel_page_url

logger = logging.getLogger(__name__)


def is_live_state(state: bool | str | None) -> bool:
    return state is True or state == "live"


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
        on_reorder_begin: Callable[[int], None] | None = None,
        on_reorder_motion: Callable[[int], None] | None = None,
        on_reorder_release: Callable[[], None] | None = None,
        get_browser_settings: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        super().__init__(parent, corner_radius=10, fg_color=_CLR_CARD, height=58)
        self.channel = channel
        self._on_toggle_enabled = on_toggle_enabled
        self._on_reorder_begin = on_reorder_begin
        self._on_reorder_motion = on_reorder_motion
        self._on_reorder_release = on_reorder_release
        self._drag_long_press_id: str | None = None
        self._drag_active = False
        self._drag_press_y = 0
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
        # True while a row shows a status restored from the previous session
        # that the running monitor has not re-confirmed yet (see status_cache).
        self._verification_pending: bool = False

        color = _CLR_TWITCH if channel["platform"] == "twitch" else _CLR_YOUTUBE
        self._platform_color = color

        move_frame = ctk.CTkFrame(self, fg_color="transparent", width=30, height=46)
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

        self.drag_handle = ctk.CTkButton(
            move_frame,
            text="⠿",
            width=30,
            height=6,
            corner_radius=2,
            fg_color="transparent",
            hover_color="#243052",
            font=_font(8),
        )
        self.drag_handle.pack()
        _tooltip_tr(self.drag_handle, "tooltip.row.drag")
        self.drag_handle.bind("<Button-1>", self._on_drag_handle_press)
        self.drag_handle.bind("<B1-Motion>", self._on_drag_handle_motion)
        self.drag_handle.bind("<ButtonRelease-1>", self._on_drag_handle_release)

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

    def set_status(
        self,
        status: bool | str | ChannelStatus | None,
        *,
        pending: bool = False,
    ) -> None:
        if not self.channel.get("enabled", True):
            return

        # A real monitor update (pending=False) always clears the
        # "restored, not yet re-checked" marker for this row.
        self._verification_pending = pending

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
        elif is_live_state(state):
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
            self._ended_at_source = (detail.ended_at_source if detail else "") or ""
            sched = (detail.scheduled_start if detail else "") or ""
            self._status_scheduled_start = sched
            self._status_countdown = (
                _format_countdown(sched) if self._upcoming_url and sched else ""
            )
            self._status_timestamp = detail.ended_at if detail else ""
            self._status_elapsed = _format_elapsed(self._status_timestamp)

        self._render_status_visuals()

    def status_snapshot(self) -> ChannelStatus | None:
        """Rebuild the row's current status as a ChannelStatus for persistence.

        Returns ``None`` when there is nothing worth saving (blank/idle row).
        """
        state = self._status_state
        if state == "live":
            return ChannelStatus(
                status=True,
                url=self._active_url,
                title=self._status_title,
                started_at=self._status_timestamp,
            )
        if state == "upcoming":
            return ChannelStatus(
                status="upcoming",
                url=self._active_url,
                title=self._status_title,
                scheduled_start=self._status_timestamp,
            )
        if state == "offline":
            return ChannelStatus(
                status=False,
                url=self._active_url,
                title=self._status_title,
                ended_at=self._status_timestamp,
                vod_url=self._vod_url,
                upcoming_url=self._upcoming_url,
                scheduled_start=self._status_scheduled_start,
                ended_at_source=self._ended_at_source,
            )
        return None

    def _compose_time_label_text(self) -> str:
        """Build i18n time label from cached status timestamps."""
        state = self._status_state
        result = ""
        if state == "live":
            self._status_elapsed = _format_elapsed(self._status_timestamp)
            result = _format_row_time("live", self._status_elapsed)
        elif state == "upcoming":
            self._status_countdown = _format_countdown(self._status_timestamp)
            result = _format_row_time("upcoming", self._status_countdown)
        elif state == "offline":
            if self._ended_at_source == "pending":
                result = tr("status.row.time.pending_detail")
            elif self._upcoming_url and self._status_scheduled_start:
                self._status_countdown = _format_countdown(
                    self._status_scheduled_start
                )
                if self._status_countdown:
                    result = _format_row_time("countdown", self._status_countdown)
            if not result:
                self._status_elapsed = _format_elapsed(self._status_timestamp)
                result = _format_row_time(
                    "offline",
                    self._status_elapsed,
                    ended_at_source=self._ended_at_source,
                )
        if self._verification_pending and state in ("live", "offline", "upcoming"):
            suffix = tr("status.row.pending_suffix")
            result = f"{result} {suffix}".strip() if result else suffix
        return result

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
        if self._verification_pending:
            parts.append(tr("tooltip.row.status.pending"))
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

    def set_reorder_highlight(self, active: bool) -> None:
        if active:
            self.configure(border_width=2, border_color="#2196F3")
        else:
            self.configure(border_width=0)

    def _cancel_drag_long_press(self) -> None:
        if self._drag_long_press_id is not None:
            self.after_cancel(self._drag_long_press_id)
            self._drag_long_press_id = None

    def cancel_reorder_drag(self) -> None:
        """Reset local drag-handle state (e.g. when release happens off the handle)."""
        self._cancel_drag_long_press()
        self._drag_active = False

    def _on_drag_handle_press(self, event: Any) -> None:
        self._drag_press_y = event.y_root
        self._cancel_drag_long_press()
        self._drag_long_press_id = self.after(
            LONG_PRESS_MS,
            lambda y=event.y_root: self._arm_drag(y),
        )

    def _arm_drag(self, y_root: int) -> None:
        self._drag_long_press_id = None
        self._drag_active = True
        if self._on_reorder_begin is not None:
            self._on_reorder_begin(y_root)

    def _on_drag_handle_motion(self, event: Any) -> None:
        if not self._drag_active:
            if abs(event.y_root - self._drag_press_y) > 20:
                self._cancel_drag_long_press()
            return
        if self._on_reorder_motion is not None:
            self._on_reorder_motion(event.y_root)

    def _on_drag_handle_release(self, _event: Any) -> None:
        was_active = self._drag_active
        self.cancel_reorder_drag()
        if was_active and self._on_reorder_release is not None:
            self._on_reorder_release()


# ═══════════════════════════════════════════════════════════════════════════
# Main App Window
# ═══════════════════════════════════════════════════════════════════════════
