"""Narrow interface consumed by ``MonitorEventBridge`` (ISP)."""

from __future__ import annotations

from typing import Any, Protocol

from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, Monitor


class AppEventSink(Protocol):
    """UI-facing operations the event bridge may invoke on the main window."""

    _monitor_mode: str
    _ui_status_pending: dict[str, Any]
    _channel_rows: list[Any]
    _monitor: Monitor | None
    config: dict[str, Any]

    def _set_poll_subline_waiting(self) -> None: ...

    def _apply_display_names(self, display_names: dict[str, str]) -> None: ...

    def _update_poll_subline(
        self, entry: ChannelEntry, phase: str, display_name: str = ""
    ) -> None: ...

    def _current_browser_settings(self) -> BrowserSettings | None: ...

    def _apply_live_row_status(
        self, entry: ChannelEntry, info: StreamInfo
    ) -> None: ...

    def _execute_live_action(
        self,
        action: str,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
    ) -> None: ...

    def _handle_channel_offline(
        self, entry: ChannelEntry, offline_info: Any
    ) -> None: ...

    def _on_stop(self, *, is_user_action: bool = True) -> None: ...

    def _quit_app(self) -> None: ...

    def _maybe_restart_dead_monitor(self) -> None: ...
