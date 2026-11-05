"""Real Twitch account acceptance: explicit UI evidence across actual broadcasts."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from stream_monitor.browser_win32 import set_system_keep_awake
from stream_monitor.cdp_client import (
    CdpClient,
    _list_targets,
    acquire_cdp_debugging_port,
)
from stream_monitor.playback_health import PlaybackHealth
from stream_monitor.twitch_acceptance import epoch, record_evidence
from stream_monitor.twitch_page_assist import _PageAssistWorker
from stream_monitor.twitch_player_recovery import PLAYER_SNAPSHOT_JS
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

# Match only a native Watch Streak widget, never arbitrary reward/chat text.
STREAK_JS = r"""(() => {
 const re = /(?:您的連續觀[看賞]紀錄|你的連續觀[看賞]紀錄|您的连续观[看赏]记录|Your Watch Streak)\s*[:：]?\s*(\d[\d,]*)/i;
 for (const button of document.querySelectorAll('[role="dialog"] button[aria-label]')) {
   if (!/連續觀[看賞]|连续观[看赏]|watch streak/i.test(button.getAttribute('aria-label') || '')) continue;
   for (let el=button,n=0;el && n<6;el=el.parentElement,n++) {
     const text=(el.innerText || '').trim();
     const risk=text.match(/^(\d[\d,]*)\s*(?:連續紀錄即將中斷|连续记录即将中断|watch streak at risk|streak at risk)/i);
     const match=text.match(re) || risk;
     if (!match || text.length > 600) continue;
     const parent=el.parentElement;
     const box=parent && parent.innerText.trim()===text ? parent : el;
     box.scrollIntoView({behavior:'instant',block:'center',inline:'nearest'});
     const r=box.getBoundingClientRect();
     if (r.width<1 || r.height<1 || r.left<0 || r.top<0 ||
         r.right>innerWidth || r.bottom>innerHeight) continue;
     const top=document.elementFromPoint(r.x+r.width/2,r.y+Math.min(8,r.height/2));
     if(!top || !(top===box || box.contains(top))) continue;
     return {count:Number(match[1].replaceAll(',', '')),text,status:risk?'at_risk':'active',
       rect:{x:r.x,y:r.y,width:r.width,height:r.height}};
   }
 }
 return null;
})()"""

POINTS_TARGET_JS = r"""(() => {
 for (const summary of document.querySelectorAll('[data-test-selector="community-points-summary"]')) {
   const balance=summary.querySelector('[data-test-selector="copo-balance-string"]');
   if (!balance) continue;
   // An available bonus chest can hide the entire points balance. Open only
   // the explicitly labelled balances menu; never click the claim button.
   const menu=[...summary.querySelectorAll('button[aria-label]')].find(b =>
     /^(?:小奇點和點數餘額|小奇点和点数余额|bits and (?:channel )?points balances?)$/i.test(b.getAttribute('aria-label') || ''));
   for (const el of [balance,menu].filter(Boolean)) {
     const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
     const top=document.elementFromPoint(x,y);
     if(r.width<1 || r.height<1 || x<0 || y<0 || x>=innerWidth || y>=innerHeight ||
        !top || !(top===el || el.contains(top))) continue;
     return {x,y};
   }
 }
 return null;
})()"""


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch_latest(state: dict, channel: str) -> float | None:
    return epoch(state.get("channels", {}).get(channel, {}).get("latest", {}).get("ts_utc"))


def notify_risk(state: dict, sample: dict) -> None:
    if sample.get("streak_status") != "at_risk" or os.name != "nt":
        return
    row = state["channels"][sample["channel"]]
    key = sample.get("prior_stream_ended_at") or sample.get("stream_started_at")
    if not key or row.get("risk_notification_key") == key:
        return
    try:
        from winotify import Notification

        Notification(app_id="HelloStreamer", title="Twitch 連續觀賞即將中斷",
                     msg=f"{sample['channel']}：{sample['streak_count']} 場。請查看 Twitch 官方保持連續紀錄入口。",
                     duration="long").show()
        row["risk_notification_key"] = key
    except Exception as exc:
        print(json.dumps({"risk_notification_error": type(exc).__name__}), flush=True)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers can briefly deny delete-sharing on the destination.
    # Retrying the atomic replacement keeps a status inspection from killing
    # a multi-day collector, without exposing a partially written checkpoint.
    deadline = time.monotonic() + 3.0
    while True:
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def escape(client: CdpClient) -> None:
    for phase in ("keyDown", "keyUp"):
        client.call("Input.dispatchKeyEvent", {
            "type": phase, "key": "Escape", "windowsVirtualKeyCode": 27,
        })


def clear_pointer(client: CdpClient) -> None:
    """Dismiss hover overlays; background pages may suspend animation frames."""
    client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
    # Chrome can suspend both rAF and page timers in a covered window. Wait on
    # the collector's clock, then remeasure and hit-test the actual native UI.
    time.sleep(.15)


def close_points_dialog(client: CdpClient) -> None:
    """Escape does not close Twitch's rewards panel. Only click its own Close."""
    clear_pointer(client)
    hit = client.evaluate(r"""(() => {
      for (const dialog of document.querySelectorAll('[role="dialog"]')) {
        if (!dialog.querySelector('[data-test-selector="copo-balance-string"]') &&
            ![...dialog.querySelectorAll('button[aria-label]')].some(b =>
              /連續觀[看賞]|连续观[看赏]|watch streak/i.test(b.getAttribute('aria-label') || ''))) continue;
        const b=[...dialog.querySelectorAll('button[aria-label]')].find(b =>
          /^(?:關閉|关闭|close|閉じる|닫기)$/i.test(b.getAttribute('aria-label') || ''));
        if (!b) continue;
        const r=b.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
        const top=document.elementFromPoint(x,y);
        if (r.width>0 && r.height>0 && top && (top===b || b.contains(top))) return {x,y};
      }
      return null;
    })()""")
    if hit:
        client.click_at(hit["x"], hit["y"])
        clear_pointer(client)


