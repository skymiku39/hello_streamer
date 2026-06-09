"""背景輪詢排程器 — 定期檢查頻道清單並透過 callback 回報狀態變化。

Twitch: 布林邊緣觸發 (get_stream_info) + ARCHIVE VOD；無待機室
YouTube: TIDUS 架構 — videoId + style 事件追蹤 (get_channel_items + SQLite)
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.util import (
    channel_key,
    normalize_channel_name,
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)

logger = logging.getLogger(__name__)

_STYLE_TO_STATUS = {
    "LIVE": "live",
    "UPCOMING": "upcoming",
    "DEFAULT": "video",
}

# Anti-flap guard: how many consecutive "not live" readings we require before
# we trust a previously-live channel/video has actually gone offline.
#
# Why: Twitch GQL occasionally returns `stream: null` for a still-live channel
# (CDN/cache lag at peak hours), and YouTube's TIDUS feed sometimes omits a
# LIVE video for one poll. Without this guard, those single-poll dropouts
# generate a fake went_offline → went_live edge pair, which (a) triggers a
# duplicate "stream is live!" notification on the next poll and (b) — if the
# user enabled close_on_offline — actually closes the player window the app
# just opened. Requiring two consecutive misses makes both problems go away
# while still bounding worst-case latency to two poll intervals.
_OFFLINE_STRIKE_THRESHOLD = 2
# After a long wall-clock gap (e.g. system sleep), run a wake verification
# poll before the next regular cycle — confirm cached state or defer edges.
_POST_RESUME_GAP_MULTIPLIER = 2
_FETCH_FAILURE_REASONS = frozenset({"fetch returned None", "fetch exception"})
# Log unchanged channel status every N poll cycles (per channel) for diagnostics.
_STABLE_STATUS_LOG_EVERY = 20
_CONFIRMED_FUTURE_SLACK = timedelta(minutes=5)
_DEFAULT_MAX_CONCURRENT = 4
# Minimum rest between poll cycles when a cycle overruns check_interval.
_MIN_POLL_REST_S = 5.0
# Periodic housekeeping for SQLite seen_videos and YouTube watch-page cache.
_MAINTENANCE_INTERVAL_S = 24 * 3600
_DB_CLEANUP_DAYS = 30


@dataclass
class ChannelEntry:
    platform: str
    name: str
    enabled: bool = True
    # monitor_only = True ⇒ the polling thread should still observe this
    # channel (status updates, "LIVE" labels in the UI) but downstream
    # action dispatch (notifications, opening the browser, close_on_offline)
    # must be suppressed. The flag is carried on the entry so callbacks can
    # easily see it without re-resolving the channel via config_manager.
    monitor_only: bool = False

    def __post_init__(self) -> None:
        self.name = normalize_channel_name(self.platform, self.name)

    @property
    def key(self) -> str:
        return channel_key(self.platform, self.name)


@dataclass
class ChannelStatus:
    status: bool | str | None
    url: str = ""
    title: str = ""
    scheduled_start: str = ""
    started_at: str = ""
    ended_at: str = ""  # ISO8601 when offline was confirmed
    vod_url: str = ""  # archive / replay link for the link button
    upcoming_url: str = ""  # waiting-room link when offline but scheduled
    ended_at_source: str = ""  # "vod" | "confirmed"

    def __eq__(self, other: object) -> bool:
        return self.status == other


StatusCallback = Callable[[ChannelEntry, StreamInfo], None]

# Fired when a channel that was previously LIVE transitions back to "not live".
# Receives the entry plus the URL and title that were last known to be live,
# so callers can e.g. close the player window we opened on the going-live edge.
OfflineCallback = Callable[["ChannelEntry", "OfflineInfo"], None]


@dataclass
class OfflineInfo:
    url: str
    title: str
    platform: str
    name: str
    video_id: str = ""
    display_name: str = ""


@dataclass
class _ProbeSnapshot:
    """Tier-1 probe cache reused by tier-2 detail refresh in the same poll."""

    twitch_info: StreamInfo | None = None
    twitch_offline_hold: bool = False
    twitch_offline_commit: bool = False
    youtube_items: list[VideoItem] | None = None
    youtube_pending_seen: list[tuple[str, str, str, str, str]] | None = None
    youtube_fallback: bool = False
    youtube_fallback_info: StreamInfo | None = None
    youtube_fallback_hold: bool = False
    fetcher: Any = None


def _video_item_to_stream_info(item: VideoItem, channel: str) -> StreamInfo:
    return StreamInfo(
        channel=channel,
        platform="youtube",
        is_live=item.style == "LIVE",
        title=item.title,
        url=item.url,
        display_name=item.display_name,
        video_id=item.video_id,
        stream_status=_STYLE_TO_STATUS.get(item.style, "video"),
        scheduled_start=item.scheduled_start,
        started_at=item.started_at,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _channel_home_url(entry: ChannelEntry) -> str:
    if entry.platform == "twitch":
        return f"https://www.twitch.tv/{entry.name}"
    if entry.name.startswith("UC"):
        return f"https://www.youtube.com/channel/{entry.name}"
    return f"https://www.youtube.com/@{entry.name}"


def _merge_offline_ended_at(
    confirmed_iso: str, platform_end: str | None
) -> tuple[str, str]:
    """Pick ended_at for offline elapsed. Returns (iso, source)."""
    confirmed_dt = parse_iso_datetime(confirmed_iso)
    if confirmed_dt is None:
        return confirmed_iso, "confirmed"
    if not platform_end:
        return confirmed_iso, "confirmed"
    platform_dt = parse_iso_datetime(platform_end)
    if platform_dt is None:
        return confirmed_iso, "confirmed"
    now = datetime.now(timezone.utc)
    if platform_dt > now + _CONFIRMED_FUTURE_SLACK:
        return confirmed_iso, "confirmed"
    if platform_dt > confirmed_dt + _CONFIRMED_FUTURE_SLACK:
        return confirmed_iso, "confirmed"
    return platform_end, "vod"


def _live_cache_key(entry_key: str, video_id: str = "") -> str:
    return f"{entry_key}|{video_id or '_'}"


def _entry_key_from_live_cache_key(key: str) -> str:
    return key.split("|", 1)[0]


def _sort_datetime(value: str, fallback: datetime) -> datetime:
    return parse_iso_datetime(value) or fallback


def _youtube_upcoming_is_usable(scheduled_start: str) -> bool:
    """True when a YouTube waiting-room schedule is worth surfacing."""
    return youtube_upcoming_schedule_is_surfacable(scheduled_start)


class Monitor:
    """Polls a list of channels in a background thread."""

    def __init__(
        self,
        channels: list[dict[str, str]],
        interval: int = 60,
        on_status_change: StatusCallback | None = None,
        on_poll_complete: Callable[[], None] | None = None,
        db: SeenVideoDB | None = None,
        on_went_offline: OfflineCallback | None = None,
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
        self._on_status_change = on_status_change
        self._on_poll_complete = on_poll_complete
        self._on_went_offline = on_went_offline
        self._db = db or SeenVideoDB()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status: dict[str, Any] = {}
        self._display_names: dict[str, str] = {}
        self._youtube_baselined: set[str] = set()
        self._fallback_triggered_live: dict[str, str] = {}
        self._live_started_at: dict[str, str] = {}
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
            self._live_payload.clear()
            self._offline_strikes.clear()
            self._pending_offline_events.clear()
            self._twitch_seen_live.clear()
        self._run_maintenance(force=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def restart_thread(self) -> None:
        """Restart the polling thread without clearing in-memory channel state."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._run_maintenance(force=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
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

        def _dispatch_live(
            events: list[tuple[ChannelEntry, StreamInfo]],
        ) -> None:
            nonlocal went_live_count
            for entry, info in events:
                went_live_count += 1
                if self._on_status_change:
                    try:
                        self._on_status_change(entry, info)
                    except Exception:
                        logger.exception(
                            "on_status_change callback error for %s",
                            entry.key,
                        )

        tier1_started = time.monotonic()
        if (
            enabled_entries
            and self._max_concurrent > 1
            and len(enabled_entries) > 1
        ):
            workers = min(self._max_concurrent, len(enabled_entries))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                probe_futures = [
                    pool.submit(self._probe_live, entry)
                    for entry in enabled_entries
                ]
                for future in as_completed(probe_futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        _dispatch_live(future.result())
                    except Exception:
                        logger.exception(
                            "Tier-1 probe failed for a channel, skipping"
                        )
        else:
            for entry in enabled_entries:
                if self._stop_event.is_set():
                    break
                try:
                    _dispatch_live(self._probe_live(entry))
                except Exception:
                    logger.exception(
                        "Tier-1 probe failed for %s, skipping", entry.key
                    )

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        tier1_elapsed = time.monotonic() - tier1_started

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
            if self._on_went_offline:
                try:
                    self._on_went_offline(entry, offline_info)
                except Exception:
                    logger.exception(
                        "on_went_offline callback error for %s", entry.key
                    )

        if self._stop_event.is_set():
            return time.monotonic() - poll_started

        if self._on_poll_complete:
            try:
                self._on_poll_complete()
            except Exception:
                logger.exception("on_poll_complete callback error")

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
            return self._status_bucket(prev.status)
        return self._status_bucket(prev)

    def _probe_channel_status(self, entry: ChannelEntry) -> str | None:
        """Lightweight live/offline/upcoming probe for wake verification."""
        try:
            fetcher = get_fetcher(entry.platform)
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

            if self._on_poll_complete:
                try:
                    self._on_poll_complete()
                except Exception:
                    logger.exception("on_poll_complete callback error")
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
                fetcher = get_fetcher("youtube")
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
                fetcher = get_fetcher(entry.platform)
            except Exception:
                logger.exception("No fetcher for %s", entry.key)
                return None
        try:
            return fetcher.get_latest_finished_vod(
                entry.name, items=channel_items
            )
        except Exception:
            logger.exception("Failed to fetch finished VOD for %s", entry.key)
            return None

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
                ended_at=prev_cs.ended_at or _utc_now_iso(),
                vod_url=prev_cs.vod_url,
                upcoming_url=upcoming_url,
                url=_channel_home_url(entry),
                ended_at_source=prev_cs.ended_at_source,
                scheduled_start=scheduled_start,
            )

        if prev_cs.vod_url and prev_cs.upcoming_url and not fetcher:
            return None

        confirmed = prev_cs.ended_at or _utc_now_iso()
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
            confirmed = prev_cs.ended_at or _utc_now_iso()
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
            return ChannelStatus(
                status=False,
                title=prev_cs.title or (payload.title if payload else ""),
                ended_at=confirmed,
                vod_url=prev_cs.vod_url or extra_vod_url,
                upcoming_url=upcoming_url,
                url=_channel_home_url(entry),
                ended_at_source=prev_cs.ended_at_source or "confirmed",
                scheduled_start=scheduled_start,
            )

        confirmed = _utc_now_iso()
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
            confirmed = prev_cs.ended_at or _utc_now_iso()
            upgraded = self._try_upgrade_twitch_offline_vod(
                entry, prev_cs, fetcher=fetcher, payload=payload
            )
            if upgraded is not None:
                return upgraded
            return ChannelStatus(
                status=False,
                title=prev_cs.title or (payload.title if payload else ""),
                ended_at=confirmed,
                vod_url=prev_cs.vod_url,
                upcoming_url="",
                url=_channel_home_url(entry),
                ended_at_source=prev_cs.ended_at_source or "confirmed",
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
            fetcher = get_fetcher(entry.platform)
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
        yt_fetcher = get_fetcher(entry.platform)
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
                    snap.fetcher = get_fetcher(entry.platform)
                else:
                    deferred_twitch = (
                        entry,
                        live_key,
                        prev,
                        label,
                        get_fetcher(entry.platform),
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
                        ended_at=_utc_now_iso(),
                        url=_channel_home_url(entry),
                        ended_at_source="confirmed",
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

    def _probe_live(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        snap = _ProbeSnapshot()
        if entry.platform == "youtube":
            events = self._probe_youtube(entry, snap)
        else:
            events = self._probe_twitch(entry, snap)
        with self._lock:
            self._probe_snapshots[entry.key] = snap
        return events

    def _refresh_details(self, entry: ChannelEntry) -> Callable[[], None]:
        with self._lock:
            snap = self._probe_snapshots.get(entry.key)
        if snap is None:
            snap = _ProbeSnapshot()
        if entry.platform == "youtube":
            return self._refresh_youtube(entry, snap)
        self._refresh_twitch(entry, snap)
        return self._noop_commit

    # ------------------------------------------------------------------
    # Tier 1 — live probe + trigger
    # ------------------------------------------------------------------
    def _probe_twitch(
        self, entry: ChannelEntry, snap: _ProbeSnapshot
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

        try:
            fetcher = get_fetcher(entry.platform)
            info = fetcher.get_stream_info(entry.name)
            # Double-check only at the live→offline edge; stable offline
            # channels skip the extra GQL round-trip.
            if info is not None and not info.is_live and prev_status is True:
                retry = fetcher.get_stream_info(entry.name)
                if retry is not None:
                    info = retry
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return self._handle_fetch_unavailable(
                entry, label="Twitch", snap=snap, reason="fetch exception"
            )

        if info is None:
            return self._handle_fetch_unavailable(
                entry, label="Twitch", snap=snap
            )

        live_key = _live_cache_key(entry.key)

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_cs = prev if isinstance(prev, ChannelStatus) else None
            prev_status = prev_cs.status if prev_cs is not None else prev

            # ── Anti-flap guard ──────────────────────────────────────────
            if not info.is_live and prev_status is True:
                miss = self._record_offline_miss(
                    entry,
                    live_key,
                    prev_status,
                    label="Twitch",
                    reason="api reported offline",
                )
                if miss == "hold":
                    if info.display_name:
                        self._display_names[entry.key] = info.display_name
                    snap.twitch_info = info
                    snap.twitch_offline_hold = True
                    snap.fetcher = fetcher
                    return []
            else:
                self._offline_strikes.pop(live_key, None)

            if info.is_live:
                self._twitch_seen_live.add(entry.key)
                started_at = info.started_at or self._live_started_at.get(live_key)
                if not started_at:
                    started_at = _utc_now_iso()
                self._live_started_at[live_key] = started_at
                info.started_at = started_at
                self._last_status[entry.key] = ChannelStatus(
                    status=True,
                    url=info.url,
                    title=info.title,
                    started_at=started_at,
                )
                self._live_payload[live_key] = OfflineInfo(
                    url=info.url,
                    title=info.title,
                    platform=entry.platform,
                    name=entry.name,
                    display_name=info.display_name or "",
                )
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        snap.twitch_info = info
        snap.fetcher = fetcher

        if self._wake_verify_mode:
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

    # ------------------------------------------------------------------
    # Tier 2 — Twitch detail refresh
    # ------------------------------------------------------------------
    def _refresh_twitch(
        self, entry: ChannelEntry, snap: _ProbeSnapshot
    ) -> None:
        """Tier 2: went_offline commit, cold ARCHIVE, stable offline refresh."""
        fetcher = snap.fetcher
        if snap.twitch_offline_commit:
            live_key = _live_cache_key(entry.key)
            with self._lock:
                prev = self._last_status.get(entry.key)
            self._enqueue_twitch_went_offline(
                entry,
                live_key,
                prev,
                label="Twitch",
                fetcher=fetcher,
            )
            return

        info = snap.twitch_info
        if info is None:
            return

        live_key = _live_cache_key(entry.key)
        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_cs = prev if isinstance(prev, ChannelStatus) else None
            prev_status = prev_cs.status if prev_cs is not None else prev

        went_offline = (
            (not info.is_live)
            and prev_status is True
            and not snap.twitch_offline_hold
        )
        if went_offline:
            self._enqueue_twitch_went_offline(
                entry,
                live_key,
                prev,
                label="Twitch",
                fetcher=fetcher,
            )
        elif not info.is_live and prev_status is not True:
            offline_cs = self._twitch_offline_status_for(
                entry,
                prev,
                fetcher=fetcher,
            )
            with self._lock:
                self._last_status[entry.key] = offline_cs

        with self._lock:
            self._maybe_log_stable_twitch_status(entry, info, prev_status)

    # ------------------------------------------------------------------
    # Tier 1/2 — YouTube
    # ------------------------------------------------------------------
    def _probe_youtube(
        self, entry: ChannelEntry, snap: _ProbeSnapshot
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            fetcher = get_fetcher(entry.platform)
            items = fetcher.get_channel_items(entry.name, fill_timing=False)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return self._handle_fetch_unavailable(
                entry, label="YouTube", snap=snap, reason="fetch exception"
            )

        if items is None:
            return self._handle_fetch_unavailable(
                entry, label="YouTube", snap=snap
            )

        snap.fetcher = fetcher
        snap.youtube_items = items
        if not items:
            with self._lock:
                prev = self._last_status.get(entry.key)
                prev_status = (
                    prev.status if isinstance(prev, ChannelStatus) else prev
                )
                was_fallback_live = entry.key in self._fallback_triggered_live
            if (
                was_fallback_live
                and prev_status is True
                and not self._wake_verify_mode
            ):
                live_key = _live_cache_key(entry.key)
                with self._lock:
                    miss = self._record_offline_miss(
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
            return self._probe_youtube_fallback_live(entry, fetcher, snap)

        new_events: list[tuple[ChannelEntry, StreamInfo]] = []
        live_items: list[VideoItem] = []
        with self._lock:
            is_baselined = entry.key in self._youtube_baselined
            fallback_title = self._fallback_triggered_live.get(entry.key)

        unseen_live_items: list[VideoItem] = []
        pending_seen: list[tuple[str, str, str, str, str]] = []

        for item in items:
            if item.style == "LIVE":
                live_key = _live_cache_key(entry.key, item.video_id)
                with self._lock:
                    started_at = item.started_at or self._live_started_at.get(live_key)
                    if not started_at:
                        started_at = _utc_now_iso()
                    self._live_started_at[live_key] = started_at
                    self._live_payload[live_key] = OfflineInfo(
                        url=item.url,
                        title=item.title,
                        platform=entry.platform,
                        name=entry.name,
                        video_id=item.video_id,
                        display_name=item.display_name or "",
                    )
                item.started_at = started_at
                live_items.append(item)

            if item.display_name:
                with self._lock:
                    self._display_names[entry.key] = item.display_name

            try:
                if self._db.is_seen(item.video_id, item.style):
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
            with self._lock:
                self._fallback_triggered_live.pop(entry.key, None)

        for item in unseen_live_items:
            info = _video_item_to_stream_info(item, entry.name)
            new_events.append((entry, info))

        snap.youtube_pending_seen = pending_seen
        if self._wake_verify_mode:
            return []
        return new_events

    def _refresh_youtube(
        self, entry: ChannelEntry, snap: _ProbeSnapshot
    ) -> Callable[[], None]:
        """Tier 2: LIVE row, strike-hold, or OFFLINE (+ optional upcoming_url)."""
        if snap.youtube_fallback:
            self._refresh_youtube_fallback(entry, snap)
            return self._noop_commit

        fetcher = snap.fetcher
        items = snap.youtube_items
        if fetcher is None or items is None:
            return self._noop_commit

        fetcher.enrich_items_for_details(items)

        live_items = [item for item in items if item.style == "LIVE"]
        has_live = bool(live_items)
        live_status: ChannelStatus | None = None
        if has_live:
            live_item = min(
                live_items,
                key=lambda item: _sort_datetime(
                    item.started_at, datetime.now(timezone.utc)
                ),
            )
            live_status = ChannelStatus(
                status=True,
                url=live_item.url,
                title=live_item.title,
                started_at=live_item.started_at,
            )

        prev_offline: Any = None
        offline_extra_vod = ""
        need_offline_status = False
        has_strike_pending = False
        active_live_ids: set[str] = set()

        with self._lock:
            observed_live_ids = {item.video_id for item in live_items}

            if has_live:
                fallback_alias_key = _live_cache_key(entry.key)
                self._live_payload.pop(fallback_alias_key, None)
                self._offline_strikes.pop(fallback_alias_key, None)
                self._fallback_triggered_live.pop(entry.key, None)

            active_live_ids = set(observed_live_ids)
            stale_candidates = [
                key
                for key in list(self._live_payload.keys())
                if _entry_key_from_live_cache_key(key) == entry.key
                and key.rsplit("|", 1)[-1] not in active_live_ids
            ]
            for stale_key in stale_candidates:
                strikes = self._offline_strikes.get(stale_key, 0) + 1
                if strikes < _OFFLINE_STRIKE_THRESHOLD:
                    self._offline_strikes[stale_key] = strikes
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
                self._offline_strikes.pop(stale_key, None)
                payload = self._live_payload.pop(stale_key, None)
                if payload is not None:
                    self._pending_offline_events.append((entry, payload))

            for vid in observed_live_ids:
                self._offline_strikes.pop(_live_cache_key(entry.key, vid), None)

            if has_live:
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if (
                        _entry_key_from_live_cache_key(key) != entry.key
                        or key.rsplit("|", 1)[-1] in active_live_ids
                    )
                }
            elif has_strike_pending:
                pass
            elif items:
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                offline_extra_vod = next(
                    (item.url for item in items if item.style == "DEFAULT"),
                    "",
                )
                prev_offline = self._last_status.get(entry.key)
                need_offline_status = True

            self._youtube_baselined.add(entry.key)

        if live_status is not None:
            with self._lock:
                self._last_status[entry.key] = live_status
        elif need_offline_status:
            offline_status = self._youtube_offline_status_for(
                entry,
                prev_offline,
                fetcher=fetcher,
                extra_vod_url=offline_extra_vod,
                channel_items=items,
            )
            with self._lock:
                self._last_status[entry.key] = offline_status

        pending_seen = snap.youtube_pending_seen or []

        def commit() -> None:
            for args in pending_seen:
                try:
                    self._db.mark_seen(*args)
                except Exception:
                    logger.exception("DB error for video %s", args[0])

        return commit

    def _probe_youtube_fallback_live(
        self, entry: ChannelEntry, fetcher: Any, snap: _ProbeSnapshot
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("Error fetching fallback status for %s", entry.key)
            return []

        if info is None:
            return self._handle_fetch_unavailable(
                entry, label="YouTube fallback"
            )

        live_key = _live_cache_key(entry.key)

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

            if not info.is_live and prev_status is True:
                miss = self._record_offline_miss(
                    entry,
                    live_key,
                    prev_status,
                    label="YouTube fallback",
                    reason="api reported offline",
                )
                if miss == "hold":
                    if info.display_name:
                        self._display_names[entry.key] = info.display_name
                    snap.youtube_fallback_info = info
                    snap.youtube_fallback_hold = True
                    return []
            else:
                self._offline_strikes.pop(live_key, None)

            if info.is_live:
                started_at = info.started_at or self._live_started_at.get(live_key)
                if not started_at:
                    started_at = _utc_now_iso()
                self._live_started_at[live_key] = started_at
                info.started_at = started_at
                self._last_status[entry.key] = ChannelStatus(
                    status=True,
                    url=info.url,
                    title=info.title,
                    started_at=started_at,
                )
                self._live_payload[live_key] = OfflineInfo(
                    url=info.url,
                    title=info.title,
                    platform=entry.platform,
                    name=entry.name,
                    display_name=info.display_name or "",
                )
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        snap.youtube_fallback_info = info

        if self._wake_verify_mode:
            return []

        went_live = info.is_live and prev_status is not True
        if went_live:
            logger.info(
                "YouTube fallback %s: went_live title=%r url=%s",
                entry.key,
                info.title,
                info.url,
            )
            with self._lock:
                self._fallback_triggered_live[entry.key] = info.title
            info.stream_status = "live"
            return [(entry, info)]
        if info.is_live and prev_status is True:
            logger.info(
                "YouTube fallback %s: went_live_suppressed (already marked live)",
                entry.key,
            )
        return []

    def _refresh_youtube_fallback(
        self, entry: ChannelEntry, snap: _ProbeSnapshot
    ) -> None:
        info = snap.youtube_fallback_info
        fetcher = snap.fetcher
        if info is None or snap.youtube_fallback_hold:
            return

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

        went_offline = (
            (not info.is_live)
            and prev_status is True
        )
        if went_offline:
            self._enqueue_youtube_fallback_went_offline(
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
                with self._lock:
                    self._live_started_at = {
                        key: value
                        for key, value in self._live_started_at.items()
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    self._fallback_triggered_live.pop(entry.key, None)
                    self._last_status[entry.key] = upcoming_status
            else:
                offline_status = self._youtube_offline_status_for(
                    entry, prev, fetcher=fetcher
                )
                with self._lock:
                    self._live_started_at = {
                        key: value
                        for key, value in self._live_started_at.items()
                        if _entry_key_from_live_cache_key(key) != entry.key
                    }
                    self._fallback_triggered_live.pop(entry.key, None)
                    self._last_status[entry.key] = offline_status
