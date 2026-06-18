"""Runtime verification for channel drag-reorder (run: uv run python scripts/verify_channel_reorder.py)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "debug-reorder-verify.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import customtkinter as ctk

    from stream_monitor.channel_reorder import (
        LONG_PRESS_MS,
        preview_order_delta,
        preview_row_indices,
        preview_visual_step,
        reorder_list,
        target_index_for_drag_source,
    )
    from stream_monitor.channel_row import ChannelRow

    results: list[dict[str, object]] = []

    # --- Layer 1: pure reorder logic ---
    logic_cases = {
        "down": reorder_list(["a", "b", "c", "d"], 1, 3),
        "up": reorder_list(["a", "b", "c", "d"], 2, 0),
        "noop": reorder_list(["a", "b", "c"], 1, 2),
    }
    logic_ok = logic_cases["down"] == ["a", "c", "b", "d"] and logic_cases[
        "up"
    ] == ["c", "a", "b", "d"] and logic_cases["noop"] is None
    results.append({"layer": "logic", "ok": logic_ok, "cases": logic_cases})

    # --- Layer 2: pointer → target mapping ---
    from dataclasses import dataclass

    @dataclass
    class _Row:
        top: int

        def winfo_rooty(self) -> int:
            return self.top

        def winfo_height(self) -> int:
            return 64

    rows = [_Row(100), _Row(164), _Row(228), _Row(292)]
    pointer_ok = target_index_for_drag_source(260, source_index=1, rows=rows) == 3
    results.append({"layer": "pointer", "ok": pointer_ok, "target": 3})

    # --- Layer 3: ChannelRow long-press state machine (Tk) ---
    ui_log: list[str] = []
    root = ctk.CTk()
    root.withdraw()
    pending: list[object] = []

    row = ChannelRow(
        root,
        {"platform": "twitch", "name": "alpha", "enabled": True},
        on_delete=lambda: None,
        on_move_up=lambda: None,
        on_move_down=lambda: None,
        on_toggle_enabled=lambda: None,
        on_reorder_begin=lambda: ui_log.append("begin"),
        on_reorder_motion=lambda y: ui_log.append(f"motion:{y}"),
        on_reorder_release=lambda: ui_log.append("release"),
    )
    row.after = lambda _ms, cb: pending.append(cb) or "id"  # type: ignore[method-assign]
    row.after_cancel = lambda _id: pending.clear()  # type: ignore[method-assign]
    row.pack()
    root.update_idletasks()

    row._on_drag_handle_press(type("Ev", (), {"y_root": 100})())
    timer_ok = len(pending) == 1
    pending[0]()
    row._on_drag_handle_motion(type("Ev", (), {"y_root": 200})())
    row._on_drag_handle_release(type("Ev", (), {})())

    ui_ok = ui_log == ["begin", "motion:100", "motion:200", "release"]
    geometry_ok = (
        row.up_btn.cget("height") == 20
        and row.down_btn.cget("height") == 20
        and row.drag_handle.cget("height") == 6
    )
    results.append(
        {
            "layer": "channel_row",
            "ok": ui_ok and timer_ok and geometry_ok,
            "log": ui_log,
            "geometry": {
                "up": row.up_btn.cget("height"),
                "down": row.down_btn.cget("height"),
                "handle": row.drag_handle.cget("height"),
            },
        }
    )

    root.destroy()

    # --- Layer 4: preview repack strategy (pure) ---
    prev = list(range(4))
    nxt = preview_row_indices(1, 3, 4)
    delta = preview_order_delta(prev, nxt)
    step = preview_visual_step(prev, nxt, 1)
    strategy_ok = delta == {1, 2} and step == 1 and len(delta) < len(prev)
    results.append(
        {
            "layer": "preview_repack",
            "ok": strategy_ok,
            "delta": sorted(delta),
            "step": step,
            "order": nxt,
        }
    )

    # --- Layer 5: CTk pack geometry (incremental/partial ≡ full) ---
    from stream_monitor.channel_reorder_ui import (
        full_repack_rows,
        repack_preview_rows,
        visual_pack_order,
    )

    root2 = ctk.CTk()
    root2.withdraw()
    parent2 = ctk.CTkFrame(root2, width=400)
    parent2.pack()
    pack_rows = []
    for _i in range(4):
        frame = ctk.CTkFrame(parent2, height=58, width=380)
        frame.pack_propagate(False)
        pack_rows.append(frame)
    identity4 = list(range(4))
    pack_failures: list[str] = []
    for src in range(4):
        for tgt in range(5):
            if reorder_list(identity4, src, tgt) is None:
                continue
            order = preview_row_indices(src, tgt, 4)
            full_repack_rows(pack_rows, identity4)
            root2.update_idletasks()
            full_repack_rows(pack_rows, order)
            root2.update_idletasks()
            expected = visual_pack_order(pack_rows)
            full_repack_rows(pack_rows, identity4)
            root2.update_idletasks()
            repack_preview_rows(pack_rows, order, identity4, src)
            root2.update_idletasks()
            actual = visual_pack_order(pack_rows)
            if actual != expected or actual != order:
                pack_failures.append(f"{src}->{tgt}")
    pack_ok = not pack_failures
    results.append(
        {
            "layer": "pack_e2e",
            "ok": pack_ok,
            "failures": pack_failures[:5],
            "checked": 4 * 5,
        }
    )
    root2.destroy()

    ok = (
        logic_ok
        and pointer_ok
        and ui_ok
        and timer_ok
        and geometry_ok
        and strategy_ok
        and pack_ok
    )
    payload = {
        "ok": ok,
        "long_press_ms": LONG_PRESS_MS,
        "results": results,
        "t": time.time(),
    }
    LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
