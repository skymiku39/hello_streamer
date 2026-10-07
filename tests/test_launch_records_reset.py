"""Safe launch-records reset: preservation, backup, persistence, and guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stream_monitor import config_manager
from stream_monitor.db import SeenVideoDB
from stream_monitor.launch_records import (
    evaluate_reset_guard,
    reset_launch_records,
)
from stream_monitor.portable_storage import PortablePaths
from stream_monitor.status_cache import CACHE_CONFIG_KEY


def test_database_commit_failure_rolls_back_deleted_records(tmp_path: Path) -> None:
    db = SeenVideoDB(tmp_path / "seen.db")
    db.mark_seen("keep", "twitch", "channel", "LIVE")
    connection = db._conn

    class FailingCommit:
        def __getattr__(self, name):
            return getattr(connection, name)

        def commit(self):
            raise OSError("simulated database commit failure")

    db._conn = FailingCommit()
    try:
        with pytest.raises(OSError, match="commit failure"):
            db.clear_dedupe_table()
        assert not connection.in_transaction
        assert db.is_seen("keep", "LIVE")
    finally:
        db._conn = connection
        db.close()


class _FakeController:
    def __init__(self, *, mode: str = "idle", running: bool = False, settled: bool = True):
        self.mode = mode
        self.is_running = running
        self._settled = settled
        self.purge_calls = 0

    def is_settled_idle(self) -> bool:
        return self._settled

    def purge_pending_for_reset(self) -> None:
        self.purge_calls += 1


class _FakeCoordinator:
    def __init__(self, active: bool = False):
        self._active = active

    def has_active_work(self) -> bool:
        return self._active


def _seed_config() -> dict:
    return {
        "channels": [
            {"platform": "twitch", "name": "keep_me", "enabled": True},
            {
                "platform": "youtube",
                "name": "UC_long_channel_identity_for_layout",
                "enabled": True,
            },
        ],
        "browser_settings": {"enabled": True, "browser_path": "chrome"},
        "language": "en",
        "run_on_startup": False,
        CACHE_CONFIG_KEY: {
            "saved_at": "2026-01-01T00:00:00+00:00",
            "channels": {
                "twitch:keep_me": {"state": "live", "title": "Old"},
            },
        },
    }


def test_reset_guard_blocks_when_monitor_active() -> None:
    guard = evaluate_reset_guard(
        mode="trigger",
        is_running=True,
        is_settled_idle=False,
        has_active_work=False,
    )
    assert guard.ok is False
    assert guard.reason_key == "settings.reset.busy"


def test_reset_guard_blocks_when_background_work_active() -> None:
    guard = evaluate_reset_guard(
        mode="idle",
        is_running=False,
        is_settled_idle=True,
        has_active_work=True,
    )
    assert guard.ok is False
    assert guard.reason_key == "settings.reset.busy"


def test_reset_clears_seen_and_status_cache_but_preserves_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "app"
    root.mkdir()
    paths = PortablePaths(root)
    config_path = paths.config_file
    monkeypatch.setattr(config_manager, "_config_path", lambda: config_path)

    db = SeenVideoDB(paths.database_file)
    db.mark_seen("vid1", "youtube", "chan", "LIVE", "Title")
    db.mark_seen("vid2", "twitch", "chan", "LIVE", "Live")
    assert db.count_seen() == 2

    config = _seed_config()
    config_manager.save(config)
    assert CACHE_CONFIG_KEY in json.loads(config_path.read_text(encoding="utf-8"))

    after_guard_calls: list[str] = []

    def after_guard() -> None:
        after_guard_calls.append("purged")

    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(),
        coordinator=_FakeCoordinator(False),
        persist_config=config_manager.save,
        after_guard=after_guard,
    )
    assert result.ok is True
    assert after_guard_calls == ["purged"]
    assert result.backup_dir is not None
    assert result.cleared_rows == 2
    assert db.count_seen() == 0
    assert db.is_seen("vid1", "LIVE") is False
    # Open connection remains usable after clear.
    db.mark_seen("fresh", "youtube", "chan", "LIVE", "Fresh")
    assert db.is_seen("fresh", "LIVE") is True

    assert CACHE_CONFIG_KEY not in config
    assert config["channels"][0]["name"] == "keep_me"
    assert config["browser_settings"]["enabled"] is True
    assert config["language"] == "en"

    disk = json.loads(config_path.read_text(encoding="utf-8"))
    # save() rehydrates DEFAULT empty cache {}; must not retain old channel rows.
    disk_cache = disk.get(CACHE_CONFIG_KEY) or {}
    assert not disk_cache.get("channels")
    assert disk["channels"][0]["name"] == "keep_me"
    assert disk["browser_settings"]["enabled"] is True
    assert paths.browser_profile_dir.resolve() not in (
        result.backup_dir / "seen_videos.db"
    ).resolve().parents

    backup_db = result.backup_dir / "seen_videos.db"
    backup_cache = result.backup_dir / "channel_status_cache.json"
    assert backup_db.is_file()
    assert backup_cache.is_file()
    cached = json.loads(backup_cache.read_text(encoding="utf-8"))
    assert "twitch:keep_me" in cached["channels"]
    db.close()


def test_reset_refuses_when_busy_without_mutating(tmp_path: Path) -> None:
    root = tmp_path / "busy"
    root.mkdir()
    paths = PortablePaths(root)
    db = SeenVideoDB(paths.database_file)
    db.mark_seen("vid", "youtube", "chan", "LIVE", "T")
    config = {
        "channels": [{"platform": "youtube", "name": "chan"}],
        CACHE_CONFIG_KEY: {"saved_at": "x", "channels": {}},
    }

    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(mode="watch", running=True, settled=False),
        coordinator=_FakeCoordinator(False),
    )
    assert result.ok is False
    assert result.error_key == "settings.reset.busy"
    assert db.count_seen() == 1
    assert CACHE_CONFIG_KEY in config
    assert not paths.backups_dir.exists()
    db.close()


def test_reset_failure_does_not_claim_partial_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fail"
    root.mkdir()
    paths = PortablePaths(root)
    db = SeenVideoDB(paths.database_file)
    db.mark_seen("vid", "youtube", "chan", "LIVE", "T")
    config = {CACHE_CONFIG_KEY: {"saved_at": "x", "channels": {}}}

    def boom(_dest: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(db, "backup_to", boom)
    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(),
        coordinator=_FakeCoordinator(False),
    )
    assert result.ok is False
    assert result.error_key == "settings.reset.fail"
    assert db.count_seen() == 1
    assert CACHE_CONFIG_KEY in config
    db.close()


def test_config_persist_failure_does_not_clear_db_or_claim_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "persist_fail"
    root.mkdir()
    paths = PortablePaths(root)
    config_path = paths.config_file
    monkeypatch.setattr(config_manager, "_config_path", lambda: config_path)

    db = SeenVideoDB(paths.database_file)
    db.mark_seen("vid", "youtube", "chan", "LIVE", "T")
    config = _seed_config()
    config_manager.save(config)
    original_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert CACHE_CONFIG_KEY in original_disk

    def boom(_config: dict) -> dict:
        raise OSError("config locked")

    row_clears: list[str] = []

    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(),
        coordinator=_FakeCoordinator(False),
        persist_config=boom,
    )
    assert result.ok is False
    assert result.error_key == "settings.reset.fail"
    assert db.count_seen() == 1
    assert CACHE_CONFIG_KEY in config
    assert config[CACHE_CONFIG_KEY]["channels"]["twitch:keep_me"]["state"] == "live"
    # Disk must still hold the previous cache — no false success / half-reset.
    disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert CACHE_CONFIG_KEY in disk
    assert disk["channels"][0]["name"] == "keep_me"
    assert disk["browser_settings"]["enabled"] is True
    assert row_clears == []
    db.close()


def test_db_clear_failure_restores_cache_and_repersists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "db_fail"
    root.mkdir()
    paths = PortablePaths(root)
    config_path = paths.config_file
    monkeypatch.setattr(config_manager, "_config_path", lambda: config_path)

    db = SeenVideoDB(paths.database_file)
    db.mark_seen("vid", "youtube", "chan", "LIVE", "T")
    config = _seed_config()
    config_manager.save(config)

    def boom_clear() -> int:
        raise OSError("sqlite locked")

    monkeypatch.setattr(db, "clear_dedupe_table", boom_clear)

    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(),
        coordinator=_FakeCoordinator(False),
        persist_config=config_manager.save,
    )
    assert result.ok is False
    assert result.error_key == "settings.reset.fail"
    assert CACHE_CONFIG_KEY in config
    assert config[CACHE_CONFIG_KEY]["channels"]["twitch:keep_me"]["title"] == "Old"
    disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert CACHE_CONFIG_KEY in disk
    assert disk["channels"][0]["name"] == "keep_me"
    # Backup connection path: original open DB handle still usable for reads.
    assert db.count_seen() == 1
    db.mark_seen("after", "twitch", "chan", "LIVE", "After")
    assert db.is_seen("after", "LIVE") is True
    db.close()


def test_after_guard_runs_only_when_idle_and_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "guard_order"
    root.mkdir()
    paths = PortablePaths(root)
    config_path = paths.config_file
    monkeypatch.setattr(config_manager, "_config_path", lambda: config_path)
    db = SeenVideoDB(paths.database_file)
    config = _seed_config()
    order: list[str] = []

    def after_guard() -> None:
        order.append("after_guard")
        assert CACHE_CONFIG_KEY in config

    def persist(cfg: dict) -> dict:
        order.append("persist")
        assert CACHE_CONFIG_KEY not in cfg
        return config_manager.save(cfg)

    db.mark_seen("vid", "youtube", "chan", "LIVE", "T")
    result = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(),
        coordinator=_FakeCoordinator(False),
        persist_config=persist,
        after_guard=after_guard,
    )
    assert result.ok is True
    assert order == ["after_guard", "persist"]

    busy = reset_launch_records(
        db=db,
        config=config,
        paths=paths,
        controller=_FakeController(mode="trigger", running=True, settled=False),
        coordinator=_FakeCoordinator(False),
        persist_config=persist,
        after_guard=lambda: order.append("should_not_run"),
    )
    assert busy.ok is False
    assert "should_not_run" not in order
    db.close()
