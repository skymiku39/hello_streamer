"""Smoke tests: imports, pub/sub wiring, and monitor round-trip."""

from __future__ import annotations

import time

from stream_monitor import __version__
from stream_monitor.event_bridge import MonitorEventBridge
from stream_monitor.events import (
    ChannelWentLive,
    MonitorEventBus,
    PollStatusUpdate,
)
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, Monitor
from stream_monitor.monitor import deps as monitor_deps


class _FakeSink:
    """Minimal ``AppEventSink`` stand-in for bridge smoke tests."""

    _monitor_mode = "watch"
    _ui_status_pending: dict = {}
    _channel_rows: list = []
    _monitor = None
    config: dict = {"action": "notify_only"}

    def _set_poll_subline_waiting(self) -> None:
        pass

    def _apply_display_names(self, display_names: dict[str, str]) -> None:
        pass

    def _update_poll_subline(
        self, entry: ChannelEntry, phase: str, display_name: str = ""
    ) -> None:
        pass

    def _current_browser_settings(self) -> dict | None:
        return None

    def _apply_live_row_status(self, entry: ChannelEntry, info: StreamInfo) -> None:
        pass

    def _execute_live_action(
        self,
        action: str,
        info: StreamInfo,
        browser_settings: dict | None,
    ) -> None:
        pass

    def _handle_channel_offline(self, entry: ChannelEntry, offline_info: object) -> None:
        pass

    def _on_stop(self, *, is_user_action: bool = True) -> None:
        pass

    def _quit_app(self) -> None:
        pass

    def _maybe_restart_dead_monitor(self) -> None:
        pass


def test_package_imports() -> None:
    from stream_monitor.app import App, main  # noqa: F401
    from stream_monitor.events import MonitorEventBus  # noqa: F401

    assert __version__


def test_event_bridge_drains_bus_without_error() -> None:
    bus = MonitorEventBus()
    sink = _FakeSink()
    bridge = MonitorEventBridge(sink, bus)
    entry = ChannelEntry(platform="twitch", name="hello")
    info = StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live",
        url="https://www.twitch.tv/hello",
    )
    bus.publish(ChannelWentLive(entry=entry, info=info))
    bridge.tick()


def test_monitor_publish_subscribe_round_trip(monkeypatch) -> None:
    class LiveFetcher:
        platform = "twitch"

        def get_stream_info(self, channel_name: str) -> StreamInfo:
            return StreamInfo(
                channel=channel_name,
                platform="twitch",
                is_live=True,
                title="Smoke Live",
                url=f"https://www.twitch.tv/{channel_name}",
            )

    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: LiveFetcher())
    bus = MonitorEventBus()
    seen: list[str] = []
    bus.subscribe(
        lambda event: seen.append(event.entry.name)
        if isinstance(event, ChannelWentLive)
        else None
    )
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a"}],
        event_bus=bus,
        max_concurrent=1,
    )
    monitor._execute_poll_cycle(time.monotonic())
    assert "a" in seen
    assert any(isinstance(e, PollStatusUpdate) for e in bus.drain())
