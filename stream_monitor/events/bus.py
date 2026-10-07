"""Thread-safe monitor event bus (producer on poll thread, consumer on UI thread)."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

from stream_monitor.events.types import MonitorEvent

logger = logging.getLogger(__name__)

Subscriber = Callable[[MonitorEvent], None]


class MonitorEventBus:
    """In-process pub/sub queue between ``Monitor`` and UI subscribers."""

    def __init__(self) -> None:
        self._queue: deque[MonitorEvent] = deque()
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        """Register a synchronous listener (used in tests and diagnostics)."""
        self._subscribers.append(callback)

    def publish(self, event: MonitorEvent) -> None:
        with self._lock:
            self._queue.append(event)
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                logger.exception("MonitorEventBus subscriber error")

    def drain(self) -> list[MonitorEvent]:
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    def requeue(self, events: list[MonitorEvent]) -> None:
        """Restore an unfinished batch ahead of newly published events."""
        with self._lock:
            self._queue.extendleft(reversed(events))

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
