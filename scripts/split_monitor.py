"""One-off helper: split stream_monitor/monitor.py into a package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src_path = ROOT / "stream_monitor" / "monitor.py"
lines = src_path.read_text(encoding="utf-8").splitlines(keepends=True)

split_idx = next(i for i, line in enumerate(lines) if line.startswith("class Monitor"))

types_content = '''"""Monitor domain types, constants, and pure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.util import (
    channel_key,
    normalize_channel_name,
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)

''' + "".join(lines[28:split_idx])

core_header = '''"""Background polling scheduler and platform probe orchestration."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher import get_fetcher
from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.fetcher.youtube import YouTubeFetcher
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineCallback,
    OfflineInfo,
    PartialSnapshotCallback,
    PollActivityCallback,
    StatusCallback,
    _CONFIRMED_FUTURE_SLACK,
    _DB_CLEANUP_DAYS,
    _DEFAULT_MAX_CONCURRENT,
    _FETCH_FAILURE_REASONS,
    _MAINTENANCE_INTERVAL_S,
    _MIN_POLL_REST_S,
    _OFFLINE_STRIKE_THRESHOLD,
    _POST_RESUME_GAP_MULTIPLIER,
    _ProbeSnapshot,
    _STABLE_STATUS_LOG_EVERY,
    _STYLE_TO_STATUS,
    _channel_home_url,
    _entry_key_from_live_cache_key,
    _live_cache_key,
    _merge_offline_ended_at,
    _sort_datetime,
    _utc_now_iso,
    _video_item_to_stream_info,
    _youtube_upcoming_is_usable,
)
from stream_monitor.util import (
    channel_key,
    normalize_channel_name,
    parse_iso_datetime,
    youtube_upcoming_schedule_is_surfacable,
)

logger = logging.getLogger(__name__)

'''

init_content = '''"""Channel polling and status transition engine."""

from stream_monitor.monitor.core import Monitor
from stream_monitor.monitor.types import (
    ChannelEntry,
    ChannelStatus,
    OfflineInfo,
    _merge_offline_ended_at,
    _youtube_upcoming_is_usable,
)

__all__ = [
    "ChannelEntry",
    "ChannelStatus",
    "Monitor",
    "OfflineInfo",
    "_merge_offline_ended_at",
    "_youtube_upcoming_is_usable",
]
'''

pkg = ROOT / "stream_monitor" / "monitor"
pkg.mkdir(exist_ok=True)
(pkg / "types.py").write_text(types_content, encoding="utf-8", newline="\n")
(pkg / "core.py").write_text(core_header + "".join(lines[split_idx:]), encoding="utf-8", newline="\n")
(pkg / "__init__.py").write_text(init_content, encoding="utf-8", newline="\n")
src_path.unlink()
print(f"Split monitor.py at line {split_idx + 1}")
