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


def clear_fetcher_cache() -> None:
    """Drop cached fetchers (for tests)."""
    with _FETCHER_LOCK:
        _FETCHERS.clear()


__all__ = ["StreamFetcher", "clear_fetcher_cache", "get_fetcher"]
