"""YouTube tier-1 / tier-2 probe strategy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.probes.host import ProbeHost
from stream_monitor.monitor.types import (
    _OFFLINE_STRIKE_THRESHOLD,
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _ProbeSnapshot,
    _sort_datetime,
    _utc_now_iso,
    _video_item_to_stream_info,
    _youtube_upcoming_is_usable,
)

logger = logging.getLogger(__name__)


class YouTubePlatformProbe:
    """YouTube live probe, tier-2 refresh, and TIDUS-empty fallback path."""

    platform = "youtube"

    def probe_live(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            fetcher = _monitor_deps.get_fetcher(entry.platform)
            items = fetcher.get_channel_items(entry.name, fill_timing=False)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return host._handle_fetch_unavailable(
                entry, label="YouTube", snap=snap, reason="fetch exception"
            )

        if items is None:
            return host._handle_fetch_unavailable(
                entry, label="YouTube", snap=snap
            )

        snap.fetcher = fetcher
        snap.youtube_items = items
        with host._lock:
            host._probe_snapshots[entry.key] = snap
        host._publish_channel_preview(entry, from_probe=True)
        if not items:
            with host._lock:
                prev = host._last_status.get(entry.key)
                prev_status = (
                    prev.status if isinstance(prev, ChannelStatus) else prev
                )
                was_fallback_live = entry.key in host._fallback_triggered_live
            if (
                was_fallback_live
                and prev_status is True
                and not host._wake_verify_mode
            ):
                live_key = _live_cache_key(entry.key)
                with host._lock:
                    miss = host._record_offline_miss(
                        entry,
                        live_key,
                        prev_status,
                        label="YouTube",
                        reason="empty tidus feed",
                    )
                if miss == "hold":
                    snap.youtube_fallback = True
                    snap.youtube_fallback_hold = True
                    return []
            snap.youtube_fallback = True
            return self._probe_fallback_live(host, entry, fetcher, snap)

        new_events: list[tuple[ChannelEntry, StreamInfo]] = []
        live_items: list[VideoItem] = []
        with host._lock:
            is_baselined = entry.key in host._youtube_baselined
            fallback_title = host._fallback_triggered_live.get(entry.key)

        unseen_live_items: list[VideoItem] = []
        pending_seen: list[tuple[str, str, str, str, str]] = []

        for item in items:
            if item.style == "LIVE":
                live_key = _live_cache_key(entry.key, item.video_id)
                with host._lock:
                    if item.started_at:
                        host._live_started_at[live_key] = item.started_at
                        host._live_platform_started_at.add(live_key)
                    host._live_payload[live_key] = OfflineInfo(
                        url=item.url,
                        title=item.title,
                        platform=entry.platform,
                        name=entry.name,
                        video_id=item.video_id,
                        display_name=item.display_name or "",
                    )
                live_items.append(item)

            if item.display_name:
                with host._lock:
                    host._display_names[entry.key] = item.display_name

            if item.style not in {"LIVE", "UPCOMING"}:
                continue

            try:
                if host._db.is_seen(item.video_id, item.style):
                    continue
            except Exception:
                logger.exception("DB error for video %s", item.video_id)
                continue

            if item.style == "UPCOMING" and not _youtube_upcoming_is_usable(
                item.scheduled_start
            ):
                continue

            pending_seen.append(
                (item.video_id, "youtube", entry.name, item.style, item.title)
            )

            if item.style == "LIVE":
                unseen_live_items.append(item)
            elif item.style == "UPCOMING" and is_baselined:
                info = _video_item_to_stream_info(item, entry.name)
                new_events.append((entry, info))

        for item in items:
            if item.style != "DEFAULT":
                continue
            try:
                if host._db.is_seen(item.video_id, item.style):
                    continue
            except Exception:
                logger.exception("DB error for video %s", item.video_id)
                continue
            pending_seen.append(
                (item.video_id, "youtube", entry.name, item.style, item.title)
            )

        if fallback_title is not None and unseen_live_items:
            suppressed_idx = -1
            for i, li in enumerate(unseen_live_items):
                if li.title == fallback_title:
                    suppressed_idx = i
                    break
            else:
                if not fallback_title and len(unseen_live_items) == 1:
                    suppressed_idx = 0
            if suppressed_idx >= 0:
                unseen_live_items.pop(suppressed_idx)
            with host._lock:
                host._fallback_triggered_live.pop(entry.key, None)

        for item in unseen_live_items:
            info = _video_item_to_stream_info(item, entry.name)
            new_events.append((entry, info))

        snap.youtube_pending_seen = pending_seen
        with host._lock:
            host._probe_snapshots[entry.key] = snap
        if host._wake_verify_mode:
            return []
        return new_events

    def refresh_details(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot
    ) -> Callable[[], None]:
        """Tier 2: LIVE row, strike-hold, or OFFLINE (+ optional upcoming_url)."""
        if snap.youtube_fallback:
            self._refresh_fallback(host, entry, snap)
            return host._noop_commit

        fetcher = snap.fetcher
        items = snap.youtube_items
        if fetcher is None or items is None:
            return host._noop_commit

        live_items = [item for item in items if item.style == "LIVE"]
        has_live = bool(live_items)

        with host._lock:
            prev_cs = host._last_status.get(entry.key)
            prev_channel = (
                prev_cs if isinstance(prev_cs, ChannelStatus) else None
            )

        live_stable = False
        if has_live:
            live_item = min(
                live_items,
                key=lambda item: _sort_datetime(
                    item.started_at, datetime.now(timezone.utc)
                ),
            )
            live_stable = host._youtube_live_row_is_stable(
                entry, prev_channel, live_item
            )
            if (
                not live_stable
                and not live_item.started_at
                and not fetcher.http_backoff_active()
            ):
                fetcher.enrich_live_for_details(items)
        elif (
            not fetcher.http_backoff_active()
            and not (
                prev_channel is not None
                and host._youtube_offline_row_is_stable(prev_channel, items)
            )
            and any(
                item.style == "UPCOMING" and not item.scheduled_start
                for item in items
            )
        ):
            fetcher.enrich_upcoming_for_details(items)

        live_status: ChannelStatus | None = None
        if has_live:
            live_status = ChannelStatus(
                status=True,
                url=live_item.url,
                title=live_item.title,
                started_at=host._resolve_youtube_live_started_at(
                    entry, live_item
                ),
            )

        prev_offline: Any = None
        offline_extra_vod = ""
        offline_payload: OfflineInfo | None = None
        need_offline_status = False
        has_strike_pending = False
        active_live_ids: set[str] = set()

        with host._lock:
            observed_live_ids = {item.video_id for item in live_items}

            if has_live:
                fallback_alias_key = _live_cache_key(entry.key)
                host._live_payload.pop(fallback_alias_key, None)
                host._offline_strikes.pop(fallback_alias_key, None)
                host._fallback_triggered_live.pop(entry.key, None)

            active_live_ids = set(observed_live_ids)
            stale_candidates = [
                key
                for key in list(host._live_payload.keys())
                if _entry_key_from_live_cache_key(key) == entry.key
                and key.rsplit("|", 1)[-1] not in active_live_ids
            ]
            for stale_key in stale_candidates:
                strikes = host._offline_strikes.get(stale_key, 0) + 1
                if strikes < _OFFLINE_STRIKE_THRESHOLD:
                    host._offline_strikes[stale_key] = strikes
                    active_live_ids.add(stale_key.rsplit("|", 1)[-1])
                    has_strike_pending = True
                    logger.info(
                        "YouTube %s: ignoring transient missing video %s (%d/%d)",
                        entry.key,
                        stale_key.rsplit("|", 1)[-1],
                        strikes,
                        _OFFLINE_STRIKE_THRESHOLD,
                    )
                    continue
                host._offline_strikes.pop(stale_key, None)
                payload = host._live_payload.pop(stale_key, None)
                if payload is not None:
                    host._pending_offline_events.append((entry, payload))
                    offline_payload = payload

            for vid in observed_live_ids:
                host._offline_strikes.pop(_live_cache_key(entry.key, vid), None)

            if has_live:
                host._live_started_at = {
                    key: value
                    for key, value in host._live_started_at.items()
                    if (
                        _entry_key_from_live_cache_key(key) != entry.key
                        or key.rsplit("|", 1)[-1] in active_live_ids
                    )
                }
                host._live_platform_started_at = {
                    key
                    for key in host._live_platform_started_at
                    if (
                        _entry_key_from_live_cache_key(key) != entry.key
                        or key.rsplit("|", 1)[-1] in active_live_ids
                    )
                }
            elif has_strike_pending:
                pass
            elif items:
                host._live_started_at = {
                    key: value
                    for key, value in host._live_started_at.items()
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                host._live_platform_started_at = {
                    key
                    for key in host._live_platform_started_at
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                offline_extra_vod = next(
                    (item.url for item in items if item.style == "DEFAULT"),
                    "",
                )
                prev_offline = host._last_status.get(entry.key)
                need_offline_status = True

            host._youtube_baselined.add(entry.key)

        if live_status is not None:
            with host._lock:
                host._last_status[entry.key] = live_status
                if live_status.started_at:
                    for item in live_items:
                        if item.url == live_status.url:
                            host._live_started_at[
                                _live_cache_key(entry.key, item.video_id)
                            ] = live_status.started_at
                            break
        elif need_offline_status:
            if (
                isinstance(prev_offline, ChannelStatus)
                and prev_offline.status is False
                and host._youtube_offline_row_is_stable(prev_offline, items)
            ):
                fresh = host._find_youtube_upcoming_item(
                    entry, fetcher, channel_items=items
                )
                upcoming_url = fresh.url if fresh else ""
                scheduled_start = fresh.scheduled_start if fresh else ""
                if (
                    upcoming_url == (prev_offline.upcoming_url or "")
                    and scheduled_start == (prev_offline.scheduled_start or "")
                ):
                    offline_status = prev_offline
                else:
                    offline_status = ChannelStatus(
                        status=False,
                        title=prev_offline.title,
                        ended_at=prev_offline.ended_at,
                        vod_url=prev_offline.vod_url,
                        upcoming_url=upcoming_url,
                        url=_channel_home_url(entry),
                        ended_at_source=prev_offline.ended_at_source,
                        scheduled_start=scheduled_start,
                    )
            else:
                offline_status = host._youtube_offline_status_for(
                    entry,
                    prev_offline,
                    offline_payload,
                    fetcher=fetcher,
                    extra_vod_url=offline_extra_vod,
                    channel_items=items,
                )
            with host._lock:
                host._last_status[entry.key] = offline_status

        pending_seen = snap.youtube_pending_seen or []

        def commit() -> None:
            for args in pending_seen:
                try:
                    host._db.mark_seen(*args)
                except Exception:
                    logger.exception("DB error for video %s", args[0])

        host._publish_channel_preview(entry, from_probe=False)
        return commit

    def _probe_fallback_live(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        fetcher: Any,
        snap: _ProbeSnapshot
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("Error fetching fallback status for %s", entry.key)
            return []

        if info is None:
            return host._handle_fetch_unavailable(
                entry, label="YouTube fallback"
            )

        live_key = _live_cache_key(entry.key)

        with host._lock:
            prev = host._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

            if not info.is_live and prev_status is True:
                miss = host._record_offline_miss(
                    entry,
                    live_key,
                    prev_status,
                    label="YouTube fallback",
                    reason="api reported offline",
                )
                if miss == "hold":
                    if info.display_name:
                        host._display_names[entry.key] = info.display_name
                    snap.youtube_fallback_info = info
                    snap.youtube_fallback_hold = True
                    return []
            else:
                host._offline_strikes.pop(live_key, None)

            if info.is_live:
                started_at = info.started_at or host._live_started_at.get(live_key)
                if not started_at:
                    started_at = _utc_now_iso()
                host._live_started_at[live_key] = started_at
                info.started_at = started_at
                host._last_status[entry.key] = ChannelStatus(
                    status=True,
                    url=info.url,
                    title=info.title,
                    started_at=started_at,
                )
                host._live_payload[live_key] = OfflineInfo(
                    url=info.url,
                    title=info.title,
                    platform=entry.platform,
                    name=entry.name,
                    display_name=info.display_name or "",
                )
            if info.display_name:
                host._display_names[entry.key] = info.display_name

        snap.youtube_fallback_info = info

        if host._wake_verify_mode:
            return []

        went_live = info.is_live and prev_status is not True
        if went_live:
            logger.info(
                "YouTube fallback %s: went_live title=%r url=%s",
                entry.key,
                info.title,
                info.url,
            )
            with host._lock:
                host._fallback_triggered_live[entry.key] = info.title
            info.stream_status = "live"
            return [(entry, info)]
        if info.is_live and prev_status is True:
            logger.info(
                "YouTube fallback %s: went_live_suppressed (already marked live)",
                entry.key,
            )
        return []

    def _refresh_fallback(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot
    ) -> None:
        info = snap.youtube_fallback_info
        fetcher = snap.fetcher
        if info is None or snap.youtube_fallback_hold:
            return

        with host._lock:
            prev = host._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

        went_offline = (
            (not info.is_live)
            and prev_status is True
        )
        if went_offline:
            host._enqueue_youtube_fallback_went_offline(
                entry, prev, label="YouTube fallback"
            )
            return

        if not info.is_live:
            if (
                info.stream_status == "upcoming"
                and _youtube_upcoming_is_usable(info.scheduled_start)
            ):
                upcoming_status = ChannelStatus(
                    status="upcoming",
                    url=info.url,
                    title=info.title,
                    scheduled_start=info.scheduled_start,
                )
                with host._lock:
                    host._live_started_at = {
                        key: value
                        for key, value in host._live_started_at.items()
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    host._live_platform_started_at = {
                        key
                        for key in host._live_platform_started_at
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    host._fallback_triggered_live.pop(entry.key, None)
                    host._last_status[entry.key] = upcoming_status
            else:
                offline_status = host._youtube_offline_status_for(
                    entry, prev, fetcher=fetcher
                )
                with host._lock:
                    host._live_started_at = {
                        key: value
                        for key, value in host._live_started_at.items()
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    host._live_platform_started_at = {
                        key
                        for key in host._live_platform_started_at
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    host._fallback_triggered_live.pop(entry.key, None)
                    host._last_status[entry.key] = offline_status
        host._publish_channel_preview(entry, from_probe=False)

    def finalize_tier1_probe(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> None:
        return None
