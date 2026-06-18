"""Monitor domain types, constants, and pure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from stream_monitor.domain import ChannelEntry, ChannelStatus, OfflineInfo
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.util import (
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)

__all__ = [
    "ChannelEntry",
    "ChannelStatus",
    "OfflineInfo",
]

_STYLE_TO_STATUS = {
    "LIVE": "live",
    "UPCOMING": "upcoming",
    "DEFAULT": "video",
}

# Anti-flap guard: how many consecutive "not live" readings we require before
# we trust a previously-live channel/video has actually gone offline.
#
# Why: Twitch GQL occasionally returns `stream: null` for a still-live channel
# (CDN/cache lag at peak hours), and YouTube's TIDUS feed sometimes omits a
# LIVE video for one poll. Without this guard, those single-poll dropouts
# generate a fake went_offline → went_live edge pair, which (a) triggers a
# duplicate "stream is live!" notification on the next poll and (b) — if the
# user enabled close_on_offline — actually closes the player window the app
# just opened. Requiring two consecutive misses makes both problems go away
# while still bounding worst-case latency to two poll intervals.
_OFFLINE_STRIKE_THRESHOLD = 2
# After a long wall-clock gap (e.g. system sleep), run a wake verification
# poll before the next regular cycle — confirm cached state or defer edges.
_POST_RESUME_GAP_MULTIPLIER = 2
_FETCH_FAILURE_REASONS = frozenset({"fetch returned None", "fetch exception"})
# Log unchanged channel status every N poll cycles (per channel) for diagnostics.
_STABLE_STATUS_LOG_EVERY = 20
_CONFIRMED_FUTURE_SLACK = timedelta(minutes=5)
_DEFAULT_MAX_CONCURRENT = 4
# YouTube fetcher enforces ~1 HTTP req/s globally; cap concurrent YouTube probes
# while Twitch uses the remaining pool slots in parallel (YouTube-first dequeue).
_YOUTUBE_MAX_CONCURRENT = 1
# Minimum rest between poll cycles when a cycle overruns check_interval.
_MIN_POLL_REST_S = 5.0
# Periodic housekeeping for SQLite seen_videos and YouTube watch-page cache.
_MAINTENANCE_INTERVAL_S = 24 * 3600
_DB_CLEANUP_DAYS = 30


@dataclass
class _ProbeSnapshot:
    """Tier-1 probe cache reused by tier-2 detail refresh in the same poll."""

    twitch_info: StreamInfo | None = None
    twitch_offline_hold: bool = False
    twitch_offline_commit: bool = False
    youtube_items: list[VideoItem] | None = None
    youtube_pending_seen: list[tuple[str, str, str, str, str]] | None = None
    youtube_fallback: bool = False
    youtube_fallback_info: StreamInfo | None = None
    youtube_fallback_hold: bool = False
    fetcher: Any = None


def _video_item_to_stream_info(item: VideoItem, channel: str) -> StreamInfo:
    return StreamInfo(
        channel=channel,
        platform="youtube",
        is_live=item.style == "LIVE",
        title=item.title,
        url=item.url,
        display_name=item.display_name,
        video_id=item.video_id,
        stream_status=_STYLE_TO_STATUS.get(item.style, "video"),
        scheduled_start=item.scheduled_start,
        started_at=item.started_at,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _channel_home_url(entry: ChannelEntry) -> str:
    if entry.platform == "twitch":
        return f"https://www.twitch.tv/{entry.name}"
    if entry.name.startswith("UC"):
        return f"https://www.youtube.com/channel/{entry.name}"
    return f"https://www.youtube.com/@{entry.name}"


def _merge_offline_ended_at(
    confirmed_iso: str, platform_end: str | None
) -> tuple[str, str]:
    """Pick ended_at for offline elapsed. Returns (iso, source)."""
    confirmed_dt = parse_iso_datetime(confirmed_iso)
    if confirmed_dt is None:
        if platform_end:
            platform_dt = parse_iso_datetime(platform_end)
            if platform_dt is not None:
                now = datetime.now(timezone.utc)
                if platform_dt <= now + _CONFIRMED_FUTURE_SLACK:
                    return platform_end, "vod"
        return confirmed_iso, "confirmed" if confirmed_iso else ""
    if not platform_end:
        return confirmed_iso, "confirmed"
    platform_dt = parse_iso_datetime(platform_end)
    if platform_dt is None:
        return confirmed_iso, "confirmed"
    now = datetime.now(timezone.utc)
    if platform_dt > now + _CONFIRMED_FUTURE_SLACK:
        return confirmed_iso, "confirmed"
    if platform_dt > confirmed_dt + _CONFIRMED_FUTURE_SLACK:
        return confirmed_iso, "confirmed"
    return platform_end, "vod"


def _live_cache_key(entry_key: str, video_id: str = "") -> str:
    return f"{entry_key}|{video_id or '_'}"


def _entry_key_from_live_cache_key(key: str) -> str:
    return key.split("|", 1)[0]


def _sort_datetime(value: str, fallback: datetime) -> datetime:
    return parse_iso_datetime(value) or fallback


def _youtube_upcoming_is_usable(scheduled_start: str) -> bool:
    """True when a YouTube waiting-room schedule is worth surfacing."""
    return youtube_upcoming_schedule_is_surfacable(scheduled_start)


def split_platform_entries(
    entries: list[ChannelEntry],
) -> tuple[list[ChannelEntry], list[ChannelEntry]]:
    """Partition enabled channels into YouTube and all other platforms."""
    youtube = [entry for entry in entries if entry.platform == "youtube"]
    other = [entry for entry in entries if entry.platform != "youtube"]
    return youtube, other


def youtube_priority_entries(
    entries: list[ChannelEntry],
) -> list[ChannelEntry]:
    """YouTube channels first, then all other platforms (config list order).

    Legacy helper for tests only. Live polling uses ``interleave_platform_entries``.
    """
    youtube = [entry for entry in entries if entry.platform == "youtube"]
    other = [entry for entry in entries if entry.platform != "youtube"]
    return youtube + other


def interleave_platform_entries(
    entries: list[ChannelEntry],
) -> list[ChannelEntry]:
    """Round-robin YouTube with other platforms; ▲▼ order is secondary within each.

    Primary scheduling: alternate platforms so Twitch is not deferred behind a
    long YouTube prefix in the UI list. Within each platform, channels keep
    their relative order from the user's ▲▼-sorted config list.
    """
    youtube: list[ChannelEntry] = []
    other: list[ChannelEntry] = []
    for entry in entries:
        if entry.platform == "youtube":
            youtube.append(entry)
        else:
            other.append(entry)
    merged: list[ChannelEntry] = []
    yi = oi = 0
    while yi < len(youtube) or oi < len(other):
        if yi < len(youtube):
            merged.append(youtube[yi])
            yi += 1
        if oi < len(other):
            merged.append(other[oi])
            oi += 1
    return merged


def poll_rest_overshoot_seconds(
    wall_now: float,
    last_poll_wall_ended: float,
    last_poll_planned_rest: float,
) -> float:
    """Seconds the next poll started beyond its planned rest (sleep detection)."""
    if last_poll_wall_ended <= 0:
        return 0.0
    since_end = wall_now - last_poll_wall_ended
    return since_end - last_poll_planned_rest


