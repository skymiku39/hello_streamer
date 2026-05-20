"""觸發行為 — 開播偵測後的四種動作 + 桌面通知（Windows Toast / Linux notify-send）。"""

from __future__ import annotations

import logging
import os
import platform
import webbrowser
from typing import Callable

from stream_monitor.fetcher.base import StreamInfo

logger = logging.getLogger(__name__)

ActionCallback = Callable[[], None]


def open_url(url: str) -> bool:
    """Open *url* in the user's browser, with a Windows fallback."""
    if not url:
        logger.warning("Cannot open empty URL")
        return False

    try:
        opened = webbrowser.open(url, new=2)
        if opened is not False:
            return True
        logger.warning("webbrowser.open returned False for URL: %s", url)
    except Exception:
        logger.exception("Failed to open URL with webbrowser: %s", url)

    if platform.system() == "Windows":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            logger.exception("Failed to open URL with Windows shell: %s", url)
    else:
        import subprocess

        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            logger.warning("xdg-open not found; cannot open URL: %s", url)
        except OSError:
            logger.exception("Failed to open URL with xdg-open: %s", url)

    return False


def _format_scheduled_start(iso_str: str) -> str:
    """Convert ISO 8601 timestamp to a human-readable local time string."""
    if not iso_str:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_str)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def action_for_stream_status(configured_action: str, info: StreamInfo) -> str | None:
    """Return the action that should run for a stream/video event."""
    status = info.stream_status or "live"
    if status == "upcoming":
        return "notify_only"
    if status == "video":
        return None
    return configured_action


def _build_toast_text(info: StreamInfo) -> tuple[str, str]:
    """Return (title, body) for a notification based on stream status."""
    channel_name = info.display_name or info.channel
    platform_display = info.platform.upper()
    status = info.stream_status or "live"

    if status == "upcoming":
        title = f"\U0001f4c5 {channel_name} 已建立待機室 [{platform_display}]"
        time_str = _format_scheduled_start(info.scheduled_start)
        body = f"預計開播：{time_str}" if time_str else (info.title or "即將開播")
    elif status == "video":
        title = f"\U0001f3ac {channel_name} 上傳了新影片 [{platform_display}]"
        body = info.title or "新影片"
    else:
        title = f"\U0001f534 {channel_name} 開播了！ [{platform_display}]"
        body = info.title or f"{channel_name} is now live on {info.platform}"

    return title, body


def _toast_windows(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a rich Windows Toast notification via winotify."""
    try:
        from winotify import Notification

        title, body = _build_toast_text(info)

        toast = Notification(
            app_id="哈嘍主播 Hello Streamer",
            title=title,
            msg=body,
            duration="long",
            icon="",
        )
        toast.set_audio("ms-winsoundevent:Notification.Default", loop=False)

        if with_open_button:
            toast.add_actions(label="立即觀看", launch=info.url)

        toast.show()
    except Exception:
        logger.exception("Failed to show Windows toast notification")


def _toast_linux(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a desktop notification via notify-send (Linux)."""
    try:
        import subprocess

        title, body = _build_toast_text(info)

        cmd = ["notify-send", "--app-name=Hello Streamer", title, body]
        subprocess.run(cmd, check=False, timeout=5)
    except FileNotFoundError:
        logger.warning("notify-send not found; desktop notifications unavailable")
    except Exception:
        logger.exception("Failed to show Linux notification")


def _toast(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a desktop notification (platform-dispatched)."""
    if platform.system() == "Windows":
        _toast_windows(info, with_open_button)
    else:
        _toast_linux(info, with_open_button)


def open_and_stop(info: StreamInfo, stop_fn: ActionCallback) -> None:
    """Open stream URL in browser and stop monitoring."""
    _toast(info, with_open_button=False)
    open_url(info.url)
    stop_fn()


def open_and_keep(info: StreamInfo) -> None:
    """Open stream URL in browser, keep monitoring other channels."""
    _toast(info, with_open_button=False)
    open_url(info.url)


def notify_only(info: StreamInfo) -> None:
    """Show a toast notification with 'open' button — no auto-browser."""
    _toast(info, with_open_button=True)


def open_and_exit(info: StreamInfo, exit_fn: ActionCallback) -> None:
    """Open stream URL in browser, then exit the application."""
    _toast(info, with_open_button=False)
    open_url(info.url)
    exit_fn()


def execute_action(
    action: str,
    info: StreamInfo,
    stop_fn: ActionCallback | None = None,
    exit_fn: ActionCallback | None = None,
) -> None:
    """Dispatch the configured action."""
    if action == "open_and_stop":
        open_and_stop(info, stop_fn or (lambda: None))
    elif action == "open_and_keep":
        open_and_keep(info)
    elif action == "notify_only":
        notify_only(info)
    elif action == "open_and_exit":
        open_and_exit(info, exit_fn or (lambda: None))
    else:
        logger.warning("Unknown action: %s", action)
