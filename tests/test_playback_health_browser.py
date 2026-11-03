"""Concurrent real media clocks in isolated Chrome; no Twitch account involved."""

import shutil
import subprocess
import time
from urllib.parse import quote

import pytest

from stream_monitor import playback_health
from stream_monitor.cdp_client import CdpClient, read_devtools_active_port
from stream_monitor.notifier import _resolve_browser_executable
from stream_monitor.playback_health import PlaybackHealth
from stream_monitor.twitch_player_recovery import PLAYER_SNAPSHOT_JS


def test_six_real_players_detect_only_the_frozen_clock(monkeypatch, tmp_path):
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome is unavailable")
    profile = tmp_path / "chrome"
    process = subprocess.Popen([
        executable, "--headless=new", "--remote-debugging-port=0",
        "--remote-allow-origins=*", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--autoplay-policy=no-user-gesture-required", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    clients = []
    root = CdpClient()
    try:
        deadline = time.monotonic() + 30
        port = None
        while port is None and time.monotonic() < deadline:
            assert process.poll() is None, "Isolated Chrome exited"
            # Windows can expose the file before Chrome releases its writer.
            port = read_devtools_active_port(str(profile))
            if port is not None:
                break
            time.sleep(.1)
        assert port is not None, "Isolated Chrome CDP startup timed out"
        root.connect(port, "about:blank")
        for i in range(6):
            html = f"""<div class='video-player' style='width:320px;height:240px'>
            <video width='320' height='240' muted autoplay></video></div>
            <canvas width='320' height='240'></canvas><script>
            window.fixtureNumber = {i};
            const canvas = document.querySelector('canvas');
            const video = document.querySelector('video');
            setInterval(() => {{
              canvas.getContext('2d').fillText(String(performance.now()), 10, 20);
            }}, 50);
            video.srcObject = canvas.captureStream(20); video.play();
            </script>"""
            url = "data:text/html," + quote(html)
            root.call("Target.createTarget", {"url": url})
            client = CdpClient()
            client.connect(port, url)
            clients.append(client)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all(c.evaluate("document.querySelector('video')?.currentTime > .1")
                   for c in clients):
                break
            time.sleep(.2)
        else:
            pytest.fail("Real fixture video clocks did not start")
        # Freeze the public media clock with paused=false to reproduce the
        # old false 'playing' inference. The other five use real video clocks.
        clients[3].evaluate("Object.defineProperty(document.querySelector('video'),"
                            "'currentTime', {get: () => 10, configurable:true})")
        monkeypatch.setattr(playback_health, "STALL_SECONDS", 2.0)
        health = [PlaybackHealth() for _ in clients]
        states = []
        # Other native/Chrome tests can temporarily delay frame delivery. Keep
        # observing real clocks until all pages establish consecutive evidence,
        # without accepting a stalled healthy page as a passing result.
        for _ in range(12):
            states = [h.observe(c.evaluate(PLAYER_SNAPSHOT_JS), time.monotonic(),
                                connected=c.connected) for h, c in zip(health, clients)]
            if states == ["playing", "playing", "playing", "stalled", "playing", "playing"]:
                break
            time.sleep(1.1)
        assert states == ["playing", "playing", "playing", "stalled", "playing", "playing"]
    finally:
        for client in clients:
            client.close()
        root.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
