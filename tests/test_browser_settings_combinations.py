"""End-to-end combination matrix for ``browser_settings``.

This file complements the focused unit tests in ``test_notifier.py`` by
walking through every meaningful combination of toggles a user can reach
from the **Browser Settings** dialog and asserting the four user-visible
behaviours we care about:

1. **Opening** — does the launch produce the expected CLI surface for the
   chosen window mode (App Mode / new window / shared tab) and profile
   isolation (none / dedicated / per-channel)?
2. **Closing on offline** — does the channel-offline path send WM_CLOSE
   to the right window, and just as importantly, *never* to the wrong one?
3. **Closing on stop** — does the user-initiated Stop button close every
   window we opened?
4. **Cookies** — does the sign-in helper open the dedicated profile in a
   plain browser session so cookies persist for subsequent launches?

The matrix:

    Profile isolation × Window mode × Close behaviour × Geometry
        4-way       ×    3-way    ×     2³        × 2-way
                    (4 × 3 × 8 × 2 = 192 cells)

Most scenario tests cover one axis at a time and the cross-axis interactions
that have produced real bugs in the past (off-topic prune × shared profile,
title-keyword fallback × shared profile, sign-in helper × Firefox). The final
exhaustive smoke test enumerates all 192 cells so a new flag cannot quietly
break an unvisited combination.

Conventions:

* Test names follow ``test_<group>_<scenario>``.
* Every test mocks out ``subprocess.Popen`` and ``_is_windows`` so they
  run identically on macOS / Linux CI and Windows dev machines.
* Each ``_reset_tracked_hwnds`` call guards against module-global state
  leaking between tests.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pytest

from stream_monitor import notifier

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _reset_tracked_hwnds() -> None:
    """Wipe both the HWND registry and the title-fallback block set.

    Both are module-global by design (HWNDs are process-global on Windows
    so any per-instance store would be a lie), which means cross-test
    contamination is a real risk if a test forgets to clean up.
    """
    with notifier._TRACKED_HWNDS_LOCK:
        notifier._TRACKED_WINDOWS_BY_URL.clear()
        notifier._TITLE_FALLBACK_BLOCKED_URLS.clear()


@pytest.fixture(autouse=True)
def _isolate_tracking_state():
    """Ensure every test starts with a clean tracker state."""
    _reset_tracked_hwnds()
    yield
    _reset_tracked_hwnds()


def _stub_chrome(monkeypatch) -> None:
    """Make ``_resolve_browser_executable`` echo back ``"chrome"`` so we get
    deterministic CLI args independent of the host's installed Chrome path.
    """
    monkeypatch.setattr(
        notifier, "_resolve_browser_executable", lambda _value: "chrome"
    )


def _stub_firefox(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier, "_resolve_browser_executable", lambda _value: "firefox"
    )


def _capture_popen(monkeypatch) -> list[list[str]]:
    """Replace ``subprocess.Popen`` with a capture stub.

    Returns the list that successive launches will append their ``args``
    lists to. Each call also returns a dummy object so the caller can
    treat ``Popen`` as having "succeeded".
    """
    captured: list[list[str]] = []

    def _fake(args, **_kw):
        captured.append(list(args))

        class _Proc:
            pass

        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "Popen", _fake)
    return captured


def _stub_win32(monkeypatch, *, manager_calls: list[Any] | None = None) -> None:
    """Pretend to be on Windows and stub the post-launch worker.

    When ``manager_calls`` is provided, every invocation of
    ``_apply_new_browser_window_settings_async`` is recorded so the test
    can assert it (didn't) fire. ``_enum_browser_hwnds`` is stubbed to
    return an empty baseline by default.
    """
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    if manager_calls is not None:
        monkeypatch.setattr(
            notifier,
            "_apply_new_browser_window_settings_async",
            lambda *args, **kwargs: manager_calls.append((args, kwargs)),
        )


def _settings(**overrides: Any) -> dict[str, Any]:
    """Sane defaults that match ``DEFAULT_BROWSER_SETTINGS`` but with
    ``enabled=True`` so the custom-launch path is exercised by default.

    Mutate only the flags the test cares about; this keeps each test's
    intent crystal-clear in the diff.
    """
    base = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "apply_geometry": True,
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
        "minimized": False,
        "user_data_dir": "",
        "per_channel_profile": False,
        "close_on_offline": False,
        "close_on_stop": False,
        "close_off_topic_pages": False,
        "hide_from_taskbar": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Group 1 — Opening the browser
# ---------------------------------------------------------------------------
#
# Profile-isolation × window-mode × geometry table:
#
#   open_1a   enabled=False                     → webbrowser
#   open_1b   enabled, shared profile           → minimal Chromium CLI
#   open_1c   enabled, dedicated single profile → --user-data-dir
#   open_1d   enabled, per-channel + Twitch URL → twitch_<channel> subdir
#   open_1e   enabled, per-channel + YouTube    → youtube_<channel> subdir
#   open_1f   enabled, per-channel + bad URL    → falls back to base dir
#   open_1g   enabled, app_mode                 → --app= flag, no --new-window
#   open_1h   enabled, app_mode=False + new_win → --new-window
#   open_1i   enabled, apply_geometry=False     → no --window-position/size
#   open_1j   enabled, Firefox + dedicated      → -profile / -no-remote
#   open_1k   enabled, Popen FileNotFoundError  → fallback to webbrowser


def test_open_1a_disabled_uses_default_browser(monkeypatch) -> None:
    """When ``enabled=False`` the custom launch is bypassed entirely and the
    system default browser is used via ``webbrowser.open``. No Popen calls."""
    popen_called: list[Any] = []
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: popen_called.append(a) or object(),
    )
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    assert (
        notifier.open_url("https://example.com", _settings(enabled=False))
        is True
    )
    assert popen_called == []
    assert opened == [("https://example.com", 2)]


def test_open_1b_shared_profile_emits_minimal_cli(monkeypatch) -> None:
    """``enabled=True`` with no isolation: minimal Chromium CLI surface —
    a new window with optional geometry but **no** ``--user-data-dir``."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(x=5, y=6, width=1024, height=768),
    )
    assert captured == [
        [
            "chrome",
            "--new-window",
            "--window-position=5,6",
            "--window-size=1024,768",
            "https://example.com",
        ]
    ]


def test_open_1c_dedicated_profile_passes_user_data_dir(
    monkeypatch, tmp_path
) -> None:
    """Single dedicated profile: ``--user-data-dir=<path>`` plus the
    first-run guards Chrome needs when the profile folder is brand new."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    profile = tmp_path / "shared_profile"
    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(user_data_dir=str(profile), per_channel_profile=False),
    )
    args = captured[0]
    assert f"--user-data-dir={profile}" in args
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args


def test_open_1d_per_channel_twitch_uses_subdir(monkeypatch, tmp_path) -> None:
    """Per-channel + Twitch URL → ``<base>/twitch_<channel>`` sub-folder."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        "https://www.twitch.tv/Kaicenat",
        _settings(user_data_dir=str(base), per_channel_profile=True),
    )
    args = captured[0]
    # ``url_parser`` lower-cases channel names, so the sub-folder is
    # ``twitch_kaicenat`` regardless of how the URL was capitalised.
    expected = base / "twitch_kaicenat"
    assert f"--user-data-dir={expected}" in args


def test_open_1e_per_channel_youtube_uses_subdir(monkeypatch, tmp_path) -> None:
    """Per-channel + YouTube URL → ``<base>/youtube_<channel>`` sub-folder."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        "https://www.youtube.com/@SomeChannel",
        _settings(user_data_dir=str(base), per_channel_profile=True),
    )
    args = captured[0]
    expected_path_prefix = f"--user-data-dir={base}"
    matches = [a for a in args if a.startswith(expected_path_prefix)]
    assert len(matches) == 1
    assert "youtube_" in matches[0]


def test_open_1f_per_channel_unparseable_url_falls_back_to_base(
    monkeypatch, tmp_path
) -> None:
    """Per-channel parser returns ``None`` for non-Twitch/non-YouTube URLs.
    The launcher must fall back to ``base_dir`` so the launch still succeeds
    instead of failing or silently using an empty path."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        "https://example.com/random-page",
        _settings(user_data_dir=str(base), per_channel_profile=True),
    )
    args = captured[0]
    assert f"--user-data-dir={base}" in args


