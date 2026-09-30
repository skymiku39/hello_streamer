"""Regression tests for wake verification and one-shot cycle boundaries."""

from __future__ import annotations

import time

from stream_monitor.db import SeenVideoDB
from stream_monitor.events import (
    ChannelWentLive,
    MonitorEventBus,
    PollStatusUpdate,
    PollWaiting,
)
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.monitor import deps as monitor_deps


class _LiveTwitchFetcher:
    platform = "twitch"

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=True,
            title="Live now",
            url=f"https://www.twitch.tv/{channel_name}",
        )

    def get_latest_finished_vod(self, channel_name: str, *, items=None):
        return None


def test_wake_offline_to_live_dispatches_live_edge(monkeypatch, tmp_path) -> None:
    fetcher = _LiveTwitchFetcher()
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    event_bus = MonitorEventBus()
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        db=db,
        event_bus=event_bus,
    )
    entry = ChannelEntry(platform="twitch", name="hello")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=False,
            url="https://www.twitch.tv/hello",
        )
    monkeypatch.setattr(monitor, "_probe_channel_status", lambda _e: "live")

    monitor._run_wake_verification([entry], time.monotonic())

    events = event_bus.drain()
    assert any(isinstance(event, ChannelWentLive) for event in events)
    db.close()


def test_wake_cycle_emits_one_completion_after_startup_refresh(
    monkeypatch, tmp_path
) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    event_bus = MonitorEventBus()
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        db=db,
        event_bus=event_bus,
    )
    entry = ChannelEntry(platform="twitch", name="hello")
    info = StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live now",
        url="https://www.twitch.tv/hello",
    )

    monkeypatch.setattr(monitor, "_should_run_wake_verification", lambda _now: True)
    monkeypatch.setattr(
        monitor,
        "_run_wake_verification",
        lambda _entries, _started: 0.1,
    )

    def startup_refresh(entries, _started, elapsed):
        assert entries[0].key == entry.key
        monitor._emit_went_live(entry, info)
        return elapsed + 0.2

    monkeypatch.setattr(monitor, "_maybe_run_startup_refresh", startup_refresh)
    monkeypatch.setattr(monitor, "_run_maintenance", lambda: None)

    monitor._execute_poll_cycle(time.monotonic())

    events = event_bus.drain()
    assert [type(event) for event in events] == [
        ChannelWentLive,
        PollWaiting,
        PollStatusUpdate,
    ]
    db.close()
