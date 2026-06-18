"""Typed settings for the Twitch "viewer engagement" assist feature.

Twitch only credits a view while the browser keeps sending its ~1/min
heartbeat *and* the player keeps rendering frames. Modern browsers and the OS
work against that: background tabs get throttled, Memory/Energy Saver freezes
tabs, the Page Visibility API reports the tab as hidden, and system sleep cuts
the connection entirely. A window the app opened minimised / hidden from the
taskbar is, from Twitch's perspective, a backgrounded tab that may not count.

These settings let the user opt in to launch-time mitigations the desktop app
*can* control (window visibility, system-sleep suppression, Chrome performance
whitelist). It cannot forge the in-page visibility/focus signals — that needs a
browser extension — so this is best-effort and documented as such in the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class ViewerEngagementSettings:
    """User preferences for keeping an opened Twitch stream "counted"."""

    enabled: bool = False
    # Skip the minimise / hide-from-taskbar window treatment for Twitch pages,
    # since a hidden window reads as a backgrounded tab to Twitch.
    force_visible: bool = True
    # Hold a Windows execution-state request while a tracked Twitch window is
    # open so the machine does not sleep mid-stream.
    keep_system_awake: bool = True
    # Merge twitch.tv into the dedicated Chrome profile's "always keep active"
    # performance exception list before launch (dedicated profile only).
    whitelist_performance: bool = True
    # Briefly bring the freshly-opened Twitch window to the foreground.
    bring_to_front: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ViewerEngagementSettings:
        if not raw:
            return cls()
        valid = {field.name for field in fields(cls)}
        kwargs = {key: raw[key] for key in valid if key in raw}
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def coerce_viewer_engagement(
    settings: ViewerEngagementSettings | dict[str, Any] | None,
) -> ViewerEngagementSettings | None:
    if settings is None:
        return None
    if isinstance(settings, ViewerEngagementSettings):
        return settings
    return ViewerEngagementSettings.from_dict(settings)


def is_twitch_url(url: str) -> bool:
    """True when *url* points at twitch.tv (the only platform v1 assists)."""
    if not url:
        return False
    lowered = url.strip().lower()
    return "twitch.tv/" in lowered or lowered.rstrip("/").endswith("twitch.tv")
