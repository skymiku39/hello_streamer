"""Per-page media progress evidence; elapsed wall time is never watch credit."""

from __future__ import annotations

import math
from typing import Any

STALL_SECONDS = 90.0
MAX_SAMPLE_GAP_SECONDS = 65.0
RELOAD_LIMIT = 2


class PlaybackHealth:
    def __init__(self) -> None:
        self.reload_attempts = 0
        self.reset_samples()

    def reset_samples(self) -> None:
        self._sample: tuple[str, float, float] | None = None
        self._stationary_seconds = 0.0
        self._healthy_seconds = 0.0
        self._advances = 0

    def reloaded(self) -> None:
        self.reload_attempts += 1
        self.reset_samples()

    @property
    def can_reload(self) -> bool:
        return self.reload_attempts < RELOAD_LIMIT

    def observe(self, snapshot: Any, now: float, *, connected: bool) -> str:
        if not connected or not isinstance(snapshot, dict):
            self.reset_samples()
            return "unknown"
        if snapshot.get("hasVideo") and snapshot.get("paused"):
            self.reset_samples()
            return "paused"
        value = snapshot.get("currentTime")
        video_id = snapshot.get("videoId")
        if (not snapshot.get("playerPresent") or not snapshot.get("hasVideo")
                or snapshot.get("errorCode") or snapshot.get("offline")
                or snapshot.get("gatePresent") or snapshot.get("ended")
                or snapshot.get("mediaError") or not isinstance(video_id, str)
                or not video_id or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            self.reset_samples()
            return "unknown"
        sample = (video_id, float(value), now)
        previous = self._sample
        if previous is None:
            self._sample = sample
            return "verifying"
        gap = now - previous[2]
        if gap < 1.0:
            return "verifying"
        delta = sample[1] - previous[1]
        if previous[0] != video_id or gap > MAX_SAMPLE_GAP_SECONDS or delta < 0:
            self.reset_samples()
            self._sample = sample
            return "verifying"
        self._sample = sample
        if delta >= max(0.1, gap * 0.5) and delta <= gap * 2:
            self._stationary_seconds = 0.0
            self._healthy_seconds += gap
            self._advances += 1
            # A short post-reload success must not re-arm an endless reload loop.
            if self._healthy_seconds >= 60.0:
                self.reload_attempts = 0
            return "playing" if self._advances >= 2 else "verifying"
        self._healthy_seconds = 0.0
        self._advances = 0
        if 0 <= delta < 0.1:
            self._stationary_seconds += gap
            if self._stationary_seconds >= STALL_SECONDS:
                return "stalled"
        else:
            self._stationary_seconds = 0.0
        return "buffering"
