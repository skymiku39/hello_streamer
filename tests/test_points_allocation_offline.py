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

import pytest

from stream_monitor import app as app_module
from stream_monitor import claim_registry
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


@pytest.fixture(autouse=True)
def _fresh_claim_registry():
    claim_registry.reset_for_tests()
    yield
    claim_registry.reset_for_tests()


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
    claim_registry.reset_for_tests()
    claim_registry.record_confirmed_claim("https://www.twitch.tv/hayabi_zr")
    claim_registry.record_confirmed_claim("https://www.twitch.tv/gracya_leejia")
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


def _serve(*names: str) -> None:
    claim_registry.reset_for_tests()
    for name in names:
        claim_registry.record_confirmed_claim(f"https://www.twitch.tv/{name}")


def test_unclaimed_holders_keep_slots_against_priority_channel(monkeypatch) -> None:
    _serve()
    rows = [_live("hayabi_zr"), _live("gracya_leejia"), _live("kspksp")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/gracya_leejia"},
    )
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("hayabi_zr", "gracya_leejia")
    assert closed == []


def test_claimed_holder_yields_slot_to_unclaimed_live_channel(monkeypatch) -> None:
    # 2026-11-10: three live channels (daemoncasoul, oldpot_o_0, ramengyozaset)
    # got no claim all night while other channels held both slots.
    _serve("gracya_leejia")
    rows = [
        _live("daemoncasoul"),
        _live("gracya_leejia"),
        _live("hanasakich"),
        _live("ramengyozaset"),
    ]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/gracya_leejia", "https://www.twitch.tv/hanasakich"},
    )
    probe = _sticky_probe(rows, ("gracya_leejia", "hanasakich"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("hanasakich", "daemoncasoul")
    assert closed == ["https://www.twitch.tv/gracya_leejia"]


def test_unclaimed_channels_are_filled_in_list_order(monkeypatch) -> None:
    _serve("hayabi_zr", "gracya_leejia")
    rows = [
        _live("hayabi_zr"),
        _live("gracya_leejia"),
        _live("oldpot_o_0"),
        _live("daemoncasoul"),
    ]
    _patch_windows(monkeypatch, set())
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))

    App.refresh_points_priority_allocation(probe)

    # Both unclaimed channels outrank the claimed holders; list order decides.
    assert set(probe._active_points_priority_channels) == {"oldpot_o_0", "daemoncasoul"}


def test_claimed_holders_stay_when_nobody_waits(monkeypatch) -> None:
    _serve("hayabi_zr", "gracya_leejia")
    rows = [_live("hayabi_zr"), _live("gracya_leejia")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/gracya_leejia"},
    )
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("hayabi_zr", "gracya_leejia")
    assert closed == []


def test_lowest_ranked_claimed_holder_is_the_one_that_yields(monkeypatch) -> None:
    # 2026-11-12 17:37: 4lianyun went live and daemoncasoul (first in the
    # main list) lost its slot instead of hayabi_zr (sixth), because the
    # holder tuple happened to list hayabi_zr first.
    _serve("daemoncasoul", "hayabi_zr")
    rows = [_live("daemoncasoul"), _live("4lianyun"), _live("hayabi_zr")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/daemoncasoul", "https://www.twitch.tv/hayabi_zr"},
    )
    probe = _sticky_probe(rows, ("hayabi_zr", "daemoncasoul"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("4lianyun", "daemoncasoul")
    assert closed == ["https://www.twitch.tv/hayabi_zr"]


def test_higher_ranked_channel_returns_once_everyone_claimed(monkeypatch) -> None:
    # 2026-11-12 17:45: after 4lianyun claimed, daemoncasoul stayed closed
    # while the two lower-ranked channels kept both slots.
    _serve("daemoncasoul", "4lianyun", "hayabi_zr")
    rows = [_live("daemoncasoul"), _live("4lianyun"), _live("hayabi_zr")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/4lianyun", "https://www.twitch.tv/hayabi_zr"},
    )
    probe = _sticky_probe(rows, ("4lianyun", "hayabi_zr"))

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("daemoncasoul", "4lianyun")
    assert closed == ["https://www.twitch.tv/hayabi_zr"]


def test_offline_channel_forgets_its_claim(monkeypatch) -> None:
    _serve("hayabi_zr")
    rows = [_row("hayabi_zr", ChannelStatus(status=False, ended_at_source="vod"))]
    _patch_windows(monkeypatch, set())
    probe = _sticky_probe(rows, ("hayabi_zr",))

    App.refresh_points_priority_allocation(probe)

    assert not claim_registry.is_served("hayabi_zr")


def test_stalled_holders_get_windows_restarted_before_giving_up(monkeypatch) -> None:
    # 2026-11-11: page assist lost CDP at 16:31, so no points were visible.
    # hanasakich and jipandan held both slots all night and daemoncasoul
    # (live 21:30-02:12) was never watched. Time alone must not end a turn:
    # 20 minutes without points means something is broken, so restart first.
    _serve()
    rows = [_live("daemoncasoul"), _live("hanasakich"), _live("jipandan")]
    urls = {"https://www.twitch.tv/hanasakich", "https://www.twitch.tv/jipandan"}
    closed = _patch_windows(monkeypatch, urls)
    probe = _sticky_probe(rows, ("hanasakich", "jipandan"))
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])

    App.refresh_points_priority_allocation(probe)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS - 1
    App.refresh_points_priority_allocation(probe)
    assert closed == []

    clock[0] += 1
    for _ in range(claim_registry.STALL_RESTART_LIMIT):
        closed.clear()
        App.refresh_points_priority_allocation(probe)
        # Both windows restart; nobody loses the slot.
        assert set(closed) == urls
        assert probe._active_points_priority_channels == ("hanasakich", "jipandan")
        clock[0] += claim_registry.STALLED_SLOT_SECONDS

    # Restarts changed nothing: only now do the holders give up their turn.
    closed.clear()
    App.refresh_points_priority_allocation(probe)
    assert probe._active_points_priority_channels == ("daemoncasoul", "hanasakich")
    assert closed == ["https://www.twitch.tv/jipandan"]