def test_open_1g_app_mode_uses_app_flag(monkeypatch, tmp_path) -> None:
    """``app_mode=True`` → ``--app=URL`` AND the redundant ``--new-window``
    is suppressed (App Mode already opens a dedicated window)."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            app_mode=True,
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    args = captured[0]
    assert "--app=https://example.com" in args
    assert "--new-window" not in args


def test_open_1h_new_window_without_app_mode(monkeypatch) -> None:
    """``new_window=True, app_mode=False`` → ``--new-window`` and the URL
    positional argument (no ``--app=``)."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(new_window=True, app_mode=False),
    )
    args = captured[0]
    assert "--new-window" in args
    assert not any(a.startswith("--app=") for a in args)


def test_open_1i_apply_geometry_off_suppresses_window_flags(monkeypatch) -> None:
    """``apply_geometry=False`` → no ``--window-position`` / ``--window-size``
    even when x/y/width/height are set. Lets users keep custom values
    handy for quick re-enable without surfacing them to Chrome."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(apply_geometry=False, x=200, y=200, width=600, height=400),
    )
    args = captured[0]
    assert not any(a.startswith("--window-position") for a in args)
    assert not any(a.startswith("--window-size") for a in args)


def test_open_1j_firefox_dedicated_profile(monkeypatch, tmp_path) -> None:
    """Firefox: ``-profile <path> -no-remote`` are required to actually open
    the dedicated profile rather than IPC-forwarding to a running Firefox."""
    _stub_firefox(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    profile = tmp_path / "ff_profile"
    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            browser_path="firefox",
            user_data_dir=str(profile),
            per_channel_profile=False,
        ),
    )
    args = captured[0]
    assert "-profile" in args
    assert str(profile) in args
    assert "-no-remote" in args
    # Firefox cannot honour Chromium-only flags — none of these should appear.
    assert not any(a.startswith("--app=") for a in args)
    assert not any(a.startswith("--window-position") for a in args)
    assert not any(a.startswith("--user-data-dir") for a in args)


def test_open_1k_popen_failure_falls_back_to_webbrowser(monkeypatch) -> None:
    """``FileNotFoundError`` from Popen → webbrowser.open is tried so users
    still get *something* even when the configured browser is missing."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("nope")),
    )
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    assert notifier.open_url("https://example.com", _settings()) is True
    assert opened == [("https://example.com", 2)]


# ---------------------------------------------------------------------------
# Group 2 — Win32 post-launch window management
# ---------------------------------------------------------------------------
#
# Cross-cutting question: when does the post-launch worker fire (and when
# does it correctly stay silent)?
#
#   wm_2a   Windows + isolation                → worker fires
#   wm_2b   Windows + no isolation             → worker stays silent (safety)
#   wm_2c   non-Windows + any settings         → worker stays silent
#   wm_2d   Windows + new_window=False         → worker stays silent
#                                                (no new HWND to manage)
#   wm_2e   Windows + isolation + app_mode     → worker fires
#                                                (App Mode opens a new HWND)


def test_wm_2a_isolation_fires_post_launch_worker(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    manager_calls: list[Any] = []
    _stub_win32(monkeypatch, manager_calls=manager_calls)
    _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    assert len(manager_calls) == 1


def test_wm_2b_no_isolation_geometry_only_fixup(monkeypatch) -> None:
    """Shared profile + App Mode: geometry fixup runs; full management does not."""
    _stub_chrome(monkeypatch)
    manager_calls: list[Any] = []
    _stub_win32(monkeypatch, manager_calls=manager_calls)
    _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            app_mode=True,
            minimized=True,
            hide_from_taskbar=True,
            apply_geometry=True,
            x=100,
            y=100,
            width=800,
            height=600,
            close_on_offline=True,
            close_off_topic_pages=True,
            user_data_dir="",
            per_channel_profile=False,
        ),
    )
    assert len(manager_calls) == 1
    _args, kwargs = manager_calls[0]
    assert kwargs["track_for_url"] == ""
    assert kwargs["apply_geometry"] is True
    assert _args[2]["minimized"] is False
    assert _args[2]["hide_from_taskbar"] is False


def test_wm_2c_non_windows_skips_post_launch_worker(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    manager_calls: list[Any] = []
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *args, **kwargs: manager_calls.append((args, kwargs)),
    )
    _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            app_mode=True,
            minimized=True,
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    assert manager_calls == []


def test_wm_2d_new_window_false_skips_post_launch_worker(
    monkeypatch, tmp_path
) -> None:
    """``new_window=False`` AND ``app_mode=False`` → URL opens as a tab in an
    existing window; there's no new HWND to manage, so the worker correctly
    stays out of it (even though we *are* on Windows with isolation)."""
    _stub_chrome(monkeypatch)
    manager_calls: list[Any] = []
    _stub_win32(monkeypatch, manager_calls=manager_calls)
    _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            new_window=False,
            app_mode=False,
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    assert manager_calls == []


def test_wm_2e_app_mode_with_isolation_fires_worker(monkeypatch, tmp_path) -> None:
    """``app_mode=True`` always implies a new dedicated HWND, even with
    ``new_window=False``. The worker must fire to apply geometry / minimize."""
    _stub_chrome(monkeypatch)
    manager_calls: list[Any] = []
    _stub_win32(monkeypatch, manager_calls=manager_calls)
    _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            new_window=False,
            app_mode=True,
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    assert len(manager_calls) == 1


# ---------------------------------------------------------------------------
# Group 3 — Close-on-offline + title-fallback safeguard
# ---------------------------------------------------------------------------
#
#   close_3a  isolation: tracked HWND → WM_CLOSE goes to it
#   close_3b  isolation: no tracked HWND → falls back to title keyword
#   close_3c  no isolation: open registers URL in fallback-blocked set,
#             so the title keyword fallback is REFUSED to prevent
#             accidentally closing unrelated browser windows whose title
#             contains the channel name.
#   close_3d  the fallback block is one-shot — once consumed (close was
#             called) the URL no longer suppresses the fallback for a
#             future open/close cycle (otherwise reopening the same URL
#             with isolation would inherit the suppression forever).


def test_close_3a_isolation_closes_tracked_hwnd_directly(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )
    fallback_calls: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: fallback_calls.append(kws) or set(),
    )

    notifier._register_tracked_hwnd("https://x", 42)
    result = notifier.close_browser_window_for_url(
        "https://x", title_keywords=["something"]
    )

    assert result == 1
    assert closed == [42]
    # Tracker hit means we never reach the unsafe title fallback.
    assert fallback_calls == []


def test_close_3b_isolation_falls_back_to_title_when_tracker_misses(
    monkeypatch,
) -> None:
    """When the user opens a URL via the dedicated browser but Chrome
    consolidates the launch into an existing window (rare cold-vs-hot
    cache race), we have no tracked HWND. Title keyword fallback is the
    last line of defence and IS allowed in this case."""
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )
    monkeypatch.setattr(
        notifier, "_find_hwnds_by_title_keyword", lambda _kws: {777}
    )

    # No tracked HWND, no fallback-block → fallback fires.
    result = notifier.close_browser_window_for_url(
        "https://x", title_keywords=["channel"]
    )
    assert result == 1
    assert closed == [777]


