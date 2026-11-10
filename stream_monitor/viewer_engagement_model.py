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
import re
import threading
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any
from urllib.parse import urlsplit

_ACTIVE_POINTS_CHANNELS_LOCK = threading.Lock()
_ACTIVE_POINTS_CHANNELS: tuple[str, ...] | None = None


def set_active_points_channels(channels: Any = None) -> None:
    """Set the runtime login allocation; ``None`` keeps static priority mode."""
    global _ACTIVE_POINTS_CHANNELS
    if channels is None:
        normalized = None
    else:
        normalized = tuple(normalize_points_priorities(channels))
    with _ACTIVE_POINTS_CHANNELS_LOCK:
        _ACTIVE_POINTS_CHANNELS = normalized


def active_points_channels_configured() -> bool:
    with _ACTIVE_POINTS_CHANNELS_LOCK:
        return _ACTIVE_POINTS_CHANNELS is not None


def normalize_points_priorities(value: Any) -> list[str]:
    """Parse explicit channel logins, rejecting an ambiguous or oversized list."""
    entries = re.split(r"[\s,，]+", value.strip()) if isinstance(value, str) else value
    if not isinstance(entries, (list, tuple)):
        raise ValueError("Expected channel names")
    names = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError("Expected channel names")
        name = entry.strip().lower()
        if not name:
            continue
        if "://" in name:
            parsed = urlsplit(name)
            if parsed.hostname not in {"twitch.tv", "www.twitch.tv"}:
                raise ValueError("Expected a Twitch channel URL")
            name = parsed.path.strip("/")
        if not re.fullmatch(r"[a-z0-9_]{1,25}", name):
            raise ValueError("Invalid Twitch channel name")
        if name not in names:
            names.append(name)
    if len(names) > 2:
        raise ValueError("Choose at most two priority channels")
    return names


@dataclass
class ViewerEngagementSettings:
    """Best-effort playback assistance; no guarantee of Twitch view credit."""

    enabled: bool = False
    # Skip the minimise / hide-from-taskbar window treatment for Twitch pages,
    # when the user prefers to keep managed playback windows visible.
    force_visible: bool = True
    separate_windows: bool = False
    always_on_top: bool = False
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
    # Read the exact second balance in Twitch's points summary on a 30 second
    # cadence; only optional background reload behavior is gated by this.
    points_stall_refresh_enabled: bool = False
    # A conservative allocation policy, not a claim about a published Twitch
    # limit. Empty preserves shared-account multiview. Managed playback outside
    # the active allocation is skipped instead of opening a Guest window.
    points_priority_channels: list[str] = field(default_factory=list)
    auto_refresh: bool = False
    refresh_minutes_min: int = 45
    refresh_minutes_max: int = 75

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ViewerEngagementSettings:
        if not raw:
            return cls()
        valid = {field.name for field in fields(cls)}
        kwargs = {key: raw[key] for key in valid if key in raw}
        try:
            kwargs["points_priority_channels"] = normalize_points_priorities(
                raw.get("points_priority_channels", [])
            )
        except ValueError:
            kwargs["points_priority_channels"] = []
        return cls(**kwargs)

    def points_role(self, url: str) -> str:
        parsed = urlsplit(url)
        if (not self.enabled or not self.points_priority_channels
                or parsed.hostname not in {"twitch.tv", "www.twitch.tv"}):
            return "shared"
        name = parsed.path.strip("/").split("/")[0].lower()
        with _ACTIVE_POINTS_CHANNELS_LOCK:
            priorities = (
                self.points_priority_channels
                if _ACTIVE_POINTS_CHANNELS is None
                else _ACTIVE_POINTS_CHANNELS
            )
        return "priority" if name in priorities else "watch_only"

    def for_channel(self, url: str) -> ViewerEngagementSettings:
        if self.points_role(url) == "watch_only":
            return replace(self, claim_channel_points=False, points_stall_refresh_enabled=False)
        return self

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
