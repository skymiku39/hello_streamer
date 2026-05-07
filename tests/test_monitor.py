from stream_monitor import monitor as monitor_module
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo, VideoItem
from stream_monitor.monitor import ChannelEntry, Monitor


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
    fetcher = FakeTwitchFetcher([False, True, True, False, True])
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
    for _ in range(5):
        results = monitor._check_channel(entry)
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

    results = monitor._check_channel(entry)
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
    results = monitor._check_channel(entry)

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

    results_first = monitor._check_channel(entry)
    assert len(results_first) == 1

    results_second = monitor._check_channel(entry)
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
    results = monitor._check_channel(entry)

    assert results == []
    assert monitor.snapshot_statuses() == {"youtube:ytchan": "upcoming"}
    assert db.is_seen("vid_upcoming", "UPCOMING") is True
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
    results = monitor._check_channel(entry)

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

    assert monitor._check_channel(entry) == []
    results = monitor._check_channel(entry)

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

    assert monitor._check_channel(entry) == []
    results = monitor._check_channel(entry)

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

    assert monitor._check_channel(entry) == []
    assert monitor._check_channel(entry) == []
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
    results = monitor._check_channel(entry)

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

    results_fallback = monitor._check_channel(entry)
    assert len(results_fallback) == 1
    assert results_fallback[0][1].title == "Live Stream"

    results_tidus = monitor._check_channel(entry)
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

    results_fallback = monitor._check_channel(entry)
    assert len(results_fallback) == 1

    results_tidus = monitor._check_channel(entry)
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

    monitor._check_channel(entry)

    results = monitor._check_channel(entry)
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

    monitor._check_channel(entry)
    assert monitor.snapshot_statuses() == {"twitch:hello": True}

    monitor.update_channels(
        [{"platform": "twitch", "name": "hello", "enabled": False}]
    )
    monitor.update_channels(
        [{"platform": "twitch", "name": "hello", "enabled": True}]
    )
    assert monitor.snapshot_statuses().get("twitch:hello") is None

    results = monitor._check_channel(entry)
    assert len(results) == 1
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