def test_close_3c_no_isolation_blocks_title_fallback(monkeypatch) -> None:
    """The crucial regression guard: shared-profile launch registers the
    URL in ``_TITLE_FALLBACK_BLOCKED_URLS``. When the channel later goes
    offline and the close path finds no tracked HWND (because we
    deliberately didn't track in shared mode), the title-keyword fallback
    is REFUSED — otherwise we'd close any user window whose title
    happens to contain the channel name (e.g. an open Twitter tab).
    """
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    # Open in shared mode → should register fallback block.
    notifier._open_with_browser_settings(
        "https://www.twitch.tv/kaicenat",
        _settings(close_on_offline=True),
    )
    assert "https://www.twitch.tv/kaicenat" in notifier._TITLE_FALLBACK_BLOCKED_URLS

    # Now try to close — fallback would otherwise nuke arbitrary windows.
    fallback_calls: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: fallback_calls.append(kws) or {999},
    )
    monkeypatch.setattr(notifier, "_post_close_window", lambda _hwnd: True)

    result = notifier.close_browser_window_for_url(
        "https://www.twitch.tv/kaicenat", title_keywords=["kaicenat"]
    )
    assert result == 0
    assert fallback_calls == []  # critical: fallback was never invoked
    # Block is consumed (one-shot) so subsequent opens are not penalised.
    assert "https://www.twitch.tv/kaicenat" not in notifier._TITLE_FALLBACK_BLOCKED_URLS


def test_close_3d_isolation_reopen_unblocks_title_fallback(
    monkeypatch, tmp_path
) -> None:
    """After a shared-profile open + close cycle (block consumed), a
    *subsequent* open with isolation must not inherit the suppression.
    Verifies the open path unblocks even after a stale block from a prior
    settings configuration."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *args, **kwargs: None,
    )
    _capture_popen(monkeypatch)

    # 1st open: shared profile → fallback blocked
    notifier._open_with_browser_settings(
        "https://x.com", _settings(user_data_dir="", per_channel_profile=False)
    )
    assert "https://x.com" in notifier._TITLE_FALLBACK_BLOCKED_URLS

    # 2nd open: dedicated profile → fallback unblocked
    notifier._open_with_browser_settings(
        "https://x.com",
        _settings(
            user_data_dir=str(tmp_path / "profile"),
            per_channel_profile=False,
        ),
    )
    assert "https://x.com" not in notifier._TITLE_FALLBACK_BLOCKED_URLS


# ---------------------------------------------------------------------------
# Group 4 — Close-on-stop (user-initiated)
# ---------------------------------------------------------------------------
#
# ``close_all_tracked_windows`` is what the "Stop" button calls when
# ``close_on_stop=True``. It must:
#   * Close every HWND across every tracked URL.
#   * Clear the registry afterwards so future opens start clean.
#   * Be a no-op on non-Windows.


def test_stop_4a_closes_every_tracked_url(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )

    notifier._register_tracked_hwnd("https://a", 100)
    notifier._register_tracked_hwnd("https://a", 101)
    notifier._register_tracked_hwnd("https://b", 200)

    count = notifier.close_all_tracked_windows()

    assert count == 3
    assert set(closed) == {100, 101, 200}
    assert notifier.tracked_hwnds_for_url("https://a") == set()
    assert notifier.tracked_hwnds_for_url("https://b") == set()


def test_stop_4b_no_op_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    notifier._register_tracked_hwnd("https://a", 100)
    assert notifier.close_all_tracked_windows() == 0


def test_stop_4c_does_not_touch_untracked_urls(monkeypatch) -> None:
    """Stop-close is HWND-only — it never falls back to title-keyword
    matching. This is the right design: even users who turned on
    ``close_on_stop`` for the dedicated-profile use case should not have
    arbitrary windows closed when they hit Stop in shared-profile mode."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    # Shared mode open: nothing is tracked.
    notifier._open_with_browser_settings(
        "https://example.com", _settings(close_on_stop=True)
    )
    fallback_calls: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: fallback_calls.append(kws) or {999},
    )

    # Nothing to close — and crucially, no title-keyword scan is attempted.
    assert notifier.close_all_tracked_windows() == 0
    assert fallback_calls == []


# ---------------------------------------------------------------------------
# Group 5 — Off-topic prune
# ---------------------------------------------------------------------------
#
#   prune_5a  title still matches → window kept
#   prune_5b  title diverged + past grace → closed + untracked
#   prune_5c  title diverged + within grace → kept (Loading… phase)
#   prune_5d  no tracked URLs (shared mode) → no-op


def test_prune_5a_keeps_window_when_title_still_matches(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_post_close_window", lambda _: True)

    user32 = type("U32", (), {})()
    user32.IsWindow = lambda _hwnd: 1
    monkeypatch.setattr(
        notifier,
        "_get_window_title",
        lambda _u32, _h: "Kaicenat - Twitch",
    )

    import ctypes

    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"user32": user32}), raising=False
    )

    notifier._register_tracked_hwnd(
        "https://t.tv/kaicenat", 42, keywords=["kaicenat"]
    )
    # Age the tracked entry past the grace period.
    notifier._TRACKED_WINDOWS_BY_URL["https://t.tv/kaicenat"][0].opened_at -= 30

    assert notifier.prune_off_topic_tracked_windows() == 0
    # Tracking stays intact for later off-topic checks.
    assert notifier.tracked_hwnds_for_url("https://t.tv/kaicenat") == {42}


def test_prune_5b_closes_window_that_lost_all_keywords(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )

    user32 = type("U32", (), {})()
    user32.IsWindow = lambda _hwnd: 1
    monkeypatch.setattr(
        notifier,
        "_get_window_title",
        lambda _u32, _h: "Some random page",
    )

    import ctypes

    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"user32": user32}), raising=False
    )

    notifier._register_tracked_hwnd(
        "https://t.tv/kaicenat", 42, keywords=["kaicenat"]
    )
    notifier._TRACKED_WINDOWS_BY_URL["https://t.tv/kaicenat"][0].opened_at -= 30

    assert notifier.prune_off_topic_tracked_windows() == 1
    assert closed == [42]
    # HWND was untracked after the close.
    assert notifier.tracked_hwnds_for_url("https://t.tv/kaicenat") == set()


def test_prune_5c_respects_grace_period(monkeypatch) -> None:
    """During the first ``min_age_s`` seconds the title is allowed to drift
    (browser is still loading). Off-topic prune must NOT fire yet."""
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )
    user32 = type("U32", (), {})()
    user32.IsWindow = lambda _hwnd: 1
    monkeypatch.setattr(
        notifier, "_get_window_title", lambda _u32, _h: "Loading…"
    )
    import ctypes

    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"user32": user32}), raising=False
    )

    notifier._register_tracked_hwnd(
        "https://t.tv/kaicenat", 42, keywords=["kaicenat"]
    )
    # Fresh entry — opened_at is "now", which is well under the 6.0s grace.
    assert notifier.prune_off_topic_tracked_windows(min_age_s=6.0) == 0
    assert closed == []
    # Still tracked — we're waiting for the loaded title.
    assert notifier.tracked_hwnds_for_url("https://t.tv/kaicenat") == {42}


