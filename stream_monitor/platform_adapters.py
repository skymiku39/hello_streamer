"""Small callback adapters used to compose concrete desktop services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.fetcher.base import StreamInfo


@dataclass(frozen=True)
class NotificationAdapter:
    """Adapt a concrete notification function to ``NotificationPort``."""

    sender: Callable[..., bool]

    def send(self, info: StreamInfo, *, with_open_button: bool) -> bool:
        return self.sender(info, with_open_button=with_open_button)


@dataclass(frozen=True)
class BrowserAdapter:
    """Adapt a concrete browser launcher to ``BrowserPort``."""

    opener: Callable[..., bool]

    def open(
        self,
        info: StreamInfo,
        browser_settings: BrowserSettings | dict[str, Any] | None,
    ) -> bool:
        return self.opener(info, browser_settings)


@dataclass(frozen=True)
class WindowManagerAdapter:
    """Adapt managed browser-window operations to ``WindowManagerPort``."""

    availability: Callable[..., bool]
    close_url: Callable[..., int]
    close_everything: Callable[[], int]
    prune: Callable[[], int]

    def tracking_available(
        self,
        browser_settings: BrowserSettings | dict[str, Any] | None,
        url: str = "https://www.twitch.tv/example",
    ) -> bool:
        return self.availability(browser_settings, url)

    def close_for_url(
        self, url: str, *, title_keywords: list[str] | None = None
    ) -> int:
        return self.close_url(url, title_keywords=title_keywords)

    def close_all(self) -> int:
        return self.close_everything()

    def prune_off_topic(self) -> int:
        return self.prune()


@dataclass(frozen=True)
class PowerPolicyAdapter:
    """Adapt the optional system keep-awake operation."""

    setter: Callable[[bool], None]

    def set_keep_awake(self, active: bool) -> None:
        self.setter(active)
