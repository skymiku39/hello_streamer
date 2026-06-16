"""Tests for the narrow ProbeFacade boundary (PR6)."""

from __future__ import annotations

import threading

from stream_monitor.db import SeenVideoDB
from stream_monitor.monitor.core import Monitor
from stream_monitor.monitor.probes import ProbeFacade, ProbeSession


def _make_monitor(tmp_path) -> Monitor:
    db = SeenVideoDB(tmp_path / "facade.db")
    return Monitor(channels=[{"platform": "twitch", "name": "ch"}], db=db)


def test_facade_session_is_monitor_session(tmp_path) -> None:
    monitor = _make_monitor(tmp_path)
    facade = monitor._facade

    assert isinstance(facade, ProbeFacade)
    assert isinstance(facade.session, ProbeSession)
    assert facade.session is monitor._session


def test_monitor_proxies_resolve_to_session(tmp_path) -> None:
    """Mixin code reads ``self._last_status``; it must be the session dict."""
    monitor = _make_monitor(tmp_path)

    assert monitor._last_status is monitor._session.last_status
    assert monitor._lock is monitor._session.lock

    monitor._session.last_status["ch:twitch"] = "live"
    assert monitor._last_status["ch:twitch"] == "live"


def test_facade_exposes_db_and_commit(tmp_path) -> None:
    monitor = _make_monitor(tmp_path)
    facade = monitor._facade

    assert facade.db is monitor._db
    assert facade.noop_commit() is None
    assert facade.wake_verify_mode is monitor._wake_verify_mode


def test_facade_publish_preview_delegates(tmp_path, monkeypatch) -> None:
    monitor = _make_monitor(tmp_path)
    calls: list[bool] = []
    monkeypatch.setattr(
        monitor,
        "_publish_channel_preview",
        lambda entry, *, from_probe=False: calls.append(from_probe),
    )

    monitor._facade.publish_preview(monitor._entries[0], from_probe=True)
    assert calls == [True]


def test_concurrent_status_writes_are_lock_safe(tmp_path) -> None:
    """Writers through the session lock never corrupt snapshot reads."""
    monitor = _make_monitor(tmp_path)
    session = monitor._facade.session
    errors: list[BaseException] = []

    def writer(start: int) -> None:
        try:
            for i in range(start, start + 200):
                with session.lock:
                    session.last_status[f"k{i}"] = i
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(200):
                monitor.snapshot_statuses()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(0,)),
        threading.Thread(target=writer, args=(1000,)),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert len(monitor._last_status) == 400
