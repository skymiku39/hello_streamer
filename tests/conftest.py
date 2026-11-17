"""Keep tests using default Monitor/SeenVideoDB arguments out of user state."""

from __future__ import annotations

import platform

import pytest

from stream_monitor import db

# On Windows the first platform.system() call may run ``ver`` in a subprocess.
# Tests that replace subprocess.Popen with a stub would then fail only when run
# alone, before anything else filled the cache; fill it up front.
platform.uname()


@pytest.fixture(autouse=True)
def isolate_default_database(monkeypatch, tmp_path):
    """Use a separate database for each test, including implicit Monitor DBs."""
    monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "default_seen_videos.db")


@pytest.fixture(autouse=True)
def drop_pending_cdp_attach_retries():
    """Launch tests with failed discovery must not leave 30 s timers armed."""
    yield
    from stream_monitor import notifier

    notifier.cancel_cdp_attach_retries()
