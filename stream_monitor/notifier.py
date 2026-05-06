"""觸發行為 — 開播偵測後的四種動作 + 豐富 Toast 通知。"""

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

    return False


def _toast(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a rich Windows Toast notification with optional action button."""
    try:
        from winotify import Notification

        channel_name = info.display_name or info.channel
        body = info.title or f"{channel_name} is now live on {info.platform}"
        platform_display = info.platform.upper()

        toast = Notification(
            app_id="哈嘍主播 Hello Streamer",
            title=f"🔴 {channel_name} 開播了！ [{platform_display}]",
            msg=body,
            duration="long",
            icon="",
        )
        toast.set_audio("ms-winsoundevent:Notification.Default", loop=False)

        if with_open_button:
            toast.add_actions(label="立即觀看", launch=info.url)

        toast.show()
    except Exception:
        logger.exception("Failed to show toast notification")


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
