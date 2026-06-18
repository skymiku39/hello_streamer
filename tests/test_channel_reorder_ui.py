"""Tests for Trello-style channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder import (
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
)


def test_preview_row_indices_pushes_cards_aside() -> None:
    assert preview_row_indices(1, 3, 4) == [0, 2, 1, 3]
    assert preview_row_indices(0, 2, 4) == [1, 0, 2, 3]


def test_pack_anchor_for_moved_row() -> None:
    order = preview_row_indices(1, 3, 4)
    assert pack_anchor_for_moved_row(order, 1) == ("before", 3)
    order_top = preview_row_indices(2, 0, 4)
    assert pack_anchor_for_moved_row(order_top, 2) == ("before", 0)
    order_bottom = preview_row_indices(0, 4, 4)
    assert pack_anchor_for_moved_row(order_bottom, 0) == ("after", 3)


def test_preview_visual_step_detects_adjacent_move() -> None:
    prev = preview_row_indices(1, 1, 4)
    nxt = preview_row_indices(1, 3, 4)
    assert preview_visual_step(prev, nxt, 1) == 1
    jump = preview_row_indices(0, 4, 4)
    assert preview_visual_step(prev, jump, 0) > 1


def test_preview_order_delta_counts_moved_slice() -> None:
    prev = list(range(4))
    nxt = preview_row_indices(1, 3, 4)
    assert preview_order_delta(prev, nxt) == {1, 2}
    assert preview_order_delta(prev, prev) == set()
    jump = preview_row_indices(0, 4, 4)
    assert len(preview_order_delta(prev, jump)) == 4