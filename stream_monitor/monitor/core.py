"""Background polling scheduler and platform probe orchestration."""

from __future__ import annotations

import logging
import threading
import time
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
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor.offline import (
    OfflineBuildersMixin,
    OfflineEnqueueMixin,
    OfflineStrikesMixin,
)
from stream_monitor.monitor.poll_cycle import PollCycleMixin
from stream_monitor.monitor.preview import PreviewMixin
from stream_monitor.monitor.probes.facade import ProbeFacade
from stream_monitor.monitor.probes.session import ProbeSession
from stream_monitor.monitor.types import (
    _DB_CLEANUP_DAYS,
    _DEFAULT_MAX_CONCURRENT,
    _MAINTENANCE_INTERVAL_S,
    _MIN_POLL_REST_S,
    ChannelEntry,
    OfflineInfo,
    _entry_key_from_live_cache_key,
    _ProbeSnapshot,
)
from stream_monitor.monitor.wake_verify import WakeVerifyMixin

logger = logging.getLogger(__name__)


class Monitor(
    OfflineStrikesMixin,
    OfflineBuildersMixin,
    OfflineEnqueueMixin,
    PreviewMixin,
    WakeVerifyMixin,
    PollCycleMixin,
):
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
        # Probe-shared, lock-guarded session state (see ProbeSession). The
        # ``_*`` properties below proxy to it so the offline/preview/poll mixins
        # can keep using ``self._last_status`` etc. while probes go through the
        # narrow ProbeFacade instead of reaching into Monitor internals.
        self._session = ProbeSession()
        self._facade = ProbeFacade(self)
        self._poll_cycle = 0
        self._stable_status_polls: dict[str, int] = {}
        self._last_poll_ended: float = 0.0
        self._last_poll_wall_started: float = 0.0
        self._last_poll_wall_ended: float = 0.0
        self._last_poll_planned_rest: float = 0.0
        self._wake_verify_mode = False
        self._wake_verify_active = False
        self._last_maintenance_wall: float = 0.0

    # ------------------------------------------------------------------
    # ProbeSession proxies (keep mixin code unchanged after state moved out)
    # ------------------------------------------------------------------
    @property
    def _lock(self) -> threading.Lock:
        return self._session.lock

    @property
    def _last_status(self) -> dict[str, Any]:
        return self._session.last_status

    @_last_status.setter
    def _last_status(self, value: dict[str, Any]) -> None:
        self._session.last_status = value

    @property
    def _display_names(self) -> dict[str, str]:
        return self._session.display_names

    @_display_names.setter
    def _display_names(self, value: dict[str, str]) -> None:
        self._session.display_names = value

    @property
    def _probe_snapshots(self) -> dict[str, _ProbeSnapshot]:
        return self._session.probe_snapshots

    @property
    def _offline_strikes(self) -> dict[str, int]:
        return self._session.offline_strikes

    @_offline_strikes.setter
    def _offline_strikes(self, value: dict[str, int]) -> None:
        self._session.offline_strikes = value

    @property
    def _live_started_at(self) -> dict[str, str]:
        return self._session.live_started_at

    @_live_started_at.setter
    def _live_started_at(self, value: dict[str, str]) -> None:
        self._session.live_started_at = value

    @property
    def _live_platform_started_at(self) -> set[str]:
        return self._session.live_platform_started_at

    @_live_platform_started_at.setter
    def _live_platform_started_at(self, value: set[str]) -> None:
        self._session.live_platform_started_at = value

    @property
    def _live_payload(self) -> dict[str, OfflineInfo]:
        return self._session.live_payload

    @_live_payload.setter
    def _live_payload(self, value: dict[str, OfflineInfo]) -> None:
        self._session.live_payload = value

    @property
    def _fallback_triggered_live(self) -> dict[str, str]:
        return self._session.fallback_triggered_live

    @_fallback_triggered_live.setter
    def _fallback_triggered_live(self, value: dict[str, str]) -> None:
        self._session.fallback_triggered_live = value

    @property
    def _youtube_baselined(self) -> set[str]:
        return self._session.youtube_baselined

    @_youtube_baselined.setter
    def _youtube_baselined(self, value: set[str]) -> None:
        self._session.youtube_baselined = value

    @property
    def _twitch_seen_live(self) -> set[str]:
        return self._session.twitch_seen_live

    @_twitch_seen_live.setter
    def _twitch_seen_live(self, value: set[str]) -> None:
        self._session.twitch_seen_live = value

    @property
    def _pending_offline_events(self) -> list[tuple[ChannelEntry, OfflineInfo]]:
        return self._session.pending_offline_events

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
            rest = self._poll_rest_seconds(elapsed)
            self._last_poll_wall_ended = time.time()
            self._last_poll_planned_rest = rest
            self._stop_event.wait(rest)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    _noop_commit: Callable[[], None] = staticmethod(lambda: None)  # type: ignore[assignment]

