"""Tests for MonitorController lifecycle and mode state machine.

The real ``Monitor`` is replaced with a recording fake so these run fast and
without spawning polling threads.
"""

from __future__ import annotations

import threading

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
        self.live_rechecks = 0
        self.poll_cycle = 0
        self.updated_channels: list = []
        self.updated_intervals: list = []

    def start(self) -> None:
        self.is_running = True
        self.started += 1

    def request_live_recheck(self) -> None:
        self.live_rechecks += 1

    def stop(self, timeout=None) -> None:
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


@pytest.mark.parametrize("watch_mode", ["watch", "watch_once"])
@pytest.mark.parametrize("trigger_mode", ["trigger", "trigger_once"])
@pytest.mark.parametrize("stop_first", [False, True])
def test_watch_to_trigger_rechecks_live_after_observation(
    controller, watch_mode, trigger_mode, stop_first,
) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start(watch_mode, channels, 30)
    if stop_first:
        controller.stop()
    controller.start(trigger_mode, channels, 30)
    assert controller._created[-1].live_rechecks == 1
    controller.start(trigger_mode, channels, 30)
    assert controller._created[-1].live_rechecks == 1


def test_start_one_shot_sets_a_cycle_boundary_when_reusing_monitor(controller) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start("watch", channels, 30)
    monitor = controller._created[0]
    monitor.poll_cycle = 7

    controller.start("trigger_once", channels, 30)

    assert controller.mode == "trigger_once"
    assert controller._bridge._one_shot_after_cycle == 7


@pytest.mark.parametrize("mode", ["trigger_once", "watch_once"])
def test_finish_one_shot_stops_after_the_current_cycle(controller, mode) -> None:
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start(mode, channels, 30)
    monitor = controller._created[0]

    assert controller.finish_one_shot() in ("trigger", "watch")
    assert controller.mode == "idle"
    assert controller.is_running is False
    assert monitor.request_stops == 1
    assert controller.generation == 1


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


@pytest.mark.parametrize("mode", ["trigger", "watch", "trigger_once", "watch_once"])
def test_restart_discards_events_published_while_old_monitor_stops(controller, monkeypatch, mode):
    channels = [{"platform": "twitch", "name": "a"}]
    controller.start("watch", channels, 30)
    old_monitor = controller._created[0]
    release_stop = threading.Event()

    def late_stop(timeout=None):
        assert release_stop.wait(2)
        old_monitor.event_bus.publish(PollWaiting(cycle_id=99))
        old_monitor.is_running = False

    monkeypatch.setattr(old_monitor, "stop", late_stop)
    controller.stop()
    real_join = controller._join_stopping_thread

    def join_after_mode_switch(*, timeout):
        release_stop.set()
        real_join(timeout=timeout)

    def start_new(monitor):
        monitor.is_running = True
        monitor.event_bus.publish(PollWaiting(cycle_id=1))

    monkeypatch.setattr(controller, "_join_stopping_thread", join_after_mode_switch)
    monkeypatch.setattr(_FakeMonitor, "start", start_new)
    try:
        controller.start(mode, channels, 30)
        assert controller._bus.drain() == [PollWaiting(cycle_id=1)]
        if mode.endswith("_once"):
            assert controller._bridge._one_shot_after_cycle == 0
    finally:
        release_stop.set()
        controller.shutdown()
