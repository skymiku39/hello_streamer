"""Tests for the cross-restart channel status cache."""

from __future__ import annotations

from datetime import datetime, timezone

from stream_monitor import status_cache
from stream_monitor.domain import ChannelStatus


def test_serialize_live_status_keeps_started_at() -> None:
    status = ChannelStatus(
        status=True,
        url="https://www.twitch.tv/foo",
        title="Live now",
        started_at="2026-06-18T10:00:00+00:00",
    )
    data = status_cache.serialize_status(status)
    assert data == {
        "state": "live",
        "url": "https://www.twitch.tv/foo",
        "title": "Live now",
        "started_at": "2026-06-18T10:00:00+00:00",
    }


def test_serialize_offline_status_carries_timing_fields() -> None:
    status = ChannelStatus(
        status=False,
        url="https://youtube.com/@bar",
        ended_at="2026-06-18T09:00:00+00:00",
        vod_url="https://youtube.com/watch?v=x",
        ended_at_source="vod",
    )
    data = status_cache.serialize_status(status)
    assert data["state"] == "offline"
    assert data["ended_at"] == "2026-06-18T09:00:00+00:00"
    assert data["vod_url"] == "https://youtube.com/watch?v=x"
    assert data["ended_at_source"] == "vod"


def test_serialize_upcoming_status() -> None:
    status = ChannelStatus(
        status="upcoming",
        scheduled_start="2026-06-19T00:00:00+00:00",
    )
    data = status_cache.serialize_status(status)
    assert data["state"] == "upcoming"
    assert data["scheduled_start"] == "2026-06-19T00:00:00+00:00"


def test_serialize_primitive_and_none() -> None:
    assert status_cache.serialize_status(True) == {"state": "live"}
    assert status_cache.serialize_status(False) == {"state": "offline"}
    assert status_cache.serialize_status(None) is None
    assert status_cache.serialize_status(ChannelStatus(status=None)) is None


def test_build_cache_has_saved_at_and_display_names() -> None:
    statuses = {
        "twitch|foo": ChannelStatus(status=True, started_at="2026-06-18T10:00:00+00:00"),
        "youtube|bar": None,  # skipped
    }
    cache = status_cache.build_cache(statuses, {"twitch|foo": "Foo"})
    assert "saved_at" in cache
    assert "youtube|bar" not in cache["channels"]
    assert cache["channels"]["twitch|foo"]["display_name"] == "Foo"


def test_round_trip_restore_statuses() -> None:
    statuses = {
        "twitch|foo": ChannelStatus(
            status=True,
            url="https://www.twitch.tv/foo",
            started_at="2026-06-18T10:00:00+00:00",
        ),
        "youtube|bar": ChannelStatus(
            status="upcoming",
            scheduled_start="2026-06-19T00:00:00+00:00",
        ),
    }
    cache = status_cache.build_cache(statuses)
    restored = status_cache.restore_statuses(cache)
    assert restored["twitch|foo"].status is True
    assert restored["twitch|foo"].started_at == "2026-06-18T10:00:00+00:00"
    assert restored["youtube|bar"].status == "upcoming"


def test_restore_is_tolerant_of_malformed_entries() -> None:
    cache = {
        "channels": {
            "good": {"state": "live"},
            "bad_state": {"state": "weird"},
            "not_a_dict": "oops",
            123: {"state": "live"},
        }
    }
    restored = status_cache.restore_statuses(cache)
    assert set(restored) == {"good"}
    assert restored["good"].status is True


def test_restore_statuses_handles_non_dict() -> None:
    assert status_cache.restore_statuses(None) == {}
    assert status_cache.restore_statuses({"channels": []}) == {}


def test_restore_display_names() -> None:
    cache = {
        "channels": {
            "a": {"state": "live", "display_name": "Alice"},
            "b": {"state": "offline"},
        }
    }
    assert status_cache.restore_display_names(cache) == {"a": "Alice"}


def test_saved_at_epoch_parses_iso() -> None:
    iso = "2026-06-18T10:00:00+00:00"
    expected = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    cache = {"saved_at": iso, "channels": {}}
    assert status_cache.saved_at_epoch(cache) == expected


def test_saved_at_epoch_missing_returns_zero() -> None:
    assert status_cache.saved_at_epoch({}) == 0.0
    assert status_cache.saved_at_epoch(None) == 0.0
    assert status_cache.saved_at_epoch({"saved_at": "not-a-date"}) == 0.0
