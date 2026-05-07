import sqlite3

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
