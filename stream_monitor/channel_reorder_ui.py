"""Overlay-based channel list reorder mode.

Keeps channel rows packed during drag so scroll position and the scrollbar
thumb stay stable. A gap indicator and a floating ghost card are positioned
with ``place`` on the scroll inner frame; row repaints from the monitor are
deferred for the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from stream_monitor.channel_reorder import (
    ROW_BODY_HEIGHT,
    ROW_SLOT_HEIGHT,
    apply_list_move,
    target_index_for_content_y,
)

if TYPE_CHECKING:
    from stream_monitor.channel_row import ChannelRow

_REORDER_SOURCE_DIM = "#1a2744"
_GAP_FG = "#0f3460"
_GAP_BORDER = "#2196F3"
_GHOST_FG = "#243052"
_GHOST_BORDER = "#2196F3"


def gap_place_y(*, list_origin_y: int, target_index: int) -> int:
    """Content Y for the drop-gap overlay before ``target_index``."""
    return list_origin_y + target_index * ROW_SLOT_HEIGHT


def ghost_place_y(
    *,
    list_origin_y: int,
    pointer_content_y: float,
    num_rows: int,
    slot_height: int = ROW_SLOT_HEIGHT,
    body_height: int = ROW_BODY_HEIGHT,
) -> int:
    """Content Y for the floating ghost card centered on the pointer."""
    if num_rows <= 0:
        return list_origin_y
    relative = pointer_content_y - list_origin_y
    max_y = list_origin_y + max(0, num_rows - 1) * slot_height
    centered = list_origin_y + relative - body_height // 2
    return int(max(list_origin_y, min(centered, max_y)))


class ChannelReorderMode:
    """Manages an in-progress drag-reorder session (overlay preview, no repack)."""

    def __init__(
        self,
        scroll_frame: ctk.CTkScrollableFrame,
        *,
        row_slot_height: int = ROW_SLOT_HEIGHT,
        row_body_height: int = ROW_BODY_HEIGHT,
        on_debug: Callable[..., None] | None = None,
    ) -> None:
        self._scroll_frame = scroll_frame
        self._inner = scroll_frame._parent_frame
        self._canvas = scroll_frame._parent_canvas
        self._slot_height = row_slot_height
        self._body_height = row_body_height
        self._on_debug = on_debug or (lambda *_a, **_k: None)
        self._gap: ctk.CTkFrame | None = None
        self._ghost: ctk.CTkFrame | None = None
        self._ghost_label: ctk.CTkLabel | None = None
        self._active = False
        self._source_index = 0
        self._target_index = 0
        self._source_row: ChannelRow | None = None
        self._list_origin_y = 0
        self._last_y_root: int | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def source_index(self) -> int:
        return self._source_index

    @property
    def target_index(self) -> int:
        return self._target_index

    @property
    def source_row(self) -> ChannelRow | None:
        return self._source_row

    def pointer_content_y(self, y_root: int) -> float:
        canvas_y = y_root - self._canvas.winfo_rooty()
        return self._canvas.canvasy(canvas_y)

    def target_for_pointer(self, y_root: int, *, num_rows: int) -> int:
        relative_y = self.pointer_content_y(y_root) - self._list_origin_y
        return target_index_for_content_y(
            relative_y,
            source_index=self._source_index,
            num_rows=num_rows,
            slot_height=self._slot_height,
        )

    def begin(
        self,
        source_row: ChannelRow,
        *,
        source_index: int,
        list_origin_y: int,
        num_rows: int,
        y_root: int | None = None,
    ) -> None:
        self._active = True
        self._source_row = source_row
        self._source_index = source_index
        self._target_index = source_index
        self._list_origin_y = list_origin_y
        source_row.set_reorder_highlight(True)
        self._set_source_dim(True)
        self._ensure_gap()
        self._ensure_ghost(source_row)
        if y_root is not None:
            self.track_pointer(y_root, num_rows=num_rows)
        else:
            self._position_ghost_at_source(num_rows)
        self._refresh_gap(num_rows)
        self._on_debug(
            "begin",
            source=source_index,
            rows=num_rows,
            list_origin_y=list_origin_y,
            channel=source_row.channel.get("name"),
        )

    def sync_list_origin(self, list_origin_y: int, *, num_rows: int) -> None:
        """Re-anchor overlays after scroll without changing the target index."""
        if not self._active:
            return
        self._list_origin_y = list_origin_y
        if self._last_y_root is not None:
            self._position_ghost(self._last_y_root, num_rows)
        self._refresh_gap(num_rows)

    def track_pointer(
        self,
        y_root: int,
        *,
        num_rows: int,
        allow_target_change: bool = True,
    ) -> bool:
        """Follow pointer with ghost; optionally move the drop gap."""
        if not self._active:
            return False
        self._last_y_root = y_root
        self._position_ghost(y_root, num_rows)
        if not allow_target_change:
            return False
        target = self.target_for_pointer(y_root, num_rows=num_rows)
        if target == self._target_index:
            return False
        self._target_index = target
        self._on_debug(
            "motion",
            y_root=y_root,
            content_y=self.pointer_content_y(y_root),
            target=target,
            source=self._source_index,
        )
        self._refresh_gap(num_rows)
        return True

    def insert_at(self, *, num_rows: int) -> int | None:
        return apply_list_move(self._source_index, self._target_index, num_rows)

    def finish(self, *, commit: bool, num_rows: int) -> int | None:
        insert_at = self.insert_at(num_rows=num_rows) if commit else None
        self._on_debug(
            "end",
            commit=commit,
            source=self._source_index,
            target=self._target_index,
            insert_at=insert_at,
            channel=(
                self._source_row.channel.get("name") if self._source_row else None
            ),
        )
        self._teardown()
        return insert_at

    def cancel(self) -> None:
        self.finish(commit=False, num_rows=0)

    def _teardown(self) -> None:
        self._hide_gap()
        self._hide_ghost()
        if self._source_row is not None:
            self._source_row.set_reorder_highlight(False)
            self._set_source_dim(False)
        self._active = False
        self._source_row = None
        self._last_y_root = None

    def _position_ghost_at_source(self, num_rows: int) -> None:
        y = gap_place_y(list_origin_y=self._list_origin_y, target_index=self._source_index)
        self._show_ghost_at(y)

    def _position_ghost(self, y_root: int, num_rows: int) -> None:
        y = ghost_place_y(
            list_origin_y=self._list_origin_y,
            pointer_content_y=self.pointer_content_y(y_root),
            num_rows=num_rows,
            slot_height=self._slot_height,
            body_height=self._body_height,
        )
        self._show_ghost_at(y)

    def _refresh_gap(self, num_rows: int) -> None:
        if apply_list_move(self._source_index, self._target_index, num_rows) is None:
            self._hide_gap()
            return
        self._show_gap_at(self._target_index)

    def _show_gap_at(self, target_index: int) -> None:
        self._ensure_gap()
        assert self._gap is not None
        y = gap_place_y(list_origin_y=self._list_origin_y, target_index=target_index)
        self._gap.place(x=0, y=y, relwidth=1)
        self._gap.lift()

    def _show_ghost_at(self, y: int) -> None:
        self._ensure_ghost_from_source()
        assert self._ghost is not None
        self._ghost.place(x=0, y=y, relwidth=1)
        self._ghost.lift()

    def _hide_gap(self) -> None:
        if self._gap is not None:
            self._gap.place_forget()

    def _hide_ghost(self) -> None:
        if self._ghost is not None:
            self._ghost.place_forget()

    def _ensure_gap(self) -> None:
        if self._gap is not None:
            return
        self._gap = ctk.CTkFrame(
            self._inner,
            height=self._slot_height,
            fg_color=_GAP_FG,
            border_width=2,
            border_color=_GAP_BORDER,
            corner_radius=10,
        )
        self._gap.pack_propagate(False)

    def _ensure_ghost(self, source_row: ChannelRow) -> None:
        if self._ghost is not None:
            self._update_ghost_label(source_row)
            return
        self._ghost = ctk.CTkFrame(
            self._inner,
            height=self._body_height,
            fg_color=_GHOST_FG,
            border_width=2,
            border_color=_GHOST_BORDER,
            corner_radius=10,
        )
        self._ghost.pack_propagate(False)
        self._ghost_label = ctk.CTkLabel(
            self._ghost,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._ghost_label.pack(side="left", padx=12, pady=8)
        self._update_ghost_label(source_row)

    def _ensure_ghost_from_source(self) -> None:
        if self._ghost is None and self._source_row is not None:
            self._ensure_ghost(self._source_row)

    def _update_ghost_label(self, source_row: ChannelRow) -> None:
        if self._ghost_label is None:
            return
        channel = source_row.channel
        name = (channel.get("display_name") or channel.get("name") or "").strip()
        platform = (channel.get("platform") or "").upper()
        self._ghost_label.configure(text=f"{platform}  {name}" if platform else name)

    def _set_source_dim(self, active: bool) -> None:
        row = self._source_row
        if row is None:
            return
        if active:
            row._reorder_saved_fg = row.cget("fg_color")  # type: ignore[attr-defined]
            row.configure(fg_color=_REORDER_SOURCE_DIM)
        elif hasattr(row, "_reorder_saved_fg"):
            row.configure(fg_color=row._reorder_saved_fg)  # type: ignore[attr-defined]
