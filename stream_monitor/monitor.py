"""背景輪詢排程器 — 定期檢查頻道清單並透過 callback 回報狀態變化。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo

logger = logging.getLogger(__name__)


@dataclass
class ChannelEntry:
    platform: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.name}"


StatusCallback = Callable[[ChannelEntry, StreamInfo], None]


class Monitor:
    """Polls a list of channels in a background thread."""

    def __init__(
        self,
        channels: list[dict[str, str]],
        interval: int = 60,
        on_status_change: StatusCallback | None = None,
        on_poll_complete: Callable[[], None] | None = None,
    ) -> None:
        self._entries = [
            ChannelEntry(platform=ch["platform"], name=ch["name"])
            for ch in channels
        ]
        self._interval = max(10, interval)
        self._on_status_change = on_status_change
        self._on_poll_complete = on_poll_complete

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status: dict[str, bool] = {}
        self._triggered: set[str] = set()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def triggered(self) -> set[str]:
        with self._lock:
            return set(self._triggered)

    def update_channels(self, channels: list[dict[str, str]]) -> None:
        with self._lock:
            self._entries = [
                ChannelEntry(platform=ch["platform"], name=ch["name"])
                for ch in channels
            ]

    def update_interval(self, interval: int) -> None:
        self._interval = max(10, interval)

    def mark_triggered(self, key: str) -> None:
        with self._lock:
            self._triggered.add(key)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._triggered.clear()
        self._last_status.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                entries = list(self._entries)

            for entry in entries:
                if self._stop_event.is_set():
                    break
                self._check_channel(entry)

            if self._on_poll_complete:
                try:
                    self._on_poll_complete()
                except Exception:
                    logger.exception("on_poll_complete callback error")

            self._stop_event.wait(self._interval)

    def _check_channel(self, entry: ChannelEntry) -> None:
        try:
            fetcher = get_fetcher(entry.platform)
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return

        if info is None:
            return

        prev = self._last_status.get(entry.key)
        self._last_status[entry.key] = info.is_live

        went_live = info.is_live and prev is not True

        if went_live and self._on_status_change:
            try:
                self._on_status_change(entry, info)
            except Exception:
                logger.exception("on_status_change callback error for %s", entry.key)
