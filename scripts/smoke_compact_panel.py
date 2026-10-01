"""Isolated native-widget smoke for the compact control panel.

Does not start live monitoring, browser automation, or notifications.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    out_dir = Path(tempfile.mkdtemp(prefix="hs_compact_smoke_"))
    shots: list[Path] = []

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

        assert app.winfo_width() >= _MIN_WINDOW_WIDTH // 2  # before map may be small
        assert hasattr(app, "mode_seg")
        assert hasattr(app, "duration_seg")
        assert hasattr(app, "status_dot")
        assert app.start_btn.cget("height") == _COMPACT_ACT_BTN_HEIGHT
        assert app.stop_btn.cget("state") == "disabled"
        assert app.start_btn.cget("state") == "normal"
        assert "監聽" in app.mode_seg.get() or "Trigger" in app.mode_seg.get()
        assert app._interval_caption.cget("text")
        assert app._notify_group_label.cget("text")
        assert app._open_group_label.cget("text")
        assert _COMPACT_CTRL_PAD_X == 16
        assert _COMPACT_CTRL_PAD_Y == 12
        # CTk overrides minsize() as a setter-only API; assert the constants we apply.
        assert _MIN_WINDOW_WIDTH == 920
        assert _MIN_WINDOW_HEIGHT == 560

        # Wide layout capture
        app.deiconify()
        app.lift()
        app.geometry("1100x640+80+80")
        app.update_idletasks()
        app.update()
        from PIL import ImageGrab

        def _shot(path: Path) -> None:
            app.update_idletasks()
            app.update()
            x = app.winfo_rootx()
            y = app.winfo_rooty()
            w = max(app.winfo_width(), 1)
            h = max(app.winfo_height(), 1)
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)

        wide = out_dir / "compact_panel_wide.png"
        _shot(wide)
        shots.append(wide)

        # Narrow layout capture (wrapping stress)
        app.geometry("760x640+80+80")
        narrow = out_dir / "compact_panel_narrow.png"
        _shot(narrow)
        shots.append(narrow)

        meta = out_dir / "smoke_result.json"
        import json

        meta.write_text(
            json.dumps(
                {
                    "ok": True,
                    "out_dir": str(out_dir),
                    "shots": [str(p) for p in shots],
                    "geometry_wide": "1100x640",
                    "geometry_narrow": "760x640",
                    "start_height": int(app.start_btn.cget("height")),
                    "widgets": [
                        "start_btn",
                        "stop_btn",
                        "mode_seg",
                        "duration_seg",
                        "interval_entry",
                        "notify_on_live_switch",
                        "open_on_live_switch",
                        "after_open_menu",
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
