"""Tests for poll scheduling: priority pool and wake-verify timing."""

from __future__ import annotations

import threading
import time

from stream_monitor.domain import ChannelEntry
from stream_monitor.monitor import Monitor
from stream_monitor.monitor.types import (
    _YOUTUBE_MAX_CONCURRENT,
    interleave_platform_entries,
    poll_rest_overshoot_seconds,
    split_platform_entries,
    youtube_priority_entries,
)


def _entry(platform: str, name: str) -> ChannelEntry:
    return ChannelEntry(platform=platform, name=name)


def test_split_platform_entries() -> None:
    entries = [
        _entry("twitch", "t1"),
        _entry("youtube", "y1"),
        _entry("twitch", "t2"),
        _entry("youtube", "y2"),
    ]
    youtube, twitch = split_platform_entries(entries)
    assert [e.name for e in youtube] == ["y1", "y2"]
    assert [e.name for e in twitch] == ["t1", "t2"]


def test_youtube_priority_entries_order() -> None:
    entries = [
        _entry("twitch", "t1"),
        _entry("youtube", "y1"),
        _entry("twitch", "t2"),
        _entry("youtube", "y2"),
    ]
    assert youtube_priority_entries(entries) == [
        _entry("youtube", "y1"),
        _entry("youtube", "y2"),
        _entry("twitch", "t1"),
        _entry("twitch", "t2"),
    ]


def test_youtube_max_concurrent_is_serial() -> None:
    assert _YOUTUBE_MAX_CONCURRENT == 1


def test_poll_rest_overshoot_zero_on_planned_rest() -> None:
    assert poll_rest_overshoot_seconds(1005.0, 1000.0, 5.0) == 0.0


def test_poll_rest_overshoot_detects_sleep() -> None:
    assert poll_rest_overshoot_seconds(1030.0, 1000.0, 5.0) == 25.0


def test_should_run_wake_verification_after_sleep() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    monitor._last_poll_wall_ended = 1000.0
    monitor._last_poll_planned_rest = 5.0
    assert monitor._should_run_wake_verification(1030.0) is True


def test_should_not_run_wake_verification_after_slow_poll() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    monitor._last_poll_wall_ended = 1000.0
    monitor._last_poll_planned_rest = 5.0
    assert monitor._should_run_wake_verification(1005.0) is False


def test_interleave_platform_entries_round_robin() -> None:
    entries = [
        _entry("youtube", "y1"),
        _entry("youtube", "y2"),
        _entry("twitch", "t1"),
        _entry("twitch", "t2"),
        _entry("twitch", "t3"),
        _entry("youtube", "y3"),
        _entry("youtube", "y4"),
    ]
    assert [e.name for e in interleave_platform_entries(entries)] == [
        "y1",
        "t1",
        "y2",
        "t2",
        "y3",
        "t3",
        "y4",
    ]


def test_interleave_preserves_secondary_order_within_platform() -> None:
    entries = [
        _entry("twitch", "t1"),
        _entry("twitch", "t2"),
        _entry("youtube", "y1"),
        _entry("youtube", "y2"),
    ]
    assert [e.name for e in interleave_platform_entries(entries)] == [
        "y1",
        "t1",
        "y2",
        "t2",
    ]


def test_priority_pool_serial_interleaves_platforms(monkeypatch) -> None:
    order: list[str] = []

    def fake_probe(entry: ChannelEntry) -> list:
        order.append(entry.key)
        return []

    monkeypatch.setattr(
        "stream_monitor.monitor.poll_cycle.PollCycleMixin._probe_live",
        lambda _self, entry: fake_probe(entry),
    )
    monitor = Monitor(
        channels=[
            {"platform": "twitch", "name": "tw"},
            {"platform": "youtube", "name": "yt"},
        ],
        max_concurrent=1,
    )
    monitor._tier1_probe_entries(list(monitor._entries))
    assert order == ["youtube:yt", "twitch:tw"]


def test_priority_pool_interleaves_when_youtube_is_slow() -> None:
    channels = [
        {"platform": "twitch", "name": "tw1"},
        {"platform": "youtube", "name": "yt1"},
        {"platform": "twitch", "name": "tw2"},
    ]
    monitor = Monitor(channels=channels, max_concurrent=4)
    started: list[str] = []
    start_lock = threading.Lock()
    yt_started = threading.Event()
    yt_release = threading.Event()

    def work(entry: ChannelEntry) -> None:
        with start_lock:
            started.append(entry.key)
        if entry.platform == "youtube":
            yt_started.set()
            assert yt_release.wait(timeout=2.0)

    entries = list(monitor._entries)
    thread = threading.Thread(
        target=monitor._run_priority_pool,
        args=(entries, work),
        daemon=True,
    )
    thread.start()
    assert yt_started.wait(timeout=2.0)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        with start_lock:
            if len(started) >= 3:
                break
        time.sleep(0.01)
    with start_lock:
        assert "twitch:tw1" in started
        assert "youtube:yt1" in started
        assert "twitch:tw2" in started
    yt_release.set()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


def test_priority_pool_runs_twitch_parallel_with_youtube() -> None:
    channels = [
        {"platform": "twitch", "name": "tw0"},
        {"platform": "youtube", "name": "yt"},
        {"platform": "twitch", "name": "tw1"},
        {"platform": "twitch", "name": "tw2"},
    ]
    monitor = Monitor(channels=channels, max_concurrent=4)
    active = 0
    peak = 0
    lock = threading.Lock()
    hold = 0.08

    def work(entry: ChannelEntry) -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(hold)
        with lock:
            active -= 1

    entries = [
        _entry("twitch", "tw1"),
        _entry("youtube", "yt1"),
        _entry("twitch", "tw2"),
    ]
    monitor._run_priority_pool(entries, work)
    assert peak >= 2
