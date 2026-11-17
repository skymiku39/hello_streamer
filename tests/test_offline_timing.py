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


def test_channel_had_live_session_iterates_under_session_lock(tmp_path) -> None:
    """Regression: live maps are mutated under the lock by tier-2 workers, so
    iterating them must happen while the lock is held (or on a snapshot)."""
    from stream_monitor.db import SeenVideoDB
    from stream_monitor.monitor import Monitor

    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "chan"}],
        db=SeenVideoDB(tmp_path / "test.db"),
    )
    held_during_iter: list[bool] = []

    class SpyDict(dict):
        def __iter__(self):
            held_during_iter.append(monitor._lock.locked())
            return super().__iter__()

    monitor._live_payload = SpyDict({"youtube:other|vid1": object()})
    monitor._live_started_at = SpyDict({"youtube:chan|vid2": "2026-01-01T00:00:00Z"})

    assert monitor._channel_had_live_session("youtube:chan") is True
    assert monitor._channel_had_live_session("youtube:none") is False
    assert held_during_iter and all(held_during_iter)
    # Non-reentrant lock must be released afterwards.
    assert not monitor._lock.locked()
