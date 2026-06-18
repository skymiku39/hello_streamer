"""Tests for overlay-based channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder_ui import gap_place_y


def test_gap_place_y_aligns_to_row_grid() -> None:
    assert gap_place_y(list_origin_y=6, target_index=0) == 6
    assert gap_place_y(list_origin_y=6, target_index=3) == 6 + 3 * 64
