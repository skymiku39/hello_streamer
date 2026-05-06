import time

from stream_monitor.db import SeenVideoDB


def test_mark_seen_and_is_seen(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    assert db.is_seen("abc123") is False

    db.mark_seen("abc123", "youtube", "chan", "LIVE", "Title")
    assert db.is_seen("abc123") is True
    db.close()


def test_mark_seen_is_idempotent(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("abc", "youtube", "chan", "LIVE", "T1")
    db.mark_seen("abc", "youtube", "chan", "LIVE", "T2")
    assert db.is_seen("abc") is True
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
    assert db.is_seen("old_vid") is False
    assert db.is_seen("new_vid") is True
    db.close()


def test_cleanup_keeps_recent_records(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("recent", "youtube", "chan", "LIVE", "Recent")

    removed = db.cleanup(days=30)
    assert removed == 0
    assert db.is_seen("recent") is True
    db.close()
