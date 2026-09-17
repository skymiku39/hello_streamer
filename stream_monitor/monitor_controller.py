"""Owns the background monitor lifecycle and the monitor-to-UI event path.

This isolates polling lifecycle (event bus, bridge, monitor thread, and the
idle/trigger/watch/one-shot state machine) from the Tk main window, which keeps
``App`` focused on layout, settings, and tray concerns.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from stream_monitor.channel_policy import (
    GLOBAL_ACTIVE_MODES,
    TRIGGER_ONCE_MODE,
    WATCH_ONCE_MODE,
    is_one_shot_monitor_mode,
)
from stream_monitor.db import SeenVideoDB
from stream_monitor.event_bridge import MonitorEventBridge
from stream_monitor.event_sink import AppEventSink
from stream_monitor.events import MonitorEventBus
from stream_monitor.monitor import Monitor

logger = logging.getLogger(__name__)

_ACTIVE_MODES = tuple(GLOBAL_ACTIVE_MODES)


class MonitorController:
    """Coordinates the polling engine and event drain on behalf of the UI."""

    def __init__(self, sink: AppEventSink, db: SeenVideoDB) -> None:
        self._db = db
        self._bus = MonitorEventBus()
        self._bridge = MonitorEventBridge(sink, self._bus)
        self._monitor: Monitor | None = None
        self._mode = "idle"
        self._generation = 0
        self._stopping_thread: threading.Thread | None = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._monitor is not None and self._monitor.is_running

    @property
    def generation(self) -> int:
        """Identify the current monitor session for delayed side-effects."""
        return self._generation

    @property
    def wake_verify_active(self) -> bool:
        return self._monitor is not None and self._monitor.wake_verify_active

    def tick(self) -> None:
        """Drain queued monitor events on the UI thread."""
        self._bridge.tick()

    def snapshot_display_names(self) -> dict[str, str]:
        return self._monitor.snapshot_display_names() if self._monitor else {}

    def snapshot_statuses(self) -> dict[str, Any]:
        return self._monitor.snapshot_statuses() if self._monitor else {}

    def update_channels(self, channels: list[dict[str, str]]) -> None:
        """Push a channel-list change to a running monitor (no-op otherwise)."""
        if self._monitor is not None and self._monitor.is_running:
            self._monitor.update_channels(channels)

    def start(
        self,
        mode: str,
        channels: list[dict[str, str]],
        interval: int,
        initial_statuses: dict[str, Any] | None = None,
        last_activity_epoch: float = 0.0,
    ) -> bool:
        """Enter ``mode`` and ensure the monitor is polling. False if no channels."""
        if not channels:
            return False
        self._generation += 1
        if is_one_shot_monitor_mode(mode):
            current_cycle = (
                self._monitor.poll_cycle if self._monitor is not None else 0
            )
            self._bridge.arm_one_shot(after_cycle=current_cycle)
        else:
            self._bridge.disarm_one_shot()
        self._mode = mode
        self._ensure_running(
            channels,
            interval,
            initial_statuses=initial_statuses,
            last_activity_epoch=last_activity_epoch,
        )
        return True

    def stop(self) -> None:
        """Leave active mode and tear down the monitor thread."""
        self._generation += 1
        monitor = self._monitor
        self._monitor = None
        self._bus.clear()
        self._bridge.reset()
        self._mode = "idle"
        if monitor is not None:
            monitor.request_stop()
            # Let the worker wait until the poll thread really exits.  A later
            # start and application shutdown must never overlap that thread
            # with a new monitor or with DB.close().
            t = threading.Thread(
                target=monitor.stop,
                kwargs={"timeout": None},
                daemon=True,
                name="monitor-stop",
            )
            t.start()
            self._stopping_thread = t

    def finish_one_shot(self) -> str | None:
        """Finish a completed one-cycle poll without cancelling its actions.

        The bridge calls this only after it has drained the cycle's
        ``PollStatusUpdate`` and submitted any live actions.  Unlike the
        user-facing ``stop()``, this does not advance the generation or clear
        the event bus, so already-submitted browser/notification workers can
        finish normally.
        """
        mode = self._mode
        if mode not in (TRIGGER_ONCE_MODE, WATCH_ONCE_MODE):
            return None

        monitor = self._monitor
        self._monitor = None
        self._mode = "idle"
        if monitor is not None:
            monitor.request_stop()
            t = threading.Thread(
                target=monitor.stop,
                kwargs={"timeout": None},
                daemon=True,
                name="monitor-stop-once",
            )
            t.start()
            self._stopping_thread = t
        return "trigger" if mode == TRIGGER_ONCE_MODE else "watch"

    def restart_if_dead(
        self, channels: list[dict[str, str]], interval: int
    ) -> bool:
        """Restart a monitor whose thread died while in an active mode."""
        if self._mode not in _ACTIVE_MODES:
            return False
        if self._monitor is None or self._monitor.is_running:
            return False
        logger.warning(
            "Monitor thread died unexpectedly (mode=%s), restarting", self._mode
        )
        if not channels:
            return False
        self._ensure_running(channels, interval)
        return True

    def shutdown(self) -> None:
        """Blocking stop used during application exit."""
        if self._monitor is not None:
            self.stop()
        self._join_stopping_thread(timeout=None)
        self._bus.clear()
        self._bridge.reset()
        self._mode = "idle"

    def _ensure_running(
        self,
        channels: list[dict[str, str]],
        interval: int,
        initial_statuses: dict[str, Any] | None = None,
        last_activity_epoch: float = 0.0,
    ) -> None:
        # Do not create a new monitor while the previous poll thread is still
        # unwinding network work or using the shared SQLite connection.
        self._join_stopping_thread(timeout=None)
        if self._monitor is not None and self._monitor.is_running:
            self._monitor.update_interval(interval)
            self._monitor.update_channels(channels)
        elif self._monitor is not None:
            self._monitor.update_interval(interval)
            self._monitor.update_channels(channels)
            self._monitor.restart_thread()
        else:
            self._monitor = Monitor(
                channels=channels,
                interval=interval,
                event_bus=self._bus,
                db=self._db,
                initial_statuses=initial_statuses,
                last_activity_epoch=last_activity_epoch,
            )
            self._monitor.start()

    def _join_stopping_thread(self, *, timeout: float | None) -> None:
        """Block until any previous monitor-stop thread has finished."""
        t = self._stopping_thread
        if t is not None and t.is_alive():
            logger.debug("Waiting for prior monitor shutdown to complete…")
            t.join(timeout=timeout)
            if t.is_alive():
                if timeout is not None:
                    logger.warning(
                        "Prior monitor stop thread did not finish within %.1fs",
                        timeout,
                    )
        self._stopping_thread = None
