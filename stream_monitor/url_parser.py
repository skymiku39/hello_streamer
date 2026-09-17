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
_YOUTUBE_BARE_HANDLE_RE = re.compile(r"^@([^/\s@]+)$")


@dataclass
class ParsedChannel:
    platform: str
    name: str


def _parse_channel_url_syntax(text: str) -> ParsedChannel | None:
    """Parse a channel URL without doing I/O.

    This helper is deliberately pure because it is used by Tk key handlers and
    browser-profile path selection.  A YouTube ``/watch`` URL does not contain
    a channel identity in its syntax, so it is left unresolved here.
    """
    text = text.strip()
    if not text:
        return None

    bare_handle = _YOUTUBE_BARE_HANDLE_RE.match(text)
    if bare_handle:
        return ParsedChannel(platform="youtube", name=bare_handle.group(1))

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


def parse_channel_url(text: str) -> ParsedChannel | None:
    """Return a channel identity using syntax only; never access the network."""
    return _parse_channel_url_syntax(text)


def parse_url(text: str) -> ParsedChannel | None:
    """Try to extract platform + channel name from a URL string.

    Normal channel URLs are parsed locally.  For backwards compatibility,
    YouTube ``/watch?v=...`` URLs may still resolve their channel through the
    fetcher; callers handling user input or launch-time paths should use
    :func:`parse_channel_url` instead.
    """
    parsed = _parse_channel_url_syntax(text)
    if parsed is not None:
        return parsed

    text = text.strip()
    if not text:
        return None
    candidate = urlparse(text if "://" in text else f"https://{text}")
    if candidate.netloc.lower() not in {"youtube.com", "www.youtube.com"}:
        return None
    if candidate.path not in {"/watch", "/watch/"} and not candidate.path.startswith(
        "/watch"
    ):
        return None
    video_id = (parse_qs(candidate.query).get("v") or [""])[0].strip()
    if not video_id:
        return None

    from stream_monitor.fetcher.youtube import YouTubeFetcher

    resolved = YouTubeFetcher().resolve_channel_from_video(video_id)
    if resolved is not None:
        return ParsedChannel(platform="youtube", name=resolved[0])
    return None