def capture_rect(client: CdpClient, rect: dict, path: Path, *, streak: bool = False) -> dict | None:
    """CDP clip uses device-independent coordinates, unlike DOM CSS rects."""
    clear_pointer(client)
    refreshed = client.evaluate(STREAK_JS) if streak else None
    if streak:
        if not isinstance(refreshed, dict):
            raise RuntimeError("Native streak became unavailable before capture")
        rect = refreshed["rect"]
    metrics = client.call("Page.getLayoutMetrics")
    zoom = metrics.get("visualViewport", {}).get("zoom", 1.0)
    css = metrics.get("cssVisualViewport", {})
    clip = {
        "x": (rect["x"] + css.get("pageX", 0)) * zoom,
        "y": (rect["y"] + css.get("pageY", 0)) * zoom,
        "width": rect["width"] * zoom, "height": rect["height"] * zoom, "scale": 1,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(client.call("Page.captureScreenshot", {
        "format": "png", "clip": clip,
    })["data"]))
    return refreshed


def probe_ui(client: CdpClient, channel: str, port: int, output: Path,
             previous: dict | None = None) -> dict:
    client.call("Page.navigate", {"url": f"https://www.twitch.tv/popout/{channel}/chat?popout="})
    deadline = time.monotonic() + 12
    hit = None
    while time.monotonic() < deadline:
        page = client.evaluate("location.pathname")
        if page == f"/popout/{channel}/chat":
            hit = client.evaluate(POINTS_TARGET_JS)
            if not hit:
                close_points_dialog(client)
                hit = client.evaluate(POINTS_TARGET_JS)
            if hit:
                break
        time.sleep(.25)
    sample = {"channel": channel, "ts_utc": utc(), "points_balance": None,
              "streak_count": None, "source": "real_account_twitch_popout_ui",
              "synthetic_fixture": False, "screenshot_version": 4}
    if not hit:
        sample["ui_status"] = "points_ui_unavailable"
        return sample
    reader = _PageAssistWorker(f"https://www.twitch.tv/{channel}", port,
                               ViewerEngagementSettings())
    sample["points_balance"] = reader._read_points_balance(client)
    unbounded = client.evaluate(
        "document.querySelector('[data-test-selector=copo-balance-string]')?.innerText.trim() === '∞'"
    )
    sample["points_unbounded"] = bool(unbounded)
    if unbounded:
        rect = client.evaluate("""(() => {
          const el=document.querySelector('[data-test-selector="community-points-summary"]');
          const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};
        })()""")
        image_path = output / "evidence" / channel / "unbounded-points.png"
        capture_rect(client, rect, image_path)
        sample["points_screenshot"] = str(image_path.resolve())
    # The balance reader leaves a tooltip over the next click target. Escape
    # neither removes that hover nor reliably closes an already-open panel.
    close_points_dialog(client)
    hit = client.evaluate(POINTS_TARGET_JS)
    if not hit:
        sample["ui_status"] = "points_control_unreachable"
        return sample
    client.click_at(hit["x"], hit["y"])
    client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
    deadline = time.monotonic() + 4
    streak = None
    while time.monotonic() < deadline:
        streak = client.evaluate(STREAK_JS)
        if isinstance(streak, dict):
            break
        time.sleep(.2)
    if isinstance(streak, dict):
        sample.update(streak_count=streak["count"], streak_text=streak["text"],
                      streak_status=streak["status"],
                      ui_status="native_streak_visible")
        if (previous and previous.get("streak_count") == streak["count"]
                and previous.get("streak_status", "active") == streak["status"]
                and previous.get("screenshot_version") == 4
                and Path(previous.get("streak_screenshot") or "").is_file()):
            sample["streak_screenshot"] = previous.get("streak_screenshot")
        else:
            image_path = output / "evidence" / channel / (str(time.time_ns()) + "-streak.png")
            captured = capture_rect(client, streak["rect"], image_path, streak=True)
            sample.update(streak_count=captured["count"], streak_text=captured["text"],
                          streak_status=captured["status"])
            sample["streak_screenshot"] = str(image_path.resolve())
    else:
        sample["ui_status"] = "streak_ui_unavailable"
    if sample["points_balance"] is None:
        # The native reward footer exposes the complete balance even when a
        # compact summary's hover tooltip cannot be read. Never scale 12.6萬.
        sample["points_balance"] = client.evaluate(r"""(() => {
          for (const b of document.querySelectorAll('[role="dialog"] button[aria-label]')) {
            const text=b.getAttribute('aria-label') || '';
            const match=text.match(/^您有\s*([\d,]+)\s*點/);
            if(match) return Number(match[1].replaceAll(',', ''));
          }
          return null;
        })()""")
        if sample["points_balance"] is None:
            # The open rewards panel can expose a visible coin even while a
            # pending bonus hides the original summary's balance.
            sample["points_balance"] = reader._read_points_balance(client)
    if client.evaluate("location.pathname") != f"/popout/{channel}/chat":
        sample.update(points_balance=None, streak_count=None, ui_status="page_changed")
        sample.pop("streak_screenshot", None)
    close_points_dialog(client)
    return sample