def test_prune_5d_shared_mode_no_op(monkeypatch) -> None:
    """In shared-profile mode the open path never registers HWNDs.
    ``prune_off_topic_tracked_windows`` iterates the tracker, so an empty
    tracker means a clean no-op — no chance of misfiring on the user's
    other browser windows."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(close_off_topic_pages=True),
    )
    assert notifier.prune_off_topic_tracked_windows() == 0


# ---------------------------------------------------------------------------
# Group 6 — Cookies / sign-in helper
# ---------------------------------------------------------------------------
#
#   signin_6a  Chromium: --user-data-dir + --new-window + URL,
#              **NO** --app / --window-position / --window-size / tracking
#   signin_6b  Firefox: -profile <path> -no-remote -new-instance
#   signin_6c  Empty / whitespace path → refuse to launch (no Popen)
#   signin_6d  Popen FileNotFoundError → returns False, no crash
#   signin_6e  Profile folder is created on the fly so the first sign-in
#              works even with a never-used path


def test_signin_6a_chromium_minimal_args(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    captured = _capture_popen(monkeypatch)

    profile = tmp_path / "for_login"
    assert notifier.open_browser_for_signin(str(profile)) is True

    args = captured[0]
    assert f"--user-data-dir={profile}" in args
    assert "--new-window" in args
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args
    assert args[-1] == notifier.SIGNIN_DEFAULT_URL
    # Critical negatives — these would re-introduce the bugs we just fixed.
    assert not any(a.startswith("--app=") for a in args)
    assert not any(a.startswith("--window-position") for a in args)
    assert not any(a.startswith("--window-size") for a in args)


def test_signin_6b_firefox_minimal_args(monkeypatch, tmp_path) -> None:
    _stub_firefox(monkeypatch)
    captured = _capture_popen(monkeypatch)

    profile = tmp_path / "for_login"
    assert (
        notifier.open_browser_for_signin(str(profile), browser_path="firefox")
        is True
    )

    args = captured[0]
    assert "-profile" in args
    assert str(profile) in args
    assert "-no-remote" in args
    assert "-new-instance" in args


def test_signin_6c_empty_path_refuses_to_launch(monkeypatch) -> None:
    popen_called: list[Any] = []
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: popen_called.append(a) or object(),
    )
    assert notifier.open_browser_for_signin("") is False
    assert notifier.open_browser_for_signin("   ") is False
    assert popen_called == []


def test_signin_6d_popen_failure_returns_false(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("nope")),
    )
    assert (
        notifier.open_browser_for_signin(str(tmp_path / "p")) is False
    )


def test_signin_6e_creates_nested_profile_dir(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    _capture_popen(monkeypatch)

    profile = tmp_path / "deeply" / "nested" / "profile"
    assert not profile.exists()
    assert notifier.open_browser_for_signin(str(profile)) is True
    assert profile.exists()


# ---------------------------------------------------------------------------
# Group 7 — Edge cases that have produced silent failures historically
# ---------------------------------------------------------------------------


def test_edge_7a_per_channel_creates_subdir_on_disk(monkeypatch, tmp_path) -> None:
    """Per-channel launch must create the channel-specific sub-folder
    *before* Popen so Chrome doesn't bail with a missing-profile error."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        "https://www.twitch.tv/somebody",
        _settings(user_data_dir=str(base), per_channel_profile=True),
    )
    subdir = base / "twitch_somebody"
    assert subdir.is_dir()


def test_edge_7b_browser_path_whitespace_uses_chrome(monkeypatch, tmp_path) -> None:
    """A whitespace-only ``browser_path`` is treated as empty → falls back
    to the resolved chrome executable. Caught at config-time too, but the
    runtime path must self-heal as well."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(browser_path="   ", user_data_dir=str(tmp_path / "p")),
    )
    assert captured[0][0] == "chrome"


def test_edge_7c_per_channel_with_unparseable_url_no_subdir(
    monkeypatch, tmp_path
) -> None:
    """An unparseable URL with per-channel ON must NOT create a phantom
    sub-folder named after the URL — fall back to the base path so the
    user doesn't end up with junk folders for every random test URL."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        "https://random.example.com/whatever",
        _settings(user_data_dir=str(base), per_channel_profile=True),
    )
    args = captured[0]
    assert f"--user-data-dir={base}" in args
    # No phantom sub-directories.
    if base.exists():
        assert list(base.iterdir()) == []


def test_edge_7d_app_mode_url_passed_verbatim(monkeypatch, tmp_path) -> None:
    """Whatever URL the user opens must reach Chrome's ``--app=`` flag
    verbatim — no URL-encoding, no scheme rewriting, no path manipulation."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    weird_url = "https://example.com/path?foo=bar&baz=1#frag"
    notifier._open_with_browser_settings(
        weird_url,
        _settings(
            app_mode=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert f"--app={weird_url}" in captured[0]


def test_edge_7e_empty_url_returns_false(monkeypatch) -> None:
    """An empty URL is a programmer error — refuse rather than open
    ``about:blank`` and confuse the user."""
    popen_called: list[Any] = []
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: popen_called.append(a) or object(),
    )
    assert notifier.open_url("", _settings()) is False
    assert popen_called == []


# ---------------------------------------------------------------------------
# Group 8 — Per-flag forwarding to the post-launch worker
# ---------------------------------------------------------------------------
#
# ``_open_with_browser_settings`` doesn't apply ``minimized`` /
# ``hide_from_taskbar`` / ``apply_geometry`` itself; it forwards the
# settings dict to ``_apply_new_browser_window_settings_async`` which
# inspects the values. Each test here verifies that exactly the right
# fields reach the worker, in the right shape.
#
#   fwd_8a   minimized=True + isolation     → worker receives minimized=True
#   fwd_8b   minimized=True + no isolation  → worker NEVER called
#   fwd_8c   hide_from_taskbar=True + iso   → worker receives it
#   fwd_8d   hide_from_taskbar=True + no iso → worker NEVER called
#   fwd_8e   apply_geometry=True + iso      → worker apply_geometry=True
#   fwd_8f   apply_geometry=False + iso     → worker apply_geometry=False
#   fwd_8g   x / y / width / height → forwarded verbatim
#   fwd_8h   close_on_offline=True → kw arg track_for_url set
#   fwd_8i   close_off_topic + title_hints → kw arg track_keywords set


def _capture_manager_args(monkeypatch) -> list[dict[str, Any]]:
    """Replace the post-launch worker with a recorder.

    Each invocation appends a dict with the keyword arguments and the
    forwarded settings snapshot so tests can assert against named fields
    rather than positional indices that drift when the signature changes.
    """
    calls: list[dict[str, Any]] = []

    def _capture(class_name, baseline, fwd_settings, **kwargs):
        calls.append(
            {
                "class_name": class_name,
                "baseline": baseline,
                "settings": dict(fwd_settings),
                **kwargs,
            }
        )

    monkeypatch.setattr(
        notifier, "_apply_new_browser_window_settings_async", _capture
    )
    return calls


def test_fwd_8a_minimized_forwarded_with_isolation(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            minimized=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert calls[0]["settings"]["minimized"] is True


def test_fwd_8b_minimized_dropped_without_isolation(monkeypatch) -> None:
    """Shared profile without App Mode: no worker path, so minimized is dropped."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(minimized=True, user_data_dir="", per_channel_profile=False),
    )
    assert calls == []


def test_fwd_8b2_geometry_only_strips_minimized_without_isolation(monkeypatch) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            app_mode=True,
            minimized=True,
            user_data_dir="",
            per_channel_profile=False,
        ),
    )
    assert len(calls) == 1
    assert calls[0]["settings"]["minimized"] is False
    assert calls[0]["track_for_url"] == ""


def test_fwd_8c_hide_from_taskbar_forwarded_with_isolation(
    monkeypatch, tmp_path
) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            hide_from_taskbar=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert calls[0]["settings"]["hide_from_taskbar"] is True


def test_fwd_8d_hide_from_taskbar_dropped_without_isolation(monkeypatch) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            hide_from_taskbar=True, user_data_dir="", per_channel_profile=False
        ),
    )
    assert calls == []


