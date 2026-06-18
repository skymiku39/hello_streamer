"""Tests for poll scheduling: platform phases and wake-verify timing."""

from __future__ import annotations

from stream_monitor.domain import ChannelEntry
from stream_monitor.monitor import Monitor
from stream_monitor.monitor.types import (
    _YOUTUBE_MAX_CONCURRENT,
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


def test_tier1_phases_run_youtube_before_twitch(monkeypatch) -> None:
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
        max_concurrent=4,
    )
    monitor._tier1_probe_entries(
        split_platform_entries(monitor._entries)[0],
        max_concurrent=_YOUTUBE_MAX_CONCURRENT,
    )
    monitor._tier1_probe_entries(
        split_platform_entries(monitor._entries)[1],
        max_concurrent=monitor._max_concurrent,
    )
    assert order == ["youtube:yt", "twitch:tw"]
