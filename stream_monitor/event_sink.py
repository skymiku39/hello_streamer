"""Public interface consumed by ``MonitorEventBridge`` (ISP).

The bridge depends only on this narrow, public contract. It never touches the
main window's private attributes, which keeps the UI free to restructure its
internals without breaking the monitor-to-UI event path.
"""

from __future__ import annotations

from typing import Any, Protocol

from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.domain import ChannelEntry
from stream_monitor.fetcher.base import StreamInfo


class ChannelRowView(Protocol):
    """Subset of a channel row the event bridge reads and paints."""

    key: str
    _status_state: str | None
    _ended_at_source: str

    def set_status(self, status: Any) -> None: ...


class AppEventSink(Protocol):
    """UI-facing operations the event bridge may invoke on the main window."""

    config: dict[str, Any]

    @property
    def monitor_mode(self) -> str: ...

    @property
    def wake_verify_active(self) -> bool: ...

    def iter_channel_rows(self) -> list[ChannelRowView]: ...

    def set_poll_waiting(self) -> None: ...

    def apply_display_names(self, display_names: dict[str, str]) -> None: ...

    def update_poll_subline(
        self, entry: ChannelEntry, phase: str, display_name: str = ""
    ) -> None: ...

    def current_browser_settings(self) -> BrowserSettings | None: ...

    def apply_live_row_status(
        self, entry: ChannelEntry, info: StreamInfo
    ) -> None: ...

    def execute_live_action(
        self,
        action: str,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
    ) -> None: ...

    def handle_channel_offline(
        self, entry: ChannelEntry, offline_info: Any
    ) -> None: ...

    def on_stop(self, *, is_user_action: bool = True) -> None: ...

    def quit_app(self) -> None: ...

    def maybe_restart_dead_monitor(self) -> None: ...
