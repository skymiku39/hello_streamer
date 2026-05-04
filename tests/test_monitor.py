from stream_monitor import monitor as monitor_module
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelEntry, Monitor


class FakeFetcher:
    def __init__(self, statuses: list[bool]) -> None:
        self.statuses = statuses

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        is_live = self.statuses.pop(0)
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=is_live,
            title="Live now" if is_live else "",
            url=f"https://www.twitch.tv/{channel_name}",
            display_name="Hello Channel",
        )


def test_monitor_triggers_only_when_channel_transitions_to_live(monkeypatch) -> None:
    fetcher = FakeFetcher([False, True, True, False, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _platform: fetcher)
    events: list[tuple[str, str]] = []
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        on_status_change=lambda entry, info: events.append((entry.key, info.title)),
    )
    entry = ChannelEntry(platform="twitch", name="hello")

    went_live: list[tuple[ChannelEntry, object]] = []
    for _ in range(5):
        result = monitor._check_channel(entry)
        if result is not None:
            went_live.append(result)

    for e, info in went_live:
        events.append((e.key, info.title))

    assert events == [
        ("twitch:hello", "Live now"),
        ("twitch:hello", "Live now"),
    ]
    assert monitor.snapshot_statuses() == {"twitch:hello": True}
    assert monitor.snapshot_display_names() == {"twitch:hello": "Hello Channel"}


def test_monitor_skips_disabled_channels(monkeypatch) -> None:
    fetcher = FakeFetcher([True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _platform: fetcher)
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello", "enabled": False}],
    )
    entry = ChannelEntry(platform="twitch", name="hello", enabled=False)

    result = monitor._check_channel(entry)
    assert result is not None

    with monitor._lock:
        entries = list(monitor._entries)
    assert entries[0].enabled is False


def test_update_channels_replaces_entries() -> None:
    monitor = Monitor(channels=[{"platform": "twitch", "name": "old"}])

    monitor.update_channels([{"platform": "youtube", "name": "new"}])

    with monitor._lock:
        assert [entry.key for entry in monitor._entries] == ["youtube:new"]


def test_update_channels_preserves_enabled_flag() -> None:
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a", "enabled": False}]
    )

    with monitor._lock:
        assert monitor._entries[0].enabled is False

    monitor.update_channels([
        {"platform": "twitch", "name": "a", "enabled": True},
        {"platform": "twitch", "name": "b"},
    ])

    with monitor._lock:
        assert monitor._entries[0].enabled is True
        assert monitor._entries[1].enabled is True
