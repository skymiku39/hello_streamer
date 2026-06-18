"""Drain monitor-thread events on the UI thread and apply side-effects."""

from __future__ import annotations

import logging
import threading
from typing import Any

from stream_monitor.browser_settings_model import coerce_browser_settings
from stream_monitor.domain import ChannelEntry, ChannelStatus
from stream_monitor.event_sink import AppEventSink, ChannelRowView
from stream_monitor.events import (
    ChannelWentLive,
    ChannelWentOffline,
    MonitorEvent,
    MonitorEventBus,
    PartialStatusUpdate,
    PollActivity,
    PollStatusUpdate,
    PollWaiting,
)
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.notifier import (
    action_for_stream_status,
    browser_window_tracking_available,
    execute_action,
    prune_off_topic_tracked_windows,
)

logger = logging.getLogger(__name__)


class PendingStatusStore:
    """Buffers per-channel status updates and flushes them to rows in batches.

    Keeping this state inside the bridge (rather than on the UI window) lets the
    bridge depend only on the public ``AppEventSink`` contract.
    """

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}

    def __len__(self) -> int:
        return len(self._pending)

    def clear(self) -> None:
        self._pending.clear()

    def merge(self, key: str, status: Any) -> None:
        self._pending[key] = prefer_richer_offline_status(
            self._pending.get(key), status
        )

    def set(self, key: str, status: Any) -> None:
        self._pending[key] = status

    def flush(self, rows: list[ChannelRowView], *, limit: int = 3) -> int:
        """Apply queued row status updates in small batches to keep UI responsive."""
        applied = 0
        keys = list(self._pending.keys())
        live_keys = [
            key for key in keys if pending_status_is_live(self._pending[key])
        ]
        ordered = live_keys + [key for key in keys if key not in live_keys]
        for key in ordered:
            if applied >= limit:
                break
            if key not in self._pending:
                continue
            status = self._pending.pop(key)
            for row in rows:
                if row.key == key:
                    if row_has_richer_offline_detail(row, status):
                        break
                    row.set_status(status)
                    applied += 1
                    break
        return applied


