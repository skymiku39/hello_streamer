"""Tests for BrowserSettings dataclass."""

from __future__ import annotations

from stream_monitor.browser_settings_model import (
    BrowserSettings,
    apply_ui_dimensions,
    coerce_browser_settings,
    infer_launch_mode,
)


def test_browser_settings_round_trip_dict() -> None:
    raw = {
        "enabled": True,
        "browser_path": "edge",
        "close_on_offline": True,
        "unknown_key": "ignored",
    }
    settings = BrowserSettings.from_dict(raw)
    assert settings.enabled is True
    assert settings.browser_path == "edge"
    assert settings.close_on_offline is True
    assert "unknown_key" not in settings.to_dict()


def test_coerce_browser_settings_accepts_dataclass() -> None:
    original = BrowserSettings(enabled=True)
    assert coerce_browser_settings(original) is original


def test_apply_ui_dimensions_returns_dict_compatible_with_from_dict() -> None:
    raw = apply_ui_dimensions(
        launch="program",
        identity="dedicated",
        placement="player",
        user_data_dir="C:/profiles/stream",
        per_channel_profile=True,
        browser_path="chrome",
        apply_geometry=True,
        x=10,
        y=20,
        width=800,
        height=600,
        minimized=False,
        close_on_offline=True,
        close_on_stop=False,
        close_off_topic_pages=False,
        hide_from_taskbar=False,
    )
    settings = BrowserSettings.from_dict(raw)
    assert settings.enabled is True
    assert settings.app_mode is True
    assert settings.user_data_dir == "C:/profiles/stream"
    assert infer_launch_mode(settings) == "program"
