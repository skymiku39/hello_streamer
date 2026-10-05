"""Ports used by action execution.

The domain/action layers depend on these small contracts rather than on
``webbrowser``, ``notify-send``, Win32, or any other desktop API.  The
concrete adapters are assembled by :mod:`stream_monitor.notifier`, which is
the platform-facing composition boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.fetcher.base import StreamInfo


class NotificationPort(Protocol):
    """Desktop notification capability."""

    def send(self, info: StreamInfo, *, with_open_button: bool) -> bool: ...


class BrowserPort(Protocol):
    """Browser launch capability."""

    def open(
        self,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
    ) -> bool: ...


class WindowManagerPort(Protocol):
    """Managed browser-window lifecycle capability."""

    def tracking_available(
        self,
        browser_settings: BrowserSettings | dict[str, Any] | None,
        url: str = "https://www.twitch.tv/example",
    ) -> bool: ...

    def close_for_url(self, url: str, *, title_keywords: list[str] | None = None) -> int: ...

    def close_all(self) -> int: ...

    def prune_off_topic(self) -> int: ...

    def release_keep_awake_for_closed(self) -> int: ...


class PowerPolicyPort(Protocol):
    """System power-policy capability used by viewer engagement."""

    def set_keep_awake(self, active: bool) -> None: ...


@dataclass(frozen=True)
class ActionPorts:
    """Platform effects required by :class:`ActionExecutor`."""

    notification: NotificationPort
    browser: BrowserPort


@dataclass(frozen=True)
class PlatformServices(ActionPorts):
    """Complete desktop capability set assembled at the composition root."""

    window: WindowManagerPort
    power: PowerPolicyPort
