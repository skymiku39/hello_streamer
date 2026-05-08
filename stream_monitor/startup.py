"""開機自啟動 — Windows 透過 Registry Run key，Linux 透過 XDG Autostart。"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "StreamMonitor"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

_DESKTOP_ENTRY_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=Hello Streamer
Comment=Twitch/YouTube stream monitor
Exec={cmd}
Hidden=false
X-GNOME-Autostart-enabled=true
Terminal=false
"""


def _autostart_path() -> Path:
    """Return the XDG autostart .desktop file path for Linux."""
    xdg_config = Path.home() / ".config" / "autostart"
    return xdg_config / "stream-monitor.desktop"


def _get_startup_command(exe_path: str | None = None) -> str | None:
    """Build the startup command string for the packaged executable."""
    if exe_path is not None:
        return subprocess.list2cmdline([exe_path, "--silent"])

    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--silent"])

    return None


# ---------------------------------------------------------------------------
# Linux (XDG Autostart)
# ---------------------------------------------------------------------------


def _is_startup_enabled_linux() -> bool:
    return _autostart_path().is_file()


def _enable_startup_linux(exe_path: str | None = None) -> bool:
    command = _get_startup_command(exe_path)
    if command is None:
        logger.warning("Startup can only be enabled for a packaged executable")
        return False

    desktop_path = _autostart_path()
    try:
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(
            _DESKTOP_ENTRY_TEMPLATE.format(cmd=command), encoding="utf-8"
        )
        logger.info("Startup enabled (XDG): %s", desktop_path)
        return True
    except OSError:
        logger.exception("Failed to write autostart desktop entry")
        return False


def _disable_startup_linux() -> bool:
    desktop_path = _autostart_path()
    try:
        desktop_path.unlink(missing_ok=True)
        logger.info("Startup disabled (XDG)")
        return True
    except OSError:
        logger.exception("Failed to remove autostart desktop entry")
        return False


# ---------------------------------------------------------------------------
# Windows (Registry Run key)
# ---------------------------------------------------------------------------


def _is_startup_enabled_windows() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("Failed to read startup registry key")
        return False


def _enable_startup_windows(exe_path: str | None = None) -> bool:
    command = _get_startup_command(exe_path)
    if command is None:
        logger.warning("Startup can only be enabled for a packaged executable")
        return False

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, command)
        logger.info("Startup enabled: %s", command)
        return True
    except OSError:
        logger.exception("Failed to enable startup")
        return False


def _disable_startup_windows() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
        logger.info("Startup disabled")
        return True
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("Failed to disable startup")
        return False


# ---------------------------------------------------------------------------
# Public API — dispatches by platform
# ---------------------------------------------------------------------------

_IS_WINDOWS = sys.platform == "win32"


def is_startup_enabled() -> bool:
    if _IS_WINDOWS:
        return _is_startup_enabled_windows()
    return _is_startup_enabled_linux()


def enable_startup(exe_path: str | None = None) -> bool:
    if _IS_WINDOWS:
        return _enable_startup_windows(exe_path)
    return _enable_startup_linux(exe_path)


def disable_startup() -> bool:
    if _IS_WINDOWS:
        return _disable_startup_windows()
    return _disable_startup_linux()
