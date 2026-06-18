"""Tests for channel list drag-reorder helpers."""

from __future__ import annotations

import pytest

from stream_monitor.channel_reorder import (
    apply_list_move,
    insert_index_for_pointer,
    nudge_insert_index,
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
