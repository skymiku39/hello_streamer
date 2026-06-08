"""Shared helpers used across stream_monitor modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_YOUTUBE_UPCOMING_MAX_FUTURE = timedelta(days=7)


def normalize_channel_name(platform: str, name: str) -> str:
    """Normalize channel slug for keys and Twitch API (login is case-insensitive)."""
    n = name.strip()
    if platform == "twitch":
        return n.lower()
    return n


def channel_key(platform: str, name: str) -> str:
    return f"{platform}:{normalize_channel_name(platform, name)}"


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def youtube_upcoming_schedule_is_surfacable(scheduled_start: str) -> bool:
    """True when a YouTube waiting-room schedule should surface in the UI."""
    if not scheduled_start:
        return False
    start_dt = parse_iso_datetime(scheduled_start)
    if start_dt is None:
        return False
    now = datetime.now(timezone.utc)
    if start_dt <= now:
        return False
    if start_dt > now + _YOUTUBE_UPCOMING_MAX_FUTURE:
        return False
    return True
