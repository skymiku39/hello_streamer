"""Real Windows/Chromium lifecycle, using local pages and a disposable profile."""

import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest

from stream_monitor import browser_win32 as bw
from stream_monitor.cdp_client import CdpClient, read_devtools_active_port
from stream_monitor.notifier import _resolve_browser_executable


@pytest.mark.skipif(os.name != "nt", reason="Native HWND layout requires Windows")
def test_separate_topmost_windows_have_distinct_native_bounds(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome is not installed")
    marker = "Layout-" + tmp_path.name
    profile = tmp_path / "profile"
    url = "data:text/html," + quote(f"<title>{marker}-control</title>")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen([
        executable, f"--user-data-dir={profile}", "--remote-debugging-port=0",
        "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check", url,
    ], startupinfo=startup, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(bw, "_REGISTRY", bw._WindowRegistry())
    client = CdpClient()
    try:
        end = time.monotonic() + 30
        port = None
        while port is None and time.monotonic() < end:
            port = read_devtools_active_port(str(profile))
            time.sleep(.1)
        assert port is not None
        client.connect(port, url)
        bounds = []
        user32 = ctypes.windll.user32
        bw._configure_user32_signatures(user32)
        for i in range(2):
            target_url = "data:text/html," + quote(f"<title>{marker}-{i}</title>")
            baseline = bw._enum_browser_hwnds("Chrome_WidgetWin_1")
            client.call("Target.createTarget", {"url": target_url, "newWindow": True})
            worker = bw._apply_new_browser_window_settings_async(
                "Chrome_WidgetWin_1", baseline,
                {"x": 0, "y": 0, "width": 480, "height": 800,
                 "user_data_dir": str(profile), "separate_windows": True, "always_on_top": True},
                track_for_url=target_url, track_keywords=(f"{marker}-{i}",), deadline_s=15,
            )
            assert worker is not None
            worker.join(timeout=20)
            assert not worker.is_alive()
            handles = bw.tracked_hwnds_for_url(target_url)
            assert len(handles) == 1
            hwnd = next(iter(handles))
            rect = wintypes.RECT()
            assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
            assert user32.GetWindowLongW(hwnd, bw._GWL_EXSTYLE) & 0x00000008  # WS_EX_TOPMOST
            bounds.append((rect.left, rect.top, rect.right, rect.bottom))
        assert bounds[0][:2] != bounds[1][:2]
        assert bounds[0][2] <= bounds[1][0] or bounds[1][2] <= bounds[0][0]
    finally:
        try:
            client.call("Browser.close")
        except (OSError, RuntimeError):
            pass
        client.close()
        if process.poll() is None:
            process.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Native HWND lifecycle requires Windows")
def test_slow_page_then_redirect_closes_original_window_only(tmp_path, monkeypatch):
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        pytest.skip("Chrome is not installed")
    marker = "HelloStreamer-" + tmp_path.name

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                body = ("<title>Google Chrome</title><script>setTimeout(() => {"
                        f"document.title = '{marker}-source';"
                        "}, 12000)</script>")
            else:
                body = f"<title>{marker}{self.path}</title>"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    profile = tmp_path / "profile"
    args = [executable, f"--user-data-dir={profile}", "--remote-debugging-port=0",
            "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding"]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    processes = []
    client = CdpClient()
    registry = bw._WindowRegistry()
    monkeypatch.setattr(bw, "_REGISTRY", registry)
    tracker = None

    def wait_until(predicate, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = predicate()
            if result:
                return result
            time.sleep(.1)
        pytest.fail("Isolated browser condition timed out")

    try:
        processes.append(subprocess.Popen(args + [f"--app={base}/control"], startupinfo=startup,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        control = wait_until(lambda: next((hwnd for hwnd, title in bw._enum_visible_hwnds_with_title()
            if title == marker + "/control"), None))
        baseline = bw._enum_browser_hwnds("Chrome_WidgetWin_1")
        url = base + "/slow"
        started = time.monotonic()
        processes.append(subprocess.Popen(args + [f"--app={url}"], startupinfo=startup,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        tracker = bw._apply_new_browser_window_settings_async(
            "Chrome_WidgetWin_1", baseline, {"user_data_dir": str(profile), "minimized": True},
            track_for_url=url, track_keywords=(marker + "-source",), apply_geometry=False,
        )
        tracked = wait_until(lambda: bw.tracked_hwnds_for_url(url))
        assert time.monotonic() - started >= 10  # Exceeds the old 8-second cutoff.
        assert len(tracked) == 1
        hwnd = next(iter(tracked))
        assert bw._window_matches_profile(hwnd, str(profile))
        assert not bw._window_matches_profile(hwnd, str(tmp_path / "unrelated-profile"))
        client.connect(read_devtools_active_port(str(profile)), url, timeout=5)
        # A real navigation changes the title and URL while retaining the HWND.
        client.call("Page.navigate", {"url": base + "/destination?referrer=raid"})
        wait_until(lambda: any(h == hwnd and title == marker + "/destination?referrer=raid"
                              for h, title in bw._enum_visible_hwnds_with_title()))
        assert bw.close_browser_window_for_url(url, title_keywords=[marker + "-source"]) == 1
        wait_until(lambda: not bw.managed_window_is_open(url)
                   and hwnd not in bw._enum_browser_hwnds("Chrome_WidgetWin_1"))
        assert control in bw._enum_browser_hwnds("Chrome_WidgetWin_1")
    finally:
        registry.reset()
        if tracker:
            tracker.join(timeout=2)
        client.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        server.shutdown()
        server.server_close()
