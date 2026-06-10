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
