"""Map browser_settings config to four UI dimensions (A/B/C/D)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
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


@dataclass
class BrowserSettings:
    """Typed browser launch settings (DIP — replaces raw dict at boundaries)."""

    enabled: bool = False
    browser_path: str = "chrome"
    new_window: bool = False
    app_mode: bool = False
    apply_geometry: bool = True
    x: int = 0
    y: int = 0
    width: int = 1280
    height: int = 720
    minimized: bool = False
    user_data_dir: str = ""
    per_channel_profile: bool = True
    close_on_offline: bool = False
    close_on_stop: bool = False
    close_off_topic_pages: bool = False
    hide_from_taskbar: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BrowserSettings:
        if not raw:
            return cls()
        kwargs: dict[str, Any] = {}
        valid = {field.name for field in fields(cls)}
        for key in valid:
            if key in raw:
                kwargs[key] = raw[key]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for gradual migration from ``dict`` settings."""
        return getattr(self, key, default)


def coerce_browser_settings(
    settings: BrowserSettings | dict[str, Any] | None,
) -> BrowserSettings | None:
    if settings is None:
        return None
    if isinstance(settings, BrowserSettings):
        return settings
    return BrowserSettings.from_dict(settings)


def _as_mapping(settings: dict[str, Any] | BrowserSettings) -> dict[str, Any]:
    if isinstance(settings, BrowserSettings):
        return settings.to_dict()
    return settings


def infer_launch_mode(settings: dict[str, Any] | BrowserSettings) -> str:
    mapping = _as_mapping(settings)
    return LAUNCH_PROGRAM if mapping.get("enabled") else LAUNCH_SYSTEM


def infer_identity_mode(settings: dict[str, Any] | BrowserSettings) -> str:
    mapping = _as_mapping(settings)
    if (mapping.get("user_data_dir") or "").strip():
        return IDENTITY_DEDICATED
    return IDENTITY_LOCAL


def infer_placement_mode(settings: dict[str, Any] | BrowserSettings) -> str:
    mapping = _as_mapping(settings)
    if bool(mapping.get("app_mode")):
        return PLACEMENT_PLAYER
    if not bool(mapping.get("new_window", False)):
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

    return BrowserSettings(
        enabled=enabled,
        browser_path=browser_path or "chrome",
        new_window=new_window,
        app_mode=app_mode,
        apply_geometry=apply_geometry,
        x=x,
        y=y,
        width=width,
        height=height,
        minimized=minimized if dedicated else False,
        user_data_dir=user_data_dir.strip() if dedicated else "",
        per_channel_profile=per_channel_profile if dedicated else False,
        close_on_offline=close_on_offline if dedicated else False,
        close_on_stop=close_on_stop if dedicated else False,
        close_off_topic_pages=close_off_topic_pages if dedicated else False,
        hide_from_taskbar=hide_from_taskbar if dedicated else False,
    ).to_dict()
