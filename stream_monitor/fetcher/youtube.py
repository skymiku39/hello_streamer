"""YouTube 開播狀態爬蟲 — 透過 /@channel/live 頁面的內嵌 JSON 判斷直播狀態。

YouTube 網頁使用 CSR，但會在 HTML 原始碼中注入 ytInitialPlayerResponse
和 ytInitialData JSON 變數。透過 Regex 抽取這些 JSON 並解析 isLive 等欄位，
即可在不使用 Selenium/Playwright 的前提下判斷直播狀態。
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

from stream_monitor.fetcher.base import StreamFetcher, StreamInfo

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_PLAYER_RESPONSE_RE = re.compile(
    r"var\s+ytInitialPlayerResponse\s*=\s*(\{.*?\})\s*;", re.DOTALL
)
_INITIAL_DATA_RE = re.compile(
    r"var\s+ytInitialData\s*=\s*(\{.*?\})\s*;", re.DOTALL
)

_MAX_RETRIES = 2
_RETRY_DELAY = 3


class YouTubeFetcher(StreamFetcher):
    platform = "youtube"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def _fetch_page(self, channel_name: str) -> str | None:
        if channel_name.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_name}/live"
        else:
            url = f"https://www.youtube.com/@{channel_name}/live"

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 404:
                    logger.warning("YouTube channel not found: %s", channel_name)
                    return None
                resp.raise_for_status()
                return resp.text
            except requests.Timeout:
                logger.warning(
                    "YouTube request timed out for %s (attempt %d)",
                    channel_name, attempt + 1,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
            except requests.RequestException as exc:
                logger.warning("YouTube request failed for %s: %s", channel_name, exc)
                return None
        return None

    def _parse_live_status(self, html: str) -> tuple[bool, str]:
        """Return (is_live, title) from the raw HTML."""
        # Strategy 1: ytInitialPlayerResponse
        m = _PLAYER_RESPONSE_RE.search(html)
        if m:
            try:
                player = json.loads(m.group(1))
                video_details = player.get("videoDetails", {})
                is_live = video_details.get("isLive", False) is True
                title = video_details.get("title", "")
                if is_live:
                    return True, title
            except (json.JSONDecodeError, KeyError):
                pass

        # Strategy 2: simple text markers in ytInitialData
        if '"isLive":true' in html or '"status":"LIVE"' in html:
            title = self._extract_title_from_initial_data(html)
            return True, title

        # Strategy 3: ytInitialData deep parse
        m2 = _INITIAL_DATA_RE.search(html)
        if m2:
            try:
                data = json.loads(m2.group(1))
                title = self._walk_for_live(data)
                if title is not None:
                    return True, title
            except (json.JSONDecodeError, KeyError):
                pass

        return False, ""

    def _extract_title_from_initial_data(self, html: str) -> str:
        m = _INITIAL_DATA_RE.search(html)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
            tabs = (
                data.get("contents", {})
                .get("twoColumnWatchNextResults", {})
                .get("results", {})
                .get("results", {})
                .get("contents", [])
            )
            for item in tabs:
                primary = item.get("videoPrimaryInfoRenderer", {})
                title_runs = primary.get("title", {}).get("runs", [])
                if title_runs:
                    return title_runs[0].get("text", "")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return ""

    def _walk_for_live(self, obj: dict | list, depth: int = 0) -> str | None:
        """Recursively look for live badges in ytInitialData."""
        if depth > 12:
            return None
        if isinstance(obj, dict):
            badges = obj.get("badges", [])
            for badge in badges if isinstance(badges, list) else []:
                lbl = (
                    badge.get("metadataBadgeRenderer", {})
                    .get("label", "")
                    .upper()
                )
                if "LIVE" in lbl:
                    title_data = obj.get("title", {})
                    if isinstance(title_data, dict):
                        runs = title_data.get("runs", [])
                        if runs:
                            return runs[0].get("text", "")
                        return title_data.get("simpleText", "")
                    return ""
            for v in obj.values():
                result = self._walk_for_live(v, depth + 1)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._walk_for_live(item, depth + 1)
                if result is not None:
                    return result
        return None

    def is_live(self, channel_name: str) -> bool:
        html = self._fetch_page(channel_name)
        if html is None:
            return False
        is_live, _ = self._parse_live_status(html)
        return is_live

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if channel_name.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_name}/live"
        else:
            url = f"https://www.youtube.com/@{channel_name}/live"

        html = self._fetch_page(channel_name)
        if html is None:
            return StreamInfo(
                channel=channel_name,
                platform="youtube",
                is_live=False,
                title="",
                url=url,
            )

        is_live, title = self._parse_live_status(html)
        return StreamInfo(
            channel=channel_name,
            platform="youtube",
            is_live=is_live,
            title=title,
            url=url,
        )
