"""Shared helpers used across stream_monitor modules."""

from __future__ import annotations

from datetime import datetime


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
