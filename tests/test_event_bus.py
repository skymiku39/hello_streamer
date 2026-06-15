"""Tests for monitor publish/subscribe event bus."""

from __future__ import annotations

import threading

from stream_monitor.events import (
    ChannelWentLive,
    MonitorEventBus,
    PollWaiting,
)
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry


def test_bus_drain_returns_published_events_in_order() -> None:
    bus = MonitorEventBus()
    entry = ChannelEntry(platform="twitch", name="hello")
    info = StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live",
        url="https://www.twitch.tv/hello",
    )
    bus.publish(PollWaiting())
    bus.publish(ChannelWentLive(entry=entry, info=info))

    drained = bus.drain()
    assert len(drained) == 2
    assert isinstance(drained[0], PollWaiting)
    assert isinstance(drained[1], ChannelWentLive)
    assert drained[1].info.title == "Live"
    assert bus.drain() == []


def test_bus_clear_drops_pending_events() -> None:
    bus = MonitorEventBus()
    bus.publish(PollWaiting())
    bus.clear()
    assert bus.drain() == []


def test_bus_requeue_preserves_order() -> None:
    bus = MonitorEventBus()
    first = PollWaiting()
    second = PollWaiting()
    bus.publish(first)
    bus.publish(second)
    drained = bus.drain()
    bus.requeue(drained[1:])
    assert bus.drain() == [second]


def test_bus_subscribe_notifies_synchronously() -> None:
    bus = MonitorEventBus()
    seen: list[str] = []
    bus.subscribe(lambda event: seen.append(type(event).__name__))
    bus.publish(PollWaiting())
    assert seen == ["PollWaiting"]


def test_bus_publish_from_background_thread() -> None:
    bus = MonitorEventBus()
    done = threading.Event()

    def producer() -> None:
        bus.publish(PollWaiting())
        done.set()

    threading.Thread(target=producer, daemon=True).start()
    done.wait(timeout=2)
    assert len(bus.drain()) == 1
