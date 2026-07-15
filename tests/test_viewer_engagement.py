"""Tests for the Twitch viewer-engagement assist (model, prefs, launch)."""

from __future__ import annotations

import json

from stream_monitor import chrome_prefs, config_manager, notifier
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    coerce_viewer_engagement,
    is_twitch_url,
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def test_settings_defaults_are_opt_in() -> None:
    settings = ViewerEngagementSettings()
    assert settings.enabled is False
    assert settings.force_visible is True
    assert settings.keep_system_awake is True


def test_from_dict_ignores_unknown_keys() -> None:
    settings = ViewerEngagementSettings.from_dict(
        {"enabled": True, "bogus": 1, "force_visible": False}
    )
    assert settings.enabled is True
    assert settings.force_visible is False


def test_coerce_passthrough_and_none() -> None:
    existing = ViewerEngagementSettings(enabled=True)
    assert coerce_viewer_engagement(existing) is existing
    assert coerce_viewer_engagement(None) is None
    assert coerce_viewer_engagement({"enabled": True}).enabled is True


def test_is_twitch_url() -> None:
    assert is_twitch_url("https://www.twitch.tv/foo") is True
    assert is_twitch_url("https://twitch.tv/bar") is True
    assert is_twitch_url("https://youtube.com/@baz") is False
    assert is_twitch_url("") is False


# ---------------------------------------------------------------------------
# Config normalization
# ---------------------------------------------------------------------------
def test_config_normalizes_viewer_engagement_defaults() -> None:
    normalized = config_manager._normalize_config({})
    assert normalized["viewer_engagement"] == config_manager.DEFAULT_VIEWER_ENGAGEMENT
    # Returned a copy, not the shared default object.
    assert (
        normalized["viewer_engagement"]
        is not config_manager.DEFAULT_VIEWER_ENGAGEMENT
    )


def test_config_normalizes_partial_viewer_engagement() -> None:
    normalized = config_manager._normalize_config(
        {"viewer_engagement": {"enabled": True, "keep_system_awake": "bad"}}
    )
    ve = normalized["viewer_engagement"]
    assert ve["enabled"] is True
    # Non-bool values fall back to the default (True for keep_system_awake).
    assert ve["keep_system_awake"] is True


# ---------------------------------------------------------------------------
# Chrome Memory Saver allowlist
# ---------------------------------------------------------------------------
def test_merge_creates_preferences_with_exceptions(tmp_path) -> None:
    user_data_dir = tmp_path / "profile"
    assert chrome_prefs.merge_tab_discarding_exceptions(str(user_data_dir)) is True
    prefs_file = user_data_dir / "Default" / "Preferences"
    data = json.loads(prefs_file.read_text(encoding="utf-8"))
    exceptions = data["performance_tuning"]["tab_discarding"]["exceptions"]
    assert "twitch.tv" in exceptions
    assert ".twitch.tv" in exceptions


def test_merge_preserves_existing_prefs(tmp_path) -> None:
    profile_dir = tmp_path / "profile" / "Default"
    profile_dir.mkdir(parents=True)
    prefs_file = profile_dir / "Preferences"
    prefs_file.write_text(
        json.dumps(
            {
                "profile": {"name": "Person 1"},
                "performance_tuning": {"tab_discarding": {"exceptions": ["a.com"]}},
            }
        ),
        encoding="utf-8",
    )
    chrome_prefs.merge_tab_discarding_exceptions(str(tmp_path / "profile"))
    data = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert data["profile"]["name"] == "Person 1"
    exceptions = data["performance_tuning"]["tab_discarding"]["exceptions"]
    assert "a.com" in exceptions
    assert "twitch.tv" in exceptions


def test_merge_is_idempotent(tmp_path) -> None:
    user_data_dir = str(tmp_path / "profile")
    chrome_prefs.merge_tab_discarding_exceptions(user_data_dir)
    chrome_prefs.merge_tab_discarding_exceptions(user_data_dir)
    prefs_file = tmp_path / "profile" / "Default" / "Preferences"
    data = json.loads(prefs_file.read_text(encoding="utf-8"))
    exceptions = data["performance_tuning"]["tab_discarding"]["exceptions"]
    assert exceptions.count("twitch.tv") == 1


def test_merge_no_op_without_dir() -> None:
    assert chrome_prefs.merge_tab_discarding_exceptions("") is False


