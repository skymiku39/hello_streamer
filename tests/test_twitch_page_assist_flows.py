"""Regression checks for player switching, bonus claims and refresh recovery."""

from __future__ import annotations

import shutil
import subprocess
import time
from unittest.mock import Mock

import pytest

from stream_monitor import twitch_page_assist as assist
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

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) >= 2:
            w.stop()
            return False
        return True

    monkeypatch.setattr(w, "_sleep", sleep)
    w._run()
    assert sleeps == [10, 10]
    assert claim.call_count == 2
