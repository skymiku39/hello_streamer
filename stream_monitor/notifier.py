"""觸發行為 — 開播偵測後的四種動作。"""

from __future__ import annotations

import logging
import sys
import webbrowser

from stream_monitor.fetcher.base import StreamInfo

logger = logging.getLogger(__name__)


def _toast(info: StreamInfo) -> None:
    """Send a Windows Toast notification."""
    try:
        from winotify import Notification

        toast = Notification(
            app_id="Stream Monitor",
            title=f"{info.channel} 開播了！",
            msg=info.title or f"{info.channel} is now live on {info.platform}",
            duration="long",
        )
        toast.set_audio("ms-winsoundevent:Notification.Default", loop=False)
        toast.show()
    except Exception:
        logger.exception("Failed to show toast notification")


def open_and_stop(info: StreamInfo, stop_fn: callable) -> None:
    """Open stream URL in browser and stop monitoring."""
    webbrowser.open(info.url)
    _toast(info)
    stop_fn()


def open_and_keep(info: StreamInfo) -> None:
    """Open stream URL in browser, keep monitoring other channels."""
    webbrowser.open(info.url)
    _toast(info)


def notify_only(info: StreamInfo) -> None:
    """Show a toast notification without opening the browser."""
    _toast(info)


def open_and_exit(info: StreamInfo) -> None:
    """Open stream URL in browser, then exit the application."""
    webbrowser.open(info.url)
    _toast(info)
    sys.exit(0)


def execute_action(
    action: str,
    info: StreamInfo,
    stop_fn: callable | None = None,
) -> None:
    """Dispatch the configured action."""
    if action == "open_and_stop":
        open_and_stop(info, stop_fn or (lambda: None))
    elif action == "open_and_keep":
        open_and_keep(info)
    elif action == "notify_only":
        notify_only(info)
    elif action == "open_and_exit":
        open_and_exit(info)
    else:
        logger.warning("Unknown action: %s", action)
