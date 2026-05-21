"""觸發行為 — 開播偵測後的四種動作 + 桌面通知（Windows Toast / Linux notify-send）。"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.url_parser import parse_url

logger = logging.getLogger(__name__)

ActionCallback = Callable[[], None]

# Win32 window class names used by mainstream browsers.
# Chromium-family (Chrome, Edge, Brave, Vivaldi, Opera, Arc…) all share the
# Chrome_WidgetWin_1 class because they're built on the same Aura toolkit.
_WIN32_WINDOW_CLASS_BY_FAMILY = {
    "chromium": "Chrome_WidgetWin_1",
    "firefox": "MozillaWindowClass",
}

# Window titles that mean "this window is the browser's regular UI, not a
# stream player we just spawned". Pure substring/equality check (lower-cased)
# is enough — these strings are stable across browser updates and rarely
# appear inside a real stream title. Multilingual entries cover Chrome/Edge
# new-tab translations on Traditional Chinese / Simplified Chinese /
# Japanese / Korean / English builds.
_NOISE_WINDOW_TITLE_SUBSTRINGS: tuple[str, ...] = (
    "new tab",
    "新分頁",
    "新分页",
    "新しいタブ",
    "새 탭",
    "new window",
)

# Browser-name suffixes — used to detect "blank chrome" windows like
# "Google Chrome" or "Microsoft Edge" with no actual page title.
_BROWSER_NAME_TITLES: tuple[str, ...] = (
    "google chrome",
    "microsoft edge",
    "brave",
    "vivaldi",
    "opera",
    "firefox",
    "mozilla firefox",
)


def _is_noise_window_title(title: str) -> bool:
    """Return True if *title* looks like a stock browser/blank window.

    Stops the post-launch HWND tracker (and the off-topic prune pass) from
    mistaking the user's regular "Google Chrome" or "New Tab" window for the
    App Mode stream window we just opened.
    """
    if not title:
        # Empty title — _enum_browser_hwnds already drops these, but keep
        # the guard so direct callers (off-topic prune) also catch them.
        return True
    low = title.strip().lower()
    if not low:
        return True
    if low in _BROWSER_NAME_TITLES:
        return True
    for noise in _NOISE_WINDOW_TITLE_SUBSTRINGS:
        if noise in low:
            return True
    return False

_SW_HIDE = 0
_SW_SHOW = 5
_SW_RESTORE = 9          # un-maximise / un-minimise before repositioning
_SW_SHOWMINNOACTIVE = 7  # minimize without taking focus
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_WM_CLOSE = 0x0010
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_MINIMIZE_POLL_INTERVAL_S = 0.15
_MINIMIZE_DEADLINE_S = 8.0  # generous: Chrome cold-start can take 2-3s

@dataclass
class _TrackedWindow:
    """Bookkeeping for a single HWND we opened.

    Attributes:
        hwnd: The Win32 handle for the browser window.
        opened_at: ``time.monotonic()`` snapshot of when we registered the
            HWND. Used by the off-topic prune pass to skip windows that
            haven't finished loading yet (title might still be "Loading…").
        keywords: Strings the legit stream window's title is expected to
            contain (channel display_name, channel slug, stream title…).
            If a tracked HWND's title later loses every keyword, the prune
            pass considers it redirected/navigated-away and closes it.
    """

    hwnd: int
    opened_at: float = field(default_factory=time.monotonic)
    keywords: tuple[str, ...] = ()


# url -> list of TrackedWindow entries for that URL. Populated by the
# post-launch window manager and consumed by close_browser_window_for_url
# (and prune_off_topic_tracked_windows) so we close the right tabs without
# nuking unrelated browser windows. Module-global on purpose: multiple App
# instances would step on each other anyway because Windows HWNDs are
# process-global.
_TRACKED_WINDOWS_BY_URL: dict[str, list[_TrackedWindow]] = {}
_TRACKED_HWNDS_LOCK = threading.Lock()


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


def _get_window_title(user32: Any, hwnd: int) -> str:
    """Return the visible title of *hwnd*, or "" if unavailable.

    Wraps GetWindowTextW with the length-then-read dance so we don't truncate
    long stream titles. Falls back to "" silently on any ctypes error.
    """
    import ctypes

    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
    except OSError:
        return ""
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    try:
        user32.GetWindowTextW(hwnd, buf, length + 1)
    except OSError:
        return ""
    return buf.value or ""


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

    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL

    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL

    # GetWindowLong/SetWindowLong (W variant). We deliberately use the *Long*
    # form (not LongPtr) because GWL_EXSTYLE is always a 32-bit DWORD on every
    # Windows ABI, and the W form exists on 64-bit Windows too for back-compat.
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG

    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG

    user32._hello_streamer_signed = True  # type: ignore[attr-defined]


def _apply_new_browser_window_settings_async(
    class_name: str,
    baseline: set[int],
    settings: dict[str, Any],
    *,
    apply_geometry: bool = True,
    deadline_s: float = _MINIMIZE_DEADLINE_S,
    track_for_url: str = "",
    track_keywords: tuple[str, ...] = (),
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
    hide_from_taskbar = bool(settings.get("hide_from_taskbar"))
    set_window_pos_flags = _SWP_NOZORDER | _SWP_NOACTIVATE

    def _worker() -> None:
        deadline = time.monotonic() + deadline_s
        managed: set[int] = set()
        while time.monotonic() < deadline:
            current = _enum_browser_hwnds(class_name)
            new_hwnds = current - baseline - managed
            for hwnd in new_hwnds:
                # ── B: noise-window guard ──────────────────────────────────
                # If the user happened to open a new "Google Chrome" or
                # "New Tab" window while we were waiting for the App Mode
                # window to appear, that HWND would show up in `new_hwnds`
                # too. Re-check the title here and skip anything that looks
                # like the stock browser chrome rather than our actual
                # stream window — better to apply geometry to nothing than
                # to nuke the user's regular browsing session.
                try:
                    current_title = _get_window_title(user32, hwnd)
                except Exception:
                    current_title = ""
                if _is_noise_window_title(current_title):
                    logger.debug(
                        "Skipping HWND=%s during post-launch tracking: "
                        "title %r looks like the user's regular browser window",
                        hwnd,
                        current_title,
                    )
                    # Don't add to `managed` — leave room for the real window
                    # to show up in a later poll.
                    continue
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
                    if hide_from_taskbar:
                        # Must happen BEFORE minimize: minimizing a window
                        # whose WS_EX_TOOLWINDOW we then toggle on can
                        # leave a "ghost" taskbar slot until the next
                        # visibility change. Apply first, then minimize.
                        _hide_window_from_taskbar(user32, hwnd)
                    if minimized:
                        user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)
                    managed.add(hwnd)
                    if track_for_url:
                        _register_tracked_hwnd(
                            track_for_url, hwnd, keywords=track_keywords
                        )
                    logger.debug(
                        "Managed new browser window HWND=%s geometry=%s minimized=%s hide_taskbar=%s url=%s",
                        hwnd,
                        (x, y, width, height) if apply_geometry else None,
                        minimized,
                        hide_from_taskbar,
                        track_for_url or "<not tracked>",
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


# ---------------------------------------------------------------------------
# Taskbar visibility: flip the WS_EX_TOOLWINDOW bit so the window stops
# appearing in the Windows taskbar and Alt+Tab list.
# ---------------------------------------------------------------------------
def _hide_window_from_taskbar(user32: Any, hwnd: int) -> bool:
    """Set ``WS_EX_TOOLWINDOW`` (and clear ``WS_EX_APPWINDOW``) on *hwnd*.

    Returns True when the style was actually changed, False otherwise. The
    function expects *user32* to be the already-loaded ``ctypes.windll.user32``
    proxy with our signatures applied (caller should have called
    ``_configure_user32_signatures``).

    Side-effect: also performs a SW_HIDE → SW_SHOW cycle, which is the
    documented way to make Windows re-evaluate whether the window deserves a
    taskbar slot. Without it, the style change is honoured *next* time the
    window changes visibility, which from the user's perspective looks like
    "nothing happened".
    """
    try:
        current = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    except OSError:
        logger.exception("GetWindowLong failed for HWND=%s", hwnd)
        return False

    desired = (int(current) | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
    if desired == current:
        return False

    try:
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, desired)
        # The taskbar caches whether each top-level window is "app-worthy"
        # at the moment WS_VISIBLE flips on. Toggling visibility forces a
        # refresh; otherwise the taskbar icon sticks around until the next
        # show/hide cycle.
        user32.ShowWindow(hwnd, _SW_HIDE)
        user32.ShowWindow(hwnd, _SW_SHOW)
    except OSError:
        logger.exception("SetWindowLong/ShowWindow failed for HWND=%s", hwnd)
        return False

    return True


# ---------------------------------------------------------------------------
# HWND tracking: register windows we spawn so we can close them later.
# ---------------------------------------------------------------------------
def _normalize_keywords(keywords: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """Lower-case + dedupe + strip empties; preserves first-seen order."""
    if not keywords:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        if not kw:
            continue
        low = kw.strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(low)
    return tuple(out)


def _register_tracked_hwnd(
    url: str,
    hwnd: int,
    keywords: tuple[str, ...] | list[str] | None = None,
) -> None:
    """Remember that we opened HWND for URL.

    *keywords* should contain identifying strings the legit stream window's
    title is expected to retain (channel display_name, channel slug, stream
    title). The off-topic prune pass uses them to detect redirects.
    """
    if not url or not hwnd:
        return
    norm_keywords = _normalize_keywords(keywords)
    with _TRACKED_HWNDS_LOCK:
        bucket = _TRACKED_WINDOWS_BY_URL.setdefault(url, [])
        # Avoid duplicate registration for the same HWND under the same URL;
        # merge keywords if the second registration brings more identifiers.
        for tracked in bucket:
            if tracked.hwnd == int(hwnd):
                if norm_keywords:
                    merged = _normalize_keywords(
                        tuple(tracked.keywords) + tuple(norm_keywords)
                    )
                    tracked.keywords = merged
                return
        bucket.append(
            _TrackedWindow(
                hwnd=int(hwnd),
                opened_at=time.monotonic(),
                keywords=norm_keywords,
            )
        )


def _snapshot_tracked_hwnds(url: str) -> set[int]:
    """Return the raw HWND set for *url* (legacy helper)."""
    with _TRACKED_HWNDS_LOCK:
        return {t.hwnd for t in _TRACKED_WINDOWS_BY_URL.get(url, ())}


def _snapshot_tracked_windows(url: str) -> list[_TrackedWindow]:
    """Return a defensive copy of TrackedWindow entries for *url*."""
    with _TRACKED_HWNDS_LOCK:
        return [
            _TrackedWindow(t.hwnd, t.opened_at, tuple(t.keywords))
            for t in _TRACKED_WINDOWS_BY_URL.get(url, ())
        ]


def _all_tracked_urls() -> list[str]:
    with _TRACKED_HWNDS_LOCK:
        return list(_TRACKED_WINDOWS_BY_URL.keys())


def _clear_tracked_hwnds(url: str) -> None:
    with _TRACKED_HWNDS_LOCK:
        _TRACKED_WINDOWS_BY_URL.pop(url, None)


def _remove_tracked_hwnd(url: str, hwnd: int) -> None:
    """Drop a single HWND from URL's tracking bucket (no-op if absent)."""
    with _TRACKED_HWNDS_LOCK:
        bucket = _TRACKED_WINDOWS_BY_URL.get(url)
        if not bucket:
            return
        _TRACKED_WINDOWS_BY_URL[url] = [t for t in bucket if t.hwnd != int(hwnd)]
        if not _TRACKED_WINDOWS_BY_URL[url]:
            _TRACKED_WINDOWS_BY_URL.pop(url, None)


