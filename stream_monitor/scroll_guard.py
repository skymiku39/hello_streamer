"""Defer heavy row repaints while the user is scrolling a list view."""

from __future__ import annotations

import math
import time
from typing import Any, Callable

_SCROLL_IDLE_MS = 200


class ScrollRepaintGuard:
    """Pause channel-row repaints during scroll; resume after a short idle gap.

    The channel list is a single Tk ``Canvas`` surface.  The guard still
    defers monitor-driven state updates while the user is dragging the
    scrollbar so the scroll path remains input-only; events are not dropped,
    because they stay queued in ``PendingStatusStore``.
    """

    def __init__(
        self,
        scroll_frame: Any,
        root: Any,
        *,
        idle_ms: int = _SCROLL_IDLE_MS,
        on_idle: Callable[[], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._root = root
        self._idle_ms = idle_ms
        self._on_idle = on_idle
        self._clock = clock or time.monotonic
        self._canvas = scroll_frame._parent_canvas
        self._scrollbar = scroll_frame._scrollbar
        self._defer = False
        self._idle_after: str | None = None
        self._last_activity_at: float | None = None

        for sequence in ("<MouseWheel>", "<ButtonPress-1>"):
            self._canvas.bind(sequence, self._on_scroll_activity, add="+")
        for sequence in ("<ButtonPress-1>", "<B1-Motion>"):
            self._scrollbar.bind(sequence, self._on_scroll_activity, add="+")

        scrollbar_set = self._scrollbar.set

        def yscroll_wrapper(first: str, last: str) -> None:
            scrollbar_set(first, last)
            self._on_scroll_activity()

        self._canvas.configure(yscrollcommand=yscroll_wrapper)

    @property
    def repaints_deferred(self) -> bool:
        return self._defer

    def destroy(self) -> None:
        """Cancel pending idle timer to prevent callbacks after teardown."""
        if self._idle_after is not None:
            try:
                self._root.after_cancel(self._idle_after)
            except Exception:
                pass
            self._idle_after = None
        self._last_activity_at = None
        self._defer = False

    def _on_scroll_activity(self, _event: Any = None) -> None:
        if not self._root.winfo_exists():
            return
        self._defer = True
        self._last_activity_at = self._clock()
        # Keep a single trailing-edge timer.  yscrollcommand can be called
        # several times for one wheel/drag gesture; cancelling and recreating
        # an ``after`` for every callback makes the scroll path unnecessarily
        # expensive on Tk/Windows.
        if self._idle_after is None:
            self._idle_after = self._root.after(self._idle_ms, self._on_scroll_idle)

    def _on_scroll_idle(self) -> None:
        self._idle_after = None
        if not self._root.winfo_exists():
            self._last_activity_at = None
            self._defer = False
            return

        last_activity_at = self._last_activity_at
        if last_activity_at is not None:
            elapsed_ms = (self._clock() - last_activity_at) * 1000
            remaining_ms = self._idle_ms - elapsed_ms
            if remaining_ms > 0:
                self._idle_after = self._root.after(
                    max(1, math.ceil(remaining_ms)), self._on_scroll_idle
                )
                return

        self._last_activity_at = None
        self._defer = False
        if self._on_idle is not None:
            self._on_idle()
