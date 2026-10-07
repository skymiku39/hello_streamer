"""YouTube rate-limit / parse-failure resilience (not false offline)."""

from __future__ import annotations

import logging

import pytest

from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.fetcher.youtube import YouTubeFetcher
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor
from stream_monitor.monitor import deps as monitor_deps
from stream_monitor.monitor.types import (
    _OFFLINE_STRIKE_THRESHOLD,
    OfflineInfo,
    _live_cache_key,
)


def _check_and_commit(monitor: Monitor, entry: ChannelEntry):
    events, commit = monitor._check_channel(entry)
    commit()
    return events


class _BackoffAwareYouTubeFetcher:
    platform = "youtube"

    def __init__(
        self,
        *,
        items_batches: list[list[VideoItem] | None] | None = None,
        info_batches: list[StreamInfo | None] | None = None,
        backoff: bool = False,
        unavailable_reason: str = "fetch returned None",
        raise_on_fallback: bool = False,
    ) -> None:
        self.items_batches = list(items_batches or [])
        self.info_batches = list(info_batches or [])
        self._backoff = backoff
        self.last_unavailable_reason = ""
        self._unavailable_reason = unavailable_reason
        self.raise_on_fallback = raise_on_fallback
        self._last_items: list[VideoItem] = []

    def http_backoff_active(self) -> bool:
        return self._backoff

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem] | None:
        self.last_unavailable_reason = ""
        if self.items_batches:
            batch = self.items_batches.pop(0)
            if batch is None:
                self.last_unavailable_reason = (
                    "http backoff" if self._backoff else self._unavailable_reason
                )
                return None
            self._last_items = batch
            return list(self._last_items)
        if self._last_items:
            return list(self._last_items)
        self.last_unavailable_reason = (
            "http backoff" if self._backoff else self._unavailable_reason
        )
        return None

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if self.raise_on_fallback:
            raise RuntimeError("fallback boom")
        if self.info_batches:
            return self.info_batches.pop(0)
        return None

    def enrich_live_for_details(self, items: list[VideoItem]) -> None:
        return None

    def enrich_upcoming_for_details(self, items: list[VideoItem]) -> None:
        return None

    def get_latest_finished_vod(self, channel_name: str, *, items=None):
        return None


@pytest.fixture(autouse=True)
def _reset_youtube_http_backoff() -> None:
    with YouTubeFetcher._rate_lock:
        YouTubeFetcher._backoff_until = 0.0
        YouTubeFetcher._last_request_at = 0.0
    yield
    with YouTubeFetcher._rate_lock:
        YouTubeFetcher._backoff_until = 0.0
        YouTubeFetcher._last_request_at = 0.0


def test_get_channel_items_missing_ytinitialdata_is_unavailable(
    monkeypatch,
) -> None:
    """HTML without ytInitialData must not silently become an empty feed."""
    fetcher = YouTubeFetcher()
    monkeypatch.setattr(
        fetcher,
        "_fetch_page",
        lambda url, timeout=15: "<html><body>consent wall</body></html>",
    )

    assert fetcher.get_channel_items("creator") is None
    assert fetcher.last_unavailable_reason == "ytInitialData parse failure"


def test_get_channel_items_429_sets_backoff_and_unavailable(
    monkeypatch,
) -> None:
    class _Resp:
        status_code = 429
        text = ""

        def raise_for_status(self) -> None:
            return None

    fetcher = YouTubeFetcher()

    def _get(_url, timeout=15):
        return _Resp()

    monkeypatch.setattr(fetcher, "_wait_for_request_slot", lambda: True)
    monkeypatch.setattr(
        fetcher,
        "_session_for_thread",
        lambda: type("S", (), {"get": staticmethod(_get)})(),
    )

    assert fetcher.get_channel_items("creator") is None
    assert fetcher.http_backoff_active() is True
    assert fetcher.last_unavailable_reason == "http backoff"


def test_get_channel_items_5xx_reports_transient_reason(monkeypatch) -> None:
    class _Resp:
        status_code = 503
        text = ""

        def raise_for_status(self) -> None:
            return None

    fetcher = YouTubeFetcher()

    def _get(_url, timeout=15):
        return _Resp()

    monkeypatch.setattr(fetcher, "_wait_for_request_slot", lambda: True)
    monkeypatch.setattr(
        fetcher,
        "_session_for_thread",
        lambda: type("S", (), {"get": staticmethod(_get)})(),
    )
    monkeypatch.setattr("stream_monitor.fetcher.youtube.time.sleep", lambda _s: None)

    assert fetcher.get_channel_items("creator") is None
    assert fetcher.last_unavailable_reason == "http 5xx"


