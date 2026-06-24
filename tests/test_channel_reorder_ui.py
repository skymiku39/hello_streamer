"""Tests for Trello-style channel reorder mode."""

from __future__ import annotations

from stream_monitor.channel_reorder import (
    DRAG_ENGAGE_PX,
    GEOMETRY_LOCK_PX,
    ROW_SLOT_HEIGHT,
    pack_anchor_for_moved_row,
    preview_order_delta,
    preview_row_indices,
    preview_visual_step,
    target_index_for_drag_source_content,
)
from stream_monitor.channel_reorder_ui import ChannelReorderMode


class _FakeCanvas:
    def __init__(self, *, root_y: int = 100, scroll_offset: int = 0) -> None:
        self._root_y = root_y
        self._scroll_offset = scroll_offset

    def winfo_rooty(self) -> int:
        return self._root_y

    def canvasy(self, viewport_y: float) -> float:
        return viewport_y + self._scroll_offset


class _FakeGeomRow:
    def __init__(self, top: int, height: int = ROW_SLOT_HEIGHT) -> None:
        self._top = top
        self._height = height

    def winfo_rooty(self) -> int:
        return self._top

    def winfo_height(self) -> int:
        return self._height


class _ScrollingRow:
  """Row whose screen Y shifts with scroll but content Y stays fixed."""

  def __init__(
      self, content_top: int, canvas: _FakeCanvas, height: int = ROW_SLOT_HEIGHT
  ) -> None:
      self._content_top = content_top
      self._canvas = canvas
      self._height = height

  def winfo_rooty(self) -> int:
      return int(self._canvas.winfo_rooty() + self._content_top - self._canvas._scroll_offset)

  def winfo_height(self) -> int:
      return self._height


class _FakeSourceRow:
    channel = {"name": "test"}

    def set_reorder_highlight(self, _active: bool) -> None:
        pass


def _four_rows() -> list[_FakeGeomRow]:
    return [_FakeGeomRow(100 + index * ROW_SLOT_HEIGHT) for index in range(4)]


def test_reorder_mode_maps_pointer_in_content_space() -> None:
    rows = _four_rows()
    canvas = _FakeCanvas(scroll_offset=900)
    mode = ChannelReorderMode(canvas, schedule_preview_repack=lambda _o: None)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    mode._engaged = True
    assert mode.target_for_pointer(260, rows) == 3


def test_content_pointer_stable_when_viewport_scrolls() -> None:
    canvas_low = _FakeCanvas(scroll_offset=100)
    canvas_high = _FakeCanvas(scroll_offset=250)
    rows_low = [
        _ScrollingRow(100 + index * ROW_SLOT_HEIGHT, canvas_low) for index in range(4)
    ]
    rows_high = [
        _ScrollingRow(100 + index * ROW_SLOT_HEIGHT, canvas_high) for index in range(4)
    ]
    pointer_low = rows_low[2].winfo_rooty() + ROW_SLOT_HEIGHT // 2
    pointer_high = rows_high[2].winfo_rooty() + ROW_SLOT_HEIGHT // 2
    tops = [100 + index * ROW_SLOT_HEIGHT for index in range(4)]
    heights = [ROW_SLOT_HEIGHT] * 4
    content_y = tops[2] + ROW_SLOT_HEIGHT // 2
    assert target_index_for_drag_source_content(
        content_y, source_index=1, row_content_tops=tops, row_heights=heights
    ) == 3
    mode_low = ChannelReorderMode(canvas_low, schedule_preview_repack=lambda _o: None)
    mode_high = ChannelReorderMode(canvas_high, schedule_preview_repack=lambda _o: None)
    mode_low._source_index = 1
    mode_high._source_index = 1
    assert mode_low.target_for_pointer(pointer_low, rows_low) == 3
    assert mode_high.target_for_pointer(pointer_high, rows_high) == 3


def test_geometry_lock_ignores_shifted_row_positions_until_pointer_moves() -> None:
    canvas = _FakeCanvas()
    mode = ChannelReorderMode(canvas, schedule_preview_repack=lambda _o: None)
    mode._source_index = 1
    locked_tops = [0, 64, 128, 192]
    mode._lock_geometry(100, locked_tops, [ROW_SLOT_HEIGHT] * 4)
    shifted_rows = [
        _FakeGeomRow(100),
        _FakeGeomRow(228),
        _FakeGeomRow(164),
        _FakeGeomRow(292),
    ]
    pointer_y = 200
    assert mode.target_for_pointer(pointer_y, shifted_rows) == 2
    unlocked = ChannelReorderMode(canvas, schedule_preview_repack=lambda _o: None)
    unlocked._source_index = 1
    assert unlocked.target_for_pointer(pointer_y, shifted_rows) == 3
    moved_y = 100 + GEOMETRY_LOCK_PX + 5 + canvas.winfo_rooty()
    assert mode.target_for_pointer(int(moved_y), shifted_rows) == 3


def test_reorder_mode_click_without_move_stays_noop() -> None:
    rows = _four_rows()
    mode = ChannelReorderMode(_FakeCanvas(), schedule_preview_repack=lambda _o: None)
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


def test_reorder_mode_drag_back_restores_identity_preview() -> None:
    orders: list[list[int]] = []

    def schedule(order: list[int]) -> None:
        orders.append(list(order))

    rows = _four_rows()
    mode = ChannelReorderMode(_FakeCanvas(), schedule_preview_repack=schedule)
    mode.begin(
        source_row=_FakeSourceRow(),  # type: ignore[arg-type]
        source_index=1,
        num_rows=4,
        y_root=164,
    )
    mode._engaged = True
    assert mode.track_pointer(260, rows=rows, num_rows=4) is True
    assert orders[-1] == preview_row_indices(1, 3, 4)
    assert mode.track_pointer(164, rows=rows, num_rows=4) is True
    assert orders[-1] == [0, 1, 2, 3]
    assert mode.finish(commit=True, num_rows=4) is None


def test_reorder_mode_requires_engage_threshold_before_target_changes() -> None:
    rows = _four_rows()
    mode = ChannelReorderMode(
        _FakeCanvas(), schedule_preview_repack=lambda _o: None, engage_px=12
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

    mode = ChannelReorderMode(_FakeCanvas(), schedule_preview_repack=schedule)
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
    assert repacked[-1] == [0, 1, 2, 3]


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
