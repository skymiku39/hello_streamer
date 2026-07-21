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


@dataclass(frozen=True)
class ChannelStatus:
    """Immutable snapshot of a channel's last-known status.

    ``status`` is the canonical state token: ``True`` (live), ``"upcoming"``
    (scheduled/waiting room), or ``False``/``None`` (offline). Some legacy
    paths also use the string ``"live"``; :attr:`is_live` normalises both.

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
    def is_live(self) -> bool:
        return self.status is True or self.status == "live"

    @property
    def is_upcoming(self) -> bool:
        return self.status == "upcoming"

    @property
    def is_offline(self) -> bool:
        return not self.is_live and not self.is_upcoming


@dataclass
class OfflineInfo:
    url: str
    title: str
    platform: str
    name: str
    video_id: str = ""
    display_name: str = ""