def test_fwd_8e_apply_geometry_true_propagates(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            apply_geometry=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert calls[0]["apply_geometry"] is True


def test_fwd_8f_apply_geometry_false_propagates(monkeypatch, tmp_path) -> None:
    """``apply_geometry=False`` must reach the worker so it doesn't apply
    leftover ``x/y/width/height`` from a previous configuration."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            apply_geometry=False,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert calls[0]["apply_geometry"] is False


def test_fwd_8g_geometry_values_passed_through(monkeypatch, tmp_path) -> None:
    """The four numeric fields must reach the worker verbatim."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://example.com",
        _settings(
            x=300,
            y=400,
            width=1600,
            height=900,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    fwd = calls[0]["settings"]
    assert (fwd["x"], fwd["y"], fwd["width"], fwd["height"]) == (300, 400, 1600, 900)


def test_fwd_8h_close_on_offline_implies_url_tracking(monkeypatch, tmp_path) -> None:
    """When isolation is available, the worker is told *which* URL to register
    HWNDs against — that's how the close-on-offline path knows what to close."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://www.twitch.tv/foo",
        _settings(
            close_on_offline=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
    )
    assert calls[0]["track_for_url"] == "https://www.twitch.tv/foo"


def test_fwd_8i_title_hints_reach_worker_for_off_topic_pruning(
    monkeypatch, tmp_path
) -> None:
    """The off-topic prune feature needs *keywords* attached to each tracked
    HWND. They're threaded through via ``title_hints``."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    notifier._open_with_browser_settings(
        "https://www.twitch.tv/foo",
        _settings(
            close_off_topic_pages=True,
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
        ),
        title_hints=("Foo Bar", "foobar", "Live!!!"),
    )
    assert calls[0]["track_keywords"] == ("Foo Bar", "foobar", "Live!!!")


# ---------------------------------------------------------------------------
# Group 9 — Action dispatch (execute_action)
# ---------------------------------------------------------------------------
#
# This is the boundary where Monitor (live-detection thread) hands off to
# the notifier. The four actions correspond 1:1 to the radio buttons in
# the main window. Each must:
#   * Show a toast (we stub it to count invocations).
#   * For open_*: call ``open_url`` with the URL + browser_settings +
#     title_hints derived from StreamInfo.
#   * For open_and_stop / open_and_exit: invoke their callback.
#
#   act_9a   action="open_and_stop"  → open + stop_fn called
#   act_9b   action="open_and_keep"  → open, no callback
#   act_9c   action="notify_only"    → no open at all
#   act_9d   action="open_and_exit"  → open + exit_fn called
#   act_9e   action_for_stream_status("open_and_stop", upcoming)  → "notify_only"
#   act_9f   action_for_stream_status("open_and_stop", video)     → None
#   act_9g   action_for_stream_status("open_and_stop", live)      → unchanged
#   act_9h   action helpers pass title_hints derived from StreamInfo
#   act_9i   action helpers pass browser_settings dict through verbatim


def _make_stream_info(**overrides: Any):
    """Build a minimal ``StreamInfo`` for action-dispatch tests."""
    from stream_monitor.fetcher.base import StreamInfo

    defaults = dict(
        channel="kaicenat",
        platform="twitch",
        is_live=True,
        title="LIVE NOW",
        url="https://www.twitch.tv/kaicenat",
        display_name="Kai Cenat",
    )
    defaults.update(overrides)
    return StreamInfo(**defaults)


def _stub_toast(monkeypatch) -> list[Any]:
    """Replace the cross-platform toast dispatcher with a no-op recorder."""
    toast_calls: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_toast",
        lambda info, with_open_button=True: toast_calls.append(
            (info.url, with_open_button)
        ),
    )
    return toast_calls


def test_act_9a_open_and_stop_opens_then_stops(monkeypatch) -> None:
    _stub_toast(monkeypatch)
    opened: list[tuple[str, dict, tuple]] = []
    stop_called: list[bool] = []

    def fake_open(url, settings=None, *, title_hints=None):
        opened.append((url, settings, tuple(title_hints or ())))
        return True

    monkeypatch.setattr(notifier, "open_url", fake_open)

    info = _make_stream_info()
    bs = _settings(enabled=True, close_on_stop=True)
    notifier.execute_action(
        "open_and_stop",
        info,
        stop_fn=lambda: stop_called.append(True),
        browser_settings=bs,
    )
    assert len(opened) == 1
    assert opened[0][0] == "https://www.twitch.tv/kaicenat"
    assert opened[0][1] is bs
    assert stop_called == [True]


