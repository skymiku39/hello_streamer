"""Poll-cycle orchestration: tier-1 probes, tier-2 refresh, dispatch."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor.probes import get_platform_probe
from stream_monitor.monitor.types import (
    _POST_RESUME_GAP_MULTIPLIER,
    ChannelEntry,
    _ProbeSnapshot,
    interleave_platform_entries,
    poll_rest_overshoot_seconds,
)

logger = logging.getLogger(__name__)


class PollCycleMixin:
    """Runs one full poll cycle and the tier-1/tier-2 probe orchestration."""

    def _should_run_wake_verification(self, wall_now: float) -> bool:
        """True when the host likely slept past the planned inter-poll rest."""
        if self._last_poll_wall_ended <= 0:
            return False
        overshoot = poll_rest_overshoot_seconds(
            wall_now,
            self._last_poll_wall_ended,
            self._last_poll_planned_rest,
        )
        grace_threshold = self._interval * _POST_RESUME_GAP_MULTIPLIER
        if overshoot > grace_threshold:
            logger.info(
                "wake_verify_scheduled: rest_overshoot=%.1fs > %.1fs "
                "(since_end=%.1fs planned_rest=%.1fs)",
                overshoot,
                grace_threshold,
                wall_now - self._last_poll_wall_ended,
                self._last_poll_planned_rest,
            )
            return True
        return False

    def _execute_poll_cycle(self, poll_started: float) -> float:
        wall_now = time.time()
        run_wake_verify = self._should_run_wake_verification(wall_now)
        self._last_poll_wall_started = wall_now

        self._poll_cycle += 1
        with self._lock:
            entries = list(self._entries)

        enabled_entries = [e for e in entries if e.enabled]

        if run_wake_verify and enabled_entries:
            return self._run_wake_verification(
                interleave_platform_entries(enabled_entries), poll_started
            )

        with self._lock:
            self._pending_offline_events.clear()
            self._probe_snapshots.clear()
        enabled_count = len(enabled_entries)
        offline_count = 0
        went_live_count = 0
        commits: list[Callable[[], None]] = []

        tier1_started = time.monotonic()
        youtube_count = sum(1 for e in enabled_entries if e.platform == "youtube")
        twitch_count = len(enabled_entries) - youtube_count
        tier1_order = interleave_platform_entries(enabled_entries)
        logger.info(
            "Poll tier-1 start: enabled=%d youtube=%d twitch=%d concurrent=%d",
            len(enabled_entries),
            youtube_count,
            twitch_count,
            self._max_concurrent,
        )
        went_live_count += self._tier1_probe_entries(tier1_order)

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        tier1_elapsed = time.monotonic() - tier1_started
        logger.info("Poll tier-1 done: %.2fs", tier1_elapsed)

        logger.info("Poll tier-2 start: enabled=%d", len(enabled_entries))
        tier2_order = interleave_platform_entries(enabled_entries)
        if (
            tier2_order
            and self._max_concurrent > 1
            and len(tier2_order) > 1
        ):
            workers = min(self._max_concurrent, len(tier2_order))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                refresh_futures = [
                    pool.submit(self._refresh_details, entry)
                    for entry in tier2_order
                ]
                for future in as_completed(refresh_futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        commits.append(future.result())
                    except Exception:
                        logger.exception(
                            "Tier-2 refresh failed for a channel, skipping"
                        )
        else:
            for entry in tier2_order:
                if self._stop_event.is_set():
                    break
                try:
                    commits.append(self._refresh_details(entry))
                except Exception:
                    logger.exception(
                        "Tier-2 refresh failed for %s, skipping", entry.key
                    )

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        tier2_elapsed = time.monotonic() - tier1_started - tier1_elapsed
        logger.info("Poll tier-2 done: %.2fs", tier2_elapsed)

        for commit in commits:
            if self._stop_event.is_set():
                break
            try:
                commit()
            except Exception:
                logger.exception("Tier-2 commit failed, skipping")

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        # Dispatch went-offline events *after* went-live so the UI sees
        # transitions in a sensible order if both occur in the same poll.
        with self._lock:
            offline_batch = list(self._pending_offline_events)
        offline_count = len(offline_batch)
        for entry, offline_info in offline_batch:
            if self._stop_event.is_set():
                break
            self._emit_went_offline(entry, offline_info)

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        self._emit_poll_complete()

        elapsed = time.monotonic() - poll_started
        with self._lock:
            snapshot_keys = len(self._last_status)
        logger.info(
            "Poll complete: enabled=%d went_live=%d went_offline=%d "
            "tier1=%.2fs total=%.2fs snapshot_keys=%d",
            enabled_count,
            went_live_count,
            offline_count,
            tier1_elapsed,
            elapsed,
            snapshot_keys,
        )
        if elapsed > self._interval:
            logger.warning(
                "Poll slower than interval: total=%.2fs interval=%ds",
                elapsed,
                self._interval,
            )
        self._run_maintenance()
        return elapsed

    def _check_channel(
        self, entry: ChannelEntry
    ) -> tuple[list[tuple[ChannelEntry, StreamInfo]], Callable[[], None]]:
        events = self._probe_live(entry)
        commit = self._refresh_details(entry)
        return events, commit

    def _dispatch_went_live_events(
        self, events: list[tuple[ChannelEntry, StreamInfo]]
    ) -> int:
        """Notify listeners as soon as tier-1 confirms a new live edge."""
        for entry, info in events:
            self._emit_went_live(entry, info)
        return len(events)

    def _tier1_probe_entries(self, entries: list[ChannelEntry]) -> int:
        """Run tier-1 probes for a batch (YouTube or Twitch).

        Dispatches went-live callbacks immediately as each probe finishes so
        the UI can open players without waiting for tier-2 detail refresh.
        """
        went_live_count = 0
        if not entries:
            return went_live_count
        if self._max_concurrent > 1 and len(entries) > 1:
            workers = min(self._max_concurrent, len(entries))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                probe_futures = [
                    pool.submit(self._probe_live, entry) for entry in entries
                ]
                for future in as_completed(probe_futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        went_live_count += self._dispatch_went_live_events(
                            future.result()
                        )
                    except Exception:
                        logger.exception(
                            "Tier-1 probe failed for a channel, skipping"
                        )
        else:
            for entry in entries:
                if self._stop_event.is_set():
                    break
                try:
                    went_live_count += self._dispatch_went_live_events(
                        self._probe_live(entry)
                    )
                except Exception:
                    logger.exception(
                        "Tier-1 probe failed for %s, skipping", entry.key
                    )
        return went_live_count

    def _notify_poll_activity(self, entry: ChannelEntry, phase: str) -> None:
        with self._lock:
            display_name = (self._display_names.get(entry.key) or "").strip()
        if not display_name:
            display_name = entry.name
        logger.debug("Poll activity: %s phase=%s", entry.key, phase)
        if self._event_bus is None:
            return
        self._emit_poll_activity(entry, phase, display_name)

    def _probe_live(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        self._notify_poll_activity(entry, "probe")
        snap = _ProbeSnapshot()
        probe = get_platform_probe(entry.platform)
        events = probe.probe_live(self._facade, entry, snap)
        probe.finalize_tier1_probe(self._facade, entry, snap)
        return events

    def _refresh_details(self, entry: ChannelEntry) -> Callable[[], None]:
        self._notify_poll_activity(entry, "refresh")
        with self._lock:
            snap = self._probe_snapshots.get(entry.key)
        if snap is None:
            snap = _ProbeSnapshot()
        return get_platform_probe(entry.platform).refresh_details(
            self._facade, entry, snap
        )
