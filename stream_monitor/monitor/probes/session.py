"""Lock-guarded state shared between the monitor and its platform probes."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from stream_monitor.monitor.types import OfflineInfo, _ProbeSnapshot


@dataclass
class ProbeSession:
    """The mutable session state a probe is allowed to read and update.

    All access must hold ``lock``. Centralising it here keeps the probe-facing
    surface explicit instead of scattering it across ``Monitor`` internals.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    last_status: dict[str, Any] = field(default_factory=dict)
    display_names: dict[str, str] = field(default_factory=dict)
    probe_snapshots: dict[str, _ProbeSnapshot] = field(default_factory=dict)
    offline_strikes: dict[str, int] = field(default_factory=dict)
    live_started_at: dict[str, str] = field(default_factory=dict)
    live_platform_started_at: set[str] = field(default_factory=set)
    live_payload: dict[str, OfflineInfo] = field(default_factory=dict)
    fallback_triggered_live: dict[str, str] = field(default_factory=dict)
    youtube_baselined: set[str] = field(default_factory=set)
    twitch_seen_live: set[str] = field(default_factory=set)
    pending_offline_events: list[tuple[Any, OfflineInfo]] = field(
        default_factory=list
    )
