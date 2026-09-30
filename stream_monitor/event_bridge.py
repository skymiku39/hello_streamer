"""Drain monitor-thread events on the UI thread and apply side-effects."""

from __future__ import annotations

import logging
from typing import Any

from stream_monitor.browser_settings_model import coerce_browser_settings
from stream_monitor.channel_policy import (
    resolve_live_action,
    should_close_on_offline,
    should_prune_blank_tabs,
)
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


class PendingDisplayNamesStore:
    """Buffers display-name updates until row repaints are allowed."""

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}

    def clear(self) -> None:
        self._pending.clear()

    def update(self, names: dict[str, str]) -> None:
        if names:
            self._pending.update(names)

    def flush(self, sink: AppEventSink) -> None:
        if not self._pending:
            return
        sink.apply_display_names(dict(self._pending))
        self._pending.clear()


class MonitorEventBridge:
    """Subscribes to ``MonitorEventBus`` and maps events to UI side-effects."""

    def __init__(self, sink: AppEventSink, event_bus: MonitorEventBus) -> None:
        self._sink = sink
        self._bus = event_bus
        self._pending = PendingStatusStore()
        self._pending_display_names = PendingDisplayNamesStore()
        self._one_shot_after_cycle: int | None = None

    def reset(self) -> None:
        """Drop buffered status updates (called when monitoring stops)."""
        self._pending.clear()
        self._pending_display_names.clear()
        self._one_shot_after_cycle = None

    def arm_one_shot(self, *, after_cycle: int) -> None:
        """Accept only events from the next monitor cycle onward.

        A mode switch can happen while the background monitor is halfway
        through a poll.  Cycle IDs prevent that in-flight cycle from being
        mistaken for the requested one-shot run.
        """
        self._bus.clear()
        self.reset()
        self._one_shot_after_cycle = after_cycle

    def disarm_one_shot(self) -> None:
        self._one_shot_after_cycle = None

    def tick(self) -> None:
        sink = self._sink
        if sink.monitor_mode == "idle":
            self._bus.clear()
            return

        live_events: list[tuple[ChannelEntry, StreamInfo]] = []
        offline_events: list[tuple[Any, Any]] = []
        poll_complete = False
        latest_poll_activity: tuple[ChannelEntry, str, str] | None = None
        max_events_per_tick = 12
        events_processed = 0
        buffered = self._bus.drain()

        if self._one_shot_after_cycle is not None:
            boundary = self._one_shot_after_cycle
            buffered = [
                event
                for event in buffered
                if getattr(event, "cycle_id", 0) > boundary
            ]

        other_events: list[MonitorEvent] = []
        for event in buffered:
            if isinstance(event, PollActivity):
                if sink.is_channel_active(event.entry):
                    latest_poll_activity = (
                        event.entry,
                        event.phase,
                        event.display_name,
                    )
                else:
                    logger.debug(
                        "Ignoring stale poll activity for removed/disabled channel %s",
                        event.entry.key,
                    )
            else:
                other_events.append(event)

        for index, event in enumerate(other_events):
            if events_processed >= max_events_per_tick:
                self._bus.requeue(other_events[index:])
                break
            events_processed += 1
            if isinstance(event, ChannelWentLive):
                if sink.is_channel_active(event.entry):
                    live_events.append((event.entry, event.info))
                else:
                    logger.info(
                        "Ignoring stale live event for removed/disabled channel %s",
                        event.entry.key,
                    )
            elif isinstance(event, ChannelWentOffline):
                if sink.is_channel_active(event.entry):
                    offline_events.append((event.entry, event.offline_info))
                else:
                    logger.info(
                        "Ignoring stale offline event for removed/disabled channel %s",
                        event.entry.key,
                    )
            elif isinstance(event, PollWaiting):
                sink.set_poll_waiting()
            elif isinstance(event, PartialStatusUpdate):
                for key, status in event.statuses.items():
                    self._pending.merge(key, status)
                self._pending_display_names.update(event.display_names)
            elif isinstance(event, PollStatusUpdate):
                self._pending_display_names.update(event.display_names)
                for row in sink.iter_channel_rows():
                    if row.key in event.statuses:
                        self._pending.set(row.key, event.statuses[row.key])
                    elif row._status_state in ("live", "offline", "upcoming"):
                        self._pending.set(row.key, None)
                poll_complete = True

        for entry, info in live_events:
            if info.display_name:
                self._pending_display_names.update({entry.key: info.display_name})

        if not sink.defer_channel_row_repaints:
            self._pending_display_names.flush(sink)

        if latest_poll_activity is not None and not sink.defer_channel_row_repaints:
            entry, phase, display_name = latest_poll_activity
            sink.update_poll_subline(entry, phase, display_name)

        if not sink.defer_channel_row_repaints:
            applied = self._pending.flush(sink.iter_channel_rows(), limit=3)
            if applied:
                logger.debug(
                    "UI status flush: %d row(s), %d queued",
                    applied,
                    len(self._pending),
                )

        mode = sink.monitor_mode

        if poll_complete:
            sink.save_status_cache()
            raw_browser_settings = coerce_browser_settings(
                sink.config.get("browser_settings")
            )
            close_off_topic = bool(
                raw_browser_settings is not None
                and raw_browser_settings.enabled
                and raw_browser_settings.close_off_topic_pages
            )
            tracking_available = bool(
                raw_browser_settings is not None
                and sink.platform_services.window.tracking_available(
                    raw_browser_settings
                )
            )
            if should_prune_blank_tabs(
                mode=mode,
                close_off_topic=close_off_topic,
                tracking_available=tracking_available,
            ):
                try:
                    closed = sink.platform_services.window.prune_off_topic()
                    if closed:
                        logger.info(
                            "blank-tab prune closed %d window(s)", closed
                        )
                except Exception:
                    logger.exception("blank-tab prune failed")
            elif (
                mode in ("trigger", "trigger_once")
                and close_off_topic
                and not tracking_available
            ):
                logger.debug(
                    "Skipped blank-tab prune: HWND window tracking unavailable "
                    "(need dedicated profile and app mode or separate window)"
                )


        configured_action = sink.config.get("action", "open_and_stop")
        trigger_settings = sink.config.get("trigger_settings")
        browser_settings = sink.current_browser_settings()
        generation = sink.monitor_generation
        for entry, info in live_events:
            if not poll_complete and not sink.defer_channel_row_repaints:
                sink.apply_live_row_status(entry, info)

            decision = resolve_live_action(
                mode=mode,
                monitor_only=getattr(entry, "monitor_only", False),
                channel_mode=getattr(entry, "channel_mode", None),
                configured_action=configured_action,
                stream_status=info.stream_status or "live",
                trigger_settings=trigger_settings,
            )
            if decision.action is None:
                if decision.suppressed_reason == "monitor_only":
                    logger.info(
                        "Skipped action for %s (monitor_only)", entry.key
                    )
                continue
            if decision.plan is None:
                logger.warning(
                    "Live action policy returned no plan for %s (action=%s)",
                    entry.key,
                    decision.action,
                )
                continue

            # The bridge only submits the pure plan.  ActionCoordinator owns
            # worker lifetime and post-launch lifecycle transitions, keeping
            # the event drain independent from desktop side-effects.
            sink.execute_live_action(
                decision.plan, info, browser_settings, generation
            )

        close_on_offline = bool(
            browser_settings is not None and browser_settings.close_on_offline
        )
        offline_tracking_available = bool(
            browser_settings is not None
            and sink.platform_services.window.tracking_available(
                browser_settings
            )
        )
        for entry, offline_info in offline_events:
            if should_close_on_offline(
                mode=mode,
                monitor_only=getattr(entry, "monitor_only", False),
                channel_mode=getattr(entry, "channel_mode", None),
                wake_verify_active=sink.wake_verify_active,
                close_on_offline=close_on_offline,
                tracking_available=offline_tracking_available,
            ):
                sink.handle_channel_offline(entry, offline_info)

        # Consume a global one-shot only after all events from this completed
        # cycle have been dispatched.  This keeps trigger actions intact while
        # still guaranteeing that the monitor will not begin another cycle.
        if poll_complete and mode in ("trigger_once", "watch_once"):
            self._one_shot_after_cycle = None
            sink.on_monitor_cycle_complete()

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
        return status.is_live
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
