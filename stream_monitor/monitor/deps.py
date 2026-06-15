"""Injectable dependencies for the monitor core (test patching entry point)."""

from __future__ import annotations

from stream_monitor.fetcher import get_fetcher

__all__ = ["get_fetcher"]