def test_live_plus_http_backoff_does_not_strike_or_went_offline(
    monkeypatch, tmp_path
) -> None:
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[None, None],
        backoff=True,
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    live_key = _live_cache_key(entry.key)

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )
        monitor._live_payload[live_key] = OfflineInfo(
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
            platform="youtube",
            name="yt",
        )

    for _ in range(_OFFLINE_STRIKE_THRESHOLD + 1):
        _check_and_commit(monitor, entry)

    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert live_key not in monitor._offline_strikes
        assert monitor._pending_offline_events == []
        snap = monitor._probe_snapshots.get(entry.key)
        assert snap is None or not snap.youtube_fallback
    db.close()


def test_ytinitialdata_parse_failure_is_unavailable_not_empty_fallback(
    monkeypatch, tmp_path, caplog
) -> None:
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[None],
        unavailable_reason="ytInitialData parse failure",
        info_batches=[
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=True,
                title="Should not use fallback",
                url="https://www.youtube.com/watch?v=fb",
            )
        ],
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )

    with caplog.at_level(logging.WARNING):
        _check_and_commit(monitor, entry)

    with monitor._lock:
        snap = monitor._probe_snapshots.get(entry.key)
        assert snap is None or not snap.youtube_fallback
        assert monitor._last_status[entry.key].status is True
        assert _live_cache_key(entry.key) not in monitor._offline_strikes
        assert monitor._pending_offline_events == []
    assert any("ytInitialData parse failure" in r.message for r in caplog.records)
    db.close()


def test_fallback_exception_is_unavailable_not_silent_empty(
    monkeypatch, tmp_path, caplog
) -> None:
    # Empty TIDUS without fallback_triggered_live goes straight to fallback.
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[[]],
        raise_on_fallback=True,
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )

    with caplog.at_level(logging.WARNING):
        _check_and_commit(monitor, entry)

    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._offline_strikes.get(_live_cache_key(entry.key)) == 1
        assert monitor._pending_offline_events == []
    assert any(
        "fallback exception" in r.message or "fallback boom" in r.message
        for r in caplog.records
    )
    db.close()


def test_wake_mode_allows_offline_to_live_fallback_edge(
    monkeypatch, tmp_path
) -> None:
    """Wake verification must still emit a fallback live edge."""
    fallback_info = StreamInfo(
        channel="yt",
        platform="youtube",
        is_live=True,
        title="Fallback wake live",
        url="https://www.youtube.com/@yt/live",
    )
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[[]],
        info_batches=[fallback_info],
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=False,
            url="https://www.youtube.com/@yt/live",
        )
    monitor._wake_verify_mode = True

    events = _check_and_commit(monitor, entry)

    assert len(events) == 1
    assert events[0][0] == entry
    assert events[0][1].is_live is True
    db.close()


def test_live_plus_transient_http_failure_does_not_strike(
    monkeypatch, tmp_path
) -> None:
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[None, None],
        unavailable_reason="request timeout",
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )

    for _ in range(_OFFLINE_STRIKE_THRESHOLD + 1):
        _check_and_commit(monitor, entry)

    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._offline_strikes.get(_live_cache_key(entry.key)) is None
        assert monitor._pending_offline_events == []
    db.close()


def test_true_offline_anti_flap_still_requires_strikes(
    monkeypatch, tmp_path
) -> None:
    """Confirmed offline readings still need the strike threshold (anti-flap)."""
    live = VideoItem(
        video_id="vid1",
        title="Live Stream",
        style="LIVE",
        url="https://www.youtube.com/watch?v=vid1",
    )
    offline_items = [
        VideoItem(
            video_id="vod1",
            title="Replay",
            style="DEFAULT",
            url="https://www.youtube.com/watch?v=vod1",
        )
    ]
    # Poll1 live; poll2 missing live (strike 1 hold); poll3 still missing (commit).
    fetcher = _BackoffAwareYouTubeFetcher(
        items_batches=[[live], offline_items, offline_items],
    )
    monkeypatch.setattr(monitor_deps, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    live_key = _live_cache_key(entry.key, "vid1")

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert live_key in monitor._live_payload

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._offline_strikes.get(live_key) == 1
        assert monitor._pending_offline_events == []

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is False
        assert live_key not in monitor._offline_strikes
        assert len(monitor._pending_offline_events) == 1
    db.close()
