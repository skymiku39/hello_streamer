"""Tests for platform probe registry (OCP)."""

from __future__ import annotations

import pytest

from stream_monitor.monitor.probes import (
    TwitchPlatformProbe,
    YouTubePlatformProbe,
    get_platform_probe,
    register_platform_probe,
)


def test_get_platform_probe_returns_twitch_and_youtube() -> None:
    assert get_platform_probe("twitch").platform == "twitch"
    assert get_platform_probe("YouTube").platform == "youtube"


def test_get_platform_probe_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="Unsupported platform"):
        get_platform_probe("kick")


def test_register_platform_probe_allows_override() -> None:
    class DummyProbe:
        platform = "dummy"

        def probe_live(self, host, entry, snap):
            return []

        def refresh_details(self, host, entry, snap):
            return lambda: None

    register_platform_probe("dummy", DummyProbe())  # type: ignore[arg-type]
    try:
        assert get_platform_probe("dummy").platform == "dummy"
    finally:
        register_platform_probe("twitch", TwitchPlatformProbe())
        register_platform_probe("youtube", YouTubePlatformProbe())
