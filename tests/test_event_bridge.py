"""Tests for MonitorEventBridge: mode gating, actions, and back-pressure.

These exercise the UI-thread consumer without Tk by driving a recording
``AppEventSink`` stand-in and a lightweight fake row.
"""

from __future__ import annotations

import threading
from typing import Any

from stream_monitor.event_bridge import MonitorEventBridge
from stream_monitor.events import (
    ChannelWentLive,
    MonitorEventBus,
    PartialStatusUpdate,
    PollActivity,
    PollStatusUpdate,
)
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry


class _FakeRow:
    def __init__(self, key: str) -> None:
        self.key = key
        self._status_state: str | None = None
        self._ended_at_source: str = ""
        self.applied: list[Any] = []

    def set_status(self, status: Any) -> None:
        self.applied.append(status)


class _RecordingSink:
    """Records the side-effects MonitorEventBridge requests on the main window."""

    def __init__(self, mode: str = "trigger", action: str = "open_and_stop") -> None:
        self._monitor_mode = mode
        self.wake_verify_active = False
        self._channel_rows: list[_FakeRow] = []
        self.config: dict[str, Any] = {"action": action}

        self.poll_waiting = 0
        self.applied_display_names: list[dict[str, str]] = []
        self.poll_subline_calls: list[tuple[Any, str, str]] = []
        self.live_row_updates: list[tuple[ChannelEntry, StreamInfo]] = []
        self.executed_actions: list[tuple[str, StreamInfo, Any]] = []
        self.offline_calls: list[tuple[ChannelEntry, Any]] = []
        self.stop_calls: list[bool] = []
        self.quit_calls = 0
        self.restart_calls = 0
        self.save_status_cache_calls = 0
        self._action_event = threading.Event()

    @property
    def monitor_mode(self) -> str:
        return self._monitor_mode

    def iter_channel_rows(self) -> list[_FakeRow]:
        return self._channel_rows

    def set_poll_waiting(self) -> None:
        self.poll_waiting += 1

    def apply_display_names(self, display_names: dict[str, str]) -> None:
        self.applied_display_names.append(dict(display_names))

    def update_poll_subline(
        self, entry: ChannelEntry, phase: str, display_name: str = ""
    ) -> None:
        self.poll_subline_calls.append((entry, phase, display_name))

    def current_browser_settings(self) -> Any:
        return None

    def apply_live_row_status(self, entry: ChannelEntry, info: StreamInfo) -> None:
        self.live_row_updates.append((entry, info))

    def execute_live_action(
        self, action: str, info: StreamInfo, browser_settings: Any
    ) -> None:
        self.executed_actions.append((action, info, browser_settings))
        self._action_event.set()

    def handle_channel_offline(self, entry: ChannelEntry, offline_info: Any) -> None:
        self.offline_calls.append((entry, offline_info))

    def on_stop(self, *, is_user_action: bool = True) -> None:
        self.stop_calls.append(is_user_action)

    def quit_app(self) -> None:
        self.quit_calls += 1

    def maybe_restart_dead_monitor(self) -> None:
        self.restart_calls += 1

    def save_status_cache(self) -> None:
        self.save_status_cache_calls += 1


def _live_info(channel: str = "hello") -> StreamInfo:
    return StreamInfo(
        channel=channel,
        platform="twitch",
        is_live=True,
        title="Live",
        url=f"https://www.twitch.tv/{channel}",
    )


def _entry(name: str = "hello", **kwargs: Any) -> ChannelEntry:
    return ChannelEntry(platform="twitch", name=name, **kwargs)


def test_idle_mode_clears_bus_without_side_effects() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="idle")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(ChannelWentLive(entry=_entry(), info=_live_info()))

    bridge.tick()

    assert bus.drain() == []
    assert sink.executed_actions == []
    assert sink.live_row_updates == []
    assert sink.restart_calls == 0


