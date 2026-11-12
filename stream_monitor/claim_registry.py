"""Per-broadcast record of which Twitch channels already paid a confirmed claim.

A channel counts as "served" once a native, server-confirmed bonus claim landed
during its current broadcast. The signed-in slot allocator uses this so every
live channel gets at least one claim before an already-served channel keeps a
slot. Records live in memory only and are cleared when the broadcast ends.

A rising points balance is the only proof Twitch credited the watch, so a
holder whose balance rose ``POINTS_INCREASES_FOR_TURN`` times has had its turn
too; that frees the slot after roughly ten minutes instead of waiting for a
chest, which short broadcasts never reach.

Both signals can be missing for hours (page assist detached, Twitch paying
nothing). A holder that has watched for ``UNCLAIMED_TURN_SECONDS`` without
either has therefore used up its turn as well, so waiting channels still get
watched.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable

_LOCK = threading.Lock()
_SERVED: set[str] = set()
# Bonus chests appear roughly every 15 minutes of watching.
UNCLAIMED_TURN_SECONDS = 20 * 60.0
# Passive watch credit arrives about every five minutes.
POINTS_INCREASES_FOR_TURN = 2
_POINTS_INCREASES: dict[str, int] = {}
_SLOT_SINCE: dict[str, float] = {}
_TURN_USED: set[str] = set()
_CHANNEL_RE = re.compile(r"twitch\.tv/([^/?#\s]+)", re.IGNORECASE)


def channel_from_url(url: str) -> str:
    match = _CHANNEL_RE.search(url or "")
    return match.group(1).strip().lower() if match else ""


def record_confirmed_claim(url: str) -> None:
    name = channel_from_url(url)
    if name:
        with _LOCK:
            _SERVED.add(name)


def record_points_increase(url: str) -> None:
    """Count one observed rise of the channel's points balance."""
    name = channel_from_url(url)
    if name:
        with _LOCK:
            _POINTS_INCREASES[name] = _POINTS_INCREASES.get(name, 0) + 1


def is_served(name: str) -> bool:
    with _LOCK:
        return name.strip().lower() in _SERVED


def had_turn(name: str) -> bool:
    """True once the channel claimed, earned points a few times, or timed out."""
    key = name.strip().lower()
    with _LOCK:
        return (
            key in _SERVED
            or _POINTS_INCREASES.get(key, 0) >= POINTS_INCREASES_FOR_TURN
            or key in _TURN_USED
        )


def track_slot_holders(holders: Iterable[str], *, now: float | None = None) -> None:
    """Time the current slot holders; an overdue unclaimed one used its turn."""
    current = time.monotonic() if now is None else now
    keys = {name.strip().lower() for name in holders}
    with _LOCK:
        for key in list(_SLOT_SINCE):
            if key not in keys:
                del _SLOT_SINCE[key]
        for key in keys:
            since = _SLOT_SINCE.setdefault(key, current)
            if current - since >= UNCLAIMED_TURN_SECONDS:
                _TURN_USED.add(key)


def clear_served(name: str) -> None:
    """Forget a channel's claim and turn when its broadcast ends."""
    key = name.strip().lower()
    with _LOCK:
        _SERVED.discard(key)
        _TURN_USED.discard(key)
        _SLOT_SINCE.pop(key, None)
        _POINTS_INCREASES.pop(key, None)


def reset_for_tests() -> None:
    with _LOCK:
        _SERVED.clear()
        _TURN_USED.clear()
        _SLOT_SINCE.clear()
        _POINTS_INCREASES.clear()
