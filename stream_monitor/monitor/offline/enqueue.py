"""Commit live->offline edges and queue went_offline events."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _ProbeSnapshot,
)

logger = logging.getLogger(__name__)


class OfflineEnqueueMixin:
    """Drives the offline commit path: prepare, build, finalize, enqueue."""

    def _offline_payload_for(
        self, entry: ChannelEntry, live_key: str, prev: Any
    ) -> OfflineInfo:
        stale_payload = self._live_payload.get(live_key)
        if stale_payload is not None:
            return stale_payload
        return OfflineInfo(
            url=(prev.url if isinstance(prev, ChannelStatus) else ""),
            title=(prev.title if isinstance(prev, ChannelStatus) else ""),
            platform=entry.platform,
            name=entry.name,
            display_name=self._display_names.get(entry.key, ""),
        )

    def _prepare_twitch_went_offline(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev: Any,
    ) -> OfflineInfo | None:
        """Pop live state for went_offline. Caller holds ``_lock``."""
        if self._wake_verify_mode:
            return None
        payload = self._live_payload.pop(live_key, None) or self._offline_payload_for(
            entry, live_key, prev
        )
        self._live_started_at.pop(live_key, None)
        self._live_platform_started_at.discard(live_key)
        self._twitch_seen_live.add(entry.key)
        return payload

    def _finalize_twitch_went_offline(
        self,
        entry: ChannelEntry,
        payload: OfflineInfo,
        offline_cs: ChannelStatus,
        *,
        label: str,
    ) -> None:
        """Apply went_offline after VOD lookup. Caller holds ``_lock``."""
        self._last_status[entry.key] = offline_cs
        self._pending_offline_events.append((entry, payload))
        logger.info("%s %s: went_offline", label, entry.key)

    def _enqueue_twitch_went_offline(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev: Any,
        *,
        label: str,
        fetcher: Any | None = None,
    ) -> None:
        """Commit twitch channel to offline and queue went_offline."""
        with self._lock:
            payload = self._prepare_twitch_went_offline(entry, live_key, prev)
        if payload is None:
            return
        offline_cs = self._twitch_offline_status_for(
            entry, prev, payload, fetcher=fetcher
        )
        with self._lock:
            self._finalize_twitch_went_offline(
                entry, payload, offline_cs, label=label
            )

    def _prepare_youtube_fallback_went_offline(
        self, entry: ChannelEntry, prev: Any
    ) -> tuple[OfflineInfo | None, list[OfflineInfo]]:
        """Sweep payloads for went_offline. Caller holds ``_lock``."""
        if self._wake_verify_mode:
            return None, []
        payloads_to_emit: list[OfflineInfo] = []
        for k in list(self._live_payload.keys()):
            if _entry_key_from_live_cache_key(k) == entry.key:
                popped = self._live_payload.pop(k, None)
                if popped is not None:
                    payloads_to_emit.append(popped)
        for k in list(self._offline_strikes.keys()):
            if _entry_key_from_live_cache_key(k) == entry.key:
                self._offline_strikes.pop(k, None)
        if not payloads_to_emit:
            payloads_to_emit.append(self._offline_payload_for(
                entry, _live_cache_key(entry.key), prev
            ))
        self._live_started_at = {
            key: value
            for key, value in self._live_started_at.items()
            if _entry_key_from_live_cache_key(key) != entry.key
        }
        self._live_platform_started_at = {
            key
            for key in self._live_platform_started_at
            if _entry_key_from_live_cache_key(key) != entry.key
        }
        self._fallback_triggered_live.pop(entry.key, None)
        primary = payloads_to_emit[0] if payloads_to_emit else None
        return primary, payloads_to_emit

    def _finalize_youtube_fallback_went_offline(
        self,
        entry: ChannelEntry,
        offline_cs: ChannelStatus,
        payloads_to_emit: list[OfflineInfo],
        *,
        label: str,
    ) -> None:
        """Apply YouTube went_offline after VOD lookup. Caller holds ``_lock``."""
        self._last_status[entry.key] = offline_cs
        for payload in payloads_to_emit:
            self._pending_offline_events.append((entry, payload))
        logger.info("%s %s: went_offline", label, entry.key)

    def _enqueue_youtube_fallback_went_offline(
        self, entry: ChannelEntry, prev: Any, *, label: str
    ) -> None:
        """Sweep all payloads for this channel and queue went_offline."""
        with self._lock:
            primary, payloads_to_emit = self._prepare_youtube_fallback_went_offline(
                entry, prev
            )
        if not payloads_to_emit:
            return
        yt_fetcher = _monitor_deps.get_fetcher(entry.platform)
        offline_cs = self._youtube_offline_status_for(
            entry, prev, primary, fetcher=yt_fetcher
        )
        with self._lock:
            self._finalize_youtube_fallback_went_offline(
                entry, offline_cs, payloads_to_emit, label=label
            )

    def _handle_fetch_unavailable(
        self,
        entry: ChannelEntry,
        *,
        label: str,
        snap: _ProbeSnapshot | None = None,
        reason: str = "fetch returned None",
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        """Preserve last-known state, visibly unverified, without an offline edge.

        A timeout, parse error, or exception is not evidence that a broadcast
        ended. Only successful platform responses can consume offline strikes.
        """
        with self._lock:
            previous = self._last_status.get(entry.key)
            status = previous if isinstance(previous, ChannelStatus) else ChannelStatus(
                status=previous, url=_channel_home_url(entry)
            )
            self._last_status[entry.key] = replace(status, unavailable=True)
            if snap is not None:
                self._probe_snapshots[entry.key] = snap
        logger.warning(
            "%s %s: %s, last-known status=%s unavailable (no offline strike)",
            label, entry.key, reason, status.status,
        )
        self._publish_channel_preview(entry)
        return []