# ---------------------------------------------------------------------------
# Launch integration
# ---------------------------------------------------------------------------
def test_engagement_forces_visible_for_twitch(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier,
        "merge_tab_discarding_exceptions",
        lambda *_a, **_k: True,
    )
    awake_calls: list[bool] = []
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: awake_calls.append(active)
    )
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(enabled=True, force_visible=True, bring_to_front=True)
    )
    try:
        effective = {"minimized": True, "hide_from_taskbar": True}
        notifier._apply_viewer_engagement_to_launch(
            "https://www.twitch.tv/foo", effective, "/tmp/profile"
        )
        assert effective["minimized"] is False
        assert effective["hide_from_taskbar"] is False
        assert effective["bring_to_front"] is True
        assert awake_calls == [True]
    finally:
        notifier.configure_viewer_engagement(None)
        notifier._ENGAGEMENT_AWAKE_URLS.clear()


def test_anti_throttle_flags_added_for_twitch() -> None:
    notifier.configure_viewer_engagement(ViewerEngagementSettings(enabled=True))
    try:
        args = notifier._build_browser_args(
            "https://www.twitch.tv/foo",
            {"browser_path": "custom-browser", "new_window": True},
        )
        for flag in notifier._ANTI_THROTTLE_FLAGS:
            assert flag in args
    finally:
        notifier.configure_viewer_engagement(None)


def test_anti_throttle_flags_skipped_when_disabled() -> None:
    notifier.configure_viewer_engagement(ViewerEngagementSettings(enabled=False))
    try:
        args = notifier._build_browser_args(
            "https://www.twitch.tv/foo",
            {"browser_path": "custom-browser", "new_window": True},
        )
        for flag in notifier._ANTI_THROTTLE_FLAGS:
            assert flag not in args
    finally:
        notifier.configure_viewer_engagement(None)


def test_anti_throttle_flags_skipped_for_non_twitch() -> None:
    notifier.configure_viewer_engagement(ViewerEngagementSettings(enabled=True))
    try:
        args = notifier._build_browser_args(
            "https://youtube.com/@bar",
            {"browser_path": "custom-browser", "new_window": True},
        )
        for flag in notifier._ANTI_THROTTLE_FLAGS:
            assert flag not in args
    finally:
        notifier.configure_viewer_engagement(None)


def test_anti_throttle_flags_skipped_for_firefox() -> None:
    notifier.configure_viewer_engagement(ViewerEngagementSettings(enabled=True))
    try:
        args = notifier._build_browser_args(
            "https://www.twitch.tv/foo",
            {"browser_path": "firefox", "new_window": True},
        )
        for flag in notifier._ANTI_THROTTLE_FLAGS:
            assert flag not in args
    finally:
        notifier.configure_viewer_engagement(None)


def test_engagement_skips_non_twitch(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: None
    )
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(enabled=True, force_visible=True)
    )
    try:
        effective = {"minimized": True, "hide_from_taskbar": True}
        notifier._apply_viewer_engagement_to_launch(
            "https://youtube.com/@bar", effective, "/tmp/profile"
        )
        assert effective["minimized"] is True
        assert effective["hide_from_taskbar"] is True
    finally:
        notifier.configure_viewer_engagement(None)


def test_engagement_disabled_is_noop(monkeypatch) -> None:
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(enabled=False, force_visible=True)
    )
    try:
        effective = {"minimized": True, "hide_from_taskbar": True}
        notifier._apply_viewer_engagement_to_launch(
            "https://www.twitch.tv/foo", effective, "/tmp/profile"
        )
        assert effective["minimized"] is True
    finally:
        notifier.configure_viewer_engagement(None)


def test_close_releases_keep_awake(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_post_close_window", lambda _hwnd: True)
    awake_calls: list[bool] = []
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: awake_calls.append(active)
    )
    url = "https://www.twitch.tv/foo"
    notifier._ENGAGEMENT_AWAKE_URLS.add(url)
    notifier._register_tracked_hwnd(url, 4242)
    try:
        notifier.close_browser_window_for_url(url)
        assert awake_calls == [False]
        assert url not in notifier._ENGAGEMENT_AWAKE_URLS
    finally:
        notifier._ENGAGEMENT_AWAKE_URLS.clear()
        with notifier._TRACKED_HWNDS_LOCK:
            notifier._TRACKED_WINDOWS_BY_URL.clear()


