"""Platform-specific live probe strategies."""

from stream_monitor.monitor.probes.host import ProbeHost
from stream_monitor.monitor.probes.protocol import PlatformProbe
from stream_monitor.monitor.probes.registry import (
    get_platform_probe,
    register_platform_probe,
)
from stream_monitor.monitor.probes.twitch import TwitchPlatformProbe
from stream_monitor.monitor.probes.youtube import YouTubePlatformProbe

__all__ = [
    "PlatformProbe",
    "ProbeHost",
    "TwitchPlatformProbe",
    "YouTubePlatformProbe",
    "get_platform_probe",
    "register_platform_probe",
]
