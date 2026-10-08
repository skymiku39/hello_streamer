"""Exercise the source app or a real Windows bundle with an isolated fixture.

uv run python scripts/verify_player_recovery.py --output-dir <external-directory>
uv run python scripts/verify_player_recovery.py --output-dir <external-directory> --exe <HelloStreamer.exe>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stream_monitor.cdp_client import read_devtools_active_port
from stream_monitor.notifier import _resolve_browser_executable
from stream_monitor.player_recovery_check import FIXTURE_PATH

FIXTURE_HTML = """<!doctype html><meta charset="utf-8">
<style>body {margin:20px} .video-player {width:640px;height:360px;background:#ddd}
video {width:320px;height:180px} button {padding:16px}</style>
<div class="video-player"></div>
<script>
window.__helloStreamerRecoveryFixture = true;
window.setRecoveryCase = (mode) => {
  if (window.paintTimer) clearInterval(window.paintTimer);
  if (window.media) window.media.getTracks().forEach(t => t.stop());
  window.clicks = 0;
  const root = document.querySelector('.video-player');
  root.innerHTML = '<p id="error">網路發生問題。請再試一次。（錯誤代碼 #2000）</p>' +
    '<button id="reload">按一下這裡重新載入播放器</button><video muted playsinline></video>';
  document.querySelector('#reload').onclick = () => {
    window.clicks++;
    if (mode !== 'recovered' && mode !== 'stalled') return;
    document.querySelector('#error').remove();
    document.querySelector('#reload').remove();
    if (mode === 'stalled') return;
    const canvas = document.createElement('canvas');
    canvas.width = 320; canvas.height = 180;
    const ctx = canvas.getContext('2d');
    let frame = 0;
    const paint = () => { ctx.fillStyle = 'hsl(' + (frame++ % 360) + ' 60% 50%)';
                         ctx.fillRect(0, 0, 320, 180); };
    paint();
    window.paintTimer = setInterval(paint, 50);
    window.media = canvas.captureStream(20);
    const video = document.querySelector('video');
    video.muted = true; video.srcObject = window.media; video.play();
  };
};
setRecoveryCase('recovered');
</script>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != FIXTURE_PATH:
            self.send_error(404)
            return
        data = FIXTURE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    executable = _resolve_browser_executable("chrome")
    if not shutil.which(executable):
        parser.error("Chrome is required; this check does not silently skip")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / ("frozen-recovery.json" if args.exe else "source-recovery.json")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}{FIXTURE_PATH}"
    try:
        with tempfile.TemporaryDirectory(prefix="hellostreamer-recovery-") as profile:
            browser = subprocess.Popen([
                executable, "--headless=new", "--remote-debugging-port=0", "--remote-allow-origins=*",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 15.
                port = None
                while time.monotonic() < deadline:
                    port = read_devtools_active_port(profile)
                    if port is not None:
                        break
                    if browser.poll() is not None:
                        raise RuntimeError("Fixture browser exited before CDP startup")
                    time.sleep(.1)
                if port is None:
                    raise RuntimeError("Fixture browser CDP startup timed out")
                command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-m", "stream_monitor"]
                command += ["--self-check-player-recovery", "--cdp-port", str(port),
                            "--fixture-url", url, "--report", str(report)]
                result = subprocess.run(command, timeout=190., check=False,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if report.exists():
                    print(report.read_text(encoding="utf-8"))
                if result.returncode:
                    raise SystemExit(result.returncode)
                if not report.is_file():
                    raise RuntimeError("App exited without a recovery report")
            finally:
                if browser.poll() is None:
                    browser.terminate()
                    browser.wait(timeout=10.)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.)


if __name__ == "__main__":
    main()