# ---------------------------------------------------------------------------
# Foreground hold settings
# ---------------------------------------------------------------------------
def test_foreground_hold_seconds_default() -> None:
    settings = ViewerEngagementSettings()
    assert settings.bring_to_front is True
    assert settings.foreground_hold_seconds == 15


def test_foreground_hold_seconds_from_dict() -> None:
    settings = ViewerEngagementSettings.from_dict(
        {"enabled": True, "foreground_hold_seconds": 30}
    )
    assert settings.foreground_hold_seconds == 30


def test_engagement_passes_foreground_hold_to_effective(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier,
        "merge_tab_discarding_exceptions",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: None
    )
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True, bring_to_front=True, foreground_hold_seconds=20
        )
    )
    try:
        effective: dict = {"minimized": False, "hide_from_taskbar": False}
        notifier._apply_viewer_engagement_to_launch(
            "https://www.twitch.tv/foo", effective, "/tmp/profile"
        )
        assert effective["bring_to_front"] is True
        assert effective["foreground_hold_seconds"] == 20
    finally:
        notifier.configure_viewer_engagement(None)
        notifier._ENGAGEMENT_AWAKE_URLS.clear()


def test_foreground_hold_not_set_when_bring_to_front_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: None
    )
    notifier.configure_viewer_engagement(
        ViewerEngagementSettings(
            enabled=True, bring_to_front=False, foreground_hold_seconds=20
        )
    )
    try:
        effective: dict = {"minimized": False, "hide_from_taskbar": False}
        notifier._apply_viewer_engagement_to_launch(
            "https://www.twitch.tv/foo", effective, "/tmp/profile"
        )
        assert effective["bring_to_front"] is False
        assert "foreground_hold_seconds" not in effective
    finally:
        notifier.configure_viewer_engagement(None)
        notifier._ENGAGEMENT_AWAKE_URLS.clear()


def test_config_normalizes_foreground_hold_seconds() -> None:
    normalized = config_manager._normalize_viewer_engagement(
        {"enabled": True, "foreground_hold_seconds": "25"}
    )
    assert normalized["foreground_hold_seconds"] == 25

    normalized_bad = config_manager._normalize_viewer_engagement(
        {"enabled": True, "foreground_hold_seconds": "not_a_number"}
    )
    assert normalized_bad["foreground_hold_seconds"] == 15


# ---------------------------------------------------------------------------
# _hold_foreground unit test
# ---------------------------------------------------------------------------
def test_hold_foreground_calls_set_foreground_window(monkeypatch) -> None:
    """Verify _hold_foreground repeatedly asserts foreground on managed HWNDs."""
    import time

    from stream_monitor import browser_win32

    foreground_calls: list[int] = []
    show_calls: list[tuple[int, int]] = []

    class FakeUser32:
        def IsWindow(self, hwnd):
            return 1

        def ShowWindow(self, hwnd, cmd):
            show_calls.append((hwnd, cmd))

        def SetForegroundWindow(self, hwnd):
            foreground_calls.append(hwnd)

    monkeypatch.setattr(browser_win32, "_FOREGROUND_HOLD_POLL_S", 0.01)

    hwnds = {1001, 1002}
    browser_win32._hold_foreground(FakeUser32(), hwnds, hold_seconds=1)

    assert len(foreground_calls) >= 2
    assert 1001 in foreground_calls
    assert 1002 in foreground_calls


def test_hold_foreground_removes_dead_windows(monkeypatch) -> None:
    """If IsWindow returns 0, the hwnd is discarded from the hold set."""
    from stream_monitor import browser_win32

    alive_hwnds = {2001}

    class FakeUser32:
        def IsWindow(self, hwnd):
            return 1 if hwnd in alive_hwnds else 0

        def ShowWindow(self, hwnd, cmd):
            pass

        def SetForegroundWindow(self, hwnd):
            pass

    monkeypatch.setattr(browser_win32, "_FOREGROUND_HOLD_POLL_S", 0.01)

    hwnds = {2001, 2002}
    browser_win32._hold_foreground(FakeUser32(), hwnds, hold_seconds=1)
    assert 2002 not in hwnds
    assert 2001 in hwnds