def test_points_rise_restarts_the_stall_wait(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    claim_registry.track_slot_holders(["hayabi_zr"])
    clock[0] = claim_registry.STALLED_SLOT_SECONDS - 60
    claim_registry.record_points_increase("https://www.twitch.tv/hayabi_zr")

    clock[0] = claim_registry.STALLED_SLOT_SECONDS + 60
    assert claim_registry.track_slot_holders(["hayabi_zr"]) == set()


def test_served_holders_also_get_windows_restarted_when_points_stall(
    monkeypatch,
) -> None:
    # 2026-11-13: kspksp had its turn by 20:49, then earned nothing for
    # about three hours (20:49-22:04, 22:05-01:15) while the player said
    # "playing"; the stall restart only watched unserved holders.
    _serve("hayabi_zr", "gracya_leejia")
    rows = [_live("hayabi_zr"), _live("gracya_leejia")]
    urls = {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/gracya_leejia"}
    closed = _patch_windows(monkeypatch, urls)
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])

    App.refresh_points_priority_allocation(probe)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS
    for _ in range(claim_registry.STALL_RESTART_LIMIT):
        closed.clear()
        App.refresh_points_priority_allocation(probe)
        assert set(closed) == urls
        assert probe._active_points_priority_channels == ("hayabi_zr", "gracya_leejia")
        clock[0] += claim_registry.STALLED_SLOT_SECONDS

    # Restarts spent: no more restarts, and the served holders keep their slots.
    closed.clear()
    App.refresh_points_priority_allocation(probe)
    assert closed == []
    assert probe._active_points_priority_channels == ("hayabi_zr", "gracya_leejia")


def test_points_rise_restores_the_restart_allowance(monkeypatch) -> None:
    _serve("kspksp")
    step = claim_registry.STALLED_SLOT_SECONDS
    clock = [0.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    claim_registry.track_slot_holders(["kspksp"])
    for _ in range(claim_registry.STALL_RESTART_LIMIT):
        clock[0] += step
        assert claim_registry.track_slot_holders(["kspksp"]) == {"kspksp"}
    clock[0] += step
    assert claim_registry.track_slot_holders(["kspksp"]) == set()

    # A later rise proves playback recovered; the next stall restarts again.
    claim_registry.record_points_increase("https://www.twitch.tv/kspksp")
    clock[0] += step - 1
    assert claim_registry.track_slot_holders(["kspksp"]) == set()
    clock[0] += 1
    assert claim_registry.track_slot_holders(["kspksp"]) == {"kspksp"}


def test_given_up_turn_is_forgotten_when_broadcast_ends() -> None:
    step = claim_registry.STALLED_SLOT_SECONDS
    for tick in range(claim_registry.STALL_RESTART_LIMIT + 2):
        claim_registry.track_slot_holders(["hayabi_zr"], now=tick * step)
    assert claim_registry.had_turn("hayabi_zr")

    claim_registry.clear_served("hayabi_zr")

    assert not claim_registry.had_turn("hayabi_zr")


def test_holder_yields_after_points_rose_a_few_times(monkeypatch) -> None:
    # 2026-11-11: 4lianyun streamed for ten minutes (21:54-22:04); waiting
    # for a chest (~15 min) or the 20-minute turn would always miss it.
    _serve()
    rows = [_live("hanasakich"), _live("jipandan"), _live("4lianyun")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hanasakich", "https://www.twitch.tv/jipandan"},
    )
    probe = _sticky_probe(rows, ("hanasakich", "jipandan"))

    claim_registry.record_points_increase("https://www.twitch.tv/hanasakich")
    App.refresh_points_priority_allocation(probe)
    assert closed == []  # one rise is not enough

    claim_registry.record_points_increase("https://www.twitch.tv/hanasakich")
    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("jipandan", "4lianyun")
    assert closed == ["https://www.twitch.tv/hanasakich"]


def test_points_rises_are_forgotten_when_broadcast_ends() -> None:
    for _ in range(claim_registry.POINTS_INCREASES_FOR_TURN):
        claim_registry.record_points_increase("https://www.twitch.tv/hayabi_zr")
    assert claim_registry.had_turn("hayabi_zr")

    claim_registry.clear_served("hayabi_zr")

    assert not claim_registry.had_turn("hayabi_zr")
