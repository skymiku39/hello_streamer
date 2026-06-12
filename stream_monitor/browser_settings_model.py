"""Map browser_settings config to four UI dimensions (A/B/C/D)."""

from __future__ import annotations

from typing import Any

LAUNCH_SYSTEM = "system"
LAUNCH_PROGRAM = "program"

IDENTITY_LOCAL = "local"
IDENTITY_DEDICATED = "dedicated"

# C — how the page appears in the browser UI
PLACEMENT_TAB = "tab"  # 1. tab in an existing window (default)
PLACEMENT_WINDOW = "window"  # 2. new window, typically single tab
PLACEMENT_PLAYER = "player"  # 3. solo window without tab bar (app mode)

CAP_OK = "ok"
CAP_WARN = "warn"
CAP_OFF = "off"

PLACEMENT_CHOICES: tuple[str, ...] = (
    PLACEMENT_TAB,
    PLACEMENT_WINDOW,
    PLACEMENT_PLAYER,
)


def infer_launch_mode(settings: dict[str, Any]) -> str:
    return LAUNCH_PROGRAM if settings.get("enabled") else LAUNCH_SYSTEM


def infer_identity_mode(settings: dict[str, Any]) -> str:
    if (settings.get("user_data_dir") or "").strip():
        return IDENTITY_DEDICATED
    return IDENTITY_LOCAL


def infer_placement_mode(settings: dict[str, Any]) -> str:
    if bool(settings.get("app_mode")):
        return PLACEMENT_PLAYER
    if not bool(settings.get("new_window", False)):
        return PLACEMENT_TAB
    return PLACEMENT_WINDOW


def placement_choices_for_identity(_identity: str) -> tuple[str, ...]:
    """All three open styles are always offered when program launch is on."""
    return PLACEMENT_CHOICES


def auto_cleanup_ui_available(launch: str, identity: str) -> bool:
    """Auto-cleanup section is shown only for a dedicated program account."""
    return launch == LAUNCH_PROGRAM and identity == IDENTITY_DEDICATED


def geometry_placement_available(launch: str, placement: str) -> bool:
    """UI / CLI geometry is meaningful only for separate or solo windows."""
    return launch == LAUNCH_PROGRAM and placement in (
        PLACEMENT_WINDOW,
        PLACEMENT_PLAYER,
    )


def window_management_available(
    launch: str, identity: str, placement: str
) -> bool:
    return (
        launch == LAUNCH_PROGRAM
        and identity == IDENTITY_DEDICATED
        and placement in (PLACEMENT_WINDOW, PLACEMENT_PLAYER)
    )


def capability_summary(
    launch: str, identity: str, placement: str
) -> list[tuple[str, str]]:
    """Return i18n keys for capability chips with status (ok/warn/off)."""
    if launch == LAUNCH_SYSTEM:
        return [
            ("browser.cap.launch", CAP_OFF),
            ("browser.cap.login", CAP_OFF),
            ("browser.cap.window", CAP_OFF),
            ("browser.cap.manage", CAP_OFF),
        ]

    login = CAP_OK if identity == IDENTITY_LOCAL else CAP_WARN
    if placement == PLACEMENT_TAB:
        window = CAP_WARN
    elif placement == PLACEMENT_PLAYER:
        window = CAP_OK if identity == IDENTITY_DEDICATED else CAP_WARN
    else:
        window = CAP_OK if identity == IDENTITY_DEDICATED else CAP_WARN

    manage = (
        CAP_OK
        if window_management_available(launch, identity, placement)
        else CAP_OFF
    )
    return [
        ("browser.cap.launch", CAP_OK),
        ("browser.cap.login", login),
        ("browser.cap.window", window),
        ("browser.cap.manage", manage),
    ]


def apply_ui_dimensions(
    *,
    launch: str,
    identity: str,
    placement: str,
    user_data_dir: str,
    per_channel_profile: bool,
    browser_path: str,
    apply_geometry: bool,
    x: int,
    y: int,
    width: int,
    height: int,
    minimized: bool,
    close_on_offline: bool,
    close_on_stop: bool,
    close_off_topic_pages: bool,
    hide_from_taskbar: bool,
) -> dict[str, Any]:
    """Build a browser_settings dict from explicit UI dimension choices."""
    enabled = launch == LAUNCH_PROGRAM
    dedicated = identity == IDENTITY_DEDICATED

    if placement == PLACEMENT_TAB:
        new_window = False
        app_mode = False
    elif placement == PLACEMENT_PLAYER:
        new_window = True
        app_mode = True
    else:
        new_window = True
        app_mode = False

    if not dedicated:
        user_data_dir = ""
        per_channel_profile = False
        minimized = False
        hide_from_taskbar = False
        close_on_offline = False
        close_on_stop = False
        close_off_topic_pages = False

    if not enabled:
        user_data_dir = ""
        per_channel_profile = False

    return {
        "enabled": enabled,
        "browser_path": browser_path or "chrome",
        "new_window": new_window,
        "app_mode": app_mode,
        "apply_geometry": apply_geometry,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "minimized": minimized if dedicated else False,
        "user_data_dir": user_data_dir.strip() if dedicated else "",
        "per_channel_profile": per_channel_profile if dedicated else False,
        "close_on_offline": close_on_offline if dedicated else False,
        "close_on_stop": close_on_stop if dedicated else False,
        "close_off_topic_pages": close_off_topic_pages if dedicated else False,
        "hide_from_taskbar": hide_from_taskbar if dedicated else False,
    }
