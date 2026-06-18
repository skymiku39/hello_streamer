"""Channel list drag-and-drop reorder helpers (pure logic + slot geometry)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# ChannelRow height (58) + pack pady (3 + 3).
ROW_BODY_HEIGHT = 58
ROW_SLOT_HEIGHT = 64
LONG_PRESS_MS = 400


def apply_list_move(from_index: int, to_index: int, length: int) -> int | None:
    """Return the insert index after removal, or None if the move is a no-op.

    ``to_index`` is the insertion point in the *original* list (0..length),
    i.e. the gap *before* the item currently at ``to_index``.
    """
    if length <= 0:
        return None
    from_index = max(0, min(from_index, length - 1))
    to_index = max(0, min(to_index, length))
    if from_index == to_index or from_index + 1 == to_index:
        return None
    insert_at = to_index - 1 if to_index > from_index else to_index
    return insert_at


def insert_index_for_pointer(
    pointer_y_root: int,
    row_tops: list[int],
    row_heights: list[int],
) -> int:
    """Pick the gap index (0..len(rows)) closest to the pointer."""
    if not row_tops:
        return 0
    for index, (top, height) in enumerate(zip(row_tops, row_heights, strict=True)):
        midpoint = top + height // 2
        if pointer_y_root < midpoint:
            return index
    return len(row_tops)


def nudge_insert_index(current: int, delta: int, *, length: int) -> int:
    """Move the insertion gap by ``delta`` steps (mouse wheel)."""
    upper = length
    return max(0, min(current + delta, upper))


class RowGeometry(Protocol):
  def winfo_rooty(self) -> int: ...
  def winfo_height(self) -> int: ...


def insert_index_for_rows(pointer_y_root: int, rows: list[RowGeometry]) -> int:
    tops = [row.winfo_rooty() for row in rows]
    heights = [row.winfo_height() for row in rows]
    return insert_index_for_pointer(pointer_y_root, tops, heights)


def target_index_from_reduced_gap(reduced_index: int, *, source_index: int) -> int:
    """Map a gap index in the list *without* the dragged row to a full-list index."""
    if reduced_index < source_index:
        return reduced_index
    return reduced_index + 1


def visual_gap_for_pointer(
    pointer_y_root: int,
    slot_tops: list[int],
    *,
    slot_height: int = ROW_SLOT_HEIGHT,
) -> int:
    """Gap index (0..len(slot_tops)) using fixed-height slots in pack order."""
    if not slot_tops:
        return 0
    for index, top in enumerate(slot_tops):
        midpoint = top + slot_height // 2
        if pointer_y_root < midpoint:
            return index
    return len(slot_tops)


def target_index_for_drag_preview(
    pointer_y_root: int,
    *,
    source_index: int,
    slot_tops: list[int],
    slot_height: int = ROW_SLOT_HEIGHT,
) -> int:
    """Map pointer Y to a full-list insert index during an in-progress drag."""
    visual_gap = visual_gap_for_pointer(
        pointer_y_root, slot_tops, slot_height=slot_height
    )
    return target_index_from_reduced_gap(visual_gap, source_index=source_index)


def target_index_for_content_y(
    content_y: float,
    *,
    source_index: int,
    num_rows: int,
    slot_height: int = ROW_SLOT_HEIGHT,
) -> int:
    """Map scroll-canvas Y to a full-list insert index (fixed ``ROW_SLOT_HEIGHT`` grid)."""
    if num_rows <= 0:
        return 0
    slot_tops = [index * slot_height for index in range(num_rows)]
    return target_index_for_drag_preview(
        int(content_y),
        source_index=source_index,
        slot_tops=slot_tops,
        slot_height=slot_height,
    )


def target_index_for_drag_source(
    pointer_y_root: int,
    *,
    source_index: int,
    rows: list[RowGeometry],
) -> int:
    """Insertion index in the full list for a drag originating at ``source_index``."""
    reduced_rows = [row for index, row in enumerate(rows) if index != source_index]
    reduced_index = insert_index_for_rows(pointer_y_root, reduced_rows)
    return target_index_from_reduced_gap(reduced_index, source_index=source_index)


def reorder_list(items: list[Any], from_index: int, to_index: int) -> list[Any] | None:
    """Return a new list with one item moved, or ``None`` when the move is a no-op."""
    insert_at = apply_list_move(from_index, to_index, len(items))
    if insert_at is None:
        return None
    result = list(items)
    item = result.pop(from_index)
    result.insert(insert_at, item)
    return result


def preview_row_indices(
    source_index: int, target_index: int, num_rows: int
) -> list[int]:
    """Original row indices in visual pack order (Trello-style push-aside preview)."""
    indices = list(range(num_rows))
    insert_at = apply_list_move(source_index, target_index, num_rows)
    if insert_at is None:
        return indices
    dragged = indices.pop(source_index)
    indices.insert(insert_at, dragged)
    return indices


@dataclass
class ChannelDragPreview:
    source_index: int
    target_index: int
