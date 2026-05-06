"""背景輪詢排程器 — 定期檢查頻道清單並透過 callback 回報狀態變化。

Twitch: 布林邊緣觸發 (get_stream_info)
YouTube: TIDUS 架構 — videoId + style 事件追蹤 (get_channel_items + SQLite)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import StreamInfo, VideoItem

logger = logging.getLogger(__name__)

_STYLE_TO_STATUS = {
    "LIVE": "live",
    "UPCOMING": "upcoming",
    "DEFAULT": "video",
}


@dataclass
class ChannelEntry:
    platform: str
    name: str
    enabled: bool = True

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.name}"


StatusCallback = Callable[[ChannelEntry, StreamInfo], None]


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
    )


class Monitor:
    """Polls a list of channels in a background thread."""

    def __init__(
        self,
        channels: list[dict[str, str]],
        interval: int = 60,
        on_status_change: StatusCallback | None = None,
        on_poll_complete: Callable[[], None] | None = None,
        db: SeenVideoDB | None = None,
    ) -> None:
        self._entries = [
            ChannelEntry(
                platform=ch["platform"],
                name=ch["name"],
                enabled=ch.get("enabled", True),
            )
            for ch in channels
        ]
        self._interval = max(10, interval)
        self._on_status_change = on_status_change
        self._on_poll_complete = on_poll_complete
        self._db = db or SeenVideoDB()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status: dict[str, Any] = {}
        self._display_names: dict[str, str] = {}
        self._triggered: set[str] = set()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def triggered(self) -> set[str]:
        with self._lock:
            return set(self._triggered)

    def snapshot_statuses(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_status)

    def snapshot_display_names(self) -> dict[str, str]:
        with self._lock:
            return dict(self._display_names)

    def update_channels(self, channels: list[dict[str, str]]) -> None:
        with self._lock:
            self._entries = [
                ChannelEntry(
                    platform=ch["platform"],
                    name=ch["name"],
                    enabled=ch.get("enabled", True),
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
            self._triggered = {key for key in self._triggered if key in keys}

    def update_interval(self, interval: int) -> None:
        self._interval = max(10, interval)

    def mark_triggered(self, key: str) -> None:
        with self._lock:
            self._triggered.add(key)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        with self._lock:
            self._triggered.clear()
            self._last_status.clear()
            self._display_names.clear()
        try:
            self._db.cleanup(days=30)
        except Exception:
            logger.exception("DB cleanup failed")
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

            went_live_batch: list[tuple[ChannelEntry, StreamInfo]] = []
            for entry in entries:
                if self._stop_event.is_set():
                    break
                if not entry.enabled:
                    continue
                results = self._check_channel(entry)
                went_live_batch.extend(results)

            for entry, info in went_live_batch:
                if self._on_status_change:
                    try:
                        self._on_status_change(entry, info)
                    except Exception:
                        logger.exception(
                            "on_status_change callback error for %s", entry.key
                        )

            if self._on_poll_complete:
                try:
                    self._on_poll_complete()
                except Exception:
                    logger.exception("on_poll_complete callback error")

            self._stop_event.wait(self._interval)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _check_channel(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        if entry.platform == "youtube":
            return self._check_youtube(entry)
        return self._check_twitch(entry)

    # ------------------------------------------------------------------
    # Twitch: boolean edge-trigger (unchanged)
    # ------------------------------------------------------------------
    def _check_twitch(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            fetcher = get_fetcher(entry.platform)
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return []

        if info is None:
            return []

        with self._lock:
            prev = self._last_status.get(entry.key)
            self._last_status[entry.key] = info.is_live
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        went_live = info.is_live and prev is not True
        if went_live:
            info.stream_status = "live"
            return [(entry, info)]
        return []

    # ------------------------------------------------------------------
    # YouTube: TIDUS videoId + style
    # ------------------------------------------------------------------
    def _check_youtube(
        self, entry: ChannelEntry
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            fetcher = get_fetcher(entry.platform)
            items = fetcher.get_channel_items(entry.name)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return []

        new_events: list[tuple[ChannelEntry, StreamInfo]] = []
        has_live = False
        has_upcoming = False

        for item in items:
            if item.style == "LIVE":
                has_live = True
            elif item.style == "UPCOMING":
                has_upcoming = True

            if item.display_name:
                with self._lock:
                    self._display_names[entry.key] = item.display_name

            try:
                if self._db.is_seen(item.video_id):
                    continue
                self._db.mark_seen(
                    video_id=item.video_id,
                    platform="youtube",
                    channel=entry.name,
                    style=item.style,
                    title=item.title,
                )
            except Exception:
                logger.exception("DB error for video %s", item.video_id)
                continue

            info = _video_item_to_stream_info(item, entry.name)
            new_events.append((entry, info))

        with self._lock:
            if has_live:
                self._last_status[entry.key] = True
            elif has_upcoming:
                self._last_status[entry.key] = "upcoming"
            elif items:
                self._last_status[entry.key] = False

        return new_events
