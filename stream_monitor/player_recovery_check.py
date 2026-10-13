"""Opt-in source/frozen smoke check against an isolated local fixture only."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from stream_monitor.cdp_client import CdpClient
from stream_monitor.twitch_page_assist import _PageAssistWorker
from stream_monitor.twitch_player_recovery import PLAYER_SNAPSHOT_JS
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

FIXTURE_PATH = "/hello-streamer-player-recovery-check"


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument("--fixture-url", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    parts = urlsplit(args.fixture_url)
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1"
            or not parts.port or parts.path != FIXTURE_PATH):
        parser.error("Only the isolated loopback player-recovery fixture is supported")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(args.report.with_suffix(".log")), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", force=True)
    report = {"mode": "frozen" if getattr(sys, "frozen", False) else "source",
              "synthetic_fixture": True, "natural_twitch_error_verified": False,
              "cases": [], "passed": False}
    client = CdpClient()
    try:
        client.connect(args.cdp_port, args.fixture_url, timeout=10.)
        if client.evaluate("window.__helloStreamerRecoveryFixture === true") is not True:
            raise RuntimeError("Fixture marker missing; no worker was started")
        for case in ("recovered", "exhausted", "stalled", "cancelled", "offline"):
            client.evaluate(f"window.setRecoveryCase({json.dumps(case)})")
            worker = _PageAssistWorker(args.fixture_url, args.cdp_port, ViewerEngagementSettings(
                enabled=True, page_assist_enabled=True, auto_refresh=False,
                accept_content_gate=False, theater_mode=True, claim_channel_points=False))
            worker._theater_confirmed_once = True
            started = time.monotonic()
            worker.start()
            try:
                limit = 105. if case == "exhausted" else 22.
                while time.monotonic() - started < limit:
                    elapsed = time.monotonic() - started
                    state = worker._player_recovery
                    if case == "cancelled" and elapsed >= 1.:
                        worker.stop()
                    if case == "offline" and elapsed >= 1.:
                        worker._confirmed_offline.set()
                    if case == "recovered" and not state.active and client.evaluate("window.clicks") == 1:
                        break
                    if case == "exhausted" and state.notice == "player_2000_exhausted":
                        break
                    if case in {"cancelled", "offline"} and elapsed >= 7.:
                        break
                    if case == "stalled" and elapsed >= 20.:
                        break
                    time.sleep(.1)
                clicks = client.evaluate("window.clicks")
                snap = client.evaluate(PLAYER_SNAPSHOT_JS)
                outcome = {"case": case, "seconds": round(time.monotonic() - started, 2),
                           "clicks": clicks, "attempts": state.attempts,
                           "active": state.active, "notice": state.notice,
                           "media_time": snap.get("currentTime"),
                           "theater_preserved": worker._theater_confirmed_once}
                report["cases"].append(outcome)
                if case == "recovered":
                    valid = clicks == 1 and not state.active and not state.notice and snap.get("currentTime", 0) > 1
                elif case == "exhausted":
                    valid = clicks == 3 and state.attempts == 3 and state.notice == "player_2000_exhausted"
                elif case == "stalled":
                    valid = clicks == 1 and state.active and state.notice == "player_2000_unverified"
                else:
                    valid = clicks == 0 and state.attempts == 0
                if not valid or not outcome["theater_preserved"]:
                    raise RuntimeError(f"Recovery contract failed: {outcome}")
            finally:
                worker.stop()
                worker.join(timeout=5.)
                if worker._thread.is_alive():
                    raise RuntimeError("Fixture worker did not stop")
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        logging.exception("Player recovery self-check failed")
    finally:
        client.close()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)
