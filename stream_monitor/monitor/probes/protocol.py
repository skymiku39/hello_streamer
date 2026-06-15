"""Platform probe strategy protocol and shared types (OCP)."""

from __future__ import annotations

from typing import Callable, Protocol

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor.probes.host import ProbeHost
from stream_monitor.monitor.types import ChannelEntry, _ProbeSnapshot

CommitFn = Callable[[], None]


class PlatformProbe(Protocol):
    """Strategy for tier-1 live probe and tier-2 detail refresh."""

    platform: str

    def probe_live(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> list[tuple[ChannelEntry, StreamInfo]]: ...

    def refresh_details(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> CommitFn: ...

    def finalize_tier1_probe(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> None: ...
