"""Tests for poll scheduling: YouTube-priority pool and wake-verify timing."""

from __future__ import annotations

from collections import deque

from stream_monitor.domain import ChannelEntry
from stream_monitor.monitor import Monitor
from stream_monitor.monitor.types import (
    poll_rest_overshoot_seconds,
    youtube_priority_entries,
)


def _entry(platform: str, name: str) -> ChannelEntry:
    return ChannelEntry(platform=platform, name=name)


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


def test_youtube_priority_single_platform() -> None:
    twitch_only = [_entry("twitch", "a"), _entry("twitch", "b")]
    assert youtube_priority_entries(twitch_only) == twitch_only


def test_youtube_priority_empty() -> None:
    assert youtube_priority_entries([]) == []


def test_priority_pool_dequeues_youtube_before_twitch() -> None:
    """Simulate the pool pop rule: pending YouTube always wins a free slot."""
    youtube_q = deque([_entry("youtube", "y1"), _entry("youtube", "y2")])
    other_q = deque([_entry("twitch", "t1")])

    def pop_next() -> ChannelEntry | None:
        if youtube_q:
            return youtube_q.popleft()
        if other_q:
            return other_q.popleft()
        return None

    assert pop_next().name == "y1"
    assert pop_next().name == "y2"
    assert pop_next().name == "t1"
    assert pop_next() is None


def test_poll_rest_overshoot_zero_on_planned_rest() -> None:
    assert poll_rest_overshoot_seconds(1005.0, 1000.0, 5.0) == 0.0


def test_poll_rest_overshoot_detects_sleep() -> None:
    assert poll_rest_overshoot_seconds(1030.0, 1000.0, 5.0) == 25.0


def test_poll_rest_overshoot_first_poll() -> None:
    assert poll_rest_overshoot_seconds(1000.0, 0.0, 0.0) == 0.0


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


def test_should_not_run_wake_verification_on_first_poll() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    assert monitor._should_run_wake_verification(1000.0) is False
