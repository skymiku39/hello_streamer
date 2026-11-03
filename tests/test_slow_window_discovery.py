"""Slow launches must retain ownership through close requests and raids."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stream_monitor import browser_win32 as bw
from stream_monitor import notifier
from stream_monitor import twitch_page_assist as assist
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

URL = "https://www.twitch.tv/karlylinnea"


@pytest.fixture
def slow_browser(monkeypatch):
    clock = [0.0]
    registry = bw._WindowRegistry()
    monkeypatch.setattr(bw, "_REGISTRY", registry)
    monkeypatch.setattr(bw, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    ))
    # Run the same worker synchronously with a virtual clock.
    class Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(bw, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=Mock()), raising=False)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda api: None)
    monkeypatch.setattr(notifier, "_is_browser_popup_or_tool_window", lambda api, hwnd: False)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda cls: {10, 20, 30})
    # A pre-existing copy (10), an unrelated newly opened tab (30), and our
    # window (20), whose real title arrives well after the old 8-second limit.
    title = ["karlylinnea - Twitch"]
    monkeypatch.setattr(notifier, "_get_window_title", lambda api, hwnd:
        title[0] if hwnd == 20 and clock[0] >= 20 else "Google Chrome")
    monkeypatch.setattr(bw, "_window_matches_profile", lambda hwnd, profile: hwnd == 20)
    closed = []
    monkeypatch.setattr(notifier, "_post_close_window", lambda hwnd: closed.append(hwnd) or True)
    monkeypatch.setattr(notifier, "stop_page_assist", lambda url: None)
    monkeypatch.setattr(notifier, "set_system_keep_awake", lambda active: None)
    return clock, registry, title, closed


def discover():
    bw._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1", {10}, {"user_data_dir": "owned-profile"},
        apply_geometry=False, track_for_url=URL, track_keywords=("karlylinnea",),
    )


def test_late_window_remains_closable_after_raid_changes_title(slow_browser, caplog):
    clock, registry, title, closed = slow_browser
    discover()
    assert clock[0] >= 20
    assert {w.hwnd for w in registry.snapshot(URL)} == {20}
    assert not registry.launch_pending(URL)
    assert "continuing discovery" in caplog.text
    title[0] = "Grey channel - Twitch"  # Original source is now raided away.
    assert bw.close_browser_window_for_url(URL, title_keywords=["karlylinnea"]) == 1
    assert closed == [20]


@pytest.mark.parametrize("close_all", [False, True])
def test_close_requested_before_window_is_ready_is_not_lost(slow_browser, monkeypatch, close_all):
    clock, registry, title, closed = slow_browser
    requested = []

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 10 and not requested:
            requested.append(True)
            if close_all:
                bw.close_all_tracked_windows()
            else:
                bw.close_browser_window_for_url(URL, title_keywords=["karlylinnea"])

    monkeypatch.setattr(bw.time, "sleep", sleep)
    fallback = Mock()
    monkeypatch.setattr(notifier, "_find_hwnds_by_title_keyword", fallback)
    discover()
    assert closed == [20]
    assert registry.snapshot(URL) == []
    assert not registry.launch_pending(URL)
    fallback.assert_not_called()


def test_pending_launch_is_not_treated_as_closed_or_opened_twice(monkeypatch):
    registry = bw._WindowRegistry()
    monkeypatch.setattr(bw, "_REGISTRY", registry)
    token = registry.begin_launch(URL)
    monkeypatch.setattr(notifier, "_OBS_MANAGED_URLS", {URL})
    monkeypatch.setattr(notifier, "_OBS_MANAGED_SINCE", {URL: 0.0})
    monkeypatch.setattr(notifier, "_ENGAGEMENT_AWAKE_URLS", {URL})
    monkeypatch.setattr(notifier, "_ENGAGEMENT_AWAKE_SINCE", {URL: 0.0})
    stopped, opened = Mock(), Mock(return_value=True)
    monkeypatch.setattr(notifier, "stop_page_assist", stopped)
    monkeypatch.setattr(notifier, "_open_with_browser_settings", opened)
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda url: False)
    assert notifier.release_keep_awake_for_closed_tracked_windows(min_age_s=0) == 0
    assert URL in notifier._OBS_MANAGED_URLS
    assert notifier.open_url(URL, {"enabled": True})
    stopped.assert_not_called()
    opened.assert_not_called()
    registry.finish_launch(URL, token)
    notifier.open_url(URL, {"enabled": True}, manage=False)
    opened.assert_called_once()  # A manual open still works.


def test_replaced_pending_worker_cannot_remove_new_launch():
    registry = bw._WindowRegistry()
    old = registry.begin_launch(URL)
    new = registry.begin_launch(URL)
    registry.finish_launch(URL, old)
    assert registry.launch_is_current(URL, new)
    assert not registry.launch_is_current(URL, old)


def test_unidentified_window_times_out_without_adoption(slow_browser, monkeypatch, caplog):
    clock, registry, title, closed = slow_browser
    monkeypatch.setattr(bw, "_window_matches_profile", lambda hwnd, profile: False)
    discover()
    assert clock[0] >= bw.MANAGED_WINDOW_DISCOVERY_TIMEOUT_S
    assert registry.snapshot(URL) == []
    assert not registry.launch_pending(URL)
    assert closed == []
    assert "leaving unverified windows untouched" in caplog.text


def test_initial_cdp_timeout_retries_same_channel(monkeypatch):
    worker = assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings())
    first, second = Mock(), Mock()
    first.connect.side_effect = TimeoutError("page still loading")
    clients = iter([first, second])
    monkeypatch.setattr(assist, "CdpClient", lambda: next(clients))
    monkeypatch.setattr(worker, "_sleep", lambda seconds: True)
    monkeypatch.setattr(worker, "_run_gate_and_theater", lambda client: worker.stop())
    worker._run()
    first.close.assert_called_once()
    second.connect.assert_called_once_with(9222, URL, timeout=assist.DEFAULT_CDP_ATTACH_TIMEOUT_S)


def test_cancel_initial_cdp_wait_does_not_start_another_connection(monkeypatch):
    worker = assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings())
    first = Mock()
    first.connect.side_effect = TimeoutError("page still loading")
    factory = Mock(return_value=first)
    monkeypatch.setattr(assist, "CdpClient", factory)
    monkeypatch.setattr(worker, "_sleep", lambda seconds: False)
    worker._run()
    factory.assert_called_once()


def test_initial_cdp_retries_stay_bounded_and_cancellable_after_slow_start(monkeypatch):
    worker = assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings())
    clock = [0.0]
    client = Mock()

    def connect(*args, timeout):
        clock[0] += timeout
        raise TimeoutError("page never appeared")

    client.connect.side_effect = connect
    monkeypatch.setattr(assist, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(assist, "CdpClient", lambda: client)
    waits = []

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds
        return len(waits) < 8

    monkeypatch.setattr(worker, "_sleep", wait)
    worker._run()
    assert clock[0] > 90
    assert waits == [1, 2, 4, 8, 16, 30, 30, 30]
    assert client.connect.call_count == 8
    assert all(call.kwargs["timeout"] == assist.DEFAULT_CDP_ATTACH_TIMEOUT_S
               for call in client.connect.call_args_list)
