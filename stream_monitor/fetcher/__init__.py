from __future__ import annotations

import threading

from stream_monitor.fetcher.base import StreamFetcher

_FETCHERS: dict[str, StreamFetcher] = {}
_FETCHER_LOCK = threading.Lock()


def get_fetcher(platform: str) -> StreamFetcher:
    """Return a cached StreamFetcher for *platform* (one instance per platform)."""
    platform = platform.lower().strip()

    with _FETCHER_LOCK:
        cached = _FETCHERS.get(platform)
        if cached is not None:
            return cached

        if platform == "twitch":
            from stream_monitor.fetcher.twitch import TwitchFetcher

            fetcher: StreamFetcher = TwitchFetcher()
        elif platform == "youtube":
            from stream_monitor.fetcher.youtube import YouTubeFetcher

            fetcher = YouTubeFetcher()
        else:
            raise ValueError(f"Unsupported platform: {platform!r}")

        _FETCHERS[platform] = fetcher
        return fetcher


__all__ = ["StreamFetcher", "get_fetcher"]