def _enum_visible_hwnds_with_title() -> list[tuple[int, str]]:
    """Return ``(hwnd, title)`` for all visible top-level windows with text.

    Used by ``close_browser_window_for_url`` as a title-keyword fallback when
    no HWND was tracked (e.g. the URL was opened via the system default
    browser instead of through our launcher).
    """
    if not _is_windows():
        return []

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return []

    try:
        _configure_user32_signatures(user32)
    except Exception:
        logger.debug("Could not configure user32 signatures", exc_info=True)

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    results: list[tuple[int, str]] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value:
            results.append((int(hwnd), buf.value))
        return True

    try:
        user32.EnumWindows(enum_proc(_callback), 0)
    except OSError:
        logger.exception("EnumWindows (title enum) failed")
        return []

    return results


def _post_close_window(hwnd: int) -> bool:
    """Politely ask Windows to close HWND. Returns True on success.

    Uses ``PostMessage(WM_CLOSE)`` rather than ``TerminateProcess`` so the
    browser can fire ``beforeunload`` handlers and unload tabs cleanly. This
    is the same code path the user triggers by hitting the [X] button.
    """
    if not _is_windows() or not hwnd:
        return False

    try:
        import ctypes
    except ImportError:
        return False

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return False

    try:
        _configure_user32_signatures(user32)
    except Exception:
        logger.debug("Could not configure user32 signatures", exc_info=True)

    try:
        if not user32.IsWindow(hwnd):
            return False
        result = user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return bool(result)
    except OSError:
        logger.exception("PostMessage(WM_CLOSE) failed for HWND=%s", hwnd)
        return False


