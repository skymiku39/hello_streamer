"""Persist and restore the last-known channel status across app restarts.

On a fresh launch the channel list used to render blank ("--") until the first
poll finished. A previously-live channel therefore showed nothing for the first
cycle, and — because the monitor started with no cached status — a still-running
stream looked like a brand-new went-live edge (re-opening a player the user was
already watching).

This module snapshots the latest per-channel status into ``config.json`` so the
next launch can repaint the rows immediately (marked "pending verification") and
seed the monitor, which suppresses those spurious edges. Elapsed/countdown
timers are recomputed from the persisted ISO timestamps, so the UI shows how
long a stream has been live (or how long ago it ended) relative to *now*.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from stream_monitor.domain import ChannelStatus
from stream_monitor.util import parse_iso_datetime

logger = logging.getLogger(__name__)

CACHE_CONFIG_KEY = "channel_status_cache"

_VALID_STATES = ("live", "upcoming", "offline")


def _status_state(status: Any) -> str | None:
    """Map a ChannelStatus / primitive into a serializable state token."""
    value = status.status if isinstance(status, ChannelStatus) else status
    if value is True or value == "live":
        return "live"
    if value == "upcoming":
        return "upcoming"
    if value is False or value == "offline":
        return "offline"
    return None


def serialize_status(status: Any) -> dict[str, Any] | None:
    """Serialize one status into a JSON-safe dict, or ``None`` to skip it."""
    state = _status_state(status)
    if state is None:
        return None
    data: dict[str, Any] = {"state": state}
    if isinstance(status, ChannelStatus):
        optional = {
            "url": status.url,
            "title": status.title,
            "started_at": status.started_at,
            "ended_at": status.ended_at,
            "scheduled_start": status.scheduled_start,
            "vod_url": status.vod_url,
            "upcoming_url": status.upcoming_url,
            "ended_at_source": status.ended_at_source,
        }
        for field, value in optional.items():
            if value:
                data[field] = value
    return data


def build_cache(
    statuses: dict[str, Any],
    display_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the on-disk cache payload from a ``{key: status}`` mapping."""
    channels: dict[str, Any] = {}
    names = display_names or {}
    for key, status in statuses.items():
        data = serialize_status(status)
        if data is None:
            continue
        name = (names.get(key) or "").strip()
        if name:
            data["display_name"] = name
        channels[key] = data
    return {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "channels": channels,
    }


def _deserialize_status(data: dict[str, Any]) -> ChannelStatus | None:
    state = data.get("state")
    if state not in _VALID_STATES:
        return None
    if state == "live":
        status: bool | str = True
    elif state == "upcoming":
        status = "upcoming"
    else:
        status = False
    return ChannelStatus(
        status=status,
        url=str(data.get("url", "")),
        title=str(data.get("title", "")),
        started_at=str(data.get("started_at", "")),
        ended_at=str(data.get("ended_at", "")),
        scheduled_start=str(data.get("scheduled_start", "")),
        vod_url=str(data.get("vod_url", "")),
        upcoming_url=str(data.get("upcoming_url", "")),
        ended_at_source=str(data.get("ended_at_source", "")),
    )


def restore_statuses(cache: Any) -> dict[str, ChannelStatus]:
    """Rebuild ``{key: ChannelStatus}`` from a stored cache payload."""
    channels = cache.get("channels") if isinstance(cache, dict) else None
    if not isinstance(channels, dict):
        return {}
    restored: dict[str, ChannelStatus] = {}
    for key, data in channels.items():
        if not isinstance(key, str) or not isinstance(data, dict):
            continue
        status = _deserialize_status(data)
        if status is not None:
            restored[key] = status
    return restored


def restore_display_names(cache: Any) -> dict[str, str]:
    """Pull any persisted display names so restored rows show real names."""
    channels = cache.get("channels") if isinstance(cache, dict) else None
    if not isinstance(channels, dict):
        return {}
    names: dict[str, str] = {}
    for key, data in channels.items():
        if not isinstance(key, str) or not isinstance(data, dict):
            continue
        raw = data.get("display_name")
        name = raw.strip() if isinstance(raw, str) else ""
        if name:
            names[key] = name
    return names


def saved_at_epoch(cache: Any) -> float:
    """Return the cache's ``saved_at`` as a POSIX timestamp (0.0 if absent)."""
    if not isinstance(cache, dict):
        return 0.0
    dt = parse_iso_datetime(str(cache.get("saved_at", "")))
    return dt.timestamp() if dt is not None else 0.0
