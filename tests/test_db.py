import sqlite3
import threading
from pathlib import Path

import pytest

from stream_monitor.db import SeenVideoDB


def test_mark_seen_and_is_seen(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    assert db.is_seen("abc123", "UPCOMING") is False

    db.mark_seen("abc123", "youtube", "chan", "UPCOMING", "Waiting")
    assert db.is_seen("abc123", "UPCOMING") is True
    assert db.is_seen("abc123", "LIVE") is False

    db.mark_seen("abc123", "youtube", "chan", "LIVE", "Title")
    assert db.is_seen("abc123", "LIVE") is True
    db.close()


def test_database_can_be_used_as_a_context_manager(tmp_path) -> None:
    with SeenVideoDB(tmp_path / "context.db") as db:
        db.mark_seen("context", "youtube", "chan", "LIVE")
        assert db.is_seen("context", "LIVE") is True

    assert db._closed is True


def test_mark_seen_is_idempotent(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("abc", "youtube", "chan", "LIVE", "T1")
    db.mark_seen("abc", "youtube", "chan", "LIVE", "T2")
    assert db.is_seen("abc", "LIVE") is True
    rows = db._conn.execute(
        "SELECT COUNT(*) FROM seen_videos WHERE video_id = ? AND style = ?",
        ("abc", "LIVE"),
    ).fetchone()
    assert rows == (1,)
    db.close()


def test_migrates_old_video_id_primary_key_schema(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE seen_videos (
            video_id   TEXT PRIMARY KEY,
            platform   TEXT NOT NULL,
            channel    TEXT NOT NULL,
            style      TEXT NOT NULL,
            title      TEXT DEFAULT '',
            first_seen TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO seen_videos "
        "(video_id, platform, channel, style, title, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "same_vid",
            "youtube",
            "chan",
            "UPCOMING",
            "Waiting",
            "2026-05-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    db = SeenVideoDB(path)
    assert db.is_seen("same_vid", "UPCOMING") is True
    assert db.is_seen("same_vid", "LIVE") is False

    db.mark_seen("same_vid", "youtube", "chan", "LIVE", "Now Live")
    rows = db._conn.execute(
        "SELECT style FROM seen_videos WHERE video_id = ? ORDER BY style",
        ("same_vid",),
    ).fetchall()
    assert rows == [("LIVE",), ("UPCOMING",)]
    db.close()


def test_cleanup_removes_old_records(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("old_vid", "youtube", "chan", "DEFAULT", "Old")

    db._conn.execute(
        "UPDATE seen_videos SET first_seen = '2020-01-01T00:00:00+00:00' "
        "WHERE video_id = 'old_vid'"
    )
    db._conn.commit()

    db.mark_seen("new_vid", "youtube", "chan", "LIVE", "New")

    removed = db.cleanup(days=30)
    assert removed == 1
    assert db.is_seen("old_vid", "DEFAULT") is False
    assert db.is_seen("new_vid", "LIVE") is True
    db.close()


def test_cleanup_keeps_recent_records(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("recent", "youtube", "chan", "LIVE", "Recent")

    removed = db.cleanup(days=30)
    assert removed == 0
    assert db.is_seen("recent", "LIVE") is True
    db.close()


def test_cleanup_keeps_listed_video_ids(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("live_vid", "youtube", "chan", "LIVE", "Live")
    db.mark_seen("gone_vid", "youtube", "chan", "LIVE", "Gone")
    db._conn.execute(
        "UPDATE seen_videos SET first_seen = '2020-01-01T00:00:00+00:00'"
    )
    db._conn.commit()

    removed = db.cleanup(days=30, keep_video_ids=["live_vid"])
    assert removed == 1
    assert db.is_seen("live_vid", "LIVE") is True
    assert db.is_seen("gone_vid", "LIVE") is False
    db.close()


def test_concurrent_is_seen_and_mark_seen(tmp_path) -> None:
    """Parallel Monitor workers must not corrupt the shared SQLite connection."""
    db = SeenVideoDB(tmp_path / "test.db")
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            vid = f"vid{index % 4}"
            style = "LIVE" if index % 2 == 0 else "UPCOMING"
            if not db.is_seen(vid, style):
                db.mark_seen(vid, "youtube", "chan", style, f"t{index}")
            assert db.is_seen(vid, style)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    db.close()


def test_corrupt_database_is_renamed_and_recreated(tmp_path) -> None:
    path = tmp_path / "seen.db"
    garbage = b"this is definitely not a sqlite database" * 50
    path.write_bytes(garbage)

    db = SeenVideoDB(path)
    db.mark_seen("v1", "youtube", "chan", "LIVE")
    assert db.is_seen("v1", "LIVE") is True
    db.close()

    backups = list(tmp_path.glob("seen.db.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == garbage


def test_corrupt_database_wal_and_shm_are_renamed_too(tmp_path) -> None:
    path = tmp_path / "seen.db"
    path.write_bytes(b"garbage" * 100)
    (tmp_path / "seen.db-wal").write_bytes(b"wal-bytes")
    (tmp_path / "seen.db-shm").write_bytes(b"shm-bytes")

    SeenVideoDB(path).close()

    wal = list(tmp_path.glob("seen.db-wal.corrupt-*"))
    shm = list(tmp_path.glob("seen.db-shm.corrupt-*"))
    assert len(wal) == 1 and wal[0].read_bytes() == b"wal-bytes"
    assert len(shm) == 1  # SQLite may rewrite the shm contents


def test_locked_database_is_not_renamed(tmp_path, monkeypatch) -> None:
    path = tmp_path / "seen.db"
    path.write_bytes(b"pretend this is in use")
    real_connect = sqlite3.connect

    class _LockedConn:
        def __init__(self, conn) -> None:
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("PRAGMA journal_mode"):
                raise sqlite3.OperationalError("database is locked")
            return self._conn.execute(sql, *args)

        def close(self) -> None:
            self._conn.close()

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: _LockedConn(real_connect(*a, **k))
    )

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        SeenVideoDB(path)

    assert path.read_bytes() == b"pretend this is in use"
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_rename_failure_re_raises_original_error(tmp_path, monkeypatch) -> None:
    path = tmp_path / "seen.db"
    path.write_bytes(b"garbage" * 100)

    def _boom(self, target):
        raise OSError("denied")

    monkeypatch.setattr(Path, "replace", _boom)

    with pytest.raises(sqlite3.DatabaseError):
        SeenVideoDB(path)


def test_healthy_database_keeps_data_after_reopen(tmp_path) -> None:
    path = tmp_path / "seen.db"
    db = SeenVideoDB(path)
    db.mark_seen("keep", "youtube", "chan", "LIVE")
    db.close()

    with SeenVideoDB(path) as reopened:
        assert reopened.is_seen("keep", "LIVE") is True
    assert list(tmp_path.glob("*.corrupt-*")) == []