def test_act_9b_open_and_keep_opens_no_callback(monkeypatch) -> None:
    _stub_toast(monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(
        notifier,
        "open_url",
        lambda url, settings=None, **kw: opened.append(url) or True,
    )
    # No stop_fn / exit_fn provided — must not raise.
    notifier.execute_action(
        "open_and_keep",
        _make_stream_info(),
        browser_settings=_settings(),
    )
    assert opened == ["https://www.twitch.tv/kaicenat"]


def test_act_9c_notify_only_does_not_open_browser(monkeypatch) -> None:
    """notify_only is the "show me the toast but don't auto-open" action.
    It MUST NOT call open_url under any circumstance — otherwise the
    "subscribe & be alerted" use case becomes auto-open by surprise."""
    _stub_toast(monkeypatch)
    open_called: list[Any] = []
    monkeypatch.setattr(
        notifier, "open_url", lambda *a, **k: open_called.append(a) or True
    )
    notifier.execute_action(
        "notify_only", _make_stream_info(), browser_settings=_settings()
    )
    assert open_called == []


def test_act_9d_open_and_exit_opens_then_exits(monkeypatch) -> None:
    _stub_toast(monkeypatch)
    opened: list[str] = []
    exit_called: list[bool] = []
    monkeypatch.setattr(
        notifier,
        "open_url",
        lambda url, settings=None, **kw: opened.append(url) or True,
    )

    notifier.execute_action(
        "open_and_exit",
        _make_stream_info(),
        exit_fn=lambda: exit_called.append(True),
        browser_settings=_settings(),
    )
    assert opened == ["https://www.twitch.tv/kaicenat"]
    assert exit_called == [True]


def test_act_9e_upcoming_stream_downgrades_to_notify_only() -> None:
    """For "upcoming" (scheduled-but-not-yet-live) streams, every action
    is forced to ``notify_only`` so we don't auto-open a not-yet-existing
    stream URL."""
    info = _make_stream_info(stream_status="upcoming", is_live=False)
    assert notifier.action_for_stream_status("open_and_stop", info) == "notify_only"
    assert notifier.action_for_stream_status("open_and_exit", info) == "notify_only"
    assert notifier.action_for_stream_status("open_and_keep", info) == "notify_only"


def test_act_9f_video_status_skips_action_entirely() -> None:
    """"video" status (YouTube uploaded a non-live video) means nothing
    should auto-trigger — ``None`` tells the caller "no action please"."""
    info = _make_stream_info(stream_status="video", is_live=False)
    assert notifier.action_for_stream_status("open_and_stop", info) is None
    assert notifier.action_for_stream_status("notify_only", info) is None


def test_act_9g_live_status_preserves_configured_action() -> None:
    info = _make_stream_info(stream_status="live", is_live=True)
    assert notifier.action_for_stream_status("open_and_stop", info) == "open_and_stop"
    assert notifier.action_for_stream_status("open_and_keep", info) == "open_and_keep"


def test_act_9h_action_helpers_thread_title_hints(monkeypatch) -> None:
    """The title_hints passed to open_url must include the channel slug,
    display name, and stream title — the three signals the off-topic
    prune feature uses to recognise our window later."""
    _stub_toast(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_open(url, settings=None, *, title_hints=None):
        captured["url"] = url
        captured["title_hints"] = tuple(title_hints or ())
        return True

    monkeypatch.setattr(notifier, "open_url", fake_open)

    info = _make_stream_info(
        channel="kaicenat",
        display_name="Kai Cenat",
        title="!!! LIVE NOW !!!",
    )
    notifier.execute_action(
        "open_and_keep", info, browser_settings=_settings()
    )
    assert "kaicenat" in captured["title_hints"]
    assert "Kai Cenat" in captured["title_hints"]
    assert "!!! LIVE NOW !!!" in captured["title_hints"]


def test_act_9i_action_helpers_pass_browser_settings_verbatim(
    monkeypatch,
) -> None:
    """The browser_settings dict reaches ``open_url`` *by reference* so any
    runtime mutation by the caller (e.g. close_on_stop toggle) is honoured."""
    _stub_toast(monkeypatch)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        notifier,
        "open_url",
        lambda url, settings=None, **kw: captured.setdefault("settings", settings)
        or True,
    )
    sentinel = _settings(close_on_offline=True, close_off_topic_pages=True)
    notifier.execute_action(
        "open_and_keep", _make_stream_info(), browser_settings=sentinel
    )
    assert captured["settings"] is sentinel


# ---------------------------------------------------------------------------
# Group 10 — Multi-URL scenarios (the real "I follow many channels" case)
# ---------------------------------------------------------------------------
#
# Each tracked URL has its own HWND bucket. Closing one URL must NOT touch
# the others. Off-topic prune iterates every URL.
#
#   multi_10a  3 URLs registered → each independently retrievable
#   multi_10b  Close URL A → URL B and C unaffected
#   multi_10c  close_all_tracked_windows → all URLs cleaned in one sweep
#   multi_10d  Per-channel produces a distinct subdir per Twitch channel
#   multi_10e  Same URL re-launched → second launch dedupes against
#              already-tracked HWND


def test_multi_10a_three_urls_register_independently() -> None:
    notifier._register_tracked_hwnd("https://a", 11)
    notifier._register_tracked_hwnd("https://b", 22)
    notifier._register_tracked_hwnd("https://c", 33)
    assert notifier.tracked_hwnds_for_url("https://a") == {11}
    assert notifier.tracked_hwnds_for_url("https://b") == {22}
    assert notifier.tracked_hwnds_for_url("https://c") == {33}


def test_multi_10b_close_one_url_leaves_others_intact(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda hwnd: closed.append(hwnd) or True
    )
    monkeypatch.setattr(notifier, "_find_hwnds_by_title_keyword", lambda _kw: set())

    notifier._register_tracked_hwnd("https://a", 11)
    notifier._register_tracked_hwnd("https://b", 22)
    notifier._register_tracked_hwnd("https://c", 33)

    notifier.close_browser_window_for_url("https://b")

    assert closed == [22]
    assert notifier.tracked_hwnds_for_url("https://a") == {11}
    assert notifier.tracked_hwnds_for_url("https://b") == set()
    assert notifier.tracked_hwnds_for_url("https://c") == {33}


def test_multi_10c_close_all_tracks_every_url(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda hwnd: closed.append(hwnd) or True
    )

    for url, hwnd in (("https://a", 11), ("https://b", 22), ("https://c", 33)):
        notifier._register_tracked_hwnd(url, hwnd)

    assert notifier.close_all_tracked_windows() == 3
    assert set(closed) == {11, 22, 33}
    for url in ("https://a", "https://b", "https://c"):
        assert notifier.tracked_hwnds_for_url(url) == set()


def test_multi_10d_per_channel_keeps_subdirs_distinct(monkeypatch, tmp_path) -> None:
    """Three channels triggering simultaneously must each get their own
    ``--user-data-dir`` sub-folder so Chrome spawns three independent
    master processes. Anything less and we're back to HWND contamination."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    base = tmp_path / "profiles"
    cfg = _settings(user_data_dir=str(base), per_channel_profile=True)

    for url in (
        "https://www.twitch.tv/alice",
        "https://www.twitch.tv/bob",
        "https://www.twitch.tv/carol",
    ):
        notifier._open_with_browser_settings(url, cfg)

    dirs = []
    for args in captured:
        for token in args:
            if token.startswith("--user-data-dir="):
                dirs.append(token[len("--user-data-dir=") :])
                break
    assert len(set(dirs)) == 3
    # All three must live under the base.
    for d in dirs:
        assert d.startswith(str(base))


def test_multi_10e_same_url_relaunch_dedupes_hwnd() -> None:
    """If the same URL is opened twice while the original window is still
    tracked, the registry deduplicates by HWND. Otherwise close-on-offline
    would attempt WM_CLOSE twice on the same HWND."""
    notifier._register_tracked_hwnd("https://x", 99)
    notifier._register_tracked_hwnd("https://x", 99)
    assert notifier.tracked_hwnds_for_url("https://x") == {99}
    tracked = notifier._snapshot_tracked_windows("https://x")
    assert len(tracked) == 1


# ---------------------------------------------------------------------------
# Group 11 — All-flags-on smoke tests
# ---------------------------------------------------------------------------
#
#   smoke_11a  Every advanced flag ON + isolation → opens, tracks, no crash
#   smoke_11b  Every advanced flag ON + no isolation → opens via degraded
#              path, no crash, no tracking
#   smoke_11c  enabled=False overrides every other flag → webbrowser path
#   smoke_11d  Firefox + every flag ON → opens via Firefox-friendly subset


def test_smoke_11a_all_flags_on_with_isolation(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    cfg = _settings(
        enabled=True,
        new_window=True,
        app_mode=True,
        apply_geometry=True,
        x=100,
        y=100,
        width=1024,
        height=768,
        minimized=True,
        hide_from_taskbar=True,
        close_on_offline=True,
        close_on_stop=True,
        close_off_topic_pages=True,
        user_data_dir=str(tmp_path / "all_on"),
        per_channel_profile=True,
    )

    assert (
        notifier._open_with_browser_settings(
            "https://www.twitch.tv/foo",
            cfg,
            title_hints=("foo", "Foo Streamer"),
        )
        is True
    )
    # Worker received the whole dict.
    fwd = calls[0]["settings"]
    for key in (
        "minimized",
        "hide_from_taskbar",
        "apply_geometry",
        "close_on_offline",
        "close_on_stop",
        "close_off_topic_pages",
    ):
        assert fwd[key] == cfg[key]
    assert calls[0]["track_for_url"] == "https://www.twitch.tv/foo"
    assert calls[0]["track_keywords"] == ("foo", "Foo Streamer")


def test_smoke_11b_all_flags_on_without_isolation_degrades_safely(
    monkeypatch,
) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    calls = _capture_manager_args(monkeypatch)

    cfg = _settings(
        enabled=True,
        app_mode=True,
        apply_geometry=True,
        minimized=True,
        hide_from_taskbar=True,
        close_on_offline=True,
        close_on_stop=True,
        close_off_topic_pages=True,
        user_data_dir="",
        per_channel_profile=False,
    )

    assert (
        notifier._open_with_browser_settings("https://example.com", cfg) is True
    )
    assert len(calls) == 1
    assert calls[0]["track_for_url"] == ""
    assert calls[0]["settings"]["minimized"] is False
    assert calls[0]["settings"]["hide_from_taskbar"] is False
    assert "https://example.com" in notifier._TITLE_FALLBACK_BLOCKED_URLS


def test_smoke_11c_enabled_false_overrides_everything(monkeypatch) -> None:
    """Even with every advanced flag ON, ``enabled=False`` short-circuits
    to ``webbrowser.open``. The custom-launch path is never entered."""
    popen_called: list[Any] = []
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: popen_called.append(a) or object(),
    )
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    cfg = _settings(
        enabled=False,  # the kill switch
        app_mode=True,
        minimized=True,
        close_on_offline=True,
        user_data_dir="/tmp/whatever",
        per_channel_profile=True,
    )
    assert notifier.open_url("https://example.com", cfg) is True
    assert popen_called == []
    assert opened == [("https://example.com", 2)]


def test_smoke_11d_firefox_all_flags_on(monkeypatch, tmp_path) -> None:
    """Firefox can only honour a *subset* of the flags. The launcher must
    silently strip the unsupported ones rather than crash."""
    _stub_firefox(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    captured = _capture_popen(monkeypatch)

    cfg = _settings(
        enabled=True,
        browser_path="firefox",
        new_window=True,
        app_mode=True,  # Firefox doesn't support this — must be dropped
        apply_geometry=True,
        x=100,
        y=100,
        width=1024,
        height=768,
        minimized=True,
        hide_from_taskbar=True,
        user_data_dir=str(tmp_path / "ff"),
        per_channel_profile=False,
    )

    assert (
        notifier._open_with_browser_settings("https://example.com", cfg) is True
    )
    args = captured[0]
    assert args[0] == "firefox"
    assert "-profile" in args
    assert "-no-remote" in args
    # Firefox-incompatible flags must be absent.
    assert not any(a.startswith("--app=") for a in args)
    assert not any(a.startswith("--window-position") for a in args)
    assert not any(a.startswith("--user-data-dir") for a in args)


# ---------------------------------------------------------------------------
# Group 12 — Full lifecycle (open → track → channel offline → close)
# ---------------------------------------------------------------------------
#
# These tests reach across modules: notifier's open path registers an
# HWND, then close path looks it up, then WM_CLOSE is sent (mocked).
# They prove the *contract* between the open and close flows holds for
# every profile-isolation mode.
#
#   life_12a  dedicated profile: open → track → close hits the HWND
#   life_12b  per-channel profile: open → track → close hits the HWND
#   life_12c  shared profile: open → NO tracking → close is a safe no-op
#   life_12d  enabled=False: open via webbrowser → close uses title fallback
#             (the documented fallback contract for webbrowser callers)


def _simulate_post_launch_tracking(url: str, hwnd: int) -> None:
    """Pretend the post-launch worker found exactly one new HWND for *url*
    and registered it. Lets the lifecycle tests skip the actual Win32
    polling without losing test fidelity."""
    notifier._register_tracked_hwnd(url, hwnd, keywords=("channel",))


def test_life_12a_dedicated_profile_open_then_close(monkeypatch, tmp_path) -> None:
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)

    # Capture the worker invocation so we know the open path *wanted* to
    # track (we then manually fake the tracker entry).
    worker_called: list[bool] = []
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: worker_called.append(True),
    )

    url = "https://www.twitch.tv/foo"
    notifier._open_with_browser_settings(
        url,
        _settings(
            user_data_dir=str(tmp_path / "p"),
            per_channel_profile=False,
            close_on_offline=True,
        ),
    )
    assert worker_called == [True]

    # Worker would have registered the HWND in real Windows — simulate it.
    _simulate_post_launch_tracking(url, 4242)

    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda h: closed.append(h) or True
    )
    monkeypatch.setattr(notifier, "_find_hwnds_by_title_keyword", lambda _kw: set())

    notifier.close_browser_window_for_url(url, title_keywords=["channel"])
    assert closed == [4242]


def test_life_12b_per_channel_open_then_close(monkeypatch, tmp_path) -> None:
    """Per-channel mode — different from dedicated in that each URL gets
    its own profile subdir, but otherwise the open/close contract is
    identical."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    captured = _capture_popen(monkeypatch)
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: None,
    )

    url = "https://www.twitch.tv/foo"
    base = tmp_path / "profiles"
    notifier._open_with_browser_settings(
        url,
        _settings(
            user_data_dir=str(base),
            per_channel_profile=True,
            close_on_offline=True,
        ),
    )
    # Verify the per-channel sub-folder name was actually used.
    args = captured[0]
    subdir = base / "twitch_foo"
    assert f"--user-data-dir={subdir}" in args

    _simulate_post_launch_tracking(url, 5151)

    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda h: closed.append(h) or True
    )
    monkeypatch.setattr(notifier, "_find_hwnds_by_title_keyword", lambda _kw: set())

    notifier.close_browser_window_for_url(url, title_keywords=["channel"])
    assert closed == [5151]


