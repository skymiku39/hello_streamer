"""Background polling scheduler and platform probe orchestration."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from stream_monitor.db import SeenVideoDB
from stream_monitor.events import (
    ChannelWentLive,
    ChannelWentOffline,
    MonitorEventBus,
    PartialStatusUpdate,
    PollActivity,
    PollStatusUpdate,
    PollWaiting,
)
from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.probes import get_platform_probe
from stream_monitor.monitor.types import (
    _DB_CLEANUP_DAYS,
    _DEFAULT_MAX_CONCURRENT,
    _MAINTENANCE_INTERVAL_S,
    _MIN_POLL_REST_S,
    _OFFLINE_STRIKE_THRESHOLD,
    _POST_RESUME_GAP_MULTIPLIER,
    _STABLE_STATUS_LOG_EVERY,
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _merge_offline_ended_at,
    _ProbeSnapshot,
    _sort_datetime,
    _utc_now_iso,
    _youtube_upcoming_is_usable,
)

logger = logging.getLogger(__name__)

class Monitor:
    """Polls a list of channels in a background thread."""

    def __init__(
        self,
        channels: list[dict[str, str]],
        interval: int = 60,
        db: SeenVideoDB | None = None,
        event_bus: MonitorEventBus | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._entries = [
            ChannelEntry(
                platform=ch["platform"],
                name=ch["name"],
                enabled=ch.get("enabled", True),
                monitor_only=bool(ch.get("monitor_only", False)),
            )
            for ch in channels
        ]
        self._interval = max(10, interval)
        self._max_concurrent = max(1, max_concurrent)
        self._event_bus = event_bus
        self._db = db or SeenVideoDB()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status: dict[str, Any] = {}
        self._display_names: dict[str, str] = {}
        self._youtube_baselined: set[str] = set()
        self._fallback_triggered_live: dict[str, str] = {}
        self._live_started_at: dict[str, str] = {}
        # LIVE cache keys whose started_at came from YouTube feed/watch (not session fallback).
        self._live_platform_started_at: set[str] = set()
        # Map of "<entry.key>|<video_id>" -> last-known url/title pair while
        # the stream was live. Used to fire went-offline events with the
        # exact URL/title we originally opened, even after live data is gone.
        self._live_payload: dict[str, OfflineInfo] = {}
        # Twitch channels we have seen live this session — offline UI only after
        # a confirmed live→offline edge, not on cold-start "already offline".
        self._twitch_seen_live: set[str] = set()
        # Consecutive-miss counter per live_cache_key. Incremented every poll a
        # previously-live entry/video appears "not live"; reset when it comes
        # back. Only when the counter reaches _OFFLINE_STRIKE_THRESHOLD do we
        # commit to the offline edge (clear last_status, emit went_offline).
        self._offline_strikes: dict[str, int] = {}
        # Filled by _check_* helpers within a single poll cycle; drained by
        # _run after went_live dispatch.
        self._pending_offline_events: list[tuple[ChannelEntry, OfflineInfo]] = []
        self._lock = threading.Lock()
        self._poll_cycle = 0
        self._stable_status_polls: dict[str, int] = {}
        self._probe_snapshots: dict[str, _ProbeSnapshot] = {}
        self._last_poll_ended: float = 0.0
        self._last_poll_wall_started: float = 0.0
        self._wake_verify_mode = False
        self._wake_verify_active = False
        self._last_maintenance_wall: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def wake_verify_active(self) -> bool:
        return self._wake_verify_active

    def snapshot_statuses(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_status)

    def snapshot_display_names(self) -> dict[str, str]:
        with self._lock:
            return dict(self._display_names)

    def _emit_went_live(self, entry: ChannelEntry, info: StreamInfo) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(ChannelWentLive(entry=entry, info=info))

    def _emit_went_offline(
        self, entry: ChannelEntry, offline_info: OfflineInfo
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            ChannelWentOffline(entry=entry, offline_info=offline_info)
        )

    def _emit_poll_activity(
        self, entry: ChannelEntry, phase: str, display_name: str
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            PollActivity(
                entry=entry, phase=phase, display_name=display_name
            )
        )

    def _emit_partial_snapshot(
        self, statuses: dict[str, Any], display_names: dict[str, str]
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish(
            PartialStatusUpdate(
                statuses=statuses, display_names=display_names
            )
        )

    def _emit_poll_complete(self) -> None:
        if self._event_bus is None:
            return
        with self._lock:
            statuses = dict(self._last_status)
            display_names = dict(self._display_names)
        self._event_bus.publish(PollWaiting())
        self._event_bus.publish(
            PollStatusUpdate(
                statuses=statuses, display_names=display_names
            )
        )

    def update_channels(self, channels: list[dict[str, str]]) -> None:
        with self._lock:
            old_enabled = {e.key: e.enabled for e in self._entries}
            self._entries = [
                ChannelEntry(
                    platform=ch["platform"],
                    name=ch["name"],
                    enabled=ch.get("enabled", True),
                    monitor_only=bool(ch.get("monitor_only", False)),
                )
                for ch in channels
            ]
            keys = {entry.key for entry in self._entries}
            self._last_status = {
                key: value for key, value in self._last_status.items() if key in keys
            }
            self._display_names = {
                key: value for key, value in self._display_names.items() if key in keys
            }
            self._youtube_baselined = {
                key for key in self._youtube_baselined if key in keys
            }
            self._fallback_triggered_live = {
                key: val
                for key, val in self._fallback_triggered_live.items()
                if key in keys
            }
            self._live_started_at = {
                key: value
                for key, value in self._live_started_at.items()
                if _entry_key_from_live_cache_key(key) in keys
            }
            self._live_platform_started_at = {
                key
                for key in self._live_platform_started_at
                if _entry_key_from_live_cache_key(key) in keys
            }
            self._live_payload = {
                key: value
                for key, value in self._live_payload.items()
                if _entry_key_from_live_cache_key(key) in keys
            }
            self._offline_strikes = {
                key: value
                for key, value in self._offline_strikes.items()
                if _entry_key_from_live_cache_key(key) in keys
            }
            self._twitch_seen_live = {
                key for key in self._twitch_seen_live if key in keys
            }
            self._stable_status_polls = {
                key: value
                for key, value in self._stable_status_polls.items()
                if key in keys
            }
            for entry in self._entries:
                if entry.enabled and not old_enabled.get(entry.key, True):
                    self._last_status.pop(entry.key, None)
                    self._twitch_seen_live.discard(entry.key)
                    # Re-enabling a channel restarts its lifecycle, so any
                    # stale strikes from the last time it was watched would
                    # otherwise short-circuit the first real offline edge.
                    self._offline_strikes = {
                        k: v
                        for k, v in self._offline_strikes.items()
                        if _entry_key_from_live_cache_key(k) != entry.key
                    }

    def update_interval(self, interval: int) -> None:
        self._interval = max(10, interval)

    def _run_maintenance(self, *, force: bool = False) -> None:
        """Prune SQLite seen_videos and the YouTube watch-details cache."""
        wall_now = time.time()
        if not force and self._last_maintenance_wall > 0:
            if wall_now - self._last_maintenance_wall < _MAINTENANCE_INTERVAL_S:
                return
        self._last_maintenance_wall = wall_now
        try:
            removed = self._db.cleanup(days=_DB_CLEANUP_DAYS)
            if removed:
                logger.info("Maintenance: removed %d stale seen_videos rows", removed)
        except Exception:
            logger.exception("DB cleanup failed")
        try:
            from stream_monitor.fetcher.youtube import YouTubeFetcher

            pruned = YouTubeFetcher.prune_watch_details_cache()
            if pruned:
                logger.info(
                    "Maintenance: pruned %d YouTube watch_details cache entries",
                    pruned,
                )
        except Exception:
            logger.exception("YouTube watch_details cache prune failed")

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        with self._lock:
            self._last_status.clear()
            self._display_names.clear()
            self._youtube_baselined.clear()
            self._fallback_triggered_live = {}
            self._live_started_at.clear()
            self._live_platform_started_at.clear()
            self._live_payload.clear()
            self._offline_strikes.clear()
            self._pending_offline_events.clear()
            self._twitch_seen_live.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def restart_thread(self) -> None:
        """Restart the polling thread without clearing in-memory channel state."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Signal the polling thread to exit without blocking the caller."""
        self._stop_event.set()

    def stop(self) -> None:
        self.request_stop()
        self._join_thread()

    def _join_thread(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._thread = None

    def _poll_rest_seconds(self, elapsed: float) -> float:
        """Seconds to wait before the next poll cycle."""
        remainder = self._interval - elapsed
        if remainder > 0:
            return remainder
        return _MIN_POLL_REST_S

    def _run(self) -> None:
        self._run_maintenance(force=True)
        while not self._stop_event.is_set():
            poll_started = time.monotonic()
            elapsed = 0.0
            try:
                elapsed = self._execute_poll_cycle(poll_started)
            except Exception:
                logger.exception("Poll cycle failed unexpectedly, continuing")
                elapsed = time.monotonic() - poll_started
            self._last_poll_ended = time.monotonic()
            self._stop_event.wait(self._poll_rest_seconds(elapsed))

    def _execute_poll_cycle(self, poll_started: float) -> float:
        wall_now = time.time()
        run_wake_verify = False
        if self._last_poll_wall_started > 0:
            wall_gap = wall_now - self._last_poll_wall_started
            grace_threshold = self._interval * _POST_RESUME_GAP_MULTIPLIER
            if wall_gap > grace_threshold:
                run_wake_verify = True
                logger.info(
                    "wake_verify_scheduled: wall_gap=%.1fs > %.1fs",
                    wall_gap,
                    grace_threshold,
                )
        self._last_poll_wall_started = wall_now

        self._poll_cycle += 1
        with self._lock:
            entries = list(self._entries)

        enabled_entries = [e for e in entries if e.enabled]

        if run_wake_verify and enabled_entries:
            return self._run_wake_verification(enabled_entries, poll_started)

        with self._lock:
            self._pending_offline_events.clear()
            self._probe_snapshots.clear()
        enabled_count = len(enabled_entries)
        offline_count = 0
        went_live_count = 0
        commits: list[Callable[[], None]] = []

        tier1_started = time.monotonic()
        youtube_entries = [
            entry for entry in enabled_entries if entry.platform == "youtube"
        ]
        twitch_entries = [
            entry for entry in enabled_entries if entry.platform != "youtube"
        ]
        logger.info(
            "Poll tier-1 start: enabled=%d youtube=%d twitch=%d concurrent=%d",
            len(enabled_entries),
            len(youtube_entries),
            len(twitch_entries),
            self._max_concurrent,
        )
        if youtube_entries:
            logger.info("Poll tier-1 youtube phase: %d channel(s)", len(youtube_entries))
            went_live_count += self._tier1_probe_entries(youtube_entries)
        if not self._stop_event.is_set() and twitch_entries:
            logger.info("Poll tier-1 twitch phase: %d channel(s)", len(twitch_entries))
            went_live_count += self._tier1_probe_entries(twitch_entries)

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        tier1_elapsed = time.monotonic() - tier1_started
        logger.info("Poll tier-1 done: %.2fs", tier1_elapsed)

        logger.info("Poll tier-2 start: enabled=%d", len(enabled_entries))
        if (
            enabled_entries
            and self._max_concurrent > 1
            and len(enabled_entries) > 1
        ):
            workers = min(self._max_concurrent, len(enabled_entries))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                refresh_futures = [
                    pool.submit(self._refresh_details, entry)
                    for entry in enabled_entries
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
            for entry in enabled_entries:
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

    # ------------------------------------------------------------------
    # Wake verification (after long pause / sleep)
    # ------------------------------------------------------------------
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

            for entry in enabled_entries:
                if self._stop_event.is_set():
                    break
                cached = self._cached_status_bucket(entry)
                observed = self._probe_channel_status(entry)
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

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    _noop_commit: Callable[[], None] = staticmethod(lambda: None)  # type: ignore[assignment]

    def _record_offline_miss(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev_status: Any,
        *,
        label: str,
        reason: str,
    ) -> str:
        """Track a poll that failed to confirm LIVE. Caller must hold ``_lock``.

        Returns ``hold`` if the anti-flap guard absorbed the miss,
        ``commit`` if the strike threshold was reached, or ``noop`` when
        the channel was not previously live.
        """
        if prev_status is not True:
            return "noop"
        if self._wake_verify_mode:
            return "hold"
        strikes = self._offline_strikes.get(live_key, 0) + 1
        if strikes < _OFFLINE_STRIKE_THRESHOLD:
            self._offline_strikes[live_key] = strikes
            logger.info(
                "%s %s: ignoring transient offline reading (%d/%d) "
                "reason=%s prev_status=True kept=True",
                label,
                entry.key,
                strikes,
                _OFFLINE_STRIKE_THRESHOLD,
                reason,
            )
            return "hold"
        self._offline_strikes.pop(live_key, None)
        return "commit"

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
        """Treat fetch failures like offline misses when we still believe LIVE."""
        live_key = _live_cache_key(entry.key)
        deferred_youtube: tuple[ChannelEntry, Any, str] | None = None
        deferred_twitch: tuple[ChannelEntry, str, Any, str, Any] | None = None

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev
            strikes_before = self._offline_strikes.get(live_key, 0)

            if prev_status is True:
                miss = self._record_offline_miss(
                    entry,
                    live_key,
                    prev_status,
                    label=label,
                    reason=reason,
                )
                if miss == "hold":
                    logger.warning(
                        "%s %s: %s, treating as offline miss "
                        "(%d/%d) prev_status=True kept=True",
                        label,
                        entry.key,
                        reason,
                        self._offline_strikes.get(live_key, 0),
                        _OFFLINE_STRIKE_THRESHOLD,
                    )
                    return []
                if label.startswith("YouTube"):
                    deferred_youtube = (entry, prev, label)
                elif snap is not None:
                    snap.twitch_offline_commit = True
                    snap.fetcher = _monitor_deps.get_fetcher(entry.platform)
                else:
                    deferred_twitch = (
                        entry,
                        live_key,
                        prev,
                        label,
                        _monitor_deps.get_fetcher(entry.platform),
                    )
            else:
                logger.warning(
                    "%s %s: %s, keeping prev_status=%s strikes=%s",
                    label,
                    entry.key,
                    reason,
                    prev_status,
                    strikes_before,
                )
                if entry.key not in self._last_status:
                    self._last_status[entry.key] = ChannelStatus(
                        status=False,
                        ended_at="",
                        url=_channel_home_url(entry),
                        ended_at_source="pending",
                    )

        if deferred_youtube is not None:
            yt_entry, yt_prev, yt_label = deferred_youtube
            self._enqueue_youtube_fallback_went_offline(
                yt_entry, yt_prev, label=yt_label
            )
        if deferred_twitch is not None:
            tw_entry, tw_live_key, tw_prev, tw_label, tw_fetcher = deferred_twitch
            self._enqueue_twitch_went_offline(
                tw_entry,
                tw_live_key,
                tw_prev,
                label=tw_label,
                fetcher=tw_fetcher,
            )
        return []

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
        logger.info("Poll activity: %s phase=%s", entry.key, phase)
        if self._event_bus is None:
            return
        self._emit_poll_activity(entry, phase, display_name)

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
        logger.info(
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

    def _probe_live(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        self._notify_poll_activity(entry, "probe")
        snap = _ProbeSnapshot()
        probe = get_platform_probe(entry.platform)
        events = probe.probe_live(self, entry, snap)
        finalize = getattr(probe, "finalize_tier1_probe", None)
        if finalize is not None:
            finalize(self, entry, snap)
        return events

    def _refresh_details(self, entry: ChannelEntry) -> Callable[[], None]:
        self._notify_poll_activity(entry, "refresh")
        with self._lock:
            snap = self._probe_snapshots.get(entry.key)
        if snap is None:
            snap = _ProbeSnapshot()
        return get_platform_probe(entry.platform).refresh_details(
            self, entry, snap
        )
