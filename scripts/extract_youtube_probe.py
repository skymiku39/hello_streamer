"""One-shot: move YouTube probe methods from core.py into probes/youtube.py."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "stream_monitor" / "monitor" / "core.py"
OUT = ROOT / "stream_monitor" / "monitor" / "probes" / "youtube.py"

HEADER = '''"""YouTube tier-1 / tier-2 probe strategy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import deps as _monitor_deps
from stream_monitor.monitor.probes.host import ProbeHost
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _OFFLINE_STRIKE_THRESHOLD,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _sort_datetime,
    _utc_now_iso,
    _video_item_to_stream_info,
    _youtube_upcoming_is_usable,
    _ProbeSnapshot,
)

logger = logging.getLogger(__name__)


class YouTubePlatformProbe:
    """YouTube live probe, tier-2 refresh, and TIDUS-empty fallback path."""

    platform = "youtube"

'''

FOOTER = '''
    def finalize_tier1_probe(
        self,
        host: ProbeHost,
        entry: ChannelEntry,
        snap: _ProbeSnapshot,
    ) -> None:
        return None
'''


def main() -> None:
    lines = CORE.read_text(encoding="utf-8").splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.strip().startswith("def _probe_youtube(")
    )
    fallback_start = next(
        i
        for i, line in enumerate(lines)
        if line.strip().startswith("def _refresh_youtube_fallback(")
    )
    end = len(lines) - 1
    while end > fallback_start and lines[end].strip() == "":
        end -= 1
    block = "\n".join(lines[start : end + 1]) + "\n"

    replacements = [
        (
            "    def _probe_youtube(\n        self, entry: ChannelEntry, snap: _ProbeSnapshot",
            "    def probe_live(\n        self,\n        host: ProbeHost,\n        entry: ChannelEntry,\n        snap: _ProbeSnapshot",
        ),
        (
            "    def _refresh_youtube(\n        self, entry: ChannelEntry, snap: _ProbeSnapshot",
            "    def refresh_details(\n        self,\n        host: ProbeHost,\n        entry: ChannelEntry,\n        snap: _ProbeSnapshot",
        ),
        (
            "    def _probe_youtube_fallback_live(\n        self, entry: ChannelEntry, fetcher: Any, snap: _ProbeSnapshot",
            "    def _probe_fallback_live(\n        self,\n        host: ProbeHost,\n        entry: ChannelEntry,\n        fetcher: Any,\n        snap: _ProbeSnapshot",
        ),
        (
            "    def _refresh_youtube_fallback(\n        self, entry: ChannelEntry, snap: _ProbeSnapshot",
            "    def _refresh_fallback(\n        self,\n        host: ProbeHost,\n        entry: ChannelEntry,\n        snap: _ProbeSnapshot",
        ),
    ]
    for old, new in replacements:
        if old not in block:
            raise SystemExit(f"Missing expected signature: {old[:40]}...")
        block = block.replace(old, new)

    block = re.sub(r"\bself\.", "host.", block)
    block = block.replace("host._probe_youtube_fallback_live(", "self._probe_fallback_live(host, ")
    block = block.replace("host._refresh_youtube_fallback(", "self._refresh_fallback(host, ")

    # refresh_details must return commit
    if "return commit" not in block:
        block = block.replace(
            "host._publish_channel_preview(entry, from_probe=False)\n",
            "host._publish_channel_preview(entry, from_probe=False)\n        return commit\n",
            1,
        )

    OUT.write_text(HEADER + block + FOOTER, encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
