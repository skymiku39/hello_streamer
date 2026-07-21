"""Tier-1 preview: push partial channel status to the UI mid-poll."""

from __future__ import annotations

import logging
from typing import Any

from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    _channel_home_url,
    _ProbeSnapshot,
)

logger = logging.getLogger(__name__)


class PreviewMixin:
    """Builds and emits early per-channel status previews from tier-1 probes."""

    @staticmethod
    def _coalesce_tier1_offline_preview(
        preview: ChannelStatus, cached: Any
    ) -> ChannelStatus:
        """Keep tier-2 offline detail when tier-1 only knows live/not-live."""
        if preview.status is not False or preview.ended_at_source != "pending":
            return preview
        if not isinstance(cached, ChannelStatus) or cached.status is not False:
            return preview
        if cached.ended_at_source == "pending":
            return preview
        return ChannelStatus(
            status=False,
            title=cached.title or preview.title,
            url=cached.url or preview.url,
            vod_url=cached.vod_url or preview.vod_url,
            upcoming_url=cached.upcoming_url or preview.upcoming_url,
            scheduled_start=cached.scheduled_start or preview.scheduled_start,
            ended_at=cached.ended_at,
            ended_at_source=cached.ended_at_source,
        )

    def _publish_channel_preview(
        self, entry: ChannelEntry, *, from_probe: bool = False
    ) -> None:
        """Push one channel's status to the UI without waiting for the full poll."""
        if self._event_bus is None:
            return
        built_preview: ChannelStatus | None = None
        cached_status: Any = None
        snap: _ProbeSnapshot | None = None
        if from_probe:
            with self._lock:
                snap = self._probe_snapshots.get(entry.key)
                cached_status = self._last_status.get(entry.key)
            if snap is None:
                return
            if entry.platform == "youtube" and snap.youtube_items is not None:
                built_preview = self._build_youtube_tier1_preview(
                    entry, snap, cached_status=cached_status
                )
            elif entry.platform == "twitch" and snap.twitch_info is not None:
                built_preview = self._build_twitch_tier1_preview(
                    entry, snap, cached_status=cached_status
                )
        with self._lock:
            if from_probe and built_preview is not None:
                preview = self._coalesce_tier1_offline_preview(
                    built_preview, cached_status
                )
                # A tier-1 preview must never downgrade a cached LIVE status to
                # offline. The live->offline edge is committed by tier-2
                # refresh_details, which relies on ``prev_status is True`` to
                # emit went_offline (and drive close_on_offline). Writing an
                # offline preview here would erase that signal, so the offline
                # event — and the browser auto-close — would silently never fire.
                downgrades_live_to_offline = (
                    isinstance(cached_status, ChannelStatus)
                    and cached_status.status is True
                    and isinstance(preview, ChannelStatus)
                    and preview.status is False
                )
                if not downgrades_live_to_offline:
                    self._last_status[entry.key] = preview
            if entry.key not in self._last_status:
                return
            status = self._last_status[entry.key]
            statuses = {entry.key: status}
            names = dict(self._display_names)
            status_repr = (
                status.status
                if isinstance(status, ChannelStatus)
                else status
            )
        logger.debug(
            "Tier preview %s: from_probe=%s status=%s ended_at_source=%s",
            entry.key,
            from_probe,
            status_repr,
            (
                status.ended_at_source
                if isinstance(status, ChannelStatus)
                else ""
            ),
        )
        self._emit_partial_snapshot(statuses, names)

    def _build_twitch_tier1_preview(
        self,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
        *,
        cached_status: Any | None = None,
    ) -> ChannelStatus | None:
        info = snap.twitch_info
        if info is None:
            return None
        if snap.twitch_offline_hold:
            if (
                isinstance(cached_status, ChannelStatus)
                and cached_status.status is True
            ):
                return cached_status
            return None
        if info.is_live:
            if (
                isinstance(cached_status, ChannelStatus)
                and cached_status.status is True
            ):
                return cached_status
            return ChannelStatus(
                status=True,
                url=info.url,
                title=info.title,
                started_at=info.started_at or "",
            )
        return ChannelStatus(
            status=False,
            title="",
            url=_channel_home_url(entry),
            vod_url="",
            ended_at="",
            ended_at_source="pending",
        )

    def _build_youtube_tier1_preview(
        self,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
        *,
        cached_status: Any | None = None,
    ) -> ChannelStatus | None:
        items = snap.youtube_items or []
        live_items = [item for item in items if item.style == "LIVE"]
        if live_items:
            item = live_items[0]
            if isinstance(cached_status, ChannelStatus) and self._youtube_live_row_is_stable(
                entry, cached_status, item
            ):
                return ChannelStatus(
                    status=True,
                    url=item.url,
                    title=item.title or cached_status.title,
                    started_at=cached_status.started_at,
                )
            return ChannelStatus(
                status=True,
                url=item.url,
                title=item.title,
                started_at=self._resolve_youtube_live_started_at(entry, item),
            )
        upcoming = self._pick_youtube_upcoming_from_items(items)
        if upcoming:
            return ChannelStatus(
                status="upcoming",
                url=upcoming.url,
                title=upcoming.title,
                scheduled_start=upcoming.scheduled_start or "",
            )
        extra_vod = next((item.url for item in items if item.style == "DEFAULT"), "")
        title = next((item.title for item in items if item.style == "DEFAULT"), "")
        return ChannelStatus(
            status=False,
            title=title,
            vod_url=extra_vod,
            url=_channel_home_url(entry),
            ended_at="",
            ended_at_source="pending",
        )
