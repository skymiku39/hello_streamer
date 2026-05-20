"""config.json 讀寫與預設值管理。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from stream_monitor import base_dir, i18n

DEFAULT_BROWSER_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "browser_path": "chrome",
    "new_window": True,
    "app_mode": False,
    # When False the X/Y/Width/Height fields are ignored entirely and the
    # browser decides where/how big to draw the window (system default).
    "apply_geometry": True,
    "x": 0,
    "y": 0,
    "width": 1280,
    "height": 720,
    "minimized": False,
    # Empty = use browser's default profile (subject to Chrome master-process
    # restrictions). Set to a folder path to force a dedicated Chrome /
    # Firefox profile, which is the only reliable way to make
    # --window-position / --window-size / --app= work when the browser is
    # already running.
    "user_data_dir": "",
    # When True (default) we append "<platform>_<channel>" to user_data_dir
    # so each channel gets its own browser master process. This is the only
    # reliable way to keep --app= working across multiple stream triggers,
    # because Chrome's master process drops --app= when launched against a
    # profile that already has an open window.
    "per_channel_profile": True,
    # When True, the app closes (PostMessage WM_CLOSE) the browser window we
    # opened on the going-live edge once the channel transitions back to
    # offline. Default off so users opt-in deliberately.
    "close_on_offline": False,
    # When True, the post-launch Win32 worker flips WS_EX_TOOLWINDOW on the
    # spawned browser window so it disappears from the taskbar and Alt+Tab.
    # Off by default — most users still want a taskbar slot to switch to.
    "hide_from_taskbar": False,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "channels": [],
    "check_interval": 60,
    "action": "open_and_stop",
    "monitor_mode": "trigger",
    "run_on_startup": False,
    "minimize_to_tray": True,
    "window_geometry": None,
    "language": i18n.DEFAULT_LANGUAGE,
    "browser_settings": deepcopy(DEFAULT_BROWSER_SETTINGS),
}

ACTION_CHOICES = [
    ("open_and_stop", "開啟網頁並停止監聽"),
    ("open_and_keep", "開啟網頁並保持監聽"),
    ("notify_only", "僅跳出系統通知"),
    ("open_and_exit", "開啟網頁後關閉程式"),
]
ACTION_KEYS = {key for key, _label in ACTION_CHOICES}
MONITOR_MODE_KEYS = {"trigger", "watch"}
PLATFORM_KEYS = {"twitch", "youtube"}
MIN_CHECK_INTERVAL = 10


def _config_path():
    """Return the path to config.json next to the executable / script."""
    return base_dir() / "config.json"


def _normalize_channels(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    channels: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        platform = item.get("platform")
        name = item.get("name")
        if not isinstance(platform, str) or not isinstance(name, str):
            continue

        platform = platform.lower().strip()
        name = name.strip()
        if platform in PLATFORM_KEYS and name:
            normalized = {"platform": platform, "name": name}
            display_name = item.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                normalized["display_name"] = display_name.strip()
            enabled = item.get("enabled")
            if isinstance(enabled, bool):
                normalized["enabled"] = enabled
            channels.append(normalized)

    return channels


def _normalize_interval(value: Any) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["check_interval"]
    return max(MIN_CHECK_INTERVAL, interval)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_browser_settings(value: Any) -> dict[str, Any]:
    defaults = deepcopy(DEFAULT_BROWSER_SETTINGS)
    if not isinstance(value, dict):
        return defaults

    normalized = deepcopy(defaults)

    for bool_key in (
        "enabled",
        "new_window",
        "app_mode",
        "apply_geometry",
        "minimized",
        "per_channel_profile",
        "close_on_offline",
        "hide_from_taskbar",
    ):
        raw = value.get(bool_key)
        if isinstance(raw, bool):
            normalized[bool_key] = raw

    browser_path = value.get("browser_path")
    if isinstance(browser_path, str) and browser_path.strip():
        normalized["browser_path"] = browser_path.strip()

    user_data_dir = value.get("user_data_dir")
    if isinstance(user_data_dir, str):
        normalized["user_data_dir"] = user_data_dir.strip()

    for int_key in ("x", "y", "width", "height"):
        normalized[int_key] = _coerce_int(value.get(int_key), normalized[int_key])

    normalized["width"] = max(100, normalized["width"])
    normalized["height"] = max(100, normalized["height"])

    return normalized


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_CONFIG)
    normalized["channels"] = _normalize_channels(config.get("channels"))
    normalized["check_interval"] = _normalize_interval(config.get("check_interval"))

    action = config.get("action")
    if isinstance(action, str) and action in ACTION_KEYS:
        normalized["action"] = action

    monitor_mode = config.get("monitor_mode")
    if isinstance(monitor_mode, str) and monitor_mode in MONITOR_MODE_KEYS:
        normalized["monitor_mode"] = monitor_mode

    run_on_startup = config.get("run_on_startup")
    if isinstance(run_on_startup, bool):
        normalized["run_on_startup"] = run_on_startup

    minimize_to_tray = config.get("minimize_to_tray")
    if isinstance(minimize_to_tray, bool):
        normalized["minimize_to_tray"] = minimize_to_tray

    window_geometry = config.get("window_geometry")
    if isinstance(window_geometry, str) and window_geometry.strip():
        normalized["window_geometry"] = window_geometry

    language = config.get("language")
    if isinstance(language, str) and language in i18n.LANGUAGE_CODES:
        normalized["language"] = language
    else:
        normalized["language"] = i18n.DEFAULT_LANGUAGE

    normalized["browser_settings"] = _normalize_browser_settings(
        config.get("browser_settings")
    )

    return normalized


def load() -> dict[str, Any]:
    """Load config from disk, falling back to defaults for missing keys."""
    path = _config_path()
    config = deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return _normalize_config(config)


def save(config: dict[str, Any]) -> None:
    """Persist config to disk."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    normalized = _normalize_config(config)
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp_path.replace(path)