def test_life_12c_shared_profile_open_then_close_is_safe_noop(
    monkeypatch,
) -> None:
    """Shared-mode lifecycle: open registers the URL in
    ``_TITLE_FALLBACK_BLOCKED_URLS``, never tracks an HWND, and the
    eventual close finds nothing to do — without touching unrelated
    browser windows. This is the headline behaviour change."""
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _c: set())
    _capture_popen(monkeypatch)
    worker_called: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: worker_called.append(True),
    )

    url = "https://www.twitch.tv/foo"
    notifier._open_with_browser_settings(
        url, _settings(close_on_offline=True)
    )
    # No worker → no HWND tracking attempted.
    assert worker_called == []
    assert url in notifier._TITLE_FALLBACK_BLOCKED_URLS

    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda h: closed.append(h) or True
    )
    fallback_calls: list[Any] = []
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: fallback_calls.append(kws) or {888},
    )

    count = notifier.close_browser_window_for_url(
        url, title_keywords=["foo"]
    )
    assert count == 0
    assert closed == []
    assert fallback_calls == []  # the *whole* point of the block


def test_life_12d_disabled_open_via_webbrowser_close_uses_title_fallback(
    monkeypatch,
) -> None:
    """When ``enabled=False`` we hand the URL to ``webbrowser.open`` and
    never track. In that case the close path's title-keyword fallback IS
    the only signal we have — it must NOT be blocked. (The block only
    fires when we deliberately launched in shared mode.)"""
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(
        notifier.webbrowser, "open", lambda *a, **k: True
    )

    url = "https://example.com/page"
    assert notifier.open_url(url, _settings(enabled=False)) is True
    # No block was registered — disabled mode skips the custom launch
    # path entirely and never touches the block set.
    assert url not in notifier._TITLE_FALLBACK_BLOCKED_URLS

    closed: list[int] = []
    monkeypatch.setattr(
        notifier, "_post_close_window", lambda h: closed.append(h) or True
    )
    monkeypatch.setattr(
        notifier, "_find_hwnds_by_title_keyword", lambda _kw: {7777}
    )

    count = notifier.close_browser_window_for_url(
        url, title_keywords=["page"]
    )
    assert count == 1
    assert closed == [7777]


# ---------------------------------------------------------------------------
# Group 13 - Exhaustive 192-cell smoke matrix
# ---------------------------------------------------------------------------
#
# Profile mode (4) x window mode (3) x close flags (2^3) x geometry (2).
# This is intentionally broader than the named scenario tests above: each cell
# opens through the real notifier entry point, then independently verifies the
# close-on-offline, close-on-stop, and off-topic-prune contracts when the
# relevant flag is enabled.

_PROFILE_MATRIX = ("disabled", "shared", "dedicated", "per_channel")
_WINDOW_MATRIX = ("app", "new_window", "tab")
_CLOSE_FLAG_NAMES = (
    "close_on_offline",
    "close_on_stop",
    "close_off_topic_pages",
)
_CLOSE_FLAG_MATRIX = tuple(product((False, True), repeat=len(_CLOSE_FLAG_NAMES)))
_EXHAUSTIVE_MATRIX = tuple(
    product(_PROFILE_MATRIX, _WINDOW_MATRIX, _CLOSE_FLAG_MATRIX, (False, True))
)


def _matrix_case_id(case: tuple[str, str, tuple[bool, ...], bool]) -> str:
    profile_mode, window_mode, close_bits, apply_geometry = case
    close_id = "".join("1" if value else "0" for value in close_bits)
    return f"{profile_mode}-{window_mode}-close{close_id}-geom{int(apply_geometry)}"


def _matrix_settings(
    profile_mode: str,
    window_mode: str,
    close_bits: tuple[bool, ...],
    apply_geometry: bool,
    tmp_path,
) -> dict[str, Any]:
    cfg = _settings(
        enabled=profile_mode != "disabled",
        app_mode=window_mode == "app",
        new_window=window_mode == "new_window",
        apply_geometry=apply_geometry,
        **dict(zip(_CLOSE_FLAG_NAMES, close_bits)),
    )
    if profile_mode == "dedicated":
        cfg["user_data_dir"] = str(tmp_path / "dedicated")
        cfg["per_channel_profile"] = False
    elif profile_mode == "per_channel":
        cfg["user_data_dir"] = str(tmp_path / "per_channel")
        cfg["per_channel_profile"] = True
    else:
        cfg["user_data_dir"] = ""
        cfg["per_channel_profile"] = False
    return cfg


