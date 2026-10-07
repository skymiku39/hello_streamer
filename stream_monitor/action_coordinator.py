"""Coordinate background action execution and lifecycle follow-ups.

This module deliberately knows nothing about Tk, Win32, or desktop APIs.  The
application supplies a UI scheduler and lifecycle callbacks, while the
coordinator owns action-worker lifetime, stale-generation checks, and the rule
that only one terminal action may win within a monitor generation.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from stream_monitor.action_executor import ActionResult
from stream_monitor.action_plan import ActionPlan, LifecycleEffect
from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.fetcher.base import StreamInfo

logger = logging.getLogger(__name__)

ActionRunner = Callable[..., ActionResult]
ScheduleUi = Callable[[Callable[[], None]], Any]
GenerationIsCurrent = Callable[[int], bool]
LifecycleCallback = Callable[[], None]


class ActionCoordinator:
    """Run action plans safely and serialize terminal effects per generation."""

    def __init__(
        self,
        *,
        runner: ActionRunner,
        schedule_ui: ScheduleUi,
        generation_is_current: GenerationIsCurrent,
        on_stop: LifecycleCallback,
        on_exit: LifecycleCallback,
        max_pending: int = 32,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._runner = runner
        self._schedule_ui = schedule_ui
        self._generation_is_current = generation_is_current
        self._on_stop = on_stop
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._closed = False
        self._terminal_generations: set[int] = set()
        self._cancelled_generations: set[int] = set()
        self._workers: set[threading.Thread] = set()
        self._pending_keys: set[tuple[int, str, str, str]] = set()
        self._max_pending = max_pending

    def submit(
        self,
        plan: ActionPlan,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
        generation: int,
    ) -> bool:
        """Queue one action; return False if stale, closed, or already pending."""
        if not self._generation_is_current(generation):
            logger.debug(
                "Discarded stale action before queueing: generation=%s action=%s",
                generation,
                plan.key,
            )
            return False

        with self._lock:
            if self._closed:
                return False
            # A monitor restart creates a new generation. Do not retain a
            # successful terminal claim from an older session indefinitely.
            self._terminal_generations.intersection_update({generation})
            self._cancelled_generations.intersection_update({generation})
            if generation in self._cancelled_generations:
                return False
            if plan.is_terminal and generation in self._terminal_generations:
                logger.info(
                    "Skipped duplicate terminal action: generation=%s action=%s",
                    generation,
                    plan.key,
                )
                return False
            if plan.is_terminal:
                self._terminal_generations.add(generation)

            pending_key = (generation, info.platform, info.channel, plan.key)
            if pending_key in self._pending_keys:
                logger.info(
                    "Skipped duplicate pending action: generation=%s platform=%s "
                    "channel=%s action=%s",
                    generation,
                    info.platform,
                    info.channel,
                    plan.key,
                )
                if plan.is_terminal:
                    self._terminal_generations.discard(generation)
                return False
            if len(self._pending_keys) >= self._max_pending:
                logger.warning(
                    "Skipped action because the pending queue is full: limit=%s "
                    "generation=%s platform=%s channel=%s action=%s",
                    self._max_pending,
                    generation,
                    info.platform,
                    info.channel,
                    plan.key,
                )
                if plan.is_terminal:
                    self._terminal_generations.discard(generation)
                return False
            self._pending_keys.add(pending_key)

            worker = threading.Thread(
                target=self._run,
                args=(plan, info, browser_settings, generation, pending_key),
                daemon=True,
                name=f"live-action-{info.platform}-{info.channel}",
            )
            self._workers.add(worker)

        try:
            worker.start()
        except Exception:
            with self._lock:
                self._workers.discard(worker)
                self._pending_keys.discard(pending_key)
                if plan.is_terminal:
                    self._terminal_generations.discard(generation)
            raise
        return True

    def shutdown(self) -> None:
        """Prevent new work and lifecycle callbacks during application exit."""
        with self._lock:
            self._closed = True
            self._terminal_generations.clear()
            self._cancelled_generations.clear()
            self._pending_keys.clear()

    def cancel_generation(self, generation: int) -> None:
        """Cancel queued/in-flight effects belonging to one monitor session."""
        with self._lock:
            self._cancelled_generations.add(generation)

    def has_active_work(self) -> bool:
        """True while any action worker or pending queue entry is still live."""
        with self._lock:
            if self._pending_keys:
                return True
            return any(worker.is_alive() for worker in self._workers)

    def _run(
        self,
        plan: ActionPlan,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
        generation: int,
        pending_key: tuple[int, str, str, str],
    ) -> None:
        current = threading.current_thread()
        result: ActionResult | None = None
        lifecycle_scheduled = threading.Event()
        try:
            if self._is_cancelled(generation):
                return

            result = self._runner(
                plan,
                info,
                stop_fn=self._lifecycle_callback(
                    generation,
                    LifecycleEffect.STOP_MONITOR,
                    lifecycle_scheduled,
                ),
                exit_fn=self._lifecycle_callback(
                    generation,
                    LifecycleEffect.EXIT_APP,
                    lifecycle_scheduled,
                ),
                browser_settings=browser_settings,
                is_cancelled=lambda: self._is_cancelled(generation),
            )
            if not result.succeeded:
                logger.warning(
                    "Action did not complete successfully: action=%s status=%s "
                    "platform=%s channel=%s",
                    plan.key,
                    result.status,
                    info.platform,
                    info.channel,
                )
        except Exception:
            logger.exception(
                "Action worker failed: action=%s platform=%s channel=%s",
                plan.key,
                info.platform,
                info.channel,
            )
        finally:
            with self._lock:
                self._workers.discard(current)
                self._pending_keys.discard(pending_key)
                if (
                    plan.is_terminal
                    and (result is None or not result.succeeded)
                    and not lifecycle_scheduled.is_set()
                    and not self._closed
                ):
                    # A failed terminal action should not permanently block a
                    # later event from recovering, unless its stop/exit is
                    # already queued (e.g. only the notification failed).
                    self._terminal_generations.discard(generation)

    def _is_cancelled(self, generation: int) -> bool:
        with self._lock:
            closed = self._closed or generation in self._cancelled_generations
        return closed or not self._generation_is_current(generation)

    def _lifecycle_callback(
        self,
        generation: int,
        effect: LifecycleEffect,
        scheduled: threading.Event,
    ) -> LifecycleCallback:
        def callback() -> None:
            if self._is_cancelled(generation):
                return
            if effect is LifecycleEffect.STOP_MONITOR:
                target = self._on_stop
                label = "stop"
            elif effect is LifecycleEffect.EXIT_APP:
                target = self._on_exit
                label = "exit"
            else:
                return

            def invoke() -> None:
                if self._is_cancelled(generation):
                    logger.debug(
                        "Skipped cancelled lifecycle effect: generation=%s effect=%s",
                        generation,
                        label,
                    )
                    return
                target()

            # Propagate scheduler errors so the executor reports failure and
            # the terminal claim can be released for a later retry.
            self._schedule_ui(invoke)
            scheduled.set()

        return callback
