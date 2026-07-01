"""觸發行為 — 開播偵測後的四種動作 + 桌面通知（Windows Toast / Linux notify-send）。"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Callable

import stream_monitor.browser_win32 as _browser_win32
from stream_monitor.browser_settings_model import (
    BrowserSettings,
    coerce_browser_settings,
)
from stream_monitor.chrome_prefs import merge_tab_discarding_exceptions
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.i18n import tr
from stream_monitor.url_parser import parse_url

_BW_PATCHABLE = (
    "_apply_new_browser_window_settings_async",
    "_configure_user32_signatures",
    "_enum_browser_hwnds",
    "_enum_visible_hwnds_with_title",
    "_find_hwnds_by_title_keyword",
    "_get_window_title",
    "_hide_window_from_taskbar",
    "_is_browser_popup_or_tool_window",
    "_is_windows",
    "_post_close_window",
)


def _install_browser_win32_routes() -> None:
    """Route Win32 calls through this module so tests can monkeypatch notifier."""
    import sys

    notifier_mod = sys.modules[__name__]
    for name in _BW_PATCHABLE:
        impl = getattr(_browser_win32, name)

        def _make_routed(impl=impl, name=name):
            def routed(*args: Any, **kwargs: Any):
                current = getattr(notifier_mod, name, impl)
                if current is not routed and current is not impl:
                    return current(*args, **kwargs)
                return impl(*args, **kwargs)

            routed.__name__ = name
            return routed

        routed_fn = _make_routed()
        setattr(_browser_win32, name, routed_fn)
        setattr(notifier_mod, name, routed_fn)


_install_browser_win32_routes()

from stream_monitor.browser_win32 import (  # noqa: E402, F401
    _GWL_EXSTYLE,
    _SW_HIDE,
    _SW_RESTORE,
    _SW_SHOW,
    _SW_SHOWMINNOACTIVE,
    _SWP_NOACTIVATE,
    _SWP_NOZORDER,
    _TITLE_FALLBACK_BLOCKED_URLS,
    _TRACKED_HWNDS_LOCK,
    _TRACKED_WINDOWS_BY_URL,
    _WIN32_WINDOW_CLASS_BY_FAMILY,
    _WM_CLOSE,
    _WS_EX_APPWINDOW,
    _WS_EX_TOOLWINDOW,
    _apply_new_browser_window_settings_async,
    _block_title_fallback_for_url,
    _clear_tracked_hwnds,
    _configure_user32_signatures,
    _enum_browser_hwnds,
    _enum_visible_hwnds_with_title,
    _find_hwnds_by_title_keyword,
    _get_window_title,
    _hide_window_from_taskbar,
    _is_browser_popup_or_tool_window,
    _is_noise_window_title,
    _is_windows,
    _post_close_window,
    _register_tracked_hwnd,
    _remove_tracked_hwnd,
    _snapshot_tracked_windows,
    _TrackedWindow,
    _unblock_title_fallback_for_url,
    close_all_tracked_windows,
    close_browser_window_for_url,
    prune_off_topic_tracked_windows,
    set_system_keep_awake,
    tracked_hwnds_for_url,
)
from stream_monitor.viewer_engagement_model import (  # noqa: E402
    ViewerEngagementSettings,
    coerce_viewer_engagement,
    is_twitch_url,
)

logger = logging.getLogger(__name__)

ActionCallback = Callable[[], None]

# Process-wide viewer-engagement assist config (mirrors the module-global Win32
# window tracking below; browser launching is inherently process-global). Set
# by the App via ``configure_viewer_engagement`` and consulted when a Twitch URL
# is opened through a custom browser launch.
_VIEWER_ENGAGEMENT: ViewerEngagementSettings | None = None
# Twitch URLs currently holding a keep-awake request, so we release it once the
# last engagement window we opened is closed.
_ENGAGEMENT_AWAKE_URLS: set[str] = set()


def configure_viewer_engagement(
    settings: ViewerEngagementSettings | dict[str, Any] | None,
) -> None:
    """Install the active viewer-engagement settings for subsequent launches."""
    global _VIEWER_ENGAGEMENT
    _VIEWER_ENGAGEMENT = coerce_viewer_engagement(settings)


def _active_viewer_engagement() -> ViewerEngagementSettings | None:
    """Return the engagement settings only when the feature is enabled."""
    settings = _VIEWER_ENGAGEMENT
    if settings is not None and settings.enabled:
        return settings
    return None


def _release_engagement_keep_awake(url: str) -> None:
    """Drop *url* from the keep-awake set; clear the request when none remain."""
    if url in _ENGAGEMENT_AWAKE_URLS:
        _ENGAGEMENT_AWAKE_URLS.discard(url)
        if not _ENGAGEMENT_AWAKE_URLS:
            set_system_keep_awake(False)


# Wrap the Win32 close helpers so closing a window we opened for a Twitch URL
# also releases its keep-awake request. ``app``/``app_dialogs`` import these
# names from ``notifier``, so the wrappers transparently apply everywhere.
_close_browser_window_for_url_impl = close_browser_window_for_url
_close_all_tracked_windows_impl = close_all_tracked_windows


def close_browser_window_for_url(
    url: str, *, title_keywords: list[str] | None = None
) -> int:
    closed = _close_browser_window_for_url_impl(url, title_keywords=title_keywords)
    _release_engagement_keep_awake(url)
    return closed


def close_all_tracked_windows() -> int:
    closed = _close_all_tracked_windows_impl()
    _ENGAGEMENT_AWAKE_URLS.clear()
    set_system_keep_awake(False)
    return closed

# Chromium switches that stop a backgrounded / occluded Twitch tab from being
# throttled or suspended, so its heartbeat keeps flowing and the view keeps
# counting even when the window is not the foreground one. Only effective on a
# cold master process (dedicated profile); harmless otherwise. Firefox has no
# command-line equivalent, so these are Chromium-only.
_ANTI_THROTTLE_FLAGS: tuple[str, ...] = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion",
)

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


def _default_browser_profile_root() -> str:
    """Return ``<app base_dir>/browser_profile`` as the runtime auto-fallback.

    Used by :func:`_resolve_effective_user_data_dir` when the user enabled
    ``per_channel_profile`` but left ``user_data_dir`` empty — that combination
    is a legacy config pitfall that silently disables every isolation flag and
    leads to cross-window HWND contamination under Chrome's master process.
    """
    from stream_monitor import default_browser_profile_dir

    return default_browser_profile_dir()


def _close_features_enabled(
    settings: BrowserSettings | dict[str, Any],
) -> bool:
    coerced = coerce_browser_settings(settings)
    if coerced is None:
        return False
    return (
        coerced.close_on_offline
        or coerced.close_off_topic_pages
        or coerced.close_on_stop
    )


def browser_window_tracking_available(
    settings: BrowserSettings | dict[str, Any] | None,
    url: str = "https://www.twitch.tv/example",
) -> bool:
    """True when Win32 HWND tracking and managed close can work.

    Requires a dedicated profile **and** a launch mode that creates a new
  top-level browser window (``app_mode`` or ``new_window``). Opening as a
    tab in an existing window cannot be tracked or closed reliably.
    """
    if not browser_isolation_available(settings, url):
        return False
    coerced = coerce_browser_settings(settings)
    if coerced is None:
        return False
    return coerced.app_mode or coerced.new_window


def browser_isolation_available(
    settings: BrowserSettings | dict[str, Any] | None,
    url: str = "https://www.twitch.tv/example",
) -> bool:
    """True when custom-browser launches can use a dedicated profile.

    HWND tracking and safe close-on-offline / off-topic pruning depend on
    this isolation. Without it, title-keyword fallbacks can close unrelated
    browser windows that merely mention a channel name.
    """
    coerced = coerce_browser_settings(settings)
    if coerced is None or not coerced.enabled:
        return False
    base_dir = coerced.user_data_dir.strip()
    return bool(
        _resolve_effective_user_data_dir(
            url, base_dir, coerced.per_channel_profile
        )
    )


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

    Safety net: if ``per_channel`` is True but ``base_dir`` is empty (legacy
    config pitfall — UI prior to v0.6.x could leave that combination after a
    manual edit), we fall back to ``<app base_dir>/browser_profile`` so each
    channel still gets its own master process. Without this fallback the
    user's HWND tracker silently maps multiple channels onto whatever window
    Chrome chooses to share, and the off-topic prune pass mistakenly closes
    live-stream windows whose visible title belongs to another channel.
    """
    base_dir = (base_dir or "").strip()
    if not base_dir:
        if not per_channel:
            return ""
        base_dir = _default_browser_profile_root()
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

    # Viewer-engagement assist: keep the Twitch tab unthrottled so it stays
    # "counted" while backgrounded. Applies to every Chromium open style (tab,
    # new window, App Mode) but only when the assist is enabled for this URL.
    if _active_viewer_engagement() is not None and is_twitch_url(url):
        args.extend(_ANTI_THROTTLE_FLAGS)

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


