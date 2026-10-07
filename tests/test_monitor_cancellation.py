"""Exercise cancellation while probes are running or waiting for a worker slot."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from stream_monitor.events import MonitorEventBus
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import Monitor, deps, poll_cycle


def test_stopped_single_channel_pool_does_not_start_a_probe():
    monitor = Monitor(channels=[{"platform": "youtube", "name": "a"}], db=object())
    calls = []
    monitor.request_stop()

    assert monitor._run_priority_pool(monitor._entries, calls.append) == []
    assert calls == []


def test_stop_cancels_youtube_work_waiting_for_a_slot(monkeypatch):
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": name} for name in ("a", "b", "c")],
        max_concurrent=2,
        db=object(),
    )
    waiting = threading.Event()
    release = threading.Event()
    calls = []

    class ObservedCondition(threading.Condition):
        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    monkeypatch.setattr(
        poll_cycle, "threading",
        SimpleNamespace(Condition=ObservedCondition, Lock=threading.Lock, Thread=threading.Thread),
    )

    def work(entry):
        calls.append(entry.name)
        assert release.wait(2)

    worker = threading.Thread(target=monitor._run_priority_pool, args=(monitor._entries, work))
    worker.start()
    try:
        assert waiting.wait(2)
        monitor.request_stop()
    finally:
        release.set()
        worker.join(3)

    assert not worker.is_alive()
    assert calls == ["a"]


def test_inflight_live_response_does_not_publish_after_stop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    bus = MonitorEventBus()
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a"}], event_bus=bus, db=object(),
    )

    def get_stream_info(name):
        entered.set()
        assert release.wait(2)
        return StreamInfo(
            channel=name, platform="twitch", is_live=True,
            title="Late response", url=f"https://www.twitch.tv/{name}",
        )

    monkeypatch.setattr(deps, "get_fetcher", lambda _: SimpleNamespace(get_stream_info=get_stream_info))
    worker = threading.Thread(target=monitor._tier1_probe_entries, args=(monitor._entries,))
    worker.start()
    try:
        assert entered.wait(2)
        monitor.request_stop()
        bus.clear()
    finally:
        release.set()
        worker.join(3)

    assert not worker.is_alive()
    assert bus.drain() == []
