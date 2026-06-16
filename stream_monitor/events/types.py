"""Typed monitor events for publish/subscribe decoupling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

from stream_monitor.domain import ChannelEntry, OfflineInfo
from stream_monitor.fetcher.base import StreamInfo


@dataclass(frozen=True, slots=True)
class ChannelWentLive:
    entry: ChannelEntry
    info: StreamInfo


@dataclass(frozen=True, slots=True)
class ChannelWentOffline:
    entry: ChannelEntry
    offline_info: OfflineInfo


@dataclass(frozen=True, slots=True)
class PollActivity:
    entry: ChannelEntry
    phase: str
    display_name: str


@dataclass(frozen=True, slots=True)
class PartialStatusUpdate:
    statuses: dict[str, Any]
    display_names: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", dict(self.statuses))
        object.__setattr__(self, "display_names", dict(self.display_names))


@dataclass(frozen=True, slots=True)
class PollWaiting:
    pass


@dataclass(frozen=True, slots=True)
class PollStatusUpdate:
    statuses: dict[str, Any]
    display_names: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", dict(self.statuses))
        object.__setattr__(self, "display_names", dict(self.display_names))


MonitorEvent = Union[
    ChannelWentLive,
    ChannelWentOffline,
    PollActivity,
    PartialStatusUpdate,
    PollWaiting,
    PollStatusUpdate,
]
