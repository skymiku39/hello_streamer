"""Tests for monitor snapshot → ChannelRow status rendering."""

from __future__ import annotations

import pytest

from stream_monitor import i18n
from stream_monitor.app import App
from stream_monitor.app_ui import _format_row_time
from stream_monitor.channel_row import ChannelRow
from stream_monitor.event_bridge import (
    prefer_richer_offline_status,
    row_has_richer_offline_detail,
)
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


def _label_colors(row) -> tuple[str, str]:
    """Return (text_color, fg_color) of the row's status badge."""
    return (
        str(row.status_label.cget("text_color")),
        str(row.status_label.cget("fg_color")),
    )


def test_status_badge_live_uses_green_filled_badge() -> None:
    root, row = _make_row()
    try:
        row.set_status(ChannelStatus(status=True, url="u", title="t"))
        text_color, fg_color = _label_colors(row)
        assert text_color == "white"
        assert fg_color == "#1b5e20"
        assert str(row.status_label.cget("cursor")) == "hand2"
    finally:
        root.destroy()


def test_status_badge_upcoming_uses_orange_filled_badge() -> None:
    root, row = _make_row()
    try:
        row.set_status(ChannelStatus(status="upcoming", url="u", title="t"))
        text_color, fg_color = _label_colors(row)
        assert text_color == "white"
        assert fg_color == "#e65100"
    finally:
        root.destroy()


def test_status_badge_offline_is_muted_transparent() -> None:
    root, row = _make_row()
    try:
        row.set_status(ChannelStatus(status=False))
        text_color, fg_color = _label_colors(row)
        assert text_color == "#999999"
        assert fg_color == "transparent"
    finally:
        root.destroy()


def test_status_badge_idle_is_placeholder_transparent() -> None:
    root, row = _make_row()
    try:
        row.set_status(None)
        row._render_status_visuals()
        text_color, fg_color = _label_colors(row)
        assert text_color == "#666677"
        assert fg_color == "transparent"
    finally:
        root.destroy()


def test_paused_row_shows_disabled_visual() -> None:
    from stream_monitor.app_ui import _CLR_TEXT_DISABLED

    root, row = _make_row()
    try:
        row.channel["enabled"] = False
        row._apply_enabled_visual()
        text_color, _ = _label_colors(row)
        assert text_color == _CLR_TEXT_DISABLED
    finally:
        root.destroy()


def test_monitor_only_keeps_channel_enabled_and_flags_suppression() -> None:
    root, row = _make_row()
    try:
        row.channel["enabled"] = True
        row.channel["monitor_only"] = False
        row._on_monitor_only_click()
        assert row.channel["enabled"] is True
        assert row.channel["monitor_only"] is True
        # toggling again clears monitor-only (back to full triggering)
        row._on_monitor_only_click()
        assert row.channel["monitor_only"] is False
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
    assert _format_row_time("offline", "") == "無時間資料"
    assert (
        _format_row_time("offline", "", ended_at_source="pending")
        == "未開播，待深度檢查"
    )


def test_prefer_richer_offline_status_keeps_resolved_empty_over_pending() -> None:
    resolved = ChannelStatus(status=False, ended_at="", ended_at_source="")
    pending = ChannelStatus(status=False, ended_at_source="pending")
    assert prefer_richer_offline_status(resolved, pending) is resolved


def test_prefer_richer_offline_status_keeps_vod_over_pending() -> None:
    rich = ChannelStatus(
        status=False,
        ended_at="2020-01-01T12:00:00+00:00",
        ended_at_source="vod",
        vod_url="https://www.twitch.tv/videos/1",
    )
    pending = ChannelStatus(status=False, ended_at_source="pending")
    assert prefer_richer_offline_status(rich, pending) is rich


def test_row_skips_pending_downgrade_when_resolved_empty() -> None:
    root, row = _make_row()
    try:
        row.set_status(ChannelStatus(status=False, ended_at="", ended_at_source=""))
        pending = ChannelStatus(status=False, ended_at_source="pending")
        assert row_has_richer_offline_detail(row, pending) is True
    finally:
        root.destroy()


def test_row_skips_pending_downgrade_when_vod_detail_present() -> None:
    root, row = _make_row()
    try:
        row.set_status(
            ChannelStatus(
                status=False,
                ended_at="2020-01-01T12:00:00+00:00",
                ended_at_source="vod",
                vod_url="https://www.twitch.tv/videos/1",
            )
        )
        pending = ChannelStatus(status=False, ended_at_source="pending")
        assert row_has_richer_offline_detail(row, pending) is True
    finally:
        root.destroy()


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