def _find_hwnds_by_title_keyword(keywords: list[str]) -> set[int]:
    """Return HWNDs whose visible window title contains any of *keywords*.

    Case-insensitive substring match (after stripping whitespace). Empty
    keywords are ignored; an empty list returns an empty set.
    """
    cleaned = [k.strip().lower() for k in keywords if k and k.strip()]
    if not cleaned:
        return set()

    matches: set[int] = set()
    for hwnd, title in _enum_visible_hwnds_with_title():
        low = title.lower()
        if any(k in low for k in cleaned):
            matches.add(hwnd)
    return matches


def close_all_tracked_windows() -> int:
    """Close every browser window this app has tracked, across all URLs.

    Used by the "close on Stop" feature so the user can wipe every player
    window in one action. Returns the count of windows that received a
    WM_CLOSE. Safe on non-Windows (returns 0).

    Sends WM_CLOSE only — same code path as the [X] button — so the browser
    can fire ``beforeunload`` handlers and flush state cleanly.
    """
    if not _is_windows():
        return 0
    closed = 0
    for url in _all_tracked_urls():
        for hwnd in _snapshot_tracked_hwnds(url):
            if _post_close_window(hwnd):
                closed += 1
        _clear_tracked_hwnds(url)
    return closed


def prune_off_topic_tracked_windows(
    *,
    min_age_s: float = 6.0,
) -> int:
    """Close (and untrack) any tracked window whose title no longer matches.

    Iterates every TrackedWindow in the registry and, for each, reads the
    *current* HWND title. If:

      1. the window has lived past *min_age_s* (so we don't kill it during
         the initial "Loading…" / "Untitled" phase), AND
      2. the title now matches a stock browser/blank pattern OR no longer
         contains any of the keywords we registered with it,

    we send WM_CLOSE and drop it from tracking. Returns the number of
    windows actually closed. Safe on non-Windows (returns 0).

    Designed to catch the "user got redirected to a billing/login page" or
    "user clicked Back to the homepage" cases where the window we opened
    is no longer watching the stream we wanted to track.
    """
    if not _is_windows():
        return 0
    try:
        import ctypes
    except ImportError:
        return 0
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return 0
    try:
        _configure_user32_signatures(user32)
    except Exception:
        logger.debug("Could not configure user32 signatures", exc_info=True)

    now = time.monotonic()
    closed = 0
    for url in _all_tracked_urls():
        for tracked in _snapshot_tracked_windows(url):
            hwnd = tracked.hwnd
            # Phase 1: drop stale HWNDs (window was already closed by the
            # user / browser crashed / etc.).
            try:
                still_alive = bool(user32.IsWindow(hwnd))
            except OSError:
                still_alive = False
            if not still_alive:
                _remove_tracked_hwnd(url, hwnd)
                continue
            # Phase 2: respect the grace period.
            if (now - tracked.opened_at) < min_age_s:
                continue
            # Phase 3: read current title and decide.
            title = _get_window_title(user32, hwnd)
            title_low = title.lower()
            looks_like_browser_chrome = _is_noise_window_title(title)
            # Off-topic iff (no keyword) OR (registered keywords all gone).
            if tracked.keywords:
                has_any_keyword = any(kw in title_low for kw in tracked.keywords)
            else:
                # No keywords registered → only browser-chrome titles count
                # as off-topic. This keeps the feature safe for callers that
                # never registered keywords (we won't aggressively close
                # arbitrary tabs we opened).
                has_any_keyword = not looks_like_browser_chrome
            if has_any_keyword and not looks_like_browser_chrome:
                continue
            # Window has drifted away from the stream we opened it for.
            if _post_close_window(hwnd):
                closed += 1
                logger.info(
                    "Closed off-topic browser window HWND=%s title=%r url=%s",
                    hwnd,
                    title,
                    url,
                )
            _remove_tracked_hwnd(url, hwnd)
    return closed


