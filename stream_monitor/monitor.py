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
        on_went_offline: OfflineCallback | None = None,
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
        # Consecutive-miss counter per live_cache_key. Incremented every poll a
        # previously-live entry/video appears "not live"; reset when it comes
        # back. Only when the counter reaches _OFFLINE_STRIKE_THRESHOLD do we
        # commit to the offline edge (clear last_status, emit went_offline).
        self._offline_strikes: dict[str, int] = {}
        # Filled by _check_* helpers within a single poll cycle; drained by
        # _run after went_live dispatch.
        self._pending_offline_events: list[tuple[ChannelEntry, OfflineInfo]] = []
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
            for entry in self._entries:
                if entry.enabled and not old_enabled.get(entry.key, True):
                    self._last_status.pop(entry.key, None)
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
                self._pending_offline_events.clear()

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

            # Dispatch went-offline events *after* went-live so the UI sees
            # transitions in a sensible order if both occur in the same poll.
            with self._lock:
                offline_batch = list(self._pending_offline_events)
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

        live_key = _live_cache_key(entry.key)

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

            # ── Anti-flap guard ──────────────────────────────────────────
            # If we previously saw this channel as LIVE and a single poll
            # comes back "not live", treat it as a transient API miss and
            # keep the last_status untouched until the strike threshold is
            # reached. Without this guard a Twitch GQL hiccup creates a
            # phantom went_offline → went_live edge pair (duplicate toast
            # + close_on_offline closing the player we just opened).
            if not info.is_live and prev_status is True:
                strikes = self._offline_strikes.get(live_key, 0) + 1
                if strikes < _OFFLINE_STRIKE_THRESHOLD:
                    self._offline_strikes[live_key] = strikes
                    if info.display_name:
                        self._display_names[entry.key] = info.display_name
                    logger.info(
                        "Twitch %s: ignoring transient offline reading (%d/%d)",
                        entry.key, strikes, _OFFLINE_STRIKE_THRESHOLD,
                    )
                    return []
                # Threshold reached → commit to offline below.
                self._offline_strikes.pop(live_key, None)
            else:
                # Either currently live, or already offline — no flap to track.
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
            else:
                self._live_started_at.pop(live_key, None)
                self._last_status[entry.key] = ChannelStatus(status=False)
            if info.display_name:
                self._display_names[entry.key] = info.display_name

        went_live = info.is_live and prev_status is not True
        went_offline = (not info.is_live) and prev_status is True
        if went_offline:
            with self._lock:
                stale_payload = self._live_payload.pop(live_key, None)
                self._pending_offline_events.append(
                    (entry, stale_payload or OfflineInfo(
                        url=(prev.url if isinstance(prev, ChannelStatus) else ""),
                        title=(prev.title if isinstance(prev, ChannelStatus) else ""),
                        platform=entry.platform,
                        name=entry.name,
                        display_name=self._display_names.get(entry.key, ""),
                    ))
                )
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
            # Detect went-offline edges: anything in self._live_payload that
            # belongs to this entry but is no longer in this poll's LIVE set.
            #
            # The TIDUS feed occasionally drops a still-live video from the
            # /streams listing for a single poll (channel re-rendering,
            # YouTube edge cache lag, "live now" tab partially populated).
            # Without a strike guard those single-poll dropouts produce a
            # phantom went_offline → went_live edge pair, duplicating the
            # "stream went live" notification. We therefore require two
            # consecutive misses *of the same video_id* before committing
            # to the offline edge — and we treat strike-pending video_ids
            # as still active so _live_started_at isn't wiped prematurely.
            # Track the original (genuinely-present) LIVE set separately from
            # the "kept-alive by strike-pending" set, so we can clear strikes
            # only for video_ids that the feed actually returned this poll.
            observed_live_ids = {item.video_id for item in live_items}
            active_live_ids = set(observed_live_ids)
            stale_candidates = [
                key
                for key in list(self._live_payload.keys())
                if _entry_key_from_live_cache_key(key) == entry.key
                and key.rsplit("|", 1)[-1] not in active_live_ids
            ]
            has_strike_pending = False
            for stale_key in stale_candidates:
                strikes = self._offline_strikes.get(stale_key, 0) + 1
                if strikes < _OFFLINE_STRIKE_THRESHOLD:
                    self._offline_strikes[stale_key] = strikes
                    # Pretend the missing video_id is still live this poll
                    # so the cleanup of _live_started_at below leaves its
                    # started_at intact for the next strike window.
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

            # Clear strikes only for video_ids that the feed *actually* showed
            # as LIVE this poll — they're provably back, so the prior miss was
            # noise. (We must NOT clear strikes for ids we only kept alive via
            # the strike-pending branch above; those still need to accumulate.)
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
            elif has_strike_pending:
                # Hold the prior LIVE display steady while we wait out the
                # transient miss. We deliberately don't touch _last_status
                # or _live_started_at so the UI doesn't flash OFFLINE for
                # a single poll between transient feed dropouts.
                pass
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

        live_key = _live_cache_key(entry.key)

        with self._lock:
            prev = self._last_status.get(entry.key)
            prev_status = prev.status if isinstance(prev, ChannelStatus) else prev

            # ── Anti-flap guard (mirrors _check_twitch) ──────────────────
            # The fallback path is used when the TIDUS feed returns nothing,
            # which is exactly when a single transient miss is most likely.
            # Same two-strike rule prevents the resulting phantom edge pair.
            if not info.is_live and prev_status is True:
                strikes = self._offline_strikes.get(live_key, 0) + 1
                if strikes < _OFFLINE_STRIKE_THRESHOLD:
                    self._offline_strikes[live_key] = strikes
                    if info.display_name:
                        self._display_names[entry.key] = info.display_name
                    logger.info(
                        "YouTube fallback %s: ignoring transient offline reading (%d/%d)",
                        entry.key, strikes, _OFFLINE_STRIKE_THRESHOLD,
                    )
                    return []
                self._offline_strikes.pop(live_key, None)
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

        went_live = info.is_live and prev_status is not True
        went_offline = (not info.is_live) and prev_status is True
        if went_offline:
            with self._lock:
                stale_payload = self._live_payload.pop(live_key, None)
                self._pending_offline_events.append(
                    (entry, stale_payload or OfflineInfo(
                        url=(prev.url if isinstance(prev, ChannelStatus) else ""),
                        title=(prev.title if isinstance(prev, ChannelStatus) else ""),
                        platform=entry.platform,
                        name=entry.name,
                        display_name=self._display_names.get(entry.key, ""),
                    ))
                )
        if went_live:
            with self._lock:
                self._fallback_triggered_live[entry.key] = info.title
            info.stream_status = "live"
            return [(entry, info)]
        return []
