"""Windows 開機自啟動 — 透過 winreg 管理 Registry Run key。"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

_APP_NAME = "StreamMonitor"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _get_startup_command(exe_path: str | None = None) -> str | None:
    """Build the registry Run command for the packaged executable."""
    if exe_path is not None:
        return subprocess.list2cmdline([exe_path, "--silent"])

    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--silent"])

    return None


def is_startup_enabled() -> bool:
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


def enable_startup(exe_path: str | None = None) -> bool:
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


def disable_startup() -> bool:
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
