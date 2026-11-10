"""Explicit login allocation for Twitch multiview; never promises server credit."""

from dataclasses import replace
from pathlib import Path

from stream_monitor.browser_settings_model import BrowserSettings
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    active_points_channels_configured,
)


def route_browser(
    url: str, browser: BrowserSettings | None,
    engagement: ViewerEngagementSettings | None,
    *, managed: bool = True,
) -> BrowserSettings | None:
    if engagement is None or engagement.points_role(url) == "shared":
        return browser
    if browser is None or not browser.enabled or not browser.user_data_dir.strip():
        raise ValueError("Points priority needs a dedicated browser profile")
    from stream_monitor.notifier import detect_browser_family

    if detect_browser_family(browser.browser_path) == "firefox":
        raise ValueError("Points priority needs a Chromium browser")
    if engagement.points_role(url) == "priority":
        return replace(browser, per_channel_profile=False, guest_mode=False)
    if managed and active_points_channels_configured():
        # Automatic playback outside the current signed-in allocation is
        # suppressed. Guest windows looked like forced sign-in prompts and
        # cannot maintain the user's account streak.
        return None
    if browser.guest_mode:
        return browser
    base = Path(browser.user_data_dir)
    return replace(
        browser, user_data_dir=str(base.parent / (base.name + "_watch_only")),
        per_channel_profile=False, guest_mode=True,
    )
