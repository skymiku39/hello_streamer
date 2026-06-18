"""Owns the background monitor lifecycle and the monitor-to-UI event path.

This isolates polling lifecycle (event bus, bridge, monitor thread, and the
idle/trigger/watch state machine) from the Tk main window, which keeps
``App`` focused on layout, settings, and tray concerns.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from stream_monitor.db import SeenVideoDB
from stream_monitor.event_bridge import MonitorEventBridge
from stream_monitor.event_sink import AppEventSink
from stream_monitor.events import MonitorEventBus
from stream_monitor.monitor import Monitor

logger = logging.getLogger(__name__)

_ACTIVE_MODES = ("trigger", "watch")


class MonitorController:
    """Coordinates the polling engine and event drain on behalf of the UI."""

    def __init__(self, sink: AppEventSink, db: SeenVideoDB) -> None:
        self._db = db
        self._bus = MonitorEventBus()
        self._bridge = MonitorEventBridge(sink, self._bus)
        self._monitor: Monitor | None = None
        self._mode = "idle"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._monitor is not None and self._monitor.is_running

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
        monitor = self._monitor
        self._monitor = None
        self._bus.clear()
        self._bridge.reset()
        self._mode = "idle"
        if monitor is not None:
            monitor.request_stop()
            threading.Thread(target=monitor.stop, daemon=True).start()

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
            self._monitor.stop()

    def _ensure_running(
        self,
        channels: list[dict[str, str]],
        interval: int,
        initial_statuses: dict[str, Any] | None = None,
        last_activity_epoch: float = 0.0,
    ) -> None:
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
