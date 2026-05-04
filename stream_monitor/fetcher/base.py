"""StreamFetcher 抽象介面 — 策略模式基底類別。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StreamInfo:
    """Snapshot of a channel's live status."""

    channel: str
    platform: str
    is_live: bool
    title: str = ""
    url: str = ""
    display_name: str = ""


class StreamFetcher(ABC):
    """Abstract base for platform-specific stream status fetchers."""

    platform: str = ""

    @abstractmethod
    def is_live(self, channel_name: str) -> bool:
        """Return True if *channel_name* is currently streaming."""
        ...

    @abstractmethod
    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        """Return rich stream info, or None on failure."""
        ...
