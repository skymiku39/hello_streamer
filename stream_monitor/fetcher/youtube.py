"""YouTube 開播狀態爬蟲 — 骨架實作（預留）。"""

from __future__ import annotations

import logging

from stream_monitor.fetcher.base import StreamFetcher, StreamInfo

logger = logging.getLogger(__name__)


class YouTubeFetcher(StreamFetcher):
    platform = "youtube"

    def is_live(self, channel_name: str) -> bool:
        logger.info("YouTube fetcher is not yet implemented for %s", channel_name)
        return False

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        logger.info("YouTube fetcher is not yet implemented for %s", channel_name)
        return StreamInfo(
            channel=channel_name,
            platform="youtube",
            is_live=False,
            title="",
            url=f"https://www.youtube.com/@{channel_name}/live",
        )
