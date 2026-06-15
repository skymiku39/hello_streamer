"""Shared monitor surface exposed to platform probe strategies (ISP)."""

from __future__ import annotations

import threading
from typing import Any, Callable, Protocol

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _ProbeSnapshot,
)

CommitFn = Callable[[], None]


class ProbeHost(Protocol):
    """Session state and helpers ``PlatformProbe`` implementations may use."""

    _lock: threading.Lock
    _db: SeenVideoDB
    _last_status: dict[str, Any]
    _display_names: dict[str, str]
    _probe_snapshots: dict[str, _ProbeSnapshot]
    _offline_strikes: dict[str, int]
    _live_started_at: dict[str, str]
    _live_platform_started_at: set[str]
    _live_payload: dict[str, OfflineInfo]
    _fallback_triggered_live: dict[str, str]
    _youtube_baselined: set[str]
    _pending_offline_events: list[tuple[ChannelEntry, OfflineInfo]]
    _wake_verify_mode: bool
    _noop_commit: CommitFn

    def _handle_fetch_unavailable(
        self,
        entry: ChannelEntry,
        *,
        label: str,
        snap: _ProbeSnapshot | None = None,
        reason: str = "fetch returned None",
    ) -> list[tuple[ChannelEntry, StreamInfo]]: ...

    def _record_offline_miss(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev_status: Any,
        *,
        label: str,
        reason: str,
    ) -> str: ...

    def _publish_channel_preview(
        self, entry: ChannelEntry, *, from_probe: bool = False
    ) -> None: ...

    def _youtube_live_row_is_stable(
        self,
        entry: ChannelEntry,
        prev_cs: ChannelStatus | None,
        live_item: VideoItem,
    ) -> bool: ...

    def _youtube_offline_row_is_stable(
        self, prev_cs: ChannelStatus, items: list[VideoItem]
    ) -> bool: ...

    def _resolve_youtube_live_started_at(
        self, entry: ChannelEntry, live_item: VideoItem
    ) -> str: ...

    def _find_youtube_upcoming_item(
        self,
        entry: ChannelEntry,
        fetcher: Any | None,
        *,
        channel_items: list[VideoItem] | None = None,
    ) -> VideoItem | None: ...

    def _youtube_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        payload: OfflineInfo | None,
        *,
        fetcher: Any | None = None,
        extra_vod_url: str = "",
        channel_items: list[VideoItem] | None = None,
    ) -> ChannelStatus: ...

    def _enqueue_youtube_fallback_went_offline(
        self,
        entry: ChannelEntry,
        prev: Any,
        *,
        label: str,
    ) -> None: ...

    def _enqueue_twitch_went_offline(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev: Any,
        *,
        label: str,
        fetcher: Any | None = None,
    ) -> None: ...

    def _twitch_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        *,
        fetcher: Any | None = None,
    ) -> ChannelStatus: ...

    def _maybe_log_stable_twitch_status(
        self,
        entry: ChannelEntry,
        info: StreamInfo,
        prev_status: Any,
    ) -> None: ...

    def _fetch_finished_vod(
        self,
        entry: ChannelEntry,
        fetcher: Any | None,
        *,
        channel_items: list[VideoItem] | None = None,
    ) -> FinishedVod | None: ...
