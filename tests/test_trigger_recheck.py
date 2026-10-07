"""Switching from observation to actions requires new live evidence."""

from types import SimpleNamespace

import pytest

from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import ChannelStatus, Monitor, deps


@pytest.mark.parametrize("platform", ["twitch", "youtube", "youtube_fallback"])
def test_recheck_emits_known_live_once_without_clearing_history(monkeypatch, platform):
    actual_platform = platform.split("_")[0]
    monitor = Monitor([{"platform": actual_platform, "name": "hello"}])
    entry = monitor._entries[0]
    url = "https://www.twitch.tv/hello" if platform == "twitch" else "https://www.youtube.com/watch?v=live"
    info = StreamInfo(platform=actual_platform, channel="hello", is_live=True,
                      url=url, stream_status="live")
    item = VideoItem(video_id="live", title="Live", url=url, style="LIVE")
    monitor._db.mark_seen("live", "youtube", "hello", "LIVE", "Live")
    monitor._last_status[entry.key] = ChannelStatus(status=True, url=url)
    fetcher = SimpleNamespace(
        get_stream_info=lambda _name: info,
        get_channel_items=lambda _name, **_kw: [item] if platform == "youtube" else [],
    )
    monkeypatch.setattr(deps, "get_fetcher", lambda _platform: fetcher)
    try:
        monitor._poll_cycle = 1
        assert monitor._probe_live(entry) == []
        monitor.request_live_recheck()
        # A mode switch midway through a poll must not consume the recheck.
        assert monitor._probe_live(entry) == []
        monitor._poll_cycle = 2
        events = monitor._probe_live(entry)
        assert len(events) == 1
        assert events[0][1].url == url
        assert monitor._db.is_seen("live", "LIVE")
        monitor._poll_cycle = 3
        assert monitor._probe_live(entry) == []
    finally:
        monitor._db.close()


def test_recheck_waits_for_available_evidence_and_does_not_reopen_offline(monkeypatch):
    monitor = Monitor([{"platform": "twitch", "name": "hello"}])
    entry = monitor._entries[0]
    monitor._last_status[entry.key] = ChannelStatus(status=True, url="https://www.twitch.tv/hello")
    result = None
    monkeypatch.setattr(deps, "get_fetcher", lambda _p: SimpleNamespace(
        get_stream_info=lambda _name: result,
    ))
    try:
        monitor.request_live_recheck()
        monitor._poll_cycle = 1
        assert monitor._probe_live(entry) == []
        assert entry.key in monitor._live_recheck_after
        result = StreamInfo(platform="twitch", channel="hello", is_live=False)
        monitor._poll_cycle = 2
        assert monitor._probe_live(entry) == []
        assert entry.key not in monitor._live_recheck_after
    finally:
        monitor._db.close()


def test_recheck_does_not_duplicate_a_new_live_edge(monkeypatch):
    monitor = Monitor([{"platform": "twitch", "name": "hello"}])
    entry = monitor._entries[0]
    info = StreamInfo(platform="twitch", channel="hello", is_live=True,
                      url="https://www.twitch.tv/hello")
    monkeypatch.setattr(deps, "get_fetcher", lambda _p: SimpleNamespace(
        get_stream_info=lambda _name: info,
    ))
    try:
        monitor.request_live_recheck()
        monitor._poll_cycle = 1
        assert len(monitor._probe_live(entry)) == 1
    finally:
        monitor._db.close()
