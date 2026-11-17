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
    probe = SimpleNamespace(
        config={
            # These tests jump the clock in big steps; a slow poll interval
            # keeps the jumps from counting as tracking interruptions (#60).
            "check_interval": 3600,
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
        _offline_closed_at={},
        _unallocated_closed_logged=set(),
        _OFFLINE_REOPEN_GUARD_SECONDS=App._OFFLINE_REOPEN_GUARD_SECONDS,
    )
    probe._recently_closed_for_offline = (
        lambda name, events: App._recently_closed_for_offline(probe, name, events)
    )
    return probe


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
    for name in ("hayabi_zr", "gracya_leejia"):
        claim_registry.record_confirmed_claim(f"https://www.twitch.tv/{name}")
        claim_registry.credit_watch_seconds(name, claim_registry.MIN_TURN_WATCH_SECONDS)
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
        claim_registry.credit_watch_seconds(name, claim_registry.MIN_TURN_WATCH_SECONDS)


def test_unclaimed_holders_keep_slots_against_priority_channel(monkeypatch) -> None:
    _serve()
    rows = [_live("hayabi_zr"), _live("gracya_leejia"), _live("kspksp")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/gracya_leejia"},
    )
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))

    App.refresh_points_priority_allocation(probe)

    assert "hayabi_zr" in probe._active_points_priority_channels
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

    assert "hayabi_zr" in probe._active_points_priority_channels
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
    # A restart closes windows, so it needs a call that reopens them (#61).
    probe = _reopening_probe(rows, ("hanasakich", "jipandan"))
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])

    App.refresh_points_priority_allocation(probe, allow_open=True)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS - 1
    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert closed == []
    assert probe.submitted == []

    clock[0] += 1
    for count in range(1, claim_registry.STALL_RESTART_LIMIT + 1):
        closed.clear()
        probe.submitted.clear()
        App.refresh_points_priority_allocation(probe, allow_open=True)
        # Both windows restart in the same call; nobody loses the slot.
        assert set(closed) == urls
        assert sorted(probe.submitted) == ["hanasakich", "jipandan"]
        assert probe._active_points_priority_channels == ("hanasakich", "jipandan")
        assert claim_registry._STALL_RESTARTS == {
            "hanasakich": count,
            "jipandan": count,
        }
        clock[0] += claim_registry.STALLED_SLOT_SECONDS

    # Restarts changed nothing: only now do the holders give up their turn.
    closed.clear()
    probe.submitted.clear()
    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert probe._active_points_priority_channels == ("daemoncasoul", "hanasakich")
    assert closed == ["https://www.twitch.tv/jipandan"]
    assert probe.submitted == ["daemoncasoul"]


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
    probe = _reopening_probe(rows, ("hayabi_zr", "gracya_leejia"))
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])

    App.refresh_points_priority_allocation(probe, allow_open=True)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS
    for _ in range(claim_registry.STALL_RESTART_LIMIT):
        closed.clear()
        App.refresh_points_priority_allocation(probe, allow_open=True)
        assert set(closed) == urls
        assert "hayabi_zr" in probe._active_points_priority_channels
        clock[0] += claim_registry.STALLED_SLOT_SECONDS

    # Restarts spent: no more restarts, and the served holders keep their slots.
    closed.clear()
    probe.submitted.clear()
    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert closed == []
    assert probe.submitted == []
    assert "hayabi_zr" in probe._active_points_priority_channels


