"""Tests for BrowserSettings dataclass."""

from __future__ import annotations

from stream_monitor.browser_settings_model import (
    CAP_OFF,
    CAP_OK,
    CAP_WARN,
    IDENTITY_DEDICATED,
    IDENTITY_LOCAL,
    LAUNCH_PROGRAM,
    LAUNCH_SYSTEM,
    PLACEMENT_PLAYER,
    PLACEMENT_TAB,
    PLACEMENT_WINDOW,
    BrowserSettings,
    apply_ui_dimensions,
    auto_cleanup_ui_available,
    capability_summary,
    coerce_browser_settings,
    geometry_placement_available,
    infer_launch_mode,
    window_management_available,
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


# --- interlock rules that drive the settings dialog's dynamic UI ---


def test_auto_cleanup_only_for_dedicated_program() -> None:
    assert auto_cleanup_ui_available(LAUNCH_PROGRAM, IDENTITY_DEDICATED) is True
    assert auto_cleanup_ui_available(LAUNCH_PROGRAM, IDENTITY_LOCAL) is False
    assert auto_cleanup_ui_available(LAUNCH_SYSTEM, IDENTITY_DEDICATED) is False


def test_geometry_only_for_separate_or_solo_window() -> None:
    assert geometry_placement_available(LAUNCH_PROGRAM, PLACEMENT_WINDOW) is True
    assert geometry_placement_available(LAUNCH_PROGRAM, PLACEMENT_PLAYER) is True
    assert geometry_placement_available(LAUNCH_PROGRAM, PLACEMENT_TAB) is False
    assert geometry_placement_available(LAUNCH_SYSTEM, PLACEMENT_WINDOW) is False


def test_window_management_needs_program_dedicated_and_own_window() -> None:
    assert (
        window_management_available(
            LAUNCH_PROGRAM, IDENTITY_DEDICATED, PLACEMENT_PLAYER
        )
        is True
    )
    assert (
        window_management_available(
            LAUNCH_PROGRAM, IDENTITY_DEDICATED, PLACEMENT_WINDOW
        )
        is True
    )
    # tab placement can't be tracked/managed
    assert (
        window_management_available(
            LAUNCH_PROGRAM, IDENTITY_DEDICATED, PLACEMENT_TAB
        )
        is False
    )
    # local identity shares the user's window → not manageable
    assert (
        window_management_available(
            LAUNCH_PROGRAM, IDENTITY_LOCAL, PLACEMENT_PLAYER
        )
        is False
    )


def test_capability_summary_system_launch_is_all_off() -> None:
    chips = capability_summary(LAUNCH_SYSTEM, IDENTITY_LOCAL, PLACEMENT_TAB)
    assert [status for _key, status in chips] == [CAP_OFF, CAP_OFF, CAP_OFF, CAP_OFF]


def test_capability_summary_dedicated_player_manage_ok() -> None:
    chips = dict(
        capability_summary(LAUNCH_PROGRAM, IDENTITY_DEDICATED, PLACEMENT_PLAYER)
    )
    assert chips["browser.cap.launch"] == CAP_OK
    assert chips["browser.cap.login"] == CAP_WARN  # dedicated account warns
    assert chips["browser.cap.window"] == CAP_OK
    assert chips["browser.cap.manage"] == CAP_OK


def test_capability_summary_local_tab_manage_off_login_ok() -> None:
    chips = dict(capability_summary(LAUNCH_PROGRAM, IDENTITY_LOCAL, PLACEMENT_TAB))
    assert chips["browser.cap.login"] == CAP_OK  # local login is best for view credit
    assert chips["browser.cap.window"] == CAP_WARN
    assert chips["browser.cap.manage"] == CAP_OFF


def test_apply_ui_dimensions_local_identity_forces_management_off() -> None:
    raw = apply_ui_dimensions(
        launch="program",
        identity="local",
        placement="window",
        user_data_dir="C:/should_be_dropped",
        per_channel_profile=True,
        browser_path="chrome",
        apply_geometry=True,
        x=0,
        y=0,
        width=800,
        height=600,
        minimized=True,
        close_on_offline=True,
        close_on_stop=True,
        close_off_topic_pages=True,
        hide_from_taskbar=True,
    )
    settings = BrowserSettings.from_dict(raw)
    assert settings.user_data_dir == ""
    assert settings.per_channel_profile is False
    assert settings.close_on_offline is False
    assert settings.close_on_stop is False
    assert settings.close_off_topic_pages is False
    assert settings.hide_from_taskbar is False
    assert settings.minimized is False


def test_apply_ui_dimensions_system_launch_drops_profile() -> None:
    raw = apply_ui_dimensions(
        launch="system",
        identity="dedicated",
        placement="tab",
        user_data_dir="C:/profiles/x",
        per_channel_profile=True,
        browser_path="chrome",
        apply_geometry=False,
        x=0,
        y=0,
        width=800,
        height=600,
        minimized=False,
        close_on_offline=False,
        close_on_stop=False,
        close_off_topic_pages=False,
        hide_from_taskbar=False,
    )
    settings = BrowserSettings.from_dict(raw)
    assert settings.enabled is False
    assert settings.user_data_dir == ""
    assert settings.per_channel_profile is False