def close_browser_window_for_url(
    url: str, *, title_keywords: list[str] | None = None
) -> int:
    """Close any browser window we previously opened for *url*.

    Returns the count of windows that received a WM_CLOSE. The lookup is:

    1. Exact HWND tracked by our post-launch window manager (most reliable;
       only sends WM_CLOSE to the window WE opened).
    2. Title-keyword fallback for the case where the URL was opened via
       ``webbrowser`` (no tracking) or the tracked HWNDs went stale
       (browser crashed, user closed manually, etc.). Each keyword is
       case-insensitively substring-matched against visible window titles.

    Safe on non-Windows (returns 0 without doing anything).
    """
    if not _is_windows() or not url:
        return 0

    hwnds = _snapshot_tracked_hwnds(url)
    closed = 0

    for hwnd in hwnds:
        if _post_close_window(hwnd):
            closed += 1

    # Always clear the registry afterwards so a re-trigger for the same URL
    # starts fresh (otherwise a future close call could try a stale HWND).
    _clear_tracked_hwnds(url)

    if closed == 0 and title_keywords:
        for hwnd in _find_hwnds_by_title_keyword(title_keywords):
            if _post_close_window(hwnd):
                closed += 1

    return closed


# Filesystem-safe characters only. Anything else gets collapsed to "_" so a
# weird channel slug can never traverse out of user_data_dir.
_SAFE_PROFILE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _slugify_channel(value: str, *, max_len: int = 64) -> str:
    cleaned = _SAFE_PROFILE_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._") or "default"
    return cleaned[:max_len]


