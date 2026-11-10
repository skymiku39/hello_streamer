"""Failed queries cannot confirm OFFLINE or silently look fresh."""

from unittest.mock import Mock

import pytest

from stream_monitor import i18n
from stream_monitor.db import SeenVideoDB
from stream_monitor.events import MonitorEventBus
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import ChannelEntry, Monitor, deps


@pytest.mark.parametrize("platform", ["twitch", "youtube"])
def test_cold_failure_live_failure_and_recovery(monkeypatch, tmp_path, platform):
    fetcher = Mock()
    fetcher.last_unavailable_reason = "ytInitialData parse failure"
    fetcher.http_backoff_active.return_value = False
    method = fetcher.get_stream_info if platform == "twitch" else fetcher.get_channel_items
    method.return_value = None
    monkeypatch.setattr(deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "seen.db")
    try:
        bus = MonitorEventBus()
        monitor = Monitor(channels=[{"platform": platform, "name": "test"}], db=db,
                          event_bus=bus)
        entry = ChannelEntry(platform, "test")

        def poll():
            _events, commit = monitor._check_channel(entry)
            commit()
            return monitor._last_status[entry.key]

        cold = poll()
        assert cold.is_unknown and cold.unavailable
        assert bus.drain(), "The failed query must reach the UI"
        info = StreamInfo(channel="test", platform=platform, is_live=True,
                          url="https://www.twitch.tv/test", title="Live",
                          started_at="2026-11-06T00:00:00Z")
        live = info if platform == "twitch" else [VideoItem(
            video_id="test", url="https://www.youtube.com/watch?v=test", title="Live",
            style="LIVE", started_at=info.started_at)]
        method.return_value = live
        assert poll().is_live
        method.return_value = None
        for _ in range(10):
            failed = poll()
            assert failed.is_live and failed.unavailable
            assert not monitor._pending_offline_events
            assert not monitor._offline_strikes
        method.return_value = live
        recovered = poll()
        assert recovered.is_live and not recovered.unavailable
    finally:
        db.close()


def test_all_languages_define_every_product_key():
    reference = set(i18n._TRANSLATIONS[i18n.DEFAULT_LANGUAGE])
    for language, translations in i18n._TRANSLATIONS.items():
        assert reference <= set(translations), (language, reference - set(translations))
