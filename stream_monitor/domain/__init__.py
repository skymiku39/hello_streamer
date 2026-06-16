"""Neutral domain types shared across monitor, events, and UI layers."""

from stream_monitor.domain.channel import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
)

__all__ = [
    "ChannelEntry",
    "ChannelStatus",
    "OfflineInfo",
]
