"""Per-broadcast record of which Twitch channels already paid a confirmed claim.

A channel counts as "served" once a native, server-confirmed bonus claim landed
during its current broadcast. The signed-in slot allocator uses this so every
live channel gets at least one claim before an already-served channel keeps a
slot. Records live in memory only and are cleared when the broadcast ends.

A rising points balance is the only proof Twitch credited the watch, so a
holder whose balance rose ``POINTS_INCREASES_FOR_TURN`` times has had its turn
too; that frees the slot after roughly ten minutes instead of waiting for a
chest, which short broadcasts never reach.

Neither proof ends the turn before the channel held a slot for
``MIN_TURN_WATCH_SECONDS``. Twitch offers a chest to a viewer the moment they
arrive, so a claim can land seconds after the window opens (2026-11-14:
jipandan claimed 21 s after opening, lost its slot at once and was never
watched again for the remaining 77 minutes of the broadcast).

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
# Two passive credits take about ten minutes; an arrival chest takes seconds.
MIN_TURN_WATCH_SECONDS = 10 * 60.0
_WATCHED_SECONDS: dict[str, float] = {}
_LAST_TRACK: float | None = None
_LAST_HOLDERS: set[str] = set()
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
    """True once the channel gave up, or was credited after a real watch.

    Credited means a confirmed claim or a few points rises; a real watch means
    ``MIN_TURN_WATCH_SECONDS`` in a slot during this broadcast.
    """
    key = name.strip().lower()
    with _LOCK:
        if key in _TURN_USED:
            return True
        credited = (
            key in _SERVED
            or _POINTS_INCREASES.get(key, 0) >= POINTS_INCREASES_FOR_TURN
        )
        return credited and _WATCHED_SECONDS.get(key, 0.0) >= MIN_TURN_WATCH_SECONDS


def credit_watch_seconds(name: str, seconds: float) -> None:
    """Add slot time for a channel (tests, or callers that timed it themselves)."""
    key = name.strip().lower()
    if key and seconds > 0:
        with _LOCK:
            _WATCHED_SECONDS[key] = _WATCHED_SECONDS.get(key, 0.0) + seconds


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
    global _LAST_TRACK, _LAST_HOLDERS
    with _LOCK:
        if _LAST_TRACK is not None and current > _LAST_TRACK:
            # Only channels holding a slot at both ends of the interval watched it.
            for key in keys & _LAST_HOLDERS:
                _WATCHED_SECONDS[key] = (
                    _WATCHED_SECONDS.get(key, 0.0) + current - _LAST_TRACK
                )
        _LAST_TRACK = current
        _LAST_HOLDERS = set(keys)
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
        _WATCHED_SECONDS.pop(key, None)


def reset_for_tests() -> None:
    global _LAST_TRACK, _LAST_HOLDERS
    with _LOCK:
        _WATCHED_SECONDS.clear()
        _LAST_TRACK = None
        _LAST_HOLDERS = set()
        _SERVED.clear()
        _TURN_USED.clear()
        _SLOT_SINCE.clear()
        _POINTS_INCREASES.clear()
        _STALL_RESTARTS.clear()
