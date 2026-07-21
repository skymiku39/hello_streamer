"""Trello-style channel list drag-reorder mode.

Confirmed rendering model (CTk / Tk ``pack`` constraints):

1. **Row geometry** — pointer maps to gaps using each row's scroll-canvas
   content Y (stable while the viewport scrolls).
2. **Engage threshold** — long-press only highlights until the pointer moves
   ``DRAG_ENGAGE_PX`` vertically or the wheel nudges the target.
3. **Push-aside preview** — when the target slot changes, widgets are shown in
   ``preview_row_indices`` order so neighbours make room like Trello.
4. **Repack policy** — adjacent one-slot moves use a single ``pack`` insert;
   larger jumps use a full preview repack. During drag, repacks use ``after(0)``
   with ``update_idletasks`` for responsive preview.
5. **Coalescing** — rapid target changes reschedule a single pending repack.

Row repaints from the monitor stay deferred for the session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol

from stream_monitor.channel_reorder import (
    DRAG_ENGAGE_PX,
    GEOMETRY_LOCK_PX,
    ROW_SLOT_HEIGHT,
    RowGeometry,
    apply_list_move,
    nudge_insert_index,
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
    target_index_for_drag_source_content,
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
    """Discrete slot drag with live list reflow (one card height per step).

    This owns the **app-level reorder session** state machine:

        idle → active (a source row began dragging)
             → engaged (pointer moved ``engage_px`` → push-aside preview shows)
             → committed / cancelled (finish)

    It is deliberately separate from the **row-local input gesture** in
    ``ChannelRow`` (long-press → arm → drag-source), which only feeds this via
    begin/motion/release callbacks. Keeping the two layers apart means the row
    widget never depends on this session object.
    """

    def __init__(
        self,
        canvas: object,
        *,
        schedule_preview_repack: SchedulePreviewRepack,
        row_slot_height: int = ROW_SLOT_HEIGHT,
        engage_px: int = DRAG_ENGAGE_PX,
        geometry_lock_px: int = GEOMETRY_LOCK_PX,
        on_debug: Callable[..., None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._schedule_preview_repack = schedule_preview_repack
        self._slot_height = row_slot_height
        self._engage_px = engage_px
        self._geometry_lock_px = geometry_lock_px
        self._on_debug = on_debug or (lambda *_a, **_k: None)
        self._active = False
        self._engaged = False
        self._press_y_root = 0
        self._source_index = 0
        self._target_index = 0
        self._source_row: ChannelRow | None = None
        self._geometry_lock_y: float | None = None
        self._locked_tops: list[int] | None = None
        self._locked_heights: list[int] | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def source_index(self) -> int:
        return self._source_index

    @property
    def target_index(self) -> int:
        return self._target_index

    @property
    def source_row(self) -> ChannelRow | None:
        return self._source_row

    def _live_geometry(
        self, rows: list[RowGeometry]
    ) -> tuple[list[int], list[int]]:
        tops = [canvas_content_y_for_widget(self._canvas, row) for row in rows]
        heights = [row.winfo_height() for row in rows]
        return tops, heights

    def _geometry_for_hit_test(
        self,
        pointer_content_y: float,
        live_tops: list[int],
        live_heights: list[int],
    ) -> tuple[list[int], list[int]]:
        if (
            self._locked_tops is not None
            and self._locked_heights is not None
            and self._geometry_lock_y is not None
            and abs(pointer_content_y - self._geometry_lock_y) < self._geometry_lock_px
        ):
            return self._locked_tops, self._locked_heights
        return live_tops, live_heights

    def _lock_geometry(
        self,
        pointer_content_y: float,
        tops: list[int],
        heights: list[int],
    ) -> None:
        self._geometry_lock_y = pointer_content_y
        self._locked_tops = list(tops)
        self._locked_heights = list(heights)

    def _clear_geometry_lock(self) -> None:
        self._geometry_lock_y = None
        self._locked_tops = None
        self._locked_heights = None

    def target_for_pointer(self, y_root: int, rows: list[RowGeometry]) -> int:
        pointer_content_y = canvas_content_y(self._canvas, y_root)
        live_tops, live_heights = self._live_geometry(rows)
        tops, heights = self._geometry_for_hit_test(
            pointer_content_y, live_tops, live_heights
        )
        return target_index_for_drag_source_content(
            pointer_content_y,
            source_index=self._source_index,
            row_content_tops=tops,
            row_heights=heights,
        )

    def begin(
        self,
        source_row: ChannelRow,
        *,
        source_index: int,
        num_rows: int,
        y_root: int,
    ) -> None:
        self._active = True
        self._engaged = False
        self._press_y_root = y_root
        self._source_row = source_row
        self._source_index = source_index
        self._target_index = source_index
        self._clear_geometry_lock()
        source_row.set_reorder_highlight(True)
        self._on_debug(
            "begin",
            source=source_index,
            rows=num_rows,
            y_root=y_root,
            channel=source_row.channel.get("name"),
        )

    def nudge_target(self, delta: int, *, num_rows: int) -> bool:
        if not self._active or delta == 0:
            return False
        self._engaged = True
        self._clear_geometry_lock()
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
        rows: list[RowGeometry],
        num_rows: int,
    ) -> bool:
        if not self._active:
            return False
        if not self._engaged:
            if abs(y_root - self._press_y_root) < self._engage_px:
                return False
            self._engaged = True
        pointer_content_y = canvas_content_y(self._canvas, y_root)
        live_tops, live_heights = self._live_geometry(rows)
        tops, heights = self._geometry_for_hit_test(
            pointer_content_y, live_tops, live_heights
        )
        target = target_index_for_drag_source_content(
            pointer_content_y,
            source_index=self._source_index,
            row_content_tops=tops,
            row_heights=heights,
        )
        if target == self._target_index:
            return False
        self._target_index = target
        self._lock_geometry(pointer_content_y, live_tops, live_heights)
        self._on_debug(
            "motion",
            y_root=y_root,
            target=target,
            source=self._source_index,
            engaged=True,
            preview=preview_row_indices(self._source_index, target, num_rows),
        )
        self._apply_preview(num_rows)
        return True

    def insert_at(self, *, num_rows: int) -> int | None:
        return apply_list_move(self._source_index, self._target_index, num_rows)

    def finish(self, *, commit: bool, num_rows: int) -> int | None:
        if commit and not self._engaged:
            commit = False
        insert_at = self.insert_at(num_rows=num_rows) if commit else None
        self._on_debug(
            "end",
            commit=commit,
            engaged=self._engaged,
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
        if not self._engaged:
            return
        if apply_list_move(self._source_index, self._target_index, num_rows) is None:
            self._schedule_preview_repack(list(range(num_rows)))
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
        self._engaged = False
        self._source_row = None
        self._clear_geometry_lock()
