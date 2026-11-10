"""Explicit point recipients must not silently share login with other players."""

import threading
import time
from unittest.mock import Mock

import pytest

from stream_monitor import config_manager, notifier, twitch_page_assist
from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.twitch_watch_policy import route_browser
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    normalize_points_priorities,
)


def settings():
    return ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, claim_channel_points=True,
        points_stall_refresh_enabled=True,
        points_priority_channels=["taiga_hikari", "kspksp"],
    )


def test_priority_names_round_trip_and_deduplicate():
    names = normalize_points_priorities("Taiga_Hikari, https://www.twitch.tv/kspksp, KSPKSP")
    assert names == ["taiga_hikari", "kspksp"]
    data = config_manager._normalize_viewer_engagement({"points_priority_channels": names})
    assert ViewerEngagementSettings.from_dict(data).to_dict()["points_priority_channels"] == names


@pytest.mark.parametrize("value", ["one,two,three", "https://example.com/one", "one/bad", [None]])
def test_invalid_priorities_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_points_priorities(value)


def test_ten_players_route_only_selected_two_to_signed_in_profile(tmp_path):
    browser = BrowserSettings(enabled=True, user_data_dir=str(tmp_path / "account"))
    channels = ["taiga_hikari", "kspksp"] + [f"viewer{i}" for i in range(8)]
    routes = [route_browser("https://www.twitch.tv/" + ch, browser, settings()) for ch in channels]
    assert all(r.user_data_dir == browser.user_data_dir and not r.guest_mode for r in routes[:2])
    assert all(r.guest_mode and r.user_data_dir != browser.user_data_dir for r in routes[2:])
    assert len({r.user_data_dir for r in routes[2:]}) == 1
    assert all(not r.per_channel_profile for r in routes)
    assert not browser.guest_mode and browser.per_channel_profile
    assert route_browser("https://www.twitch.tv/viewer0", routes[2], settings()) == routes[2]
    args = notifier._build_browser_args("https://www.twitch.tv/viewer0", routes[2].to_dict())
    assert "--guest" in args


def test_watch_only_keeps_playback_help_without_point_claim_or_point_reload():
    active = settings()
    guest = active.for_channel("https://www.twitch.tv/other")
    assert guest.page_assist_active()
    assert not guest.claim_channel_points and not guest.points_stall_refresh_enabled
    assert active.for_channel("https://www.twitch.tv/kspksp").claim_channel_points
    assert active.claim_channel_points


def test_priority_failure_cannot_fall_back_to_default_logged_in_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", settings())
    monkeypatch.setattr(notifier, "managed_window_launch_pending", lambda _u: False)
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda _u: False)
    monkeypatch.setattr(notifier, "_open_with_browser_settings", lambda *_a, **_k: False)
    fallback = Mock()
    monkeypatch.setattr(notifier.webbrowser, "open", fallback)
    assert not notifier.open_url("https://www.twitch.tv/other", BrowserSettings(
        enabled=True, user_data_dir=str(tmp_path / "account")))
    fallback.assert_not_called()


@pytest.mark.parametrize("browser", [None, BrowserSettings(), BrowserSettings(enabled=True),
                                     BrowserSettings(enabled=True, user_data_dir="account", browser_path="firefox")])
def test_unsupported_priority_setup_is_not_silently_accepted(browser):
    with pytest.raises(ValueError):
        route_browser("https://www.twitch.tv/kspksp", browser, settings())


def test_non_twitch_and_disabled_policy_preserve_browser(tmp_path):
    browser = BrowserSettings(enabled=True, user_data_dir=str(tmp_path))
    assert route_browser("https://youtube.com/watch?v=foo", browser, settings()) is browser
    assert route_browser("https://www.twitch.tv/other", browser,
                         ViewerEngagementSettings(enabled=True)) is browser


def test_saving_claim_switch_updates_running_workers_without_restarting(monkeypatch):
    workers = {url: twitch_page_assist._PageAssistWorker(url, 1234, ViewerEngagementSettings())
               for url in ["https://www.twitch.tv/kspksp", "https://www.twitch.tv/other"]}
    monkeypatch.setattr(twitch_page_assist, "_assist_by_url", workers)
    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", None)
    notifier.configure_viewer_engagement(settings())
    primary, guest = workers.values()
    assert primary.settings.claim_channel_points
    assert not guest.settings.claim_channel_points
    assert all(w._wake.is_set() and w._settings_changed.is_set() for w in workers.values())
    assert all(not w._stop.is_set() for w in workers.values())


@pytest.mark.parametrize("priorities,allowed", [(["kspksp"], True), ([], False)])
def test_signin_bootstraps_the_effective_priority_profile(monkeypatch, priorities, allowed):
    from types import SimpleNamespace

    from stream_monitor import app_dialogs
    from stream_monitor.browser_settings_model import IDENTITY_DEDICATED

    def value(v):
        return SimpleNamespace(get=lambda: v)

    dialog = SimpleNamespace(
        identity_var=value(IDENTITY_DEDICATED), user_data_dir_entry=value("account"),
        per_channel_profile_var=value(True), path_entry=value("chrome"),
        _collect_viewer_engagement=lambda: {"enabled": True, "points_priority_channels": priorities},
        _set_message=Mock(),
    )
    signin = Mock(return_value=True)
    monkeypatch.setattr(app_dialogs, "open_browser_for_signin", signin)
    app_dialogs.BrowserSettingsDialog._on_signin(dialog)
    if allowed:
        signin.assert_called_once_with("account", browser_path="chrome")
    else:
        signin.assert_not_called()


def test_concurrent_managed_opens_of_same_url_launch_once(monkeypatch, tmp_path):
    """2026-11-10: open_keep and points_priority_open raced and opened two
    windows for the same channel."""


    pending: set[str] = set()
    launches: list[str] = []
    entered = threading.Event()

    def fake_launch(url, *_a, **_k):
        launches.append(url)
        entered.set()
        time.sleep(0.2)  # spawn + window tracking registration
        pending.add(url)
        return True

    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", None)
    monkeypatch.setattr(notifier, "managed_window_launch_pending", lambda u: u in pending)
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda _u: False)
    monkeypatch.setattr(notifier, "_open_with_browser_settings", fake_launch)
    browser = BrowserSettings(enabled=True, user_data_dir=str(tmp_path / "account"))
    url = "https://www.twitch.tv/hayabi_zr"
    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(notifier.open_url(url, browser)))
        for _ in range(2)
    ]
    threads[0].start()
    entered.wait(2)
    threads[1].start()
    for thread in threads:
        thread.join(5)

    assert launches == [url]
    assert results == [True, True]