class MonitorEventBridge:
    """Subscribes to ``MonitorEventBus`` and maps events to UI side-effects."""

    def __init__(self, sink: AppEventSink, event_bus: MonitorEventBus) -> None:
        self._sink = sink
        self._bus = event_bus
        self._pending = PendingStatusStore()

    def reset(self) -> None:
        """Drop buffered status updates (called when monitoring stops)."""
        self._pending.clear()

    def tick(self) -> None:
        sink = self._sink
        if sink.monitor_mode == "idle":
            self._bus.clear()
            return

        live_events: list[tuple[ChannelEntry, StreamInfo]] = []
        offline_events: list[tuple[Any, Any]] = []
        poll_complete = False
        latest_poll_activity: tuple[ChannelEntry, str, str] | None = None
        pending_names: dict[str, str] = {}
        max_events_per_tick = 12
        events_processed = 0
        buffered = self._bus.drain()

        other_events: list[MonitorEvent] = []
        for event in buffered:
            if isinstance(event, PollActivity):
                latest_poll_activity = (
                    event.entry,
                    event.phase,
                    event.display_name,
                )
            else:
                other_events.append(event)

        for index, event in enumerate(other_events):
            if events_processed >= max_events_per_tick:
                self._bus.requeue(other_events[index:])
                break
            events_processed += 1
            if isinstance(event, ChannelWentLive):
                live_events.append((event.entry, event.info))
            elif isinstance(event, ChannelWentOffline):
                offline_events.append((event.entry, event.offline_info))
            elif isinstance(event, PollWaiting):
                sink.set_poll_waiting()
            elif isinstance(event, PartialStatusUpdate):
                for key, status in event.statuses.items():
                    self._pending.merge(key, status)
                pending_names.update(event.display_names)
            elif isinstance(event, PollStatusUpdate):
                pending_names.update(event.display_names)
                for row in sink.iter_channel_rows():
                    if row.key in event.statuses:
                        self._pending.set(row.key, event.statuses[row.key])
                    elif row._status_state in ("live", "offline", "upcoming"):
                        self._pending.set(row.key, None)
                poll_complete = True

        if pending_names:
            sink.apply_display_names(pending_names)

        if latest_poll_activity is not None:
            entry, phase, display_name = latest_poll_activity
            sink.update_poll_subline(entry, phase, display_name)

        applied = self._pending.flush(sink.iter_channel_rows(), limit=3)
        if applied:
            logger.debug(
                "UI status flush: %d row(s), %d queued",
                applied,
                len(self._pending),
            )

        trigger_enabled = sink.monitor_mode == "trigger"

        if poll_complete:
            raw_browser_settings = coerce_browser_settings(
                sink.config.get("browser_settings")
            )
            if (
                trigger_enabled
                and raw_browser_settings is not None
                and raw_browser_settings.enabled
                and raw_browser_settings.close_off_topic_pages
                and browser_window_tracking_available(raw_browser_settings)
            ):
                try:
                    closed = prune_off_topic_tracked_windows()
                    if closed:
                        logger.info(
                            "off-topic prune closed %d window(s)", closed
                        )
                except Exception:
                    logger.exception("off-topic prune failed")
            elif (
                trigger_enabled
                and raw_browser_settings is not None
                and raw_browser_settings.close_off_topic_pages
                and not browser_window_tracking_available(raw_browser_settings)
            ):
                logger.debug(
                    "Skipped off-topic prune: HWND window tracking unavailable "
                    "(need dedicated profile and app mode or separate window)"
                )

        configured_action = sink.config.get("action", "open_and_stop")
        browser_settings = sink.current_browser_settings()
        should_stop = False
        should_exit = False

        for entry, info in live_events:
            if info.display_name:
                sink.apply_display_names({entry.key: info.display_name})
            if not poll_complete:
                sink.apply_live_row_status(entry, info)

            if not trigger_enabled:
                continue

            if getattr(entry, "monitor_only", False):
                logger.info(
                    "Skipped action for %s (monitor_only)", entry.key
                )
                continue

            action = action_for_stream_status(configured_action, info)
            if action is None:
                continue

            if action in ("open_and_stop", "open_and_keep", "open_and_exit"):
                threading.Thread(
                    target=sink.execute_live_action,
                    args=(action, info, browser_settings),
                    daemon=True,
                ).start()
                if action == "open_and_stop":
                    should_stop = True
                elif action == "open_and_exit":
                    should_exit = True
            else:
                noop = lambda: None  # noqa: E731
                execute_action(
                    action,
                    info,
                    stop_fn=noop,
                    exit_fn=noop,
                    browser_settings=browser_settings,
                )

        skip_close_on_offline = sink.wake_verify_active
        if (
            trigger_enabled
            and offline_events
            and browser_settings is not None
            and browser_settings.close_on_offline
            and browser_window_tracking_available(browser_settings)
            and not skip_close_on_offline
        ):
            for entry, offline_info in offline_events:
                if getattr(entry, "monitor_only", False):
                    continue
                sink.handle_channel_offline(entry, offline_info)

        if should_stop:
            sink.on_stop(is_user_action=False)
        elif should_exit:
            sink.quit_app()

        sink.maybe_restart_dead_monitor()


def prefer_richer_offline_status(old: Any, new: Any) -> Any:
    """Drop tier-1 pending previews that would erase tier-2 offline timing."""
    if not isinstance(new, ChannelStatus) or new.status is not False:
        return new
    if new.ended_at_source != "pending":
        return new
    if (
        isinstance(old, ChannelStatus)
        and old.status is False
        and old.ended_at_source != "pending"
    ):
        return old
    return new


def pending_status_is_live(status: Any) -> bool:
    if isinstance(status, ChannelStatus):
        return status.status is True
    return status is True


def row_has_richer_offline_detail(row: Any, status: Any) -> bool:
    if not isinstance(status, ChannelStatus) or status.status is not False:
        return False
    if status.ended_at_source != "pending":
        return False
    return (
        row._status_state == "offline"
        and row._ended_at_source != "pending"
    )
