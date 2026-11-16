"""Delayed CDP re-attach after a cold launch's endpoint discovery fails (#63)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from stream_monitor import cdp_client, claim_registry, notifier
from stream_monitor.cdp_client import CdpAttachResult
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

URL = "https://www.twitch.tv/foo"


class FakeTimer:
    """Stands in for threading.Timer; the test fires it by hand."""

    created: list[FakeTimer] = []

    def __init__(self, interval, function) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        self.name = ""
        self.started = False
        self.cancelled = False
        FakeTimer.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class Harness:
    def __init__(self) -> None:
        self.window_open = True
        self.workers: set[str] = set()
        self.results: list[CdpAttachResult] = []
        self.resolve_calls = 0
        self.assist = Mock(return_value=True)

    def resolve(self, _profile, **_kwargs) -> CdpAttachResult:
        self.resolve_calls += 1
        if self.results:
            return self.results.pop(0)
        return CdpAttachResult(ok=False, reason="timeout")


@pytest.fixture
def harness(monkeypatch):
    h = Harness()
    FakeTimer.created = []
    claim_registry.reset_for_tests()
    engagement = ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, keep_system_awake=False,
        whitelist_performance=False,
    )
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: engagement)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(
        notifier, "_apply_new_browser_window_settings_async", lambda *a, **k: None,
    )
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda *_a, **_k: set())
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())
    monkeypatch.setattr(notifier, "_OBS_MANAGED_URLS", set())
    monkeypatch.setattr(notifier, "_OBS_MANAGED_SINCE", {})
    monkeypatch.setattr(notifier, "_CDP_ATTACH_RETRIES", {})
    monkeypatch.setattr(notifier, "_CDP_ATTACH_RETRY_TIMER", FakeTimer)
    monkeypatch.setattr(notifier, "managed_window_is_open", lambda url: h.window_open)
    monkeypatch.setattr(notifier, "active_page_assist_urls", lambda: set(h.workers))
    monkeypatch.setattr(notifier, "start_page_assist", h.assist)
    monkeypatch.setattr(notifier, "stop_page_assist", lambda url: None)
    monkeypatch.setattr(notifier, "stop_all_page_assist", lambda: None)
    monkeypatch.setattr(notifier, "_close_all_tracked_windows_impl", lambda: 0)
    monkeypatch.setattr(notifier, "_close_browser_window_for_url_impl", lambda *_a, **_k: 0)
    monkeypatch.setattr(notifier, "set_system_keep_awake", lambda *_a: None)
    monkeypatch.setattr(cdp_client, "acquire_cdp_debugging_port", lambda *_a, **_k: CdpAttachResult(
        ok=True, port=35555, reason="allocated", pass_debugging_flag=True,
    ))
    monkeypatch.setattr(cdp_client, "resolve_cdp_attach", h.resolve)
    yield h
    claim_registry.reset_for_tests()


def _launch(tmp_path) -> bool:
    return notifier._open_with_browser_settings(URL, {
        "enabled": True, "browser_path": "chrome", "app_mode": True,
        "user_data_dir": str(tmp_path / "profile"), "per_channel_profile": False,
    })


def _pending() -> list[FakeTimer]:
    return [t for t in FakeTimer.created if t.started and not t.cancelled]


def test_failed_discovery_schedules_retry_that_starts_page_assist(harness, tmp_path) -> None:
    assert _launch(tmp_path)
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1
    timer = FakeTimer.created[0]
    assert timer.started and timer.daemon
    assert timer.interval == notifier.CDP_ATTACH_RETRY_DELAY_S

    harness.results = [CdpAttachResult(ok=True, port=35555, reason="process")]
    timer.function()

    harness.assist.assert_called_once()
    assert harness.assist.call_args.args[:2] == (URL, 35555)
    assert len(FakeTimer.created) == 1
    assert notifier._CDP_ATTACH_RETRIES == {}
    assert not claim_registry.take_endpoint_restart(now=1000.0)


def test_successful_discovery_schedules_no_retry(harness, tmp_path) -> None:
    harness.results = [CdpAttachResult(ok=True, port=35555, reason="process")]
    assert _launch(tmp_path)
    harness.assist.assert_called_once()
    assert FakeTimer.created == []


def test_retry_gives_up_when_window_is_closed(harness, tmp_path) -> None:
    assert _launch(tmp_path)
    calls = harness.resolve_calls
    harness.window_open = False
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1


def test_retry_gives_up_when_url_is_no_longer_managed(harness, tmp_path) -> None:
    assert _launch(tmp_path)
    calls = harness.resolve_calls
    notifier._unregister_obs_managed_url(URL)
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1


def test_retry_gives_up_when_worker_already_attached(harness, tmp_path) -> None:
    assert _launch(tmp_path)
    calls = harness.resolve_calls
    harness.workers = {URL}
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1


def test_retry_gives_up_when_page_assist_is_disabled(harness, tmp_path, monkeypatch) -> None:
    assert _launch(tmp_path)
    calls = harness.resolve_calls
    monkeypatch.setattr(notifier, "_active_viewer_engagement", lambda: None)
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1


def test_window_closed_during_retry_discovery_does_not_start_assist(
    harness, tmp_path, monkeypatch,
) -> None:
    assert _launch(tmp_path)

    def resolve(_profile, *, is_cancelled, **_kwargs):
        assert not is_cancelled()
        harness.window_open = False
        assert is_cancelled()
        return CdpAttachResult(ok=True, port=35555, reason="process")

    monkeypatch.setattr(cdp_client, "resolve_cdp_attach", resolve)
    FakeTimer.created[0].function()
    harness.assist.assert_not_called()
    assert len(FakeTimer.created) == 1


def test_retry_stops_after_limit(harness, tmp_path) -> None:
    assert notifier.CDP_ATTACH_RETRY_LIMIT == 2
    assert _launch(tmp_path)
    FakeTimer.created[0].function()
    assert len(FakeTimer.created) == 2
    assert FakeTimer.created[1].started
    FakeTimer.created[1].function()
    assert len(FakeTimer.created) == 2
    assert harness.resolve_calls == 3
    harness.assist.assert_not_called()
    assert notifier._CDP_ATTACH_RETRIES == {}
    # Re-attaching never asks the allocator to close or reopen windows.
    assert not claim_registry.take_endpoint_restart(now=1000.0)


def test_relaunch_replaces_pending_retry_for_same_url(harness, tmp_path) -> None:
    assert _launch(tmp_path)
    assert _launch(tmp_path)
    assert len(FakeTimer.created) == 2
    assert FakeTimer.created[0].cancelled
    assert _pending() == [FakeTimer.created[1]]
    # A superseded timer that still fires must not act.
    calls = harness.resolve_calls
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    assert _pending() == [FakeTimer.created[1]]


@pytest.mark.parametrize("close_all", [False, True])
def test_closing_window_cancels_pending_retry(harness, tmp_path, close_all) -> None:
    assert _launch(tmp_path)
    if close_all:
        notifier.close_all_tracked_windows()
    else:
        notifier.close_browser_window_for_url(URL)
    assert FakeTimer.created[0].cancelled
    assert notifier._CDP_ATTACH_RETRIES == {}
    calls = harness.resolve_calls
    FakeTimer.created[0].function()
    assert harness.resolve_calls == calls
    harness.assist.assert_not_called()


def test_disabling_engagement_cancels_pending_retries(harness, tmp_path, monkeypatch) -> None:
    assert _launch(tmp_path)
    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", None)
    notifier.configure_viewer_engagement(None)
    assert FakeTimer.created[0].cancelled
    assert notifier._CDP_ATTACH_RETRIES == {}


def test_recovering_nondebug_requests_endpoint_restart_without_retry(
    harness, tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        cdp_client, "acquire_cdp_debugging_port",
        lambda *_a, **_k: CdpAttachResult(ok=False, reason="profile_nondebug"),
    )
    monkeypatch.setattr(notifier, "_nondebug_recovery_port", lambda _attach: 49674)
    assert _launch(tmp_path)
    assert FakeTimer.created == []
    assert claim_registry.take_endpoint_restart(now=1000.0)


def test_real_timer_is_daemon_and_cancellable(harness, monkeypatch) -> None:
    """The production timer must never keep the process alive at exit."""
    import threading

    monkeypatch.setattr(notifier, "_CDP_ATTACH_RETRY_TIMER", threading.Timer)
    monkeypatch.setattr(notifier, "CDP_ATTACH_RETRY_DELAY_S", 3600.0)
    assert notifier._schedule_cdp_attach_retry(URL, "profile")
    (timer,) = notifier._CDP_ATTACH_RETRIES.values()
    try:
        assert timer.daemon and timer.is_alive()
    finally:
        notifier.cancel_cdp_attach_retries()
    timer.join(timeout=2.0)
    assert not timer.is_alive()
    assert notifier._CDP_ATTACH_RETRIES == {}
