"""Twitch 開播狀態爬蟲 — 透過非公開 GQL endpoint 判斷直播狀態。

使用 Twitch 網頁版的公用 Client-ID 搭配完整的瀏覽器 Headers 偽裝，
繞過 Cloudflare 基本防護。輪詢間隔應保持 ≥30 秒以避免觸發 Rate Limit。
"""

from __future__ import annotations

import logging
import time

import requests

from stream_monitor.fetcher.base import StreamFetcher, StreamInfo

logger = logging.getLogger(__name__)

_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_GQL_URL = "https://gql.twitch.tv/gql"

_HEADERS = {
    "Client-ID": _CLIENT_ID,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.twitch.tv",
    "Referer": "https://www.twitch.tv/",
    "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="8"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

_GQL_QUERY = """
query StreamStatus($login: String!) {
      user(login: $login) {
    displayName
    stream {
      title
      type
      createdAt
      viewersCount
      game {
        name
      }
    }
  }
}
"""

_ARCHIVE_QUERY = """
query LatestArchive($login: String!) {
  user(login: $login) {
    videos(first: 1, type: ARCHIVE) {
      edges {
        node {
          id
        }
      }
    }
  }
}
"""

_MAX_RETRIES = 2
_RETRY_DELAY = 3


class TwitchFetcher(StreamFetcher):
    platform = "twitch"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def _gql(self, query: str, variables: dict[str, str]) -> dict | None:
        payload = {"query": query, "variables": variables}
        channel_name = variables.get("login", "")
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.post(_GQL_URL, json=payload, timeout=10)
                if resp.status_code == 403:
                    logger.warning(
                        "Twitch returned 403 for %s (attempt %d/%d), "
                        "possible Cloudflare block",
                        channel_name, attempt + 1, _MAX_RETRIES + 1,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(_RETRY_DELAY)
                        continue
                    return None
                if resp.status_code >= 500:
                    logger.warning(
                        "Twitch server error %d for %s (attempt %d/%d)",
                        resp.status_code, channel_name,
                        attempt + 1, _MAX_RETRIES + 1,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(_RETRY_DELAY)
                        continue
                    return None
                resp.raise_for_status()
                data = resp.json()
                if "errors" in data:
                    logger.warning("Twitch GQL errors for %s: %s", channel_name, data["errors"])
                return data
            except requests.Timeout:
                logger.warning("Twitch request timed out for %s (attempt %d)", channel_name, attempt + 1)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
            except requests.RequestException as exc:
                logger.warning("Twitch request failed for %s: %s", channel_name, exc)
                return None
            except ValueError as exc:
                logger.warning("Invalid JSON from Twitch for %s: %s", channel_name, exc)
                return None
        return None

    def _query(self, channel_name: str) -> dict | None:
        return self._gql(_GQL_QUERY, {"login": channel_name.lower()})

    def get_latest_archive_url(self, channel_name: str) -> str | None:
        """Return the most recent VOD URL for *channel_name*, if available."""
        data = self._gql(_ARCHIVE_QUERY, {"login": channel_name.lower()})
        if data is None:
            return None
        try:
            edges = data["data"]["user"]["videos"]["edges"]
            if not edges:
                return None
            node = edges[0]["node"]
            video_id = node.get("id")
            if not video_id:
                return None
            return f"https://www.twitch.tv/videos/{video_id}"
        except (KeyError, TypeError, IndexError) as exc:
            logger.warning(
                "Failed to parse Twitch archive response for %s: %s",
                channel_name,
                exc,
            )
            return None

    def is_live(self, channel_name: str) -> bool:
        data = self._query(channel_name)
        if data is None:
            return False
        try:
            stream = data["data"]["user"]["stream"]
            return stream is not None and stream.get("type") == "live"
        except (KeyError, TypeError):
            return False

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        data = self._query(channel_name)
        if data is None:
            return None
        try:
            user = data["data"]["user"]
            if user is None:
                return None
            stream = user.get("stream")
            is_live = stream is not None and stream.get("type") == "live"
            title = stream.get("title", "") if stream else ""
            started_at = stream.get("createdAt", "") if stream else ""
            return StreamInfo(
                channel=channel_name,
                platform="twitch",
                is_live=is_live,
                title=title,
                url=f"https://www.twitch.tv/{channel_name}",
                display_name=user.get("displayName", ""),
                started_at=started_at,
            )
        except (KeyError, TypeError) as exc:
            logger.warning("Failed to parse Twitch response for %s: %s", channel_name, exc)
            return None
