import logging
import threading
import time

from stream_monitor import monitor as monitor_module
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import FinishedVod, StreamInfo, VideoItem
from stream_monitor.monitor import ChannelEntry, ChannelStatus, Monitor


def _check_and_commit(monitor: Monitor, entry: ChannelEntry):
    """Call _check_channel, commit DB writes, return events."""
    events, commit = monitor._check_channel(entry)
    commit()
    return events


class FakeTwitchFetcher:
    platform = "twitch"

    def __init__(self, statuses: list[bool]) -> None:
        self.statuses = statuses
        self._repeat_offline: bool | None = None

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        if self._repeat_offline is not None:
            is_live = self._repeat_offline
            self._repeat_offline = None
        else:
            is_live = self.statuses.pop(0) if self.statuses else False
            if not is_live:
                self._repeat_offline = is_live
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=is_live,
            title="Live now" if is_live else "",
            url=f"https://www.twitch.tv/{channel_name}",
            display_name="Hello Channel",
        )

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        return []

    def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
        return None


class FakeTwitchFetcherCalls:
    """Twitch fetcher driven by per-HTTP-call readings (not per-poll)."""

    platform = "twitch"

    def __init__(self, readings: list[bool]) -> None:
        self.readings = list(readings)

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        is_live = self.readings.pop(0) if self.readings else False
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=is_live,
            title="Live now" if is_live else "",
            url=f"https://www.twitch.tv/{channel_name}",
            display_name="Hello Channel",
        )

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        return []

    def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
        return None


class FakeTwitchFetcherReadings:
    """Twitch fetcher that can return ``None`` (failed fetch) per poll."""

    platform = "twitch"

    def __init__(self, readings: list[bool | None]) -> None:
        self.readings = readings
        self._repeat_offline: bool | None = None

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if self._repeat_offline is not None:
            val = self._repeat_offline
            self._repeat_offline = None
        else:
            val = self.readings.pop(0) if self.readings else None
            if val is False:
                self._repeat_offline = val
        if val is None:
            return None
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=val,
            title="Live now" if val else "",
            url=f"https://www.twitch.tv/{channel_name}",
            display_name="Hello Channel",
        )

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        return []


class FakeYouTubeFetcher:
    platform = "youtube"

    def __init__(
        self,
        items_batches: list[list[VideoItem]],
        info_batches: list[StreamInfo | None] | None = None,
    ) -> None:
        self.items_batches = list(items_batches)
        self.info_batches = info_batches or []
        self._last_items: list[VideoItem] = []

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if self.info_batches:
            return self.info_batches.pop(0)
        return None

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        if self.items_batches:
            self._last_items = self.items_batches.pop(0)
        return list(self._last_items)

    def http_backoff_active(self) -> bool:
        return False

    def enrich_items_for_details(self, items: list[VideoItem]) -> None:
        return None

    def enrich_live_for_details(self, items: list[VideoItem]) -> None:
        return None

    def enrich_upcoming_for_details(self, items: list[VideoItem]) -> None:
        return None

    def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
        return None


# ─────────────────────────────────────────────
# Twitch: boolean edge-trigger (unchanged)
# ─────────────────────────────────────────────
def test_tier1_offline_preview_preserves_tier2_resolved_empty_detail() -> None:
    cached = ChannelStatus(
        status=False,
        url="https://www.twitch.tv/karlylinnea",
        ended_at="",
        ended_at_source="",
        vod_url="",
    )
    pending = ChannelStatus(
        status=False,
        url="https://www.twitch.tv/karlylinnea",
        ended_at_source="pending",
    )
    merged = Monitor._coalesce_tier1_offline_preview(pending, cached)
    assert merged.ended_at_source == ""
    assert merged.ended_at == ""


def test_tier1_offline_preview_preserves_tier2_vod_detail() -> None:
    cached = ChannelStatus(
        status=False,
        ended_at="2026-06-10T08:00:00+00:00",
        ended_at_source="vod",
        vod_url="https://www.twitch.tv/videos/1",
        url="https://www.twitch.tv/hello",
    )
    pending = ChannelStatus(
        status=False,
        url="https://www.twitch.tv/hello",
        ended_at_source="pending",
    )
    merged = Monitor._coalesce_tier1_offline_preview(pending, cached)
    assert merged.ended_at_source == "vod"
    assert merged.ended_at == cached.ended_at
    assert merged.vod_url == cached.vod_url


