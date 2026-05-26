"""config.json 讀寫與預設值管理。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from stream_monitor import base_dir, i18n

DEFAULT_BROWSER_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "browser_path": "chrome",
    "new_window": True,
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
    # closes any window whose title no longer matches the channel it was
    # opened for. Catches the "redirect to a billing page" / "user clicked
    # back into the homepage" cases where the original stream URL is no
    # longer being watched. Default off so users opt-in deliberately.
    "close_off_topic_pages": False,
    # When True, the post-launch Win32 worker flips WS_EX_TOOLWINDOW on the
    # spawned browser window so it disappears from the taskbar and Alt+Tab.
    # Off by default — most users still want a taskbar slot to switch to.
    "hide_from_taskbar": False,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "channels": [],
    "check_interval": 60,
    "action": "open_and_stop",
    "monitor_mode": "trigger",
    "run_on_startup": False,
    "minimize_to_tray": True,
    "window_geometry": None,
    "language": i18n.DEFAULT_LANGUAGE,
    "browser_settings": deepcopy(DEFAULT_BROWSER_SETTINGS),
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


def _config_path():
    """Return the path to config.json next to the executable / script."""
    return base_dir() / "config.json"


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
            normalized = {"platform": platform, "name": name}
            display_name = item.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                normalized["display_name"] = display_name.strip()
            enabled = item.get("enabled")
            if isinstance(enabled, bool):
                normalized["enabled"] = enabled
            # monitor_only = True 表示「只查詢狀態，不觸發通知/開瀏覽器/關窗」。
            # 等於 enabled=True 的子模式；enabled=False 時這個欄位實質上無意義
            # 但我們仍保留它，這樣使用者從暫停切回監聽時能恢復先前的偏好。
            monitor_only = item.get("monitor_only")
            if isinstance(monitor_only, bool):
                normalized["monitor_only"] = monitor_only
            channels.append(normalized)

    return channels


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

    Used by :func:`_migrate_browser_settings` to heal the legacy combination
    ``per_channel_profile=True`` + ``user_data_dir=""``, which used to silently
    disable per-channel isolation and cause cross-window HWND contamination
    under Chrome's master process.
    """
    try:
        return str(base_dir() / "browser_profile")
    except Exception:  # noqa: BLE001 — never block config load on this.
        return ""


# Flags whose runtime implementation depends on the Win32 post-launch
# worker — and therefore on a dedicated browser profile being in use.
# Used by Migration #2 as the trigger for "user wants these to work, so
# auto-fill the profile path they forgot to set". ``apply_geometry`` is
# default-True so we deliberately exclude it; otherwise even a casual
# "I just enabled browser" user would silently get a dedicated profile
# they never asked for. Only the explicitly-opted-in flags below count.
_ISOLATION_DEPENDENT_FLAGS: tuple[str, ...] = (
    # App Mode's --app= CLI flag is silently downgraded by Chrome's master
    # process when no dedicated profile is in use (master IPC turns it into
    # a regular tab), so opting in to App Mode is, in practice, opting in
    # to the dedicated profile too. ``app_mode`` default is ``False`` so it
    # still counts as an "explicit opt-in" trigger for Migration #2.
    "app_mode",
    "hide_from_taskbar",
    "minimized",
    "close_on_offline",
    "close_on_stop",
    "close_off_topic_pages",
)


