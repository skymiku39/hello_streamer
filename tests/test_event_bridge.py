"""Tests for MonitorEventBridge: mode gating, actions, and back-pressure.

These exercise the UI-thread consumer without Tk by driving a recording
``AppEventSink`` stand-in and a lightweight fake row.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
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
from stream_monitor.util import channel_key


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
        self._monitor_generation = 1
        self.wake_verify_active = False
        self.defer_channel_row_repaints = False
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
        self.monitor_cycle_complete_calls = 0
        self._action_event = threading.Event()
        self.platform_services = SimpleNamespace(
            window=SimpleNamespace(
                tracking_available=lambda _settings, _url="": False,
                prune_off_topic=lambda: 0,
                release_keep_awake_for_closed=lambda: 0,
            )
        )

    @property
    def monitor_mode(self) -> str:
        return self._monitor_mode

    @property
    def monitor_generation(self) -> int:
        return self._monitor_generation

    def iter_channel_rows(self) -> list[_FakeRow]:
        return self._channel_rows

    def is_channel_active(self, entry: ChannelEntry) -> bool:
        channels = self.config.get("channels")
        if not isinstance(channels, list):
            # Older lightweight sinks in integrations did not expose the
            # channel list; retain their historical permissive behavior.
            return True
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            if channel_key(channel.get("platform", ""), channel.get("name", "")) == entry.key:
                return bool(channel.get("enabled", True))
        return False

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
        self,
        action: str,
        info: StreamInfo,
        browser_settings: Any,
        generation: int | None = None,
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

    def on_monitor_cycle_complete(self) -> None:
        self.monitor_cycle_complete_calls += 1


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
    assert sink.executed_actions[0][0].key == "open_and_stop"
    # The bridge only dispatches the action.  The action worker schedules the
    # lifecycle transition after the browser launch succeeds.
    assert sink.stop_calls == []
    assert sink.quit_calls == 0


def test_trigger_mode_monitor_only_entry_skips_action() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(ChannelWentLive(entry=_entry(monitor_only=True), info=_live_info()))

    bridge.tick()

    assert sink.executed_actions == []
    assert sink.stop_calls == []


def test_removed_channel_live_event_is_ignored_before_side_effects() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger", action="open_and_stop")
    sink.config["channels"] = [
        {"platform": "twitch", "name": "still-configured", "enabled": True}
    ]
    bridge = MonitorEventBridge(sink, bus)
    removed = _entry("removed")
    bus.publish(ChannelWentLive(entry=removed, info=_live_info("removed")))

    bridge.tick()

    assert sink.live_row_updates == []
    assert sink.executed_actions == []


def test_trigger_mode_notify_entry_dispatches_notification_only() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    entry = _entry(channel_mode="notify")
    bus.publish(ChannelWentLive(entry=entry, info=_live_info()))

    bridge.tick()

    assert len(sink.executed_actions) == 1
    plan = sink.executed_actions[0][0]
    assert plan.key == "notify_only"
    assert plan.notify is True
    assert plan.open_browser is False
    assert sink.monitor_cycle_complete_calls == 0


def test_completed_poll_consumes_global_one_shot_mode() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch_once")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(PollStatusUpdate(statuses={}, display_names={}))

    bridge.tick()

    assert sink.monitor_cycle_complete_calls == 1


def test_trigger_once_dispatches_actions_then_consumes_global_mode() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger_once", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(ChannelWentLive(entry=_entry(), info=_live_info()))
    bus.publish(PollStatusUpdate(statuses={}, display_names={}))

    bridge.tick()

    assert len(sink.executed_actions) == 1
    assert sink.executed_actions[0][0].key == "open_and_stop"
    assert sink.monitor_cycle_complete_calls == 1


def test_one_shot_ignores_events_from_the_cycle_already_in_progress() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger_once", action="open_and_stop")
    bridge = MonitorEventBridge(sink, bus)
    bridge.arm_one_shot(after_cycle=4)

    bus.publish(
        ChannelWentLive(entry=_entry("old"), info=_live_info("old"), cycle_id=4)
    )
    bus.publish(PollStatusUpdate(statuses={}, display_names={}, cycle_id=4))
    bus.publish(
        ChannelWentLive(entry=_entry("new"), info=_live_info("new"), cycle_id=5)
    )
    bus.publish(PollStatusUpdate(statuses={}, display_names={}, cycle_id=5))

    bridge.tick()

    assert [info.channel for _, info, _ in sink.executed_actions] == ["new"]
    assert sink.monitor_cycle_complete_calls == 1


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


def test_defer_channel_row_repaints_skips_row_updates() -> None:
    from stream_monitor.monitor import ChannelStatus

    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    sink.defer_channel_row_repaints = True
    row = _FakeRow("twitch:hello")
    sink._channel_rows = [row]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PartialStatusUpdate(
            statuses={
                "twitch:hello": ChannelStatus(status=True),
            },
            display_names={"twitch:hello": "Hello"},
        )
    )
    bus.publish(PollActivity(entry=_entry(), phase="probe", display_name="Hello"))

    bridge.tick()

    assert row.applied == []
    assert sink.applied_display_names == []
    assert sink.poll_subline_calls == []


def test_deferred_display_names_flush_when_repaints_resume() -> None:
    from stream_monitor.monitor import ChannelStatus

    bus = MonitorEventBus()
    sink = _RecordingSink(mode="watch")
    sink.defer_channel_row_repaints = True
    row = _FakeRow("twitch:hello")
    sink._channel_rows = [row]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        PollStatusUpdate(
            statuses={"twitch:hello": ChannelStatus(status=True)},
            display_names={"twitch:hello": "Hello"},
        )
    )

    bridge.tick()

    assert sink.applied_display_names == []
    assert row.applied == []

    sink.defer_channel_row_repaints = False
    bridge.tick()

    assert sink.applied_display_names == [{"twitch:hello": "Hello"}]
    assert len(row.applied) == 1


def test_poll_complete_releases_keep_awake_when_blank_tab_cleanup_disabled() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger")
    released: list[int] = []
    pruned: list[int] = []
    sink.config["browser_settings"] = {
        "enabled": True,
        "browser_path": "chrome",
        "user_data_dir": "C:/tmp/profile",
        "app_mode": True,
        "close_off_topic_pages": False,
    }
    sink.platform_services = SimpleNamespace(
        window=SimpleNamespace(
            tracking_available=lambda *_a, **_k: True,
            prune_off_topic=lambda: pruned.append(1) or 0,
            release_keep_awake_for_closed=lambda: released.append(1) or 1,
        )
    )
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(PollStatusUpdate(statuses={}, display_names={}))
    bridge.tick()
    assert released == [1]
    assert pruned == []


def test_poll_complete_uses_prune_path_when_blank_tab_cleanup_enabled() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger")
    released: list[int] = []
    pruned: list[int] = []
    sink.config["browser_settings"] = {
        "enabled": True,
        "browser_path": "chrome",
        "user_data_dir": "C:/tmp/profile",
        "app_mode": True,
        "close_off_topic_pages": True,
    }
    sink.platform_services = SimpleNamespace(
        window=SimpleNamespace(
            tracking_available=lambda *_a, **_k: True,
            prune_off_topic=lambda: pruned.append(1) or 1,
            release_keep_awake_for_closed=lambda: released.append(1) or 1,
        )
    )
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(PollStatusUpdate(statuses={}, display_names={}))
    bridge.tick()
    assert pruned == [1]
    # Keep-awake sync is owned by prune when cleanup is enabled.
    assert released == []
