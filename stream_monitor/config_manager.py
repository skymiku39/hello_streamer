"""config.json 讀寫與預設值管理。"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "channels": [],
    "check_interval": 60,
    "action": "open_and_stop",
    "run_on_startup": False,
    "window_geometry": None,
}

ACTION_CHOICES = [
    ("open_and_stop", "開啟網頁並停止監聽"),
    ("open_and_keep", "開啟網頁並保持監聯"),
    ("notify_only", "僅跳出系統通知"),
    ("open_and_exit", "開啟網頁後關閉程式"),
]


def _config_path() -> Path:
    """Return the path to config.json next to the executable / script."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "config.json"


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
    return config


def save(config: dict[str, Any]) -> None:
    """Persist config to disk."""
    path = _config_path()
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