def _derive_channel_profile_subdir(url: str) -> str | None:
    """Return a per-channel sub-folder name (e.g. ``twitch_kaicenat``) or None.

    Falls back to None when the URL doesn't match a known platform pattern;
    the caller should then keep using the base user_data_dir as-is.
    """
    parsed = parse_url(url)
    if parsed is None:
        return None
    return f"{_slugify_channel(parsed.platform)}_{_slugify_channel(parsed.name)}"


def _resolve_effective_user_data_dir(
    url: str, base_dir: str, per_channel: bool
) -> str:
    """Pick the actual --user-data-dir path to feed the browser.

    When ``per_channel`` is True and the URL parses cleanly, we append a
    sub-folder named ``<platform>_<channel>`` so each followed channel gets
    its own browser master process. This is the only reliable way to make
    --app= / --window-position survive across multiple stream triggers,
    because Chrome's master process drops those flags on every IPC-forwarded
    launch.
    """
    base_dir = (base_dir or "").strip()
    if not base_dir:
        return ""
    if not per_channel:
        return base_dir

    subdir = _derive_channel_profile_subdir(url)
    if not subdir:
        return base_dir
    expanded = Path(os.path.expandvars(os.path.expanduser(base_dir)))
    return str(expanded / subdir)


def _wants_geometry_flags(
    *,
    apply_geometry: bool,
    x: int,
    y: int,
    width: int,
    height: int,
) -> bool:
    """Return True when ``--window-position`` / ``--window-size`` would be
    emitted but Chrome/Edge will likely *silently drop them* because their
    master process is already running.

    Note: ``--app=`` is **not** included here. Empirically, Chrome/Edge still
    honour ``--app=URL`` when forwarded to an existing master process — what
    they drop is only the per-launch *geometry* flags. The previous warning
    that lumped ``--app=`` in with the position/size flags caused users to
    set ``user_data_dir`` unnecessarily.
    """
    if not apply_geometry:
        return False
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
    apply_geometry = bool(settings.get("apply_geometry", True))
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
        can_apply_geometry_after_launch = (
            apply_geometry and _is_windows() and (new_window or app_mode)
        )
        if apply_geometry and (x or y) and not can_apply_geometry_after_launch:
            dropped.append("window position")
        if (
            apply_geometry
            and (width != 1280 or height != 720)
            and not can_apply_geometry_after_launch
        ):
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
        # Fresh per-channel profiles otherwise pop up Chrome's welcome /
        # "make Chrome default" / signed-out NTP screens, which break App Mode.
        args.extend(["--no-first-run", "--no-default-browser-check"])
    elif _wants_geometry_flags(
        apply_geometry=apply_geometry,
        x=x,
        y=y,
        width=width,
        height=height,
    ):
        # Geometry flags (only) get IPC-dropped by Chrome's master process —
        # ``--app=`` itself still works fine when the user wants the dedicated
        # window without committing to a separate profile directory. The
        # post-launch Win32 worker (_apply_new_browser_window_settings_async)
        # will fix up window position/size after the fact.
        logger.info(
            "Chrome/Edge: --window-position / --window-size are dropped by "
            "the master process when the browser is already running. "
            "Geometry will be applied via Win32 after launch instead. "
            "Set browser_settings.user_data_dir to a folder path if you "
            "need the CLI flags to take effect at startup."
        )

    # Note: minimisation is NOT a CLI flag for Chromium (--start-minimized
    # doesn't actually exist as a switch). We handle it post-launch via
    # Win32 ShowWindow inside _open_with_browser_settings.
    if app_mode:
        # --app= already opens its own dedicated window; --new-window would
        # just be redundant noise (and slightly confusing in logs).
        if apply_geometry:
            args.append(f"--window-position={x},{y}")
            args.append(f"--window-size={width},{height}")
        args.append(f"--app={url}")
        return args

    if new_window:
        args.append("--new-window")
        if apply_geometry:
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


