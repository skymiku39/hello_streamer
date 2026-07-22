"""Tests for hybrid offline ended_at merge logic."""

from datetime import datetime, timedelta, timezone

from stream_monitor.monitor import _merge_offline_ended_at


def test_merge_prefers_vod_when_earlier_than_confirmed() -> None:
    confirmed_dt = datetime.now(timezone.utc)
    confirmed = confirmed_dt.isoformat()
    vod_end = (confirmed_dt - timedelta(minutes=30)).isoformat()
    ended, source = _merge_offline_ended_at(confirmed, vod_end)
    assert ended == vod_end
    assert source == "vod"


def test_merge_uses_vod_even_when_older_than_confirmed() -> None:
    confirmed_dt = datetime.now(timezone.utc)
    confirmed = confirmed_dt.isoformat()
    vod_end = (confirmed_dt - timedelta(days=3)).isoformat()
    ended, source = _merge_offline_ended_at(confirmed, vod_end)
    assert ended == vod_end
    assert source == "vod"


def test_merge_uses_vod_when_confirmed_empty() -> None:
    vod_end = "2020-01-01T00:00:00+00:00"
    ended, source = _merge_offline_ended_at("", vod_end)
    assert ended == vod_end
    assert source == "vod"


def test_merge_keeps_confirmed_when_vod_in_future() -> None:
    confirmed_dt = datetime.now(timezone.utc)
    confirmed = confirmed_dt.isoformat()
    vod_end = (confirmed_dt + timedelta(hours=1)).isoformat()
    ended, source = _merge_offline_ended_at(confirmed, vod_end)
    assert ended == confirmed
    assert source == "confirmed"


def test_offline_vod_should_refresh_when_archive_url_changes() -> None:
    from stream_monitor.domain import ChannelStatus
    from stream_monitor.fetcher.base import FinishedVod
    from stream_monitor.monitor.types import _offline_vod_should_refresh

    prev = ChannelStatus(
        status=False,
        title="Old",
        vod_url="https://www.twitch.tv/videos/old",
        ended_at="2026-07-20T18:10:41+00:00",
        ended_at_source="vod",
    )
    vod = FinishedVod(
        url="https://www.twitch.tv/videos/new",
        ended_at="2026-07-21T17:44:09+00:00",
        title="New",
    )
    assert _offline_vod_should_refresh(prev, vod) is True


def test_offline_vod_should_not_refresh_when_archive_unchanged() -> None:
    from stream_monitor.domain import ChannelStatus
    from stream_monitor.fetcher.base import FinishedVod
    from stream_monitor.monitor.types import _offline_vod_should_refresh

    prev = ChannelStatus(
        status=False,
        title="Same",
        vod_url="https://www.twitch.tv/videos/same",
        ended_at="2026-07-21T17:44:09+00:00",
        ended_at_source="vod",
    )
    vod = FinishedVod(
        url="https://www.twitch.tv/videos/same",
        ended_at="2026-07-21T17:44:09+00:00",
        title="Same",
    )
    assert _offline_vod_should_refresh(prev, vod) is False