def acceptance_scope(config: dict, requested: list[str] | None) -> list[str]:
    configured = [c["name"].lower() for c in config["channels"]
                  if c["platform"] == "twitch" and c.get("enabled", True)]
    if requested is None:
        return configured
    selected = {name.lower() for name in requested}
    if not selected or not selected.issubset(configured):
        raise ValueError("--channels must select enabled Twitch channels")
    # Completion must cover exactly the requested channels. Keeping unrelated
    # guest players in the scope would leave an otherwise successful run pending.
    return [name for name in configured if name in selected]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--max-hours", type=float, default=168)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--inspector-target", help="ID of this task's own chat-only tab")
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--channels", nargs="+", help="Limit acceptance scope to these configured channels")
    parser.add_argument("--notify", action="store_true", help="Desktop notices for streak risks and run completion")
    args = parser.parse_args()
    if not args.baseline and not args.watch:
        parser.error("Choose --baseline or --watch")
    if args.interval < 5 or args.max_hours <= 0:
        parser.error("Use a positive duration and an interval of at least 5 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.log_file:
        stream = args.log_file.open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    lock = (output / "collector.lock").open("a+b")
    if os.name == "nt":
        import msvcrt

        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = output / "acceptance.json"
    config_path = args.app_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        scope = acceptance_scope(config, args.channels)
    except ValueError as exc:
        parser.error(str(exc))
    state = (json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else
             {"schema_version": 1, "created_utc": utc(), "scope": scope,
              "channels": {}, "result": "pending"})
    assert state["scope"] == scope, "Acceptance scope changed; use a separate output directory"
    state["collector"] = {"pid": os.getpid(), "state": "running", "started_utc": utc()}
    atomic_json(state_path, state)
    deadline = time.monotonic() + args.max_hours * 3600
    health: dict[str, PlaybackHealth] = {}
    inspector_id = args.inspector_target or state.get("inspector_target_id")
    cursor = 0
    awake = False
    try:
        while time.monotonic() < deadline and not (output / "STOP").exists():
            cycle_started = time.monotonic()
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                state["collector"]["last_error"] = {
                    "type": type(exc).__name__, "ts_utc": utc(), "operation": "read_config",
                }
                atomic_json(state_path, state)
                time.sleep(5)
                continue
            profile = config["browser_settings"]["user_data_dir"]
            settings = config.get("viewer_engagement", {})
            requested_awake = bool(args.watch and settings.get("enabled")
                                   and settings.get("keep_system_awake"))
            if requested_awake != awake:
                set_system_keep_awake(requested_awake)
                awake = requested_awake
                state["collector"]["keep_awake_from_saved_preference"] = awake
            attach = acquire_cdp_debugging_port(profile)
            if attach.lease is not None:
                attach.lease.release()
            if not attach.ok or attach.lease is not None:
                state["collector"]["waiting_for"] = "app_owned_browser_endpoint"
                state["collector"]["last_cycle_utc"] = utc()
                atomic_json(state_path, state)
                if args.baseline:
                    raise RuntimeError("No verified real-account browser is running")
                time.sleep(args.interval)
                continue
            port = attach.port
            try:
                targets = _list_targets(port)
            except (OSError, ValueError):
                health.clear()
                time.sleep(5)
                continue
            pages = [t for t in targets if t.get("type") == "page"]
            inspectors = [t for t in pages if t["id"] == inspector_id]
            if not inspectors:
                # A chat-only inspector never plays video or earns watch credit.
                launcher = CdpClient()
                if not pages:
                    time.sleep(args.interval)
                    continue
                try:
                    launcher.connect(port, pages[0]["url"])
                    inspector_id = launcher.call("Target.createTarget", {
                        "url": f"https://www.twitch.tv/popout/{scope[0]}/chat?popout=",
                    })["targetId"]
                    state["inspector_target_id"] = inspector_id
                    atomic_json(state_path, state)
                except (OSError, RuntimeError, TimeoutError):
                    time.sleep(5)
                finally:
                    launcher.close()
                continue
            inspector = inspectors[0]
            inspector_id = inspector["id"]
            state["inspector_target_id"] = inspector_id
            playing = []
            playback_pages = []
            cache = config.get("channel_status_cache", {}).get("channels", {})
            for target in pages:
                url = str(target.get("url", "")).split("?", 1)[0].rstrip("/")
                channel = url.rsplit("/", 1)[-1].lower()
                if channel not in scope or url != "https://www.twitch.tv/" + channel:
                    continue
                if cache.get("twitch:" + channel, {}).get("state") != "live":
                    continue
                player = CdpClient()
                try:
                    player.connect(port, target["url"], timeout=3)
                    snap = player.evaluate(PLAYER_SNAPSHOT_JS)
                    status = health.setdefault(target["id"], PlaybackHealth()).observe(
                        snap, time.monotonic(), connected=player.connected)
                    environment = player.evaluate("""({
                      document_visibility:document.visibilityState, has_focus:document.hasFocus(),
                      logged_in:!!document.querySelector('[data-a-target="user-menu-toggle"]'),
                      login_button:!!document.querySelector('[data-a-target="login-button"]'),
                      screen_x:screenX,screen_y:screenY,outer_width:outerWidth,outer_height:outerHeight,
                      screen_width:screen.width,screen_height:screen.height
                    })""")
                    window = player.call("Browser.getWindowForTarget", {"targetId": target["id"]})
                    playback_pages.append({
                        "channel": channel, "playback": status,
                        "media_time": snap.get("currentTime"), "video_id": snap.get("videoId"),
                        "paused": snap.get("paused"), "ready_state": snap.get("readyState"),
                        "error_code": snap.get("errorCode"), "environment": environment,
                        "window_bounds": window.get("bounds"),
                    })
                    if status == "playing":
                        playing.append(channel)
                except Exception:
                    health.pop(target["id"], None)
                finally:
                    player.close()
            for row in state["channels"].values():
                prior = row.get("overlap_sample")
                if prior and (prior["channel"] not in playing or len(
                    set(prior["playing_channels"]).intersection(playing)
                ) < 2):
                    row["overlap_sample"] = None
            with (output / "observations.ndjson").open("a", encoding="utf-8") as file:
                file.write(json.dumps({"type": "playback.overlap", "ts_utc": utc(),
                                       "playing_channels": playing,
                                       "pages": playback_pages}) + "\n")
            live_channels = [
                name for name in scope if cache.get("twitch:" + name, {}).get("state") == "live"
            ]
            # Refresh offline native states too: this captures missed-stream
            # recovery warnings and a new baseline before the next broadcast.
            offline_due = [name for name in scope if name not in live_channels and
                           time.time() - (epoch_latest(state, name) or 0) >= 300]
            requested = scope if args.baseline else live_channels + offline_due
            if not args.baseline and requested:
                # Keep the cursor in the fixed scope, since the offline-due
                # subset changes after each probe. A changing ring can starve
                # live channels and invalidate adjacent evidence intervals.
                rotated = scope[cursor:] + scope[:cursor]
                requested = [name for name in rotated if name in requested][:3]
                cursor = (scope.index(requested[-1]) + 1) % len(scope)
            client = CdpClient()
            try:
                client.connect(port, inspector["url"], timeout=5)
                for channel in requested:
                    try:
                        previous = state["channels"].get(channel, {}).get("latest")
                        sample = probe_ui(client, channel, port, output, previous)
                        stream = cache.get("twitch:" + channel, {})
                        sample.update(stream_state=stream.get("state", "unknown"),
                                      stream_started_at=stream.get("started_at"),
                                      prior_stream_ended_at=stream.get("ended_at"),
                                      playing_channels=sorted(set(playing)))
                        record_evidence(state, sample)
                        if args.notify:
                            notify_risk(state, sample)
                        with (output / "observations.ndjson").open("a", encoding="utf-8") as file:
                            file.write(json.dumps(sample, ensure_ascii=False) + "\n")
                        atomic_json(state_path, state)
                        print(json.dumps(sample), flush=True)
                    except Exception as exc:
                        state["collector"]["last_error"] = {
                            "channel": channel, "type": type(exc).__name__, "ts_utc": utc(),
                        }
                        atomic_json(state_path, state)
                        try:
                            close_points_dialog(client)
                        except Exception:
                            pass
                state["collector"].update(last_cycle_utc=utc(),
                    playing_channels=sorted(set(playing)), live_channels=live_channels,
                    waiting_for="actual_live_overlap_and_next_broadcast_streak_increment")
                atomic_json(state_path, state)
            except (OSError, RuntimeError, TimeoutError) as exc:
                health.clear()
                state["collector"]["last_error"] = {
                    "type": type(exc).__name__, "ts_utc": utc(), "operation": "inspect_browser",
                }
                atomic_json(state_path, state)
            finally:
                client.close()
            if args.baseline or state["result"] in {"passed", "completed_with_not_applicable"}:
                break
            time.sleep(max(1.0, args.interval - (time.monotonic() - cycle_started)))
    finally:
        if awake:
            set_system_keep_awake(False)
        state["collector"].update(state="stopped", stopped_utc=utc())
        atomic_json(state_path, state)
        lock.close()
        print(json.dumps({"result": state["result"], "output": str(state_path)}), flush=True)
        if args.notify and os.name == "nt":
            try:
                from winotify import Notification

                passed = sum(row.get("result") == "passed" for row in state["channels"].values())
                Notification(
                    app_id="HelloStreamer", title="Twitch 真實驗收已結束",
                    msg=f"已通過 {passed} 個頻道；整體結果：{state['result']}。結果位於 {state_path}",
                    duration="long",
                ).show()
            except Exception as exc:
                print(json.dumps({"notification_error": type(exc).__name__}), flush=True)


if __name__ == "__main__":
    main()