def _open_with_browser_settings(
    url: str,
    settings: dict[str, Any],
    *,
    title_hints: tuple[str, ...] = (),
) -> bool:
    """Spawn the configured browser with CLI args.

    Returns True if ``Popen`` succeeded. On Windows, we capture the current set
    of top-level browser windows *before* spawning, then start a background
    thread that applies position, size, and minimised state to any new windows
    that appear afterwards. This is more reliable than browser CLI flags alone,
    because Chrome/Edge route requests through a long-lived master process.
    """
    # Resolve the effective user_data_dir up front so both the CLI args and
    # the mkdir below see the same per-channel path.
    effective_settings = dict(settings)
    base_user_data_dir = (settings.get("user_data_dir") or "").strip()
    per_channel = bool(settings.get("per_channel_profile", True))
    effective_user_data_dir = _resolve_effective_user_data_dir(
        url, base_user_data_dir, per_channel
    )
    effective_settings["user_data_dir"] = effective_user_data_dir

    args = _build_browser_args(url, effective_settings)
    family = detect_browser_family(args[0])

    # Make sure the profile folder exists *before* Popen — Chrome will happily
    # create it itself but failing fast here gives much nicer error messages
    # than a silent browser launch failure.
    if effective_user_data_dir:
        try:
            Path(
                os.path.expandvars(os.path.expanduser(effective_user_data_dir))
            ).mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception(
                "Could not create browser user_data_dir: %s", effective_user_data_dir
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
            effective_settings,
            apply_geometry=bool(effective_settings.get("apply_geometry", True)),
            track_for_url=url,
            track_keywords=tuple(title_hints),
        )

    return True


