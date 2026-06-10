"""Tests for monitor snapshot → ChannelRow status rendering."""

from __future__ import annotations

import pytest

from stream_monitor import i18n
from stream_monitor.app import App, ChannelRow
from stream_monitor.app_ui import _format_row_time
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import ChannelStatus


def _make_row():
    try:
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
    except Exception as exc:  # noqa: BLE001 — Tcl/Tk missing on headless CI
        pytest.skip(f"Tk unavailable in this environment: {exc}")
    channel = {"platform": "twitch", "name": "hello", "enabled": True}
    row = ChannelRow(
        root,
        channel,
        on_delete=lambda: None,
        on_move_up=lambda: None,
        on_move_down=lambda: None,
        on_toggle_enabled=lambda: None,
    )
    return root, row


def test_channel_row_live_status() -> None:
    root, row = _make_row()
    try:
        row.set_status(
            ChannelStatus(
                status=True,
                url="https://www.twitch.tv/hello",
                title="Live title",
                started_at="2020-01-01T12:00:00+00:00",
            )
        )
        assert row._status_state == "live"
        assert row._status_title == "Live title"
    finally:
        root.destroy()


def test_channel_row_offline_status() -> None:
    root, row = _make_row()
    try:
        row.set_status(
            ChannelStatus(
                status=False,
                ended_at="2020-01-01T12:00:00+00:00",
                ended_at_source="confirmed",
                url="https://www.twitch.tv/hello",
            )
        )
        assert row._status_state == "offline"
    finally:
        root.destroy()


def test_channel_row_upcoming_status() -> None:
    root, row = _make_row()
    try:
        row.set_status(
            ChannelStatus(
                status="upcoming",
                url="https://www.youtube.com/watch?v=up1",
                title="Waiting room",
                scheduled_start="2099-01-01T12:00:00+00:00",
            )
        )
        assert row._status_state == "upcoming"
    finally:
        root.destroy()


def test_channel_row_none_resets_placeholder() -> None:
    root, row = _make_row()
    try:
        assert row._status_state is None
        row.set_status(None)
        assert row._status_state is None
    finally:
        root.destroy()


def test_channel_status_from_stream_info_maps_live_fields() -> None:
    info = StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Stream title",
        url="https://www.twitch.tv/hello",
        started_at="2020-01-01T12:00:00+00:00",
    )
    status = App._channel_status_from_stream_info(info)
    assert status.status is True
    assert status.title == "Stream title"
    assert status.url == "https://www.twitch.tv/hello"
    assert status.started_at == "2020-01-01T12:00:00+00:00"


def test_channel_status_from_stream_info_maps_youtube_upcoming() -> None:
    info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=False,
        title="Waiting room",
        url="https://www.youtube.com/watch?v=up1",
        stream_status="upcoming",
        scheduled_start="2099-01-01T12:00:00+00:00",
    )
    status = App._channel_status_from_stream_info(info)
    assert status.status == "upcoming"
    assert status.scheduled_start == "2099-01-01T12:00:00+00:00"


def test_channel_status_from_stream_info_never_maps_upcoming_as_live() -> None:
    """Regression: v0.9.14 edge handler used status=True for every StreamInfo."""
    info = StreamInfo(
        channel="ytchan",
        platform="youtube",
        is_live=False,
        title="Waiting room",
        url="https://www.youtube.com/watch?v=up1",
        stream_status="upcoming",
        scheduled_start="2099-01-01T12:00:00+00:00",
    )
    status = App._channel_status_from_stream_info(info)
    assert status.status == "upcoming"
    assert status.status is not True


def test_format_row_time_live_offline_upcoming() -> None:
    i18n.set_language("zh_TW", notify=False)
    assert _format_row_time("live", "2h 15m") == "已開播 2h 15m"
    assert _format_row_time("offline", "30m") == "已下播 30m"
    assert _format_row_time("upcoming", "1h 0m") == "1h 0m 後開始"
    assert _format_row_time("countdown", "45m") == "45m 後開始"
    assert _format_row_time("live", "") == ""


def test_status_snapshot_guard_preserves_existing_state() -> None:
    """Mimic _poll_events: missing snapshot key must not wipe a painted row."""
    root, row = _make_row()
    try:
        row.set_status(
            ChannelStatus(
                status=False,
                ended_at="2020-01-01T12:00:00+00:00",
                ended_at_source="confirmed",
            )
        )
        status = None
        if status is None:
            if row._status_state in ("live", "offline", "upcoming"):
                pass
            else:
                row.set_status(None)
        else:
            row.set_status(status)
        assert row._status_state == "offline"
    finally:
        root.destroy()
