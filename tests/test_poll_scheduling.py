"""Tests for poll scheduling: platform interleaving and wake-verify timing."""

from __future__ import annotations

from stream_monitor.domain import ChannelEntry
from stream_monitor.monitor import Monitor
from stream_monitor.monitor.types import (
    interleave_platform_entries,
    poll_rest_overshoot_seconds,
)


def _entry(platform: str, name: str) -> ChannelEntry:
    return ChannelEntry(platform=platform, name=name)


def test_interleave_platform_entries_round_robin() -> None:
    entries = [
        _entry("youtube", "y1"),
        _entry("youtube", "y2"),
        _entry("twitch", "t1"),
        _entry("twitch", "t2"),
        _entry("twitch", "t3"),
    ]
    assert interleave_platform_entries(entries) == [
        _entry("youtube", "y1"),
        _entry("twitch", "t1"),
        _entry("youtube", "y2"),
        _entry("twitch", "t2"),
        _entry("twitch", "t3"),
    ]


def test_interleave_preserves_platform_order() -> None:
    entries = [
        _entry("youtube", "a"),
        _entry("youtube", "b"),
        _entry("twitch", "x"),
        _entry("twitch", "y"),
    ]
    merged = interleave_platform_entries(entries)
    assert [e.name for e in merged] == ["a", "x", "b", "y"]


def test_interleave_single_platform_unchanged() -> None:
    twitch_only = [_entry("twitch", "a"), _entry("twitch", "b")]
    assert interleave_platform_entries(twitch_only) == twitch_only


def test_interleave_empty() -> None:
    assert interleave_platform_entries([]) == []


def test_poll_rest_overshoot_zero_on_planned_rest() -> None:
    assert poll_rest_overshoot_seconds(1005.0, 1000.0, 5.0) == 0.0


def test_poll_rest_overshoot_detects_sleep() -> None:
    # ended at 1000, planned rest 5s, next poll at 1030 => 25s overshoot
    assert poll_rest_overshoot_seconds(1030.0, 1000.0, 5.0) == 25.0


def test_poll_rest_overshoot_first_poll() -> None:
    assert poll_rest_overshoot_seconds(1000.0, 0.0, 0.0) == 0.0


def test_should_run_wake_verification_after_sleep() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    monitor._last_poll_wall_ended = 1000.0
    monitor._last_poll_planned_rest = 5.0
    assert monitor._should_run_wake_verification(1030.0) is True


def test_should_not_run_wake_verification_after_slow_poll() -> None:
    """Slow poll + min rest should not look like system sleep."""
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    monitor._last_poll_wall_ended = 1000.0
    monitor._last_poll_planned_rest = 5.0
    assert monitor._should_run_wake_verification(1005.0) is False


def test_should_not_run_wake_verification_on_first_poll() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], interval=10)
    assert monitor._should_run_wake_verification(1000.0) is False
