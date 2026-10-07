from datetime import datetime, timedelta, timezone

import pytest

from stream_monitor.util import (
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)


@pytest.mark.parametrize("value", ["2026-06-05", "2026-06-05T00:00:00"])
def test_parse_iso_datetime_uses_utc_for_missing_timezone(value) -> None:
    assert parse_iso_datetime(value) == datetime(2026, 6, 5, tzinfo=timezone.utc)


def test_parse_iso_datetime_preserves_explicit_timezone() -> None:
    expected = datetime(2026, 6, 5, tzinfo=timezone(timedelta(hours=8)))
    assert parse_iso_datetime("2026-06-05T00:00:00+08:00") == expected


@pytest.mark.parametrize("value", [None, 123, [], {}, "invalid"])
def test_parse_iso_datetime_rejects_invalid_values(value) -> None:
    assert parse_iso_datetime(value) is None


def test_upcoming_schedule_accepts_timestamp_without_timezone() -> None:
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    assert youtube_upcoming_schedule_is_surfacable(
        start.replace(tzinfo=None).isoformat()
    ) is True


def test_youtube_upcoming_schedule_is_surfacable() -> None:
    now = datetime.now(timezone.utc)
    assert youtube_upcoming_schedule_is_surfacable("") is False
    assert youtube_upcoming_schedule_is_surfacable("not-a-date") is False
    assert youtube_upcoming_schedule_is_surfacable(
        (now - timedelta(minutes=5)).isoformat()
    ) is False
    assert youtube_upcoming_schedule_is_surfacable(
        (now + timedelta(hours=2)).isoformat()
    ) is True
    assert youtube_upcoming_schedule_is_surfacable(
        (now + timedelta(days=8)).isoformat()
    ) is False