def _apply_viewer_engagement_to_launch(
    url: str,
    effective_settings: dict[str, Any],
    effective_user_data_dir: str,
) -> None:
    """Adjust launch settings for the Twitch watch-credit assist (in place).

    A minimised / taskbar-hidden window reads as a background tab to Twitch, so
    when the assist is enabled for a Twitch URL we keep the window visible and
    optionally bring it to the front. We also hold a system keep-awake request
    while the window is open, and (for a dedicated profile) add twitch.tv to
    Chrome's Memory Saver allowlist so the tab is not frozen.
    """
    engagement = _active_viewer_engagement()
    if engagement is None or not is_twitch_url(url):
        return
    if engagement.force_visible:
        effective_settings["minimized"] = False
        effective_settings["hide_from_taskbar"] = False
    effective_settings["bring_to_front"] = bool(engagement.bring_to_front)
    if engagement.keep_system_awake:
        _ENGAGEMENT_AWAKE_URLS.add(url)
        set_system_keep_awake(True)
    if engagement.whitelist_performance and effective_user_data_dir:
        try:
            merge_tab_discarding_exceptions(effective_user_data_dir)
        except Exception:
            logger.exception(
                "viewer engagement: failed to merge Chrome performance "
                "allowlist for %s",
                effective_user_data_dir,
            )


