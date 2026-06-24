"""Tests for Trello-style channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder import (
    DRAG_ENGAGE_PX,
    ROW_SLOT_HEIGHT,
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
)
from stream_monitor.channel_reorder_ui import ChannelReorderMode


class _FakeGeomRow:
    def __init__(self, top: int, height: int = ROW_SLOT_HEIGHT) -> None:
        self._top = top
        self._height = height

    def winfo_rooty(self) -> int:
        return self._top

    def winfo_height(self) -> int:
        return self._height


class _FakeSourceRow:
    channel = {"name": "test"}

    def set_reorder_highlight(self, _active: bool) -> None:
        pass


def _four_rows() -> list[_FakeGeomRow]:
    return [_FakeGeomRow(100 + index * ROW_SLOT_HEIGHT) for index in range(4)]


def test_reorder_mode_uses_on_screen_row_geometry() -> None:
    rows = _four_rows()
    mode = ChannelReorderMode(object(), schedule_preview_repack=lambda _o: None)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    mode._engaged = True
    assert mode.target_for_pointer(260, rows) == 3


def test_reorder_mode_click_without_move_stays_noop() -> None:
    rows = _four_rows()
    mode = ChannelReorderMode(object(), schedule_preview_repack=lambda _o: None)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    assert mode.track_pointer(164, rows=rows, num_rows=4) is False
    assert mode.engaged is False
    assert mode.target_index == 1
    assert mode.finish(commit=True, num_rows=4) is None


def test_reorder_mode_requires_engage_threshold_before_target_changes() -> None:
    rows = _four_rows()
    mode = ChannelReorderMode(
        object(), schedule_preview_repack=lambda _o: None, engage_px=12
    )
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    assert mode.track_pointer(164 + DRAG_ENGAGE_PX - 1, rows=rows, num_rows=4) is False
    assert mode.track_pointer(164 + DRAG_ENGAGE_PX, rows=rows, num_rows=4) is True
    assert mode.engaged is True


def test_reorder_mode_nudge_target_moves_insert_gap() -> None:
    repacked: list[list[int]] = []

    def schedule(order: list[int]) -> None:
        repacked.append(list(order))

    mode = ChannelReorderMode(object(), schedule_preview_repack=schedule)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    assert mode.nudge_target(2, num_rows=4) is True
    assert mode.target_index == 3
    assert mode.engaged is True
    assert repacked[-1] == preview_row_indices(1, 3, 4)
    assert mode.nudge_target(-2, num_rows=4) is True
    assert mode.target_index == 1


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
