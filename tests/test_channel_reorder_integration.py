"""Integration tests for channel drag-reorder (logic + row drag state machine)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from stream_monitor.channel_reorder import (
    apply_list_move,
    nudge_insert_index,
    reorder_list,
    target_index_for_drag_preview,
    target_index_for_drag_source,
    target_index_from_reduced_gap,
    visual_gap_for_pointer,
)


@dataclass
class _FakeRow:
    top: int
    height: int = 64

    def winfo_rooty(self) -> int:
        return self.top

    def winfo_height(self) -> int:
        return self.height


def test_target_index_from_reduced_gap() -> None:
    assert target_index_from_reduced_gap(0, source_index=2) == 0
    assert target_index_from_reduced_gap(2, source_index=2) == 3
    assert target_index_from_reduced_gap(1, source_index=3) == 1


def test_target_index_for_drag_source_moves_middle_item_down() -> None:
    rows = [_FakeRow(100), _FakeRow(164), _FakeRow(228), _FakeRow(292)]
    # Drag index 1 (B); pointer below row 2 midpoint → insert before index 3.
    target = target_index_for_drag_source(260, source_index=1, rows=rows)
    assert target == 3
    assert reorder_list(["A", "B", "C", "D"], 1, target) == ["A", "C", "B", "D"]


def test_target_index_for_drag_source_moves_middle_item_up() -> None:
    rows = [_FakeRow(100), _FakeRow(164), _FakeRow(228), _FakeRow(292)]
    target = target_index_for_drag_source(110, source_index=2, rows=rows)
    assert target == 0
    assert reorder_list(["A", "B", "C", "D"], 2, target) == ["C", "A", "B", "D"]


def test_target_index_adjacent_noop() -> None:
    rows = [_FakeRow(100), _FakeRow(164), _FakeRow(228)]
    target = target_index_for_drag_source(200, source_index=1, rows=rows)
    assert target == 2
    assert apply_list_move(1, 2, 3) is None
    assert reorder_list(["A", "B", "C"], 1, target) is None


def test_visual_gap_snaps_one_slot_per_row_height() -> None:
    tops = [100, 164, 228]
    assert visual_gap_for_pointer(120, tops) == 0
    assert visual_gap_for_pointer(132, tops) == 1
    assert visual_gap_for_pointer(196, tops) == 2
    assert visual_gap_for_pointer(300, tops) == 3


def test_target_index_for_drag_preview_moves_one_slot() -> None:
    tops = [100, 164, 228]
    assert target_index_for_drag_preview(120, source_index=1, slot_tops=tops) == 0
    assert target_index_for_drag_preview(196, source_index=1, slot_tops=tops) == 3
    # Adjacent gap → noop at apply_list_move (source=1, target=2).
    assert target_index_for_drag_preview(132, source_index=1, slot_tops=tops) == 2


def test_wheel_nudge_then_commit_sequence() -> None:
    channels = ["c0", "c1", "c2", "c3"]
    source = 1
    target = source
    for _ in range(2):
        target = nudge_insert_index(target, 1, length=len(channels))
    assert target == 3
    assert reorder_list(channels, source, target) == ["c0", "c2", "c1", "c3"]


class _ReorderSessionHarness:
    """Minimal mirror of App reorder session (no Tk)."""

    def __init__(self, channel_names: list[str]) -> None:
        self._channel_rows = [_FakeRow(100 + index * 64) for index in range(len(channel_names))]
        self.channels = list(channel_names)
        self._reorder_source_index = 0
        self._reorder_target_index = 0
        self._dragging = False
        self.events: list[str] = []

    def begin(self, source: int) -> None:
        self._dragging = True
        self._reorder_source_index = source
        self._reorder_target_index = source
        self.events.append(f"begin:{source}")

    def motion(self, y_root: int) -> None:
        if not self._dragging:
            return
        target = target_index_for_drag_source(
            y_root,
            source_index=self._reorder_source_index,
            rows=self._channel_rows,
        )
        if target != self._reorder_target_index:
            self._reorder_target_index = target
            self.events.append(f"motion:{target}")

    def end(self) -> None:
        if not self._dragging:
            return
        result = reorder_list(
            self.channels,
            self._reorder_source_index,
            self._reorder_target_index,
        )
        self.events.append(f"end:{result}")
        if result is not None:
            self.channels = result
        self._dragging = False


def test_full_drag_session_harness() -> None:
    session = _ReorderSessionHarness(["a", "b", "c", "d"])
    session.begin(1)
    session.motion(260)
    session.end()
    assert session.channels == ["a", "c", "b", "d"]
    assert session.events == ["begin:1", "motion:3", "end:['a', 'c', 'b', 'd']"]


def _make_channel_row():
    try:
        import customtkinter as ctk
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk unavailable: {exc}")

    from stream_monitor.channel_row import ChannelRow

    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    return root, ChannelRow


def test_channel_row_long_press_state_machine() -> None:
    from stream_monitor.channel_row import ChannelRow

    log: list[str] = []
    root, _ = _make_channel_row()
    pending: list[Any] = []

    def fake_after(_ms: int, callback: Any) -> str:
        pending.append(callback)
        return "after-id"

    def fake_after_cancel(_after_id: str) -> None:
        pending.clear()

    row = ChannelRow(
        root,
        {"platform": "twitch", "name": "chan", "enabled": True},
        on_delete=lambda: None,
        on_move_up=lambda: None,
        on_move_down=lambda: None,
        on_toggle_enabled=lambda: None,
        on_reorder_begin=lambda: log.append("begin"),
        on_reorder_motion=lambda y: log.append(f"motion:{y}"),
        on_reorder_release=lambda: log.append("release"),
    )
    row.after = fake_after  # type: ignore[method-assign]
    row.after_cancel = fake_after_cancel  # type: ignore[method-assign]

    press = type("Ev", (), {"y_root": 100})()
    row._on_drag_handle_press(press)
    assert pending, "long-press timer should be scheduled"
    assert log == []

    pending[0]()
    assert log == ["begin", "motion:100"]

    move = type("Ev", (), {"y_root": 180})()
    row._on_drag_handle_motion(move)
    assert log[-1] == "motion:180"

    row._on_drag_handle_release(type("Ev", (), {})())
    assert log[-1] == "release"
    assert row._drag_active is False

    root.destroy()


def test_channel_row_motion_before_arm_cancels_long_press() -> None:
    from stream_monitor.channel_row import ChannelRow

    log: list[str] = []
    root, _ = _make_channel_row()
    pending: list[Any] = []

    row = ChannelRow(
        root,
        {"platform": "twitch", "name": "chan", "enabled": True},
        on_delete=lambda: None,
        on_move_up=lambda: None,
        on_move_down=lambda: None,
        on_toggle_enabled=lambda: None,
        on_reorder_begin=lambda: log.append("begin"),
    )
    row.after = lambda _ms, cb: pending.append(cb) or "id"  # type: ignore[method-assign]
    row.after_cancel = lambda _id: pending.clear()  # type: ignore[method-assign]

    row._on_drag_handle_press(type("Ev", (), {"y_root": 100})())
    row._on_drag_handle_motion(type("Ev", (), {"y_root": 130})())
    assert pending == []
    row._on_drag_handle_release(type("Ev", (), {})())
    assert log == []

    root.destroy()


def test_move_frame_button_heights_match_spec() -> None:
    from stream_monitor.channel_row import ChannelRow

    root, _ = _make_channel_row()
    row = ChannelRow(
        root,
        {"platform": "twitch", "name": "x", "enabled": True},
        on_delete=lambda: None,
        on_move_up=lambda: None,
        on_move_down=lambda: None,
        on_toggle_enabled=lambda: None,
    )
    row.pack()
    root.update_idletasks()
    assert row.up_btn.cget("height") == 20
    assert row.down_btn.cget("height") == 20
    assert row.drag_handle.cget("height") == 6
    root.destroy()
