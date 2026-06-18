"""CTk widget-level E2E: preview repack modes match full repack geometry."""

from __future__ import annotations

import pytest

from stream_monitor.channel_reorder import (
    apply_list_move,
    preview_row_indices,
    preview_visual_step,
)
from stream_monitor.channel_reorder_ui import (
    full_repack_rows,
    repack_preview_rows,
    visual_pack_order,
)


def _make_row_parent():
    try:
        import customtkinter as ctk
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Tk unavailable: {exc}")
    root.withdraw()
    parent = ctk.CTkFrame(root, width=400)
    parent.pack()
    return root, parent


def _make_rows(parent, count: int):
    import customtkinter as ctk

    rows = []
    for i in range(count):
        frame = ctk.CTkFrame(parent, height=58, width=380)
        frame.pack_propagate(False)
        rows.append(frame)
    return rows


@pytest.mark.parametrize("num_rows", [4, 6, 8])
def test_repack_modes_match_full_repack_for_all_moves(num_rows: int) -> None:
    root, parent = _make_row_parent()
    rows = _make_rows(parent, num_rows)
    identity = list(range(num_rows))

    for source in range(num_rows):
        for target in range(num_rows + 1):
            if apply_list_move(source, target, num_rows) is None:
                continue
            order = preview_row_indices(source, target, num_rows)

            full_repack_rows(rows, identity)
            root.update_idletasks()
            full_repack_rows(rows, order)
            root.update_idletasks()
            expected = visual_pack_order(rows)

            full_repack_rows(rows, identity)
            root.update_idletasks()
            mode = repack_preview_rows(rows, order, identity, source)
            root.update_idletasks()
            actual = visual_pack_order(rows)

            assert actual == expected == order
            step = preview_visual_step(identity, order, source)
            if step == 1:
                assert mode == "incremental"
            elif len(order) > 1:
                assert mode in {"partial", "full"}

    root.destroy()


def test_chained_preview_repack_matches_drag_session() -> None:
    """Simulate slot-by-slot drag: each step uses previous preview as baseline."""
    root, parent = _make_row_parent()
    rows = _make_rows(parent, 5)
    identity = list(range(5))
    source = 1
    targets = [1, 2, 3, 4]

    full_repack_rows(rows, identity)
    root.update_idletasks()

    previous = list(identity)
    for target in targets:
        order = preview_row_indices(source, target, len(rows))
        if order == previous:
            continue
        mode = repack_preview_rows(rows, order, previous, source)
        root.update_idletasks()
        assert visual_pack_order(rows) == order
        assert mode in {"incremental", "partial", "full", "skip"}
        previous = list(order)

    root.destroy()