def test_twitch_live_tier1_preview_does_not_deadlock(
    monkeypatch, tmp_path
) -> None:
    """LIVE tier-1 preview must not re-acquire Monitor._lock (non-reentrant)."""
    fetcher = FakeTwitchFetcher([True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hayabi_zr"}],
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hayabi_zr")
    result: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            monitor._probe_live(entry)
            result.append("ok")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=3.0)
    assert not worker.is_alive(), "twitch live tier-1 preview deadlocked"
    assert result == ["ok"]
    assert not errors
    db.close()


def test_twitch_triggers_only_on_live_transition(monkeypatch, tmp_path) -> None:
    # Sequence chosen to exercise *confirmed* offline transitions only —
    # the anti-flap guard requires two consecutive "not live" readings
    # before it commits to the offline edge (see _OFFLINE_STRIKE_THRESHOLD).
    # Stable-offline polls issue one GQL call; live→offline edges double-check.
    fetcher = FakeTwitchFetcherCalls(
        [
            False,  # poll 1: stable offline
            True,  # poll 2: went live
            True,  # poll 3: still live
            False,
            False,  # poll 4: offline edge (double-check)
            False,
            False,  # poll 5: offline edge (double-check) → confirmed offline
            True,  # poll 6: went live again
        ]
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    events: list[tuple[str, str]] = []
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        on_status_change=lambda entry, info: events.append((entry.key, info.title)),
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hello")

    went_live: list[tuple[ChannelEntry, object]] = []
    for _ in range(6):
        results = _check_and_commit(monitor, entry)
        went_live.extend(results)

    for e, info in went_live:
        events.append((e.key, info.title))

    assert events == [
        ("twitch:hello", "Live now"),
        ("twitch:hello", "Live now"),
    ]
    assert monitor.snapshot_statuses() == {"twitch:hello": True}
    assert monitor.snapshot_display_names() == {"twitch:hello": "Hello Channel"}
    db.close()


def test_check_channel_does_not_filter_by_enabled_flag(monkeypatch, tmp_path) -> None:
    """_check_channel processes the entry regardless of enabled; filtering is _run()'s job."""
    fetcher = FakeTwitchFetcher([True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello", "enabled": False}],
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hello", enabled=False)

    results = _check_and_commit(monitor, entry)
    assert len(results) == 1

    with monitor._lock:
        entries = list(monitor._entries)
    assert entries[0].enabled is False
    db.close()


# ─────────────────────────────────────────────
# YouTube: TIDUS videoId dedup
# ─────────────────────────────────────────────
def test_youtube_new_video_triggers_event(monkeypatch, tmp_path) -> None:
    items = [
        VideoItem(
            video_id="vid1",
            title="Going Live",
            style="LIVE",
            url="https://youtube.com/watch?v=vid1",
            display_name="YT Chan",
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")
    results = _check_and_commit(monitor, entry)

    assert len(results) == 1
    _, info = results[0]
    assert info.stream_status == "live"
    assert info.video_id == "vid1"
    assert monitor.snapshot_statuses() == {"youtube:ytchan": True}
    db.close()


def test_youtube_duplicate_video_skipped(monkeypatch, tmp_path) -> None:
    item = VideoItem(
        video_id="vid1",
        title="Going Live",
        style="LIVE",
        url="https://youtube.com/watch?v=vid1",
        display_name="YT Chan",
    )
    fetcher = FakeYouTubeFetcher([[item], [item]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    results_first = _check_and_commit(monitor, entry)
    assert len(results_first) == 1

    results_second = _check_and_commit(monitor, entry)
    assert len(results_second) == 0
    db.close()


def test_youtube_upcoming_poll_fills_missing_schedule(
    monkeypatch, tmp_path
) -> None:
    """Monitor poll path must surface UPCOMING countdown when /streams omits startTime."""
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    bare_upcoming = VideoItem(
        video_id="vid_upcoming",
        title="Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=vid_upcoming",
        display_name="YT Chan",
        scheduled_start="",
    )

    class FetcherFillsUpcoming(FakeYouTubeFetcher):
        def enrich_upcoming_for_details(self, items: list[VideoItem]) -> None:
            for item in items:
                if item.style == "UPCOMING" and not item.scheduled_start:
                    item.scheduled_start = soon

    fetcher = FetcherFillsUpcoming([[bare_upcoming]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")
    _check_and_commit(monitor, entry)

    with monitor._lock:
        status = monitor._last_status["youtube:ytchan"]
        assert isinstance(status, ChannelStatus)
        assert status.status is False
        assert status.upcoming_url == "https://youtube.com/watch?v=vid_upcoming"
        assert status.scheduled_start == soon
    db.close()


def test_youtube_past_upcoming_does_not_set_upcoming_status(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    items = [
        VideoItem(
            video_id="vid_past",
            title="Missed Premiere",
            style="UPCOMING",
            url="https://www.youtube.com/watch?v=vid_past",
            scheduled_start=past,
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "ytchan"}], db=db)
    entry = ChannelEntry(platform="youtube", name="ytchan")
    _check_and_commit(monitor, entry)

    with monitor._lock:
        status = monitor._last_status["youtube:ytchan"]
        assert isinstance(status, ChannelStatus)
        assert status.status is not True
        assert status.status != "upcoming"
    db.close()


def test_youtube_upcoming_sets_status(monkeypatch, tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    items = [
        VideoItem(
            video_id="vid_upcoming",
            title="Waiting Room",
            style="UPCOMING",
            url="https://youtube.com/watch?v=vid_upcoming",
            display_name="YT Chan",
            scheduled_start=soon,
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")
    results = _check_and_commit(monitor, entry)

    assert results == []
    with monitor._lock:
        row = monitor._last_status["youtube:ytchan"]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.upcoming_url == "https://youtube.com/watch?v=vid_upcoming"
    assert db.is_seen("vid_upcoming", "UPCOMING") is True
    db.close()


def test_youtube_upcoming_status_uses_nearest_scheduled_start(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    later_start = (now + timedelta(hours=4)).isoformat()
    sooner_start = (now + timedelta(hours=2)).isoformat()
    later = VideoItem(
        video_id="later_waiting",
        title="Later Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=later_waiting",
        display_name="YT Chan",
        scheduled_start=later_start,
    )
    sooner = VideoItem(
        video_id="sooner_waiting",
        title="Sooner Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=sooner_waiting",
        display_name="YT Chan",
        scheduled_start=sooner_start,
    )
    fetcher = FakeYouTubeFetcher([[later, sooner]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    assert _check_and_commit(monitor, entry) == []
    status = monitor.snapshot_statuses()["youtube:ytchan"]

    assert isinstance(status, ChannelStatus)
    assert status.status is False
    assert status.upcoming_url == "https://youtube.com/watch?v=sooner_waiting"
    assert status.scheduled_start == sooner_start
    db.close()


def test_youtube_live_without_feed_started_at_uses_session_cache(
    monkeypatch, tmp_path,
) -> None:
    """LIVE rows must show elapsed time even when tier-1 skips watch pages."""
    live = VideoItem(
        video_id="G8YqTVg0IUQ",
        title="Live Now",
        style="LIVE",
        url="https://www.youtube.com/watch?v=G8YqTVg0IUQ",
    )

    class EnrichLiveFetcher(FakeYouTubeFetcher):
        def enrich_live_for_details(self, items: list[VideoItem]) -> None:
            for item in items:
                if item.style == "LIVE" and not item.started_at:
                    item.started_at = "2026-06-10T08:00:00+00:00"

    fetcher = EnrichLiveFetcher([[live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "fukuri_1017"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="fukuri_1017")

    _check_and_commit(monitor, entry)

    status = monitor.snapshot_statuses()["youtube:fukuri_1017"]
    assert isinstance(status, ChannelStatus)
    assert status.status is True
    assert status.started_at == "2026-06-10T08:00:00+00:00"
    db.close()


def test_youtube_tier1_live_preview_sets_started_at_without_watch(
    monkeypatch, tmp_path,
) -> None:
    """Tier-1 preview must show elapsed time before tier-2 watch enrichment."""
    live = VideoItem(
        video_id="G8YqTVg0IUQ",
        title="Live Now",
        style="LIVE",
        url="https://www.youtube.com/watch?v=G8YqTVg0IUQ",
    )
    fetcher = FakeYouTubeFetcher([[live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    partial: dict[str, ChannelStatus] = {}
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "fukuri_1017"}],
        db=db,
        on_partial_snapshot=lambda statuses, _names: partial.update(statuses),
    )
    entry = ChannelEntry(platform="youtube", name="fukuri_1017")

    monitor._probe_live(entry)

    row = partial["youtube:fukuri_1017"]
    assert row.status is True
    assert row.started_at != ""
    db.close()


def test_youtube_session_fallback_upgrades_to_platform_start_time(
    monkeypatch, tmp_path,
) -> None:
    """Tier-1 first-seen must not block tier-2 watch enrich for real start time."""
    live = VideoItem(
        video_id="G8YqTVg0IUQ",
        title="Live Now",
        style="LIVE",
        url="https://www.youtube.com/watch?v=G8YqTVg0IUQ",
    )
    enrich_calls = 0

    class PlatformFetcher(FakeYouTubeFetcher):
        def enrich_live_for_details(self, items: list[VideoItem]) -> None:
            nonlocal enrich_calls
            enrich_calls += 1
            for item in items:
                if item.style == "LIVE" and not item.started_at:
                    item.started_at = "2026-06-10T09:31:07+00:00"

    fetcher = PlatformFetcher([[live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "fukuri_1017"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="fukuri_1017")

    _check_and_commit(monitor, entry)

    assert enrich_calls == 1
    status = monitor.snapshot_statuses()["youtube:fukuri_1017"]
    assert isinstance(status, ChannelStatus)
    assert status.started_at == "2026-06-10T09:31:07+00:00"
    live_key = "youtube:fukuri_1017|G8YqTVg0IUQ"
    with monitor._lock:
        assert live_key in monitor._live_platform_started_at
    db.close()


def test_youtube_stable_live_skips_watch_enrich_on_second_poll(
    monkeypatch, tmp_path,
) -> None:
    live = VideoItem(
        video_id="G8YqTVg0IUQ",
        title="Live Now",
        style="LIVE",
        url="https://www.youtube.com/watch?v=G8YqTVg0IUQ",
    )
    enrich_calls = 0

    class CountingFetcher(FakeYouTubeFetcher):
        def enrich_live_for_details(self, items: list[VideoItem]) -> None:
            nonlocal enrich_calls
            enrich_calls += 1
            for item in items:
                if item.style == "LIVE" and not item.started_at:
                    item.started_at = "2026-06-10T08:00:00+00:00"

    fetcher = CountingFetcher([[live], [live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "fukuri_1017"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="fukuri_1017")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    assert enrich_calls == 1
    status = monitor.snapshot_statuses()["youtube:fukuri_1017"]
    assert isinstance(status, ChannelStatus)
    assert status.started_at == "2026-06-10T08:00:00+00:00"
    db.close()


def test_youtube_live_status_uses_longest_running_stream(
    monkeypatch, tmp_path
) -> None:
    newer = VideoItem(
        video_id="newer_live",
        title="Newer Live",
        style="LIVE",
        url="https://youtube.com/watch?v=newer_live",
        display_name="YT Chan",
        started_at="2026-05-06T13:00:00+00:00",
    )
    older = VideoItem(
        video_id="older_live",
        title="Older Live",
        style="LIVE",
        url="https://youtube.com/watch?v=older_live",
        display_name="YT Chan",
        started_at="2026-05-06T12:00:00+00:00",
    )
    fetcher = FakeYouTubeFetcher([[newer, older]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    assert len(_check_and_commit(monitor, entry)) == 2
    status = monitor.snapshot_statuses()["youtube:ytchan"]

    assert isinstance(status, ChannelStatus)
    assert status.status is True
    assert status.url == "https://youtube.com/watch?v=older_live"
    assert status.started_at == "2026-05-06T12:00:00+00:00"
    db.close()


def test_youtube_mixed_styles_in_single_poll(monkeypatch, tmp_path) -> None:
    items = [
        VideoItem(
            video_id="v_live", title="Live", style="LIVE",
            url="https://youtube.com/watch?v=v_live", display_name="Chan",
        ),
        VideoItem(
            video_id="v_default", title="Upload", style="DEFAULT",
            url="https://youtube.com/watch?v=v_default", display_name="Chan",
        ),
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")
    results = _check_and_commit(monitor, entry)

    assert len(results) == 1
    statuses = [info.stream_status for _, info in results]
    assert "live" in statuses
    assert monitor.snapshot_statuses() == {"youtube:ytchan": True}
    assert db.is_seen("v_default", "DEFAULT") is True
    db.close()


def test_youtube_upcoming_then_live_same_video_triggers_live(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    upcoming = VideoItem(
        video_id="same_vid",
        title="Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=same_vid",
        display_name="YT Chan",
        scheduled_start=soon,
    )
    live = VideoItem(
        video_id="same_vid",
        title="Now Live",
        style="LIVE",
        url="https://youtube.com/watch?v=same_vid",
        display_name="YT Chan",
    )
    fetcher = FakeYouTubeFetcher([[upcoming], [live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    assert _check_and_commit(monitor, entry) == []
    results = _check_and_commit(monitor, entry)

    assert len(results) == 1
    _, info = results[0]
    assert info.stream_status == "live"
    assert info.video_id == "same_vid"
    assert db.is_seen("same_vid", "UPCOMING") is True
    assert db.is_seen("same_vid", "LIVE") is True
    db.close()


def test_youtube_new_upcoming_after_baseline_triggers_notify_event(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    old = VideoItem(
        video_id="old_waiting",
        title="Old Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=old_waiting",
        display_name="YT Chan",
        scheduled_start=soon,
    )
    new = VideoItem(
        video_id="new_waiting",
        title="New Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=new_waiting",
        display_name="YT Chan",
        scheduled_start=later,
    )
    fetcher = FakeYouTubeFetcher([[old], [old, new]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    assert _check_and_commit(monitor, entry) == []
    results = _check_and_commit(monitor, entry)

    assert len(results) == 1
    _, info = results[0]
    assert info.stream_status == "upcoming"
    assert info.video_id == "new_waiting"
    db.close()


def test_youtube_default_items_are_marked_but_never_emit_events(
    monkeypatch, tmp_path
) -> None:
    old = VideoItem(
        video_id="old_upload",
        title="Old Upload",
        style="DEFAULT",
        url="https://youtube.com/watch?v=old_upload",
        display_name="YT Chan",
    )
    new = VideoItem(
        video_id="new_upload",
        title="New Upload",
        style="DEFAULT",
        url="https://youtube.com/watch?v=new_upload",
        display_name="YT Chan",
    )
    fetcher = FakeYouTubeFetcher([[old], [old, new]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    assert _check_and_commit(monitor, entry) == []
    assert _check_and_commit(monitor, entry) == []
    assert db.is_seen("old_upload", "DEFAULT") is True
    assert db.is_seen("new_upload", "DEFAULT") is True
    assert monitor.snapshot_statuses() == {"youtube:ytchan": False}
    db.close()


def test_youtube_empty_items_uses_live_fallback(monkeypatch, tmp_path) -> None:
    fallback = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=True,
        title="Fallback Live",
        url="https://www.youtube.com/@ytchan/live",
        display_name="YT Chan",
    )
    fetcher = FakeYouTubeFetcher([[]], [fallback])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")
    results = _check_and_commit(monitor, entry)

    assert len(results) == 1
    _, info = results[0]
    assert info.stream_status == "live"
    assert info.title == "Fallback Live"
    assert monitor.snapshot_statuses() == {"youtube:ytchan": True}
    db.close()


def test_youtube_fallback_then_tidus_recovery_no_duplicate(
    monkeypatch, tmp_path
) -> None:
    """When fallback triggers LIVE and then TIDUS recovers, no duplicate fires."""
    live_item = VideoItem(
        video_id="vid_fb",
        title="Live Stream",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_fb",
        display_name="Chan",
    )
    fallback_info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=True,
        title="Live Stream",
        url="https://www.youtube.com/@ytchan/live",
        display_name="Chan",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [live_item]],
        info_batches=[fallback_info],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    results_fallback = _check_and_commit(monitor, entry)
    assert len(results_fallback) == 1
    assert results_fallback[0][1].title == "Live Stream"

    results_tidus = _check_and_commit(monitor, entry)
    assert len(results_tidus) == 0

    assert monitor.snapshot_statuses() == {"youtube:ytchan": True}
    db.close()


def test_youtube_fallback_suppression_only_consumes_one_live(
    monkeypatch, tmp_path
) -> None:
    """Suppression is consumed once; a second LIVE item in the same batch triggers."""
    live_a = VideoItem(
        video_id="vid_a",
        title="Stream A",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_a",
        display_name="Chan",
    )
    live_b = VideoItem(
        video_id="vid_b",
        title="Stream B",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_b",
        display_name="Chan",
    )
    fallback_info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=True,
        title="Stream A",
        url="https://www.youtube.com/@ytchan/live",
        display_name="Chan",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [live_a, live_b]],
        info_batches=[fallback_info],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    results_fallback = _check_and_commit(monitor, entry)
    assert len(results_fallback) == 1

    results_tidus = _check_and_commit(monitor, entry)
    assert len(results_tidus) == 1
    assert results_tidus[0][1].video_id == "vid_b"
    db.close()


def test_youtube_fallback_no_title_match_multiple_items_suppresses_none(
    monkeypatch, tmp_path
) -> None:
    """When no title matches and multiple LIVE items exist, suppress nothing."""
    live_a = VideoItem(
        video_id="vid_a",
        title="New Stream X",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_a",
        display_name="Chan",
    )
    live_b = VideoItem(
        video_id="vid_b",
        title="New Stream Y",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_b",
        display_name="Chan",
    )
    fallback_info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=True,
        title="Old Stream Title",
        url="https://www.youtube.com/@ytchan/live",
        display_name="Chan",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [live_a, live_b]],
        info_batches=[fallback_info],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    _check_and_commit(monitor, entry)

    results = _check_and_commit(monitor, entry)
    assert len(results) == 2
    db.close()


def test_twitch_re_enable_clears_last_status(monkeypatch, tmp_path) -> None:
    """Re-enabling a channel clears _last_status so new LIVE is detected."""
    fetcher = FakeTwitchFetcher([True, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello", "enabled": True}],
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    assert monitor.snapshot_statuses() == {"twitch:hello": True}

    monitor.update_channels(
        [{"platform": "twitch", "name": "hello", "enabled": False}]
    )
    monitor.update_channels(
        [{"platform": "twitch", "name": "hello", "enabled": True}]
    )
    assert monitor.snapshot_statuses().get("twitch:hello") is None

    results = _check_and_commit(monitor, entry)
    assert len(results) == 1
    db.close()


def test_youtube_stop_discards_uncommitted_seen(monkeypatch, tmp_path) -> None:
    """If stop occurs before commit, items are NOT marked as seen in DB."""
    item = VideoItem(
        video_id="vid_stop",
        title="Going Live",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_stop",
        display_name="Chan",
    )
    fetcher = FakeYouTubeFetcher([[item]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    events, commit = monitor._check_channel(entry)
    assert len(events) == 1

    assert db.is_seen("vid_stop", "LIVE") is False
    db.close()


def test_youtube_fallback_marker_preserved_across_nonlive_tidus(
    monkeypatch, tmp_path
) -> None:
    """Fallback marker is NOT consumed when TIDUS returns only non-LIVE items."""
    default_item = VideoItem(
        video_id="vid_def",
        title="Some Upload",
        style="DEFAULT",
        url="https://youtube.com/watch?v=vid_def",
        display_name="Chan",
    )
    live_item = VideoItem(
        video_id="vid_live",
        title="Live Stream",
        style="LIVE",
        url="https://youtube.com/watch?v=vid_live",
        display_name="Chan",
    )
    fallback_info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=True,
        title="Live Stream",
        url="https://www.youtube.com/@ytchan/live",
        display_name="Chan",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [default_item], [live_item]],
        info_batches=[fallback_info],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "ytchan"}],
        db=db,
    )
    entry = ChannelEntry(platform="youtube", name="ytchan")

    results_fallback = _check_and_commit(monitor, entry)
    assert len(results_fallback) == 1

    results_default = _check_and_commit(monitor, entry)
    assert len(results_default) == 0

    results_live = _check_and_commit(monitor, entry)
    assert len(results_live) == 0

    db.close()


# ─────────────────────────────────────────────
# General
# ─────────────────────────────────────────────
def test_update_channels_replaces_entries(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "old"}], db=db)
    monitor.update_channels([{"platform": "youtube", "name": "new"}])
    with monitor._lock:
        assert [entry.key for entry in monitor._entries] == ["youtube:new"]
    db.close()


def test_update_channels_preserves_enabled_flag(tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a", "enabled": False}],
        db=db,
    )
    with monitor._lock:
        assert monitor._entries[0].enabled is False

    monitor.update_channels([
        {"platform": "twitch", "name": "a", "enabled": True},
        {"platform": "twitch", "name": "b"},
    ])
    with monitor._lock:
        assert monitor._entries[0].enabled is True
        assert monitor._entries[1].enabled is True
    db.close()


# ─────────────────────────────────────────────
# Went-offline edge events
# ─────────────────────────────────────────────
def test_twitch_emits_went_offline_after_live(monkeypatch, tmp_path) -> None:
    """Live → offline edge must enqueue a went-offline event with the prior URL.

    The anti-flap guard requires two consecutive "not live" readings before
    committing to the offline edge, so the sequence here is [True, False, False].
    """
    fetcher = FakeTwitchFetcher([True, False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hello")

    # Poll 1: went LIVE
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []

    # Poll 2: first "not live" reading — treated as transient noise.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []

    # Poll 3: second consecutive "not live" — now committed to offline.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert len(monitor._pending_offline_events) == 1
        evt_entry, payload = monitor._pending_offline_events[0]
        assert evt_entry.key == "twitch:hello"
        assert payload.url == "https://www.twitch.tv/hello"
        assert payload.title == "Live now"
        assert payload.platform == "twitch"
        assert payload.name == "hello"
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.status is False
        assert offline.title == "Live now"
        assert offline.ended_at
    db.close()


def test_twitch_offline_status_sets_vod_url_from_archive(
    monkeypatch, tmp_path
) -> None:
    class FetcherWithArchive(FakeTwitchFetcher):
        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            from datetime import datetime, timedelta, timezone

            ended = (
                datetime.now(timezone.utc) - timedelta(minutes=45)
            ).isoformat()
            return FinishedVod(
                url="https://www.twitch.tv/videos/archive1",
                ended_at=ended,
                title="Archive stream",
            )

    fetcher = FetcherWithArchive([True, False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    with monitor._lock:
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.vod_url == "https://www.twitch.tv/videos/archive1"
        assert offline.url == "https://www.twitch.tv/hello"
        assert offline.ended_at
        assert offline.ended_at_source == "vod"
    db.close()


def test_youtube_offline_sets_upcoming_url_with_vod(tmp_path) -> None:
    """YouTube offline row keeps waiting-room link separate from VOD."""
    from datetime import datetime, timedelta, timezone

    upcoming_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    upcoming_item = VideoItem(
        video_id="sched1",
        title="Next stream",
        url="https://www.youtube.com/watch?v=sched1",
        style="UPCOMING",
        scheduled_start=upcoming_start,
    )

    class FetcherWithUpcoming(FakeYouTubeFetcher):
        def get_channel_items(self, channel_name: str) -> list[VideoItem]:
            return [upcoming_item]

        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            ended = (
                datetime.now(timezone.utc) - timedelta(minutes=30)
            ).isoformat()
            return FinishedVod(
                url="https://www.youtube.com/watch?v=archive1",
                ended_at=ended,
                title="Archive stream",
            )

    fetcher = FetcherWithUpcoming([])
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    confirmed = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    offline = monitor._build_youtube_offline_channel_status(
        entry,
        confirmed,
        fetcher=fetcher,
        payload=None,
        prev_cs=None,
    )

    assert offline.upcoming_url == "https://www.youtube.com/watch?v=sched1"
    assert offline.vod_url == "https://www.youtube.com/watch?v=archive1"
    assert offline.scheduled_start == upcoming_start
    assert offline.url == "https://www.youtube.com/@yt"
    db.close()


def test_youtube_offline_clears_expired_upcoming_url(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    fetcher = FakeYouTubeFetcher([])
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    expired_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    prev = ChannelStatus(
        status=False,
        upcoming_url="https://www.youtube.com/watch?v=old",
        scheduled_start=expired_start,
        ended_at=expired_start,
    )

    offline = monitor._build_youtube_offline_channel_status(
        entry,
        expired_start,
        fetcher=fetcher,
        payload=None,
        prev_cs=prev,
    )

    assert offline.upcoming_url == ""
    assert offline.scheduled_start == ""
    db.close()


def test_twitch_offline_never_sets_upcoming_url(monkeypatch, tmp_path) -> None:
    fetcher = FakeTwitchFetcher([True, False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    with monitor._lock:
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.upcoming_url == ""
        assert offline.scheduled_start == ""
    db.close()


def test_youtube_upcoming_is_usable_helper() -> None:
    from datetime import datetime, timedelta, timezone

    from stream_monitor.monitor import _youtube_upcoming_is_usable

    expired = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()

    assert _youtube_upcoming_is_usable(expired) is False
    assert _youtube_upcoming_is_usable(soon) is True
    assert _youtube_upcoming_is_usable(far) is False
    assert _youtube_upcoming_is_usable("") is False
    recent_past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert _youtube_upcoming_is_usable(recent_past) is False


def test_youtube_offline_ignores_upcoming_beyond_7_days(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    far = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    upcoming_item = VideoItem(
        video_id="far1",
        title="Far stream",
        url="https://www.youtube.com/watch?v=far1",
        style="UPCOMING",
        scheduled_start=far,
    )

    class FetcherWithFarUpcoming(FakeYouTubeFetcher):
        def get_channel_items(self, channel_name: str) -> list[VideoItem]:
            return [upcoming_item]

        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            ended = (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat()
            return FinishedVod(
                url="https://www.youtube.com/watch?v=vod1",
                ended_at=ended,
                title="Recent VOD",
            )

    fetcher = FetcherWithFarUpcoming([])
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    confirmed = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()

    offline = monitor._build_youtube_offline_channel_status(
        entry,
        confirmed,
        fetcher=fetcher,
        payload=None,
        prev_cs=None,
    )

    assert offline.upcoming_url == ""
    assert offline.vod_url == "https://www.youtube.com/watch?v=vod1"
    db.close()


def test_youtube_fallback_sets_upcoming_status(monkeypatch, tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    fetcher = FakeYouTubeFetcher([])

    def fake_get_stream_info(channel_name: str) -> StreamInfo:
        return StreamInfo(
            channel=channel_name,
            platform="youtube",
            is_live=False,
            title="Waiting Room",
            url="https://www.youtube.com/watch?v=wait1",
            video_id="wait1",
            stream_status="upcoming",
            scheduled_start=soon,
        )

    monkeypatch.setattr(
        fetcher, "get_channel_items", lambda _name, **kwargs: []
    )
    monkeypatch.setattr(fetcher, "get_stream_info", fake_get_stream_info)
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)

    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        status = monitor._last_status["youtube:yt"]
        assert isinstance(status, ChannelStatus)
        assert status.status == "upcoming"
        assert status.url == "https://www.youtube.com/watch?v=wait1"
        assert status.scheduled_start == soon
    db.close()


def test_twitch_no_went_offline_when_never_was_live(monkeypatch, tmp_path) -> None:
    fetcher = FakeTwitchFetcher([False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []
    db.close()


def test_twitch_cold_offline_shows_vod_when_archive_available(
    monkeypatch, tmp_path
) -> None:
    """Never-live Twitch channels show OFFLINE + VOD when ARCHIVE exists."""

    class FetcherWithArchive(FakeTwitchFetcher):
        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            from datetime import datetime, timedelta, timezone

            ended = (
                datetime.now(timezone.utc) - timedelta(hours=2)
            ).isoformat()
            return FinishedVod(
                url="https://www.twitch.tv/videos/cold1",
                ended_at=ended,
                title="Past broadcast",
            )

    fetcher = FetcherWithArchive([False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.status is False
        assert offline.vod_url == "https://www.twitch.tv/videos/cold1"
        assert offline.ended_at_source == "vod"
    db.close()


def test_twitch_cold_offline_stale_vod_uses_archive_elapsed(
    monkeypatch, tmp_path
) -> None:
    """Infrequent Twitch streamers: cold-start should show days-old VOD elapsed."""

    class FetcherWithStaleArchive(FakeTwitchFetcher):
        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            from datetime import datetime, timedelta, timezone

            ended = (
                datetime.now(timezone.utc) - timedelta(days=4)
            ).isoformat()
            return FinishedVod(
                url="https://www.twitch.tv/videos/stale1",
                ended_at=ended,
                title="Stream from last week",
            )

    fetcher = FetcherWithStaleArchive([False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.vod_url == "https://www.twitch.tv/videos/stale1"
        assert offline.ended_at_source == "vod"
        from datetime import datetime, timedelta, timezone

        ended_dt = datetime.fromisoformat(offline.ended_at)
        assert ended_dt < datetime.now(timezone.utc) - timedelta(days=3)
    db.close()


def test_youtube_cold_offline_rejects_merge_confirmed_from_future_vod(
    tmp_path,
) -> None:
    """Future/invalid VOD end must not become a poll-time offline elapsed."""
    from datetime import datetime, timedelta, timezone

    future_end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    class FutureVodFetcher(FakeYouTubeFetcher):
        def get_latest_finished_vod(
            self, channel_name: str, *, items=None
        ) -> FinishedVod | None:
            return FinishedVod(
                url="https://www.youtube.com/watch?v=bad",
                ended_at=future_end,
                title="Bad timing",
            )

    fetcher = FutureVodFetcher([])
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    offline = monitor._build_youtube_offline_channel_status(
        entry,
        datetime.now(timezone.utc).isoformat(),
        fetcher=fetcher,
        payload=None,
        prev_cs=None,
    )

    assert offline.ended_at == ""
    assert offline.ended_at_source == ""
    assert offline.vod_url == "https://www.youtube.com/watch?v=bad"
    db.close()


def test_youtube_cold_offline_feed_vod_without_end_time_shows_no_elapsed(
    tmp_path,
) -> None:
    """Never-live YouTube channels must not show poll time as ended_at."""
    from datetime import datetime, timezone

    class FeedVodOnlyFetcher(FakeYouTubeFetcher):
        def get_latest_finished_vod(
            self, channel_name: str, *, items=None
        ) -> FinishedVod | None:
            return FinishedVod(
                url="https://www.youtube.com/watch?v=hy_5pf6-des",
                title="Past stream",
            )

    fetcher = FeedVodOnlyFetcher([])
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")
    confirmed = datetime.now(timezone.utc).isoformat()

    offline = monitor._build_youtube_offline_channel_status(
        entry,
        confirmed,
        fetcher=fetcher,
        payload=None,
        prev_cs=None,
        extra_vod_url="https://www.youtube.com/watch?v=hy_5pf6-des",
    )

    assert offline.status is False
    assert offline.vod_url == "https://www.youtube.com/watch?v=hy_5pf6-des"
    assert offline.ended_at == ""
    assert offline.ended_at_source == ""
    db.close()


def test_twitch_tier2_clears_pending_without_archive(
    monkeypatch, tmp_path,
) -> None:
    """After tier-2, cold offline Twitch rows must leave pending state."""
    partial: dict[str, ChannelStatus] = {}

    def on_partial(statuses: dict[str, ChannelStatus], _names: dict[str, str]) -> None:
        partial.update(statuses)

    fetcher = FakeTwitchFetcher([False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "karlylinnea"}],
        db=db,
        on_partial_snapshot=on_partial,
    )
    entry = ChannelEntry(platform="twitch", name="karlylinnea")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        row = monitor._last_status["twitch:karlylinnea"]
        assert isinstance(row, ChannelStatus)
        assert row.ended_at_source != "pending"
        assert row.ended_at == ""
    db.close()


def test_twitch_cold_offline_shows_confirmed_without_archive(
    monkeypatch, tmp_path
) -> None:
    """Never-live Twitch channels show OFFLINE even when no ARCHIVE VOD exists."""
    fetcher = FakeTwitchFetcher([False, False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        offline = monitor._last_status["twitch:hello"]
        assert isinstance(offline, ChannelStatus)
        assert offline.status is False
        assert offline.ended_at == ""
        assert offline.ended_at_source == ""
        assert offline.vod_url == ""
    db.close()


def test_twitch_entry_key_normalizes_login_case() -> None:
    entry = ChannelEntry(platform="twitch", name="RunRunLuna")
    assert entry.name == "runrunluna"
    assert entry.key == "twitch:runrunluna"


def test_youtube_tidus_emits_went_offline_when_video_drops_out(
    monkeypatch, tmp_path
) -> None:
    """Poll 1 has a LIVE video; later polls have no LIVE items → went-offline fires.

    With the anti-flap guard, the fallback path needs *two* consecutive
    "not live" readings before committing to the offline edge.
    """
    live_item = VideoItem(
        video_id="vid1",
        title="Live Stream",
        url="https://www.youtube.com/watch?v=vid1",
        style="LIVE",
        display_name="YT Channel",
    )
    fetcher = FakeYouTubeFetcher(items_batches=[[live_item], [], []])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []

    # Poll 2: items list returns []; this triggers the fallback path. Need
    # to supply a non-live StreamInfo so fallback can see "not live".
    # First strike: transient — no offline event yet.
    fetcher.info_batches = [
        StreamInfo(
            channel="yt",
            platform="youtube",
            is_live=False,
            title="",
            url="",
        ),
        StreamInfo(
            channel="yt",
            platform="youtube",
            is_live=False,
            title="",
            url="",
        ),
    ]
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []

    # Poll 3: second consecutive "not live" → fallback now commits to offline.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        # Fallback path emits the offline event with the prior payload.
        assert len(monitor._pending_offline_events) >= 1
        urls = {p.url for _, p in monitor._pending_offline_events}
        assert "https://www.youtube.com/watch?v=vid1" in urls
    db.close()


def test_youtube_tidus_emits_went_offline_when_live_set_changes(
    monkeypatch, tmp_path
) -> None:
    """LIVE A in poll 1; LIVE B in polls 2-3 → A confirmed offline after 2 strikes."""
    live_a = VideoItem(
        video_id="A",
        title="Stream A",
        url="https://www.youtube.com/watch?v=A",
        style="LIVE",
    )
    live_b = VideoItem(
        video_id="B",
        title="Stream B",
        url="https://www.youtube.com/watch?v=B",
        style="LIVE",
    )
    fetcher = FakeYouTubeFetcher(items_batches=[[live_a], [live_b], [live_b]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)  # A live
    _check_and_commit(monitor, entry)  # A missing (strike 1)
    with monitor._lock:
        # Strike 1: A's offline edge is held back.
        offline_urls = {p.url for _, p in monitor._pending_offline_events}
        assert "https://www.youtube.com/watch?v=A" not in offline_urls

    _check_and_commit(monitor, entry)  # A still missing (strike 2 → confirmed)
    with monitor._lock:
        offline_urls = {p.url for _, p in monitor._pending_offline_events}
        assert "https://www.youtube.com/watch?v=A" in offline_urls
        assert "https://www.youtube.com/watch?v=B" not in offline_urls
    db.close()


def test_monitor_callback_signature_accepts_on_went_offline(tmp_path) -> None:
    """Construction accepts the new callback without breaking existing kwargs."""
    db = SeenVideoDB(tmp_path / "test.db")
    captured: list = []
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hi"}],
        on_went_offline=lambda entry, payload: captured.append((entry, payload)),
        db=db,
    )
    assert monitor._on_went_offline is not None
    db.close()


# ─────────────────────────────────────────────
# Anti-flap guard: single-poll API hiccups must NOT trigger duplicate
# went_live notifications or fake went_offline events.
# ─────────────────────────────────────────────
def test_twitch_single_offline_flap_does_not_emit_offline_event(
    monkeypatch, tmp_path
) -> None:
    """A single not-live reading between two live readings is ignored entirely."""
    fetcher = FakeTwitchFetcher([True, False, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    # Poll 1: went LIVE
    went_live_1 = _check_and_commit(monitor, entry)
    assert len(went_live_1) == 1

    # Poll 2: transient "not live" (Twitch GQL cache hiccup). Should be
    # absorbed by the strike guard — no offline event, last_status stays True.
    went_live_2 = _check_and_commit(monitor, entry)
    assert went_live_2 == []
    with monitor._lock:
        assert monitor._pending_offline_events == []
        assert monitor._last_status["twitch:hello"].status is True

    # Poll 3: back to live. Because last_status was preserved as True, this
    # is NOT a new went_live edge → no duplicate notification.
    went_live_3 = _check_and_commit(monitor, entry)
    assert went_live_3 == []
    with monitor._lock:
        assert monitor._pending_offline_events == []
    db.close()


def test_twitch_strike_resets_when_channel_returns_live(monkeypatch, tmp_path) -> None:
    """Once a previously-flapping channel returns live, the strike counter resets."""
    fetcher = FakeTwitchFetcher([True, False, True, False, False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hi"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hi")

    _check_and_commit(monitor, entry)  # live
    _check_and_commit(monitor, entry)  # not live (strike 1, ignored)
    _check_and_commit(monitor, entry)  # live again → strike reset
    with monitor._lock:
        assert monitor._offline_strikes == {}
        assert monitor._pending_offline_events == []

    # New offline streak should require TWO strikes again, not one, because
    # the previous strike was cleared by the intervening live reading.
    _check_and_commit(monitor, entry)  # not live (fresh strike 1)
    with monitor._lock:
        assert monitor._pending_offline_events == []
    _check_and_commit(monitor, entry)  # not live (strike 2 → confirmed)
    with monitor._lock:
        assert len(monitor._pending_offline_events) == 1
    db.close()


def test_twitch_fetch_none_increments_offline_strike_when_was_live(
    monkeypatch, tmp_path,
) -> None:
    """A failed fetch while LIVE counts toward the offline strike threshold."""
    fetcher = FakeTwitchFetcherReadings([True, None, None])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is True

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is True
        assert monitor._offline_strikes.get("twitch:hello|_") == 1
        assert monitor._pending_offline_events == []

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is False
        assert len(monitor._pending_offline_events) == 1
    db.close()


def test_twitch_live_after_fetch_none_commit_fires_went_live(
    monkeypatch, tmp_path,
) -> None:
    """After fetch failures clear a stale LIVE state, a new stream triggers went_live."""
    fetcher = FakeTwitchFetcherReadings([True, None, None, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    went_live: list[tuple[str, str]] = []
    for _ in range(4):
        results = _check_and_commit(monitor, entry)
        for evt_entry, info in results:
            went_live.append((evt_entry.key, info.title))

    assert went_live == [("twitch:hello", "Live now"), ("twitch:hello", "Live now")]
    db.close()


def test_twitch_went_live_suppressed_emits_log(
    monkeypatch, tmp_path, caplog,
) -> None:
    fetcher = FakeTwitchFetcher([True, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    caplog.set_level(logging.INFO)
    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    assert any("went_live_suppressed" in r.message for r in caplog.records)
    db.close()


def test_youtube_fallback_single_offline_flap_is_absorbed(
    monkeypatch, tmp_path
) -> None:
    """The YouTube fallback path must also debounce single-poll dropouts."""
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [], []],
        info_batches=[
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=True,
                title="Stream",
                url="https://www.youtube.com/@yt/live",
                display_name="YT",
            ),
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=False,
                title="",
                url="",
            ),
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=True,
                title="Stream",
                url="https://www.youtube.com/@yt/live",
                display_name="YT",
            ),
        ],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    # Poll 1: fallback (items empty), live → went_live event.
    events_1 = _check_and_commit(monitor, entry)
    assert len(events_1) == 1

    # Poll 2: fallback, single not-live reading — must NOT emit offline event,
    # must NOT fire a second went_live on the next poll.
    events_2 = _check_and_commit(monitor, entry)
    assert events_2 == []
    with monitor._lock:
        assert monitor._pending_offline_events == []
        assert monitor._last_status["youtube:yt"].status is True

    # Poll 3: fallback live again → no duplicate notification because last
    # status was preserved as True throughout the flap.
    events_3 = _check_and_commit(monitor, entry)
    assert events_3 == []
    db.close()


def test_youtube_tidus_single_missing_video_id_is_absorbed(
    monkeypatch, tmp_path
) -> None:
    """A single-poll dropout of a LIVE video in the TIDUS feed is ignored."""
    live_item = VideoItem(
        video_id="vidX",
        title="Live Stream",
        url="https://www.youtube.com/watch?v=vidX",
        style="LIVE",
        display_name="YT Channel",
    )
    other_item = VideoItem(
        video_id="other",
        title="Old VOD",
        url="https://www.youtube.com/watch?v=other",
        style="DEFAULT",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[live_item], [other_item], [live_item, other_item]]
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    # Poll 1: vidX is LIVE → went_live event.
    events_1 = _check_and_commit(monitor, entry)
    assert any(info.video_id == "vidX" for _e, info in events_1)

    # Poll 2: vidX missing from feed (single-poll TIDUS dropout). Strike 1
    # absorbs it — no offline event, last_status stays True.
    events_2 = _check_and_commit(monitor, entry)
    assert events_2 == []
    with monitor._lock:
        assert monitor._pending_offline_events == []
        assert monitor._last_status["youtube:yt"].status is True

    # Poll 3: vidX returns to the feed. No duplicate went_live event because
    # the strike kept the video in _live_payload (DB also has it marked seen).
    events_3 = _check_and_commit(monitor, entry)
    assert events_3 == []
    with monitor._lock:
        assert monitor._offline_strikes == {}
        assert monitor._pending_offline_events == []
    db.close()


def test_update_channels_clears_strikes_for_removed_entries(tmp_path) -> None:
    """Removing a channel from the watch list also drops its pending strikes."""
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[
            {"platform": "twitch", "name": "alice"},
            {"platform": "twitch", "name": "bob"},
        ],
        db=db,
    )
    # Inject strikes for both channels directly (simulating mid-stream state).
    with monitor._lock:
        monitor._offline_strikes = {
            monitor_module._live_cache_key("twitch:alice"): 1,
            monitor_module._live_cache_key("twitch:bob"): 1,
        }

    monitor.update_channels([{"platform": "twitch", "name": "alice"}])

    with monitor._lock:
        # Bob's strike must be gone; Alice's strike survives.
        keys = set(monitor._offline_strikes.keys())
        assert any("twitch:alice" in k for k in keys)
        assert not any("twitch:bob" in k for k in keys)
    db.close()


# ─────────────────────────────────────────────
# P1: TIDUS recovering after a fallback-live poll must not emit a fake
# went_offline for the fallback alias.
# ─────────────────────────────────────────────
def test_youtube_fallback_alias_does_not_emit_offline_when_tidus_recovers(
    monkeypatch, tmp_path
) -> None:
    """After fallback live → TIDUS live, the fallback alias is silently dropped."""
    live_item = VideoItem(
        video_id="vidX",
        title="Stream",
        url="https://www.youtube.com/watch?v=vidX",
        style="LIVE",
        display_name="YT Channel",
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[[], [live_item], [live_item], [live_item]],
        info_batches=[
            # Poll 1: TIDUS empty → fallback path sees the stream as live.
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=True,
                title="Stream",
                url="https://www.youtube.com/@yt/live",
                display_name="YT Channel",
            ),
        ],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    captured_offline: list = []
    monitor = Monitor(
        channels=[{"platform": "youtube", "name": "yt"}],
        db=db,
        on_went_offline=lambda e, p: captured_offline.append((e, p)),
    )
    entry = ChannelEntry(platform="youtube", name="yt")

    # Poll 1: fallback sees live → fallback alias stored in _live_payload.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor_module._live_cache_key("youtube:yt") in monitor._live_payload

    # Poll 2: TIDUS recovers → fallback alias must be dropped silently
    # (no strike accumulation, no offline event), TIDUS payload installed.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        # Fallback alias gone, no orphan strike, no pending offline event.
        assert monitor_module._live_cache_key("youtube:yt") not in monitor._live_payload
        assert monitor_module._live_cache_key("youtube:yt") not in monitor._offline_strikes
        assert monitor._pending_offline_events == []
        # TIDUS payload installed under the real video_id.
        assert (
            monitor_module._live_cache_key("youtube:yt", "vidX")
            in monitor._live_payload
        )

    # Poll 3: still live → no spurious offline event later either.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._pending_offline_events == []
    db.close()


# ─────────────────────────────────────────────
# P2: After TIDUS-live → fallback-confirmed-offline, the leftover TIDUS
# payload must NOT cause a second went_offline when TIDUS later returns.
# ─────────────────────────────────────────────
def test_fallback_offline_clears_all_tidus_payloads(monkeypatch, tmp_path) -> None:
    """Fallback offline = entire channel offline → all payloads emit once."""
    live_item = VideoItem(
        video_id="vidX",
        title="Stream",
        url="https://www.youtube.com/watch?v=vidX",
        style="LIVE",
        display_name="YT Channel",
    )
    new_live_item = VideoItem(
        video_id="vidY",
        title="Another Stream",
        url="https://www.youtube.com/watch?v=vidY",
        style="LIVE",
        display_name="YT Channel",
    )
    fetcher = FakeYouTubeFetcher(
        # Poll 1: TIDUS live (vidX). Poll 2-3: TIDUS empty (fallback strikes).
        # Poll 4: TIDUS comes back with a *different* video_id (vidY).
        items_batches=[[live_item], [], [], [new_live_item]],
        info_batches=[
            # Poll 2: fallback offline (strike 1, ignored).
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=False,
                title="",
                url="",
            ),
            # Poll 3: fallback offline (strike 2 → confirmed). Fires
            # went_offline for vidX and clears the leftover TIDUS payload.
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=False,
                title="",
                url="",
            ),
        ],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)  # Poll 1: vidX live
    _check_and_commit(monitor, entry)  # Poll 2: fallback offline strike 1
    with monitor._lock:
        assert monitor._pending_offline_events == []

    _check_and_commit(monitor, entry)  # Poll 3: fallback offline strike 2
    with monitor._lock:
        # Exactly one offline event — for vidX — was emitted via the cleared
        # TIDUS payload, not for the (non-existent) fallback alias.
        offline_payloads = [p for _e, p in monitor._pending_offline_events]
        vidX_events = [p for p in offline_payloads if p.video_id == "vidX"]
        assert len(vidX_events) == 1, (
            "fallback offline must emit went_offline for the prior TIDUS payload"
        )
        # The TIDUS payload was popped — no leftover that could re-fire later.
        assert (
            monitor_module._live_cache_key("youtube:yt", "vidX")
            not in monitor._live_payload
        )

    # Snapshot pending events count, then clear and run Poll 4: TIDUS returns
    # with a brand-new video_id (vidY). The cleared payload must NOT cause a
    # second went_offline for vidX.
    with monitor._lock:
        monitor._pending_offline_events.clear()
    _check_and_commit(monitor, entry)
    with monitor._lock:
        post_recovery_offlines = [
            p for _e, p in monitor._pending_offline_events
        ]
        assert post_recovery_offlines == [], (
            "no leftover TIDUS payload should remain to re-fire offline events"
        )
    db.close()


# ─────────────────────────────────────────────
# P3: A single-poll live dropout must not flip the UI to UPCOMING (or fire
# a phantom UPCOMING notification path's last_status side-effects).
# ─────────────────────────────────────────────
def test_channel_entry_carries_monitor_only_flag(tmp_path) -> None:
    """monitor_only on the channel dict propagates onto ChannelEntry."""
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[
            {"platform": "twitch", "name": "a", "monitor_only": True},
            {"platform": "twitch", "name": "b"},  # default False
            {"platform": "twitch", "name": "c", "monitor_only": False},
        ],
        db=db,
    )
    entries = {e.key: e for e in monitor._entries}
    assert entries["twitch:a"].monitor_only is True
    assert entries["twitch:b"].monitor_only is False
    assert entries["twitch:c"].monitor_only is False
    db.close()


def test_update_channels_refreshes_monitor_only_flag(tmp_path) -> None:
    """update_channels swaps in the new monitor_only flag, not the old one."""
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a", "monitor_only": False}],
        db=db,
    )
    assert monitor._entries[0].monitor_only is False

    monitor.update_channels(
        [{"platform": "twitch", "name": "a", "monitor_only": True}]
    )
    assert monitor._entries[0].monitor_only is True

    monitor.update_channels([{"platform": "twitch", "name": "a"}])
    assert monitor._entries[0].monitor_only is False
    db.close()


def test_youtube_single_live_dropout_with_upcoming_keeps_live_status(
    monkeypatch, tmp_path
) -> None:
    """When LIVE flaps for one poll but UPCOMING is present, last_status stays LIVE."""
    live_item = VideoItem(
        video_id="vidLive",
        title="Live Stream",
        url="https://www.youtube.com/watch?v=vidLive",
        style="LIVE",
        display_name="YT Channel",
    )
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    upcoming_item = VideoItem(
        video_id="vidNext",
        title="Premiere Later",
        url="https://www.youtube.com/watch?v=vidNext",
        style="UPCOMING",
        display_name="YT Channel",
        scheduled_start=soon,
    )
    fetcher = FakeYouTubeFetcher(
        items_batches=[
            [live_item, upcoming_item],
            [upcoming_item],  # LIVE flaps out, UPCOMING still there
            [live_item, upcoming_item],
        ]
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    # Poll 1: LIVE + UPCOMING → last_status = LIVE.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["youtube:yt"].status is True
        live_started_at_snapshot = dict(monitor._live_started_at)

    # Poll 2: LIVE drops out (single-poll dropout) but UPCOMING remains. The
    # anti-flap guard must hold last_status at LIVE rather than flipping to
    # "upcoming", and must NOT clear _live_started_at for the live video.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["youtube:yt"].status is True, (
            "single-poll LIVE dropout must not flip UI to UPCOMING"
        )
        # _live_started_at for vidLive must survive the dropout so the
        # "live since" timestamp stays stable.
        assert monitor._live_started_at == live_started_at_snapshot
        assert monitor._pending_offline_events == []

    # Poll 3: LIVE returns → still no duplicate notifications / events.
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["youtube:yt"].status is True
        assert monitor._pending_offline_events == []
    db.close()


# ─────────────────────────────────────────────
# Poll performance optimizations
# ─────────────────────────────────────────────


class SlowFetcher:
    """Fetcher that sleeps on each call to simulate HTTP latency."""

    platform = "twitch"
    delay_s = 0.2

    def __init__(self) -> None:
        self.call_count = 0
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        with self._lock:
            self.call_count += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            time.sleep(self.delay_s)
        finally:
            with self._lock:
                self._active -= 1
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=False,
            url=f"https://www.twitch.tv/{channel_name}",
        )

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        return []


class CountingTwitchFetcher:
    platform = "twitch"

    def __init__(self) -> None:
        self.calls = 0

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        self.calls += 1
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=False,
            url=f"https://www.twitch.tv/{channel_name}",
        )

    def get_channel_items(
        self,
        channel_name: str,
        *,
        fill_timing: bool = True,
        timeout: float | None = None,
    ) -> list[VideoItem]:
        return []


def test_poll_interval_compensation(monkeypatch) -> None:
    """Wait time should subtract poll elapsed so cycles start every interval."""
    sleeps: list[float] = []
    clock = [0.0]

    def fake_monotonic() -> float:
        return clock[0]

    def fake_sleep(duration: float) -> None:
        clock[0] += duration

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(time, "sleep", fake_sleep)

    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a", "enabled": True}],
        interval=10,
    )
    monitor._stop_event.clear()

    def controlled_wait(timeout: float) -> bool:
        sleeps.append(timeout)
        if len(sleeps) >= 2:
            monitor._stop_event.set()
        return monitor._stop_event.is_set()

    monitor._stop_event.wait = controlled_wait  # type: ignore[method-assign]

    def slow_probe(_entry: ChannelEntry):
        time.sleep(0.15)
        return []

    monkeypatch.setattr(monitor, "_probe_live", slow_probe)
    monkeypatch.setattr(monitor, "_refresh_details", lambda _e: monitor._noop_commit)
    monitor._run()

    assert len(sleeps) == 2
    assert sleeps[0] < 10.0
    assert sleeps[0] >= 9.5


def test_poll_rest_when_slower_than_interval(monkeypatch) -> None:
    """Overlong polls must still rest briefly instead of hammering APIs back-to-back."""
    sleeps: list[float] = []
    clock = [0.0]

    def fake_monotonic() -> float:
        return clock[0]

    def fake_sleep(duration: float) -> None:
        clock[0] += duration

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(time, "sleep", fake_sleep)

    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "a", "enabled": True}],
        interval=10,
    )
    monitor._stop_event.clear()

    def controlled_wait(timeout: float) -> bool:
        sleeps.append(timeout)
        monitor._stop_event.set()
        return True

    monitor._stop_event.wait = controlled_wait  # type: ignore[method-assign]
    monkeypatch.setattr(
        monitor,
        "_execute_poll_cycle",
        lambda _poll_started: 15.0,
    )
    monitor._run()

    assert sleeps == [monitor_module._MIN_POLL_REST_S]


def test_parallel_poll_faster_than_sequential(monkeypatch) -> None:
    """Parallel probes overlap HTTP work; sequential probes do not."""
    channels = [
        {"platform": "twitch", "name": f"ch{i}", "enabled": True} for i in range(12)
    ]

    seq_fetcher = SlowFetcher()
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: seq_fetcher)
    seq_monitor = Monitor(channels=channels, max_concurrent=1)
    for entry in seq_monitor._entries:
        seq_monitor._check_channel(entry)
    assert seq_fetcher.max_active == 1

    par_fetcher = SlowFetcher()
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: par_fetcher)
    par_monitor = Monitor(channels=channels, max_concurrent=4)
    enabled = [e for e in par_monitor._entries if e.enabled]
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(par_monitor._check_channel, e) for e in enabled]
        for f in as_completed(futures):
            f.result()
    assert par_fetcher.max_active >= 2


def test_twitch_skips_offline_retry_when_stable_offline(monkeypatch) -> None:
    fetcher = CountingTwitchFetcher()
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    monitor = Monitor(channels=[{"platform": "twitch", "name": "stable"}])
    entry = ChannelEntry(platform="twitch", name="stable")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(status=False)

    monitor._probe_live(entry)
    assert fetcher.calls == 1

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(status=True)

    monitor._probe_live(entry)
    assert fetcher.calls == 3


def test_youtube_poll_passes_fill_timing_false(monkeypatch, tmp_path) -> None:
    captured: list[bool] = []

    class TimingAwareFetcher(FakeYouTubeFetcher):
        def get_channel_items(
            self,
            channel_name: str,
            *,
            fill_timing: bool = True,
            timeout: float | None = None,
        ) -> list[VideoItem]:
            captured.append(fill_timing)
            return super().get_channel_items(
                channel_name, fill_timing=fill_timing, timeout=timeout
            )

    live = VideoItem(
        video_id="vid1",
        title="Live",
        style="LIVE",
        url="https://www.youtube.com/watch?v=vid1",
    )
    fetcher = TimingAwareFetcher(items_batches=[[live]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)

    assert captured == [False]
    db.close()


def test_youtube_offline_reuses_channel_items() -> None:
    from datetime import datetime, timedelta, timezone

    fetch_calls: list[str] = []

    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    upcoming = VideoItem(
        video_id="up1",
        title="Soon",
        style="UPCOMING",
        url="https://www.youtube.com/watch?v=up1",
        scheduled_start=soon,
    )

    class TrackingFetcher:
        platform = "youtube"

        def get_channel_items(
            self,
            channel_name: str,
            *,
            fill_timing: bool = True,
            timeout: float | None = None,
        ) -> list[VideoItem]:
            fetch_calls.append(channel_name)
            return []

        def enrich_upcoming_for_details(self, items: list[VideoItem]) -> None:
            return None

        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            return None

    fetcher = TrackingFetcher()
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}])
    entry = ChannelEntry(platform="youtube", name="yt")

    status = monitor._build_youtube_offline_channel_status(
        entry,
        "2020-01-01T00:00:00+00:00",
        fetcher=fetcher,
        payload=None,
        prev_cs=None,
        channel_items=[upcoming],
    )

    assert fetch_calls == []
    assert status.upcoming_url == upcoming.url


def test_youtube_never_live_upcoming_shows_offline_row(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    items = [
        VideoItem(
            video_id="vid_up",
            title="Waiting Room",
            style="UPCOMING",
            url="https://youtube.com/watch?v=vid_up",
            scheduled_start=soon,
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.upcoming_url == "https://youtube.com/watch?v=vid_up"
        assert row.scheduled_start == soon
    db.close()


def test_youtube_live_end_with_upcoming_keeps_offline_fields(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    live_item = VideoItem(
        video_id="vidLive",
        title="Live Stream",
        url="https://www.youtube.com/watch?v=vidLive",
        style="LIVE",
        display_name="YT",
    )
    upcoming_only = [
        VideoItem(
            video_id="vidNext",
            title="Next Premiere",
            style="UPCOMING",
            url="https://www.youtube.com/watch?v=vidNext",
            scheduled_start=soon,
        )
    ]

    class FetcherWithVod(FakeYouTubeFetcher):
        def get_latest_finished_vod(
        self, channel_name: str, *, items=None
    ) -> FinishedVod | None:
            ended = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            return FinishedVod(
                url="https://www.youtube.com/watch?v=vidLive",
                ended_at=ended,
                title="Live Stream",
            )

    fetcher = FetcherWithVod(
        [[live_item], upcoming_only, upcoming_only],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["youtube:yt"].status is True

    _check_and_commit(monitor, entry)
    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.ended_at
        assert row.vod_url == "https://www.youtube.com/watch?v=vidLive"
        assert row.upcoming_url == "https://www.youtube.com/watch?v=vidNext"
        assert row.scheduled_start == soon
    db.close()


def test_youtube_tier1_upcoming_notify_requires_surfacable_schedule(
    monkeypatch, tmp_path
) -> None:
    from datetime import datetime, timedelta, timezone

    bare = VideoItem(
        video_id="no_sched",
        title="Bare",
        style="UPCOMING",
        url="https://youtube.com/watch?v=no_sched",
    )
    expired = VideoItem(
        video_id="past",
        title="Past",
        style="UPCOMING",
        url="https://youtube.com/watch?v=past",
        scheduled_start=(
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(),
    )
    fetcher = FakeYouTubeFetcher([[bare], [expired]])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    assert _check_and_commit(monitor, entry) == []
    assert db.is_seen("no_sched", "UPCOMING") is False
    assert db.is_seen("past", "UPCOMING") is False
    db.close()


def test_youtube_upcoming_plus_default_vod_url(monkeypatch, tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    items = [
        VideoItem(
            video_id="up1",
            title="Waiting",
            style="UPCOMING",
            url="https://www.youtube.com/watch?v=up1",
            scheduled_start=soon,
        ),
        VideoItem(
            video_id="vod1",
            title="Last Upload",
            style="DEFAULT",
            url="https://www.youtube.com/watch?v=vod1",
        ),
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.upcoming_url == "https://www.youtube.com/watch?v=up1"
        assert row.vod_url == "https://www.youtube.com/watch?v=vod1"
    db.close()


def test_twitch_fetch_none_offline_commits_in_tier2(
    monkeypatch, tmp_path
) -> None:
    fetcher = FakeTwitchFetcherReadings([None, None])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.twitch.tv/hello",
            title="Live",
        )

    monitor._probe_live(entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._pending_offline_events == []

    monitor._probe_live(entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._pending_offline_events == []

    commit = monitor._refresh_details(entry)
    commit()
    with monitor._lock:
        assert monitor._last_status[entry.key].status is False
        assert len(monitor._pending_offline_events) == 1
    db.close()


def test_run_tier_gap_youtube_status(monkeypatch, tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    items = [
        VideoItem(
            video_id="vid_up",
            title="Waiting",
            style="UPCOMING",
            url="https://youtube.com/watch?v=vid_up",
            scheduled_start=soon,
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    monitor._probe_live(entry)
    with monitor._lock:
        assert entry.key not in monitor._last_status

    commit = monitor._refresh_details(entry)
    commit()
    with monitor._lock:
        row = monitor._last_status[entry.key]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.upcoming_url
    db.close()


def test_run_survives_probe_exception(monkeypatch, tmp_path) -> None:
    """An uncaught probe error must not kill the background monitor thread."""
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        interval=1,
        db=db,
    )

    def _boom(_entry: ChannelEntry) -> list:
        raise RuntimeError("probe boom")

    monkeypatch.setattr(monitor, "_probe_live", _boom)
    monitor.start()
    time.sleep(2.5)
    try:
        assert monitor.is_running
    finally:
        monitor.stop()
    db.close()


def test_twitch_fetch_exception_increments_strike_when_was_live(
    monkeypatch, tmp_path,
) -> None:
    """Fetch exceptions while LIVE count toward offline strikes (not frozen)."""
    fetcher = FakeTwitchFetcherReadings([True])
    calls = {"n": 0}
    orig_get = fetcher.get_stream_info

    def flaky_get(channel_name: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return orig_get(channel_name)
        raise RuntimeError("parse error")

    monkeypatch.setattr(fetcher, "get_stream_info", flaky_get)
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is True

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is True
        assert monitor._offline_strikes.get("twitch:hello|_") == 1
    db.close()


def test_wake_verify_same_live_refreshes_no_event(
    monkeypatch, tmp_path, caplog,
) -> None:
    fetcher = FakeTwitchFetcherReadings([True, True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    events: list[tuple[str, str]] = []
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}],
        on_status_change=lambda entry, info: events.append((entry.key, info.title)),
        db=db,
    )
    entry = ChannelEntry(platform="twitch", name="hello")
    _check_and_commit(monitor, entry)

    monkeypatch.setattr(monitor, "_probe_channel_status", lambda _e: "live")
    caplog.set_level(logging.INFO)
    monitor._run_wake_verification([entry], time.monotonic())

    assert events == []
    with monitor._lock:
        assert monitor._last_status["twitch:hello"].status is True
    assert any("wake_verify_confirmed" in r.message for r in caplog.records)
    db.close()


def test_wake_verify_mismatch_defers_offline(
    monkeypatch, tmp_path, caplog,
) -> None:
    fetcher = FakeTwitchFetcherReadings([False])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.twitch.tv/hello",
            title="Live",
        )

    monkeypatch.setattr(monitor, "_probe_channel_status", lambda _e: "offline")
    caplog.set_level(logging.INFO)
    monitor._run_wake_verification([entry], time.monotonic())

    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._pending_offline_events == []
    assert any("wake_verify_deferred" in r.message for r in caplog.records)
    db.close()


def test_wake_verify_fetch_none_deferred(
    monkeypatch, tmp_path, caplog,
) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.twitch.tv/hello",
            title="Live",
        )

    monkeypatch.setattr(monitor, "_probe_channel_status", lambda _e: None)
    caplog.set_level(logging.INFO)
    monitor._run_wake_verification([entry], time.monotonic())

    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
    assert any("wake_verify_deferred" in r.message for r in caplog.records)
    db.close()


def test_youtube_tidus_upcoming_wake_bucket_matches_probe(
    monkeypatch, tmp_path, caplog,
) -> None:
    """TIDUS offline+upcoming_url must bucket as upcoming for wake verify."""
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    items = [
        VideoItem(
            video_id="vid_up",
            title="Waiting Room",
            style="UPCOMING",
            url="https://youtube.com/watch?v=vid_up",
            scheduled_start=soon,
        )
    ]
    fetcher = FakeYouTubeFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)
    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert row.status is False
        assert row.upcoming_url

    assert monitor._cached_status_bucket(entry) == "upcoming"
    assert monitor._probe_channel_status(entry) == "upcoming"

    caplog.set_level(logging.INFO)
    monitor._run_wake_verification([entry], time.monotonic())
    assert any("wake_verify_confirmed" in r.message for r in caplog.records)
    db.close()


def test_run_maintenance_cleans_db_and_youtube_cache(tmp_path) -> None:
    from stream_monitor.fetcher.youtube import YouTubeFetcher, _WatchDetails

    db = SeenVideoDB(tmp_path / "test.db")
    db.mark_seen("oldvid", "youtube", "ch", "LIVE", title="t")
    db._conn.execute(
        "UPDATE seen_videos SET first_seen = '2020-01-01T00:00:00+00:00'"
    )
    db._conn.commit()

    YouTubeFetcher._watch_details_cache.clear()
    try:
        stale_ts = time.time() - 400
        YouTubeFetcher._watch_details_cache["stale"] = (
            stale_ts,
            _WatchDetails(),
        )

        monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], db=db)
        monitor._run_maintenance(force=True)

        assert db.is_seen("oldvid", "LIVE") is False
        assert "stale" not in YouTubeFetcher._watch_details_cache
    finally:
        YouTubeFetcher._watch_details_cache.clear()
        db.close()


def test_restart_thread_runs_maintenance(monkeypatch, tmp_path) -> None:
    db = SeenVideoDB(tmp_path / "test.db")
    calls: list[bool] = []
    monitor = Monitor(channels=[{"platform": "twitch", "name": "a"}], db=db)
    monkeypatch.setattr(
        monitor,
        "_run_maintenance",
        lambda *, force=False: calls.append(force),
    )
    def _run_once() -> None:
        monitor._run_maintenance(force=True)

    monkeypatch.setattr(monitor, "_run", _run_once)
    monitor.stop()
    monitor.restart_thread()
    assert calls == [True]
    db.close()


def test_restart_thread_preserves_last_status(monkeypatch, tmp_path) -> None:
    fetcher = FakeTwitchFetcherReadings([True])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "twitch", "name": "hello"}], db=db)
    entry = ChannelEntry(platform="twitch", name="hello")
    _check_and_commit(monitor, entry)

    with monitor._lock:
        expected = monitor._last_status[entry.key]
        monitor._twitch_seen_live.add(entry.key)
        monitor._offline_strikes["twitch:hello|_"] = 1

    monkeypatch.setattr(monitor, "_run", lambda: None)
    monitor.stop()
    monitor.restart_thread()
    time.sleep(0.1)
    try:
        with monitor._lock:
            assert monitor._last_status[entry.key] == expected
            assert entry.key in monitor._twitch_seen_live
            assert monitor._offline_strikes.get("twitch:hello|_") == 1
    finally:
        monitor.stop()
    db.close()


def test_empty_tidus_feed_strike_before_fallback(
    monkeypatch, tmp_path,
) -> None:
    fetcher = FakeYouTubeFetcher(
        items_batches=[[]],
        info_batches=[
            StreamInfo(
                channel="yt",
                platform="youtube",
                is_live=False,
                title="",
                url="",
            ),
        ],
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )
        monitor._fallback_triggered_live[entry.key] = "Live Stream"

    _check_and_commit(monitor, entry)
    with monitor._lock:
        assert monitor._last_status[entry.key].status is True
        assert monitor._offline_strikes.get("youtube:yt|_") == 1
        assert monitor._pending_offline_events == []
    db.close()


def test_youtube_tidus_fetch_failure_counts_strike_not_fallback(
    monkeypatch, tmp_path,
) -> None:
    """TIDUS HTTP failure (None) must not be treated as an empty feed."""
    fetcher = FakeYouTubeFetcher(items_batches=[])
    monkeypatch.setattr(
        fetcher,
        "get_channel_items",
        lambda _name, **kwargs: None,
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    with monitor._lock:
        monitor._last_status[entry.key] = ChannelStatus(
            status=True,
            url="https://www.youtube.com/watch?v=live1",
            title="Live Stream",
        )

    _check_and_commit(monitor, entry)
    with monitor._lock:
        snap = monitor._probe_snapshots.get(entry.key)
        assert snap is None or not snap.youtube_fallback
        assert monitor._last_status[entry.key].status is True
        assert monitor._offline_strikes.get("youtube:yt|_") == 1
    db.close()


def test_youtube_fetch_unavailable_cold_start_writes_offline(
    monkeypatch, tmp_path,
) -> None:
    """Cold-start TIDUS failure should still surface an offline row in the UI."""
    fetcher = FakeYouTubeFetcher(items_batches=[])
    monkeypatch.setattr(
        fetcher,
        "get_channel_items",
        lambda _name, **kwargs: None,
    )
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)

    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert isinstance(row, ChannelStatus)
        assert row.status is False
        assert row.ended_at_source == "pending"
        assert row.ended_at == ""
    db.close()


def test_youtube_vod_complete_skips_full_vod_rescan_without_upcoming(
    monkeypatch, tmp_path,
) -> None:
    """Stable offline rows with VOD should not re-fetch finished VOD every poll."""
    items = [
        VideoItem(
            video_id="vid1",
            title="Replay",
            style="DEFAULT",
            url="https://www.youtube.com/watch?v=vid1",
        )
    ]
    fetcher = FakeYouTubeFetcher([items, items])
    vod_calls: list[str] = []

    def counting_vod(channel_name: str, *, items=None):
        vod_calls.append(channel_name)
        return FinishedVod(
            url="https://www.youtube.com/watch?v=vid1",
            ended_at="2020-01-01T00:00:00+00:00",
            title="Replay",
        )

    monkeypatch.setattr(fetcher, "get_latest_finished_vod", counting_vod)
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    _check_and_commit(monitor, entry)
    _check_and_commit(monitor, entry)

    assert len(vod_calls) == 1
    with monitor._lock:
        row = monitor._last_status["youtube:yt"]
        assert row.vod_url.endswith("vid1")
        assert row.upcoming_url == ""
    db.close()


def test_snapshot_readable_during_slow_youtube_offline_refresh(
    monkeypatch, tmp_path,
) -> None:
    """snapshot_statuses() must stay responsive while VOD lookup runs."""
    import threading
    import time

    items = [
        VideoItem(
            video_id="vid1",
            title="Replay",
            style="DEFAULT",
            url="https://www.youtube.com/watch?v=vid1",
        )
    ]
    release_vod = threading.Event()

    class SlowVodFetcher(FakeYouTubeFetcher):
        def get_latest_finished_vod(self, channel_name: str, *, items=None):
            release_vod.wait(timeout=2)
            return FinishedVod(
                url="https://www.youtube.com/watch?v=vid1",
                ended_at="2020-01-01T00:00:00+00:00",
                title="Replay",
            )

    fetcher = SlowVodFetcher([items])
    monkeypatch.setattr(monitor_module, "get_fetcher", lambda _p: fetcher)
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(channels=[{"platform": "youtube", "name": "yt"}], db=db)
    entry = ChannelEntry(platform="youtube", name="yt")

    snap = monitor_module._ProbeSnapshot()
    snap.fetcher = fetcher
    snap.youtube_items = items

    refresh_error: list[BaseException] = []

    def run_refresh() -> None:
        try:
            commit = monitor._refresh_youtube(entry, snap)
            commit()
        except BaseException as exc:  # noqa: BLE001
            refresh_error.append(exc)
        finally:
            release_vod.set()

    worker = threading.Thread(target=run_refresh)
    worker.start()
    time.sleep(0.05)
    started = time.monotonic()
    monitor.snapshot_statuses()
    elapsed = time.monotonic() - started
    release_vod.set()
    worker.join(timeout=2)
    assert not refresh_error
    assert elapsed < 0.2
    db.close()


def test_execute_poll_cycle_dispatches_all_live_before_poll_complete(
    monkeypatch, tmp_path,
) -> None:
    """Went-live callbacks must batch until tier-2 commits, before poll_complete."""
    order: list[str] = []

    class MultiLiveTwitchFetcher:
        platform = "twitch"

        def get_stream_info(self, channel_name: str) -> StreamInfo:
            return StreamInfo(
                channel=channel_name,
                platform="twitch",
                is_live=True,
                title=f"Live {channel_name}",
                url=f"https://www.twitch.tv/{channel_name}",
            )

        def get_channel_items(
            self,
            channel_name: str,
            *,
            fill_timing: bool = True,
            timeout: float | None = None,
        ) -> list[VideoItem]:
            return []

        def get_latest_finished_vod(
            self, channel_name: str, *, items=None
        ) -> FinishedVod | None:
            return None

    monkeypatch.setattr(
        monitor_module, "get_fetcher", lambda _p: MultiLiveTwitchFetcher()
    )
    db = SeenVideoDB(tmp_path / "test.db")
    monitor = Monitor(
        channels=[
            {"platform": "twitch", "name": "a"},
            {"platform": "twitch", "name": "b"},
            {"platform": "twitch", "name": "c"},
        ],
        max_concurrent=4,
        on_status_change=lambda entry, _info: order.append(f"live:{entry.name}"),
        on_poll_complete=lambda: order.append("poll_complete"),
        db=db,
    )
    monitor._execute_poll_cycle(time.monotonic())

    assert order[-1] == "poll_complete"
    assert {e for e in order if e != "poll_complete"} == {
        "live:a",
        "live:b",
        "live:c",
    }
    db.close()
