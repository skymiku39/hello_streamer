"""Smoke tests for the neutral domain value objects."""

from __future__ import annotations

from stream_monitor.domain import ChannelEntry, ChannelStatus, OfflineInfo


def test_channel_entry_normalizes_and_keys() -> None:
    entry = ChannelEntry(platform="twitch", name="Hello_Streamer")
    assert entry.name == "hello_streamer"
    assert entry.key == "twitch:hello_streamer"
    assert entry.enabled is True
    assert entry.monitor_only is False


def test_channel_status_state_helpers() -> None:
    """Explicit state helpers replace the old asymmetric ``== primitive`` magic.

    Design change: ``ChannelStatus`` no longer overrides ``__eq__`` to compare
    against its bare ``status`` value (which made ``cs == True`` true but
    ``cs is True`` false — a silent misjudgment trap and left the value
    unhashable). Callers now branch on ``.is_live`` / ``.is_upcoming`` /
    ``.is_offline`` or on ``.status`` directly.
    """
    live = ChannelStatus(status=True)
    assert live.is_live and not live.is_upcoming and not live.is_offline

    upcoming = ChannelStatus(status="upcoming")
    assert upcoming.is_upcoming and not upcoming.is_live and not upcoming.is_offline

    offline = ChannelStatus(status=False)
    assert offline.is_offline and not offline.is_live and not offline.is_upcoming

    # "live" string form is normalised to live as well.
    assert ChannelStatus(status="live").is_live
    # None (unknown) is treated as offline.
    assert ChannelStatus(status=None).is_offline


def test_channel_status_is_hashable_and_field_equal() -> None:
    """Frozen dataclass ⇒ field-based equality + hashable (safe in sets)."""
    a = ChannelStatus(status=True, url="u", title="t")
    b = ChannelStatus(status=True, url="u", title="t")
    c = ChannelStatus(status=True, url="other", title="t")
    assert a == b
    assert a != c
    assert len({a, b, c}) == 2  # a and b collapse; c is distinct
    # No longer equal to its bare status value (that magic is gone).
    assert a != True  # noqa: E712


def test_offline_info_defaults() -> None:
    info = OfflineInfo(url="u", title="t", platform="twitch", name="n")
    assert info.video_id == ""
    assert info.display_name == ""


def test_monitor_and_events_reexport_same_domain_types() -> None:
    from stream_monitor.events.types import ChannelWentLive
    from stream_monitor.monitor import ChannelEntry as MonitorChannelEntry
    from stream_monitor.monitor.types import OfflineInfo as MonitorOfflineInfo

    assert MonitorChannelEntry is ChannelEntry
    assert MonitorOfflineInfo is OfflineInfo
    assert ChannelWentLive.__annotations__["entry"] in ("ChannelEntry", ChannelEntry)
