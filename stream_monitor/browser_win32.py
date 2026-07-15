"""Win32 browser window tracking and post-launch window management."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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
_GW_OWNER = 4
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
_TITLE_FALLBACK_BLOCKED_URLS: set[str] = set()
_TRACKED_HWNDS_LOCK = threading.Lock()


def _is_windows() -> bool:
    """Thin wrapper so tests can pretend to be on/off Windows without
    monkeypatching ``os.name`` globally (which corrupts pytest's pathlib)."""
    return os.name == "nt"


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

    user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindow.restype = wintypes.HWND

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    # GetWindowLong/SetWindowLong (W variant). We deliberately use the *Long*
    # form (not LongPtr) because GWL_EXSTYLE is always a 32-bit DWORD on every
    # Windows ABI, and the W form exists on 64-bit Windows too for back-compat.
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG

    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG

    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    user32._hello_streamer_signed = True  # type: ignore[attr-defined]


def _is_browser_popup_or_tool_window(user32: Any, hwnd: int) -> bool:
    """Return True for browser-owned popups/helpers, not real player windows.

    Do not filter by window rectangle here. The settings UI deliberately allows
    very small player windows (for example 100x100), and treating those as
    browser helpers prevents close_on_offline / close_on_stop from ever seeing
    the HWND. Size-independent noise is handled by owner/tool-window style here
    and by the title check in the post-launch worker.
    """
    try:
        owner = user32.GetWindow(hwnd, _GW_OWNER)
    except (AttributeError, OSError, TypeError):
        owner = 0
    if isinstance(owner, int) and owner:
        return True

    try:
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    except (AttributeError, OSError, TypeError):
        ex_style = 0
    if isinstance(ex_style, int):
        is_tool = bool(ex_style & _WS_EX_TOOLWINDOW)
        is_app = bool(ex_style & _WS_EX_APPWINDOW)
        if is_tool and not is_app:
            return True

    return False


_FOREGROUND_HOLD_POLL_S = 2.0


def _hold_foreground(user32: Any, hwnds: set[int], hold_seconds: int) -> None:
    """Periodically re-assert foreground on *hwnds* for *hold_seconds*.

    Twitch credits a view only when the Page Visibility API reports "visible"
    during the player's first heartbeat (~10-15s after page load). By keeping
    the window in the foreground for the hold period we ensure the initial
    heartbeat sees an active, watched state.
    """
    end = time.monotonic() + hold_seconds
    while time.monotonic() < end:
        for hwnd in list(hwnds):
            try:
                if not user32.IsWindow(hwnd):
                    hwnds.discard(hwnd)
                    continue
                user32.ShowWindow(hwnd, _SW_SHOW)
                user32.SetForegroundWindow(hwnd)
            except OSError:
                hwnds.discard(hwnd)
        if not hwnds:
            return
        time.sleep(_FOREGROUND_HOLD_POLL_S)
    logger.debug(
        "Foreground hold complete (%ds) for %d window(s)", hold_seconds, len(hwnds)
    )


def _apply_new_browser_window_settings_async(
    class_name: str,
    baseline: set[int],
    settings: dict[str, Any],
    *,
    apply_geometry: bool = True,
    deadline_s: float = _MINIMIZE_DEADLINE_S,
    track_for_url: str = "",
    track_keywords: tuple[str, ...] = (),
    foreground_hold_seconds: int = 0,
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
    bring_to_front = bool(settings.get("bring_to_front"))
    set_window_pos_flags = _SWP_NOZORDER | _SWP_NOACTIVATE

    def _worker() -> None:
        deadline = time.monotonic() + deadline_s
        managed: set[int] = set()
        ignored: set[int] = set()
        while time.monotonic() < deadline:
            current = _enum_browser_hwnds(class_name)
            new_hwnds = current - baseline - managed - ignored
            for hwnd in new_hwnds:
                if _is_browser_popup_or_tool_window(user32, hwnd):
                    logger.debug(
                        "Skipping HWND=%s during post-launch tracking: "
                        "window looks like a browser popup/tool surface",
                        hwnd,
                    )
                    ignored.add(hwnd)
                    continue
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
                if track_keywords:
                    cleaned_kws = [kw.strip().lower() for kw in track_keywords if kw and kw.strip()]
                    if cleaned_kws:
                        title_low = current_title.lower()
                        if not any(kw in title_low for kw in cleaned_kws):
                            logger.debug(
                                "Skipping HWND=%s during post-launch tracking: "
                                "title %r does not contain any of keywords %r",
                                hwnd,
                                current_title,
                                cleaned_kws,
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
                    elif bring_to_front:
                        # Viewer-engagement assist: a foreground, visible window
                        # is what Twitch treats as an actively watched tab.
                        user32.ShowWindow(hwnd, _SW_SHOW)
                        try:
                            user32.SetForegroundWindow(hwnd)
                        except OSError:
                            logger.debug(
                                "SetForegroundWindow failed for HWND=%s", hwnd
                            )
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
            return

        if bring_to_front and foreground_hold_seconds > 0:
            _hold_foreground(user32, managed, foreground_hold_seconds)

    thread = threading.Thread(target=_worker, daemon=True, name="browser-window-manager")
    thread.start()
    return thread


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


def _snapshot_tracked_windows(url: str) -> list[_TrackedWindow]:
    """Return a defensive copy of TrackedWindow entries for *url*."""
    with _TRACKED_HWNDS_LOCK:
        return [
            _TrackedWindow(t.hwnd, t.opened_at, tuple(t.keywords))
            for t in _TRACKED_WINDOWS_BY_URL.get(url, ())
        ]


def tracked_hwnds_for_url(url: str) -> set[int]:
    """Return the HWND set currently tracked for *url*."""
    return {t.hwnd for t in _snapshot_tracked_windows(url)}


def _all_tracked_urls() -> list[str]:
    with _TRACKED_HWNDS_LOCK:
        return list(_TRACKED_WINDOWS_BY_URL.keys())


def _clear_tracked_hwnds(url: str) -> None:
    with _TRACKED_HWNDS_LOCK:
        _TRACKED_WINDOWS_BY_URL.pop(url, None)


def _block_title_fallback_for_url(url: str) -> None:
    if not url:
        return
    with _TRACKED_HWNDS_LOCK:
        _TITLE_FALLBACK_BLOCKED_URLS.add(url)


def _unblock_title_fallback_for_url(url: str) -> None:
    with _TRACKED_HWNDS_LOCK:
        _TITLE_FALLBACK_BLOCKED_URLS.discard(url)


def _pop_title_fallback_block(url: str) -> bool:
    with _TRACKED_HWNDS_LOCK:
        blocked = url in _TITLE_FALLBACK_BLOCKED_URLS
        _TITLE_FALLBACK_BLOCKED_URLS.discard(url)
        return blocked


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
        for hwnd in tracked_hwnds_for_url(url):
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
       This fallback is suppressed for custom-browser launches that had no
       profile isolation, because in that mode we cannot prove the title
       belongs to a window opened by Hello Streamer.

    Safe on non-Windows (returns 0 without doing anything).
    """
    if not _is_windows() or not url:
        return 0

    hwnds = tracked_hwnds_for_url(url)
    closed = 0

    for hwnd in hwnds:
        if _post_close_window(hwnd):
            closed += 1

    # Always clear the registry afterwards so a re-trigger for the same URL
    # starts fresh (otherwise a future close call could try a stale HWND).
    _clear_tracked_hwnds(url)

    title_fallback_blocked = _pop_title_fallback_block(url)
    if closed == 0 and title_keywords and not title_fallback_blocked:
        for hwnd in _find_hwnds_by_title_keyword(title_keywords):
            if _post_close_window(hwnd):
                closed += 1

    return closed


# ---------------------------------------------------------------------------
# System sleep suppression (viewer-engagement assist).
#
# Twitch stops crediting a view when the machine sleeps mid-stream. While the
# user has an engagement-tracked Twitch window open we ask Windows to stay
# awake. SetThreadExecutionState with ES_CONTINUOUS persists only while the
# *calling thread* is alive, so a dedicated holder thread owns the request and
# clears it on release.
# ---------------------------------------------------------------------------
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class _KeepAwakeManager:
    """Hold a Windows execution-state request on a dedicated holder thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_active(self, active: bool) -> None:
        if not _is_windows():
            return
        with self._lock:
            if active and not self._active:
                self._active = True
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="keep-awake"
                )
                self._thread.start()
            elif not active and self._active:
                self._active = False
                self._stop.set()
                self._thread = None

    def _run(self) -> None:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
        except (ImportError, AttributeError, OSError):
            logger.debug("keep-awake: kernel32 unavailable", exc_info=True)
            return
        try:
            kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
            logger.info("keep-awake: system sleep suppressed")
            self._stop.wait()
        finally:
            try:
                kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            except OSError:
                logger.debug("keep-awake: failed to clear state", exc_info=True)
            logger.info("keep-awake: system sleep suppression released")


_KEEP_AWAKE = _KeepAwakeManager()


def set_system_keep_awake(active: bool) -> None:
    """Request (or release) Windows sleep suppression. No-op off Windows."""
    _KEEP_AWAKE.set_active(active)