def test_points_rise_restores_the_restart_allowance(monkeypatch) -> None:
    _serve("kspksp")
    step = claim_registry.STALLED_SLOT_SECONDS
    clock = [0.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    claim_registry.track_slot_holders(["kspksp"])
    for _ in range(claim_registry.STALL_RESTART_LIMIT):
        clock[0] += step
        assert claim_registry.track_slot_holders(["kspksp"]) == {"kspksp"}
        claim_registry.consume_stall_restart({"kspksp"})
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
        stalled = claim_registry.track_slot_holders(["hayabi_zr"], now=tick * step)
        claim_registry.consume_stall_restart(stalled)
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
    claim_registry.credit_watch_seconds("hanasakich", claim_registry.MIN_TURN_WATCH_SECONDS)

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
    claim_registry.credit_watch_seconds("hayabi_zr", claim_registry.MIN_TURN_WATCH_SECONDS)
    assert claim_registry.had_turn("hayabi_zr")

    claim_registry.clear_served("hayabi_zr")

    assert not claim_registry.had_turn("hayabi_zr")


@pytest.mark.parametrize("slots", [3, 4])
def test_slot_count_setting_fills_more_signed_in_slots(monkeypatch, slots) -> None:
    # 2026-11-14 experiment: does Twitch credit more than two streams at once?
    names = ["kspksp", "hayabi_zr", "gracya_leejia", "hanasakich", "oldpot_o_0"]
    rows = [_live(name) for name in names]
    _patch_windows(monkeypatch, set())
    probe = _sticky_probe(rows, ())
    probe.config["viewer_engagement"]["points_slot_count"] = slots

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == tuple(names[:slots])


@pytest.mark.parametrize(("raw", "expected"), [(None, 2), ("x", 2), (0, 1), (3, 3), (9, 4)])
def test_slot_count_is_clamped(raw, expected) -> None:
    from stream_monitor.config_manager import _normalize_viewer_engagement
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    raw_dict = {} if raw is None else {"points_slot_count": raw}
    assert ViewerEngagementSettings.from_dict(raw_dict or {"enabled": True}).points_slot_count == expected
    if isinstance(raw, int):
        assert _normalize_viewer_engagement(raw_dict)["points_slot_count"] == expected


def test_channel_closed_for_offline_is_not_reopened_while_row_lags(monkeypatch) -> None:
    # 2026-11-13 19:41: close_on_offline closed hayabi_zr, its row still read
    # live for a moment, and the allocator reopened the window 1 s later.
    rows = [_live("hayabi_zr"), _live("jipandan"), _live("gracya_leejia")]
    _serve("hayabi_zr", "jipandan", "gracya_leejia")
    _patch_windows(monkeypatch, {"https://www.twitch.tv/gracya_leejia"})
    probe = _sticky_probe(rows, ("hayabi_zr", "gracya_leejia"))
    clock = [500.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    probe._offline_closed_at["hayabi_zr"] = clock[0]

    App.refresh_points_priority_allocation(probe)
    assert set(probe._active_points_priority_channels) == {"gracya_leejia", "jipandan"}

    # A new went_live (or the guard expiring) lets it compete again.
    App.refresh_points_priority_allocation(probe, event_channels={"hayabi_zr"})
    assert "hayabi_zr" in probe._active_points_priority_channels
    assert "hayabi_zr" not in probe._offline_closed_at


def test_unallocated_close_is_logged_once(monkeypatch, caplog) -> None:
    rows = [_live("kspksp"), _live("hayabi_zr"), _live("jipandan")]
    _serve("kspksp", "hayabi_zr", "jipandan")
    closed = _patch_windows(monkeypatch, {"https://www.twitch.tv/jipandan"})
    probe = _sticky_probe(rows, ("kspksp", "hayabi_zr"))

    with caplog.at_level("INFO"):
        for _ in range(3):
            App.refresh_points_priority_allocation(probe)

    assert len(closed) == 3
    assert caplog.text.count("Closed unallocated Twitch playback: channel=jipandan") == 1


def test_arrival_chest_does_not_end_the_turn(monkeypatch) -> None:
    # 2026-11-14 20:53: jipandan went live, Twitch offered a chest on arrival,
    # the claim landed 21 s after the window opened and the slot went straight
    # back to gracya_leejia. jipandan was not watched again for 77 minutes.
    _serve("gracya_leejia", "4lianyun")
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    rows = [_live("4lianyun"), _live("gracya_leejia"), _live("jipandan")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/4lianyun", "https://www.twitch.tv/jipandan"},
    )
    probe = _sticky_probe(rows, ("jipandan", "4lianyun"))
    App.refresh_points_priority_allocation(probe)

    clock[0] += 21
    claim_registry.record_confirmed_claim("https://www.twitch.tv/jipandan")
    claim_registry.record_points_increase("https://www.twitch.tv/jipandan")
    App.refresh_points_priority_allocation(probe)

    assert "jipandan" in probe._active_points_priority_channels
    assert closed == []

    clock[0] += claim_registry.MIN_TURN_WATCH_SECONDS
    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("4lianyun", "gracya_leejia")
    assert closed == ["https://www.twitch.tv/jipandan"]


def test_watch_time_only_counts_while_holding_a_slot() -> None:
    claim_registry.reset_for_tests()
    half = claim_registry.MIN_TURN_WATCH_SECONDS / 2
    claim_registry.record_confirmed_claim("https://www.twitch.tv/jipandan")
    claim_registry.track_slot_holders(["jipandan"], now=0.0)
    claim_registry.track_slot_holders(["jipandan"], now=half)
    claim_registry.track_slot_holders([], now=half + 1)
    claim_registry.track_slot_holders(["jipandan"], now=half + 5000)  # not held
    assert not claim_registry.had_turn("jipandan")

    claim_registry.track_slot_holders(["jipandan"], now=half + 5000 + half)

    assert claim_registry.had_turn("jipandan")


_GAP = claim_registry.MAX_TRACK_GAP_SECONDS


def test_gap_after_monitor_stop_is_not_watch_time() -> None:
    # Issue #60: a 15-minute stop must not be credited as watching.
    claim_registry.record_confirmed_claim("https://www.twitch.tv/jipandan")
    claim_registry.track_slot_holders(["jipandan"], now=0.0, max_gap=_GAP)
    claim_registry.track_slot_holders(["jipandan"], now=10.0, max_gap=_GAP)

    claim_registry.track_slot_holders(["jipandan"], now=10.0 + 15 * 60, max_gap=_GAP)

    assert claim_registry._WATCHED_SECONDS["jipandan"] == 10.0
    assert not claim_registry.had_turn("jipandan")
    assert claim_registry.is_served("jipandan")  # claim record is kept


def test_gap_longer_than_stall_window_does_not_report_stall() -> None:
    def track(now: float) -> set[str]:
        return claim_registry.track_slot_holders(["hayabi_zr"], now=now, max_gap=_GAP)

    track(0.0)
    resume = 25 * 60.0

    assert track(resume) == set()
    assert claim_registry._STALL_RESTARTS.get("hayabi_zr", 0) == 0
    # The stall wait restarted at the resume, so a full window must pass again.
    now = resume
    while now < resume + claim_registry.STALLED_SLOT_SECONDS - 10:
        now += 10.0
        assert track(now) == set()
    assert track(now + 10.0) == {"hayabi_zr"}
    # Reporting spends nothing; the caller consumes when it restarts (#61).
    assert claim_registry._STALL_RESTARTS.get("hayabi_zr", 0) == 0


def test_normal_polling_interval_still_accumulates_watch_time() -> None:
    for tick in range(7):
        claim_registry.track_slot_holders(["jipandan"], now=tick * 10.0, max_gap=_GAP)

    assert claim_registry._WATCHED_SECONDS["jipandan"] == 60.0


def test_gap_limit_scales_with_a_slow_poll_interval() -> None:
    # The poll interval has no upper bound; a fixed limit would make every
    # slow poll look like an interruption and nobody would earn a turn.
    assert claim_registry.track_gap_limit(10) == claim_registry.MAX_TRACK_GAP_SECONDS
    limit = claim_registry.track_gap_limit(300)
    assert limit == 900

    claim_registry.track_slot_holders(["jipandan"], now=0.0, max_gap=limit)
    claim_registry.track_slot_holders(["jipandan"], now=300.0, max_gap=limit)

    assert claim_registry._WATCHED_SECONDS["jipandan"] == 300.0


def _reopening_probe(rows: list[Any], previous: tuple[str, ...]) -> Any:
    probe = _sticky_probe(rows, previous)
    submitted: list[str] = []
    pending: set[str] = set()
    probe._controller = SimpleNamespace(mode=app_module.TRIGGER_MODE, generation=1)
    probe._action_coordinator = SimpleNamespace(
        submit=lambda _plan, info, _settings, _gen: submitted.append(info.channel) or True,
        is_pending=lambda _gen, _platform, channel, _key: channel in pending,
    )
    probe.submitted = submitted
    probe.pending = pending
    return probe


def test_queued_slot_open_is_not_resubmitted_on_every_repaint(monkeypatch) -> None:
    # 11/17 10:46: each row repaint reran the allocation while the open action
    # was still queued, logging ~170 "Skipped duplicate pending action" lines.
    rows = [_live("hayabi_zr"), _live("neipuduck")]
    _patch_windows(monkeypatch, set())
    probe = _reopening_probe(rows, ("hayabi_zr", "neipuduck"))
    probe.pending.add("hayabi_zr")

    App.refresh_points_priority_allocation(probe, allow_open=True)

    assert probe.submitted == ["neipuduck"]


def test_browser_without_endpoint_restarts_every_slot_window(monkeypatch) -> None:
    # 2026-11-16 14:36: the replacement browser started without a debugging
    # endpoint; reopening one window would land in the same browser.
    rows = [_live("hayabi_zr"), _live("neipuduck")]
    urls = {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/neipuduck"}
    closed = _patch_windows(monkeypatch, urls)
    probe = _reopening_probe(rows, ("hayabi_zr", "neipuduck"))
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: 1000.0)

    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert closed == []

    claim_registry.request_endpoint_restart()
    App.refresh_points_priority_allocation(probe, allow_open=True)

    assert set(closed) == urls
    assert sorted(probe.submitted) == ["hayabi_zr", "neipuduck"]
    assert probe._active_points_priority_channels == ("hayabi_zr", "neipuduck")


def test_endpoint_restart_waits_until_windows_can_be_reopened(monkeypatch) -> None:
    rows = [_live("hayabi_zr"), _live("neipuduck")]
    closed = _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hayabi_zr", "https://www.twitch.tv/neipuduck"},
    )
    probe = _reopening_probe(rows, ("hayabi_zr", "neipuduck"))
    claim_registry.request_endpoint_restart()

    App.refresh_points_priority_allocation(probe)  # poll without opening
    assert closed == []

    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert len(closed) == 2


def test_endpoint_restarts_are_spaced_and_bounded() -> None:
    now = 1000.0
    claim_registry.request_endpoint_restart()
    assert claim_registry.take_endpoint_restart(now=now)
    assert not claim_registry.take_endpoint_restart(now=now)  # nothing pending

    claim_registry.request_endpoint_restart()
    assert not claim_registry.take_endpoint_restart(now=now + 60)  # cooldown
    now += claim_registry.ENDPOINT_RESTART_COOLDOWN_SECONDS
    assert claim_registry.take_endpoint_restart(now=now)

    # The limit is spent: a browser that never exposes an endpoint is left to
    # the points stall restart instead of being restarted forever.
    claim_registry.request_endpoint_restart()
    now += claim_registry.ENDPOINT_RESTART_COOLDOWN_SECONDS
    assert not claim_registry.take_endpoint_restart(now=now)

    # A successful attach proves the restart worked and restores the allowance.
    claim_registry.note_endpoint_attached()
    claim_registry.request_endpoint_restart()
    assert claim_registry.take_endpoint_restart(now=now)


def test_attach_clears_a_pending_endpoint_restart() -> None:
    claim_registry.request_endpoint_restart()
    claim_registry.note_endpoint_attached()

    assert not claim_registry.take_endpoint_restart(now=1000.0)


def _stalled_pair(monkeypatch) -> tuple[Any, list[str], set[str], list[float]]:
    _serve()
    rows = [_live("hanasakich"), _live("jipandan")]
    urls = {"https://www.twitch.tv/hanasakich", "https://www.twitch.tv/jipandan"}
    closed = _patch_windows(monkeypatch, urls)
    probe = _reopening_probe(rows, ("hanasakich", "jipandan"))
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    App.refresh_points_priority_allocation(probe)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS
    return probe, closed, urls, clock


def _block_reopen(probe: Any, how: str) -> dict[str, Any]:
    """Make the next call unable to reopen windows; return its kwargs."""
    if how == "allow_open":
        return {}
    if how == "open_on_live":
        probe.config["trigger_settings"] = {"open_on_live": False}
    else:
        probe._controller.mode = how
    return {"allow_open": True}


def _unblock_reopen(probe: Any) -> None:
    probe.config["trigger_settings"] = {}
    probe._controller.mode = app_module.TRIGGER_MODE


@pytest.mark.parametrize(
    "how", ["allow_open", "idle", app_module.WATCH_MODE, "open_on_live"]
)
def test_stall_waits_for_a_call_that_reopens_windows(monkeypatch, how) -> None:
    # Issue #61: a stall seen by a call that cannot reopen (startup or
    # monitor-mode poll, watch/idle mode, open_on_live off) closed every slot
    # window and spent a restart; in watch/idle mode the windows never came back.
    probe, closed, urls, clock = _stalled_pair(monkeypatch)
    kwargs = _block_reopen(probe, how)

    for _ in range(3):
        App.refresh_points_priority_allocation(probe, **kwargs)
        clock[0] += 60

    assert closed == []
    assert probe.submitted == []
    assert claim_registry._STALL_RESTARTS == {}
    assert probe._active_points_priority_channels == ("hanasakich", "jipandan")

    # The first call that can reopen performs the restart it was owed.
    _unblock_reopen(probe)
    App.refresh_points_priority_allocation(probe, allow_open=True)

    assert set(closed) == urls
    assert sorted(probe.submitted) == ["hanasakich", "jipandan"]
    assert claim_registry._STALL_RESTARTS == {"hanasakich": 1, "jipandan": 1}


def test_stall_restart_reopens_in_trigger_mode_and_counts_once(monkeypatch) -> None:
    probe, closed, urls, clock = _stalled_pair(monkeypatch)

    App.refresh_points_priority_allocation(probe, allow_open=True)

    assert set(closed) == urls
    assert sorted(probe.submitted) == ["hanasakich", "jipandan"]
    assert claim_registry._STALL_RESTARTS == {"hanasakich": 1, "jipandan": 1}

    # Every holder starts a new wait: the next call does not restart again.
    closed.clear()
    probe.submitted.clear()
    clock[0] += claim_registry.STALLED_SLOT_SECONDS - 1
    App.refresh_points_priority_allocation(probe, allow_open=True)
    assert closed == []
    assert claim_registry._STALL_RESTARTS == {"hanasakich": 1, "jipandan": 1}


def test_spent_restarts_still_end_the_turn_without_reopening(monkeypatch) -> None:
    # Giving up a turn once restarts are spent does not depend on reopening.
    _serve()
    rows = [_live("daemoncasoul"), _live("hanasakich"), _live("jipandan")]
    _patch_windows(
        monkeypatch,
        {"https://www.twitch.tv/hanasakich", "https://www.twitch.tv/jipandan"},
    )
    probe = _sticky_probe(rows, ("hanasakich", "jipandan"))
    for name in ("hanasakich", "jipandan"):
        claim_registry._STALL_RESTARTS[name] = claim_registry.STALL_RESTART_LIMIT
    clock = [1000.0]
    monkeypatch.setattr(claim_registry.time, "monotonic", lambda: clock[0])
    App.refresh_points_priority_allocation(probe)
    clock[0] += claim_registry.STALLED_SLOT_SECONDS

    App.refresh_points_priority_allocation(probe)

    assert claim_registry.had_turn("hanasakich")
    assert probe._active_points_priority_channels == ("daemoncasoul", "hanasakich")


def test_reporting_a_stall_does_not_spend_a_restart() -> None:
    step = claim_registry.STALLED_SLOT_SECONDS
    claim_registry.track_slot_holders(["hayabi_zr", "kspksp"], now=0.0)
    for tick in range(1, claim_registry.STALL_RESTART_LIMIT + 3):
        assert claim_registry.track_slot_holders(
            ["hayabi_zr", "kspksp"], now=tick * step
        ) == {"hayabi_zr", "kspksp"}
    assert claim_registry._STALL_RESTARTS == {}
    assert not claim_registry.had_turn("hayabi_zr")

    claim_registry.consume_stall_restart({"hayabi_zr"})

    # The restart reset every holder's wait, not only the consumed one.
    later = (claim_registry.STALL_RESTART_LIMIT + 2) * step + step - 1
    assert claim_registry.track_slot_holders(["hayabi_zr", "kspksp"], now=later) == set()
    assert claim_registry._STALL_RESTARTS == {"hayabi_zr": 1}


def _toggle_probe(rows: list[Any], *, allocation: bool | None) -> Any:
    probe = _sticky_probe(rows, ("a_one", "b_two"))
    engagement = probe.config["viewer_engagement"]
    engagement["points_priority_channels"] = []
    if allocation is not None:
        engagement["points_allocation_enabled"] = allocation
    return probe


def test_allocation_switch_with_empty_list_follows_main_list(monkeypatch) -> None:
    # #91: the slot limit no longer depends on a non-empty priority list.
    rows = [_live("a_one"), _live("b_two"), _live("c_three")]
    _serve("a_one", "b_two", "c_three")
    closed = _patch_windows(
        monkeypatch, {f"https://www.twitch.tv/{n}" for n in ("a_one", "b_two", "c_three")}
    )
    probe = _toggle_probe(rows, allocation=True)

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels == ("a_one", "b_two")
    assert closed == ["https://www.twitch.tv/c_three"]


def test_allocation_switch_off_does_not_limit_windows(monkeypatch) -> None:
    rows = [_live("a_one"), _live("b_two"), _live("c_three")]
    closed = _patch_windows(
        monkeypatch, {f"https://www.twitch.tv/{n}" for n in ("a_one", "b_two", "c_three")}
    )
    probe = _toggle_probe(rows, allocation=False)
    probe.config["viewer_engagement"]["points_priority_channels"] = ["a_one"]

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels is None
    assert closed == []


@pytest.mark.parametrize(("priorities", "expected"), [(["a_one"], True), ([], False)])
def test_missing_allocation_key_is_derived_from_priority_list(
    monkeypatch, priorities, expected
) -> None:
    rows = [_live("a_one"), _live("b_two"), _live("c_three")]
    closed = _patch_windows(monkeypatch, {"https://www.twitch.tv/c_three"})
    probe = _toggle_probe(rows, allocation=None)
    probe.config["viewer_engagement"]["points_priority_channels"] = priorities

    App.refresh_points_priority_allocation(probe)

    assert (probe._active_points_priority_channels is not None) is expected
    assert (closed == ["https://www.twitch.tv/c_three"]) is expected


def test_incomplete_cold_start_with_empty_list_stays_unallocated(monkeypatch) -> None:
    rows = [_live("a_one"), SimpleNamespace(key="twitch:b_two", verification_pending=True)]
    closed = _patch_windows(monkeypatch, {"https://www.twitch.tv/a_one"})
    probe = _toggle_probe(rows, allocation=True)
    probe._active_points_priority_channels = None

    App.refresh_points_priority_allocation(probe)

    assert probe._active_points_priority_channels is None
    assert closed == []
