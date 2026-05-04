"""URL 解析器 — 從貼上的網址自動偵測平台與頻道名稱。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TWITCH_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?twitch\.tv/([A-Za-z0-9_]+)"),
]

_YOUTUBE_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/@([A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/channel/([A-Za-z0-9_\-]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/c/([A-Za-z0-9_.\-]+)"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/user/([A-Za-z0-9_.\-]+)"),
]


@dataclass
class ParsedChannel:
    platform: str
    name: str


def parse_url(text: str) -> ParsedChannel | None:
    """Try to extract platform + channel name from a URL string.

    Returns None if the text doesn't match any known pattern.
    """
    text = text.strip()

    for pat in _TWITCH_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).lower()
            if name not in {"directory", "downloads", "jobs", "p", "settings"}:
                return ParsedChannel(platform="twitch", name=name)

    for pat in _YOUTUBE_PATTERNS:
        m = pat.search(text)
        if m:
            return ParsedChannel(platform="youtube", name=m.group(1))

    return None
