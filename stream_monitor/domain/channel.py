"""Core channel value objects shared between monitor, events, and UI.

These have no dependency on the polling engine or the event bus, so both
``monitor`` and ``events`` can depend on them without creating a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from stream_monitor.util import channel_key, normalize_channel_name


@dataclass
class ChannelEntry:
    platform: str
    name: str
    enabled: bool = True
    # monitor_only = True ⇒ the polling thread should still observe this
    # channel (status updates, "LIVE" labels in the UI) but downstream
    # action dispatch (notifications, opening the browser, close_on_offline)
    # must be suppressed. The flag is carried on the entry so callbacks can
    # easily see it without re-resolving the channel via config_manager.
    monitor_only: bool = False

    def __post_init__(self) -> None:
        self.name = normalize_channel_name(self.platform, self.name)

    @property
    def key(self) -> str:
        return channel_key(self.platform, self.name)


@dataclass
class ChannelStatus:
    status: bool | str | None
    url: str = ""
    title: str = ""
    scheduled_start: str = ""
    started_at: str = ""
    ended_at: str = ""  # ISO8601 when offline was confirmed
    vod_url: str = ""  # archive / replay link for the link button
    upcoming_url: str = ""  # waiting-room link when offline but scheduled
    ended_at_source: str = ""  # "vod" | "confirmed" | "pending"

    def __eq__(self, other: object) -> bool:
        return self.status == other


@dataclass
class OfflineInfo:
    url: str
    title: str
    platform: str
    name: str
    video_id: str = ""
    display_name: str = ""
