"""Channel-list scroll: hide embedded content while moving, defer row repaints."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterable
from typing import Any

import customtkinter as ctk
from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass

logger = logging.getLogger(__name__)

_SCROLL_IDLE_MS = 120


def _force_redraw(widget: Any) -> None:
    """Ask a CTk widget subtree to repaint after a canvas viewport jump."""
    if isinstance(widget, CTkBaseClass):
        try:
            widget._draw()
        except Exception:
            logger.debug("CTk redraw skipped for %r", widget, exc_info=True)
    for child in widget.winfo_children():
        _force_redraw(child)


class ChannelListScrollController:
    """Anti-ghost scrolling for the main channel ``CTkScrollableFrame``.

  CustomTkinter scrolls by shifting a ``Canvas`` window that embeds every row.
  On Windows the inner CTk canvases often leave trails when the outer viewport
  moves, especially while monitor status updates repaint rows. This controller:

  1. Hides the embedded window while the viewport moves (solid bg only).
  2. Defers row ``configure()`` / status flushes until scrolling idles.
  3. Replaces the frame's wheel handler so we do not double-scroll.
  4. Forces a CTk subtree redraw when content is shown again.
    """

    def __init__(
        self,
        scroll_frame: ctk.CTkScrollableFrame,
        root: ctk.CTk,
        *,
        rows_provider: Callable[[], Iterable[Any]],
        idle_ms: int = _SCROLL_IDLE_MS,
        on_idle: Callable[[], None] | None = None,
    ) -> None:
        self._scroll_frame = scroll_frame
        self._root = root
        self._rows_provider = rows_provider
        self._idle_ms = idle_ms
        self._on_idle = on_idle
        self._canvas = scroll_frame._parent_canvas
        self._scrollbar = scroll_frame._scrollbar
        self._window_id = scroll_frame._create_window_id
        self._defer_repaints = False
        self._content_hidden = False
        self._idle_after: str | None = None
        self._original_wheel = scroll_frame._mouse_wheel_all

        for sequence in ("<ButtonPress-1>", "<B1-Motion>"):
            self._scrollbar.bind(sequence, self._on_scrollbar_drag, add="+")
        self._scrollbar.bind(
            "<ButtonRelease-1>", self._on_scrollbar_release, add="+"
        )

        original_yscroll = self._canvas.cget("yscrollcommand")

        def yscroll_wrapper(first: str, last: str) -> None:
            if callable(original_yscroll):
                original_yscroll(first, last)
            self._begin_scroll_motion()

        self._canvas.configure(yscrollcommand=yscroll_wrapper)
        scroll_frame._mouse_wheel_all = self._mouse_wheel_all

    @property
    def repaints_deferred(self) -> bool:
        return self._defer_repaints or self._content_hidden

    def _begin_scroll_motion(self) -> None:
        self._defer_repaints = True
        self._hide_content()
        self._schedule_scroll_idle()

    def _hide_content(self) -> None:
        if self._content_hidden:
            return
        try:
            self._canvas.itemconfigure(self._window_id, state="hidden")
            self._content_hidden = True
        except Exception:
            logger.debug("Failed to hide scroll content", exc_info=True)

    def _show_content(self) -> None:
        if not self._content_hidden:
            return
        try:
            self._canvas.itemconfigure(self._window_id, state="normal")
        except Exception:
            logger.debug("Failed to show scroll content", exc_info=True)
        self._content_hidden = False
        for row in self._rows_provider():
            _force_redraw(row)
        try:
            self._canvas.update_idletasks()
        except Exception:
            pass

    def _schedule_scroll_idle(self) -> None:
        if self._idle_after is not None:
            self._root.after_cancel(self._idle_after)
        self._idle_after = self._root.after(self._idle_ms, self._on_scroll_idle)

    def _on_scroll_idle(self) -> None:
        self._idle_after = None
        self._defer_repaints = False
        self._show_content()
        if self._on_idle is not None:
            self._on_idle()

    def _on_scrollbar_drag(self, _event: Any = None) -> None:
        self._begin_scroll_motion()

    def _on_scrollbar_release(self, _event: Any = None) -> None:
        self._schedule_scroll_idle()

    def _mouse_wheel_all(self, event: Any) -> str | None:
        if not self._scroll_frame.check_if_master_is_canvas(event.widget):
            return self._original_wheel(event)

        self._begin_scroll_motion()
        if sys.platform.startswith("win"):
            delta = -int(event.delta / 6)
            if getattr(self._scroll_frame, "_shift_pressed", False):
                if self._canvas.xview() != (0.0, 1.0):
                    self._canvas.xview("scroll", delta, "units")
            elif self._canvas.yview() != (0.0, 1.0):
                self._canvas.yview("scroll", delta, "units")
        elif sys.platform == "darwin":
            delta = -event.delta
            if getattr(self._scroll_frame, "_shift_pressed", False):
                if self._canvas.xview() != (0.0, 1.0):
                    self._canvas.xview("scroll", delta, "units")
            elif self._canvas.yview() != (0.0, 1.0):
                self._canvas.yview("scroll", delta, "units")
        else:
            delta = -event.delta
            if getattr(self._scroll_frame, "_shift_pressed", False):
                if self._canvas.xview() != (0.0, 1.0):
                    self._canvas.xview("scroll", delta, "units")
            elif self._canvas.yview() != (0.0, 1.0):
                self._canvas.yview("scroll", delta, "units")
        return "break"

    # Backward-compatible alias used during the first anti-ghost iteration.
    _on_scroll_activity = _begin_scroll_motion


# Legacy name kept for imports/tests that still reference ScrollRepaintGuard.
ScrollRepaintGuard = ChannelListScrollController
