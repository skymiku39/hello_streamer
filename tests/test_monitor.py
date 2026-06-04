import logging

from stream_monitor import monitor as monitor_module
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo, VideoItem
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

    def get_stream_info(self, channel_name: str) -> StreamInfo:
        is_live = self.statuses.pop(0)
        return StreamInfo(
            channel=channel_name,
            platform="twitch",
            is_live=is_live,
            title="Live now" if is_live else "",
            url=f"https://www.twitch.tv/{channel_name}",
            display_name="Hello Channel",
        )

    def get_channel_items(self, channel_name: str) -> list[VideoItem]:
        return []


class FakeTwitchFetcherReadings:
    """Twitch fetcher that can return ``None`` (failed fetch) per poll."""

    platform = "twitch"

    def __init__(self, readings: list[bool | None]) -> None:
        self.readings = readings

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        val = self.readings.pop(0)
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

    def get_channel_items(self, channel_name: str) -> list[VideoItem]:
        return []


class FakeYouTubeFetcher:
    platform = "youtube"

    def __init__(
        self,
        items_batches: list[list[VideoItem]],
        info_batches: list[StreamInfo | None] | None = None,
    ) -> None:
        self.items_batches = items_batches
        self.info_batches = info_batches or []

    def get_stream_info(self, channel_name: str) -> StreamInfo | None:
        if self.info_batches:
            return self.info_batches.pop(0)
        return None

    def get_channel_items(self, channel_name: str) -> list[VideoItem]:
        if self.items_batches:
            return self.items_batches.pop(0)
        return []


# ─────────────────────────────────────────────
# Twitch: boolean edge-trigger (unchanged)
# ─────────────────────────────────────────────
def test_twitch_triggers_only_on_live_transition(monkeypatch, tmp_path) -> None:
    # Sequence chosen to exercise *confirmed* offline transitions only —
    # the anti-flap guard requires two consecutive "not live" readings
    # before it commits to the offline edge (see _OFFLINE_STRIKE_THRESHOLD).
    fetcher = FakeTwitchFetcher([False, True, True, False, False, True])
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


def test_youtube_upcoming_sets_status(monkeypatch, tmp_path) -> None:
    items = [
        VideoItem(
            video_id="vid_upcoming",
            title="Waiting Room",
            style="UPCOMING",
            url="https://youtube.com/watch?v=vid_upcoming",
            display_name="YT Chan",
            scheduled_start="2026-05-06T12:00:00+00:00",
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
    assert monitor.snapshot_statuses() == {"youtube:ytchan": "upcoming"}
    assert db.is_seen("vid_upcoming", "UPCOMING") is True
    db.close()


def test_youtube_upcoming_status_uses_nearest_scheduled_start(
    monkeypatch, tmp_path
) -> None:
    later = VideoItem(
        video_id="later_waiting",
        title="Later Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=later_waiting",
        display_name="YT Chan",
        scheduled_start="2026-05-06T13:00:00+00:00",
    )
    sooner = VideoItem(
        video_id="sooner_waiting",
        title="Sooner Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=sooner_waiting",
        display_name="YT Chan",
        scheduled_start="2026-05-06T12:00:00+00:00",
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
    assert status == "upcoming"
    assert status.url == "https://youtube.com/watch?v=sooner_waiting"
    assert status.scheduled_start == "2026-05-06T12:00:00+00:00"
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
    upcoming = VideoItem(
        video_id="same_vid",
        title="Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=same_vid",
        display_name="YT Chan",
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
    old = VideoItem(
        video_id="old_waiting",
        title="Old Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=old_waiting",
        display_name="YT Chan",
    )
    new = VideoItem(
        video_id="new_waiting",
        title="New Waiting Room",
        style="UPCOMING",
        url="https://youtube.com/watch?v=new_waiting",
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
        def get_latest_archive_url(self, channel_name: str) -> str:
            return "https://www.twitch.tv/videos/archive1"

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
        assert offline.ended_at
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
    upcoming_item = VideoItem(
        video_id="vidNext",
        title="Premiere Later",
        url="https://www.youtube.com/watch?v=vidNext",
        style="UPCOMING",
        display_name="YT Channel",
        scheduled_start="2099-01-01T00:00:00+00:00",
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
