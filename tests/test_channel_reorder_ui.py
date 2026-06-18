"""Tests for overlay-based channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder_ui import gap_place_y, ghost_place_y


def test_gap_place_y_aligns_to_row_grid() -> None:
    assert gap_place_y(list_origin_y=6, target_index=0) == 6
    assert gap_place_y(list_origin_y=6, target_index=3) == 6 + 3 * 64


def test_ghost_place_y_follows_pointer_within_bounds() -> None:
    origin = 6
    # Pointer near top of first row
    y = ghost_place_y(
        list_origin_y=origin, pointer_content_y=origin + 20, num_rows=4
    )
    assert y == origin
    # Pointer lower in list
    y2 = ghost_place_y(
        list_origin_y=origin, pointer_content_y=origin + 200, num_rows=4
    )
    assert y2 > y