def _migrate_browser_settings(settings: dict[str, Any]) -> None:
    """In-place migration of legacy/missing-parameter browser_settings.

    Called from :func:`_normalize_browser_settings` *after* coercion so any
    cross-field invariants are evaluated on real values, not raw user input.

    Each migration step is idempotent — re-running on already-clean settings
    is a no-op, so we can run it on every load without side effects.

    Current migrations:

    1. **Orphan per-channel profile** — historically the UI let users save
       ``per_channel_profile=True`` while leaving ``user_data_dir`` blank.
       The downstream resolver returned ``""`` in that case, which made
       every channel share Chrome's master process and led to the off-topic
       prune feature closing live windows by mistake. We now populate
       ``user_data_dir`` with the default profile root so the per-channel
       sub-folder logic can actually take effect.

    2. **Opt-in advanced features without isolation** — when the user has
       explicitly enabled any of ``hide_from_taskbar`` / ``minimized`` /
       ``close_on_offline`` / ``close_on_stop`` / ``close_off_topic_pages``
       (i.e. flags whose default is ``False``), they have clearly opted
       in to runtime behaviour that only works when the launcher runs the
       Win32 post-launch worker. ``notifier._open_with_browser_settings``
       deliberately skips that worker when ``user_data_dir`` is blank, so
       leaving the field empty in this configuration makes every checked
       flag a silent no-op. We auto-fill ``user_data_dir`` with the default
       profile root and enable ``per_channel_profile`` (the only mode where
       Chrome's master-process IPC doesn't sabotage --app= / geometry on
       hot launches across multiple channels). The existing per-channel
       sub-folders from any previous run of the same install path are
       picked up transparently, preserving saved logins.
    """
    # Migration #1: orphan per-channel profile.
    if settings.get("per_channel_profile") and not (
        settings.get("user_data_dir") or ""
    ).strip():
        default_root = _default_browser_profile_path()
        if default_root:
            settings["user_data_dir"] = default_root

    # Migration #2: opt-in advanced features without isolation.
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
            # Per-channel sub-profiles are the only Chromium configuration
            # where --app= and the post-launch HWND diff survive hot
            # launches across multiple channels. Anything less and the
            # safety degradation in notifier kicks back in again, undoing
            # the migration we just performed.
            settings["per_channel_profile"] = True


def _normalize_browser_settings(value: Any) -> dict[str, Any]:
    defaults = deepcopy(DEFAULT_BROWSER_SETTINGS)
    if not isinstance(value, dict):
        # No saved settings yet — apply the migration to the freshly-cloned
        # defaults so first-launch behaviour matches a post-migration config.
        _migrate_browser_settings(defaults)
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
        raw = value.get(bool_key)
        if isinstance(raw, bool):
            normalized[bool_key] = raw

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

    _migrate_browser_settings(normalized)
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
    if isinstance(run_on_startup, bool):
        normalized["run_on_startup"] = run_on_startup

    minimize_to_tray = config.get("minimize_to_tray")
    if isinstance(minimize_to_tray, bool):
        normalized["minimize_to_tray"] = minimize_to_tray

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

    return normalized


def load() -> dict[str, Any]:
    """Load config from disk, falling back to defaults for missing keys.

    If normalization changes the on-disk content — typically because the
    config was written by an older version that is missing keys, or holds
    a legacy combination cleaned up by :func:`_migrate_browser_settings` —
    we transparently rewrite the file with the normalized form. That makes
    upgrades self-healing: the next launch sees a clean, complete config.
    """
    import logging

    logger = logging.getLogger(__name__)
    path = _config_path()
    config = deepcopy(DEFAULT_CONFIG)
    disk_existed = False
    if path.exists():
        disk_existed = True
        try:
            with path.open("r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                config.update(stored)
        except (json.JSONDecodeError, OSError):
            # Corrupt JSON or unreadable — fall through to defaults rather
            # than crashing the app at startup. The auto-rewrite below will
            # replace the bad file with a clean default.
            pass

    normalized = _normalize_config(config)

    # Self-healing: write back when the disk content disagrees with the
    # normalized form. We compare against the *raw* stored dict (with
    # defaults filled in) so missing-key migrations also trigger a rewrite.
    if disk_existed and config != normalized:
        try:
            save(normalized)
            logger.info(
                "config.json self-healed (missing keys filled in / legacy combinations migrated)"
            )
        except OSError:
            # Read-only install location or transient I/O failure — keep
            # the normalized values in memory so this session still runs
            # correctly. The next launch will retry the migration.
            logger.warning(
                "config.json self-heal write failed — running with in-memory migration only",
                exc_info=True,
            )

    return normalized


def save(config: dict[str, Any]) -> None:
    """Persist config to disk."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    normalized = _normalize_config(config)
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temp_path.replace(path)
