"""觸發行為 — 開播偵測後的四種動作 + 桌面通知（Windows Toast / Linux notify-send）。"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo

logger = logging.getLogger(__name__)

ActionCallback = Callable[[], None]

# Win32 window class names used by mainstream browsers.
# Chromium-family (Chrome, Edge, Brave, Vivaldi, Opera, Arc…) all share the
# Chrome_WidgetWin_1 class because they're built on the same Aura toolkit.
_WIN32_WINDOW_CLASS_BY_FAMILY = {
    "chromium": "Chrome_WidgetWin_1",
    "firefox": "MozillaWindowClass",
}

_SW_RESTORE = 9          # un-maximise / un-minimise before repositioning
_SW_SHOWMINNOACTIVE = 7  # minimize without taking focus
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_MINIMIZE_POLL_INTERVAL_S = 0.15
_MINIMIZE_DEADLINE_S = 8.0  # generous: Chrome cold-start can take 2-3s


def _is_windows() -> bool:
    """Thin wrapper so tests can pretend to be on/off Windows without
    monkeypatching ``os.name`` globally (which corrupts pytest's pathlib)."""
    return os.name == "nt"


_WINDOWS_BROWSER_ALIASES = {
    "chrome": "chrome",
    "chrome.exe": "chrome",
    "google chrome": "chrome",
    "msedge": "msedge",
    "msedge.exe": "msedge",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "firefox.exe": "firefox",
    "mozilla firefox": "firefox",
}

_WINDOWS_BROWSER_PATHS = {
    "chrome": (
        ("ProgramFiles", "Google", "Chrome", "Application", "chrome.exe"),
        ("ProgramFiles(x86)", "Google", "Chrome", "Application", "chrome.exe"),
        ("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
    ),
    "msedge": (
        ("ProgramFiles", "Microsoft", "Edge", "Application", "msedge.exe"),
        ("ProgramFiles(x86)", "Microsoft", "Edge", "Application", "msedge.exe"),
        ("LOCALAPPDATA", "Microsoft", "Edge", "Application", "msedge.exe"),
    ),
    "firefox": (
        ("ProgramFiles", "Mozilla Firefox", "firefox.exe"),
        ("ProgramFiles(x86)", "Mozilla Firefox", "firefox.exe"),
        ("LOCALAPPDATA", "Mozilla Firefox", "firefox.exe"),
    ),
}


def _clean_browser_path(value: str) -> str:
    """Trim whitespace and one layer of quotes from a browser executable path."""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        return cleaned[1:-1].strip()
    return cleaned


def _windows_browser_candidates(alias: str) -> list[Path]:
    candidates: list[Path] = []
    for env_key, *parts in _WINDOWS_BROWSER_PATHS.get(alias, ()):
        base = os.environ.get(env_key)
        if base:
            candidates.append(Path(base, *parts))
    return candidates


def _resolve_browser_executable(browser_path: str) -> str:
    """Resolve friendly browser names like ``chrome`` to executable paths."""
    cleaned = _clean_browser_path(browser_path)
    expanded = os.path.expandvars(os.path.expanduser(cleaned))

    explicit_path = Path(expanded)
    if explicit_path.is_file():
        return str(explicit_path)

    path_match = shutil.which(expanded)
    if path_match:
        return path_match

    if platform.system() == "Windows":
        alias = _WINDOWS_BROWSER_ALIASES.get(expanded.lower())
        if alias:
            for candidate in _windows_browser_candidates(alias):
                if candidate.is_file():
                    return str(candidate)

    return expanded


def detect_browser_family(executable: str) -> str:
    """Classify the resolved executable as ``chromium`` / ``firefox`` / ``unknown``.

    Only the basename is inspected, so absolute paths and bare aliases both work.
    """
    base = Path(executable).name.lower()
    if not base:
        base = executable.lower()
    if base in {"chrome", "chrome.exe", "msedge", "msedge.exe", "chromium", "chromium.exe", "brave", "brave.exe"}:
        return "chromium"
    if "chrome" in base or "edge" in base or "chromium" in base or "brave" in base:
        return "chromium"
    if base in {"firefox", "firefox.exe"} or "firefox" in base:
        return "firefox"
    return "unknown"


# ---------------------------------------------------------------------------
# Win32 window-after-launch helpers
#
# Background: Chrome / Edge run a long-lived "master" process. When you spawn
# chrome.exe a second time, it just IPCs the request to the existing master,
# which is the process that actually creates the visible HWND. That means
# neither STARTUPINFO/wShowWindow nor Chromium's window-position / window-size
# flags reliably affect the new window. The robust workaround is to diff the
# top-level window list before vs. after spawning, then call Win32 APIs on
# whatever new windows appear.
# ---------------------------------------------------------------------------
def _enum_browser_hwnds(class_name: str) -> set[int]:
    """Return all visible, top-level Win32 HWNDs whose class equals *class_name*.

    Returns an empty set on non-Windows platforms or if ctypes calls fail —
    callers should treat an empty baseline as "give up, no Win32 minimize".
    """
    if not _is_windows():
        return set()

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return set()

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return set()

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    hwnds: set[int] = set()
    class_buf = ctypes.create_unicode_buffer(256)

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            # Skip hidden helper / utility surfaces that share the same class
            return True
        user32.GetClassNameW(hwnd, class_buf, 256)
        if class_buf.value == class_name:
            hwnds.add(int(hwnd))
        return True

    try:
        user32.EnumWindows(enum_proc(_callback), 0)
    except OSError:
        logger.exception("EnumWindows call failed")
        return set()

    return hwnds


def _configure_user32_signatures(user32: Any) -> None:
    """Declare explicit argtypes/restype on user32 calls we use.

    Without this, ctypes defaults treat every arg as ``c_int`` (32-bit). On
    64-bit Windows ``HWND`` is pointer-sized, so a raw Python int handle can
    be silently truncated mid-call — that's a major reason why post-launch
    Win32 fix-ups appear to "do nothing" intermittently.
    """
    import ctypes
    from ctypes import wintypes

    if getattr(user32, "_hello_streamer_signed", False):
        return

    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int

    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int

    user32._hello_streamer_signed = True  # type: ignore[attr-defined]


def _apply_new_browser_window_settings_async(
    class_name: str,
    baseline: set[int],
    settings: dict[str, Any],
    *,
    apply_geometry: bool = True,
    deadline_s: float = _MINIMIZE_DEADLINE_S,
) -> threading.Thread | None:
    """Spawn a daemon thread that configures any *new* HWND of *class_name*.

    The thread keeps polling ``EnumWindows`` until either:
      - it has managed at least one new window, or
      - ``deadline_s`` seconds elapsed (browser may take a moment to launch).

    Returns the started thread (or ``None`` when not applicable) so tests can
    join on it deterministically.
    """
    if not _is_windows() or not class_name:
        return None

    try:
        import ctypes
    except ImportError:
        return None

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return None

    try:
        _configure_user32_signatures(user32)
    except Exception:
        # Signature setup is best-effort; if it fails (e.g. mocked user32 in
        # tests) we still attempt the calls without strict typing.
        logger.debug("Could not configure user32 signatures", exc_info=True)

    x = int(settings.get("x", 0) or 0)
    y = int(settings.get("y", 0) or 0)
    width = max(100, int(settings.get("width", 1280) or 1280))
    height = max(100, int(settings.get("height", 720) or 720))
    minimized = bool(settings.get("minimized"))
    set_window_pos_flags = _SWP_NOZORDER | _SWP_NOACTIVATE

    def _worker() -> None:
        deadline = time.monotonic() + deadline_s
        managed: set[int] = set()
        while time.monotonic() < deadline:
            current = _enum_browser_hwnds(class_name)
            new_hwnds = current - baseline - managed
            for hwnd in new_hwnds:
                try:
                    if apply_geometry:
                        # Chrome may open the new window in a "maximised" state,
                        # in which case SetWindowPos updates the *restored*
                        # geometry but the user sees no visible change. Force
                        # a restore first so our X/Y/W/H actually take effect
                        # on screen.
                        user32.ShowWindow(hwnd, _SW_RESTORE)
                        user32.SetWindowPos(
                            hwnd,
                            0,
                            x,
                            y,
                            width,
                            height,
                            set_window_pos_flags,
                        )
                    if minimized:
                        user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)
                    managed.add(hwnd)
                    logger.debug(
                        "Managed new browser window HWND=%s geometry=%s minimized=%s",
                        hwnd,
                        (x, y, width, height) if apply_geometry else None,
                        minimized,
                    )
                except OSError:
                    logger.exception("Window management failed for HWND=%s", hwnd)
            if managed:
                # First batch of new windows is almost always our target;
                # keep going a little longer in case the browser opens
                # additional satellite windows that should also be managed.
                if time.monotonic() > deadline - (_MINIMIZE_DEADLINE_S / 2):
                    return
            time.sleep(_MINIMIZE_POLL_INTERVAL_S)

        if not managed:
            logger.warning(
                "Did not detect a new %s window to manage within %.1fs; "
                "the browser may have been launched into an existing window. "
                "Tip: set browser_settings.user_data_dir to a folder path to "
                "force a dedicated profile (and a real new window).",
                class_name,
                deadline_s,
            )

    thread = threading.Thread(target=_worker, daemon=True, name="browser-window-manager")
    thread.start()
    return thread


def _minimize_new_browser_windows_async(
    class_name: str,
    baseline: set[int],
    deadline_s: float = _MINIMIZE_DEADLINE_S,
) -> threading.Thread | None:
    """Compatibility wrapper for tests and older private callers."""
    return _apply_new_browser_window_settings_async(
        class_name,
        baseline,
        {"minimized": True},
        apply_geometry=False,
        deadline_s=deadline_s,
    )


def _wants_startup_only_flags(
    *, app_mode: bool, x: int, y: int, width: int, height: int
) -> bool:
    """Return True when *any* startup-only browser flag is requested.

    These flags are silently dropped by Chrome/Edge's master process if a
    browser instance is already running. ``user_data_dir`` is the only
    reliable escape hatch.
    """
    if app_mode:
        return True
    if x or y:
        return True
    if width != 1280 or height != 720:
        return True
    return False


def _build_browser_args(url: str, settings: dict[str, Any]) -> list[str]:
    """Compose browser CLI arguments from a normalized browser_settings dict.

    Branches by browser family so we never feed Firefox a Chromium-only flag
    (or vice versa). Also collapses logically redundant flags (e.g. ``--app``
    already implies a new window).
    """
    browser_path = (settings.get("browser_path") or "chrome").strip() or "chrome"
    executable = _resolve_browser_executable(browser_path)
    family = detect_browser_family(executable)

    new_window = bool(settings.get("new_window", True))
    app_mode = bool(settings.get("app_mode", False))
    width = int(settings.get("width", 1280) or 1280)
    height = int(settings.get("height", 720) or 720)
    x = int(settings.get("x", 0) or 0)
    y = int(settings.get("y", 0) or 0)
    user_data_dir = (settings.get("user_data_dir") or "").strip()

    args: list[str] = [executable]

    if family == "firefox":
        # Firefox CLI subset: only --new-window URL is honoured. Position,
        # size and app-mode flags are not command-line options. On Windows we
        # apply size/position after launch via Win32; elsewhere those geometry
        # settings are ignored.
        dropped: list[str] = []
        if app_mode:
            dropped.append("app_mode")
        can_apply_geometry_after_launch = _is_windows() and (new_window or app_mode)
        if (x or y) and not can_apply_geometry_after_launch:
            dropped.append("window position")
        if (width != 1280 or height != 720) and not can_apply_geometry_after_launch:
            dropped.append("window size")
        if dropped:
            logger.warning(
                "Firefox does not support these browser_settings flags: %s — falling back to a plain new window",
                ", ".join(dropped),
            )
        if user_data_dir:
            # ``-no-remote`` ensures Firefox spawns a separate process for this
            # profile instead of forwarding the request to an existing instance.
            args.extend(["-profile", user_data_dir, "-no-remote"])
        if new_window or app_mode:
            args.append("--new-window")
        args.append(url)
        return args

    # Chromium-family branch (chrome, msedge, brave, edge, chromium, …) — and
    # also the "unknown" branch, which we treat best-effort as Chromium so the
    # legacy behaviour is preserved when a user points at a non-standard exe.
    #
    # IMPORTANT: --window-position / --window-size / --app= are all
    # "startup flags" — Chrome's master process IPC ignores them if an
    # instance is already running. ``--user-data-dir=<path>`` forces a fresh
    # master process per profile path, which makes those flags effective.
    # When the user hasn't configured one, we still emit the flags (they help
    # in the cold-start case) and rely on the Win32 post-launch fix-up.
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")
    elif _wants_startup_only_flags(
        app_mode=app_mode, x=x, y=y, width=width, height=height
    ):
        logger.warning(
            "Chrome/Edge: --app= / --window-position / --window-size are "
            "ignored when the browser is already running. Set "
            "browser_settings.user_data_dir to a folder path to force a "
            "dedicated profile (App Mode in particular requires this)."
        )

    # Note: minimisation is NOT a CLI flag for Chromium (--start-minimized
    # doesn't actually exist as a switch). We handle it post-launch via
    # Win32 ShowWindow inside _open_with_browser_settings.
    if app_mode:
        # --app= already opens its own dedicated window; --new-window would
        # just be redundant noise (and slightly confusing in logs).
        args.append(f"--window-position={x},{y}")
        args.append(f"--window-size={width},{height}")
        args.append(f"--app={url}")
        return args

    if new_window:
        args.append("--new-window")
        args.append(f"--window-position={x},{y}")
        args.append(f"--window-size={width},{height}")
    else:
        logger.warning(
            "browser_settings.new_window is False and app_mode is False — "
            "x/y/width/height/minimized will be ignored by the browser "
            "(URL will open as a tab in an existing window)"
        )

    args.append(url)
    return args


def _open_with_browser_settings(url: str, settings: dict[str, Any]) -> bool:
    """Spawn the configured browser with CLI args.

    Returns True if ``Popen`` succeeded. On Windows, we capture the current set
    of top-level browser windows *before* spawning, then start a background
    thread that applies position, size, and minimised state to any new windows
    that appear afterwards. This is more reliable than browser CLI flags alone,
    because Chrome/Edge route requests through a long-lived master process.
    """
    args = _build_browser_args(url, settings)
    family = detect_browser_family(args[0])

    # When the user pointed us at a dedicated profile path, make sure the
    # folder exists *before* Popen — Chrome will happily create it itself
    # but failing fast here gives much nicer error messages than a silent
    # browser launch failure.
    user_data_dir = (settings.get("user_data_dir") or "").strip()
    if user_data_dir:
        try:
            Path(os.path.expandvars(os.path.expanduser(user_data_dir))).mkdir(
                parents=True, exist_ok=True
            )
        except OSError:
            logger.exception(
                "Could not create browser user_data_dir: %s", user_data_dir
            )

    want_minimize = bool(settings.get("minimized")) and _is_windows()
    new_window_expected = bool(settings.get("app_mode")) or bool(
        settings.get("new_window", True)
    )
    want_window_management = _is_windows() and new_window_expected
    class_name = (
        _WIN32_WINDOW_CLASS_BY_FAMILY.get(family) if want_window_management else None
    )

    # Chromium covers chrome/edge/brave/etc; treat "unknown" as Chromium too
    # because most custom Chromium-derived browsers share the same class.
    if want_window_management and class_name is None and family == "unknown":
        class_name = _WIN32_WINDOW_CLASS_BY_FAMILY["chromium"]

    baseline: set[int] = set()
    if class_name:
        baseline = _enum_browser_hwnds(class_name)

    # Belt-and-suspenders: STARTUPINFO still helps in the (rare) cold-start
    # case where no browser master process is yet running, e.g. first launch
    # after a reboot. Harmless when the master process already exists.
    startupinfo = None
    if _is_windows() and want_minimize:
        try:
            startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            startupinfo.wShowWindow = _SW_SHOWMINNOACTIVE
        except AttributeError:
            startupinfo = None

    try:
        subprocess.Popen(
            args,
            startupinfo=startupinfo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning(
            "Browser executable not found: %s — falling back to default browser",
            args[0],
        )
        return False
    except OSError:
        logger.exception("Failed to spawn browser with custom settings: %s", args)
        return False

    if class_name:
        _apply_new_browser_window_settings_async(
            class_name,
            baseline,
            settings,
            apply_geometry=True,
        )

    return True


def open_url(url: str, browser_settings: dict[str, Any] | None = None) -> bool:
    """Open *url* in the user's browser.

    If *browser_settings* is provided and ``enabled`` is true, the URL is
    launched via ``subprocess`` so we can control window position, size and
    minimised state (Chrome/Edge CLI flags). Otherwise we fall back to the
    standard ``webbrowser`` module with a Windows shell fallback.
    """
    if not url:
        logger.warning("Cannot open empty URL")
        return False

    if browser_settings and browser_settings.get("enabled"):
        if _open_with_browser_settings(url, browser_settings):
            return True

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
    else:
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            logger.warning("xdg-open not found; cannot open URL: %s", url)
        except OSError:
            logger.exception("Failed to open URL with xdg-open: %s", url)

    return False


def _format_scheduled_start(iso_str: str) -> str:
    """Convert ISO 8601 timestamp to a human-readable local time string."""
    if not iso_str:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_str)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def action_for_stream_status(configured_action: str, info: StreamInfo) -> str | None:
    """Return the action that should run for a stream/video event."""
    status = info.stream_status or "live"
    if status == "upcoming":
        return "notify_only"
    if status == "video":
        return None
    return configured_action


def _build_toast_text(info: StreamInfo) -> tuple[str, str]:
    """Return (title, body) for a notification based on stream status."""
    channel_name = info.display_name or info.channel
    platform_display = info.platform.upper()
    status = info.stream_status or "live"

    if status == "upcoming":
        title = f"\U0001f4c5 {channel_name} 已建立待機室 [{platform_display}]"
        time_str = _format_scheduled_start(info.scheduled_start)
        body = f"預計開播：{time_str}" if time_str else (info.title or "即將開播")
    elif status == "video":
        title = f"\U0001f3ac {channel_name} 上傳了新影片 [{platform_display}]"
        body = info.title or "新影片"
    else:
        title = f"\U0001f534 {channel_name} 開播了！ [{platform_display}]"
        body = info.title or f"{channel_name} is now live on {info.platform}"

    return title, body


def _toast_windows(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a rich Windows Toast notification via winotify."""
    try:
        from winotify import Notification

        title, body = _build_toast_text(info)

        toast = Notification(
            app_id="哈嘍主播 Hello Streamer",
            title=title,
            msg=body,
            duration="long",
            icon="",
        )
        toast.set_audio("ms-winsoundevent:Notification.Default", loop=False)

        if with_open_button:
            toast.add_actions(label="立即觀看", launch=info.url)

        toast.show()
    except Exception:
        logger.exception("Failed to show Windows toast notification")


def _toast_linux(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a desktop notification via notify-send (Linux)."""
    try:
        title, body = _build_toast_text(info)

        cmd = ["notify-send", "--app-name=Hello Streamer", title, body]
        subprocess.run(cmd, check=False, timeout=5)
    except FileNotFoundError:
        logger.warning("notify-send not found; desktop notifications unavailable")
    except Exception:
        logger.exception("Failed to show Linux notification")


def _toast(info: StreamInfo, with_open_button: bool = True) -> None:
    """Send a desktop notification (platform-dispatched)."""
    if platform.system() == "Windows":
        _toast_windows(info, with_open_button)
    else:
        _toast_linux(info, with_open_button)


def open_and_stop(
    info: StreamInfo,
    stop_fn: ActionCallback,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser and stop monitoring."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings)
    stop_fn()


def open_and_keep(
    info: StreamInfo,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser, keep monitoring other channels."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings)


def notify_only(info: StreamInfo) -> None:
    """Show a toast notification with 'open' button — no auto-browser."""
    _toast(info, with_open_button=True)


def open_and_exit(
    info: StreamInfo,
    exit_fn: ActionCallback,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser, then exit the application."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings)
    exit_fn()


def execute_action(
    action: str,
    info: StreamInfo,
    stop_fn: ActionCallback | None = None,
    exit_fn: ActionCallback | None = None,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Dispatch the configured action."""
    if action == "open_and_stop":
        open_and_stop(info, stop_fn or (lambda: None), browser_settings)
    elif action == "open_and_keep":
        open_and_keep(info, browser_settings)
    elif action == "notify_only":
        notify_only(info)
    elif action == "open_and_exit":
        open_and_exit(info, exit_fn or (lambda: None), browser_settings)
    else:
        logger.warning("Unknown action: %s", action)
