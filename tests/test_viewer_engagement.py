"""Tests for the Twitch viewer-engagement assist (model, prefs, launch)."""

from __future__ import annotations

import json
from pathlib import Path

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
        should_keep_awake = notifier._apply_viewer_engagement_to_launch(
            "https://www.twitch.tv/foo", effective, "/tmp/profile"
        )
        assert effective["minimized"] is False
        assert effective["hide_from_taskbar"] is False
        assert effective["bring_to_front"] is True
        assert should_keep_awake is True
        # The request is registered only after the browser process starts.
        assert awake_calls == []
    finally:
        notifier.configure_viewer_engagement(None)
        notifier._ENGAGEMENT_AWAKE_URLS.clear()
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


def test_keep_awake_requires_a_managed_dedicated_window(monkeypatch, tmp_path) -> None:
    awake_calls: list[bool] = []
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: awake_calls.append(active)
    )
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *a, **k: object())
    notifier.configure_viewer_engagement(ViewerEngagementSettings(enabled=True))
    try:
        assert notifier._open_with_browser_settings(
            "https://www.twitch.tv/foo",
            {
                "enabled": True,
                "browser_path": "chrome",
                "new_window": False,
                "app_mode": False,
                "user_data_dir": str(tmp_path / "profile"),
                "per_channel_profile": False,
            },
        ) is True
        assert awake_calls == []
    finally:
        notifier.configure_viewer_engagement(None)
        notifier._ENGAGEMENT_AWAKE_URLS.clear()
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


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
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


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
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


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


def test_hold_foreground_stops_when_tracked_url_cleared(monkeypatch) -> None:
    """If tracked HWNDs for the URL are cleared, hold should stop early."""
    from stream_monitor import browser_win32
    from stream_monitor.browser_win32 import (
        _clear_tracked_hwnds,
        _register_tracked_hwnd,
    )

    foreground_calls: list[int] = []

    class FakeUser32:
        def IsWindow(self, hwnd):
            return 1

        def ShowWindow(self, hwnd, cmd):
            pass

        def SetForegroundWindow(self, hwnd):
            foreground_calls.append(hwnd)

    monkeypatch.setattr(browser_win32, "_FOREGROUND_HOLD_POLL_S", 0.05)

    url = "https://www.twitch.tv/test_cancel"
    _register_tracked_hwnd(url, 3001)
    try:
        import threading

        def clear_after_delay():
            import time
            time.sleep(0.15)
            _clear_tracked_hwnds(url)

        t = threading.Thread(target=clear_after_delay, daemon=True)
        t.start()

        hwnds = {3001}
        browser_win32._hold_foreground(
            FakeUser32(), hwnds, hold_seconds=5, tracked_url=url
        )
        t.join(timeout=2)

        assert len(foreground_calls) < 20
    finally:
        _clear_tracked_hwnds(url)

def test_release_keep_awake_when_only_tracked_window_closes(monkeypatch) -> None:
    awake_calls: list[bool] = []
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: awake_calls.append(active)
    )
    monkeypatch.setattr(notifier, "tracked_hwnds_for_url", lambda _url: set())
    url = "https://www.twitch.tv/only"
    notifier._ENGAGEMENT_AWAKE_URLS.add(url)
    notifier._ENGAGEMENT_AWAKE_SINCE[url] = 0.0
    try:
        released = notifier.release_keep_awake_for_closed_tracked_windows(min_age_s=0.0)
        assert released == 1
        assert awake_calls == [False]
        assert url not in notifier._ENGAGEMENT_AWAKE_URLS
    finally:
        notifier._ENGAGEMENT_AWAKE_URLS.clear()
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


def test_release_keep_awake_holds_while_another_tracked_window_remains(
    monkeypatch,
) -> None:
    awake_calls: list[bool] = []
    monkeypatch.setattr(
        notifier, "set_system_keep_awake", lambda active: awake_calls.append(active)
    )

    closed = "https://www.twitch.tv/closed"
    open_url = "https://www.twitch.tv/open"

    def hwnds(url: str):
        return {99} if url == open_url else set()

    monkeypatch.setattr(notifier, "tracked_hwnds_for_url", hwnds)
    notifier._ENGAGEMENT_AWAKE_URLS.update({closed, open_url})
    notifier._ENGAGEMENT_AWAKE_SINCE[closed] = 0.0
    notifier._ENGAGEMENT_AWAKE_SINCE[open_url] = 0.0
    try:
        released = notifier.release_keep_awake_for_closed_tracked_windows(min_age_s=0.0)
        assert released == 1
        assert awake_calls == []
        assert closed not in notifier._ENGAGEMENT_AWAKE_URLS
        assert open_url in notifier._ENGAGEMENT_AWAKE_URLS
    finally:
        notifier._ENGAGEMENT_AWAKE_URLS.clear()
        notifier._ENGAGEMENT_AWAKE_SINCE.clear()


