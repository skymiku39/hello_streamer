"""Typed settings for the Twitch "viewer engagement" assist feature.

Twitch's Viewer Count FAQ says muted players and unfocused tabs count while
live video is playing. It does not specify a heartbeat interval or per-IP
limit. System sleep or browser suspension can interrupt playback.

These settings let the user opt in to launch-time mitigations the desktop app
*can* control (window visibility, system-sleep suppression, Chrome performance
whitelist). Optional CDP page-assist (content gate, theater mode, channel-point
claim, auto-refresh) is available in source and packaged builds.
"""

from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class ViewerEngagementSettings:
    """Best-effort playback assistance; no guarantee of Twitch view credit."""

    enabled: bool = False
    # Skip the minimise / hide-from-taskbar window treatment for Twitch pages,
    # when the user prefers to keep managed playback windows visible.
    force_visible: bool = True
    # Hold a Windows execution-state request while a tracked Twitch window is
    # open so the machine does not sleep mid-stream.
    keep_system_awake: bool = True
    # Merge twitch.tv into the dedicated Chrome profile's "always keep active"
    # performance exception list before launch (dedicated profile only).
    whitelist_performance: bool = True
    # Bring the freshly-opened Twitch window to the foreground so the Page
    # Visibility API reports "visible" during player initialisation.
    bring_to_front: bool = True
    # How many seconds to keep reasserting foreground after the browser window
    # is first detected. This is a playback-initialization preference, not
    # a requirement for Twitch viewer counting.
    foreground_hold_seconds: int = 15

    # --- Optional CDP page assist ---
    page_assist_enabled: bool = False
    accept_content_gate: bool = True
    claim_channel_points: bool = False
    claim_delay_seconds_min: int = 2
    claim_delay_seconds_max: int = 12
    claim_click_offset_px: int = 10
    claim_poll_seconds_min: int = 45
    claim_poll_seconds_max: int = 90
    theater_mode: bool = False
    theater_delay_seconds: int = 8
    auto_refresh: bool = False
    refresh_minutes_min: int = 45
    refresh_minutes_max: int = 75

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

    def page_assist_active(self) -> bool:
        """True when CDP page assist should run for a qualifying launch."""
        return (
            self.enabled
            and self.page_assist_enabled
            and page_assist_runtime_allowed()
        )


def page_assist_runtime_allowed() -> bool:
    """Both distribution modes support CDP when its transport is installed."""
    return importlib.util.find_spec("websocket") is not None


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
