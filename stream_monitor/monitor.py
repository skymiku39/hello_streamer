"""背景輪詢排程器 — 定期檢查頻道清單並透過 callback 回報狀態變化。

Twitch: 布林邊緣觸發 (get_stream_info)
YouTube: TIDUS 架構 — videoId + style 事件追蹤 (get_channel_items + SQLite)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class ChannelStatus:
    status: bool | str | None
    url: str = ""
    title: str = ""
    scheduled_start: str = ""
    started_at: str = ""

    def __eq__(self, other: object) -> bool:
        return self.status == other


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
        started_at=item.started_at,
    )


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _live_cache_key(entry_key: str, video_id: str = "") -> str:
    return f"{entry_key}|{video_id or '_'}"


def _entry_key_from_live_cache_key(key: str) -> str:
    return key.split("|", 1)[0]


def _sort_datetime(value: str, fallback: datetime) -> datetime:
    return _parse_iso_datetime(value) or fallback


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
        self._youtube_baselined: set[str] = set()
        self._fallback_triggered_live: dict[str, str] = {}
        self._live_started_at: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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
            for entry in self._entries:
                if entry.enabled and not old_enabled.get(entry.key, True):
                    self._last_status.pop(entry.key, None)

    def update_interval(self, interval: int) -> None:
        self._interval = max(10, interval)

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
            if not self._thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                entries = list(self._entries)

            went_live_batch: list[tuple[ChannelEntry, StreamInfo]] = []
            commits: list[Callable[[], None]] = []
            for entry in entries:
                if self._stop_event.is_set():
                    break
                if not entry.enabled:
                    continue
                results, commit = self._check_channel(entry)
                went_live_batch.extend(results)
                commits.append(commit)

            if self._stop_event.is_set():
                break

            for commit in commits:
                if self._stop_event.is_set():
                    break
                commit()

            if self._stop_event.is_set():
                break

            for entry, info in went_live_batch:
                if self._stop_event.is_set():
                    break
                if self._on_status_change:
                    try:
                        self._on_status_change(entry, info)
                    except Exception:
                        logger.exception(
                            "on_status_change callback error for %s", entry.key
                        )

            if self._stop_event.is_set():
                break

            if self._on_poll_complete:
                try:
                    self._on_poll_complete()
                except Exception:
                    logger.exception("on_poll_complete callback error")

            self._stop_event.wait(self._interval)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    _noop_commit: Callable[[], None] = staticmethod(lambda: None)  # type: ignore[assignment]

    def _check_channel(
        self, entry: ChannelEntry
    ) -> tuple[list[tuple[ChannelEntry, StreamInfo]], Callable[[], None]]:
        if entry.platform == "youtube":
            return self._check_youtube(entry)
        return self._check_twitch(entry), self._noop_commit

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
            if info.is_live:
                live_key = _live_cache_key(entry.key)
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
            else:
                self._live_started_at.pop(_live_cache_key(entry.key), None)
                self._last_status[entry.key] = ChannelStatus(status=False)
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        prev_status = prev.status if isinstance(prev, ChannelStatus) else prev
        went_live = info.is_live and prev_status is not True
        if went_live:
            info.stream_status = "live"
            return [(entry, info)]
        return []

    # ------------------------------------------------------------------
    # YouTube: TIDUS videoId + style
    # ------------------------------------------------------------------
    def _check_youtube(
        self, entry: ChannelEntry
    ) -> tuple[list[tuple[ChannelEntry, StreamInfo]], Callable[[], None]]:
        try:
            fetcher = get_fetcher(entry.platform)
            items = fetcher.get_channel_items(entry.name)
        except Exception:
            logger.exception("Error fetching %s", entry.key)
            return [], self._noop_commit

        if not items:
            return self._check_youtube_fallback(entry, fetcher), self._noop_commit

        new_events: list[tuple[ChannelEntry, StreamInfo]] = []
        has_live = False
        has_upcoming = False
        live_items: list[VideoItem] = []
        upcoming_items: list[VideoItem] = []
        with self._lock:
            is_baselined = entry.key in self._youtube_baselined
            fallback_title = self._fallback_triggered_live.get(entry.key)

        unseen_live_items: list[VideoItem] = []
        pending_seen: list[tuple[str, str, str, str, str]] = []

        for item in items:
            if item.style == "LIVE":
                has_live = True
                live_key = _live_cache_key(entry.key, item.video_id)
                with self._lock:
                    started_at = item.started_at or self._live_started_at.get(live_key)
                    if not started_at:
                        started_at = _utc_now_iso()
                    self._live_started_at[live_key] = started_at
                item.started_at = started_at
                live_items.append(item)
            elif item.style == "UPCOMING":
                has_upcoming = True
                upcoming_items.append(item)

            if item.display_name:
                with self._lock:
                    self._display_names[entry.key] = item.display_name

            try:
                if self._db.is_seen(item.video_id, item.style):
                    continue
            except Exception:
                logger.exception("DB error for video %s", item.video_id)
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

        with self._lock:
            if has_live:
                active_live_ids = {item.video_id for item in live_items}
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if (
                        _entry_key_from_live_cache_key(key) != entry.key
                        or key.rsplit("|", 1)[-1] in active_live_ids
                    )
                }
                live_item = min(
                    live_items,
                    key=lambda item: _sort_datetime(
                        item.started_at, datetime.now(timezone.utc)
                    ),
                )
                self._last_status[entry.key] = ChannelStatus(
                    status=True,
                    url=live_item.url,
                    title=live_item.title,
                    started_at=live_item.started_at,
                )
            elif has_upcoming:
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                upcoming_item = min(
                    upcoming_items,
                    key=lambda item: _sort_datetime(
                        item.scheduled_start, datetime.max.replace(tzinfo=timezone.utc)
                    ),
                )
                self._last_status[entry.key] = ChannelStatus(
                    status="upcoming",
                    url=upcoming_item.url,
                    title=upcoming_item.title,
                    scheduled_start=upcoming_item.scheduled_start,
                )
            elif items:
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                self._last_status[entry.key] = ChannelStatus(status=False)
            self._youtube_baselined.add(entry.key)

        def commit() -> None:
            for args in pending_seen:
                try:
                    self._db.mark_seen(*args)
                except Exception:
                    logger.exception("DB error for video %s", args[0])

        return new_events, commit

    def _check_youtube_fallback(
        self, entry: ChannelEntry, fetcher: Any
    ) -> list[tuple[ChannelEntry, StreamInfo]]:
        try:
            info = fetcher.get_stream_info(entry.name)
        except Exception:
            logger.exception("Error fetching fallback status for %s", entry.key)
            return []

        if info is None:
            return []

        with self._lock:
            prev = self._last_status.get(entry.key)
            if info.is_live:
                live_key = _live_cache_key(entry.key)
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
            else:
                self._live_started_at = {
                    key: value
                    for key, value in self._live_started_at.items()
                    if _entry_key_from_live_cache_key(key) != entry.key
                }
                self._fallback_triggered_live.pop(entry.key, None)
                self._last_status[entry.key] = ChannelStatus(status=False)
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        prev_status = prev.status if isinstance(prev, ChannelStatus) else prev
        went_live = info.is_live and prev_status is not True
        if went_live:
            with self._lock:
                self._fallback_triggered_live[entry.key] = info.title
            info.stream_status = "live"
            return [(entry, info)]
        return []
