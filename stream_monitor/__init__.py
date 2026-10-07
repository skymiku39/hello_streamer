"""開播監聯器 (Stream Monitor) — 監控實況主開播狀態的桌面應用程式。"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "1.3.0"


def base_dir() -> Path:
    """Return the portable base directory (next to the executable or project root)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def default_browser_profile_dir() -> str:
    """Return the portable browser-profile directory as a string."""
    try:
        from stream_monitor.portable_storage import portable_paths

        return str(portable_paths().browser_profile_dir)
    except Exception:  # noqa: BLE001 — never block callers on path resolution.
        return ""
