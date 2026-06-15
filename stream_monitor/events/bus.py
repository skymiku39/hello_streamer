"""Thread-safe monitor event bus (producer on poll thread, consumer on UI thread)."""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable

from stream_monitor.events.types import MonitorEvent

logger = logging.getLogger(__name__)

Subscriber = Callable[[MonitorEvent], None]


class MonitorEventBus:
    """In-process pub/sub queue between ``Monitor`` and UI subscribers."""

    def __init__(self) -> None:
        self._queue: queue.Queue[MonitorEvent] = queue.Queue()
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        """Register a synchronous listener (used in tests and diagnostics)."""
        self._subscribers.append(callback)

    def publish(self, event: MonitorEvent) -> None:
        self._queue.put(event)
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                logger.exception("MonitorEventBus subscriber error")

    def drain(self) -> list[MonitorEvent]:
        items: list[MonitorEvent] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def requeue(self, events: list[MonitorEvent]) -> None:
        for event in events:
            self._queue.put(event)

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
