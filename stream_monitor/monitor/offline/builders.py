"""Construct offline/upcoming/VOD row status for a channel."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.types import (
    _STABLE_STATUS_LOG_EVERY,
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _merge_offline_ended_at,
    _sort_datetime,
    _utc_now_iso,
    _youtube_upcoming_is_usable,
)

logger = logging.getLogger(__name__)


class OfflineBuildersMixin:
    """Builds and upgrades offline row state from fetcher/VOD/upcoming data."""

    @staticmethod
    def _pick_youtube_upcoming_from_items(
        items: list[VideoItem],
    ) -> VideoItem | None:
        upcoming = [
            item
            for item in items
            if item.style == "UPCOMING"
            and _youtube_upcoming_is_usable(item.scheduled_start)
        ]
        if not upcoming:
            return None
        return min(
            upcoming,
            key=lambda item: _sort_datetime(
                item.scheduled_start, datetime.max.replace(tzinfo=timezone.utc)
            ),
        )

    def _find_youtube_upcoming_item(
        self,
        entry: ChannelEntry,
        fetcher: Any | None,
        *,
        channel_items: list[VideoItem] | None = None,
    ) -> VideoItem | None:
        """Return the nearest YouTube UPCOMING (waiting-room) item, if any."""
        if channel_items is not None:
            return self._pick_youtube_upcoming_from_items(channel_items)
        if fetcher is None:
            try:
                fetcher = _monitor_deps.get_fetcher("youtube")
            except Exception:
                logger.exception("No YouTube fetcher for %s", entry.key)
                return None
        try:
            items = fetcher.get_channel_items(entry.name)
        except Exception:
            logger.exception("Failed to fetch YouTube channel items for %s", entry.key)
            return None
        if items is None:
            return None
        return self._pick_youtube_upcoming_from_items(items)

    def _fetch_finished_vod(
        self,
        entry: ChannelEntry,
        fetcher: Any | None,
        *,
        channel_items: list[VideoItem] | None = None,
    ) -> FinishedVod | None:
        if fetcher is None:
            try:
                fetcher = _monitor_deps.get_fetcher(entry.platform)
            except Exception:
                logger.exception("No fetcher for %s", entry.key)
                return None
        try:
            if getattr(fetcher, "http_backoff_active", lambda: False)():
                return None
            return fetcher.get_latest_finished_vod(
                entry.name, items=channel_items
            )
        except Exception:
            logger.exception("Failed to fetch finished VOD for %s", entry.key)
            return None

    def _resolve_youtube_live_started_at(
        self, entry: ChannelEntry, item: VideoItem
    ) -> str:
        """Prefer feed/watch started_at; fall back to session cache or first-seen."""
        live_key = _live_cache_key(entry.key, item.video_id)
        if item.started_at:
            with self._lock:
                self._live_started_at[live_key] = item.started_at
                self._live_platform_started_at.add(live_key)
            return item.started_at
        with self._lock:
            cached = self._live_started_at.get(live_key)
            if cached:
                return cached
            now = _utc_now_iso()
            self._live_started_at[live_key] = now
            return now

    def _youtube_offline_row_is_stable(
        self, prev_cs: ChannelStatus, items: list[VideoItem]
    ) -> bool:
        """True when tier-2 can skip VOD/upcoming watch enrichment."""
        if prev_cs.ended_at_source == "vod" and prev_cs.vod_url:
            return True
        if prev_cs.ended_at_source != "pending" and not prev_cs.vod_url:
            return True
        return False

    def _youtube_live_row_is_stable(
        self,
        entry: ChannelEntry,
        prev_cs: ChannelStatus | None,
        live_item: VideoItem,
    ) -> bool:
        """True when tier-2 can skip LIVE watch enrichment."""
        if not isinstance(prev_cs, ChannelStatus) or prev_cs.status is not True:
            return False
        if not prev_cs.started_at:
            return False
        live_key = _live_cache_key(entry.key, live_item.video_id)
        with self._lock:
            if live_key not in self._live_platform_started_at:
                return False
        if live_item.video_id and prev_cs.url:
            marker = f"v={live_item.video_id}"
            if marker not in prev_cs.url and live_item.url != prev_cs.url:
                return False
        elif live_item.url != prev_cs.url:
            return False
        return True

    def _channel_had_live_session(self, entry_key: str) -> bool:
        """True if this channel was live at any point in the current session."""
        if entry_key in self._fallback_triggered_live:
            return True
        for key in self._live_payload:
            if _entry_key_from_live_cache_key(key) == entry_key:
                return True
        for key in self._live_started_at:
            if _entry_key_from_live_cache_key(key) == entry_key:
                return True
        return False

    def _offline_title(
        self,
        prev_cs: ChannelStatus | None,
        payload: OfflineInfo | None,
        vod: FinishedVod | None,
    ) -> str:
        if vod and vod.title:
            return vod.title
        if payload and payload.title:
            return payload.title
        if prev_cs:
            return prev_cs.title
        return ""

    def _resolve_offline_vod(
        self,
        entry: ChannelEntry,
        *,
        fetcher: Any | None,
        payload: OfflineInfo | None,
        prev_cs: ChannelStatus | None,
        extra_vod_url: str = "",
        channel_items: list[VideoItem] | None = None,
    ) -> tuple[str, str | None, FinishedVod | None]:
        """Shared VOD URL + platform end time for offline rows."""
        vod = self._fetch_finished_vod(
            entry, fetcher, channel_items=channel_items
        )
        vod_url = vod.url if vod else ""
        if not vod_url and payload and payload.url and entry.platform == "youtube":
            vod_url = payload.url
        if not vod_url:
            vod_url = extra_vod_url or (prev_cs.vod_url if prev_cs else "")
        platform_end = vod.ended_at if vod and vod.ended_at else None
        return vod_url, platform_end, vod

    def _build_youtube_offline_channel_status(
        self,
        entry: ChannelEntry,
        confirmed_iso: str,
        *,
        fetcher: Any | None,
        payload: OfflineInfo | None,
        prev_cs: ChannelStatus | None,
        extra_vod_url: str = "",
        channel_items: list[VideoItem] | None = None,
    ) -> ChannelStatus:
        """YouTube offline: waiting room + VOD + hybrid ended_at."""
        home = _channel_home_url(entry)
        vod_url, platform_end, vod = self._resolve_offline_vod(
            entry,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
            extra_vod_url=extra_vod_url,
            channel_items=channel_items,
        )
        ended_at, source = _merge_offline_ended_at(confirmed_iso, platform_end)
        had_live_context = (
            payload is not None
            or (prev_cs is not None and prev_cs.status is True)
            or self._channel_had_live_session(entry.key)
        )
        if not had_live_context and source == "confirmed":
            ended_at = ""
            source = ""
        upcoming_item = self._find_youtube_upcoming_item(
            entry, fetcher, channel_items=channel_items
        )
        upcoming_url = upcoming_item.url if upcoming_item else ""
        scheduled_start = upcoming_item.scheduled_start if upcoming_item else ""
        if not upcoming_url and prev_cs and prev_cs.upcoming_url:
            if _youtube_upcoming_is_usable(prev_cs.scheduled_start):
                upcoming_url = prev_cs.upcoming_url
                if not scheduled_start:
                    scheduled_start = prev_cs.scheduled_start
        if not vod_url and not platform_end and not upcoming_url:
            ended_at = ""
            source = ""
        title = self._offline_title(prev_cs, payload, vod)
        if upcoming_item and upcoming_item.title:
            title = upcoming_item.title
        return ChannelStatus(
            status=False,
            title=title,
            ended_at=ended_at,
            vod_url=vod_url,
            upcoming_url=upcoming_url,
            url=home,
            ended_at_source=source,
            scheduled_start=scheduled_start,
        )

    def _build_twitch_offline_channel_status(
        self,
        entry: ChannelEntry,
        confirmed_iso: str,
        *,
        fetcher: Any | None,
        payload: OfflineInfo | None,
        prev_cs: ChannelStatus | None,
    ) -> ChannelStatus:
        """Twitch offline: ARCHIVE VOD only (no waiting-room concept)."""
        home = _channel_home_url(entry)
        vod_url, platform_end, vod = self._resolve_offline_vod(
            entry,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
        )
        ended_at, source = _merge_offline_ended_at(confirmed_iso, platform_end)
        had_live_context = payload is not None or (
            prev_cs is not None and prev_cs.status is True
        )
        if not vod_url and not platform_end and not had_live_context:
            ended_at = ""
            source = ""
        title = self._offline_title(prev_cs, payload, vod)
        return ChannelStatus(
            status=False,
            title=title,
            ended_at=ended_at,
            vod_url=vod_url,
            upcoming_url="",
            url=home,
            ended_at_source=source,
            scheduled_start="",
        )

    def _try_upgrade_youtube_offline_vod(
        self,
        entry: ChannelEntry,
        prev_cs: ChannelStatus,
        *,
        fetcher: Any | None,
        payload: OfflineInfo | None,
        channel_items: list[VideoItem] | None = None,
    ) -> ChannelStatus | None:
        """Retry YouTube waiting-room + VOD links on stable offline polls."""
        if prev_cs.ended_at_source == "vod" and prev_cs.vod_url:
            if channel_items is None:
                return None
            fresh = self._find_youtube_upcoming_item(
                entry, fetcher, channel_items=channel_items
            )
            if fresh is None:
                return None
            upcoming_url = fresh.url
            scheduled_start = fresh.scheduled_start
            if (
                upcoming_url == prev_cs.upcoming_url
                and scheduled_start == prev_cs.scheduled_start
            ):
                return None
            title = fresh.title or prev_cs.title or (payload.title if payload else "")
            return ChannelStatus(
                status=False,
                title=title,
                ended_at=prev_cs.ended_at,
                vod_url=prev_cs.vod_url,
                upcoming_url=upcoming_url,
                url=_channel_home_url(entry),
                ended_at_source=prev_cs.ended_at_source,
                scheduled_start=scheduled_start,
            )

        if prev_cs.vod_url and prev_cs.upcoming_url and not fetcher:
            return None

        confirmed = prev_cs.ended_at or (
            _utc_now_iso() if payload is not None else ""
        )
        upgraded = self._build_youtube_offline_channel_status(
            entry,
            confirmed,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
            channel_items=channel_items,
        )
        if not upgraded.vod_url and not upgraded.upcoming_url:
            return None
        if (
            upgraded.ended_at == prev_cs.ended_at
            and upgraded.vod_url == prev_cs.vod_url
            and upgraded.upcoming_url == prev_cs.upcoming_url
            and upgraded.ended_at_source == prev_cs.ended_at_source
        ):
            return None
        return upgraded

    def _try_upgrade_twitch_offline_vod(
        self,
        entry: ChannelEntry,
        prev_cs: ChannelStatus,
        *,
        fetcher: Any | None,
        payload: OfflineInfo | None,
    ) -> ChannelStatus | None:
        """Retry Twitch ARCHIVE VOD link on stable offline polls."""
        if prev_cs.ended_at_source == "vod" and prev_cs.vod_url:
            return None
        if prev_cs.vod_url and not fetcher:
            return None
        confirmed = prev_cs.ended_at or _utc_now_iso()
        upgraded = self._build_twitch_offline_channel_status(
            entry,
            confirmed,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
        )
        if not upgraded.vod_url:
            return None
        if (
            upgraded.ended_at == prev_cs.ended_at
            and upgraded.vod_url == prev_cs.vod_url
            and upgraded.ended_at_source == prev_cs.ended_at_source
        ):
            return None
        return upgraded

    def _youtube_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        payload: OfflineInfo | None = None,
        *,
        fetcher: Any | None = None,
        extra_vod_url: str = "",
        channel_items: list[VideoItem] | None = None,
    ) -> ChannelStatus:
        """Build or preserve YouTube offline row state."""
        prev_cs = prev if isinstance(prev, ChannelStatus) else None
        if prev_cs and prev_cs.status is False:
            upgraded = self._try_upgrade_youtube_offline_vod(
                entry,
                prev_cs,
                fetcher=fetcher,
                payload=payload,
                channel_items=channel_items,
            )
            if upgraded is not None:
                return upgraded
            keep_upcoming = _youtube_upcoming_is_usable(prev_cs.scheduled_start)
            upcoming_url = prev_cs.upcoming_url if keep_upcoming else ""
            scheduled_start = prev_cs.scheduled_start if keep_upcoming else ""
            if channel_items is not None:
                fresh = self._find_youtube_upcoming_item(
                    entry, fetcher, channel_items=channel_items
                )
                if fresh:
                    upcoming_url = fresh.url
                    scheduled_start = fresh.scheduled_start
                elif not keep_upcoming:
                    upcoming_url = ""
                    scheduled_start = ""
            ended_at = prev_cs.ended_at
            source = prev_cs.ended_at_source or ""
            had_live = payload is not None or self._channel_had_live_session(
                entry.key
            )
            if source == "pending":
                ended_at = ""
                source = ""
            elif source == "confirmed" and not had_live:
                ended_at = ""
                source = ""
            return ChannelStatus(
                status=False,
                title=prev_cs.title or (payload.title if payload else ""),
                ended_at=ended_at,
                vod_url=prev_cs.vod_url or extra_vod_url,
                upcoming_url=upcoming_url,
                url=_channel_home_url(entry),
                ended_at_source=source,
                scheduled_start=scheduled_start,
            )

        had_recent_live = payload is not None or (
            prev_cs is not None and prev_cs.status is True
        )
        confirmed = _utc_now_iso() if had_recent_live else ""
        return self._build_youtube_offline_channel_status(
            entry,
            confirmed,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
            extra_vod_url=extra_vod_url,
            channel_items=channel_items,
        )

    def _twitch_offline_status_for(
        self,
        entry: ChannelEntry,
        prev: Any,
        payload: OfflineInfo | None = None,
        *,
        fetcher: Any | None = None,
    ) -> ChannelStatus:
        """Build or preserve Twitch offline row state (VOD link only)."""
        prev_cs = prev if isinstance(prev, ChannelStatus) else None
        if prev_cs and prev_cs.status is False:
            upgraded = self._try_upgrade_twitch_offline_vod(
                entry, prev_cs, fetcher=fetcher, payload=payload
            )
            if upgraded is not None:
                return upgraded
            ended_at = prev_cs.ended_at
            source = prev_cs.ended_at_source or ""
            if source == "pending":
                ended_at = ""
                source = ""
            elif not prev_cs.vod_url and source == "confirmed":
                ended_at = ""
                source = ""
            return ChannelStatus(
                status=False,
                title=prev_cs.title or (payload.title if payload else ""),
                ended_at=ended_at,
                vod_url=prev_cs.vod_url,
                upcoming_url="",
                url=_channel_home_url(entry),
                ended_at_source=source,
                scheduled_start="",
            )

        confirmed = _utc_now_iso()
        return self._build_twitch_offline_channel_status(
            entry,
            confirmed,
            fetcher=fetcher,
            payload=payload,
            prev_cs=prev_cs,
        )

    def _maybe_log_stable_twitch_status(
        self, entry: ChannelEntry, info: StreamInfo, prev_status: Any
    ) -> None:
        """Caller holds ``_lock``. Periodic log when Twitch reading is unchanged."""
        api_live = info.is_live
        unchanged = (prev_status is True and api_live) or (
            prev_status is not True and not api_live
        )
        if not unchanged:
            self._stable_status_polls.pop(entry.key, None)
            return
        count = self._stable_status_polls.get(entry.key, 0) + 1
        self._stable_status_polls[entry.key] = count
        if count % _STABLE_STATUS_LOG_EVERY != 0:
            return
        last = self._last_status.get(entry.key)
        last_repr = last.status if isinstance(last, ChannelStatus) else last
        logger.info(
            "Twitch %s: stable poll #%d api_live=%s last_status=%s",
            entry.key,
            count,
            api_live,
            last_repr,
        )
        if not api_live and isinstance(last, ChannelStatus) and last.status is False:
            fetcher = _monitor_deps.get_fetcher(entry.platform)
            upgraded = self._try_upgrade_twitch_offline_vod(
                entry, last, fetcher=fetcher, payload=None
            )
            if upgraded is not None:
                self._last_status[entry.key] = upgraded
                logger.info(
                    "Twitch %s: upgraded offline VOD url=%s ended_at_source=%s",
                    entry.key,
                    upgraded.vod_url,
                    upgraded.ended_at_source,
                )
