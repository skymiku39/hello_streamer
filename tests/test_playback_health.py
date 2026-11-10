from unittest.mock import Mock

import pytest

from stream_monitor import twitch_page_assist as assist
from stream_monitor.playback_health import PlaybackHealth
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings
from stream_monitor.watch_observation import should_enable_watch_observation

URL = "https://www.twitch.tv/fixture"


def snapshot(time=10, **extra):
    return dict(url=URL, playerPresent=True, hasVideo=True, videoId="video",
                readyState=4, paused=False, currentTime=time, **extra)


def test_flags_alone_never_prove_playback():
    health = PlaybackHealth()
    assert [health.observe(snapshot(), t, connected=True) for t in (0, 30, 60, 90)] == [
        "verifying", "buffering", "buffering", "stalled",
    ]


def test_media_progress_requires_adjacent_samples():
    health = PlaybackHealth()
    assert health.observe(snapshot(10), 0, connected=True) == "verifying"
    assert health.observe(snapshot(40), 30, connected=True) == "verifying"
    assert health.observe(snapshot(70), 60, connected=True) == "playing"


@pytest.mark.parametrize("changes", [
    {"paused": True}, {"offline": True}, {"gatePresent": True},
    {"hasVideo": False}, {"errorCode": "2000"}, {"mediaError": True},
    {"ended": True}, {"currentTime": float("nan")}, {"currentTime": True},
])
def test_ineligible_samples_break_stall_evidence(changes):
    health = PlaybackHealth()
    for t in (0, 30, 60):
        health.observe(snapshot(), t, connected=True)
    value = snapshot()
    value.update(changes)
    assert health.observe(value, 90, connected=True) in {"unknown", "paused"}
    assert health.observe(snapshot(), 120, connected=True) == "verifying"


def test_disconnect_replacement_seek_and_sleep_break_stall_evidence():
    for replacement, now, connected in [
        ({"videoId": "replacement"}, 90, True),
        ({"currentTime": 1}, 90, True), ({}, 200, True), ({}, 90, False),
    ]:
        health = PlaybackHealth()
        for t in (0, 30, 60):
            health.observe(snapshot(), t, connected=True)
        value = snapshot()
        value.update(replacement)
        assert health.observe(value, now, connected=connected) != "stalled"


def test_retry_budget_survives_reload_and_short_success():
    health = PlaybackHealth()
    health.reloaded()
    health.reloaded()
    assert not health.can_reload
    for t in (0, 1, 2):
        health.observe(snapshot(10 + t), t, connected=True)
    assert not health.can_reload
    for t in (30, 60):
        health.observe(snapshot(10 + t), t, connected=True)
    assert health.can_reload


def test_multiple_pages_reload_only_the_stalled_page(monkeypatch):
    clock = [0]
    monkeypatch.setattr(assist.time, "monotonic", lambda: clock[0])
    workers = [assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings(
        enabled=True, page_assist_enabled=True,
    )) for _ in range(6)]
    clients = [Mock(connected=True) for _ in workers]
    for worker in workers:
        monkeypatch.setattr(worker, "_reload_page", Mock(return_value=True))
    for t in (0, 30, 60, 90):
        clock[0] = t
        for i, (worker, client) in enumerate(zip(workers, clients)):
            client.evaluate.return_value = snapshot(10 if i == 3 else 10 + t)
            worker._check_playback_health(client)
    for i, worker in enumerate(workers):
        assert worker._reload_page.call_count == (1 if i == 3 else 0)
    assert workers[3]._f5_verification_active
    assert workers[3]._playback_health.reload_attempts == 1


def test_raid_does_not_authorize_stall_reload(monkeypatch):
    worker = assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings())
    client = Mock(connected=True)
    client.evaluate.return_value = dict(snapshot(), url="https://www.twitch.tv/raid")
    reload_page = Mock()
    monkeypatch.setattr(worker, "_reload_page", reload_page)
    for t in (0, 30, 60, 90):
        monkeypatch.setattr(assist.time, "monotonic", lambda: t)
        assert not worker._check_playback_health(client)
    reload_page.assert_not_called()


def test_raid_points_are_never_attributed_to_original_channel(monkeypatch):
    worker = assist._PageAssistWorker(URL, 9222, ViewerEngagementSettings())
    worker._points_last_balance = 100
    worker._points_active_watch_seconds = 1800
    client = Mock(connected=True)
    client.evaluate.return_value = dict(snapshot(), url="https://www.twitch.tv/raid")
    monkeypatch.setattr(worker, "_read_points_balance", lambda _: 999)
    reload_page = Mock()
    monkeypatch.setattr(worker, "_reload_page", reload_page)
    assert not worker._check_points_stall(client, user_requested=True)
    assert worker._points_last_balance == 100
    reload_page.assert_not_called()


def test_assistance_enables_observation_without_command_line_flag():
    assert should_enable_watch_observation([], {
        "viewer_engagement": {"enabled": True, "page_assist_enabled": True},
    })
    assert not should_enable_watch_observation([], {})
    assert not should_enable_watch_observation([], {
        "viewer_engagement": {"enabled": False, "page_assist_enabled": True},
    })
    assert should_enable_watch_observation(["--watch-observation"], {})
