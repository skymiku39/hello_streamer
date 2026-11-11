"""Per-broadcast record of which Twitch channels already paid a confirmed claim.

A channel counts as "served" once a native, server-confirmed bonus claim landed
during its current broadcast. The signed-in slot allocator uses this so every
live channel gets at least one claim before an already-served channel keeps a
slot. Records live in memory only and are cleared when the broadcast ends.
"""

from __future__ import annotations

import re
import threading

_LOCK = threading.Lock()
_SERVED: set[str] = set()
_CHANNEL_RE = re.compile(r"twitch\.tv/([^/?#\s]+)", re.IGNORECASE)


def channel_from_url(url: str) -> str:
    match = _CHANNEL_RE.search(url or "")
    return match.group(1).strip().lower() if match else ""


def record_confirmed_claim(url: str) -> None:
    name = channel_from_url(url)
    if name:
        with _LOCK:
            _SERVED.add(name)


def is_served(name: str) -> bool:
    with _LOCK:
        return name.strip().lower() in _SERVED


def clear_served(name: str) -> None:
    """Forget a channel's claim when its broadcast ends."""
    with _LOCK:
        _SERVED.discard(name.strip().lower())


def reset_for_tests() -> None:
    with _LOCK:
        _SERVED.clear()
