"""Core channel value objects shared between monitor, events, and UI.

These have no dependency on the polling engine or the event bus, so both
``monitor`` and ``events`` can depend on them without creating a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from stream_monitor.channel_policy import (
    CHANNEL_MODE_MONITOR,
    normalize_channel_mode,
)
from stream_monitor.util import channel_key, normalize_channel_name


class ChannelState(StrEnum):
    """Canonical observable state for one channel snapshot."""

    LIVE = "live"
    UPCOMING = "upcoming"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class ChannelEntry:
    platform: str
    name: str
    enabled: bool = True
    # Legacy compatibility view. The canonical per-channel policy is
    # ``channel_mode``: trigger / monitor / notify. One-shot is global and is
    # selected from the bottom monitor controls.
    monitor_only: bool = False
    channel_mode: str | None = None

    def __post_init__(self) -> None:
        self.name = normalize_channel_name(self.platform, self.name)
        self.channel_mode = normalize_channel_mode(
            self.channel_mode,
            legacy_monitor_only=self.monitor_only,
        )
        self.monitor_only = self.channel_mode == CHANNEL_MODE_MONITOR

    @property
    def key(self) -> str:
        return channel_key(self.platform, self.name)


@dataclass(frozen=True)
class ChannelStatus:
    """Immutable snapshot of a channel's last-known status.

    ``status`` is the canonical state token: ``True`` (live), ``"upcoming"``
    (scheduled/waiting room), or ``False`` (offline). Some legacy paths also
    use the string ``"live"``. ``None`` or an unrecognised token means
    ``unknown`` and must not be treated as a confirmed offline result.

    The class is ``frozen`` on purpose:

    * Equality and hashing are field-based (the normal dataclass behaviour), so
      instances are safe to place in sets / dict keys and never surprise callers
      with an asymmetric or status-only ``==``.
    * Immutability means a snapshot cannot be mutated in place behind a caller's
      back; produce a new value with :func:`dataclasses.replace` instead.

    Always branch on the explicit :attr:`is_live` / :attr:`is_upcoming` /
    :attr:`is_offline` helpers (or on ``.status``) rather than ``x is True`` on
    the object itself — the object is never identical to a bare ``bool``.
    """

    status: bool | str | None
    url: str = ""
    title: str = ""
    scheduled_start: str = ""
    started_at: str = ""
    ended_at: str = ""  # ISO8601 when offline was confirmed
    vod_url: str = ""  # archive / replay link for the link button
    upcoming_url: str = ""  # waiting-room link when offline but scheduled
    ended_at_source: str = ""  # "vod" | "confirmed" | "pending"

    @property
    def state(self) -> ChannelState:
        """Return the canonical state without exposing legacy encodings."""
        if self.status is True or self.status == "live":
            return ChannelState.LIVE
        if self.status == "upcoming":
            return ChannelState.UPCOMING
        if self.status is False or self.status == "offline":
            return ChannelState.OFFLINE
        return ChannelState.UNKNOWN

    @property
    def is_live(self) -> bool:
        return self.state is ChannelState.LIVE

    @property
    def is_upcoming(self) -> bool:
        return self.state is ChannelState.UPCOMING

    @property
    def is_offline(self) -> bool:
        return self.state is ChannelState.OFFLINE

    @property
    def is_unknown(self) -> bool:
        return self.state is ChannelState.UNKNOWN


@dataclass
class OfflineInfo:
    url: str
    title: str
    platform: str
    name: str
    video_id: str = ""
    display_name: str = ""
