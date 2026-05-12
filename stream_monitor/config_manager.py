"""config.json 讀寫與預設值管理。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from stream_monitor import base_dir

DEFAULT_CONFIG: dict[str, Any] = {
    "channels": [],
    "check_interval": 60,
    "action": "open_and_stop",
    "run_on_startup": False,
    "minimize_to_tray": True,
    "window_geometry": None,
}

ACTION_CHOICES = [
    ("open_and_stop", "開啟網頁並停止監聽"),
    ("open_and_keep", "開啟網頁並保持監聽"),
    ("notify_only", "僅跳出系統通知"),
    ("open_and_exit", "開啟網頁後關閉程式"),
]
ACTION_KEYS = {key for key, _label in ACTION_CHOICES}
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


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_CONFIG)
    normalized["channels"] = _normalize_channels(config.get("channels"))
    normalized["check_interval"] = _normalize_interval(config.get("check_interval"))

    action = config.get("action")
    if isinstance(action, str) and action in ACTION_KEYS:
        normalized["action"] = action

    run_on_startup = config.get("run_on_startup")
    if isinstance(run_on_startup, bool):
        normalized["run_on_startup"] = run_on_startup

    minimize_to_tray = config.get("minimize_to_tray")
    if isinstance(minimize_to_tray, bool):
        normalized["minimize_to_tray"] = minimize_to_tray

    window_geometry = config.get("window_geometry")
    if isinstance(window_geometry, str) and window_geometry.strip():
        normalized["window_geometry"] = window_geometry

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
