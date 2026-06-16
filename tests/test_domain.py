"""Smoke tests for the neutral domain value objects."""

from __future__ import annotations

from stream_monitor.domain import ChannelEntry, ChannelStatus, OfflineInfo


def test_channel_entry_normalizes_and_keys() -> None:
    entry = ChannelEntry(platform="twitch", name="Hello_Streamer")
    assert entry.name == "hello_streamer"
    assert entry.key == "twitch:hello_streamer"
    assert entry.enabled is True
    assert entry.monitor_only is False


def test_channel_status_equality_compares_status() -> None:
    assert ChannelStatus(status=True) == True  # noqa: E712
    assert ChannelStatus(status="upcoming") == "upcoming"
    assert ChannelStatus(status=False) == False  # noqa: E712


def test_offline_info_defaults() -> None:
    info = OfflineInfo(url="u", title="t", platform="twitch", name="n")
    assert info.video_id == ""
    assert info.display_name == ""


def test_monitor_and_events_reexport_same_domain_types() -> None:
    from stream_monitor.events.types import ChannelWentLive
    from stream_monitor.monitor import ChannelEntry as MonitorChannelEntry
    from stream_monitor.monitor.types import OfflineInfo as MonitorOfflineInfo

    assert MonitorChannelEntry is ChannelEntry
    assert MonitorOfflineInfo is OfflineInfo
    assert ChannelWentLive.__annotations__["entry"] in ("ChannelEntry", ChannelEntry)
