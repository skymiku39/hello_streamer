"""Renderer regression checks, distinct from real-account Twitch acceptance."""

import shutil
import subprocess
import time

import pytest

from scripts.verify_twitch_live import (
    POINTS_TARGET_JS,
    STREAK_JS,
    clear_pointer,
    close_points_dialog,
)
from stream_monitor.cdp_client import CdpClient, read_devtools_active_port
from stream_monitor.notifier import _resolve_browser_executable
from stream_monitor.twitch_page_assist import _PageAssistWorker
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings


def test_hover_overlay_escape_ignored_dialog_and_native_risk_widget(tmp_path):
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome is unavailable")
    profile = tmp_path / "chrome"
    process = subprocess.Popen([
        executable, "--headless=new", "--remote-debugging-port=0",
        "--remote-allow-origins=*", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    client = CdpClient()
    try:
        deadline = time.monotonic() + 30
        port = None
        while port is None and time.monotonic() < deadline:
            assert process.poll() is None
            port = read_devtools_active_port(str(profile))
            if port is None:
                time.sleep(.1)
        assert port is not None
        client.connect(port, "about:blank")
        client.evaluate("""document.body.innerHTML = `
          <div data-test-selector="community-points-summary" style="position:fixed;left:20px;top:200px">
            <div><img id="coin" style="width:20px;height:20px">
              <span data-test-selector="copo-balance-string">20.1萬</span></div>
          </div>
          <div role="tooltip" style="display:none;position:fixed;left:40px;top:200px;z-index:10">
            1,790 Bits, 201,623 HiCoin</div>
          <div role="dialog" style="display:none;position:fixed;inset:10px;z-index:20;background:white">
            <button aria-label="關閉">x</button>
            <div id="native"><p id="count">您的連續觀賞紀錄：32</p>
              <button aria-label="開啟連續觀賞選單">&gt;</button></div>
            <span data-test-selector="copo-balance-string">20.1萬</span>
          </div>`;
          const tooltip=document.querySelector('[role=tooltip]');
          const coin=document.querySelector('#coin');
          coin.addEventListener('mouseenter',()=>tooltip.style.display='block');
          coin.addEventListener('mouseleave',()=>tooltip.style.display='none');
          document.querySelector('button[aria-label="關閉"]').onclick=()=>
            document.querySelector('[role=dialog]').style.display='none';
        """)
        reader = _PageAssistWorker("https://www.twitch.tv/fixture", port,
                                   ViewerEngagementSettings())
        assert reader._read_points_balance(client) == 201623
        assert client.evaluate(POINTS_TARGET_JS) is None  # Tooltip covers the balance text.
        clear_pointer(client)
        assert client.evaluate(POINTS_TARGET_JS) is not None
        client.evaluate("document.querySelector('[role=dialog]').style.display='block'")
        assert client.evaluate(STREAK_JS)["count"] == 32
        client.evaluate("document.querySelector('#count').innerText='32\\n連續紀錄即將中斷'")
        risk = client.evaluate(STREAK_JS)
        assert risk["count"] == 32 and risk["status"] == "at_risk"
        close_points_dialog(client)
        assert client.evaluate(POINTS_TARGET_JS) is not None
        client.evaluate("""document.querySelector('[data-test-selector="community-points-summary"]').innerHTML=`
          <button aria-label="小奇點和點數餘額" style="width:120px;height:30px">1,790
            <span data-test-selector="copo-balance-string" style="display:none">12.7萬</span>
          </button>
          <button id="bonus" aria-label="領取額外獎勵">Bonus</button>`;
          window.claims=0;document.querySelector('#bonus').onclick=()=>window.claims++;
          document.querySelector('button[aria-label="小奇點和點數餘額"]').onclick=()=>
            document.querySelector('[role=dialog]').style.display='block';
        """)
        menu = client.evaluate(POINTS_TARGET_JS)
        assert menu is not None
        client.click_at(menu["x"], menu["y"])
        assert client.evaluate(STREAK_JS)["count"] == 32
        assert client.evaluate("window.claims") == 0
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
