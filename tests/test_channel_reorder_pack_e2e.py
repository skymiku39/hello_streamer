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

# One hidden CTk root for this module; keep alive until pytest process exits.
# Parameterized cases share this root; per-test cleanup destroys child hosts only.
# Prefer an already-live default root over creating a second top-level CTk.
_CTK_ROOT = None
_CTK_ROOT_ERROR: BaseException | None = None


def _module_ctk_root():
    """Return a live CTk/Tk root, creating one only when none exists yet."""
    global _CTK_ROOT, _CTK_ROOT_ERROR
    if _CTK_ROOT is not None:
        try:
            if int(_CTK_ROOT.winfo_exists()):
                return _CTK_ROOT
        except Exception:  # noqa: BLE001
            _CTK_ROOT = None

    try:
        import tkinter as tk

        existing = tk._default_root
        if existing is not None and int(existing.winfo_exists()):
            _CTK_ROOT = existing
            _CTK_ROOT_ERROR = None
            return _CTK_ROOT
    except Exception:  # noqa: BLE001
        pass

    if _CTK_ROOT_ERROR is not None:
        pytest.skip(f"Tk unavailable: {_CTK_ROOT_ERROR}")
    try:
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
    except Exception as exc:  # noqa: BLE001
        _CTK_ROOT_ERROR = exc
        pytest.skip(f"Tk unavailable: {exc}")
    _CTK_ROOT = root
    return root


def _make_row_parent():
    """Create a per-test CTkFrame host under the shared module root."""
    import customtkinter as ctk

    root = _module_ctk_root()
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
    try:
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
    finally:
        parent.destroy()


def test_chained_preview_repack_matches_drag_session() -> None:
    """Simulate slot-by-slot drag: each step uses previous preview as baseline."""
    root, parent = _make_row_parent()
    try:
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
    finally:
        parent.destroy()
