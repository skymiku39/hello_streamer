"""Isolated Tk screenshots for settings/reset UI review (no live I/O).

Captures:
  - main window at supported minimum width and at 1100px (legacy + mockup names)
  - idle and visually-simulated run states (no polling / no network)
  - AppSettingsDialog
  - geometry assertions: equal top-card heights; action controls contained

Uses a disposable temp portable root with synthetic long channel names and
offline/live row statuses. Never reads real project channels/config, never
starts monitoring, and never touches the Windows startup registry.
"""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="hs_ui_shot_"))
_OUT_DIR = _ROOT / "artifacts"
_OUT_MAIN_MIN = _OUT_DIR / "ui-settings-reset-main-min.png"
_OUT_MAIN_1100 = _OUT_DIR / "ui-settings-reset-main-1100.png"
_OUT_SETTINGS = _OUT_DIR / "ui-settings-reset-dialog.png"
_OUT_MOCKUP_MIN_IDLE = _OUT_DIR / "ui-mockup-layout-main-min-idle.png"
_OUT_MOCKUP_1100_IDLE = _OUT_DIR / "ui-mockup-layout-main-1100-idle.png"
_OUT_MOCKUP_MIN_RUN = _OUT_DIR / "ui-mockup-layout-main-min-run.png"
_OUT_MOCKUP_1100_RUN = _OUT_DIR / "ui-mockup-layout-main-1100-run.png"
_OUT_GEOMETRY_JSON = _OUT_DIR / "ui-mockup-layout-geometry.json"