@pytest.mark.parametrize(
    ("profile_mode", "window_mode", "close_bits", "apply_geometry"),
    _EXHAUSTIVE_MATRIX,
    ids=[_matrix_case_id(case) for case in _EXHAUSTIVE_MATRIX],
)
def test_exhaustive_13_every_browser_settings_combination_runs_cleanly(
    monkeypatch,
    tmp_path,
    profile_mode: str,
    window_mode: str,
    close_bits: tuple[bool, ...],
    apply_geometry: bool,
) -> None:
    """Run every reachable Browser Settings combination through open/close.

    The browser process and Win32 APIs are still stubbed, but this is not a
    paper-only table: every cell invokes ``open_url`` and the same close helpers
    the app uses for offline events, Stop, and off-topic pruning.
    """
    _stub_chrome(monkeypatch)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_enum_browser_hwnds", lambda _class: set())

    import ctypes

    user32 = type("U32", (), {})()
    user32.IsWindow = lambda _hwnd: 1
    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"user32": user32}), raising=False
    )
    monkeypatch.setattr(
        notifier, "_get_window_title", lambda _u32, _h: "Unrelated page"
    )

    launched: list[list[str]] = []
    opened: list[tuple[str, int]] = []
    closed: list[int] = []
    fallback_calls: list[tuple[str, ...]] = []
    fallback_hwnd = 990001
    settings = _matrix_settings(
        profile_mode, window_mode, close_bits, apply_geometry, tmp_path
    )
    close_flags = dict(zip(_CLOSE_FLAG_NAMES, close_bits))

    def fake_popen(args, **_kw):
        launched.append(list(args))

        class _Proc:
            pass

        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: closed.append(hwnd) or True,
    )
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda keywords: fallback_calls.append(tuple(keywords)) or {fallback_hwnd},
    )

    custom_enabled = profile_mode != "disabled"
    isolated = profile_mode in {"dedicated", "per_channel"}
    expects_tracking = custom_enabled and isolated and window_mode in {
        "app",
        "new_window",
    }
    expects_geometry_only = (
        custom_enabled
        and not isolated
        and window_mode == "app"
        and apply_geometry
    )

    open_index = 0

    def open_cycle(suffix: str) -> tuple[str, int]:
        nonlocal open_index
        _reset_tracked_hwnds()
        launched.clear()
        opened.clear()
        closed.clear()
        fallback_calls.clear()

        worker_calls: list[dict[str, Any]] = []
        hwnd = 880000 + open_index
        open_index += 1
        channel = f"matrixchannel{suffix}"
        url = f"https://www.twitch.tv/{channel}"

        def fake_worker(
            class_name,
            baseline,
            worker_settings,
            *,
            apply_geometry=True,
            deadline_s=0,
            track_for_url="",
            track_keywords=(),
            foreground_hold_seconds=0,
        ):
            worker_calls.append(
                {
                    "class_name": class_name,
                    "baseline": set(baseline),
                    "settings": dict(worker_settings),
                    "apply_geometry": apply_geometry,
                    "deadline_s": deadline_s,
                    "track_for_url": track_for_url,
                    "track_keywords": tuple(track_keywords),
                    "foreground_hold_seconds": foreground_hold_seconds,
                }
            )
            if track_for_url:
                notifier._register_tracked_hwnd(
                    track_for_url, hwnd, keywords=tuple(track_keywords)
                )
            return None

        monkeypatch.setattr(
            notifier, "_apply_new_browser_window_settings_async", fake_worker
        )

        assert notifier.open_url(url, settings, title_hints=(channel,)) is True

        if not custom_enabled:
            assert launched == []
            assert opened == [(url, 2)]
            assert worker_calls == []
            assert notifier.tracked_hwnds_for_url(url) == set()
            assert url not in notifier._TITLE_FALLBACK_BLOCKED_URLS
            return url, hwnd

        assert opened == []
        assert len(launched) == 1
        args = launched[0]
        assert args[0] == "chrome"

        profile_args = [
            token for token in args if token.startswith("--user-data-dir=")
        ]
        if profile_mode == "shared":
            assert profile_args == []
        elif profile_mode == "dedicated":
            expected_dir = tmp_path / "dedicated"
            assert profile_args == [f"--user-data-dir={expected_dir}"]
            assert expected_dir.exists()
        elif profile_mode == "per_channel":
            expected_dir = tmp_path / "per_channel" / f"twitch_{channel}"
            assert profile_args == [f"--user-data-dir={expected_dir}"]
            assert expected_dir.exists()

        has_app = any(token == f"--app={url}" for token in args)
        has_new_window = "--new-window" in args
        has_position = any(token.startswith("--window-position=") for token in args)
        has_size = any(token.startswith("--window-size=") for token in args)
        should_emit_geometry = apply_geometry and window_mode in {
            "app",
            "new_window",
        }

        assert has_app is (window_mode == "app")
        assert has_new_window is (window_mode == "new_window")
        assert has_position is should_emit_geometry
        assert has_size is should_emit_geometry
        if window_mode != "app":
            assert args[-1] == url

        if expects_tracking:
            assert len(worker_calls) == 1
            assert worker_calls[0]["track_for_url"] == url
            assert worker_calls[0]["track_keywords"] == (channel,)
            assert worker_calls[0]["apply_geometry"] is apply_geometry
            assert notifier.tracked_hwnds_for_url(url) == {hwnd}
            assert url not in notifier._TITLE_FALLBACK_BLOCKED_URLS
        elif expects_geometry_only:
            assert len(worker_calls) == 1
            assert worker_calls[0]["track_for_url"] == ""
            assert worker_calls[0]["apply_geometry"] is apply_geometry
            assert notifier.tracked_hwnds_for_url(url) == set()
            wants_close_or_hide_mgmt = (
                close_flags["close_on_offline"]
                or close_flags["close_off_topic_pages"]
                or settings.get("minimized")
                or settings.get("hide_from_taskbar")
            )
            if wants_close_or_hide_mgmt:
                assert url in notifier._TITLE_FALLBACK_BLOCKED_URLS
            else:
                assert url not in notifier._TITLE_FALLBACK_BLOCKED_URLS
        else:
            assert worker_calls == []
            assert notifier.tracked_hwnds_for_url(url) == set()
            if profile_mode == "shared":
                assert url in notifier._TITLE_FALLBACK_BLOCKED_URLS
            else:
                assert url not in notifier._TITLE_FALLBACK_BLOCKED_URLS

        return url, hwnd

    # Baseline open assertion for the cell, independent of close flags.
    open_cycle("open")

    # close_on_offline: app calls close_browser_window_for_url only when set.
    url, hwnd = open_cycle("offline")
    if close_flags["close_on_offline"]:
        count = notifier.close_browser_window_for_url(
            url, title_keywords=["matrixchanneloffline"]
        )
        if expects_tracking:
            assert count == 1
            assert closed == [hwnd]
            assert fallback_calls == []
        elif profile_mode == "shared":
            assert count == 0
            assert closed == []
            assert fallback_calls == []
        else:
            assert count == 1
            assert closed == [fallback_hwnd]
            assert fallback_calls == [("matrixchanneloffline",)]
    else:
        assert closed == []
        assert fallback_calls == []

    # close_on_stop: Stop uses close_all_tracked_windows, never title fallback.
    url, hwnd = open_cycle("stop")
    if close_flags["close_on_stop"]:
        count = notifier.close_all_tracked_windows()
        assert count == (1 if expects_tracking else 0)
        assert closed == ([hwnd] if expects_tracking else [])
        assert notifier.tracked_hwnds_for_url(url) == set()
    else:
        assert closed == []

    # close_off_topic_pages: prune only touches tracked HWNDs.
    url, hwnd = open_cycle("prune")
    if close_flags["close_off_topic_pages"]:
        count = notifier.prune_off_topic_tracked_windows(min_age_s=0.0)
        assert count == (1 if expects_tracking else 0)
        assert closed == ([hwnd] if expects_tracking else [])
        assert notifier.tracked_hwnds_for_url(url) == set()
    else:
        assert closed == []
