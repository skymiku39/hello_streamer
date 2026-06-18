"""Tests for MonitorController lifecycle and mode state machine.

The real ``Monitor`` is replaced with a recording fake so these run fast and
without spawning polling threads.
"""

from __future__ import annotations

import pytest

from stream_monitor import monitor_controller
from stream_monitor.events.types import PollWaiting
from stream_monitor.monitor_controller import MonitorController


class _FakeMonitor:
    def __init__(
        self,
        *,
        channels,
        interval,
        event_bus,
        db,
        initial_statuses=None,
        last_activity_epoch=0.0,
    ) -> None:
        self.channels = channels
        self.interval = interval
        self.initial_statuses = initial_statuses
        self.last_activity_epoch = last_activity_epoch
        self.event_bus = event_bus
        self.db = db
        self.is_running = False
        self.wake_verify_active = False
        self.started = 0
        self.request_stops = 0
        self.restarts = 0
        self.updated_channels: list = []
        self.updated_intervals: list = []

    def start(self) -> None:
        self.is_running = True
        self.started += 1

    def stop(self) -> None:
        self.is_running = False

    def request_stop(self) -> None:
        self.request_stops += 1

    def restart_thread(self) -> None:
        self.is_running = True
        self.restarts += 1

    def update_channels(self, channels) -> None:
        self.updated_channels.append(channels)

    def update_interval(self, interval) -> None:
        self.updated_intervals.append(interval)

    def snapshot_display_names(self) -> dict[str, str]:
        return {"twitch:a": "A"}


@pytest.fixture
def controller(monkeypatch):
    created: list[_FakeMonitor] = []

    def _factory(**kwargs):
        monitor = _FakeMonitor(**kwargs)
        created.append(monitor)
        return monitor

    monkeypatch.setattr(monitor_controller, "Monitor", _factory)
    ctrl = MonitorController(sink=object(), db=object())
    ctrl._created = created  # type: ignore[attr-defined]
    return ctrl


def test_start_without_channels_stays_idle(controller) -> None:
    assert controller.start("trigger", [], 60) is False
    assert controller.mode == "idle"
    assert controller.is_running is False
    assert controller._created == []


def test_start_trigger_creates_and_runs_monitor(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    assert controller.start("trigger", channels, 30) is True
    assert controller.mode == "trigger"
    assert controller.is_running is True
    assert len(controller._created) == 1
    monitor = controller._created[0]
    assert monitor.started == 1
    assert monitor.interval == 30
    assert monitor.event_bus is controller._bus


def test_start_again_reuses_running_monitor(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start("trigger", channels, 30)
    controller.start("watch", channels, 45)
    assert controller.mode == "watch"
    assert len(controller._created) == 1
    monitor = controller._created[0]
    assert monitor.updated_intervals[-1] == 45
    assert monitor.updated_channels[-1] == channels


def test_stop_resets_mode_and_signals_monitor(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start("trigger", channels, 30)
    monitor = controller._created[0]
    controller._bus.publish(PollWaiting())

    controller.stop()

    assert controller.mode == "idle"
    assert controller.is_running is False
    assert monitor.request_stops == 1
    assert controller._bus.drain() == []


def test_update_channels_only_when_running(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.update_channels(channels)  # no monitor yet -> no-op
    controller.start("trigger", channels, 30)
    monitor = controller._created[0]
    controller.update_channels(channels)
    assert monitor.updated_channels[-1] == channels


def test_restart_if_dead_recreates_when_thread_died(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start("trigger", channels, 30)
    controller._created[0].is_running = False  # simulate dead thread

    assert controller.restart_if_dead(channels, 30) is True
    assert controller.is_running is True


def test_restart_if_dead_noop_when_idle(controller) -> None:
    assert controller.restart_if_dead([{"platform": "twitch", "name": "a"}], 30) is False


def test_snapshot_display_names_passthrough(controller) -> None:
    assert controller.snapshot_display_names() == {}
    controller.start("trigger", [{"platform": "twitch", "name": "a"}], 30)
    assert controller.snapshot_display_names() == {"twitch:a": "A"}
