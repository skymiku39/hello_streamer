"""Runtime check: local account + app mode + custom geometry.

Run while Chrome is already open (typical case):
  uv run python scripts/verify_local_app_geometry.py
"""

from __future__ import annotations

import time

from stream_monitor.browser_win32 import (
    _WIN32_WINDOW_CLASS_BY_FAMILY,
    _enum_browser_hwnds,
    _get_window_title,
    _is_windows,
)
from stream_monitor.notifier import open_url

TARGET = {"x": 120, "y": 120, "width": 420, "height": 280}

SETTINGS = {
    "enabled": True,
    "browser_path": "chrome",
    "new_window": True,
    "app_mode": True,
    "apply_geometry": True,
    **TARGET,
    "user_data_dir": "",
    "per_channel_profile": False,
    "minimized": False,
    "close_on_offline": False,
    "close_on_stop": False,
    "close_off_topic_pages": False,
    "hide_from_taskbar": False,
}


def _snapshot_chrome_windows() -> list[dict]:
    if not _is_windows():
        return []
    import ctypes

    user32 = ctypes.windll.user32
    class_name = _WIN32_WINDOW_CLASS_BY_FAMILY["chromium"]
    hwnds = _enum_browser_hwnds(class_name)
    out: list[dict] = []
    for hwnd in hwnds:
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append(
            {
                "hwnd": int(hwnd),
                "title": _get_window_title(user32, hwnd),
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
        )
    return out


def main() -> None:
    before = _snapshot_chrome_windows()
    open_url("https://www.twitch.tv/", SETTINGS)
    time.sleep(5)
    after = _snapshot_chrome_windows()
    matching = [
        w
        for w in after
        if abs(w["width"] - TARGET["width"]) <= 8
        and abs(w["height"] - TARGET["height"]) <= 8
    ]
    print("Target:", TARGET)
    print("Before windows:", len(before))
    print("After windows:", len(after))
    print("Matching target:", matching)


if __name__ == "__main__":
    main()
