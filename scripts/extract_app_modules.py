"""Extract ChannelRow from app.py into channel_row.py."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app_path = ROOT / "stream_monitor" / "app.py"
lines = app_path.read_text(encoding="utf-8").splitlines(keepends=True)

# Find ChannelRow block (line 90 class ChannelRow through line before Main App)
start = next(i for i, line in enumerate(lines) if line.startswith("class ChannelRow"))
end = next(i for i, line in enumerate(lines) if line.startswith("class App("))

channel_row_header = '''"""Single channel row widget for the main channel list."""

from __future__ import annotations

import logging
from typing import Any, Callable

import customtkinter as ctk

from stream_monitor import i18n
from stream_monitor.app_ui import (
    _CLR_CARD,
    _CLR_CARD_DISABLED,
    _CLR_DELETE_HOVER,
    _CLR_LINK_HOVER,
    _CLR_TEXT_DISABLED,
    _CLR_TWITCH,
    _CLR_YOUTUBE,
    _font,
    _format_countdown,
    _format_elapsed,
    _format_row_time,
    _status_row_label_width,
    _tooltip,
    _tooltip_tr,
)
from stream_monitor.i18n import tr
from stream_monitor.monitor import ChannelStatus
from stream_monitor.notifier import open_url
from stream_monitor.util import channel_key, channel_page_url

logger = logging.getLogger(__name__)


def is_live_state(state: bool | str | None) -> bool:
    return state is True or state == "live"


'''

channel_row_body = "".join(lines[start:end])
channel_row_body = channel_row_body.replace("_is_live_state", "is_live_state")

(ROOT / "stream_monitor" / "channel_row.py").write_text(
    channel_row_header + channel_row_body, encoding="utf-8", newline="\n"
)

# Rewrite app.py: remove _is_live_state and ChannelRow, add import
new_app_lines = lines[:79]  # through imports/logger, before _is_live_state
import_line = "from stream_monitor.channel_row import ChannelRow, is_live_state\n"
# Insert after monitor import
inserted = False
app_without_row: list[str] = []
for line in lines[:start]:
    app_without_row.append(line)
    if line.startswith("from stream_monitor.monitor import"):
        app_without_row.append(import_line)
        inserted = True
if not inserted:
    app_without_row.append(import_line)
app_without_row.extend(lines[end:])

# Remove _is_live_state function block (lines 80-81)
filtered: list[str] = []
skip = False
for line in app_without_row:
    if line.startswith("def _is_live_state"):
        skip = True
        continue
    if skip:
        if line.startswith("ctk.set_appearance_mode"):
            skip = False
            filtered.append(line)
        continue
    filtered.append(line)

# Replace _is_live_state calls in App if any - ChannelRow uses is_live_state now
app_content = "".join(filtered).replace("_is_live_state", "is_live_state")
app_path.write_text(app_content, encoding="utf-8", newline="\n")
print(f"Extracted ChannelRow lines {start + 1}-{end}")
