"""Trello-style channel list drag-reorder mode.

Confirmed rendering model (CTk / Tk ``pack`` constraints):

1. **Discrete slots** — pointer maps to a target index on a 64px grid; no
   continuous floating card.
2. **Push-aside preview** — when the target slot changes, widgets are shown in
   ``preview_row_indices`` order so neighbours make room like Trello.
3. **Repack policy** — adjacent one-slot moves use a single ``pack`` insert;
   larger jumps use a full preview repack. **Never** call ``update_idletasks``
   during the drag session (flush only on commit/cancel).
4. **Coalescing** — the UI schedules at most one repack per idle frame via
   ``after_idle`` to avoid motion-event storms.

Row repaints from the monitor stay deferred for the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol

from stream_monitor.channel_reorder import (
    ROW_SLOT_HEIGHT,
    apply_list_move,
    nudge_insert_index,
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
    target_index_for_content_y,
)

if TYPE_CHECKING:
    from stream_monitor.channel_row import ChannelRow

SchedulePreviewRepack = Callable[[list[int]], None]

ROW_PACK_KWARGS: dict[str, Any] = {"fill": "x", "pady": 3}


def canvas_content_y(canvas: object, y_root: int) -> float:
    """Map a screen Y coordinate to scroll-canvas content Y."""
    viewport_y = y_root - canvas.winfo_rooty()  # type: ignore[attr-defined]
    return canvas.canvasy(viewport_y)  # type: ignore[attr-defined]


def canvas_content_y_for_widget(canvas: object, widget: object) -> int:
    """Content Y of a widget embedded in a scroll canvas."""
    viewport_y = widget.winfo_rooty() - canvas.winfo_rooty()  # type: ignore[attr-defined]
    return int(canvas.canvasy(viewport_y))  # type: ignore[attr-defined]


class PackableRow(Protocol):
    def pack_forget(self) -> None: ...
    def pack(self, **kwargs: Any) -> None: ...
    @property
    def master(self) -> Any: ...


def visual_pack_order(rows: list[PackableRow]) -> list[int]:
    """Return row indices in current ``pack`` order (uses ``pack_slaves``, not ``winfo_y``)."""
    if not rows:
        return []
    parent = rows[0].master
    index_by_id = {id(row): index for index, row in enumerate(rows)}
    return [
        index_by_id[id(widget)]
        for widget in parent.pack_slaves()
        if id(widget) in index_by_id
    ]


def full_repack_rows(rows: list[PackableRow], order: list[int]) -> None:
    for row in rows:
        row.pack_forget()
    for index in order:
        rows[index].pack(**ROW_PACK_KWARGS)


def partial_repack_rows(
    rows: list[PackableRow], order: list[int], previous: list[int]
) -> bool:
    changed = preview_order_delta(previous, order)
    if not changed:
        return True
    if len(changed) >= len(order):
        return False
    for idx in changed:
        rows[idx].pack_forget()
    forgotten = set(changed)
    prev_row: PackableRow | None = None
    for idx in order:
        if idx not in forgotten:
            prev_row = rows[idx]
            continue
        row = rows[idx]
        if prev_row is None:
            anchor: PackableRow | None = None
            start = order.index(idx) + 1
            for later in order[start:]:
                if later not in forgotten:
                    anchor = rows[later]
                    break
            if anchor is not None:
                row.pack(**ROW_PACK_KWARGS, before=anchor)
            else:
                row.pack(**ROW_PACK_KWARGS)
        else:
            row.pack(**ROW_PACK_KWARGS, after=prev_row)
        prev_row = row
        forgotten.discard(idx)
    return True


def incremental_move_row(
    rows: list[PackableRow], order: list[int], moved_index: int
) -> bool:
    anchor = pack_anchor_for_moved_row(order, moved_index)
    if anchor is None:
        return False
    side, anchor_index = anchor
    row = rows[moved_index]
    anchor_row = rows[anchor_index]
    row.pack_forget()
    if side == "before":
        row.pack(**ROW_PACK_KWARGS, before=anchor_row)
    else:
        row.pack(**ROW_PACK_KWARGS, after=anchor_row)
    return True


def repack_preview_rows(
    rows: list[PackableRow],
    order: list[int],
    previous: list[int] | None,
    source_index: int | None,
) -> str:
    """Apply preview repack; return ``skip``|``incremental``|``partial``|``full``."""
    if order == previous:
        return "skip"
    if previous is not None and source_index is not None:
        step = preview_visual_step(previous, order, source_index)
        if step == 1 and incremental_move_row(rows, order, source_index):
            return "incremental"
        if partial_repack_rows(rows, order, previous):
            return "partial"
        full_repack_rows(rows, order)
        return "full"
    if previous is not None and partial_repack_rows(rows, order, previous):
        return "partial"
    full_repack_rows(rows, order)
    return "full"


class ChannelReorderMode:
    """Discrete slot drag with live list reflow (one card height per step)."""

    def __init__(
        self,
        canvas: object,
        *,
        schedule_preview_repack: SchedulePreviewRepack,
        row_slot_height: int = ROW_SLOT_HEIGHT,
        on_debug: Callable[..., None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._schedule_preview_repack = schedule_preview_repack
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
        return canvas_content_y(self._canvas, y_root)

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

    def nudge_target(self, delta: int, *, num_rows: int) -> bool:
        if not self._active or delta == 0:
            return False
        target = nudge_insert_index(self._target_index, delta, length=num_rows)
        if target == self._target_index:
            return False
        self._target_index = target
        self._on_debug(
            "wheel",
            delta=delta,
            target=target,
            source=self._source_index,
            preview=preview_row_indices(self._source_index, target, num_rows),
        )
        self._apply_preview(num_rows)
        return True

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
        if apply_list_move(self._source_index, self._target_index, num_rows) is None:
            return
        order = preview_row_indices(self._source_index, self._target_index, num_rows)
        self._schedule_preview_repack(order)
        row = self._source_row
        if row is not None:
            row.set_reorder_highlight(True)

    def _teardown(self) -> None:
        if self._source_row is not None:
            self._source_row.set_reorder_highlight(False)
        self._active = False
        self._source_row = None
