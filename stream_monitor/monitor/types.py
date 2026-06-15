"""Monitor domain types, constants, and pure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.util import (
    channel_key,
    normalize_channel_name,
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)

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
# Minimum rest between poll cycles when a cycle overruns check_interval.
_MIN_POLL_REST_S = 5.0
# Periodic housekeeping for SQLite seen_videos and YouTube watch-page cache.
_MAINTENANCE_INTERVAL_S = 24 * 3600
_DB_CLEANUP_DAYS = 30


@dataclass
class ChannelEntry:
    platform: str
    name: str
    enabled: bool = True
    # monitor_only = True ⇒ the polling thread should still observe this
    # channel (status updates, "LIVE" labels in the UI) but downstream
    # action dispatch (notifications, opening the browser, close_on_offline)
    # must be suppressed. The flag is carried on the entry so callbacks can
    # easily see it without re-resolving the channel via config_manager.
    monitor_only: bool = False

    def __post_init__(self) -> None:
        self.name = normalize_channel_name(self.platform, self.name)

    @property
    def key(self) -> str:
        return channel_key(self.platform, self.name)


@dataclass
class ChannelStatus:
    status: bool | str | None
    url: str = ""
    title: str = ""
    scheduled_start: str = ""
    started_at: str = ""
    ended_at: str = ""  # ISO8601 when offline was confirmed
    vod_url: str = ""  # archive / replay link for the link button
    upcoming_url: str = ""  # waiting-room link when offline but scheduled
    ended_at_source: str = ""  # "vod" | "confirmed" | "pending"

    def __eq__(self, other: object) -> bool:
        return self.status == other


StatusCallback = Callable[[ChannelEntry, StreamInfo], None]
PollActivityCallback = Callable[[ChannelEntry, str, str], None]
PartialSnapshotCallback = Callable[[dict[str, Any], dict[str, str]], None]

# Fired when a channel that was previously LIVE transitions back to "not live".
# Receives the entry plus the URL and title that were last known to be live,
# so callers can e.g. close the player window we opened on the going-live edge.
OfflineCallback = Callable[["ChannelEntry", "OfflineInfo"], None]


@dataclass
class OfflineInfo:
    url: str
    title: str
    platform: str
    name: str
    video_id: str = ""
    display_name: str = ""


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