def test_watch_mode_updates_row_but_skips_action() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    bridge = MonitorEventBridge(sink, bus)
    entry, info = _entry(), _live_info()
    bus.publish(ChannelWentLive(entry=entry, info=info))

    bridge.tick()

    assert sink.live_row_updates == [(entry, info)]
    assert sink.executed_actions == []
    assert sink.stop_calls == []
    assert sink.restart_calls == 1


def test_trigger_mode_open_and_stop_runs_action_and_stops() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(ChannelWentLive(entry=_entry(), info=_live_info()))

    bridge.tick()

    assert sink._action_event.wait(timeout=2.0)
    assert len(sink.executed_actions) == 1
    assert sink.executed_actions[0][0] == "open_and_stop"
    assert sink.stop_calls == [False]
    assert sink.quit_calls == 0


def test_trigger_mode_monitor_only_entry_skips_action() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(ChannelWentLive(entry=_entry(monitor_only=True), info=_live_info()))

    bridge.tick()

    assert sink.executed_actions == []
    assert sink.stop_calls == []


def test_back_pressure_requeues_events_beyond_tick_budget() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    bridge = MonitorEventBridge(sink, bus)
    for i in range(15):
        bus.publish(ChannelWentLive(entry=_entry(f"c{i}"), info=_live_info(f"c{i}")))

    bridge.tick()

    assert len(sink.live_row_updates) == 12
    assert len(bus.drain()) == 3


def test_poll_activity_coalesced_to_latest_only() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(PollActivity(entry=_entry("a"), phase="probe", display_name="A"))
    bus.publish(PollActivity(entry=_entry("b"), phase="refresh", display_name="B"))

    bridge.tick()

    assert len(sink.poll_subline_calls) == 1
    assert sink.poll_subline_calls[0][1] == "refresh"


def test_partial_status_update_flushes_pending_to_rows() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    row = _FakeRow("twitch:hello")
    sink._channel_rows = [row]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PartialStatusUpdate(
            statuses={"twitch:hello": True},
            display_names={"twitch:hello": "Hello"},
        )
    )

    bridge.tick()

    assert row.applied == [True]
    assert sink.applied_display_names == [{"twitch:hello": "Hello"}]


def test_poll_status_update_clears_stale_painted_rows() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    row = _FakeRow("twitch:gone")
    row._status_state = "live"
    sink._channel_rows = [row]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(PollStatusUpdate(statuses={}, display_names={}))

    bridge.tick()

    assert row.applied == [None]


def test_pending_status_flush_capped_per_tick() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    rows = [_FakeRow(f"twitch:c{i}") for i in range(5)]
    sink._channel_rows = rows
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PartialStatusUpdate(
            statuses={f"twitch:c{i}": True for i in range(5)},
            display_names={},
        )
    )

    bridge.tick()

    painted = sum(1 for row in rows if row.applied)
    assert painted == 3

    bridge.tick()
    painted = sum(1 for row in rows if row.applied)
    assert painted == 5


def test_reset_drops_buffered_pending_status() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    rows = [_FakeRow(f"twitch:c{i}") for i in range(5)]
    sink._channel_rows = rows
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PartialStatusUpdate(
            statuses={f"twitch:c{i}": True for i in range(5)},
            display_names={},
        )
    )

    bridge.tick()
    bridge.reset()
    bridge.tick()

    painted = sum(1 for row in rows if row.applied)
    assert painted == 3


def test_poll_complete_refreshes_status_cache() -> None:
    from stream_monitor.monitor import ChannelStatus

    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    sink._channel_rows = [_FakeRow("twitch:hello")]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PollStatusUpdate(
            statuses={
                "twitch:hello": ChannelStatus(
                    status=True, started_at="2026-01-01T00:00:00+00:00"
                )
            },
            display_names={"twitch:hello": "Hello"},
        )
    )

    bridge.tick()

    assert sink.save_status_cache_calls == 1
