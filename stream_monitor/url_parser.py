"""URL 解析器 — 從貼上的網址自動偵測平台與頻道名稱。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

_TWITCH_PATTERNS = [
    re.compile(r"^/([A-Za-z0-9_]+)(?:/.*)?$"),
]

_YOUTUBE_PATTERNS = [
    re.compile(r"^/channel/([A-Za-z0-9_\-]+)/?$"),
    re.compile(r"^/c/([A-Za-z0-9_.\-]+)/?$"),
    re.compile(r"^/user/([A-Za-z0-9_.\-]+)/?$"),
]
_YOUTUBE_HANDLE_RE = re.compile(r"/@([^/]+)")


@dataclass
class ParsedChannel:
    platform: str
    name: str


def parse_url(text: str) -> ParsedChannel | None:
    """Try to extract platform + channel name from a URL string.

    Returns None if the text doesn't match any known pattern.
    """
    text = text.strip()
    if not text:
        return None

    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = parsed.netloc.lower()
    path = parsed.path

    if host in {"twitch.tv", "www.twitch.tv"}:
        for pat in _TWITCH_PATTERNS:
            m = pat.match(path)
            if m:
                name = m.group(1).lower()
                if name not in {"directory", "downloads", "jobs", "p", "settings"}:
                    return ParsedChannel(platform="twitch", name=name)
        return None

    if host in {"youtube.com", "www.youtube.com"}:
        if path in {"/watch", "/watch/"} or path.startswith("/watch"):
            video_id = (parse_qs(parsed.query).get("v") or [""])[0].strip()
            if video_id:
                from stream_monitor.fetcher.youtube import YouTubeFetcher

                resolved = YouTubeFetcher().resolve_channel_from_video(video_id)
                if resolved is not None:
                    return ParsedChannel(platform="youtube", name=resolved[0])
            return None

        decoded_path = unquote(path)
        handle_match = _YOUTUBE_HANDLE_RE.search(decoded_path)
        if handle_match:
            return ParsedChannel(platform="youtube", name=handle_match.group(1))

        for pat in _YOUTUBE_PATTERNS:
            m = pat.match(path)
            if m:
                return ParsedChannel(platform="youtube", name=m.group(1))

    return None
