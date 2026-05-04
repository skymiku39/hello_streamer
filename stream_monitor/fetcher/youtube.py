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

_JSON_DECODER = json.JSONDecoder()

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

    def _parse_live_status(self, html: str) -> tuple[bool, str, str]:
        """Return (is_live, title, display_name) from the raw HTML."""
        player = self._extract_json_assignment(html, "ytInitialPlayerResponse")
        if not isinstance(player, dict):
            return False, "", ""

        video_details = player.get("videoDetails", {})
        title = video_details.get("title", "") if isinstance(video_details, dict) else ""
        display_name = (
            video_details.get("author", "") if isinstance(video_details, dict) else ""
        )

        playability = player.get("playabilityStatus", {})
        if isinstance(playability, dict):
            status = playability.get("status")
            if status in {"LIVE_STREAM_OFFLINE", "UNPLAYABLE"}:
                return False, title, display_name

        microformat = player.get("microformat", {})
        renderer = {}
        if isinstance(microformat, dict):
            renderer = microformat.get("playerMicroformatRenderer", {}) or {}
        if not display_name and isinstance(renderer, dict):
            display_name = renderer.get("ownerChannelName", "")
        live_details = {}
        if isinstance(renderer, dict):
            live_details = renderer.get("liveBroadcastDetails", {}) or {}
        if isinstance(live_details, dict) and live_details.get("isLiveNow") is False:
            return False, title, display_name

        if isinstance(live_details, dict) and live_details.get("isLiveNow") is True:
            return True, title, display_name

        if isinstance(video_details, dict) and video_details.get("isLive") is True:
            return True, title, display_name

        return False, title, display_name

    def _extract_json_assignment(self, html: str, var_name: str) -> object | None:
        """Extract a JSON object assigned to a JavaScript variable."""
        pattern = re.compile(rf"(?:var\s+)?{re.escape(var_name)}\s*=\s*", re.DOTALL)
        match = pattern.search(html)
        if not match:
            return None

        try:
            value, _end = _JSON_DECODER.raw_decode(html[match.end() :])
        except json.JSONDecodeError:
            return None
        return value

    def is_live(self, channel_name: str) -> bool:
        html = self._fetch_page(channel_name)
        if html is None:
            return False
        is_live, _title, _display_name = self._parse_live_status(html)
        return is_live

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if channel_name.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_name}/live"
        else:
            url = f"https://www.youtube.com/@{channel_name}/live"

        html = self._fetch_page(channel_name)
        if html is None:
            return None

        is_live, title, display_name = self._parse_live_status(html)
        return StreamInfo(
            channel=channel_name,
            platform="youtube",
            is_live=is_live,
            title=title,
            url=url,
            display_name=display_name,
        )
