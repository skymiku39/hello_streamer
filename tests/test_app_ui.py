"""Tests for shared UI formatting helpers."""

from stream_monitor import i18n
from stream_monitor.app_ui import (
    _clamp_tooltip_position,
    _format_minutes_delta,
    monitor_mode_button_states,
)


def test_format_minutes_delta_under_one_minute() -> None:
    i18n.set_language("en-US", notify=False)
    assert _format_minutes_delta(30) == "<1m"
    assert _format_minutes_delta(90) == "1m"
    assert _format_minutes_delta(0) == "0m"


def test_clamp_tooltip_position_keeps_left_monitor_negative_x() -> None:
    """Monitors left of primary use negative root coordinates."""
    x, y = _clamp_tooltip_position(
        -1500,
        200,
        tip_w=180,
        tip_h=40,
        vroot_x=-1920,
        vroot_y=0,
        vroot_w=3840,
        vroot_h=1080,
    )
    assert x == -1500
    assert y == 200


def test_clamp_tooltip_position_old_logic_would_pull_to_primary() -> None:
    """Primary-only clamp (x < 8) incorrectly snaps negative coords to 8."""
    x, _y = _clamp_tooltip_position(
        -1500,
        200,
        tip_w=180,
        tip_h=40,
        vroot_x=-1920,
        vroot_y=0,
        vroot_w=3840,
        vroot_h=1080,
    )
    assert x < 0


def test_clamp_tooltip_position_clamps_within_virtual_desktop() -> None:
    x, y = _clamp_tooltip_position(
        3700,
        1200,
        tip_w=200,
        tip_h=50,
        vroot_x=-1920,
        vroot_y=0,
        vroot_w=3840,
        vroot_h=1080,
    )
    assert x == -1920 + 3840 - 200 - 8
    assert y == 1080 - 50 - 8


def test_clamp_tooltip_position_primary_only_setup() -> None:
    x, y = _clamp_tooltip_position(
        900,
        40,
        tip_w=200,
        tip_h=50,
        vroot_x=0,
        vroot_y=0,
        vroot_w=1920,
        vroot_h=1080,
    )
    assert x == 900
    assert y == 40


def test_monitor_mode_buttons_idle_disables_only_stop() -> None:
    assert monitor_mode_button_states("idle") == {
        "start": "normal",
        "watch": "normal",
        "stop": "disabled",
    }


def test_monitor_mode_buttons_trigger_disables_start() -> None:
    states = monitor_mode_button_states("trigger")
    assert states["start"] == "disabled"
    assert states["watch"] == "normal"
    assert states["stop"] == "normal"


def test_monitor_mode_buttons_watch_disables_watch() -> None:
    states = monitor_mode_button_states("watch")
    assert states["start"] == "normal"
    assert states["watch"] == "disabled"
    assert states["stop"] == "normal"


def test_monitor_mode_buttons_unknown_mode_falls_back_to_idle() -> None:
    assert monitor_mode_button_states("bogus") == monitor_mode_button_states("idle")
