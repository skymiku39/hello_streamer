"""開播監聯器 (Stream Monitor) — 監控實況主開播狀態的桌面應用程式。"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.5.0"


def base_dir() -> Path:
    """Return the portable base directory (next to the executable or project root)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent
