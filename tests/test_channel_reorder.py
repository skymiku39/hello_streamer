"""Tests for channel list drag-reorder helpers."""

from __future__ import annotations

import pytest

from stream_monitor.channel_reorder import (
    apply_list_move,
    insert_index_for_pointer,
    nudge_insert_index,
    preview_row_indices,
    target_index_for_content_y,
    target_index_for_drag_preview,
    visual_gap_for_pointer,
)


@pytest.mark.parametrize(
    ("from_index", "to_index", "length", "expected"),
    [
        (0, 0, 3, None),
        (0, 1, 3, None),
        (1, 2, 3, None),
        (0, 2, 3, 1),
        (2, 0, 3, 0),
        (1, 3, 4, 2),
    ],
)
def test_apply_list_move(
    from_index: int, to_index: int, length: int, expected: int | None
) -> None:
    assert apply_list_move(from_index, to_index, length) == expected


def test_insert_index_for_pointer_empty() -> None:
    assert insert_index_for_pointer(100, [], []) == 0


def test_insert_index_for_pointer_midpoints() -> None:
    tops = [100, 164, 228]
    heights = [58, 58, 58]
    assert insert_index_for_pointer(120, tops, heights) == 0
    assert insert_index_for_pointer(180, tops, heights) == 1
    assert insert_index_for_pointer(250, tops, heights) == 2
    assert insert_index_for_pointer(400, tops, heights) == 3


def test_nudge_insert_index_clamps() -> None:
    assert nudge_insert_index(0, -1, length=3) == 0
    assert nudge_insert_index(2, 1, length=3) == 3
    assert nudge_insert_index(1, -1, length=3) == 0


def test_visual_gap_for_pointer_fixed_slots() -> None:
    tops = [100, 164, 228]
    assert visual_gap_for_pointer(120, tops) == 0
    assert visual_gap_for_pointer(132, tops) == 1
    assert visual_gap_for_pointer(196, tops) == 2
    assert visual_gap_for_pointer(300, tops) == 3


def test_target_index_for_content_y_snaps_per_slot() -> None:
    assert target_index_for_content_y(16, source_index=1, num_rows=4) == 0
    assert target_index_for_content_y(144, source_index=1, num_rows=4) == 3


def test_target_index_for_drag_preview() -> None:
    tops = [100, 164, 228]
    assert target_index_for_drag_preview(196, source_index=1, slot_tops=tops) == 3
    assert target_index_for_drag_preview(120, source_index=1, slot_tops=tops) == 0


def test_preview_row_indices_matches_reorder_list() -> None:
    order = preview_row_indices(1, 3, 4)
    names = ["a", "b", "c", "d"]
    preview = [names[i] for i in order]
    assert preview == ["a", "c", "b", "d"]
