"""Twitch 開播狀態爬蟲 — 透過非公開 GQL endpoint 判斷直播狀態。"""

from __future__ import annotations

import logging

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
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

_GQL_QUERY = """
query StreamStatus($login: String!) {
  user(login: $login) {
    stream {
      title
      type
      game {
        name
      }
    }
  }
}
"""


class TwitchFetcher(StreamFetcher):
    platform = "twitch"

    def _query(self, channel_name: str) -> dict | None:
        payload = {
            "query": _GQL_QUERY,
            "variables": {"login": channel_name.lower()},
        }
        try:
            resp = requests.post(
                _GQL_URL, json=payload, headers=_HEADERS, timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Twitch GQL request failed for %s: %s", channel_name, exc)
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
            return StreamInfo(
                channel=channel_name,
                platform="twitch",
                is_live=is_live,
                title=title,
                url=f"https://www.twitch.tv/{channel_name}",
            )
        except (KeyError, TypeError) as exc:
            logger.warning("Failed to parse Twitch response for %s: %s", channel_name, exc)
            return None
