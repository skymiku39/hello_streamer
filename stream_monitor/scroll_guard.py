"""Defer heavy row repaints while the user is scrolling a CTkScrollableFrame."""

from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

_SCROLL_IDLE_MS = 200


class ScrollRepaintGuard:
    """Pause channel-row repaints during scroll; resume after a short idle gap.

    CustomTkinter scroll areas embed widgets in a Tk ``Canvas``. Repainting
    rounded CTk rows while the canvas viewport moves often leaves trails on
    Windows. Deferring ``configure()``-driven row updates until scrolling stops
    keeps the scroll path cheap without dropping monitor events (they stay
    queued in ``PendingStatusStore``).
    """

    def __init__(
        self,
        scroll_frame: ctk.CTkScrollableFrame,
        root: ctk.CTk,
        *,
        idle_ms: int = _SCROLL_IDLE_MS,
        on_idle: Callable[[], None] | None = None,
    ) -> None:
        self._root = root
        self._idle_ms = idle_ms
        self._on_idle = on_idle
        self._canvas = scroll_frame._parent_canvas
        self._scrollbar = scroll_frame._scrollbar
        self._defer = False
        self._idle_after: str | None = None

        for sequence in ("<MouseWheel>", "<ButtonPress-1>"):
            self._canvas.bind(sequence, self._on_scroll_activity, add="+")
        for sequence in ("<ButtonPress-1>", "<B1-Motion>"):
            self._scrollbar.bind(sequence, self._on_scroll_activity, add="+")

        original_yscroll = self._canvas.cget("yscrollcommand")

        def yscroll_wrapper(first: str, last: str) -> None:
            if callable(original_yscroll):
                original_yscroll(first, last)
            self._on_scroll_activity()

        self._canvas.configure(yscrollcommand=yscroll_wrapper)

    @property
    def repaints_deferred(self) -> bool:
        return self._defer

    def _on_scroll_activity(self, _event: Any = None) -> None:
        self._defer = True
        if self._idle_after is not None:
            self._root.after_cancel(self._idle_after)
        self._idle_after = self._root.after(self._idle_ms, self._on_scroll_idle)

    def _on_scroll_idle(self) -> None:
        self._idle_after = None
        self._defer = False
        try:
            self._canvas.update_idletasks()
        except Exception:
            pass
        if self._on_idle is not None:
            self._on_idle()
