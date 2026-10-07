"""Keep tests using default Monitor/SeenVideoDB arguments out of user state."""

from __future__ import annotations

import pytest

from stream_monitor import db


@pytest.fixture(autouse=True)
def isolate_default_database(monkeypatch, tmp_path):
    """Use a separate database for each test, including implicit Monitor DBs."""
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "default_seen_videos.db")