def open_url(
    url: str,
    browser_settings: dict[str, Any] | None = None,
    *,
    title_hints: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Open *url* in the user's browser.

    If *browser_settings* is provided and ``enabled`` is true, the URL is
    launched via ``subprocess`` so we can control window position, size and
    minimised state (Chrome/Edge CLI flags). Otherwise we fall back to the
    standard ``webbrowser`` module with a Windows shell fallback.
    """
    if not url:
        logger.warning("Cannot open empty URL")
        return False

    hints_tuple = tuple(title_hints or ())

    if browser_settings and browser_settings.get("enabled"):
        if _open_with_browser_settings(
            url, browser_settings, title_hints=hints_tuple
        ):
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
        title = tr(
            "notify.upcoming.title",
            channel_name=channel_name,
            platform_display=platform_display,
        )
        time_str = _format_scheduled_start(info.scheduled_start)
        if time_str:
            body = tr("notify.upcoming.body.scheduled", time_str=time_str)
        else:
            body = info.title or tr("notify.upcoming.body.default")
    elif status == "video":
        title = tr(
            "notify.video.title",
            channel_name=channel_name,
            platform_display=platform_display,
        )
        body = info.title or tr("notify.video.body.default")
    else:
        title = tr(
            "notify.live.title",
            channel_name=channel_name,
            platform_display=platform_display,
        )
        body = info.title or tr(
            "notify.live.body.default",
            channel_name=channel_name,
            platform=info.platform,
        )

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
            toast.add_actions(label=tr("notify.watch_now"), launch=info.url)

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


def _title_hints_from_stream_info(info: StreamInfo) -> tuple[str, ...]:
    """Derive the keywords the prune pass will look for in window titles.

    We include the channel display_name and the channel slug (so the prune
    pass survives a re-rendered title that drops one but not the other),
    plus the stream title itself (most reliable identifier for App Mode
    windows, which usually show "<stream title>" as the window text).
    """
    raw = (info.display_name or "", info.channel or "", info.title or "")
    return tuple(h for h in raw if h)


def open_and_stop(
    info: StreamInfo,
    stop_fn: ActionCallback,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser and stop monitoring."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings, title_hints=_title_hints_from_stream_info(info))
    stop_fn()


def open_and_keep(
    info: StreamInfo,
    browser_settings: dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser, keep monitoring other channels."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings, title_hints=_title_hints_from_stream_info(info))


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
    open_url(info.url, browser_settings, title_hints=_title_hints_from_stream_info(info))
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
