"""Tests for YouTube finished-VOD timing helpers."""

from stream_monitor.fetcher.youtube import YouTubeFetcher


def test_estimate_ended_at_from_end_timestamp() -> None:
    ended = YouTubeFetcher._estimate_ended_at_from_player(
        {},
        {"endTimestamp": "2026-06-05T10:00:00Z"},
    )
    assert ended == "2026-06-05T10:00:00+00:00"


def test_estimate_ended_at_from_upload_and_length() -> None:
    ended = YouTubeFetcher._estimate_ended_at_from_player(
        {"uploadDate": "20260605", "lengthSeconds": "3600"},
        {},
    )
    assert ended.endswith("+00:00")
    assert "T01:00:00" in ended
