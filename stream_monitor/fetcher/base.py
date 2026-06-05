"""StreamFetcher 抽象介面 — 策略模式基底類別。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VideoItem:
    """A single video/stream entry from a channel page."""

    video_id: str
    title: str
    style: str  # "LIVE", "UPCOMING", "DEFAULT"
    url: str
    display_name: str = ""
    scheduled_start: str = ""  # ISO 8601, only for UPCOMING
    started_at: str = ""  # ISO 8601, only for LIVE when available


@dataclass
class FinishedVod:
    """Most recent finished broadcast / archive for offline elapsed + link."""

    url: str
    ended_at: str = ""  # ISO8601 estimated stream end
    title: str = ""


@dataclass
class StreamInfo:
    """Snapshot of a channel's live status."""

    channel: str
    platform: str
    is_live: bool
    title: str = ""
    url: str = ""
    display_name: str = ""
    video_id: str = ""
    stream_status: str = ""  # "live", "upcoming", "video"
    scheduled_start: str = ""
    started_at: str = ""


class StreamFetcher(ABC):
    """Abstract base for platform-specific stream status fetchers."""

    platform: str = ""

    @abstractmethod
    def is_live(self, channel_name: str) -> bool:
        """Return True if *channel_name* is currently streaming."""
        ...

    @abstractmethod
    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        """Return rich stream info, or None on failure."""
        ...

    def get_channel_items(
        self, channel_name: str, *, fill_timing: bool = True
    ) -> list[VideoItem]:
        """Return video items from channel page.

        YouTube-only (TIDUS /streams feed). Twitch uses boolean live checks
        and ARCHIVE VOD queries instead; the base implementation is empty.

        *fill_timing* is YouTube-specific; when False the poll path skips
        per-video watch-page fetches for started_at / scheduled_start.
        """
        return []

    def get_latest_finished_vod(self, channel_name: str) -> FinishedVod | None:
        """Return the latest finished VOD/replay, if the platform supports it."""
        return None
