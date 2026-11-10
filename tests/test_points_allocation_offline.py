"""Offline channels must not keep or regain a signed-in Twitch points slot.

Regression for 2026-11-09/10: a channel's window was closed on its offline
edge and reopened about one second later, because the offline row update was
still queued behind the per-tick repaint cap while points allocation read the
row as live. The reopened page then held a slot for hours since offline rows
carry no active URL and the unallocated sweep skipped them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stream_monitor import app as app_module
from stream_monitor.app import App
from stream_monitor.domain import ChannelStatus
from stream_monitor.event_bridge import MonitorEventBridge
from stream_monitor.events import (
    ChannelWentOffline,
    MonitorEventBus,
    PollStatusUpdate,
)
from stream_monitor.monitor import ChannelEntry
from tests.test_event_bridge import _FakeRow, _RecordingSink


class _StatefulRow(_FakeRow):
    def __init__(self, key: str, status: Any) -> None:
        super().__init__(key)
        self.status = status

    def set_status(self, status: Any) -> None:
        super().set_status(status)
        self.status = status


def test_offline_edge_repaints_row_before_allocation_refresh() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger")
    live = ChannelStatus(status=True, url="https://www.twitch.tv/gone")
    target = _StatefulRow("twitch:gone", live)
    others = [
        _StatefulRow(f"twitch:c{i}", ChannelStatus(status=False)) for i in range(5)
    ]
    sink._channel_rows = [*others, target]
    seen: list[Any] = []
    sink.refresh_points_priority_allocation = (
        lambda **_kwargs: seen.append(target.status)
    )
    bridge = MonitorEventBridge(sink, bus)
    entry = ChannelEntry(platform="twitch", name="gone")
    bus.publish(
        ChannelWentOffline(
            entry=entry,
            offline_info=SimpleNamespace(url="https://www.twitch.tv/gone", title="t"),
        )
    )
    # Other channels flipping live take the capped repaint budget first.
    statuses: dict[str, Any] = {
        row.key: ChannelStatus(status=True, url="https://x") for row in others
    }
    statuses["twitch:gone"] = ChannelStatus(status=False, ended_at_source="vod")
    bus.publish(PollStatusUpdate(statuses=statuses, display_names={}))

    bridge.tick()

    assert seen, "allocation must be refreshed after the completed poll"
    assert all(status.is_offline for status in seen)
    assert target.status.ended_at_source == "vod"


def test_offline_edge_without_snapshot_still_marks_row_offline() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger")
    target = _StatefulRow(
        "twitch:gone", ChannelStatus(status=True, url="https://www.twitch.tv/gone")
    )
    sink._channel_rows = [target]
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        ChannelWentOffline(
            entry=ChannelEntry(platform="twitch", name="gone"),
            offline_info=SimpleNamespace(url="https://www.twitch.tv/gone"),
        )
    )

    bridge.tick()

    assert target.status.is_offline


def test_offline_edge_waits_for_repaints_then_applies_first() -> None:
    bus = MonitorEventBus()
    sink = _RecordingSink(mode="trigger")
    sink.defer_channel_row_repaints = True
    target = _StatefulRow(
        "twitch:gone", ChannelStatus(status=True, url="https://www.twitch.tv/gone")
    )
    others = [
        _StatefulRow(f"twitch:c{i}", ChannelStatus(status=False)) for i in range(5)
    ]
    sink._channel_rows = [*others, target]
    seen: list[Any] = []
    sink.refresh_points_priority_allocation = (
        lambda **_kwargs: seen.append(target.status)
    )
    bridge = MonitorEventBridge(sink, bus)
    bus.publish(
        ChannelWentOffline(
            entry=ChannelEntry(platform="twitch", name="gone"),
            offline_info=SimpleNamespace(url="https://www.twitch.tv/gone"),
        )
    )
    bus.publish(
        PollStatusUpdate(
            statuses={
                row.key: ChannelStatus(status=True, url="https://x") for row in others
            },
            display_names={},
        )
    )
    bridge.tick()
    assert target.status.is_live

    sink.defer_channel_row_repaints = False
    bridge.tick()

    assert seen and all(status.is_offline for status in seen)


def _allocation_probe(rows: list[Any], *, close_on_offline: bool) -> Any:
    return SimpleNamespace(
        config={
            "viewer_engagement": {
                "enabled": True,
                "points_priority_channels": ["kspksp"],
            },
            "trigger_settings": {},
            "channels": [
                {"platform": "twitch", "name": row.key.split(":", 1)[1]}
                for row in rows
            ],
        },
        _channel_rows=rows,
        _active_points_priority_channels=("kspksp", "jipandan"),
        _points_main_profile_channels={"kspksp", "jipandan", "hayabi_zr"},
        current_browser_settings=lambda: SimpleNamespace(
            close_on_offline=close_on_offline
        ),
        _controller=SimpleNamespace(mode="idle"),
    )


def _row(name: str, status: ChannelStatus) -> Any:
    return SimpleNamespace(
        key=f"twitch:{name}",
        verification_pending=False,
        status_snapshot=lambda: status,
    )


def _patch_windows(monkeypatch, open_urls: set[str]) -> list[str]:
    closed: list[str] = []
    monkeypatch.setattr(app_module, "set_active_points_channels", lambda _sel: None)
    monkeypatch.setattr(
        app_module, "managed_window_is_open", lambda url: url in open_urls
    )
    monkeypatch.setattr(app_module, "managed_window_launch_pending", lambda _url: False)
    monkeypatch.setattr(app_module, "close_browser_window_for_url", closed.append)
    return closed


def _rows() -> list[Any]:
    return [
        _row("kspksp", ChannelStatus(status=True, url="https://www.twitch.tv/kspksp")),
        _row(
            "jipandan", ChannelStatus(status=True, url="https://www.twitch.tv/jipandan")
        ),
        # Offline rows keep no active URL (ChannelRow.status_snapshot).
        _row("hayabi_zr", ChannelStatus(status=False, ended_at_source="vod")),
    ]


def test_allocation_closes_leftover_window_of_offline_channel(monkeypatch) -> None:
    closed = _patch_windows(
        monkeypatch,
        {
            "https://www.twitch.tv/kspksp",
            "https://www.twitch.tv/jipandan",
            "https://www.twitch.tv/hayabi_zr",
        },
    )
    probe = _allocation_probe(_rows(), close_on_offline=True)

    App.refresh_points_priority_allocation(probe)

    assert closed == ["https://www.twitch.tv/hayabi_zr"]
    assert "hayabi_zr" not in probe._points_main_profile_channels


def test_allocation_keeps_offline_window_when_close_on_offline_off(
    monkeypatch,
) -> None:
    closed = _patch_windows(monkeypatch, {"https://www.twitch.tv/hayabi_zr"})
    probe = _allocation_probe(_rows(), close_on_offline=False)

    App.refresh_points_priority_allocation(probe)

    assert closed == []


def _live(name: str) -> Any:
    return _row(name, ChannelStatus(status=True, url=f"https://www.twitch.tv/{name}"))


def _sticky_probe(rows: list[Any], previous: tuple[str, ...] | None) -> Any:
    probe = _allocation_probe(rows, close_on_offline=True)
    probe._active_points_priority_channels = previous
    probe._points_main_profile_channels = set(previous or ())
    return probe


def test_higher_ranked_channel_does_not_preempt_assigned_slot(monkeypatch) -> None:
    # 2026-11-09: enmei_suiin went live and cut off oldpot_o_0 mid-stream,
    # then daemoncasoul cut off enmei_suiin six minutes later.
    rows = [_live("daemoncasoul"), _live("hayabi_zr"), _live("oldpot_o_0")]
    open_urls = {
        "https://www.twitch.tv/hayabi_zr",
        "https://www.twitch.tv/oldpot_o_0",
    }
    closed = _patch_windows(monkeypatch, open_urls)
    probe = _sticky_probe(rows, ("hayabi_zr", "oldpot_o_0"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("hayabi_zr", "oldpot_o_0")
    assert closed == []


def test_freed_slot_is_filled_in_main_list_order(monkeypatch) -> None:
    rows = [
        _live("daemoncasoul"),
        _row("hayabi_zr", ChannelStatus(status=False, ended_at_source="vod")),
        _live("enmei_suiin"),
        _live("oldpot_o_0"),
    ]
    _patch_windows(monkeypatch, {"https://www.twitch.tv/oldpot_o_0"})
    probe = _sticky_probe(rows, ("hayabi_zr", "oldpot_o_0"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("oldpot_o_0", "daemoncasoul")


def test_live_priority_channel_still_takes_a_slot(monkeypatch) -> None:
    rows = [_live("hayabi_zr"), _live("gracya_leejia"), _live("kspksp")]
    closed = _patch_windows(
        monkeypatch,
        {
            "https://www.twitch.tv/hayabi_zr",
            "https://www.twitch.tv/gracya_leejia",
        },
    )
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("kspksp", "hayabi_zr")
    assert closed == ["https://www.twitch.tv/gracya_leejia"]


def test_cold_start_fills_slots_in_main_list_order(monkeypatch) -> None:
    rows = [_live("daemoncasoul"), _live("hayabi_zr"), _live("oldpot_o_0")]
    _patch_windows(monkeypatch, set())
    probe = _sticky_probe(rows, None)

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("daemoncasoul", "hayabi_zr")
