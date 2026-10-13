"""Recovery contracts using explicit snapshots and a deterministic clock."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from stream_monitor import twitch_page_assist as assist
from stream_monitor import twitch_player_recovery as recovery
from stream_monitor.page_assist_status import drain_page_assist_status
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

URL = "https://www.twitch.tv/example"
ERROR = {"url": URL, "playerPresent": True, "errorCode": "2000", "retryToken": "button-1"}
PLAYING = {"url": URL, "playerPresent": True, "errorCode": None,
           "hasVideo": True, "readyState": 4, "paused": False,
           "currentTime": 10., "videoId": "video-1"}


@pytest.fixture
def rig(monkeypatch):
    drain_page_assist_status()
    clock = [0.]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: clock[0])
    state = SimpleNamespace(snapshot=dict(ERROR), clicks=[], clicked=True)
    client = Mock(connected=True)

    def evaluate(expression):
        if expression == recovery.PLAYER_SNAPSHOT_JS:
            return dict(state.snapshot)
        state.clicks.append(clock[0])
        return {"clicked": state.clicked}

    client.evaluate.side_effect = evaluate
    controller = recovery.PlayerRecovery(URL, threading.Event(), threading.Event())

    def step(at, snapshot=None):
        clock[0] = at
        if snapshot is not None:
            state.snapshot = dict(snapshot)
        controller.step(client)

    state.controller, state.client, state.step, state.clock = controller, client, step, clock
    yield state
    drain_page_assist_status()


def test_bounded_backoff_and_exhaustion_are_observable(rig, caplog):
    with caplog.at_level("INFO"):
        for at in (0, 4.9, 5, 16, 17, 31.9, 32, 44, 73.9, 74, 86, 120, 1000):
            rig.step(at)
    assert rig.clicks == [5, 32, 74]
    assert rig.controller.attempts == 3
    assert rig.controller.notice == "player_2000_exhausted"
    assert "code=2000 attempt=3/3 action=reload_player" in caplog.text
    assert "result=player_2000_exhausted" in caplog.text
    rig.client.reload.assert_not_called()


def test_recovery_requires_three_separated_advancing_samples(rig):
    rig.step(0)
    rig.step(5)
    assert rig.controller.active  # Successful click alone proves nothing.
    rig.step(6, PLAYING)
    rig.step(6.1, {**PLAYING, "currentTime": 11})
    assert rig.controller.active
    rig.step(7, {**PLAYING, "currentTime": 11})
    assert rig.controller.active
    rig.step(8, {**PLAYING, "currentTime": 12})
    assert not rig.controller.active
    assert rig.controller.attempts == 0
    assert drain_page_assist_status()[-1] == (URL, "")
    rig.step(10, ERROR)
    rig.step(15)
    assert rig.clicks == [5, 15]  # Only verified recovery resets the budget.


@pytest.mark.parametrize("change", [
    {"currentTime": 10}, {"currentTime": None}, {"currentTime": float("nan")},
    {"currentTime": float("inf")}, {"currentTime": True}, {"paused": True},
    {"readyState": 2}, {"ended": True}, {"mediaError": True},
    {"errorCode": "3000"}, {"errorCode": "2000"},
    {"hasVideo": False}, {"playerPresent": False}, {"gatePresent": True},
    {"offline": True}, {"url": "https://www.twitch.tv/other"},
])
def test_inconclusive_playback_never_resets_budget_or_claims_recovery(rig, change):
    rig.step(0)
    rig.step(5)
    for at in (6, 7, 8, 17, 40):
        rig.step(at, {**PLAYING, "currentTime": at, **change})
    assert rig.controller.active
    assert rig.controller.attempts == 1
    assert (URL, "") not in drain_page_assist_status()


def test_video_replacement_time_reset_and_stall_break_the_streak(rig):
    rig.step(0)
    rig.step(5)
    for at, current, identity in [(6, 10, "a"), (7, 11, "a"), (8, 12, "b"),
                                  (9, 1, "b"), (10, 1, "b"), (11, 2, "b")]:
        rig.step(at, {**PLAYING, "currentTime": current, "videoId": identity})
        assert rig.controller.active
    rig.step(12, {**PLAYING, "currentTime": 3, "videoId": "b"})
    assert not rig.controller.active


@pytest.mark.parametrize("snapshot", [
    PLAYING, {**PLAYING, "paused": True}, {**PLAYING, "readyState": 2},
    {**PLAYING, "hasVideo": False}, {**ERROR, "errorCode": "3000"},
    {**ERROR, "offline": True}, {**ERROR, "gatePresent": True},
    {**ERROR, "playerPresent": False}, {**ERROR, "url": "https://evil.example/"},
])
def test_non_errors_pauses_buffering_offline_and_wrong_pages_do_not_click(rig, snapshot):
    for at in (0, 30, 60, 120):
        rig.step(at, snapshot)
    assert rig.clicks == []


def test_pause_before_error_is_preserved_until_user_resumes(rig):
    rig.step(0, {**PLAYING, "paused": True})
    rig.step(30, ERROR)
    rig.step(60)
    assert rig.clicks == []
    for at in (90, 120, 150):
        rig.step(at, {**PLAYING, "currentTime": at})
    assert not rig.controller.active


@pytest.mark.parametrize("cancel", ["stop", "offline", "disconnected", "missing_button", "changed_url"])
def test_cancel_or_revalidate_before_pending_retry(rig, cancel):
    rig.step(0)
    if cancel in {"stop", "offline"}:
        getattr(rig.controller, cancel).set()
    elif cancel == "disconnected":
        rig.client.connected = False
    elif cancel == "missing_button":
        rig.snapshot["retryToken"] = None
    else:
        rig.snapshot["url"] = "https://www.twitch.tv/other"
    rig.step(5)
    assert rig.clicks == []


def test_stop_during_probe_cancels_the_following_click(rig):
    rig.step(0)

    def stop_and_return(_expression):
        rig.controller.stop.set()
        return dict(ERROR)

    rig.client.evaluate.side_effect = stop_and_return
    rig.step(5)
    assert rig.controller.attempts == 0


def test_reconnect_retains_attempt_budget_and_starts_fresh_cooldown(rig):
    rig.step(0)
    rig.step(5)
    rig.controller.disconnected()
    rig.step(10)
    rig.step(24.9)
    assert rig.clicks == [5]
    rig.step(25)
    assert rig.clicks == [5, 25]


def test_uncertain_click_transport_error_consumes_an_attempt(rig):
    rig.step(0)
    evaluate = rig.client.evaluate.side_effect

    def fail_click(expression):
        if expression != recovery.PLAYER_SNAPSHOT_JS:
            raise RuntimeError("CDP disconnected after dispatch")
        return evaluate(expression)

    rig.client.evaluate.side_effect = fail_click
    for at in (5, 6, 21, 22, 52, 53, 90):
        rig.step(at)
    assert rig.controller.attempts == 3
    assert rig.controller.notice == "player_2000_exhausted"


def test_false_click_result_is_never_reported_as_recovery(rig):
    rig.clicked = False
    rig.step(0)
    rig.step(5)
    assert rig.controller.notice == "player_2000_unverified"
    assert rig.controller.active


def test_each_managed_page_has_an_independent_budget(rig):
    other = recovery.PlayerRecovery("https://www.twitch.tv/other", threading.Event(), threading.Event())
    other_client = Mock(connected=True)
    other_client.evaluate.return_value = {**PLAYING, "url": other.url}
    for at in (0, 5, 17, 32, 44, 74, 86):
        rig.step(at)
        other.step(other_client)
    assert rig.controller.attempts == 3
    assert other.attempts == 0 and not other.active
    assert all(call.args[0] == recovery.PLAYER_SNAPSHOT_JS for call in other_client.evaluate.call_args_list)
    other_client.reload.assert_not_called()


def test_worker_recovers_with_refresh_disabled_and_preserves_theater(rig, monkeypatch):
    worker = assist._PageAssistWorker(URL, 1234, ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True, auto_refresh=False, theater_mode=True))
    worker._theater_confirmed_once = True
    worker._player_recovery = rig.controller
    monkeypatch.setattr(worker, "_probe_page_ready", lambda c: {})
    for at in (0, 5):
        rig.clock[0] = at
        worker._check_page(rig.client)
    assert rig.clicks == [5]
    assert rig.controller.attempts == 1
    assert worker._theater_confirmed_once
    rig.client.send_alt_t.assert_not_called()
    rig.client.click_at.assert_not_called()
    rig.client.reload.assert_not_called()


def test_disabled_assist_does_not_probe_recovery(monkeypatch):
    worker = assist._PageAssistWorker(URL, 1234, ViewerEngagementSettings())
    recovery_step = Mock()
    monkeypatch.setattr(worker._player_recovery, "step", recovery_step)
    monkeypatch.setattr(worker, "_probe_page_ready", lambda c: {})
    worker._check_page(Mock())
    recovery_step.assert_not_called()


def test_stop_all_signals_every_worker_before_join(monkeypatch):
    workers = [assist._PageAssistWorker(f"{URL}{i}", 1234, ViewerEngagementSettings()) for i in range(2)]
    monkeypatch.setattr(assist, "_assist_by_url", {str(i): w for i, w in enumerate(workers)})
    def check_stopped(**_):
        assert all(x._stop.is_set() for x in workers)

    for w in workers:
        monkeypatch.setattr(w, "join", check_stopped)
    assist.stop_all_page_assist()
    assert all(w._player_recovery.stop.is_set() for w in workers)
    assert not assist._assist_by_url


@pytest.mark.parametrize("settings", [None, {}, {"enabled": True, "page_assist_enabled": False}])
def test_disabling_page_assist_stops_existing_workers(monkeypatch, settings):
    from stream_monitor import notifier

    worker = assist._PageAssistWorker(URL, 1234, ViewerEngagementSettings())
    monkeypatch.setattr(assist, "_assist_by_url", {assist._url_key(URL): worker})
    monkeypatch.setattr(notifier, "_VIEWER_ENGAGEMENT", None)
    notifier.configure_viewer_engagement(settings)
    assert worker._stop.is_set()
    assert not assist._assist_by_url


def test_manual_window_close_cancels_retries_without_keep_awake(monkeypatch):
    from stream_monitor import notifier

    worker = assist._PageAssistWorker(URL, 1234, ViewerEngagementSettings())
    monkeypatch.setattr(assist, "_assist_by_url", {assist._url_key(URL): worker})
    monkeypatch.setattr(notifier, "_OBS_MANAGED_URLS", {URL})
    monkeypatch.setattr(notifier, "_OBS_MANAGED_SINCE", {URL: 0.})
    monkeypatch.setattr(notifier, "_ENGAGEMENT_AWAKE_URLS", set())
    monkeypatch.setattr(notifier, "tracked_hwnds_for_url", lambda url: set())
    notifier.sync_observed_closes_for_tracked_windows(min_age_s=0.)
    assert worker._stop.is_set()
    assert not assist._assist_by_url
