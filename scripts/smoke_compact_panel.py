"""Isolated native-widget smoke for the compact control panel.

Does not start live monitoring, browser automation, or notifications.
Writes screenshots and JSON under a temp directory (not the repo).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_INTERACTIVE = (
    "start_btn",
    "stop_btn",
    "mode_seg",
    "duration_seg",
    "interval_entry",
    "notify_on_live_switch",
    "notify_on_upcoming_switch",
    "notify_on_open_failure_switch",
    "open_on_live_switch",
    "open_on_upcoming_switch",
    "after_open_menu",
)


def _widget_inside(panel: Any, widget: Any, *, slack: int = 2) -> bool:
    """True when ``widget`` is fully inside ``panel`` root bounds."""
    if not widget.winfo_ismapped():
        return False
    px = panel.winfo_rootx()
    py = panel.winfo_rooty()
    pw = panel.winfo_width()
    ph = panel.winfo_height()
    x = widget.winfo_rootx()
    y = widget.winfo_rooty()
    w = widget.winfo_width()
    h = widget.winfo_height()
    if w < 8 or h < 8:
        return False
    return (
        x >= px - slack
        and y >= py - slack
        and x + w <= px + pw + slack
        and y + h <= py + ph + slack
    )


def _assert_all_inside(app: Any, panel: Any, *, label: str) -> None:
    app.update_idletasks()
    app.update()
    missing: list[str] = []
    for name in _INTERACTIVE:
        widget = getattr(app, name)
        if not _widget_inside(panel, widget):
            missing.append(
                f"{name} mapped={widget.winfo_ismapped()} "
                f"size={widget.winfo_width()}x{widget.winfo_height()} "
                f"rel=({widget.winfo_rootx() - panel.winfo_rootx()},"
                f"{widget.winfo_rooty() - panel.winfo_rooty()})"
            )
    if missing:
        raise AssertionError(f"{label}: clipped/hidden controls: {missing}")


def main() -> int:
    out_dir = Path(tempfile.mkdtemp(prefix="hs_compact_smoke_"))
    shots: list[Path] = []
    dims: dict[str, dict[str, int]] = {}

    with (
        patch("stream_monitor.app.TrayIcon") as tray_cls,
        patch("stream_monitor.app.heal_startup_command_if_enabled"),
        patch("stream_monitor.app.is_startup_enabled", return_value=False),
        patch("stream_monitor.app.config_manager.load") as load_cfg,
        patch("stream_monitor.app.config_manager.save"),
        patch("stream_monitor.app.SeenVideoDB"),
        patch("stream_monitor.app.MonitorController") as ctrl_cls,
        patch("stream_monitor.app.platform_services", return_value=MagicMock()),
        patch("stream_monitor.app.configure_viewer_engagement"),
        patch("stream_monitor.app.i18n.subscribe", return_value=lambda: None),
    ):
        tray = tray_cls.return_value
        tray.start = MagicMock()
        tray.update_tooltip_key = MagicMock()

        controller = ctrl_cls.return_value
        controller.mode = "idle"
        controller.generation = 0
        controller.wake_verify_active = False
        controller.snapshot_display_names.return_value = {}

        load_cfg.return_value = {
            "channels": [],
            "check_interval": 10,
            "monitor_mode": "trigger",
            "language": "zh_TW",
            "minimize_to_tray": False,
            "trigger_settings": {
                "notify_on_live": True,
                "open_on_live": True,
                "after_open": "stop_monitor",
                "notify_on_upcoming": False,
                "open_on_upcoming": False,
                "notify_on_open_failure": True,
            },
            "browser_settings": {},
            "viewer_engagement": {},
            "window_geometry": "1000x640",
        }

        from stream_monitor import i18n
        from stream_monitor.app import App
        from stream_monitor.app_ui import (
            _COMPACT_ACT_BTN_HEIGHT,
            _COMPACT_CTRL_PAD_X,
            _COMPACT_CTRL_PAD_Y,
            _MIN_WINDOW_HEIGHT,
            _MIN_WINDOW_WIDTH,
        )

        app = App(silent=False)
        app.update_idletasks()
        app.update()

        assert hasattr(app, "mode_seg")
        assert hasattr(app, "duration_seg")
        assert hasattr(app, "status_dot")
        assert hasattr(app, "_run_flow")
        assert hasattr(app, "_settings_flow")
        assert app.start_btn.cget("height") == _COMPACT_ACT_BTN_HEIGHT
        assert app.stop_btn.cget("state") == "disabled"
        assert app.start_btn.cget("state") == "normal"
        assert "監聽" in app.mode_seg.get() or "Trigger" in app.mode_seg.get()
        assert app._interval_caption.cget("text")
        assert app._notify_group_label.cget("text")
        assert app._open_group_label.cget("text")
        assert _COMPACT_CTRL_PAD_X == 16
        assert _COMPACT_CTRL_PAD_Y == 6
        assert _MIN_WINDOW_WIDTH == 920
        assert _MIN_WINDOW_HEIGHT == 560

        # Mocked start handlers — never start live monitoring.
        handlers = {
            "_on_start": MagicMock(name="start"),
            "_on_watch": MagicMock(name="watch"),
            "_on_start_once": MagicMock(name="start_once"),
            "_on_watch_once": MagicMock(name="watch_once"),
        }
        for attr, mock in handlers.items():
            setattr(app, attr, mock)

        def _dispatch(kind: str, once: bool) -> None:
            app._monitor_kind = kind
            app._monitor_once = once
            app._segment_syncing = True
            try:
                app.mode_seg.set(app._mode_segment_label(kind))
                app.duration_seg.set(app._duration_segment_label(once))
            finally:
                app._segment_syncing = False
            app._apply_monitor_mode_buttons()
            app._on_compact_run()

        _dispatch("trigger", False)
        _dispatch("watch", False)
        _dispatch("trigger", True)
        _dispatch("watch", True)
        assert handlers["_on_start"].call_count == 1
        assert handlers["_on_watch"].call_count == 1
        assert handlers["_on_start_once"].call_count == 1
        assert handlers["_on_watch_once"].call_count == 1

        # Segment callbacks while idle must not start monitoring.
        for mock in handlers.values():
            mock.reset_mock()
        controller.mode = "idle"
        app._on_mode_segment(app._mode_segment_label("watch"))
        app._on_duration_segment(app._duration_segment_label(True))
        assert app._monitor_kind == "watch"
        assert app._monitor_once is True
        assert all(m.call_count == 0 for m in handlers.values())

        app.deiconify()
        app.lift()
        from PIL import ImageGrab

        def _shot(path: Path) -> dict[str, int]:
            app.update_idletasks()
            app.update()
            app._reflow_compact_panel()
            app.update_idletasks()
            app.update()
            x = app.winfo_rootx()
            y = app.winfo_rooty()
            w = max(app.winfo_width(), 1)
            h = max(app.winfo_height(), 1)
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
            return {
                "winfo_width": int(app.winfo_width()),
                "winfo_height": int(app.winfo_height()),
                "panel_width": int(app._compact_ctrl.winfo_width()),
                "panel_height": int(app._compact_ctrl.winfo_height()),
            }

        panel = app._compact_ctrl

        # Wide capture (requested 1100; report actual winfo).
        app.geometry("1100x640+80+80")
        app.update_idletasks()
        app.update()
        for lang in ("zh_TW", "en", "ja"):
            i18n.set_language(lang, notify=False)
            app._on_language_changed()
            app.update_idletasks()
            app.update()
            app._reflow_compact_panel()
            _assert_all_inside(app, panel, label=f"wide/{lang}")

        wide = out_dir / "compact_panel_wide.png"
        dims["wide"] = _shot(wide)
        shots.append(wide)
        assert dims["wide"]["winfo_width"] >= 1000

        # Narrow = minsize floor (geometry 760 is clamped to 920 by Tk minsize).
        app.geometry("760x640+80+80")
        app.update_idletasks()
        app.update()
        app._reflow_compact_panel()
        for lang in ("zh_TW", "en", "ja"):
            i18n.set_language(lang, notify=False)
            app._on_language_changed()
            app.update_idletasks()
            app.update()
            app._reflow_compact_panel()
            _assert_all_inside(app, panel, label=f"narrow/{lang}")

        narrow = out_dir / "compact_panel_narrow.png"
        dims["narrow"] = _shot(narrow)
        shots.append(narrow)
        assert dims["narrow"]["winfo_width"] == _MIN_WINDOW_WIDTH

        meta = out_dir / "smoke_result.json"
        meta.write_text(
            json.dumps(
                {
                    "ok": True,
                    "out_dir": str(out_dir),
                    "shots": [str(p) for p in shots],
                    "requested_geometry_wide": "1100x640",
                    "requested_geometry_narrow": "760x640",
                    "actual": dims,
                    "minsize_width": _MIN_WINDOW_WIDTH,
                    "start_height": int(app.start_btn.cget("height")),
                    "widgets": list(_INTERACTIVE),
                    "langs_checked": ["zh_TW", "en", "ja"],
                    "start_actions_dispatched": [
                        "trigger",
                        "watch",
                        "trigger_once",
                        "watch_once",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(meta.read_text(encoding="utf-8"))
        app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
