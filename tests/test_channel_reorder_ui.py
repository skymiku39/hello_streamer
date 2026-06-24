"""Tests for Trello-style channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder import (
    ROW_SLOT_HEIGHT,
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
)
from stream_monitor.channel_reorder_ui import (
    ChannelReorderMode,
    canvas_content_y,
    canvas_content_y_for_widget,
)


class _FakeCanvas:
    def __init__(self, *, root_y: int = 100, scroll_offset: int = 0) -> None:
        self._root_y = root_y
        self._scroll_offset = scroll_offset

    def winfo_rooty(self) -> int:
        return self._root_y

    def canvasy(self, viewport_y: float) -> float:
        return viewport_y + self._scroll_offset


class _FakeRow:
    def __init__(self, root_y: int) -> None:
        self._root_y = root_y

    def winfo_rooty(self) -> int:
        return self._root_y


class _FakeSourceRow:
    channel = {"name": "test"}

    def set_reorder_highlight(self, _active: bool) -> None:
        pass


def test_canvas_content_y_accounts_for_scroll_offset() -> None:
    canvas = _FakeCanvas(root_y=100, scroll_offset=900)
    row = _FakeRow(root_y=103)
    assert canvas_content_y_for_widget(canvas, row) == 903
    assert canvas_content_y(canvas, 103 + 12 * ROW_SLOT_HEIGHT) == 903 + 12 * ROW_SLOT_HEIGHT


def test_reorder_mode_pointer_uses_same_coordinate_space_when_scrolled() -> None:
    canvas = _FakeCanvas(root_y=100, scroll_offset=900)
    row = _FakeRow(103)
    list_origin = canvas_content_y_for_widget(canvas, row)
    mode = ChannelReorderMode(canvas, schedule_preview_repack=lambda _o: None)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        list_origin_y=list_origin,
        num_rows=4,
    )
    pointer_y = 103 + 2 * ROW_SLOT_HEIGHT + (ROW_SLOT_HEIGHT // 2 - 1)
    assert mode.target_for_pointer(pointer_y, num_rows=4) == 3

    # ``winfo_y()``-style origin would ignore scroll offset and map too low.
    wrong_origin_mode = ChannelReorderMode(canvas, schedule_preview_repack=lambda _o: None)
    wrong_origin_mode._active = True
    wrong_origin_mode._source_index = 1
    wrong_origin_mode._list_origin_y = 3
    assert wrong_origin_mode.target_for_pointer(pointer_y, num_rows=4) != 3


def test_reorder_mode_nudge_target_moves_insert_gap() -> None:
    repacked: list[list[int]] = []

    def schedule(order: list[int]) -> None:
        repacked.append(list(order))

    mode = ChannelReorderMode(_FakeCanvas(), schedule_preview_repack=schedule)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        list_origin_y=0,
        num_rows=4,
    )
    assert mode.nudge_target(2, num_rows=4) is True
    assert mode.target_index == 3
    assert repacked[-1] == preview_row_indices(1, 3, 4)
    assert mode.nudge_target(-2, num_rows=4) is True
    assert mode.target_index == 1
    assert mode.nudge_target(0, num_rows=4) is False


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