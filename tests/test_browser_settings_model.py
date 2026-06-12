"""Tests for browser_settings_model dimension mapping."""

from __future__ import annotations

from stream_monitor import browser_settings_model as model


def test_infer_local_new_window() -> None:
    settings = {
        "enabled": True,
        "user_data_dir": "",
        "new_window": True,
        "app_mode": False,
    }
    assert model.infer_launch_mode(settings) == model.LAUNCH_PROGRAM
    assert model.infer_identity_mode(settings) == model.IDENTITY_LOCAL
    assert model.infer_placement_mode(settings) == model.PLACEMENT_WINDOW


def test_infer_dedicated_player() -> None:
    settings = {
        "enabled": True,
        "user_data_dir": "C:/p",
        "new_window": True,
        "app_mode": True,
    }
    assert model.infer_identity_mode(settings) == model.IDENTITY_DEDICATED
    assert model.infer_placement_mode(settings) == model.PLACEMENT_PLAYER


def test_apply_local_player_keeps_app_mode() -> None:
    out = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_LOCAL,
        placement=model.PLACEMENT_PLAYER,
        user_data_dir="C:/ignored",
        per_channel_profile=True,
        browser_path="chrome",
        apply_geometry=True,
        x=0,
        y=0,
        width=340,
        height=300,
        minimized=False,
        close_on_offline=False,
        close_on_stop=False,
        close_off_topic_pages=False,
        hide_from_taskbar=False,
    )
    assert out["app_mode"] is True
    assert out["new_window"] is True
    assert out["user_data_dir"] == ""


def test_infer_default_placement_is_existing_window() -> None:
    settings = {"enabled": True, "app_mode": False}
    assert model.infer_placement_mode(settings) == model.PLACEMENT_TAB


def test_apply_local_clears_management_flags() -> None:
    out = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_LOCAL,
        placement=model.PLACEMENT_WINDOW,
        user_data_dir="C:/should-clear",
        per_channel_profile=True,
        browser_path="chrome",
        apply_geometry=True,
        x=0,
        y=0,
        width=340,
        height=300,
        minimized=True,
        close_on_offline=True,
        close_on_stop=True,
        close_off_topic_pages=True,
        hide_from_taskbar=True,
    )
    assert out["user_data_dir"] == ""
    assert out["per_channel_profile"] is False
    assert out["close_on_offline"] is False
    assert out["new_window"] is True


def test_apply_dedicated_player() -> None:
    out = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_DEDICATED,
        placement=model.PLACEMENT_PLAYER,
        user_data_dir="C:/p",
        per_channel_profile=False,
        browser_path="chrome",
        apply_geometry=True,
        x=1,
        y=2,
        width=400,
        height=300,
        minimized=False,
        close_on_offline=True,
        close_on_stop=False,
        close_off_topic_pages=False,
        hide_from_taskbar=False,
    )
    assert out["app_mode"] is True
    assert out["new_window"] is True
    assert out["user_data_dir"] == "C:/p"
    assert out["close_on_offline"] is True


def test_three_placement_modes_map_to_config() -> None:
    tab = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_LOCAL,
        placement=model.PLACEMENT_TAB,
        user_data_dir="",
        per_channel_profile=False,
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
    assert tab["new_window"] is False and tab["app_mode"] is False

    window = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_LOCAL,
        placement=model.PLACEMENT_WINDOW,
        user_data_dir="",
        per_channel_profile=False,
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
    assert window["new_window"] is True and window["app_mode"] is False

    player = model.apply_ui_dimensions(
        launch=model.LAUNCH_PROGRAM,
        identity=model.IDENTITY_LOCAL,
        placement=model.PLACEMENT_PLAYER,
        user_data_dir="",
        per_channel_profile=False,
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
    assert player["new_window"] is True and player["app_mode"] is True


def test_auto_cleanup_ui_vs_window_management() -> None:
    """Auto-cleanup section follows account; runtime mgmt also needs window mode."""
    assert model.auto_cleanup_ui_available(
        model.LAUNCH_PROGRAM, model.IDENTITY_DEDICATED
    )
    assert not model.auto_cleanup_ui_available(
        model.LAUNCH_PROGRAM, model.IDENTITY_LOCAL
    )
    assert not model.auto_cleanup_ui_available(
        model.LAUNCH_SYSTEM, model.IDENTITY_DEDICATED
    )


def test_geometry_placement_vs_window_management() -> None:
    """Geometry UI needs only separate/solo window; auto-mgmt also needs dedicated."""
    assert model.geometry_placement_available(
        model.LAUNCH_PROGRAM, model.PLACEMENT_WINDOW
    )
    assert model.geometry_placement_available(
        model.LAUNCH_PROGRAM, model.PLACEMENT_PLAYER
    )
    assert not model.geometry_placement_available(
        model.LAUNCH_PROGRAM, model.PLACEMENT_TAB
    )
    assert not model.geometry_placement_available(
        model.LAUNCH_SYSTEM, model.PLACEMENT_WINDOW
    )

    assert model.window_management_available(
        model.LAUNCH_PROGRAM, model.IDENTITY_LOCAL, model.PLACEMENT_WINDOW
    ) is False
    assert model.window_management_available(
        model.LAUNCH_PROGRAM, model.IDENTITY_DEDICATED, model.PLACEMENT_WINDOW
    )


def test_capability_system_launch() -> None:
    caps = model.capability_summary(
        model.LAUNCH_SYSTEM, model.IDENTITY_LOCAL, model.PLACEMENT_WINDOW
    )
    assert all(status == model.CAP_OFF for _, status in caps)
