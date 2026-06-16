"""Narrow probe-facing boundary over ``Monitor`` (ISP, DIP).

Probes depend on this facade plus their fetcher — never on ``Monitor``'s
internals. State lives in :class:`ProbeSession`; behaviour is delegated to the
monitor's offline/preview helpers through explicit, named methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor.probes.session import ProbeSession
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _ProbeSnapshot,
)

if TYPE_CHECKING:
    from stream_monitor.monitor.core import Monitor

CommitFn = Callable[[], None]


class ProbeFacade:
    """Everything a ``PlatformProbe`` may touch on the host monitor."""

    def __init__(self, monitor: Monitor) -> None:
        self._monitor = monitor

    @property
    def session(self) -> ProbeSession:
        return self._monitor._session

    @property
    def db(self) -> SeenVideoDB:
        return self._monitor._db

    @property
    def wake_verify_mode(self) -> bool:
        return self._monitor._wake_verify_mode

    @property
    def noop_commit(self) -> CommitFn:
        return self._monitor._noop_commit

    def handle_fetch_unavailable(
        self,
        entry: ChannelEntry,
        *,
        label: str,
        snap: _ProbeSnapshot | None = None,
        reason: str = "fetch returned None",
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        return self._monitor._handle_fetch_unavailable(
            entry, label=label, snap=snap, reason=reason
        )

    def record_offline_miss(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev_status: Any,
        *,
        label: str,
        reason: str,
    ) -> str:
        return self._monitor._record_offline_miss(
            entry, live_key, prev_status, label=label, reason=reason
        )

    def publish_preview(
        self, entry: ChannelEntry, *, from_probe: bool = False
    ) -> None:
        self._monitor._publish_channel_preview(entry, from_probe=from_probe)

    def youtube_live_row_is_stable(
        self,
        entry: ChannelEntry,
        prev_cs: ChannelStatus | None,
        live_item: VideoItem,
    ) -> bool:
        return self._monitor._youtube_live_row_is_stable(
            entry, prev_cs, live_item
        )

    def youtube_offline_row_is_stable(
        self, prev_cs: ChannelStatus, items: list[VideoItem]
    ) -> bool:
        return self._monitor._youtube_offline_row_is_stable(prev_cs, items)

    def resolve_youtube_live_started_at(
        self, entry: ChannelEntry, live_item: VideoItem
    ) -> str:
        return self._monitor._resolve_youtube_live_started_at(entry, live_item)

    def find_youtube_upcoming_item(
        self,
        entry: ChannelEntry,
        fetcher: Any | None,
        *,
        channel_items: list[VideoItem] | None = None,
    ) -> VideoItem | None:
        return self._monitor._find_youtube_upcoming_item(
            entry, fetcher, channel_items=channel_items
        )

    def youtube_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        payload: OfflineInfo | None = None,
        *,
        fetcher: Any | None = None,
        extra_vod_url: str = "",
        channel_items: list[VideoItem] | None = None,
    ) -> ChannelStatus:
        return self._monitor._youtube_offline_status_for(
            entry,
            prev,
            payload,
            fetcher=fetcher,
            extra_vod_url=extra_vod_url,
            channel_items=channel_items,
        )

    def enqueue_youtube_fallback_offline(
        self, entry: ChannelEntry, prev: Any, *, label: str
    ) -> None:
        self._monitor._enqueue_youtube_fallback_went_offline(
            entry, prev, label=label
        )

    def enqueue_twitch_offline(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev: Any,
        *,
        label: str,
        fetcher: Any | None = None,
    ) -> None:
        self._monitor._enqueue_twitch_went_offline(
            entry, live_key, prev, label=label, fetcher=fetcher
        )

    def twitch_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        *,
        fetcher: Any | None = None,
    ) -> ChannelStatus:
        return self._monitor._twitch_offline_status_for(
            entry, prev, fetcher=fetcher
        )

    def maybe_log_stable_twitch_status(
        self, entry: ChannelEntry, info: StreamInfo, prev_status: Any
    ) -> None:
        self._monitor._maybe_log_stable_twitch_status(entry, info, prev_status)
