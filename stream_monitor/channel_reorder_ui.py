"""Trello-style channel list drag-reorder mode.

Repacks the real channel rows in a preview order when the snapped target slot
changes so neighbouring cards are pushed aside. Repaint from the monitor is
deferred for the session; scroll position is preserved by the repack callback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from stream_monitor.channel_reorder import (
    ROW_SLOT_HEIGHT,
    apply_list_move,
    preview_row_indices,
    target_index_for_content_y,
)

if TYPE_CHECKING:
    from stream_monitor.channel_row import ChannelRow

RepackPreview = Callable[[list[int]], None]


class ChannelReorderMode:
    """Discrete slot drag with live list reflow (one card height per step)."""

    def __init__(
        self,
        canvas: object,
        *,
        repack_preview: RepackPreview,
        row_slot_height: int = ROW_SLOT_HEIGHT,
        on_debug: Callable[..., None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._repack_preview = repack_preview
        self._slot_height = row_slot_height
        self._on_debug = on_debug or (lambda *_a, **_k: None)
        self._active = False
        self._source_index = 0
        self._target_index = 0
        self._source_row: ChannelRow | None = None
        self._list_origin_y = 0

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
        canvas_y = y_root - self._canvas.winfo_rooty()  # type: ignore[attr-defined]
        return self._canvas.canvasy(canvas_y)  # type: ignore[attr-defined]

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
        self._apply_preview(num_rows)
        if y_root is not None:
            self.track_pointer(
                y_root, num_rows=num_rows, allow_target_change=True
            )
        self._on_debug(
            "begin",
            source=source_index,
            rows=num_rows,
            list_origin_y=list_origin_y,
            channel=source_row.channel.get("name"),
        )

    def update_list_origin(self, list_origin_y: int) -> None:
        if self._active:
            self._list_origin_y = list_origin_y

    def track_pointer(
        self,
        y_root: int,
        *,
        num_rows: int,
        allow_target_change: bool = True,
    ) -> bool:
        if not self._active:
            return False
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
            preview=preview_row_indices(self._source_index, target, num_rows),
        )
        self._apply_preview(num_rows)
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

    def _apply_preview(self, num_rows: int) -> None:
        order = preview_row_indices(self._source_index, self._target_index, num_rows)
        self._repack_preview(order)
        row = self._source_row
        if row is not None:
            row.set_reorder_highlight(True)
            row.lift()

    def _teardown(self) -> None:
        if self._source_row is not None:
            self._source_row.set_reorder_highlight(False)
        self._active = False
        self._source_row = None
