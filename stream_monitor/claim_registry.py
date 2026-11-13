"""Per-broadcast record of which Twitch channels already paid a confirmed claim.

A channel counts as "served" once a native, server-confirmed bonus claim landed
during its current broadcast. The signed-in slot allocator uses this so every
live channel gets at least one claim before an already-served channel keeps a
slot. Records live in memory only and are cleared when the broadcast ends.

A rising points balance is the only proof Twitch credited the watch, so a
holder whose balance rose ``POINTS_INCREASES_FOR_TURN`` times has had its turn
too; that frees the slot after roughly ten minutes instead of waiting for a
chest, which short broadcasts never reach.

Time alone never ends a turn. A holder whose balance has not moved for
``STALLED_SLOT_SECONDS`` is not being credited at all (page assist detached,
playback broken), so the allocator restarts the playback windows instead.
Only after ``STALL_RESTART_LIMIT`` restarts changed nothing does the holder
give up its turn, so one broken channel cannot starve the others all night.
Holders that already had their turn are watched the same way (2026-11-13:
kspksp went three hours without points after its turn); once their restarts
are spent they simply keep the slot their rank gives them. Any rise or claim
restores the restart allowance, since it proves the last restart worked.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable

_LOCK = threading.Lock()
_SERVED: set[str] = set()
# Passive credit arrives about every five minutes; twenty without any means
# the watch is not being counted.
STALLED_SLOT_SECONDS = 20 * 60.0
STALL_RESTART_LIMIT = 2
_STALL_RESTARTS: dict[str, int] = {}
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
            _STALL_RESTARTS.pop(name, None)
            if name in _SLOT_SINCE:
                _SLOT_SINCE[name] = time.monotonic()


def record_points_increase(url: str) -> None:
    """Count one observed rise of the channel's points balance."""
    name = channel_from_url(url)
    if name:
        with _LOCK:
            _POINTS_INCREASES[name] = _POINTS_INCREASES.get(name, 0) + 1
            _STALL_RESTARTS.pop(name, None)
            if name in _SLOT_SINCE:
                _SLOT_SINCE[name] = time.monotonic()


def is_served(name: str) -> bool:
    with _LOCK:
        return name.strip().lower() in _SERVED


def had_turn(name: str) -> bool:
    """True once the channel claimed, earned points a few times, or gave up."""
    key = name.strip().lower()
    with _LOCK:
        return (
            key in _SERVED
            or _POINTS_INCREASES.get(key, 0) >= POINTS_INCREASES_FOR_TURN
            or key in _TURN_USED
        )


def track_slot_holders(
    holders: Iterable[str], *, now: float | None = None
) -> set[str]:
    """Time the current slot holders; return those whose playback is stalled.

    A returned holder earned nothing for ``STALLED_SLOT_SECONDS`` and still
    has restarts left: the caller should restart the playback windows. Once
    the restarts are spent the holder gives up its turn instead.
    """
    current = time.monotonic() if now is None else now
    keys = {name.strip().lower() for name in holders}
    stalled: set[str] = set()
    with _LOCK:
        for key in list(_SLOT_SINCE):
            if key not in keys:
                del _SLOT_SINCE[key]
        for key in keys:
            since = _SLOT_SINCE.setdefault(key, current)
            if current - since < STALLED_SLOT_SECONDS:
                continue
            if _STALL_RESTARTS.get(key, 0) >= STALL_RESTART_LIMIT:
                # Restarts changed nothing. An unserved holder gives up its
                # turn; a served one already had it and keeps its rank.
                _TURN_USED.add(key)
                continue
            _STALL_RESTARTS[key] = _STALL_RESTARTS.get(key, 0) + 1
            stalled.add(key)
        if stalled:
            # The restart covers every window, so everyone starts a new wait.
            for key in keys:
                _SLOT_SINCE[key] = current
    return stalled


def clear_served(name: str) -> None:
    """Forget a channel's claim and turn when its broadcast ends."""
    key = name.strip().lower()
    with _LOCK:
        _SERVED.discard(key)
        _TURN_USED.discard(key)
        _SLOT_SINCE.pop(key, None)
        _POINTS_INCREASES.pop(key, None)
        _STALL_RESTARTS.pop(key, None)


def reset_for_tests() -> None:
    with _LOCK:
        _SERVED.clear()
        _TURN_USED.clear()
        _SLOT_SINCE.clear()
        _POINTS_INCREASES.clear()
        _STALL_RESTARTS.clear()
