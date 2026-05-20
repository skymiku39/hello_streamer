"""SQLite 持久化層 — 追蹤已見過的 videoId 以防止重複通知。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stream_monitor import base_dir

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_videos (
    video_id   TEXT NOT NULL,
    platform   TEXT NOT NULL,
    channel    TEXT NOT NULL,
    style      TEXT NOT NULL,
    title      TEXT DEFAULT '',
    first_seen TEXT NOT NULL,
    PRIMARY KEY (video_id, style)
);
"""


def _style_key(style: str) -> str:
    return (style or "DEFAULT").strip().upper() or "DEFAULT"


def _db_path() -> Path:
    return base_dir() / "seen_videos.db"


class SeenVideoDB:
    """Thin wrapper around SQLite for tracking seen video IDs."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def _ensure_schema(self) -> None:
        if not self._table_exists("seen_videos"):
            self._conn.executescript(_SCHEMA)
            return

        if self._primary_key_columns("seen_videos") == ["video_id", "style"]:
            return

        self._migrate_to_composite_key()

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _primary_key_columns(self, table_name: str) -> list[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        pk_rows = sorted((row[5], row[1]) for row in rows if row[5])
        return [name for _index, name in pk_rows]

    def _migrate_to_composite_key(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        logger.info("Migrating seen_videos table to composite video_id/style key")
        self._conn.execute("BEGIN")
        try:
            self._conn.execute("ALTER TABLE seen_videos RENAME TO seen_videos_old")
            self._conn.execute(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO seen_videos "
                "(video_id, platform, channel, style, title, first_seen) "
                "SELECT video_id, "
                "COALESCE(NULLIF(platform, ''), 'youtube'), "
                "COALESCE(channel, ''), "
                "UPPER(COALESCE(NULLIF(style, ''), 'DEFAULT')), "
                "COALESCE(title, ''), "
                "COALESCE(NULLIF(first_seen, ''), ?) "
                "FROM seen_videos_old "
                "WHERE video_id IS NOT NULL AND video_id != ''",
                (now,),
            )
            self._conn.execute("DROP TABLE seen_videos_old")
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def is_seen(self, video_id: str, style: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_videos WHERE video_id = ? AND style = ?",
            (video_id, _style_key(style)),
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
            (video_id, platform, channel, _style_key(style), title, now),
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
