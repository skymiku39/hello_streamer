"""Channel list drag-and-drop reorder helpers (pure logic + slot geometry)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# ChannelRow height (58) + pack pady (3 + 3).
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


@dataclass
class ChannelDragPreview:
    source_index: int
    target_index: int
