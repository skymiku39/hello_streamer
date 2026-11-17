"""Log level for "already marked live" suppressions.

Every poll of every live channel reaches the suppression branch; at INFO it was
a third of the deployed log (11/17) and drove log rotation. The README still
uses the line to diagnose missed go-live actions, so the first suppression of
each broadcast stays at INFO and the repeats drop to DEBUG.
"""

from __future__ import annotations

import logging
import threading

_lock = threading.Lock()
_logged_broadcast: dict[str, str] = {}


def suppressed_log_level(key: str, started_at: str | None) -> int:
    """INFO for the first suppression of this broadcast, DEBUG afterwards."""
    broadcast = started_at or ""
    with _lock:
        if _logged_broadcast.get(key) == broadcast:
            return logging.DEBUG
        _logged_broadcast[key] = broadcast
        return logging.INFO
