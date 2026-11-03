"""Read-only playback evidence from an app-owned Chrome profile, without reloads."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from stream_monitor.cdp_client import (
    CdpClient,
    _list_targets,
    acquire_cdp_debugging_port,
    endpoint_belongs_to_profile,
    page_urls_match,
    read_devtools_active_port,
)
from stream_monitor.playback_health import PlaybackHealth
from stream_monitor.twitch_page_assist import (
    _PageAssistWorker,
)
from stream_monitor.twitch_player_recovery import PLAYER_SNAPSHOT_JS
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings
from stream_monitor.watch_observation import twitch_channel_key_from_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 3 or args.interval < 1:
        parser.error("Use at least three samples, at least one second apart")
    port = args.port or read_devtools_active_port(str(args.profile))
    if port is None or not endpoint_belongs_to_profile(port, str(args.profile)):
        verified = acquire_cdp_debugging_port(str(args.profile))
        if not verified.ok or verified.lease is not None or (
            port is not None and port != verified.port
        ):
            if verified.lease is not None:
                verified.lease.release()
            parser.error("Endpoint is not verified against the supplied profile")
        port = verified.port
    clients = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for target in _list_targets(port):
            url = str(target.get("url", ""))
            parsed = urlsplit(url)
            if target.get("type") != "page" or parsed.hostname not in {
                "www.twitch.tv", "twitch.tv",
            } or len(parsed.path.strip("/").split("/")) != 1:
                continue
            client = CdpClient()
            try:
                client.connect(port, url, timeout=3)
            except Exception:
                client.close()
                continue
            reader = _PageAssistWorker(url, port, ViewerEngagementSettings())
            clients.append((parsed.path.strip("/"), client, PlaybackHealth(), reader))
        with args.output.open("a", encoding="utf-8") as output:
            for index in range(args.samples):
                for channel, client, health, reader in clients:
                    record = {
                        "ts_utc": datetime.now(timezone.utc).isoformat(),
                        "sample": index, "channel_key": "twitch:" + channel,
                        "simultaneous_pages": len(clients),
                        "server_watch_credit": "unknown",
                    }
                    try:
                        snapshot = client.evaluate(PLAYER_SNAPSHOT_JS)
                        if not isinstance(snapshot, dict) or not page_urls_match(
                            reader.url, str(snapshot.get("url", "")),
                        ):
                            health.reset_samples()
                            record.update(
                                playback="unknown", channel_changed=True,
                                observed_channel_key=twitch_channel_key_from_url(
                                    str(snapshot.get("url", ""))
                                    if isinstance(snapshot, dict) else ""),
                                points_balance=None,
                            )
                            output.write(json.dumps(record, ensure_ascii=False) + "\n")
                            output.flush()
                            print(json.dumps(record, ensure_ascii=False), flush=True)
                            continue
                        balance = reader._read_points_balance(client)
                        record.update({
                            "playback": health.observe(snapshot, time.monotonic(),
                                                       connected=client.connected),
                            "media_time": snapshot.get("currentTime"),
                            "paused": snapshot.get("paused"),
                            "ready_state": snapshot.get("readyState"),
                            "points_balance": balance,
                        })
                    except Exception as exc:
                        health.reset_samples()
                        record.update(playback="unknown", error=type(exc).__name__)
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                if index + 1 < args.samples:
                    time.sleep(args.interval)
        print(json.dumps({"pages": len(clients), "samples": args.samples,
                          "output": str(args.output)}))
    finally:
        for _, client, _, _ in clients:
            client.close()


if __name__ == "__main__":
    main()
