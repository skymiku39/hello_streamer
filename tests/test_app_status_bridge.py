"""Tests for monitor snapshot → ChannelRow status rendering."""

from __future__ import annotations

import pytest

from stream_monitor.app import ChannelRow
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
