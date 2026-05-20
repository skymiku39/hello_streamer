"""YouTube 開播狀態爬蟲 — 透過 /@channel/streams 的 ytInitialData 追蹤直播、待機室與新影片。

主要流程 (TIDUS 架構):
  1. 抓取 /@channel/streams 頁面
  2. 從 ytInitialData 提取所有 videoRenderer 項目
  3. 每個項目透過 thumbnailOverlayTimeStatusRenderer.style 判斷狀態
     - LIVE: 正在直播
     - UPCOMING: 待機室 (含 startTime)
     - DEFAULT: 一般影片 / 已結束的直播重播

舊的 /@channel/live + ytInitialPlayerResponse 保留為 fallback。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import NamedTuple

import requests

from stream_monitor.fetcher.base import StreamFetcher, StreamInfo, VideoItem

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
_WATCH_DETAILS_CACHE_TTL = 300


class _WatchDetails(NamedTuple):
    scheduled_start: str = ""
    started_at: str = ""


def _channel_url(channel_name: str) -> str:
    if channel_name.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel_name}"
    return f"https://www.youtube.com/@{channel_name}"


def _unix_to_iso(ts: str) -> str:
    """Convert a Unix timestamp string to ISO 8601."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ""


class YouTubeFetcher(StreamFetcher):
    platform = "youtube"
    _watch_details_cache: dict[str, tuple[float, _WatchDetails]] = {}

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._session.cookies.set("CONSENT", "PENDING+987", domain=".youtube.com")
        self._session.cookies.set(
            "SOCS", "CAESEwgDEgk2NDcwMTcxMjQaAmVuIAEaBgiA_LyaBg",
            domain=".youtube.com",
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _fetch_page(self, url: str) -> str | None:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 404:
                    logger.warning("YouTube page not found: %s", url)
                    return None
                if resp.status_code >= 500:
                    logger.warning(
                        "YouTube server error %d for %s (attempt %d/%d)",
                        resp.status_code, url,
                        attempt + 1, _MAX_RETRIES + 1,
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(_RETRY_DELAY)
                        continue
                    return None
                resp.raise_for_status()
                return resp.text
            except requests.Timeout:
                logger.warning(
                    "YouTube request timed out for %s (attempt %d)",
                    url,
                    attempt + 1,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
            except requests.RequestException as exc:
                logger.warning("YouTube request failed for %s: %s", url, exc)
                return None
        return None

    def _extract_json_var(self, html: str, var_name: str) -> object | None:
        pattern = re.compile(rf"(?:var\s+)?{re.escape(var_name)}\s*=\s*", re.DOTALL)
        match = pattern.search(html)
        if not match:
            return None
        try:
            value, _end = _JSON_DECODER.raw_decode(html[match.end() :])
        except json.JSONDecodeError:
            return None
        return value

    # ------------------------------------------------------------------
    # TIDUS: ytInitialData channel items
    # ------------------------------------------------------------------
    def _parse_channel_items(
        self, data: dict, channel_name: str
    ) -> tuple[list[VideoItem], str]:
        """Extract VideoItems and display_name from ytInitialData."""
        display_name = ""
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            renderer = metadata.get("channelMetadataRenderer", {})
            if isinstance(renderer, dict):
                display_name = renderer.get("title", "")

        items: list[VideoItem] = []
        tabs = (
            data.get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", [])
        )
        if not isinstance(tabs, list):
            return items, display_name

        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tab_renderer = tab.get("tabRenderer", {})
            if not isinstance(tab_renderer, dict):
                continue
            content = tab_renderer.get("content", {})
            if not isinstance(content, dict):
                continue

            grid = content.get("richGridRenderer", {})
            if isinstance(grid, dict):
                self._extract_from_grid(grid, channel_name, display_name, items)
                continue

            section_list = content.get("sectionListRenderer", {})
            if isinstance(section_list, dict):
                for section_content in section_list.get("contents", []):
                    if not isinstance(section_content, dict):
                        continue
                    isr = section_content.get("itemSectionRenderer", {})
                    if not isinstance(isr, dict):
                        continue
                    for isr_content in isr.get("contents", []):
                        if not isinstance(isr_content, dict):
                            continue
                        g = isr_content.get("gridRenderer", {})
                        if isinstance(g, dict):
                            self._extract_from_grid_items(
                                g.get("items", []),
                                channel_name,
                                display_name,
                                items,
                            )

        return items, display_name

    def _extract_from_grid(
        self,
        grid: dict,
        channel_name: str,
        display_name: str,
        out: list[VideoItem],
    ) -> None:
        for content_item in grid.get("contents", []):
            if not isinstance(content_item, dict):
                continue
            rich_item = content_item.get("richItemRenderer", {})
            if not isinstance(rich_item, dict):
                continue
            vr = rich_item.get("content", {})
            if isinstance(vr, dict):
                renderer = vr.get("videoRenderer")
                if isinstance(renderer, dict):
                    item = self._parse_video_renderer(
                        renderer, channel_name, display_name
                    )
                    if item:
                        out.append(item)
                lockup = vr.get("lockupViewModel")
                if isinstance(lockup, dict):
                    item = self._parse_lockup_view_model(lockup, display_name)
                    if item:
                        out.append(item)

    def _extract_from_grid_items(
        self,
        items: list,
        channel_name: str,
        display_name: str,
        out: list[VideoItem],
    ) -> None:
        for grid_item in items:
            if not isinstance(grid_item, dict):
                continue
            renderer = grid_item.get("gridVideoRenderer")
            if isinstance(renderer, dict):
                item = self._parse_video_renderer(renderer, channel_name, display_name)
                if item:
                    out.append(item)
            lockup = grid_item.get("lockupViewModel")
            if isinstance(lockup, dict):
                item = self._parse_lockup_view_model(lockup, display_name)
                if item:
                    out.append(item)

    def _parse_video_renderer(
        self, renderer: dict, channel_name: str, display_name: str
    ) -> VideoItem | None:
        video_id = renderer.get("videoId")
        if not isinstance(video_id, str) or not video_id:
            return None

        title = self._extract_text(renderer.get("title", {}))

        style = "DEFAULT"
        has_upcoming_data = isinstance(renderer.get("upcomingEventData"), dict)
        overlays = renderer.get("thumbnailOverlays", [])
        if isinstance(overlays, list):
            for overlay in overlays:
                if not isinstance(overlay, dict):
                    continue
                tsr = overlay.get("thumbnailOverlayTimeStatusRenderer", {})
                if isinstance(tsr, dict) and isinstance(tsr.get("style"), str):
                    raw_style = tsr["style"].strip().upper()
                    if raw_style == "LIVE":
                        style = "LIVE"
                        break
                    if raw_style.startswith("UPCOMING"):
                        style = "UPCOMING"

        if style != "LIVE" and has_upcoming_data:
            style = "UPCOMING"

        scheduled_start = ""
        if style == "UPCOMING":
            upcoming_data = renderer.get("upcomingEventData", {})
            if isinstance(upcoming_data, dict):
                raw_ts = upcoming_data.get("startTime", "")
                if isinstance(raw_ts, str) and raw_ts:
                    scheduled_start = _unix_to_iso(raw_ts)

        url = f"https://www.youtube.com/watch?v={video_id}"

        return VideoItem(
            video_id=video_id,
            title=title,
            style=style,
            url=url,
            display_name=display_name,
            scheduled_start=scheduled_start,
        )

    def _parse_lockup_view_model(
        self, lockup: dict, display_name: str
    ) -> VideoItem | None:
        video_id = lockup.get("contentId")
        if not isinstance(video_id, str) or not video_id:
            renderer_context = lockup.get("rendererContext", {})
            if isinstance(renderer_context, dict):
                command_context = renderer_context.get("commandContext", {})
                command = {}
                if isinstance(command_context, dict):
                    on_tap = command_context.get("onTap", {})
                    if isinstance(on_tap, dict):
                        command = on_tap.get("innertubeCommand", {}) or {}
                if isinstance(command, dict):
                    endpoint = command.get("watchEndpoint", {})
                    if isinstance(endpoint, dict):
                        video_id = endpoint.get("videoId")
        if not isinstance(video_id, str) or not video_id:
            return None

        metadata = lockup.get("metadata", {})
        metadata_model = {}
        if isinstance(metadata, dict):
            metadata_model = metadata.get("lockupMetadataViewModel", {}) or {}
        title = ""
        if isinstance(metadata_model, dict):
            title = self._extract_text(metadata_model.get("title", {}))

        badge_texts = self._extract_lockup_badge_texts(lockup)
        metadata_texts = self._extract_lockup_metadata_texts(metadata_model)
        searchable = " ".join([*badge_texts, *metadata_texts]).upper()

        style = "DEFAULT"
        if any("即將" in text or "預定" in text for text in [*badge_texts, *metadata_texts]):
            style = "UPCOMING"
        elif "UPCOMING" in searchable or "SCHEDULED" in searchable:
            style = "UPCOMING"
        elif "LIVE" in searchable or any("直播" in text for text in badge_texts):
            style = "LIVE"

        return VideoItem(
            video_id=video_id,
            title=title,
            style=style,
            url=f"https://www.youtube.com/watch?v={video_id}",
            display_name=display_name,
        )

    @staticmethod
    def _extract_lockup_badge_texts(lockup: dict) -> list[str]:
        texts: list[str] = []
        thumbnail = (
            lockup.get("contentImage", {})
            .get("thumbnailViewModel", {})
            if isinstance(lockup.get("contentImage"), dict)
            else {}
        )
        overlays = thumbnail.get("overlays", []) if isinstance(thumbnail, dict) else []
        if not isinstance(overlays, list):
            return texts
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            bottom = overlay.get("thumbnailBottomOverlayViewModel", {})
            if not isinstance(bottom, dict):
                continue
            badges = bottom.get("badges", [])
            if not isinstance(badges, list):
                continue
            for badge in badges:
                if not isinstance(badge, dict):
                    continue
                model = badge.get("thumbnailBadgeViewModel", {})
                if not isinstance(model, dict):
                    continue
                text = model.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        return texts

    @staticmethod
    def _extract_lockup_metadata_texts(metadata_model: dict) -> list[str]:
        texts: list[str] = []
        metadata = metadata_model.get("metadata", {})
        if not isinstance(metadata, dict):
            return texts
        content_model = metadata.get("contentMetadataViewModel", {})
        if not isinstance(content_model, dict):
            return texts
        rows = content_model.get("metadataRows", [])
        if not isinstance(rows, list):
            return texts
        for row in rows:
            if not isinstance(row, dict):
                continue
            parts = row.get("metadataParts", [])
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text_obj = part.get("text", {})
                if isinstance(text_obj, dict):
                    text = text_obj.get("content")
                    if isinstance(text, str) and text:
                        texts.append(text)
        return texts

    @staticmethod
    def _extract_text(obj: object) -> str:
        if not isinstance(obj, dict):
            return ""
        content = obj.get("content")
        if isinstance(content, str):
            return content
        simple = obj.get("simpleText")
        if isinstance(simple, str):
            return simple
        runs = obj.get("runs", [])
        if isinstance(runs, list) and runs:
            first = runs[0]
            if isinstance(first, dict):
                return first.get("text", "")
        return ""

    # ------------------------------------------------------------------
    # Public API: get_channel_items (TIDUS)
    # ------------------------------------------------------------------
    def get_channel_items(self, channel_name: str) -> list[VideoItem]:
        base = _channel_url(channel_name)
        html = self._fetch_page(f"{base}/streams")
        if html is None:
            return []

        data = self._extract_json_var(html, "ytInitialData")
        if not isinstance(data, dict):
            return []

        items, _display_name = self._parse_channel_items(data, channel_name)
        self._fill_live_timing_details(items)
        return items

    def _fill_live_timing_details(self, items: list[VideoItem]) -> None:
        for item in items:
            if item.style not in {"LIVE", "UPCOMING"}:
                continue
            if item.style == "UPCOMING" and item.scheduled_start:
                continue
            if item.style == "LIVE" and item.started_at:
                continue
            details = self._get_watch_details(item.video_id)
            if item.style == "UPCOMING" and details.scheduled_start:
                item.scheduled_start = details.scheduled_start
            elif item.style == "LIVE" and details.started_at:
                item.started_at = details.started_at

    def _get_watch_details(self, video_id: str) -> _WatchDetails:
        now = time.time()
        cached = self._watch_details_cache.get(video_id)
        if cached and now - cached[0] < _WATCH_DETAILS_CACHE_TTL:
            return cached[1]

        details = self._fetch_watch_details(video_id)
        self._watch_details_cache[video_id] = (now, details)
        return details

    def _fetch_watch_details(self, video_id: str) -> _WatchDetails:
        html = self._fetch_page(f"https://www.youtube.com/watch?v={video_id}")
        if html is None:
            return _WatchDetails()

        player = self._extract_json_var(html, "ytInitialPlayerResponse")
        if not isinstance(player, dict):
            return _WatchDetails()

        video_details = player.get("videoDetails", {})
        is_upcoming = (
            isinstance(video_details, dict)
            and video_details.get("isUpcoming") is True
        )

        microformat = player.get("microformat", {})
        renderer = {}
        if isinstance(microformat, dict):
            renderer = microformat.get("playerMicroformatRenderer", {}) or {}
        live_details = {}
        if isinstance(renderer, dict):
            live_details = renderer.get("liveBroadcastDetails", {}) or {}

        start = ""
        if isinstance(live_details, dict):
            start = live_details.get("startTimestamp", "") or ""
        if not isinstance(start, str):
            start = ""

        if is_upcoming and start:
            return _WatchDetails(scheduled_start=start)
        if (
            isinstance(live_details, dict)
            and live_details.get("isLiveNow") is True
            and start
        ):
            return _WatchDetails(started_at=start)

        playability = player.get("playabilityStatus", {})
        live_streamability = {}
        if isinstance(playability, dict):
            live_streamability = playability.get("liveStreamability", {}) or {}
        live_renderer = {}
        if isinstance(live_streamability, dict):
            live_renderer = live_streamability.get("liveStreamabilityRenderer", {}) or {}
        offline_slate = {}
        if isinstance(live_renderer, dict):
            offline_slate = live_renderer.get("offlineSlate", {}) or {}
        slate_renderer = {}
        if isinstance(offline_slate, dict):
            slate_renderer = offline_slate.get("liveStreamOfflineSlateRenderer", {}) or {}
        scheduled_time = ""
        if isinstance(slate_renderer, dict):
            scheduled_time = slate_renderer.get("scheduledStartTime", "") or ""
        if isinstance(scheduled_time, str) and scheduled_time:
            return _WatchDetails(scheduled_start=_unix_to_iso(scheduled_time))

        return _WatchDetails()

    # ------------------------------------------------------------------
    # Public API: get_stream_info (used by AddChannelDialog validation)
    # ------------------------------------------------------------------
    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        base = _channel_url(channel_name)
        html = self._fetch_page(f"{base}/streams")
        if html is None:
            return self._fallback_live_check(channel_name)

        data = self._extract_json_var(html, "ytInitialData")
        if not isinstance(data, dict):
            return self._fallback_live_check(channel_name)

        items, display_name = self._parse_channel_items(data, channel_name)

        if not display_name:
            metadata = data.get("metadata", {})
            if isinstance(metadata, dict):
                renderer = metadata.get("channelMetadataRenderer", {})
                if isinstance(renderer, dict):
                    display_name = renderer.get("title", "")

        has_live = any(item.style == "LIVE" for item in items)
        live_item = next((i for i in items if i.style == "LIVE"), None)
        title = live_item.title if live_item else ""

        if not items:
            fb = self._fallback_live_check(channel_name)
            if fb is not None:
                if display_name and not fb.display_name:
                    fb.display_name = display_name
                return fb

        return StreamInfo(
            channel=channel_name,
            platform="youtube",
            is_live=has_live,
            title=title,
            url=f"{base}/live",
            display_name=display_name,
        )

    def is_live(self, channel_name: str) -> bool:
        info = self.get_stream_info(channel_name)
        return info.is_live if info else False

    # ------------------------------------------------------------------
    # Fallback: old /@channel/live + ytInitialPlayerResponse
    # ------------------------------------------------------------------
    def _fallback_live_check(self, channel_name: str) -> StreamInfo | None:
        base = _channel_url(channel_name)
        url = f"{base}/live"
        html = self._fetch_page(url)
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

    def _parse_live_status(self, html: str) -> tuple[bool, str, str]:
        """Return (is_live, title, display_name) from ytInitialPlayerResponse."""
        player = self._extract_json_var(html, "ytInitialPlayerResponse")
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

        return False, title, display_name
