"""開機自啟動 — Windows 透過 Registry Run key，Linux 透過 XDG Autostart。"""

from __future__ import annotations

import logging
import os
import shlex
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


def _parse_command_exe(command: str) -> str | None:
    """Extract the executable path from a Run key value / Exec line.

    Handles a quoted path followed by arguments, and an unquoted path
    (possibly containing spaces) ending in ``.exe``.
    """
    command = command.strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end > 1 else command[1:] or None
    idx = command.lower().find(".exe")
    if idx != -1:
        return command[: idx + 4]
    return command.split(None, 1)[0]


def _read_startup_exe() -> str | None:
    """Return the exe path the current autostart entry points at."""
    if _IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ
            ) as key:
                val, _ = winreg.QueryValueEx(key, _APP_NAME)
        except OSError:
            return None
        return _parse_command_exe(str(val))

    try:
        for line in _autostart_path().read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                parts = shlex.split(line[len("Exec="):])
                return parts[0] if parts else None
    except (OSError, ValueError):
        return None
    return None


def _same_path(a: str, b: str) -> bool:
    norm_a = os.path.normpath(a)
    norm_b = os.path.normpath(b)
    if _IS_WINDOWS:
        return norm_a.lower() == norm_b.lower()
    return norm_a == norm_b


def heal_startup_command_if_enabled(
    exe_path: str | None = None, *, keep_existing_other_exe: bool = False
) -> bool:
    """Rewrite the autostart entry to the current executable when enabled.

    Versioned install folders rename the .exe on each release; an outdated
    Run key / .desktop Exec then fails at login (looks like a flash-quit).

    With ``keep_existing_other_exe`` (used by the automatic startup sync), an
    entry pointing at a *different* exe that still exists is left alone, so
    running a second copy (e.g. a dev build) cannot hijack the autostart.
    """
    if not is_startup_enabled():
        return False
    if keep_existing_other_exe:
        current = exe_path or (
            sys.executable if getattr(sys, "frozen", False) else None
        )
        registered = _read_startup_exe()
        if (
            current
            and registered
            and not _same_path(registered, current)
            and os.path.exists(registered)
        ):
            logger.info(
                "Startup entry points at another existing exe; skipping rewrite "
                "(registered=%s, current=%s)",
                registered,
                current,
            )
            return False
    return enable_startup(exe_path=exe_path)


def disable_startup() -> bool:
    if _IS_WINDOWS:
        return _disable_startup_windows()
    return _disable_startup_linux()