_SYNTHETIC_CHANNELS = [
    {
        "platform": "youtube",
        "name": "UC_SyntheticVeryLongChannelName_ForLayoutStress_ABCDEFG",
        "enabled": True,
        "channel_mode": "trigger",
    },
    {
        "platform": "twitch",
        "name": "synthetic_super_long_twitch_channel_name_layout_probe",
        "enabled": True,
        "channel_mode": "trigger",
    },
    {
        "platform": "twitch",
        "name": "synth_offline_demo",
        "enabled": True,
        "channel_mode": "trigger",
    },
]


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _enable_dpi_awareness() -> None:
    """Best-effort Per-Monitor DPI awareness so GetWindowRect matches grab."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _hwnd_for_tk(window) -> int:
    window.update_idletasks()
    raw = window.winfo_id()
    user32 = ctypes.windll.user32
    # CTk / Tk child id → nearest top-level HWND.
    get_ancestor = user32.GetAncestor
    get_ancestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    get_ancestor.restype = ctypes.c_void_p
    hwnd = get_ancestor(raw, 2)  # GA_ROOT
    return int(hwnd or raw)


def _activate_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    get_foreground = user32.GetForegroundWindow
    get_thread = user32.GetWindowThreadProcessId
    attach_threads = user32.AttachThreadInput
    get_current_thread = ctypes.windll.kernel32.GetCurrentThreadId
    get_foreground.restype = ctypes.c_void_p
    get_thread.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_thread.restype = ctypes.c_ulong
    get_current_thread.restype = ctypes.c_ulong
    attach_threads.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
    attach_threads.restype = ctypes.c_bool

    foreground_hwnd = get_foreground()
    foreground_pid = ctypes.c_ulong()
    target_pid = ctypes.c_ulong()
    foreground_thread = get_thread(foreground_hwnd, ctypes.byref(foreground_pid))
    target_thread = get_thread(hwnd, ctypes.byref(target_pid))
    current_thread = get_current_thread()
    attached: list[tuple[int, int]] = []
    for other_thread in (foreground_thread, target_thread):
        if other_thread and other_thread != current_thread:
            pair = (current_thread, other_thread)
            if pair not in attached and attach_threads(*pair, True):
                attached.append(pair)
    try:
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        return bool(get_foreground() == hwnd)
    finally:
        for first, second in reversed(attached):
            attach_threads(first, second, False)


def _grab_window(window, dest: Path) -> None:
    from PIL import Image, ImageGrab

    window.update_idletasks()
    window.update()
    hwnd = _hwnd_for_tk(window)
    user32 = ctypes.windll.user32
    rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetWindowRect failed for hwnd={hwnd}")
    _activate_window(hwnd)
    time.sleep(0.15)
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w < 200 or h < 200:
        raise RuntimeError(f"unexpected window size {w}x{h} bbox={bbox}")
    ImageGrab.grab(bbox=bbox, all_screens=True).save(dest)
    img = Image.open(dest)
    px = img.getpixel((min(80, img.width - 1), min(80, img.height - 1)))
    if isinstance(px, int):
        bright = px > 200
    else:
        bright = sum(px[:3]) / 3 > 200
    if bright:
        raise RuntimeError(
            f"screenshot looks like wrong window (pixel={px}); kept {dest}"
        )


def _pump(app, rounds: int = 12) -> None:
    for _ in range(rounds):
        app.update_idletasks()
        app.update()
        time.sleep(0.04)


def _apply_synthetic_statuses(app) -> None:
    from stream_monitor.monitor import ChannelStatus

    statuses = {
        "youtube:UC_SyntheticVeryLongChannelName_ForLayoutStress_ABCDEFG": ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=synthLIVE0001",
            title="Synthetic live title — layout probe (no network)",
            started_at="2026-10-01T02:00:00+00:00",
        ),
        "twitch:synthetic_super_long_twitch_channel_name_layout_probe": ChannelStatus(
            status=False,
            url="",
            title="Last stream: synthetic offline demo",
            vod_url="",
        ),
        "twitch:synth_offline_demo": ChannelStatus(
            status=False,
            title="Offline representative row",
        ),
    }
    for row in app._channel_rows:
        status = statuses.get(row.key)
        if status is not None:
            row.set_status(status)
    app.scroll_frame.redraw_all()


def _widget_box(widget) -> dict[str, int]:
    widget.update_idletasks()
    return {
        "x": int(widget.winfo_rootx()),
        "y": int(widget.winfo_rooty()),
        "w": int(widget.winfo_width()),
        "h": int(widget.winfo_height()),
    }


def _contains(outer: dict[str, int], inner: dict[str, int], *, pad: int = 0) -> bool:
    return (
        inner["x"] >= outer["x"] - pad
        and inner["y"] >= outer["y"] - pad
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + pad
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + pad
    )


def _assert_control_panel_geometry(app, *, label: str) -> dict:
    from stream_monitor.app_ui import _CTRL_MON_WIDE_WIDTH, _CTRL_TOP_CARD_HEIGHT

    app.update_idletasks()
    app.update()
    mon = _widget_box(app._mon_card)
    trg = _widget_box(app._trg_card)
    act = _widget_box(app._act_card)
    start = _widget_box(app.start_btn)
    stop = _widget_box(app.stop_btn)
    status = _widget_box(app._status_frame)

    if abs(mon["h"] - trg["h"]) > 1:
        raise RuntimeError(
            f"{label}: unequal top-card heights mon={mon['h']} trg={trg['h']}"
        )
    if mon["h"] < _CTRL_TOP_CARD_HEIGHT - 2:
        raise RuntimeError(
            f"{label}: top cards shorter than reference "
            f"{mon['h']} < {_CTRL_TOP_CARD_HEIGHT}"
        )
    for name, box in (("start", start), ("stop", stop), ("status", status)):
        if not _contains(act, box, pad=2):
            raise RuntimeError(
                f"{label}: {name} not contained in action card "
                f"act={act} child={box}"
            )
    # Action card must sit below both top cards (independent row).
    if act["y"] < max(mon["y"] + mon["h"], trg["y"] + trg["h"]) - 2:
        raise RuntimeError(
            f"{label}: action card overlaps top row act.y={act['y']} "
            f"top_bottom={max(mon['y'] + mon['h'], trg['y'] + trg['h'])}"
        )
    wide = bool(app._ctrl_panel_wide)
    if wide and abs(mon["w"] - _CTRL_MON_WIDE_WIDTH) > 8:
        raise RuntimeError(
            f"{label}: wide monitor card width {mon['w']} != {_CTRL_MON_WIDE_WIDTH}"
        )
    return {
        "label": label,
        "mon_h": mon["h"],
        "trg_h": trg["h"],
        "mon_w": mon["w"],
        "trg_w": trg["w"],
        "act_h": act["h"],
        "panel_w": int(app._ctrl_panel.winfo_width()),
        "wide": wide,
    }


def _simulate_run_ui(app) -> None:
    """Paint running chrome without starting the monitor / network."""
    from stream_monitor.app_ui import _CLR_LIVE, monitor_mode_button_colors

    colors = monitor_mode_button_colors("trigger")
    app.start_btn.configure(state="disabled", **colors["start"])
    app.stop_btn.configure(state="normal", **colors["stop"])
    app.mode_kind_seg.configure(state="disabled")
    app.mode_freq_seg.configure(state="disabled")
    app._set_status_text("status.trigger_running", _CLR_LIVE)
    app._status_subline_key = "status.poll_waiting"
    app._status_subline_kwargs = {}
    app._render_status_text()


def _restore_idle_ui(app) -> None:
    app._apply_monitor_mode_buttons()
    from stream_monitor.app_ui import _CLR_OFFLINE

    app._set_status_text("status.idle", _CLR_OFFLINE)
    app._set_awaiting_start_subline()


def main() -> int:
    _enable_dpi_awareness()

    import stream_monitor
    import stream_monitor.config_manager as config_manager_mod
    import stream_monitor.portable_storage as portable_storage_mod
    import stream_monitor.startup as startup_mod
    import stream_monitor.tray as tray_mod
    from stream_monitor.app_ui import _MIN_WINDOW_HEIGHT, _MIN_WINDOW_WIDTH
    from stream_monitor.config_manager import DEFAULT_CONFIG

    def _temp_base() -> Path:
        return _TMP

    # Patch both the package attribute and already-bound imports used by
    # config/db path resolution so we never touch the real project root.
    stream_monitor.base_dir = _temp_base  # type: ignore[assignment]
    portable_storage_mod.base_dir = _temp_base  # type: ignore[assignment]
    config_manager_mod._config_path = lambda: _TMP / "config.json"  # type: ignore[assignment]

    # Never read or write the real startup registry / autostart entry.
    startup_mod.is_startup_enabled = lambda: False  # type: ignore[assignment]
    startup_mod.heal_startup_command_if_enabled = lambda: False  # type: ignore[assignment]
    startup_mod.enable_startup = lambda *_a, **_k: False  # type: ignore[assignment]
    startup_mod.disable_startup = lambda: True  # type: ignore[assignment]

    class _SilentTray:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def update_tooltip_key(self, *_a, **_k) -> None:
            return None

        def set_tooltip(self, *_a, **_k) -> None:
            return None

        def refresh_menu(self) -> None:
            return None

    tray_mod.TrayIcon = _SilentTray  # type: ignore[misc,assignment]

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["channels"] = list(_SYNTHETIC_CHANNELS)
    cfg["language"] = "zh_TW"
    cfg["window_geometry"] = f"{_MIN_WINDOW_WIDTH}x{_MIN_WINDOW_HEIGHT}+40+40"
    cfg["minimize_to_tray"] = True
    cfg["run_on_startup"] = False
    cfg.pop("channel_status_cache", None)
    (_TMP / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    import stream_monitor.app as app_mod

    app_mod.TrayIcon = _SilentTray  # type: ignore[misc,assignment]
    app_mod.is_startup_enabled = lambda: False  # type: ignore[assignment]
    app_mod.heal_startup_command_if_enabled = lambda: False  # type: ignore[assignment]
    app_mod.portable_paths = lambda root=None: portable_storage_mod.PortablePaths(  # type: ignore[assignment]
        (_TMP if root is None else root).resolve()
    )

    app = app_mod.App(silent=False)
    if len(app._channel_rows) != len(_SYNTHETIC_CHANNELS):
        raise RuntimeError(
            f"expected {len(_SYNTHETIC_CHANNELS)} synthetic rows, "
            f"got {len(app._channel_rows)}; config={app.config.get('channels')!r}"
        )
    # Force idle button colors / states (startup already calls this; refresh again).
    app._apply_monitor_mode_buttons()
    _apply_synthetic_statuses(app)
    # Never start monitoring / polling / browser.
    assert app._controller.mode == "idle"
    assert app._controller.is_running is False

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    geometry_report: list[dict] = []

    try:
        app.geometry(f"{_MIN_WINDOW_WIDTH}x{_MIN_WINDOW_HEIGHT}+40+40")
        app.attributes("-topmost", True)
        app.deiconify()
        app.lift()
        app.focus_force()
        _pump(app)
        app._on_ctrl_panel_configure()
        _pump(app, rounds=4)
        geometry_report.append(_assert_control_panel_geometry(app, label="min-idle"))
        _grab_window(app, _OUT_MAIN_MIN)
        paths.append(_OUT_MAIN_MIN)
        _grab_window(app, _OUT_MOCKUP_MIN_IDLE)
        paths.append(_OUT_MOCKUP_MIN_IDLE)

        _simulate_run_ui(app)
        _pump(app, rounds=6)
        geometry_report.append(_assert_control_panel_geometry(app, label="min-run"))
        _grab_window(app, _OUT_MOCKUP_MIN_RUN)
        paths.append(_OUT_MOCKUP_MIN_RUN)
        _restore_idle_ui(app)
        _pump(app, rounds=4)

        app.geometry("1100x720+40+40")
        _pump(app)
        app._on_ctrl_panel_configure()
        _pump(app, rounds=4)
        geometry_report.append(_assert_control_panel_geometry(app, label="wide-idle"))
        _grab_window(app, _OUT_MAIN_1100)
        paths.append(_OUT_MAIN_1100)
        _grab_window(app, _OUT_MOCKUP_1100_IDLE)
        paths.append(_OUT_MOCKUP_1100_IDLE)

        _simulate_run_ui(app)
        _pump(app, rounds=6)
        geometry_report.append(_assert_control_panel_geometry(app, label="wide-run"))
        _grab_window(app, _OUT_MOCKUP_1100_RUN)
        paths.append(_OUT_MOCKUP_1100_RUN)
        _restore_idle_ui(app)

        dialog = app_mod.AppSettingsDialog(
            app,
            minimize_to_tray=True,
            run_on_startup=False,
            on_tray_changed=lambda _v: None,
            on_startup_changed=lambda _v: True,
            on_reset_launch_records=lambda: (True, "settings.reset.ok"),
        )
        dialog.attributes("-topmost", True)
        _pump(app)
        _grab_window(dialog, _OUT_SETTINGS)
        paths.append(_OUT_SETTINGS)
        dialog.destroy()

        _OUT_GEOMETRY_JSON.write_text(
            json.dumps({"ok": True, "measurements": geometry_report}, indent=2),
            encoding="utf-8",
        )
        paths.append(_OUT_GEOMETRY_JSON)
    except Exception as exc:
        print(f"screenshot_failed: {exc}", file=sys.stderr)
        try:
            app.destroy()
        except Exception:
            pass
        return 1

    for path in paths:
        print(path)

    try:
        app.quit_app()
    except Exception:
        try:
            app.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
