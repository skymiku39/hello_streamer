"""Wake verification: an extra confirm-or-defer poll after a long pause."""

from __future__ import annotations

import logging
import time
from typing import Any

from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.types import (
    _YOUTUBE_MAX_CONCURRENT,
    ChannelEntry,
    ChannelStatus,
    _youtube_upcoming_is_usable,
    split_platform_entries,
)

logger = logging.getLogger(__name__)


class WakeVerifyMixin:
    """Reconciles cached vs freshly-probed status before trusting edges post-sleep."""

    @staticmethod
    def _status_bucket(status: Any) -> str:
        if status is True or status == "live":
            return "live"
        if status == "upcoming":
            return "upcoming"
        return "offline"

    def _cached_status_bucket(self, entry: ChannelEntry) -> str:
        with self._lock:
            prev = self._last_status.get(entry.key)
        if prev is None:
            return "offline"
        if isinstance(prev, ChannelStatus):
            # TIDUS stores schedulable waiting rooms on the offline row
            # (status=False + upcoming_url); fallback uses status="upcoming".
            # Align buckets so wake verification does not defer a stable row.
            if (
                prev.status is False
                and prev.upcoming_url
                and _youtube_upcoming_is_usable(prev.scheduled_start)
            ):
                return "upcoming"
            return self._status_bucket(prev.status)
        return self._status_bucket(prev)

    def _probe_channel_status(self, entry: ChannelEntry) -> str | None:
        """Lightweight live/offline/upcoming probe for wake verification."""
        try:
            fetcher = _monitor_deps.get_fetcher(entry.platform)
        except Exception:
            logger.exception("wake_verify: no fetcher for %s", entry.key)
            return None

        if entry.platform == "twitch":
            try:
                info = fetcher.get_stream_info(entry.name)
            except Exception:
                logger.exception("wake_verify: fetch error for %s", entry.key)
                return None
            if info is None:
                return None
            return "live" if info.is_live else "offline"

        try:
            items = fetcher.get_channel_items(entry.name, fill_timing=False)
        except Exception:
            logger.exception("wake_verify: fetch error for %s", entry.key)
            return None
        if items is None:
            return None
        if any(item.style == "LIVE" for item in items):
            return "live"
        upcoming = self._pick_youtube_upcoming_from_items(items)
        if upcoming is not None:
            return "upcoming"
        if items:
            return "offline"
        try:
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("wake_verify: fallback error for %s", entry.key)
            return None
        if info is None:
            return None
        if info.is_live:
            return "live"
        if (
            info.stream_status == "upcoming"
            and _youtube_upcoming_is_usable(info.scheduled_start)
        ):
            return "upcoming"
        return "offline"

    def _run_wake_verification(
        self,
        enabled_entries: list[ChannelEntry],
        poll_started: float,
    ) -> float:
        """Extra poll after wake: refresh when API agrees with cache, else defer."""
        self._wake_verify_active = True
        self._wake_verify_mode = True
        confirmed = 0
        deferred = 0
        try:
            with self._lock:
                self._pending_offline_events.clear()
                self._probe_snapshots.clear()

            observed_by_key: dict[str, str | None] = {}
            youtube_entries, twitch_entries = split_platform_entries(
                enabled_entries
            )

            def record_status(entry: ChannelEntry) -> None:
                try:
                    observed_by_key[entry.key] = self._probe_channel_status(
                        entry
                    )
                except Exception:
                    logger.exception(
                        "wake_verify: status probe failed for %s", entry.key
                    )
                    observed_by_key[entry.key] = None

            if youtube_entries:
                self._run_concurrent_pool(
                    youtube_entries,
                    record_status,
                    max_concurrent=_YOUTUBE_MAX_CONCURRENT,
                )
            if not self._stop_event.is_set() and twitch_entries:
                self._run_concurrent_pool(
                    twitch_entries,
                    record_status,
                    max_concurrent=self._max_concurrent,
                )

            for entry in enabled_entries:
                if self._stop_event.is_set():
                    break
                cached = self._cached_status_bucket(entry)
                observed = observed_by_key.get(entry.key)
                if observed is None:
                    deferred += 1
                    logger.info(
                        "wake_verify_deferred %s: fetch unavailable "
                        "cached=%s",
                        entry.key,
                        cached,
                    )
                    continue
                if observed != cached:
                    deferred += 1
                    logger.info(
                        "wake_verify_deferred %s: mismatch cached=%s "
                        "observed=%s",
                        entry.key,
                        cached,
                        observed,
                    )
                    continue

                confirmed += 1
                logger.info(
                    "wake_verify_confirmed %s: cached=%s observed=%s",
                    entry.key,
                    cached,
                    observed,
                )
                try:
                    self._probe_live(entry)
                    commit = self._refresh_details(entry)
                    commit()
                except Exception:
                    logger.exception(
                        "wake_verify refresh failed for %s", entry.key
                    )

            self._emit_poll_complete()
        finally:
            self._wake_verify_mode = False
            self._wake_verify_active = False

        elapsed = time.monotonic() - poll_started
        logger.info(
            "Wake verify complete: enabled=%d confirmed=%d deferred=%d "
            "total=%.2fs",
            len(enabled_entries),
            confirmed,
            deferred,
            elapsed,
        )
        return elapsed
