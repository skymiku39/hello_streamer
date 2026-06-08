from datetime import datetime, timedelta, timezone

from stream_monitor.util import youtube_upcoming_schedule_is_surfacable


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
