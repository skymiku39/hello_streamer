"""Poll-cycle orchestration: tier-1 probes, tier-2 refresh, dispatch."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor.probes import get_platform_probe
from stream_monitor.monitor.types import (
    _POST_RESUME_GAP_MULTIPLIER,
    _YOUTUBE_MAX_CONCURRENT,
    ChannelEntry,
    _ProbeSnapshot,
    poll_rest_overshoot_seconds,
    split_platform_entries,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class PollCycleMixin:
    """Runs one full poll cycle and the tier-1/tier-2 probe orchestration."""

    def _run_concurrent_pool(
        self,
        entries: list[ChannelEntry],
        work_fn: Callable[[ChannelEntry], _T],
        *,
        max_concurrent: int,
    ) -> list[_T]:
        """Run work on a fixed-size pool (sequential when max_concurrent is 1)."""
        if not entries:
            return []
        results: list[_T] = []
        if max_concurrent <= 1 or len(entries) <= 1:
            for entry in entries:
                if self._stop_event.is_set():
                    break
                try:
                    results.append(work_fn(entry))
                except Exception:
                    logger.exception(
                        "Concurrent pool work failed for %s, skipping", entry.key
                    )
            return results

        workers = min(max_concurrent, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work_fn, entry) for entry in entries]
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    break
                try:
                    results.append(future.result())
                except Exception:
                    logger.exception(
                        "Concurrent pool work failed for a channel, skipping"
                    )
        return results

    def _run_priority_pool(
        self,
        entries: list[ChannelEntry],
        work_fn: Callable[[ChannelEntry], _T],
        *,
        pool_tag: str = "pool",
    ) -> list[_T]:
        """Shared worker pool: one YouTube wave + parallel Twitch fillers.

        Primary: at most one YouTube probe at a time; remaining workers drain
        the Twitch queue (e.g. YT1 + TW1/TW2/TW3 together, then YT2).
        Secondary: ▲▼ list order within each platform queue.
        """
        if not entries:
            return []
        youtube_pending, twitch_pending = split_platform_entries(entries)
        youtube_pending = list(youtube_pending)
        twitch_pending = list(twitch_pending)
        if len(entries) == 1:
            try:
                return [work_fn(entries[0])]
            except Exception:
                logger.exception(
                    "Priority pool work failed for %s, skipping", entries[0].key
                )
                return []

        results: list[_T] = []
        results_lock = threading.Lock()
        state_lock = threading.Lock()
        youtube_active = 0
        cond = threading.Condition(state_lock)
        def _claim() -> ChannelEntry | None:
            nonlocal youtube_active
            if (
                youtube_active < _YOUTUBE_MAX_CONCURRENT
                and youtube_pending
            ):
                entry = youtube_pending.pop(0)
                youtube_active += 1
            elif twitch_pending:
                entry = twitch_pending.pop(0)
            else:
                return None
            return entry

        def _release(entry: ChannelEntry) -> None:
            nonlocal youtube_active
            if entry.platform == "youtube":
                youtube_active = max(0, youtube_active - 1)

        def worker() -> None:
            while not self._stop_event.is_set():
                with cond:
                    while True:
                        entry = _claim()
                        if entry is not None:
                            break
                        if not youtube_pending and not twitch_pending:
                            return
                        cond.wait(timeout=0.25)
                try:
                    item = work_fn(entry)
                    with results_lock:
                        results.append(item)
                except Exception:
                    logger.exception(
                        "Priority pool work failed for %s, skipping", entry.key
                    )
                finally:
                    with cond:
                        _release(entry)
                        cond.notify_all()

        workers = min(self._max_concurrent, len(entries))
        threads = [
            threading.Thread(target=worker, daemon=True) for _ in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

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
            elapsed = self._run_wake_verification(enabled_entries, poll_started)
            return self._maybe_run_startup_refresh(
                enabled_entries, poll_started, elapsed
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
        logger.info(
            "Poll tier-1 start: enabled=%d youtube=%d twitch=%d "
            "pool=%d youtube_concurrent=%d order=platform_wave",
            len(enabled_entries),
            youtube_count,
            twitch_count,
            self._max_concurrent,
            _YOUTUBE_MAX_CONCURRENT,
        )
        went_live_count = self._tier1_probe_entries(enabled_entries)

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        tier1_elapsed = time.monotonic() - tier1_started
        logger.info("Poll tier-1 done: %.2fs", tier1_elapsed)

        logger.info("Poll tier-2 start: enabled=%d", len(enabled_entries))
        commits.extend(
            self._run_priority_pool(
                enabled_entries, self._refresh_details, pool_tag="tier2"
            )
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
        return self._maybe_run_startup_refresh(
            enabled_entries, poll_started, elapsed
        )

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

    def _tier1_probe_entries(
        self,
        entries: list[ChannelEntry],
    ) -> int:
        """Run tier-1 probes; dispatch went-live as each probe finishes."""
        went_live_count = 0
        if not entries:
            return went_live_count

        dispatch_lock = threading.Lock()

        def probe_and_dispatch(entry: ChannelEntry) -> None:
            nonlocal went_live_count
            events = self._probe_live(entry)
            with dispatch_lock:
                went_live_count += self._dispatch_went_live_events(events)

        self._run_priority_pool(entries, probe_and_dispatch, pool_tag="tier1")
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