# ---------------------------------------------------------------------------
# Localized copy — Twitch Viewer Count FAQ alignment (issue #7)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_OFFICIAL_VIEWER_COUNT_URL = (
    "https://help.twitch.tv/s/article/understanding-viewer-count-vs-users-in-chat"
)
_OFFICIAL_BOT_URL = (
    "https://help.twitch.tv/s/article/how-to-handle-view-follow-bots"
)

# False claims that official Twitch sources do not support.
_FORBIDDEN_COPY_FRAGMENTS = (
    "only up to 2 Twitch streams per IP",
    "同一 IP 同時最多 2",
    "同一 IP 同时最多 2",
    "同一 IP で同時に計上されるのは最大 2",
    "동일 IP에서 동시에 집계되는 것은 최대 2",
    "mute the tab, not the player's speaker",
    "請對「分頁」靜音而非點播放器喇叭",
    "请对“标签页”静音而非点播放器喇叭",
    "プレーヤーのスピーカーではなく「タブ」をミュート",
    '플레이어 스피커가 아니라 "탭"을 음소거',
    "keeps sending its heartbeat and the player keeps rendering",
    "只在分頁持續送出心跳且播放器持續播放時才計入",
    "只在标签页持续发送心跳且播放器持续播放时才计入",
    "タブがハートビートを送り続け、プレーヤーが再生し続けている間だけ",
    "탭이 하트비트를 계속 보내고 플레이어가 계속 재생될 때만",
    "first heartbeat counts as watched",
    "確保首次心跳被認定為觀看中",
    "确保首次心跳被认定为观看中",
    "最初のハートビートを視聴中と認識",
    "첫 하트비트가 시청 중으로 인식",
    "reduce missed counts from sleep",
    "降低因休眠等導致觀看不被計入的機率",
    "降低背景分頁／休眠導致觀看不被計入的機率",
    "降低因休眠等导致观看不被计入的概率",
    "スリープ等による未計上リスクを軽減",
    "절전 등으로 집계되지 않을 가능성을 줄입니다",
)


def test_engagement_i18n_matches_twitch_viewer_count_faq() -> None:
    """All five locales must reflect official FAQ facts; no unsupported claims."""
    from stream_monitor import i18n

    for code, table in i18n._TRANSLATIONS.items():
        intro = table["engagement.intro"]
        tips = table["engagement.tips"]
        bring_hint = table["engagement.toggle.bring_front.hint"]
        tooltip = table["tooltip.viewer_engagement"]
        combined = f"{intro}\n{tips}\n{bring_hint}\n{tooltip}"

        assert _OFFICIAL_VIEWER_COUNT_URL in tips, code
        assert _OFFICIAL_BOT_URL in tips, code

        for fragment in _FORBIDDEN_COPY_FRAGMENTS:
            assert fragment not in combined, (code, fragment)

    # English copy is the SSOT for FAQ facts; other locales mirror the meaning.
    en = i18n._TRANSLATIONS["en"]
    en_intro = en["engagement.intro"]
    en_tips = en["engagement.tips"]
    assert "muted Twitch player or browser tab still counts" in en_intro
    assert "background or unfocused tab still counts" in en_intro
    assert "viewer-count updates may take a few minutes" in en_intro
    assert "do not state a fixed tab-heartbeat interval" in en_intro
    assert "per-IP view limit" in en_intro
    assert "only attempt to keep playback active" in en_intro
    assert "do not guarantee view credit" in en_intro
    assert "artificial view inflation" in en_tips
    assert "coordinated fake engagement" in en_tips
    assert "unspecified" in en_tips

    # README (repo root) must not restate unsupported count claims.
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert _OFFICIAL_VIEWER_COUNT_URL in readme
    for fragment in _FORBIDDEN_COPY_FRAGMENTS:
        assert fragment not in readme, fragment
