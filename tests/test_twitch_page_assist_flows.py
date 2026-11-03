"""Regression checks for player switching, bonus claims and refresh recovery."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from unittest.mock import Mock

import pytest

from stream_monitor import twitch_page_assist as assist
from stream_monitor import twitch_player_recovery as recovery
from stream_monitor.cdp_client import CdpClient
from stream_monitor.notifier import _resolve_browser_executable
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings


@pytest.fixture(scope="module")
def dom_browser(tmp_path_factory):
    """Use an isolated headless Chromium profile; never attach to a user tab."""
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome not available for DOM fixture tests")
    profile = tmp_path_factory.mktemp("twitch-dom-browser")
    process = subprocess.Popen(
        [executable, "--headless=new", "--remote-debugging-port=0",
         "--remote-allow-origins=*", f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client = CdpClient()
    try:
        deadline = time.monotonic() + 15
        port_file = profile / "DevToolsActivePort"
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Isolated Chrome exited before CDP was ready")
            time.sleep(.1)
        assert port_file.exists(), "Isolated Chrome CDP startup timed out"
        client.connect(int(port_file.read_text().splitlines()[0]), "about:blank", timeout=5)
        yield client
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def set_dom(client, html):
    frame = client.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
    client.call("Page.setDocumentContent", {"frameId": frame, "html": html})


def test_browser_connection_survives_quiet_interval(dom_browser):
    set_dom(dom_browser, "<p>Quiet page</p>")
    time.sleep(12)  # Longer than websocket.create_connection(timeout=10).
    assert dom_browser.connected
    assert dom_browser.evaluate("document.querySelector('p').textContent") == "Quiet page"


@pytest.mark.parametrize("label", [
    "劇院模式 (alt+t)", "剧院模式 (alt+t)", "Theatre Mode (alt+t)",
    "Theater Mode (alt+t)", "シアターモード (alt+t)", "극장 모드 (alt+t)",
])
def test_current_localized_theater_button_is_known_off(dom_browser, label):
    set_dom(dom_browser, f'<div class="player-controls"><button aria-label="{label}" '
            'aria-haspopup="menu" aria-expanded="true"> </button></div>')
    result = dom_browser.evaluate(assist._THEATER_STATE_JS)
    assert result["state"] == "off"
    assert result["control"]["found"]


@pytest.mark.parametrize("label", [
    "離開劇院模式 (alt+t)", "Exit Theatre Mode (alt+t)",
    "シアターモードを終了 (alt+t)", "극장 모드 종료 (alt+t)",
])
def test_exit_label_is_known_on(dom_browser, label):
    set_dom(dom_browser, f'<div class="player-controls"><button aria-label="{label}"> </button></div>')
    assert dom_browser.evaluate(assist._THEATER_STATE_JS)["state"] == "on"


def test_generic_popup_outside_player_is_unknown(dom_browser):
    set_dom(dom_browser, '<button aria-label="劇院模式" aria-expanded="true"> </button>')
    result = dom_browser.evaluate(assist._THEATER_STATE_JS)
    assert result["state"] == "unknown"
    assert result["control"] is None


@pytest.mark.parametrize("extra", [
    'style="position:absolute;left:20000px"', 'disabled',
    'style="pointer-events:none"',
])
def test_unreachable_theater_control_is_not_clicked(dom_browser, extra):
    set_dom(dom_browser, f'<div class="player-controls"><button aria-label="劇院模式 (alt+t)" {extra}> </button></div>')
    result = dom_browser.evaluate(assist._THEATER_STATE_JS)
    assert result["state"] == "off"
    assert result["control"] is None


def test_conflicting_theater_signals_do_not_toggle(dom_browser):
    set_dom(dom_browser, '<div class="video-player--theatre"><div class="player-controls">'
            '<button aria-label="劇院模式 (alt+t)"> </button></div></div>')
    result = dom_browser.evaluate(assist._THEATER_STATE_JS)
    assert result["state"] == "unknown"
    assert result["control"] is None


def test_current_bonus_selector_and_no_redemption_click(dom_browser):
    set_dom(dom_browser, '<div data-test-selector="community-points-summary">'
            '<button class="claimable-bonus" aria-label="領取額外獎勵">Bonus</button>'
            '<button aria-label="使用點數兌換獎勵">Redeem</button></div>')
    before = dom_browser.evaluate(assist._FIND_CLAIM_JS)
    assert before["found"]
    assert "領取額外獎勵" in before["label"]
    dom_browser.evaluate("document.querySelector('.claimable-bonus').remove()")
    after = dom_browser.evaluate(assist._FIND_CLAIM_JS)
    assert after == {"found": False, "pointsPresent": True}


@pytest.mark.parametrize("html", [
    '<div class="chat-room"><button aria-label="領取獎勵">Claim</button></div>',
    '<div data-test-selector="community-points-summary"><button aria-label="領取 Drops 獎勵">Claim</button></div>',
    '<div data-test-selector="community-points-summary"><button aria-label="領取兌換獎勵">Claim</button></div>',
    '<div class="chat-room"><button aria-label="Claim Bonus">Claim</button></div>',
])
def test_other_rewards_are_not_channel_points_bonus(dom_browser, html):
    set_dom(dom_browser, html)
    assert not dom_browser.evaluate(assist._FIND_CLAIM_JS)["found"]


def test_hidden_points_summary_is_not_claim_confirmation(dom_browser):
    set_dom(dom_browser, '<div data-test-selector="community-points-summary" style="display:none">'
            '<button class="claimable-bonus">Bonus</button></div>')
    assert dom_browser.evaluate(assist._FIND_CLAIM_JS) == {
        "found": False, "pointsPresent": False,
    }


def test_second_points_balance_reads_full_hover_tooltip(dom_browser, monkeypatch):
    set_dom(dom_browser, '''<style>
      body { margin: 8px; }
      button { position: relative; border: 0; padding: 0; }
      .coin-balance { display: flex; align-items: center; gap: 4px; }
      #copo-icon { width: 20px; height: 20px; }
      [role="tooltip"] { display: none; position: absolute; width: 320px; height: 30px; }
      .coin-balance:hover + [role="tooltip"] { display: block; }
    </style>
    <div data-test-selector="community-points-summary">
      <button aria-label="小奇點和點數餘額">
        <div class="coin-balance">
          <img id="copo-icon" alt="">
          <div data-test-selector="copo-balance-string">11.5萬</div>
        </div>
        <div role="tooltip">1,790 枚小奇點, 115,380 儚(ㄇㄥˊ)點</div>
      </button>
    </div>''')
    w = worker(monkeypatch)
    assert w._read_points_balance(dom_browser) == 115380


def test_second_points_balance_accepts_exact_display_and_rejects_compact_values():
    assert assist._parse_exact_points_balance("115,380") == 115380
    assert assist._parse_exact_points_balance("115380") == 115380
    assert assist._parse_exact_points_balance("11.5萬") is None
    assert assist._parse_points_tooltip_balance([
        "1,790 枚小奇點, 115,380 儚(ㄇㄥˊ)點",
    ]) == 115380
    assert assist._parse_points_tooltip_balance([
        "1,790 枚小奇點, 115,380 儚點, 22 個獎勵",
    ]) is None


def test_points_tooltip_reads_accessibility_region_with_zero_height(dom_browser):
    tooltip = "1,790 枚小奇點, 115,380 儚(ㄇㄥˊ)點"
    set_dom(dom_browser, f'<div role="tooltip" style="display:block;width:300px;height:0;overflow:visible">{tooltip}</div>')
    assert dom_browser.evaluate(assist._READ_POINTS_TOOLTIP_JS) == [tooltip]


def test_unknown_points_value_never_triggers_f5_refresh(monkeypatch):
    w = worker(monkeypatch, points_stall_refresh_enabled=True)
    w._points_active_watch_seconds = assist._POINTS_STALL_WINDOW_S
    w._points_last_balance = 100
    monkeypatch.setattr(w, "_read_points_balance", lambda _client: None)
    client = Mock()
    client.connected = True
    assert not w._check_points_stall(client, user_requested=True)
    client.reload.assert_not_called()


def worker(monkeypatch, **settings):
    w = assist._PageAssistWorker("https://www.twitch.tv/example", 1234,
                                ViewerEngagementSettings(**settings))
    monkeypatch.setattr(w, "_sleep", lambda seconds: not w._stop.is_set())
    return w


@pytest.mark.parametrize("states,clicks,keys", [
    (["on"], 0, 0), (["unknown"], 0, 0), (["off", "on"], 1, 0),
    (["off", "unknown"], 1, 0), (["off", "off", "on"], 1, 1),
    (["off", "off", "off"], 1, 1),
])
def test_theater_transition_is_bounded(monkeypatch, states, clicks, keys):
    w = worker(monkeypatch)
    client = Mock()
    client.evaluate.return_value = None
    snapshots = iter({"state": s, "control": {"found": True, "x": 30, "y": 40}}
                     for s in states)
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: next(snapshots))
    w._ensure_theater(client)
    assert client.click_at.call_count == clicks
    assert client.send_alt_t.call_count == keys


def test_repeated_theater_ensure_preserves_active_mode(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    client.evaluate.return_value = None
    snapshots = iter([{"state": "off", "control": {"found": True, "x": 1, "y": 2}},
                      {"state": "on"}, {"state": "on"}])
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: next(snapshots))
    w._ensure_theater(client)
    w._ensure_theater(client)
    assert client.click_at.call_count == 1
    client.send_alt_t.assert_not_called()


BONUS = {"found": True, "x": 50, "y": 30, "left": 30, "top": 20,
         "width": 40, "height": 20, "key": "bonus"}


@pytest.mark.parametrize("after,success", [
    ({"found": False, "pointsPresent": True}, True),
    ({"found": False}, False), ({"found": False, "probeError": True}, False),
    (BONUS, False),
])
def test_claim_requires_disappearance_with_points_ui(monkeypatch, after, success):
    w = worker(monkeypatch, claim_click_offset_px=100)
    client = Mock()
    monkeypatch.setattr(w, "_ensure_chat_visible", lambda c: "visible")
    probes = iter([BONUS, BONUS, after])
    monkeypatch.setattr(w, "_probe_claim", lambda c: next(probes))
    assert w._try_claim(client) is success
    x, y = client.click_at.call_args.args
    assert 30 < x < 70 and 20 < y < 40


@pytest.mark.parametrize("chat", ["hidden", "collapsed", "not_logged_in", "unavailable"])
def test_claim_skipped_without_visible_logged_in_chat(monkeypatch, chat):
    w = worker(monkeypatch)
    client = Mock()
    monkeypatch.setattr(w, "_ensure_chat_visible", lambda c: chat)
    assert not w._try_claim(client)
    client.click_at.assert_not_called()


def test_claim_rechecks_after_delay(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    monkeypatch.setattr(w, "_ensure_chat_visible", lambda c: "visible")
    probes = iter([BONUS, {"found": False, "pointsPresent": True}])
    monkeypatch.setattr(w, "_probe_claim", lambda c: next(probes))
    assert not w._try_claim(client)
    client.click_at.assert_not_called()


def test_stop_cancels_claim_before_click(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    monkeypatch.setattr(w, "_ensure_chat_visible", lambda c: "visible")
    monkeypatch.setattr(w, "_probe_claim", lambda c: BONUS)
    monkeypatch.setattr(w, "_sleep", lambda seconds: False)
    assert not w._try_claim(client)
    client.click_at.assert_not_called()


@pytest.mark.parametrize("after,expected", [
    ({"status": "visible", "chatVisible": True}, "visible"),
    ({"status": "hidden"}, "hidden"),
    ({"status": "visible", "notLoggedIn": True}, "not_logged_in"),
])
def test_collapsed_chat_is_expanded_and_verified(monkeypatch, after, expected):
    w = worker(monkeypatch)
    client = Mock()
    snapshots = iter([{"status": "collapsed", "expandControl":
                       {"found": True, "x": 30, "y": 40}}, after])
    monkeypatch.setattr(w, "_chat_snapshot", lambda c: next(snapshots))
    assert w._ensure_chat_visible(client) == expected
    client.click_at.assert_called_once_with(30., 40.)


def test_visible_chat_with_login_prompt_cannot_claim(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    monkeypatch.setattr(w, "_chat_snapshot", lambda c:
                        {"status": "visible", "notLoggedIn": True})
    assert not w._try_claim(client)
    client.click_at.assert_not_called()


def test_content_gate_click_and_ready_poll(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    client.evaluate.return_value = {"found": True, "x": 100, "y": 200}
    monkeypatch.setattr(w, "_probe_page_ready", lambda c: {"gatePresent": True})
    assert w._wait_for_gate_or_ready(client)
    client.click_at.assert_called_once_with(100., 200.)


def test_ready_without_gate_needs_confirmation(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    readiness = Mock(return_value={"playbackReady": True, "gatePresent": False})
    monkeypatch.setattr(w, "_probe_page_ready", readiness)
    assert w._wait_for_gate_or_ready(client)
    assert readiness.call_count == assist._READY_CONFIRM_POLLS
    client.click_at.assert_not_called()


def test_refresh_restarts_gate_and_theater(monkeypatch):
    w = worker(monkeypatch, auto_refresh=True)
    client = Mock()
    monkeypatch.setattr(assist, "CdpClient", lambda: client)
    schedules = iter([0, None])
    monkeypatch.setattr(w, "_schedule_refresh", lambda: next(schedules))
    runs = []

    def run(c):
        runs.append(c)
        if len(runs) == 2:
            w.stop()

    monkeypatch.setattr(w, "_run_gate_and_theater", run)
    w._run()
    assert len(runs) == 2
    client.reload.assert_called_once()
    client.close.assert_called_once()


def test_successful_claim_keeps_configured_poll_interval(monkeypatch):
    w = worker(monkeypatch, claim_channel_points=True,
               claim_poll_seconds_min=10, claim_poll_seconds_max=10)
    client = Mock()
    monkeypatch.setattr(assist, "CdpClient", lambda: client)
    monkeypatch.setattr(w, "_run_gate_and_theater", lambda c: None)
    clock = [0.]
    sleeps = []
    monkeypatch.setattr(assist.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(assist, "fuzzy_uniform", lambda low, high: low)
    claim = Mock(return_value=True)
    monkeypatch.setattr(w, "_try_claim", claim)

    def wait(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) >= 2:
            w.stop()
            return False
        return False

    monkeypatch.setattr(w, "_wait_for_wake", wait)
    w._run()
    assert sleeps == [10, 10]
    assert claim.call_count == 2


def test_periodic_check_reconciles_enabled_controls(monkeypatch, caplog):
    w = worker(monkeypatch, theater_mode=True, claim_channel_points=True)
    client = Mock()
    monkeypatch.setattr(w, "_probe_page_ready", lambda c:
                        {"hasVideo": True, "readyState": 4, "paused": False,
                         "playbackReady": True})
    theater = Mock()
    chat = Mock(return_value="visible")
    monkeypatch.setattr(w, "_ensure_theater", theater)
    monkeypatch.setattr(w, "_ensure_chat_visible", chat)
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: {"state": "on"})
    with caplog.at_level("INFO"):
        w._check_page(client)
    theater.assert_called_once_with(client)
    chat.assert_called_once_with(client)
    assert "playback=playing theater=on chat=visible" in caplog.text
    client.reload.assert_not_called()


def test_periodic_check_respects_disabled_controls_and_paused_video(monkeypatch, caplog):
    w = worker(monkeypatch)
    client = Mock()
    monkeypatch.setattr(w, "_probe_page_ready", lambda c:
                        {"hasVideo": True, "readyState": 4, "paused": True})
    theater = Mock()
    chat = Mock()
    monkeypatch.setattr(w, "_ensure_theater", theater)
    monkeypatch.setattr(w, "_ensure_chat_visible", chat)
    with caplog.at_level("INFO"):
        w._check_page(client)
    theater.assert_not_called()
    chat.assert_not_called()
    client.reload.assert_not_called()
    assert "playback=paused" in caplog.text


def points_player_sample(current_time, **overrides):
    sample = {
        "playerPresent": True,
        "hasVideo": True,
        "readyState": 4,
        "paused": False,
        "ended": False,
        "mediaError": False,
        "offline": False,
        "gatePresent": False,
        "videoId": "fixture-video",
        "currentTime": current_time,
    }
    sample.update(overrides)
    return sample


def test_points_stall_requires_30_minutes_of_advancing_video(monkeypatch):
    w = worker(monkeypatch)
    due, eligible = w._record_points_observation(
        100, points_player_sample(0), 0, connected=True,
    )
    assert not due and eligible
    for sample_number in range(1, 61):
        now = sample_number * assist._PAGE_CHECK_INTERVAL_S
        due, eligible = w._record_points_observation(
            100, points_player_sample(now), now, connected=True,
        )
        assert eligible
        assert due is (sample_number == 60)
    assert w._points_active_watch_seconds == assist._POINTS_STALL_WINDOW_S


@pytest.mark.parametrize("snapshot,connected", [
    (points_player_sample(30, paused=True), True),
    (points_player_sample(30, readyState=2), True),
    (points_player_sample(30, offline=True), True),
    (points_player_sample(30, gatePresent=True), True),
    (points_player_sample(30, mediaError=True), True),
    (points_player_sample(30), False),
])
def test_points_stall_does_not_count_paused_loading_offline_or_disconnected(
    monkeypatch, snapshot, connected,
):
    w = worker(monkeypatch)
    w._points_last_balance = 100
    w._points_active_watch_seconds = 300
    w._points_media_sample = ("fixture-video", 10, 0)
    due, eligible = w._record_points_observation(
        100, snapshot, 30, connected=connected,
    )
    assert not due and not eligible
    assert w._points_active_watch_seconds == 300
    assert w._points_media_sample is None


def test_unknown_balance_breaks_watch_sample_without_refresh(monkeypatch):
    w = worker(monkeypatch, points_stall_refresh_enabled=True)
    w._points_last_balance = 100
    w._points_active_watch_seconds = assist._POINTS_STALL_WINDOW_S
    w._points_media_sample = ("fixture-video", 10, 0)
    monkeypatch.setattr(w, "_read_points_balance", lambda _client: None)
    client = Mock()
    client.connected = True
    assert not w._check_points_stall(client, user_requested=False)
    assert w._points_active_watch_seconds == assist._POINTS_STALL_WINDOW_S
    assert w._points_media_sample is None
    client.reload.assert_not_called()


def test_positive_points_delta_resets_timer_and_retry_budget(monkeypatch):
    w = worker(monkeypatch)
    w._points_last_balance = 100
    w._points_active_watch_seconds = 900
    w._points_stall_attempts = 2
    w._points_stall_exhausted = True
    w._points_media_sample = ("fixture-video", 10, 0)
    due, eligible = w._record_points_observation(
        110, points_player_sample(40), 30, connected=True,
    )
    assert not due and eligible
    assert w._points_active_watch_seconds == 0
    assert w._points_stall_attempts == 0
    assert not w._points_stall_exhausted


def test_f5_check_refreshes_only_a_stalled_page_even_with_auto_mode_off(monkeypatch):
    w = worker(monkeypatch, points_stall_refresh_enabled=False)
    w._points_last_balance = 100
    w._points_active_watch_seconds = assist._POINTS_STALL_WINDOW_S
    monkeypatch.setattr(w, "_read_points_balance", lambda _client: 100)
    client = Mock()
    client.connected = True
    client.evaluate.return_value = points_player_sample(100)
    reload_page = Mock(return_value=True)
    monkeypatch.setattr(w, "_reload_page", reload_page)
    monkeypatch.setattr(w, "_start_f5_verification", Mock())
    assert w._check_points_stall(client, user_requested=True)
    reload_page.assert_called_once_with(client, reason="F5 points-stall check")
    assert w._points_stall_attempts == 1


def test_f5_broadcast_queues_checks_for_every_worker_without_refreshing(monkeypatch):
    source = Mock()
    other = Mock()
    monkeypatch.setattr(assist, "_assist_by_url", {"other": other, "source": source})
    assist._broadcast_f5_check(source)
    source.request_external_check.assert_called_once_with(delay_s=0.0)
    other.request_external_check.assert_called_once_with(delay_s=assist._F5_WINDOW_STAGGER_S)
    assert "location.reload()" not in assist._F5_BROADCAST_SCRIPT


def test_background_points_refresh_requires_new_setting(monkeypatch):
    w = worker(monkeypatch, points_stall_refresh_enabled=False)
    w._points_last_balance = 100
    w._points_active_watch_seconds = assist._POINTS_STALL_WINDOW_S
    monkeypatch.setattr(w, "_read_points_balance", lambda _client: 100)
    client = Mock()
    client.connected = True
    client.evaluate.return_value = points_player_sample(100)
    reload_page = Mock(return_value=True)
    monkeypatch.setattr(w, "_reload_page", reload_page)
    assert not w._check_points_stall(client, user_requested=False)
    reload_page.assert_not_called()

    w.settings.points_stall_refresh_enabled = True
    assert w._check_points_stall(client, user_requested=False)
    reload_page.assert_called_once_with(client, reason="points-stall auto refresh")


def test_f5_rearms_two_exhausted_points_stall_attempts(monkeypatch):
    from stream_monitor.page_assist_status import drain_page_assist_status

    w = worker(monkeypatch)
    w._points_last_balance = 100
    w._points_active_watch_seconds = assist._POINTS_STALL_WINDOW_S
    w._points_stall_attempts = 2
    w._points_stall_exhausted = True
    monkeypatch.setattr(w, "_read_points_balance", lambda _client: 100)
    client = Mock()
    client.connected = True
    client.evaluate.return_value = points_player_sample(100)
    reload_page = Mock(return_value=True)
    monkeypatch.setattr(w, "_reload_page", reload_page)
    monkeypatch.setattr(w, "_start_f5_verification", Mock())
    assert w._check_points_stall(client, user_requested=True)
    assert w._points_stall_attempts == 1
    assert not w._points_stall_exhausted
    reload_page.assert_called_once_with(client, reason="F5 points-stall check")
    drain_page_assist_status()


def test_periodic_check_rechecks_content_gate_before_controls(monkeypatch):
    w = worker(monkeypatch, accept_content_gate=True, theater_mode=True)
    client = Mock()
    ready = iter([{"gatePresent": True}, {"gatePresent": True}])
    monkeypatch.setattr(w, "_probe_page_ready", lambda c: next(ready))
    gate = Mock(return_value=True)
    theater = Mock()
    monkeypatch.setattr(w, "_try_accept_gate", gate)
    monkeypatch.setattr(w, "_ensure_theater", theater)
    w._check_page(client)
    gate.assert_called_once_with(client)
    theater.assert_not_called()


def test_each_page_checks_every_30_seconds_without_claims(monkeypatch):
    clock = [0.]
    monkeypatch.setattr(assist.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(assist, "fuzzy_uniform", lambda low, high: 90)
    for _ in range(2):
        w = worker(monkeypatch, claim_channel_points=False)
        client = Mock()
        client.connected = True
        monkeypatch.setattr(assist, "CdpClient", lambda: client)
        monkeypatch.setattr(w, "_run_gate_and_theater", lambda c: None)
        start = clock[0]
        checks = []
        points_observations = []

        def check(c):
            checks.append(clock[0] - start)
            if len(checks) == 2:
                w.stop()

        def wait(seconds):
            if w._stop.is_set():
                return False
            clock[0] += seconds
            return False

        monkeypatch.setattr(w, "_check_page", check)
        monkeypatch.setattr(w, "_wait_for_wake", wait)
        monkeypatch.setattr(
            w, "_read_points_balance",
            lambda _client: points_observations.append(clock[0] - start) or 100,
        )
        w._run()
        assert checks == [30, 60]
        assert points_observations == [30, 60]
        client.reload.assert_not_called()


def test_disconnected_page_reconnects_to_same_channel(monkeypatch):
    w = worker(monkeypatch)
    first, replacement = Mock(), Mock()
    first.connected = False
    replacement.connected = True
    clients = iter([first, replacement])
    monkeypatch.setattr(assist, "CdpClient", lambda: next(clients))
    monkeypatch.setattr(w, "_run_gate_and_theater", lambda c: None)
    checked = []

    def check(c):
        checked.append(c)
        w.stop()

    monkeypatch.setattr(w, "_check_page", check)
    w._run()
    assert checked == [replacement]
    replacement.connect.assert_called_once_with(w.port, w.url,
        timeout=assist.DEFAULT_CDP_ATTACH_TIMEOUT_S)
    first.close.assert_called_once()
    replacement.close.assert_called_once()


def test_failed_reconnect_does_not_claim_or_reload(monkeypatch):
    w = worker(monkeypatch, claim_channel_points=True)
    first, replacement = Mock(), Mock()
    first.connected = False
    replacement.connect.side_effect = RuntimeError("offline endpoint")
    clients = iter([first, replacement])
    monkeypatch.setattr(assist, "CdpClient", lambda: next(clients))
    monkeypatch.setattr(w, "_run_gate_and_theater", lambda c: None)
    claim = Mock()
    monkeypatch.setattr(w, "_try_claim", claim)
    monkeypatch.setattr(w, "_sleep", lambda seconds: False)
    w._run()
    claim.assert_not_called()
    replacement.reload.assert_not_called()


def test_periodic_check_preserves_manual_theater_off_after_initialization(monkeypatch, caplog):
    w = worker(monkeypatch, theater_mode=True, claim_channel_points=True)
    client = Mock()
    client.evaluate.return_value = None
    snapshots = iter([{"state": "off", "control": {"found": True, "x": 1, "y": 2}},
                      {"state": "on"}])
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: next(snapshots))
    w._ensure_theater(client)
    assert w._theater_confirmed_once
    client.reset_mock()
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: {"state": "off"})
    monkeypatch.setattr(w, "_probe_page_ready", lambda c:
                        {"hasVideo": True, "readyState": 4, "paused": False})
    monkeypatch.setattr(w, "_ensure_chat_visible", lambda c: "visible")
    with caplog.at_level("INFO"):
        w._check_page(client)
        w._check_page(client)
    client.click_at.assert_not_called()
    client.send_alt_t.assert_not_called()
    assert "theater=off" in caplog.text


def test_initially_active_theater_also_preserves_later_user_choice(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    client.evaluate.return_value = None
    snapshot = Mock(return_value={"state": "on"})
    monkeypatch.setattr(w, "_theater_snapshot", snapshot)
    w._ensure_theater(client)
    snapshot.return_value = {"state": "off"}
    w._ensure_theater(client)
    assert snapshot.call_count == 1
    assert w._theater_confirmed_once
    client.click_at.assert_not_called()
    client.send_alt_t.assert_not_called()


def test_unconfirmed_theater_can_retry_initialization(monkeypatch):
    w = worker(monkeypatch)
    client = Mock()
    client.evaluate.return_value = None
    snapshots = iter([{"state": "unknown"},
                      {"state": "off", "control": {"found": True, "x": 1, "y": 2}},
                      {"state": "on"}])
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: next(snapshots))
    w._ensure_theater(client)
    assert not w._theater_confirmed_once
    w._ensure_theater(client)
    assert w._theater_confirmed_once
    client.click_at.assert_called_once()


def test_gate_flow_alone_does_not_reset_confirmed_theater(monkeypatch):
    w = worker(monkeypatch, theater_mode=True)
    w._theater_confirmed_once = True
    client = Mock()
    monkeypatch.setattr(w, "_wait_for_gate_or_ready", lambda c: True)
    snapshot = Mock(return_value={"state": "off"})
    monkeypatch.setattr(w, "_theater_snapshot", snapshot)
    w._run_gate_and_theater(client)
    snapshot.assert_not_called()
    assert w._theater_confirmed_once
    client.click_at.assert_not_called()
    client.send_alt_t.assert_not_called()


@pytest.mark.parametrize("before,enabled,clicks", [
    ("on", True, 1), ("off", True, 0), ("unknown", True, 0),
    ("on", False, 0),
])
def test_auto_refresh_restores_only_previously_active_theater(
    monkeypatch, before, enabled, clicks,
):
    w = worker(monkeypatch, auto_refresh=True, theater_mode=enabled)
    w._theater_confirmed_once = True
    client = Mock()
    client.evaluate.return_value = None
    monkeypatch.setattr(assist, "CdpClient", lambda: client)
    schedules = iter([0, None])
    monkeypatch.setattr(w, "_schedule_refresh", lambda: next(schedules))
    monkeypatch.setattr(w, "_wait_for_gate_or_ready", lambda c: True)
    monkeypatch.setattr(w, "_wait_for_player_ready", lambda c: True)
    state = [before]
    monkeypatch.setattr(w, "_theater_snapshot", lambda c: {
        "state": state[0], "control": {"found": True, "x": 1, "y": 2},
    })
    client.reload.side_effect = lambda: state.__setitem__(0, "off")
    client.click_at.side_effect = lambda *args: state.__setitem__(0, "on")
    original = w._run_gate_and_theater
    runs = []

    def run(c):
        original(c)
        runs.append(c)
        if len(runs) == 2:
            w.stop()

    monkeypatch.setattr(w, "_run_gate_and_theater", run)
    w._run()
    assert len(runs) == 2
    client.reload.assert_called_once()
    assert client.click_at.call_count == clicks
    client.send_alt_t.assert_not_called()
    assert state[0] == ("on" if clicks else "off")


def error_dom(message="網路發生問題。請再試一次。（錯誤代碼 #2000）",
              button="按一下這裡重新載入播放器", *, error_style="", button_attrs=""):
    return ('<div data-a-target="video-player" style="width:640px;height:360px">'
            f'<p id="error" style="{error_style}">{message}</p>'
            f'<button id="reload" {button_attrs} onclick="window.clicks=(window.clicks||0)+1">'
            f'{button}</button></div>')


@pytest.mark.parametrize("message,button", [
    ("網路發生問題。請再試一次。（錯誤代碼 #2000）", "按一下這裡重新載入播放器"),
    ("There was a network error. Please try again. (Error #2000)", "Click Here to Reload Player"),
    ("網路發生問題。（錯誤代碼 <span>#</span><span>2000</span>）", "重新載入播放器"),
])
def test_player_2000_visible_localized_error_and_guarded_click(dom_browser, message, button):
    set_dom(dom_browser, error_dom(message, button))
    dom_browser.evaluate("window.clicks=0")
    snap = dom_browser.evaluate(recovery.PLAYER_SNAPSHOT_JS)
    assert snap["errorCode"] == "2000"
    assert snap["retryToken"]
    args = json.dumps({"clickToken": snap["retryToken"], "expectedUrl": snap["url"]})
    result = dom_browser.evaluate(f"({recovery._PLAYER_FUNCTION_JS})({args})")
    assert result["clicked"]
    assert dom_browser.evaluate("window.clicks") == 1


@pytest.mark.parametrize("html", [
    '<div class="video-player"><p>Loading</p></div><div class="chat-room">Error #2000</div>',
    '<div class="video-player"><div class="chat-room">Error #2000</div></div>',
    '<div>Network Error #2000<button>Reload Player</button></div>',
    error_dom(error_style="display:none"),
    error_dom(error_style="visibility:hidden"),
    error_dom(error_style="opacity:0"),
    error_dom(error_style="position:absolute;left:20000px"),
    '<div style="opacity:0">' + error_dom() + '</div>',
    error_dom(message="Error #20000"),
])
def test_player_2000_hidden_chat_and_outside_text_are_not_errors(dom_browser, html):
    set_dom(dom_browser, html)
    snap = dom_browser.evaluate(recovery.PLAYER_SNAPSHOT_JS)
    assert snap["errorCode"] is None
    assert snap["retryToken"] is None


@pytest.mark.parametrize("attrs", [
    'disabled', 'aria-disabled="true"', 'style="pointer-events:none"',
    'style="display:none"', 'style="position:absolute;left:20000px"',
])
def test_player_2000_unavailable_reload_control_is_not_eligible(dom_browser, attrs):
    set_dom(dom_browser, error_dom(button_attrs=attrs))
    snap = dom_browser.evaluate(recovery.PLAYER_SNAPSHOT_JS)
    assert snap["errorCode"] == "2000"
    assert snap["retryToken"] is None


@pytest.mark.parametrize("mutation", [
    "document.querySelector('#reload').remove()",
    "document.querySelector('#reload').outerHTML='<button>Reload Player</button>'",
    "document.querySelector('#error').remove()",
    "document.querySelector('#error').textContent='Error #3000'",
    "document.querySelector('#reload').disabled=true",
    "document.querySelector('.video-player, [data-a-target=video-player]').remove()",
    "document.body.insertAdjacentHTML('beforeend', '<div style=\"position:fixed;inset:0;z-index:9\">Blocked</div>')",
])
def test_player_2000_dom_change_cancels_exact_button_click(dom_browser, mutation):
    set_dom(dom_browser, error_dom())
    dom_browser.evaluate("window.clicks=0")
    snap = dom_browser.evaluate(recovery.PLAYER_SNAPSHOT_JS)
    dom_browser.evaluate(mutation)
    args = json.dumps({"clickToken": snap["retryToken"], "expectedUrl": snap["url"]})
    result = dom_browser.evaluate(f"({recovery._PLAYER_FUNCTION_JS})({args})")
    assert not result["clicked"]
    assert dom_browser.evaluate("window.clicks") == 0


@pytest.mark.parametrize("extra", [
    '<div data-a-target="offline-channel-main-content">Offline</div>',
    '<p>This channel is offline</p>',
    '<p>目前離線</p>',
    '<button data-a-target="content-classification-gate-overlay-start-watching-button">Start Watching</button>',
])
def test_player_2000_offline_and_gate_do_not_offer_recovery(dom_browser, extra):
    set_dom(dom_browser, error_dom().replace('</div>', extra + '</div>'))
    snap = dom_browser.evaluate(recovery.PLAYER_SNAPSHOT_JS)
    assert snap["errorCode"] == "2000"
    assert snap["offline"] or snap["gatePresent"]
    assert snap["retryToken"] is None
