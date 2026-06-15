"""Platform probe registry (OCP — register new platforms without editing Monitor)."""

from __future__ import annotations

from stream_monitor.monitor.probes.protocol import PlatformProbe
from stream_monitor.monitor.probes.twitch import TwitchPlatformProbe
from stream_monitor.monitor.probes.youtube import YouTubePlatformProbe

_REGISTRY: dict[str, PlatformProbe] = {
    "twitch": TwitchPlatformProbe(),
    "youtube": YouTubePlatformProbe(),
}


def get_platform_probe(platform: str) -> PlatformProbe:
    key = platform.lower().strip()
    probe = _REGISTRY.get(key)
    if probe is None:
        raise ValueError(f"Unsupported platform: {platform!r}")
    return probe


def register_platform_probe(platform: str, probe: PlatformProbe) -> None:
    """Register or replace a platform probe (for tests and future platforms)."""
    _REGISTRY[platform.lower().strip()] = probe