def _wants_geometry_only_fixup(
    *,
    isolation_available: bool,
    app_mode: bool,
    apply_geometry: bool,
) -> bool:
    """Shared-profile App Mode: Win32 geometry only, never HWND tracking."""
    return (
        _is_windows()
        and not isolation_available
        and app_mode
        and apply_geometry
    )


def _open_with_browser_settings(
    url: str,
    settings: BrowserSettings | dict[str, Any],
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
    coerced = coerce_browser_settings(settings)
    if coerced is None:
        return False
    settings = coerced.to_dict()
    # Resolve the effective user_data_dir up front so both the CLI args and
    # the mkdir below see the same per-channel path.
    effective_settings = dict(settings)
    base_user_data_dir = (settings.get("user_data_dir") or "").strip()
    per_channel = bool(settings.get("per_channel_profile", True))
    effective_user_data_dir = _resolve_effective_user_data_dir(
        url, base_user_data_dir, per_channel
    )
    effective_settings["user_data_dir"] = effective_user_data_dir

    _apply_viewer_engagement_to_launch(
        url, effective_settings, effective_user_data_dir
    )

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

    want_minimize = bool(effective_settings.get("minimized")) and _is_windows()
    new_window_expected = bool(settings.get("app_mode")) or bool(
        settings.get("new_window", True)
    )
    # ── Safety degradation when no profile isolation ────────────────────────
    # When the effective user_data_dir is empty we are launching against the
    # user's main Chrome master process, which means:
    #   1. ``--app=`` / ``--window-position`` / ``--window-size`` may be
    #      silently dropped by Chrome's IPC handler.
    #   2. The HWND diff (pre-launch baseline vs post-launch poll) is
    #      ambiguous — the "new" window might be something the user opened
    #      manually in another app, not the URL we just spawned.
    #   3. Registering that ambiguous HWND would let off-topic prune /
    #      close-on-offline send WM_CLOSE to the wrong window.
    #
    # Refuse to play the Win32 post-launch game when we can't isolate. The
    # browser still opens (Popen below); we just don't try to manage the
    # window or track it. This is a deliberate downgrade — the surface area
    # of "settings cargo-culted by an unrelated browser window" is much
    # worse than "geometry didn't apply for one launch".
    isolation_available = bool(effective_user_data_dir)
    app_mode = bool(settings.get("app_mode"))
    apply_geometry = bool(effective_settings.get("apply_geometry", True))
    want_geometry_only_fixup = _wants_geometry_only_fixup(
        isolation_available=isolation_available,
        app_mode=app_mode,
        apply_geometry=apply_geometry,
    )
    want_window_management = (
        _is_windows() and new_window_expected and isolation_available
    )
    want_win32_fixup = want_window_management or want_geometry_only_fixup
    wants_close_or_hide_mgmt = (
        bool(settings.get("close_on_offline"))
        or bool(settings.get("close_off_topic_pages"))
        or want_minimize
        or bool(settings.get("hide_from_taskbar"))
    )
    if (
        _is_windows()
        and new_window_expected
        and not isolation_available
        and wants_close_or_hide_mgmt
    ):
        logger.warning(
            "browser_settings.user_data_dir is empty — close-on-offline, "
            "close-off-topic-pages, minimize-on-launch, and hide-from-taskbar "
            "require a dedicated profile path. Set browser_settings.user_data_dir "
            "to a folder path (or enable per_channel_profile) to restore them."
        )
    elif (
        _is_windows()
        and new_window_expected
        and not isolation_available
        and apply_geometry
        and not app_mode
    ):
        logger.warning(
            "browser_settings.user_data_dir is empty — --window-position / "
            "--window-size are unreliable on a shared Chrome profile unless "
            "App Mode (solo window) is enabled; Win32 geometry fixup is only "
            "available for App Mode without a dedicated profile."
        )
    elif want_geometry_only_fixup:
        logger.info(
            "Applying Win32 geometry fixup for App Mode on shared profile "
            "(no HWND tracking)."
        )

    class_name = (
        _WIN32_WINDOW_CLASS_BY_FAMILY.get(family) if want_win32_fixup else None
    )

    # Chromium covers chrome/edge/brave/etc; treat "unknown" as Chromium too
    # because most custom Chromium-derived browsers share the same class.
    if want_win32_fixup and class_name is None and family == "unknown":
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
        if want_window_management:
            _unblock_title_fallback_for_url(url)
            worker_settings = effective_settings
            track_url = url
            track_keywords = tuple(title_hints)
        else:
            worker_settings = {
                **effective_settings,
                "minimized": False,
                "hide_from_taskbar": False,
                "bring_to_front": False,
            }
            track_url = ""
            track_keywords = ()
            if wants_close_or_hide_mgmt:
                _block_title_fallback_for_url(url)
        _apply_new_browser_window_settings_async(
            class_name,
            baseline,
            worker_settings,
            apply_geometry=apply_geometry,
            track_for_url=track_url,
            track_keywords=track_keywords,
        )
    elif _is_windows() and not isolation_available:
        _block_title_fallback_for_url(url)

    return True


def open_url(
    url: str,
    browser_settings: BrowserSettings | dict[str, Any] | None = None,
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
    coerced = coerce_browser_settings(browser_settings)

    custom_launch_failed = False
    if coerced is not None and coerced.enabled:
        if _open_with_browser_settings(
            url, coerced, title_hints=hints_tuple
        ):
            return True
        custom_launch_failed = True

    block_fallback = (
        _is_windows()
        and custom_launch_failed
        and coerced is not None
        and _close_features_enabled(coerced)
    )

    try:
        opened = webbrowser.open(url, new=2)
        if opened is not False:
            if block_fallback:
                _block_title_fallback_for_url(url)
            return True
        logger.warning("webbrowser.open returned False for URL: %s", url)
    except Exception:
        logger.exception("Failed to open URL with webbrowser: %s", url)

    if platform.system() == "Windows":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            if block_fallback:
                _block_title_fallback_for_url(url)
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


SIGNIN_DEFAULT_URL = "https://www.twitch.tv/"


def open_browser_for_signin(
    user_data_dir: str,
    *,
    browser_path: str = "chrome",
    url: str = SIGNIN_DEFAULT_URL,
) -> bool:
    """Open the configured browser with ``user_data_dir`` for a sign-in session.

    Dedicated browser profiles created via ``--user-data-dir=<NEW path>`` start
    out completely empty — no cookies, no saved logins. That makes the very
    first stream launch land on Twitch / YouTube logged out, which surprises
    users who expected the dedicated profile to inherit from their main
    Chrome session (Chrome's design makes that inheritance impossible).

    This helper provides the manual one-time bootstrap: it launches the
    browser pointed at ``user_data_dir`` in **plain** mode — no ``--app=``,
    no minimisation, no geometry constraints, no HWND tracking — so the
    user can log in normally and let the browser persist cookies inside
    the profile folder. Subsequent stream triggers using the same
    ``user_data_dir`` then reuse those saved credentials.

    Returns True if ``Popen`` succeeded. Safe to call repeatedly.
    """
    cleaned = (user_data_dir or "").strip()
    if not cleaned:
        logger.warning("open_browser_for_signin: user_data_dir is empty")
        return False

    executable = _resolve_browser_executable(browser_path or "chrome")
    family = detect_browser_family(executable)
    args: list[str] = [executable]

    if family == "firefox":
        # Firefox needs ``-no-remote`` to avoid forwarding to an already-
        # running default-profile instance; ``-new-instance`` makes it
        # spawn its own process for the dedicated profile path.
        args.extend(["-profile", cleaned, "-no-remote", "-new-instance", url])
    else:
        # Chromium family — same fresh-profile guards we use in the main
        # launch path so the welcome / "make Chrome default" wizards don't
        # trip the user up on first run inside the new profile.
        args.extend(
            [
                f"--user-data-dir={cleaned}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                url,
            ]
        )

    try:
        Path(os.path.expandvars(os.path.expanduser(cleaned))).mkdir(
            parents=True, exist_ok=True
        )
    except OSError:
        logger.exception(
            "Could not create sign-in user_data_dir: %s", cleaned
        )

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning(
            "Sign-in browser executable not found: %s", executable
        )
        return False
    except OSError:
        logger.exception(
            "Failed to spawn sign-in browser session: %s", args
        )
        return False

    logger.info(
        "Opened sign-in browser session: profile=%s url=%s family=%s",
        cleaned,
        url,
        family,
    )
    return True


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
    browser_settings: BrowserSettings | dict[str, Any] | None = None,
) -> None:
    """Open stream URL in browser and stop monitoring."""
    _toast(info, with_open_button=False)
    open_url(info.url, browser_settings, title_hints=_title_hints_from_stream_info(info))
    stop_fn()


def open_and_keep(
    info: StreamInfo,
    browser_settings: BrowserSettings | dict[str, Any] | None = None,
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
    browser_settings: BrowserSettings | dict[str, Any] | None = None,
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
    browser_settings: BrowserSettings | dict[str, Any] | None = None,
) -> None:
    """Dispatch the configured action."""
    logger.info(
        "execute_action: action=%s platform=%s channel=%s url=%s",
        action,
        info.platform,
        info.channel,
        info.url,
    )
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
