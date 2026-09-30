"""Regression tests for wake verification and one-shot cycle boundaries."""

from __future__ import annotations

import time

from stream_monitor.db import SeenVideoDB
from stream_monitor.events import (
    ChannelWentLive,
    ChannelWentOffline,
    MonitorEventBus,
    PollStatusUpdate,
    PollWaiting,
)
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.monitor import deps as monitor_deps
from stream_monitor.monitor.types import OfflineInfo, _live_cache_key


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


def test_pending_offline_survives_startup_refresh_then_dispatches_once(
    monkeypatch, tmp_path
) -> None:
    """Offline edges queued before startup refresh must not be cleared away."""
    db = SeenVideoDB(tmp_path / "test.db")
    event_bus = MonitorEventBus()
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello", "enabled": True}],
        db=db,
        event_bus=event_bus,
        interval=60,
    )
    entry = ChannelEntry(platform="twitch", name="hello")
    payload = OfflineInfo(
        url="https://www.twitch.tv/hello",
        title="Was live",
        platform="twitch",
        name="hello",
    )

    monitor._startup_refresh_pending = True
    monkeypatch.setattr(monitor, "_should_run_wake_verification", lambda _now: False)
    monkeypatch.setattr(monitor, "_tier1_probe_entries", lambda _entries: 0)
    monkeypatch.setattr(monitor, "_run_maintenance", lambda: None)

    def fake_pool(entries, work_fn, pool_tag=""):
        if pool_tag == "tier2":
            with monitor._lock:
                monitor._pending_offline_events.append((entry, payload))
            return []
        if pool_tag == "startup_refresh":
            with monitor._lock:
                assert len(monitor._pending_offline_events) == 1
            return [0]
        return []

    monkeypatch.setattr(monitor, "_run_priority_pool", fake_pool)

    monitor._execute_poll_cycle(time.monotonic())

    events = event_bus.drain()
    offline = [event for event in events if isinstance(event, ChannelWentOffline)]
    assert len(offline) == 1
    assert offline[0].offline_info.url == payload.url
    assert sum(1 for event in events if isinstance(event, PollStatusUpdate)) == 1
    with monitor._lock:
        assert monitor._pending_offline_events == []
    db.close()


def test_wake_defers_secondary_youtube_without_overwrite(
    monkeypatch, tmp_path
) -> None:
    """Deferred secondary YouTube must keep cached LIVE; wake primary stays."""
    primary_live = VideoItem(
        video_id="pri1",
        title="Primary",
        style="LIVE",
        url="https://www.youtube.com/watch?v=pri1",
    )

    class _YtFetcher:
        platform = "youtube"

        def get_channel_items(self, channel_name, *, fill_timing=True, timeout=None):
            if channel_name == "primary":
                return [primary_live]
            # Secondary still reports empty/offline while cache says LIVE.
            return []

        def get_stream_info(self, channel_name):
            return None

        def http_backoff_active(self):
            return False

        def enrich_live_for_details(self, items):
            return None

        def enrich_upcoming_for_details(self, items):
            return None

        def get_latest_finished_vod(self, channel_name, *, items=None):
            return None

    fetcher = _YtFetcher()
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[
            {"platform": "youtube", "name": "primary", "enabled": True},
            {"platform": "youtube", "name": "secondary", "enabled": True},
        ],
        db=db,
    )
    primary = ChannelEntry(platform="youtube", name="primary")
    secondary = ChannelEntry(platform="youtube", name="secondary")
    secondary_status = ChannelStatus(
        status=True,
        url="https://www.youtube.com/watch?v=sec1",
        title="Secondary stale live",
    )
    with monitor._lock:
        monitor._last_status[primary.key] = ChannelStatus(
            status=False,
            url="https://www.youtube.com/@primary",
        )
        monitor._last_status[secondary.key] = secondary_status
        # Stale secondary live payload on primary channel (multi-live residue).
        monitor._live_payload[_live_cache_key(primary.key, "sec_old")] = OfflineInfo(
            url="https://www.youtube.com/watch?v=sec_old",
            title="Old secondary",
            platform="youtube",
            name="primary",
            video_id="sec_old",
        )

    def probe_status(entry: ChannelEntry) -> str | None:
        if entry.name == "primary":
            return "live"
        return "offline"

    monkeypatch.setattr(monitor, "_probe_channel_status", probe_status)

    monitor._run_wake_verification([primary, secondary], time.monotonic())

    with monitor._lock:
        # Secondary deferred: cached LIVE must remain (no overwrite).
        assert monitor._last_status[secondary.key] == secondary_status
        # Stale secondary payload on primary must not be popped during wake.
        assert _live_cache_key(primary.key, "sec_old") in monitor._live_payload
        assert monitor._pending_offline_events == []
    assert "secondary" in monitor._wake_deferred_keys or (
        "youtube:secondary" in monitor._wake_deferred_keys
    )
    assert "youtube:primary" not in monitor._wake_deferred_keys
    db.close()


def test_wake_cycle_skips_deferred_keys_in_startup_refresh(
    monkeypatch, tmp_path
) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[
            {"platform": "twitch", "name": "ok", "enabled": True},
            {"platform": "youtube", "name": "deferred", "enabled": True},
        ],
        db=db,
    )
    seen: list[str] = []

    monkeypatch.setattr(monitor, "_should_run_wake_verification", lambda _now: True)

    def wake(entries, _started):
        monitor._wake_deferred_keys = {"youtube:deferred"}
        return 0.1

    def startup(entries, _started, elapsed):
        seen.extend(entry.key for entry in entries)
        return elapsed

    monkeypatch.setattr(monitor, "_run_wake_verification", wake)
    monkeypatch.setattr(monitor, "_maybe_run_startup_refresh", startup)
    monkeypatch.setattr(monitor, "_run_maintenance", lambda: None)

    monitor._execute_poll_cycle(time.monotonic())

    assert seen == ["twitch:ok"]
    db.close()
