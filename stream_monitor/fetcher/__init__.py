from __future__ import annotations

from stream_monitor.fetcher.base import StreamFetcher


def get_fetcher(platform: str) -> StreamFetcher:
    """Factory: return a concrete StreamFetcher for the given platform."""
    platform = platform.lower().strip()

    if platform == "twitch":
        from stream_monitor.fetcher.twitch import TwitchFetcher
        return TwitchFetcher()
    elif platform == "youtube":
        from stream_monitor.fetcher.youtube import YouTubeFetcher
        return YouTubeFetcher()
    else:
        raise ValueError(f"Unsupported platform: {platform!r}")


__all__ = ["StreamFetcher", "get_fetcher"]
