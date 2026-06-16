"""Anti-flap strike accounting for offline transitions."""

from __future__ import annotations

import logging
from typing import Any

from stream_monitor.monitor.types import (
    _OFFLINE_STRIKE_THRESHOLD,
    ChannelEntry,
)

logger = logging.getLogger(__name__)


class OfflineStrikesMixin:
    """Tracks consecutive 'not live' readings before committing an offline edge."""

    def _record_offline_miss(
        self,
        entry: ChannelEntry,
        live_key: str,
        prev_status: Any,
        *,
        label: str,
        reason: str,
    ) -> str:
        """Track a poll that failed to confirm LIVE. Caller must hold ``_lock``.

        Returns ``hold`` if the anti-flap guard absorbed the miss,
        ``commit`` if the strike threshold was reached, or ``noop`` when
        the channel was not previously live.
        """
        if prev_status is not True:
            return "noop"
        if self._wake_verify_mode:
            return "hold"
        strikes = self._offline_strikes.get(live_key, 0) + 1
        if strikes < _OFFLINE_STRIKE_THRESHOLD:
            self._offline_strikes[live_key] = strikes
            logger.info(
                "%s %s: ignoring transient offline reading (%d/%d) "
                "reason=%s prev_status=True kept=True",
                label,
                entry.key,
                strikes,
                _OFFLINE_STRIKE_THRESHOLD,
                reason,
            )
            return "hold"
        self._offline_strikes.pop(live_key, None)
        return "commit"
