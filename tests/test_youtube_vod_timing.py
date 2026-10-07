"""Tests for YouTube finished-VOD timing helpers."""

import pytest

from stream_monitor.fetcher.youtube import YouTubeFetcher


def test_estimate_ended_at_from_end_timestamp() -> None:
    ended = YouTubeFetcher._estimate_ended_at_from_player(
        {},
        {"endTimestamp": "2026-06-05T10:00:00Z"},
    )
    assert ended == "2026-06-05T10:00:00+00:00"


@pytest.mark.parametrize("upload_date", ["20260605", "2026-06-05"])
def test_estimate_ended_at_from_upload_and_length(upload_date) -> None:
    ended = YouTubeFetcher._estimate_ended_at_from_player(
        {"uploadDate": upload_date, "lengthSeconds": "3600"},
        {},
    )
    assert ended.endswith("+00:00")
    assert "T01:00:00" in ended
