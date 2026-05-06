"""SQLite 持久化層 — 追蹤已見過的 videoId 以防止重複通知。"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_videos (
    video_id   TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,
    channel    TEXT NOT NULL,
    style      TEXT NOT NULL,
    title      TEXT DEFAULT '',
    first_seen TEXT NOT NULL
);
"""


def _db_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "seen_videos.db"


class SeenVideoDB:
    """Thin wrapper around SQLite for tracking seen video IDs."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def is_seen(self, video_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None

    def mark_seen(
        self,
        video_id: str,
        platform: str,
        channel: str,
        style: str,
        title: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_videos "
            "(video_id, platform, channel, style, title, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, platform, channel, style, title, now),
        )
        self._conn.commit()

    def cleanup(self, days: int = 30) -> int:
        """Delete records older than *days*. Return number of rows removed."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM seen_videos WHERE first_seen < ?", (cutoff,)
        )
        self._conn.commit()
        removed = cursor.rowcount
        if removed:
            logger.info("Cleaned up %d old seen_videos records", removed)
        return removed
