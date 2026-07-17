"""config.json 讀寫與預設值管理。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from stream_monitor import base_dir, default_browser_profile_dir, i18n

# Bump when persisted schema semantics change.  Older files without this key
# (or with a lower number) receive :func:`_migrate_iso_features_without_profile`
# once on load.  Save never runs that migration so explicit UI choices such as
# "local identity + app mode" are not overwritten.
CONFIG_FORMAT_VERSION = 1

DEFAULT_BROWSER_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "browser_path": "chrome",
    "new_window": False,
    "app_mode": False,
    # When False the X/Y/Width/Height fields are ignored entirely and the
    # browser decides where/how big to draw the window (system default).
    "apply_geometry": True,
    "x": 0,
    "y": 0,
    "width": 1280,
    "height": 720,
    "minimized": False,
    # Empty = use browser's default profile (subject to Chrome master-process
    # restrictions). Set to a folder path to force a dedicated Chrome /
    # Firefox profile, which is the only reliable way to make
    # --window-position / --window-size / --app= work when the browser is
    # already running.
    "user_data_dir": "",
    # When True (default) we append "<platform>_<channel>" to user_data_dir
    # so each channel gets its own browser master process. This is the only
    # reliable way to keep --app= working across multiple stream triggers,
    # because Chrome's master process drops --app= when launched against a
    # profile that already has an open window.
    "per_channel_profile": True,
    # When True, the app closes (PostMessage WM_CLOSE) the browser window we
    # opened on the going-live edge once the channel transitions back to
    # offline. Default off so users opt-in deliberately.
    "close_on_offline": False,
    # When True, every browser window this app opened is closed (WM_CLOSE)
    # when the user explicitly hits the "Stop" button. Does NOT fire on the
    # auto-stop produced by ``open_and_stop`` — that just opened a player
    # window the user wants to keep watching. Default off so existing users
    # don't lose windows after upgrading.
    "close_on_stop": False,
    # When True, the app periodically inspects each tracked HWND's title and
    # closes windows that have become obvious browser chrome (New Tab, blank
    # title, bare "Google Chrome" etc.). Does NOT close based on keyword
    # drift — stream end is handled solely by close_on_offline via the
    # monitor's went_offline event. Default off so users opt-in deliberately.
    "close_off_topic_pages": False,
    # When True, the post-launch Win32 worker flips WS_EX_TOOLWINDOW on the
    # spawned browser window so it disappears from the taskbar and Alt+Tab.
    # Off by default — most users still want a taskbar slot to switch to.
    "hide_from_taskbar": False,
}

# Twitch viewer-engagement assist (see viewer_engagement_model.py). Opt-in:
# disabled by default so existing launch behaviour is unchanged.
DEFAULT_VIEWER_ENGAGEMENT: dict[str, Any] = {
    "enabled": False,
    "force_visible": True,
    "keep_system_awake": True,
    "whitelist_performance": True,
    "bring_to_front": True,
    "foreground_hold_seconds": 15,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "config_format_version": CONFIG_FORMAT_VERSION,
    "channels": [],
    "check_interval": 60,
    "action": "open_and_stop",
    "monitor_mode": "trigger",
    "run_on_startup": False,
    "minimize_to_tray": True,
    "window_geometry": None,
    "language": i18n.DEFAULT_LANGUAGE,
    "browser_settings": deepcopy(DEFAULT_BROWSER_SETTINGS),
    "viewer_engagement": deepcopy(DEFAULT_VIEWER_ENGAGEMENT),
    # Last-known per-channel status, written on quit / tray-hide and restored
    # on the next launch so rows repaint immediately (see status_cache.py).
    "channel_status_cache": {},
}

ACTION_KEYS: set[str] = {
    "open_and_stop",
    "open_and_keep",
    "notify_only",
    "open_and_exit",
}
MONITOR_MODE_KEYS = {"trigger", "watch"}
PLATFORM_KEYS = {"twitch", "youtube"}
MIN_CHECK_INTERVAL = 10

_BOOL_TRUTHY = frozenset({"true", "yes", "1", "on"})
_BOOL_FALSY = frozenset({"false", "no", "0", "off", ""})


def _config_path():
    """Return the path to config.json next to the executable / script."""
    return base_dir() / "config.json"


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce legacy JSON bool representations; unknown shapes keep *default*."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUTHY:
            return True
        if lowered in _BOOL_FALSY:
            return False
    return default


def _normalize_channels(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    channels: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        platform = item.get("platform")
        name = item.get("name")
        if not isinstance(platform, str) or not isinstance(name, str):
            continue

        platform = platform.lower().strip()
        name = name.strip()
        if platform in PLATFORM_KEYS and name:
            normalized: dict[str, str | bool] = {"platform": platform, "name": name}
            display_name = item.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                normalized["display_name"] = display_name.strip()
            enabled = _coerce_bool(item.get("enabled"), False)
            if "enabled" in item:
                normalized["enabled"] = enabled
            # monitor_only = True 表示「只查詢狀態，不觸發通知/開瀏覽器/關窗」。
            # 等於 enabled=True 的子模式；enabled=False 時這個欄位實質上無意義
            # 但我們仍保留它，這樣使用者從暫停切回監聽時能恢復先前的偏好。
            monitor_only = _coerce_bool(item.get("monitor_only"), False)
            if "monitor_only" in item:
                normalized["monitor_only"] = monitor_only
            channels.append(normalized)  # type: ignore[arg-type]

    return channels  # type: ignore[return-value]


def _normalize_interval(value: Any) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["check_interval"]
    return max(MIN_CHECK_INTERVAL, interval)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _default_browser_profile_path() -> str:
    """Return the default per-app browser profile root (``<base_dir>/browser_profile``).

    Used by :func:`_heal_orphan_per_channel_profile` to heal the legacy combination
    ``per_channel_profile=True`` + ``user_data_dir=""``, which used to silently
    disable per-channel isolation and cause cross-window HWND contamination
    under Chrome's master process.
    """
    return default_browser_profile_dir()


# Flags whose runtime implementation depends on the Win32 post-launch
# worker — and therefore on a dedicated browser profile being in use.
# Used by legacy Migration #2 as the trigger for "user wants these to work, so
# auto-fill the profile path they forgot to set". ``apply_geometry`` is
# default-True so we deliberately exclude it; otherwise even a casual
# "I just enabled browser" user would silently get a dedicated profile
# they never asked for. ``app_mode`` is also excluded: the UI allows
# "local identity + player (app mode)" as an explicit, valid choice and
# must not be upgraded to a dedicated profile on save.
_ISOLATION_DEPENDENT_FLAGS: tuple[str, ...] = (
    "hide_from_taskbar",
    "minimized",
    "close_on_offline",
    "close_on_stop",
    "close_off_topic_pages",
)


def _heal_orphan_per_channel_profile(settings: dict[str, Any]) -> None:
    """Fill ``user_data_dir`` when ``per_channel_profile`` is on but path is blank."""
    if settings.get("per_channel_profile") and not (
        settings.get("user_data_dir") or ""
    ).strip():
        default_root = _default_browser_profile_path()
        if default_root:
            settings["user_data_dir"] = default_root


def _migrate_iso_features_without_profile(settings: dict[str, Any]) -> None:
    """Legacy load-time migration for advanced flags without a profile path."""
    iso_features_requested = any(
        bool(settings.get(flag)) for flag in _ISOLATION_DEPENDENT_FLAGS
    )
    if (
        settings.get("enabled")
        and iso_features_requested
        and not (settings.get("user_data_dir") or "").strip()
    ):
        default_root = _default_browser_profile_path()
        if default_root:
            settings["user_data_dir"] = default_root
            settings["per_channel_profile"] = True


def _migrate_browser_settings(settings: dict[str, Any]) -> None:
    """Apply all legacy browser_settings migrations (load-time helper / tests).

    Production ``save()`` does **not** call this — only orphan-profile healing
    runs during normalisation so explicit UI opt-outs are preserved.
    """
    _heal_orphan_per_channel_profile(settings)
    _migrate_iso_features_without_profile(settings)


def _normalize_browser_settings(value: Any) -> dict[str, Any]:
    defaults = deepcopy(DEFAULT_BROWSER_SETTINGS)
    if not isinstance(value, dict):
        _heal_orphan_per_channel_profile(defaults)
        return defaults

    normalized = deepcopy(defaults)

    for bool_key in (
        "enabled",
        "new_window",
        "app_mode",
        "apply_geometry",
        "minimized",
        "per_channel_profile",
        "close_on_offline",
        "close_on_stop",
        "close_off_topic_pages",
        "hide_from_taskbar",
    ):
        if bool_key in value:
            normalized[bool_key] = _coerce_bool(value.get(bool_key), normalized[bool_key])

    browser_path = value.get("browser_path")
    if isinstance(browser_path, str) and browser_path.strip():
        normalized["browser_path"] = browser_path.strip()

    user_data_dir = value.get("user_data_dir")
    if isinstance(user_data_dir, str):
        normalized["user_data_dir"] = user_data_dir.strip()

    for int_key in ("x", "y", "width", "height"):
        normalized[int_key] = _coerce_int(value.get(int_key), normalized[int_key])

    normalized["width"] = max(100, normalized["width"])
    normalized["height"] = max(100, normalized["height"])

    _heal_orphan_per_channel_profile(normalized)
    return normalized


_VIEWER_ENGAGEMENT_INT_KEYS = ("foreground_hold_seconds",)


def _normalize_viewer_engagement(value: Any) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_VIEWER_ENGAGEMENT)
    if isinstance(value, dict):
        for key in normalized:
            if key not in value:
                continue
            if key in _VIEWER_ENGAGEMENT_INT_KEYS:
                normalized[key] = _coerce_int(value[key], normalized[key])
            else:
                normalized[key] = _coerce_bool(value[key], normalized[key])
    return normalized


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(DEFAULT_CONFIG)
    normalized["channels"] = _normalize_channels(config.get("channels"))
    normalized["check_interval"] = _normalize_interval(config.get("check_interval"))

    action = config.get("action")
    if isinstance(action, str) and action in ACTION_KEYS:
        normalized["action"] = action

    monitor_mode = config.get("monitor_mode")
    if isinstance(monitor_mode, str) and monitor_mode in MONITOR_MODE_KEYS:
        normalized["monitor_mode"] = monitor_mode

    run_on_startup = config.get("run_on_startup")
    if "run_on_startup" in config:
        normalized["run_on_startup"] = _coerce_bool(
            run_on_startup, normalized["run_on_startup"]
        )

    minimize_to_tray = config.get("minimize_to_tray")
    if "minimize_to_tray" in config:
        normalized["minimize_to_tray"] = _coerce_bool(
            minimize_to_tray, normalized["minimize_to_tray"]
        )

    window_geometry = config.get("window_geometry")
    if isinstance(window_geometry, str) and window_geometry.strip():
        normalized["window_geometry"] = window_geometry

    language = config.get("language")
    if isinstance(language, str) and language in i18n.LANGUAGE_CODES:
        normalized["language"] = language
    else:
        normalized["language"] = i18n.DEFAULT_LANGUAGE

    normalized["browser_settings"] = _normalize_browser_settings(
        config.get("browser_settings")
    )
    normalized["viewer_engagement"] = _normalize_viewer_engagement(
        config.get("viewer_engagement")
    )

    status_cache = config.get("channel_status_cache")
    if isinstance(status_cache, dict):
        normalized["channel_status_cache"] = status_cache

    return normalized


def _stored_format_version(stored: dict[str, Any]) -> int:
    raw = stored.get("config_format_version", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _finalize_config(
    config: dict[str, Any], *, apply_legacy_migration: bool
) -> dict[str, Any]:
    """Normalise *config* and stamp the current format version."""
    normalized = _normalize_config(config)
    if apply_legacy_migration:
        _migrate_iso_features_without_profile(normalized["browser_settings"])
    normalized["config_format_version"] = CONFIG_FORMAT_VERSION
    return normalized


def _needs_self_heal(stored: dict[str, Any], finalized: dict[str, Any]) -> bool:
    """Return True when on-disk JSON should be rewritten to *finalized*."""
    if _stored_format_version(stored) < CONFIG_FORMAT_VERSION:
        return True
    on_disk_final = _finalize_config(
        {**DEFAULT_CONFIG, **stored},
        apply_legacy_migration=False,
    )
    return on_disk_final != finalized


def load() -> dict[str, Any]:
    """Load config from disk, falling back to defaults for missing keys.

    If normalization or legacy migration changes the on-disk content, we
    transparently rewrite the file with the canonical form.
    """
    import logging

    logger = logging.getLogger(__name__)
    path = _config_path()
    stored: dict[str, Any] = {}
    disk_existed = False
    if path.exists():
        disk_existed = True
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                stored = raw
        except (json.JSONDecodeError, OSError):
            pass

    merged = deepcopy(DEFAULT_CONFIG)
    merged.update(stored)
    apply_legacy = _stored_format_version(stored) < CONFIG_FORMAT_VERSION
    finalized = _finalize_config(merged, apply_legacy_migration=apply_legacy)

    if disk_existed and _needs_self_heal(stored, finalized):
        try:
            save(finalized)
            logger.info(
                "config.json self-healed (schema normalised / legacy migration applied)"
            )
        except OSError:
            logger.warning(
                "config.json self-heal write failed — running with in-memory migration only",
                exc_info=True,
            )

    return finalized


def save(config: dict[str, Any]) -> dict[str, Any]:
    """Persist config to disk and return the canonical in-memory form."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    normalized = _finalize_config(config, apply_legacy_migration=False)
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp_path.replace(path)
    return normalized

