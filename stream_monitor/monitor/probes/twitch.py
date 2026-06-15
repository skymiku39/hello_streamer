"""Twitch tier-1 / tier-2 probe strategy."""

from __future__ import annotations

import logging
from typing import Callable

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.probes.host import ProbeHost
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _live_cache_key,
    _ProbeSnapshot,
    _utc_now_iso,
)

logger = logging.getLogger(__name__)


class TwitchPlatformProbe:
    platform = "twitch"

    def probe_live(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        with host._lock:
            prev = host._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

        try:
            fetcher = _monitor_deps.get_fetcher(entry.platform)
            info = fetcher.get_stream_info(entry.name)
            if info is not None and not info.is_live and prev_status is True:
                retry = fetcher.get_stream_info(entry.name)
                if retry is not None:
                    info = retry
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return host._handle_fetch_unavailable(
                entry, label="Twitch", snap=snap, reason="fetch exception"
            )

        if info is None:
            return host._handle_fetch_unavailable(
                entry, label="Twitch", snap=snap
            )

        live_key = _live_cache_key(entry.key)

        with host._lock:
            prev = host._last_status.get(entry.key)
            prev_cs = prev if isinstance(prev, ChannelStatus) else None
            prev_status = prev_cs.status if prev_cs is not None else prev

            if not info.is_live and prev_status is True:
                miss = host._record_offline_miss(
                    entry,
                    live_key,
                    prev_status,
                    label="Twitch",
                    reason="api reported offline",
                )
                if miss == "hold":
                    if info.display_name:
                        host._display_names[entry.key] = info.display_name
                    snap.twitch_info = info
                    snap.twitch_offline_hold = True
                    snap.fetcher = fetcher
                    return []
            else:
                host._offline_strikes.pop(live_key, None)

            if info.is_live:
                host._twitch_seen_live.add(entry.key)
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

        snap.twitch_info = info
        snap.fetcher = fetcher

        if host._wake_verify_mode:
            return []

        went_live = info.is_live and prev_status is not True
        if went_live:
            logger.info(
                "Twitch %s: went_live title=%r url=%s",
                entry.key,
                info.title,
                info.url,
            )
            info.stream_status = "live"
            return [(entry, info)]
        if info.is_live and prev_status is True:
            logger.info(
                "Twitch %s: went_live_suppressed (already marked live)",
                entry.key,
            )
        return []

    def refresh_details(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> Callable[[], None]:
        fetcher = snap.fetcher
        if snap.twitch_offline_commit:
            live_key = _live_cache_key(entry.key)
            with host._lock:
                prev = host._last_status.get(entry.key)
            host._enqueue_twitch_went_offline(
                entry,
                live_key,
                prev,
                label="Twitch",
                fetcher=fetcher,
            )
            return host._noop_commit

        info = snap.twitch_info
        if info is None:
            return host._noop_commit

        live_key = _live_cache_key(entry.key)
        with host._lock:
            prev = host._last_status.get(entry.key)
            prev_cs = prev if isinstance(prev, ChannelStatus) else None
            prev_status = prev_cs.status if prev_cs is not None else prev

        went_offline = (
            (not info.is_live)
            and prev_status is True
            and not snap.twitch_offline_hold
        )
        if went_offline:
            host._enqueue_twitch_went_offline(
                entry,
                live_key,
                prev,
                label="Twitch",
                fetcher=fetcher,
            )
        elif not info.is_live and prev_status is not True:
            offline_cs = host._twitch_offline_status_for(
                entry,
                prev,
                fetcher=fetcher,
            )
            with host._lock:
                host._last_status[entry.key] = offline_cs

        with host._lock:
            host._maybe_log_stable_twitch_status(entry, info, prev_status)
        host._publish_channel_preview(entry, from_probe=False)
        return host._noop_commit

    def finalize_tier1_probe(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> None:
        with host._lock:
            host._probe_snapshots[entry.key] = snap
        host._publish_channel_preview(entry, from_probe=True)
