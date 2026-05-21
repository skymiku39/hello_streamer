from pathlib import Path
from unittest.mock import MagicMock

from stream_monitor import notifier
from stream_monitor.fetcher.base import StreamInfo


def _info(**kwargs) -> StreamInfo:
    defaults = dict(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live now",
        url="https://www.twitch.tv/hello",
    )
    defaults.update(kwargs)
    return StreamInfo(**defaults)


# ─────────────────────────────────────────────
# Action dispatch (unchanged behaviour)
# ─────────────────────────────────────────────
def test_execute_open_and_stop_opens_browser_and_stops(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        notifier,
        "_toast",
        lambda _info, with_open_button=True: events.append(("toast", with_open_button)),
    )
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: events.append(("open", url)),
    )

    notifier.execute_action(
        "open_and_stop",
        _info(),
        stop_fn=lambda: events.append(("stop", None)),
    )

    assert events == [
        ("toast", False),
        ("open", "https://www.twitch.tv/hello"),
        ("stop", None),
    ]


def test_execute_open_and_keep_opens_without_stopping(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        notifier,
        "_toast",
        lambda _info, with_open_button=True: events.append(("toast", with_open_button)),
    )
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: events.append(("open", url)),
    )

    notifier.execute_action("open_and_keep", _info())

    assert events == [
        ("toast", False),
        ("open", "https://www.twitch.tv/hello"),
    ]


def test_execute_notify_only_shows_open_button(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        notifier,
        "_toast",
        lambda _info, with_open_button=True: events.append(("toast", with_open_button)),
    )

    notifier.execute_action("notify_only", _info())

    assert events == [("toast", True)]


def test_execute_open_and_exit_uses_exit_callback(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        notifier,
        "_toast",
        lambda _info, with_open_button=True: events.append(("toast", with_open_button)),
    )
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: events.append(("open", url)),
    )

    notifier.execute_action(
        "open_and_exit",
        _info(),
        exit_fn=lambda: events.append(("exit", None)),
    )

    assert events == [
        ("toast", False),
        ("open", "https://www.twitch.tv/hello"),
        ("exit", None),
    ]


def test_open_url_falls_back_to_windows_shell(monkeypatch) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(notifier.webbrowser, "open", lambda url, new=0: False)
    monkeypatch.setattr(notifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        notifier.os,
        "startfile",
        lambda url: events.append(("startfile", url)),
        raising=False,
    )

    assert notifier.open_url("https://www.twitch.tv/hello") is True
    assert events == [("startfile", "https://www.twitch.tv/hello")]


# ─────────────────────────────────────────────
# Toast text varies by stream_status
# ─────────────────────────────────────────────
def test_format_scheduled_start() -> None:
    result = notifier._format_scheduled_start("2026-05-06T12:00:00+00:00")
    assert result != ""
    assert "2026" in result


def test_format_scheduled_start_empty() -> None:
    assert notifier._format_scheduled_start("") == ""


def test_format_scheduled_start_invalid() -> None:
    result = notifier._format_scheduled_start("not-a-date")
    assert isinstance(result, str)


# ─────────────────────────────────────────────
# Status-aware action routing
# ─────────────────────────────────────────────
def test_action_for_live_uses_configured_action() -> None:
    assert (
        notifier.action_for_stream_status(
            "open_and_stop", _info(stream_status="live")
        )
        == "open_and_stop"
    )


def test_action_for_upcoming_forces_notify_only() -> None:
    assert (
        notifier.action_for_stream_status(
            "open_and_exit", _info(stream_status="upcoming", is_live=False)
        )
        == "notify_only"
    )


def test_action_for_video_returns_none() -> None:
    assert (
        notifier.action_for_stream_status(
            "open_and_keep", _info(stream_status="video", is_live=False)
        )
        is None
    )


# ─────────────────────────────────────────────
# Browser-settings driven open_url
# ─────────────────────────────────────────────
def test_build_browser_args_default_window() -> None:
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "custom-browser",
            "new_window": True,
            "app_mode": False,
            "x": 10,
            "y": 20,
            "width": 800,
            "height": 600,
        },
    )
    assert args == [
        "custom-browser",
        "--new-window",
        "--window-position=10,20",
        "--window-size=800,600",
        "https://example.com",
    ]


def test_build_browser_args_app_mode_replaces_url() -> None:
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "custom-browser",
            "new_window": False,
            "app_mode": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )
    assert "--new-window" not in args
    assert "--app=https://example.com" in args
    assert "https://example.com" not in args
    assert args[0] == "custom-browser"


def test_build_browser_args_resolves_windows_chrome_alias(
    tmp_path, monkeypatch
) -> None:
    program_files = tmp_path / "Program Files"
    chrome = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")

    monkeypatch.setattr(notifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(notifier.shutil, "which", lambda _value: None)
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": False,
            "app_mode": False,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )

    assert args[0] == str(chrome)


def test_build_browser_args_strips_quoted_explicit_browser_path(
    tmp_path, monkeypatch
) -> None:
    browser = tmp_path / "Browser With Spaces" / "browser.exe"
    browser.parent.mkdir()
    browser.write_text("", encoding="utf-8")
    monkeypatch.setattr(notifier.shutil, "which", lambda _value: None)

    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": f'"{browser}"',
            "new_window": False,
            "app_mode": False,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )

    assert args[0] == str(browser)


def test_open_url_uses_browser_settings_when_enabled(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_popen(args, **kwargs):
        calls.append(args)

        class _Proc:
            pass

        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(notifier.webbrowser, "open", lambda *_a, **_k: True)

    settings = {
        "enabled": True,
        "browser_path": "custom-browser",
        "new_window": True,
        "app_mode": False,
        "x": 5,
        "y": 6,
        "width": 1024,
        "height": 768,
        "minimized": False,
    }

    assert notifier.open_url("https://example.com", settings) is True
    assert calls == [
        [
            "custom-browser",
            "--new-window",
            "--window-position=5,6",
            "--window-size=1024,768",
            "https://example.com",
        ]
    ]


def test_open_url_falls_back_to_webbrowser_when_disabled(monkeypatch) -> None:
    popen_called = []
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *a, **k: popen_called.append(a) or None,
    )
    opened = []
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    assert notifier.open_url("https://example.com", {"enabled": False}) is True
    assert popen_called == []
    assert opened == [("https://example.com", 2)]


def test_open_url_subprocess_failure_falls_back(monkeypatch) -> None:
    def fail_popen(*_a, **_k):
        raise FileNotFoundError("no chrome")

    opened = []
    monkeypatch.setattr(notifier.subprocess, "Popen", fail_popen)
    monkeypatch.setattr(
        notifier.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    settings = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "x": 0,
        "y": 0,
        "width": 800,
        "height": 600,
        "minimized": False,
    }
    assert notifier.open_url("https://example.com", settings) is True
    assert opened == [("https://example.com", 2)]


def test_execute_open_and_keep_passes_browser_settings(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_toast", lambda *_a, **_k: None)

    captured: dict[str, object] = {}

    def fake_open(url, browser_settings=None, **kwargs):
        captured["url"] = url
        captured["settings"] = browser_settings
        captured["kwargs"] = kwargs
        return True

    monkeypatch.setattr(notifier, "open_url", fake_open)

    settings = {"enabled": True, "browser_path": "chrome"}
    notifier.execute_action("open_and_keep", _info(), browser_settings=settings)

    assert captured["url"] == "https://www.twitch.tv/hello"
    assert captured["settings"] is settings
    # title_hints should be threaded through so the off-topic prune feature
    # can identify our window later. Channel slug "hello" plus stream title
    # "Live now" are both present.
    hints = captured["kwargs"].get("title_hints", ())
    assert "hello" in hints
    assert "Live now" in hints


# ─────────────────────────────────────────────
# Browser-family aware flag handling
# ─────────────────────────────────────────────
def _stub_browser_resolution(monkeypatch, exe: str = "chrome") -> None:
    """Force ``_resolve_browser_executable`` to echo back a fixed exe path."""
    monkeypatch.setattr(notifier, "_resolve_browser_executable", lambda _value: exe)


def test_detect_browser_family() -> None:
    assert notifier.detect_browser_family("chrome") == "chromium"
    assert notifier.detect_browser_family("chrome.exe") == "chromium"
    assert (
        notifier.detect_browser_family(
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
        == "chromium"
    )
    assert notifier.detect_browser_family("msedge.exe") == "chromium"
    assert notifier.detect_browser_family("brave.exe") == "chromium"
    assert notifier.detect_browser_family("firefox") == "firefox"
    assert notifier.detect_browser_family("firefox.exe") == "firefox"
    assert notifier.detect_browser_family("/usr/bin/firefox") == "firefox"
    assert notifier.detect_browser_family("safari") == "unknown"


def test_build_browser_args_app_mode_drops_redundant_new_window(monkeypatch) -> None:
    """--app= already implies a new window, so --new-window must not appear."""
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": True,
            "x": 100,
            "y": 50,
            "width": 1280,
            "height": 720,
        },
    )
    assert "--new-window" not in args
    assert "--app=https://example.com" in args
    assert "--window-position=100,50" in args
    assert "--window-size=1280,720" in args


def test_build_browser_args_chromium_minimized_does_not_emit_cli_flag(
    monkeypatch,
) -> None:
    """--start-minimized isn't a real Chromium switch; minimisation is Win32-only."""
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": False,
            "minimized": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )
    assert "--start-minimized" not in args
    assert "--new-window" in args


def test_build_browser_args_app_mode_minimized_no_cli_flag(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "msedge")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "msedge",
            "new_window": False,
            "app_mode": True,
            "minimized": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )
    assert "--start-minimized" not in args
    assert "--app=https://example.com" in args


def test_build_browser_args_no_new_window_drops_geometry_and_warns(
    monkeypatch, caplog
) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    with caplog.at_level("WARNING", logger="stream_monitor.notifier"):
        args = notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "chrome",
                "new_window": False,
                "app_mode": False,
                "minimized": True,
                "x": 100,
                "y": 0,
                "width": 800,
                "height": 600,
            },
        )
    assert args == ["chrome", "https://example.com"]
    assert all(not a.startswith("--window-") for a in args)
    assert any("new_window" in rec.message for rec in caplog.records)


def test_build_browser_args_firefox_strips_chromium_flags(monkeypatch, caplog) -> None:
    _stub_browser_resolution(monkeypatch, "firefox")
    with caplog.at_level("WARNING", logger="stream_monitor.notifier"):
        args = notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "firefox",
                "new_window": True,
                "app_mode": True,
                "minimized": False,
                "x": 100,
                "y": 50,
                "width": 800,
                "height": 600,
            },
        )
    assert args == ["firefox", "--new-window", "https://example.com"]
    assert not any(a.startswith("--app=") for a in args)
    assert not any(a.startswith("--window-position") for a in args)
    assert not any(a.startswith("--window-size") for a in args)
    assert any("Firefox" in rec.message for rec in caplog.records)


def test_build_browser_args_firefox_default_geometry_no_warning(
    monkeypatch, caplog
) -> None:
    """When geometry equals defaults & no app_mode, Firefox shouldn't spam a warning."""
    _stub_browser_resolution(monkeypatch, "firefox")
    with caplog.at_level("WARNING", logger="stream_monitor.notifier"):
        args = notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "firefox",
                "new_window": True,
                "app_mode": False,
                "minimized": False,
                "x": 0,
                "y": 0,
                "width": 1280,
                "height": 720,
            },
        )
    assert args == ["firefox", "--new-window", "https://example.com"]
    assert not any("Firefox" in rec.message for rec in caplog.records)


def test_build_browser_args_firefox_without_new_window(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "firefox")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "firefox",
            "new_window": False,
            "app_mode": False,
            "minimized": False,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
        },
    )
    assert args == ["firefox", "https://example.com"]


# ─────────────────────────────────────────────
# Win32 minimize-after-launch wiring
# ─────────────────────────────────────────────
def test_open_with_browser_settings_triggers_win32_window_management_on_windows(
    monkeypatch,
) -> None:
    """On Windows we diff HWNDs and apply geometry/minimize after launch."""
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    enum_calls: list[str] = []
    monkeypatch.setattr(
        notifier,
        "_enum_browser_hwnds",
        lambda class_name: enum_calls.append(class_name) or {1, 2},
    )

    manager_calls: list[tuple[str, set[int], dict[str, object], bool]] = []
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda class_name, baseline, settings, apply_geometry=True, **_kw: manager_calls.append(
            (class_name, baseline, settings, apply_geometry)
        ),
    )

    settings = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "minimized": True,
        "x": 123,
        "y": 45,
        "width": 900,
        "height": 700,
    }

    assert notifier._open_with_browser_settings("https://example.com", settings) is True
    assert enum_calls == ["Chrome_WidgetWin_1"]
    assert len(manager_calls) == 1
    class_name, baseline, fwd_settings, fwd_apply_geometry = manager_calls[0]
    assert class_name == "Chrome_WidgetWin_1"
    assert baseline == {1, 2}
    assert fwd_apply_geometry is True
    # All original settings are forwarded (effective_settings is a superset
    # because _open_with_browser_settings injects user_data_dir).
    for k, v in settings.items():
        assert fwd_settings[k] == v


def test_open_with_browser_settings_uses_firefox_class(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "firefox")
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        notifier, "_enum_browser_hwnds", lambda class_name: {99}
    )
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda class_name, baseline, settings, **_kw: captured.update(
            class_name=class_name, baseline=baseline
        ),
    )

    settings = {
        "enabled": True,
        "browser_path": "firefox",
        "new_window": True,
        "app_mode": False,
        "minimized": True,
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
    }

    assert notifier._open_with_browser_settings("https://example.com", settings) is True
    assert captured == {"class_name": "MozillaWindowClass", "baseline": {99}}


def test_open_with_browser_settings_applies_geometry_when_minimize_disabled(
    monkeypatch,
) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    enum_calls: list[str] = []
    monkeypatch.setattr(
        notifier,
        "_enum_browser_hwnds",
        lambda class_name: enum_calls.append(class_name) or set(),
    )

    manager_calls: list[object] = []
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: manager_calls.append((a, k)),
    )

    settings = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "minimized": False,
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
    }

    assert notifier._open_with_browser_settings("https://example.com", settings) is True
    assert enum_calls == ["Chrome_WidgetWin_1"]
    assert len(manager_calls) == 1


def test_open_with_browser_settings_skips_minimize_on_non_windows(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    manager_calls: list[object] = []
    monkeypatch.setattr(
        notifier,
        "_apply_new_browser_window_settings_async",
        lambda *a, **k: manager_calls.append((a, k)),
    )

    settings = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "minimized": True,
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
    }

    assert notifier._open_with_browser_settings("https://example.com", settings) is True
    assert manager_calls == []


def test_minimize_new_browser_windows_async_returns_none_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    assert (
        notifier._minimize_new_browser_windows_async("Chrome_WidgetWin_1", set()) is None
    )


def test_enum_browser_hwnds_returns_empty_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    assert notifier._enum_browser_hwnds("Chrome_WidgetWin_1") == set()


def test_minimize_thread_actually_minimizes_new_window(monkeypatch) -> None:
    """End-to-end: simulate a new browser window appearing after spawn and
    verify the background worker calls ShowWindow on it with SW_SHOWMINNOACTIVE."""
    import ctypes
    import sys

    # Skip when the real Windows user32 isn't usable (e.g. Linux CI machines).
    if sys.platform != "win32":
        return

    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    snapshots = iter(
        [
            {1, 2, 3},  # baseline
            {1, 2, 3},  # nothing new yet
            {1, 2, 3, 999},  # new window appeared
        ]
    )

    def fake_enum(class_name):
        try:
            return next(snapshots)
        except StopIteration:
            return {1, 2, 3, 999}

    monkeypatch.setattr(notifier, "_enum_browser_hwnds", fake_enum)

    show_window_calls: list[tuple[int, int]] = []

    class FakeUser32:
        def ShowWindow(self, hwnd, cmd):
            show_window_calls.append((int(hwnd), int(cmd)))
            return True

        # Stub GetWindowText* so the noise-filter in the worker treats this
        # hwnd as a legitimate stream window. Returning a plain non-empty
        # title (anything not in the "New Tab" / browser-chrome blacklist)
        # is enough.
        def GetWindowTextLengthW(self, _hwnd):
            return len("Live Stream")

        def GetWindowTextW(self, _hwnd, buf, size):
            buf.value = "Live Stream"
            return len("Live Stream")

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll())

    thread = notifier._minimize_new_browser_windows_async(
        "Chrome_WidgetWin_1", baseline={1, 2, 3}, deadline_s=2.0
    )
    assert thread is not None
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert show_window_calls == [(999, notifier._SW_SHOWMINNOACTIVE)]


def test_window_manager_thread_applies_geometry_and_minimize(monkeypatch) -> None:
    """Geometry path: SW_RESTORE → SetWindowPos → SW_SHOWMINNOACTIVE.

    SW_RESTORE is the new safety call we add before SetWindowPos so that
    Chrome windows opened in maximised state actually move on screen.
    """
    import ctypes
    import sys

    if sys.platform != "win32":
        return

    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    snapshots = iter(
        [
            {1, 2, 3},
            {1, 2, 3, 999},
        ]
    )

    def fake_enum(class_name):
        try:
            return next(snapshots)
        except StopIteration:
            return {1, 2, 3, 999}

    monkeypatch.setattr(notifier, "_enum_browser_hwnds", fake_enum)

    set_window_pos_calls: list[tuple[int, int, int, int, int, int, int]] = []
    show_window_calls: list[tuple[int, int]] = []

    class FakeUser32:
        def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
            set_window_pos_calls.append(
                (
                    int(hwnd),
                    int(insert_after),
                    int(x),
                    int(y),
                    int(width),
                    int(height),
                    int(flags),
                )
            )
            return True

        def ShowWindow(self, hwnd, cmd):
            show_window_calls.append((int(hwnd), int(cmd)))
            return True

        def GetWindowTextLengthW(self, _hwnd):
            return len("Live Stream")

        def GetWindowTextW(self, _hwnd, buf, size):
            buf.value = "Live Stream"
            return len("Live Stream")

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll())

    thread = notifier._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1",
        baseline={1, 2, 3},
        settings={
            "x": 111,
            "y": 222,
            "width": 900,
            "height": 600,
            "minimized": True,
        },
        deadline_s=2.0,
    )
    assert thread is not None
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert set_window_pos_calls == [
        (
            999,
            0,
            111,
            222,
            900,
            600,
            notifier._SWP_NOZORDER | notifier._SWP_NOACTIVATE,
        )
    ]
    # First SW_RESTORE (so SetWindowPos is visible on maximised windows),
    # then SW_SHOWMINNOACTIVE for the requested minimize.
    assert show_window_calls == [
        (999, notifier._SW_RESTORE),
        (999, notifier._SW_SHOWMINNOACTIVE),
    ]


# ─────────────────────────────────────────────
# user_data_dir injection (the master-process workaround)
# ─────────────────────────────────────────────
def test_build_browser_args_chromium_user_data_dir_injected(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": False,
            "minimized": False,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "user_data_dir": r"C:\my\profile",
        },
    )
    assert args[1] == "--user-data-dir=C:\\my\\profile"
    assert args == [
        "chrome",
        "--user-data-dir=C:\\my\\profile",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "--window-position=0,0",
        "--window-size=1280,720",
        "https://example.com",
    ]


def test_build_browser_args_chromium_user_data_dir_with_app_mode(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": False,
            "app_mode": True,
            "minimized": False,
            "x": 100,
            "y": 50,
            "width": 800,
            "height": 600,
            "user_data_dir": "/tmp/profile",
        },
    )
    assert "--user-data-dir=/tmp/profile" in args
    assert "--app=https://example.com" in args
    assert "--window-position=100,50" in args
    assert "--window-size=800,600" in args


def test_build_browser_args_firefox_user_data_dir_uses_profile(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "firefox")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "firefox",
            "new_window": True,
            "app_mode": False,
            "minimized": False,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "user_data_dir": "/home/me/ff_profile",
        },
    )
    # Firefox uses ``-profile <path> -no-remote`` instead of --user-data-dir.
    assert args == [
        "firefox",
        "-profile",
        "/home/me/ff_profile",
        "-no-remote",
        "--new-window",
        "https://example.com",
    ]


def test_build_browser_args_chromium_logs_when_geometry_set_but_no_user_data_dir(
    monkeypatch, caplog
) -> None:
    """Non-default position/size + no user_data_dir → log a heads-up.

    We no longer raise this at WARNING level (post-launch Win32 fix-up will
    re-apply geometry anyway), but the diagnostic is still emitted at INFO
    so users have something to grep for when they're confused about why
    --window-position seemed to do nothing on a hot-start browser.
    """
    _stub_browser_resolution(monkeypatch, "chrome")
    with caplog.at_level("INFO", logger="stream_monitor.notifier"):
        notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "chrome",
                "new_window": False,
                "app_mode": True,
                "minimized": False,
                "x": 200,
                "y": 200,
                "width": 1280,
                "height": 720,
                "user_data_dir": "",
            },
        )
    matching = [
        rec for rec in caplog.records
        if "user_data_dir" in rec.message and "Chrome/Edge" in rec.message
    ]
    assert matching, "expected geometry/user_data_dir diagnostic"
    # The message must NOT claim --app= itself is dropped; that was the
    # misleading wording before this fix.
    for rec in matching:
        assert "--app=" not in rec.message or "--window-position" in rec.message


def test_build_browser_args_chromium_no_warning_when_user_data_dir_provided(
    monkeypatch, caplog
) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    with caplog.at_level("WARNING", logger="stream_monitor.notifier"):
        notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "chrome",
                "new_window": True,
                "app_mode": True,
                "minimized": False,
                "x": 200,
                "y": 200,
                "width": 1280,
                "height": 720,
                "user_data_dir": "/tmp/profile",
            },
        )
    assert not any(
        "user_data_dir" in rec.message and "Chrome/Edge" in rec.message
        for rec in caplog.records
    )


def test_open_with_browser_settings_creates_user_data_dir(monkeypatch, tmp_path) -> None:
    """The profile folder should be created on disk before the browser launches."""
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)  # skip Win32 path
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *_a, **_k: object())

    profile_dir = tmp_path / "fresh_profile"
    assert not profile_dir.exists()

    settings = {
        "enabled": True,
        "browser_path": "chrome",
        "new_window": True,
        "app_mode": False,
        "minimized": False,
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 720,
        "user_data_dir": str(profile_dir),
    }

    assert notifier._open_with_browser_settings("https://example.com", settings) is True
    assert profile_dir.is_dir()


def test_build_browser_args_apply_geometry_false_omits_window_flags(monkeypatch) -> None:
    """When apply_geometry is off, no --window-position / --window-size is sent."""
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": False,
            "apply_geometry": False,
            "minimized": False,
            "x": 100,
            "y": 50,
            "width": 1280,
            "height": 720,
        },
    )
    assert args == ["chrome", "--new-window", "https://example.com"]
    assert not any(a.startswith("--window-") for a in args)


def test_build_browser_args_apply_geometry_false_with_app_mode(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "chrome",
            "new_window": False,
            "app_mode": True,
            "apply_geometry": False,
            "minimized": False,
            "x": 999,
            "y": 999,
            "width": 800,
            "height": 600,
        },
    )
    assert args == ["chrome", "--app=https://example.com"]
    assert not any(a.startswith("--window-") for a in args)


def test_build_browser_args_apply_geometry_false_no_warning_on_non_default(
    monkeypatch, caplog
) -> None:
    """Even with custom x/y/w/h, apply_geometry=False should silence the warning."""
    _stub_browser_resolution(monkeypatch, "chrome")
    with caplog.at_level("WARNING", logger="stream_monitor.notifier"):
        notifier._build_browser_args(
            "https://example.com",
            {
                "browser_path": "chrome",
                "new_window": True,
                "app_mode": False,
                "apply_geometry": False,
                "minimized": False,
                "x": 500,
                "y": 500,
                "width": 1024,
                "height": 768,
                "user_data_dir": "",
            },
        )
    assert not any("user_data_dir" in rec.message for rec in caplog.records)


def test_derive_channel_profile_subdir_twitch() -> None:
    assert (
        notifier._derive_channel_profile_subdir("https://www.twitch.tv/Kaicenat")
        == "twitch_kaicenat"
    )


def test_derive_channel_profile_subdir_youtube_handle() -> None:
    assert (
        notifier._derive_channel_profile_subdir(
            "https://www.youtube.com/@SomeOne/live"
        )
        == "youtube_SomeOne"
    )


def test_derive_channel_profile_subdir_unknown_returns_none() -> None:
    assert notifier._derive_channel_profile_subdir("https://example.com/path") is None


def test_resolve_effective_user_data_dir_per_channel_on(tmp_path) -> None:
    base = str(tmp_path / "browser_profile")
    result = notifier._resolve_effective_user_data_dir(
        "https://www.twitch.tv/abc", base, per_channel=True
    )
    assert result.endswith("twitch_abc")
    assert Path(result).parent == Path(base)


def test_resolve_effective_user_data_dir_per_channel_off(tmp_path) -> None:
    base = str(tmp_path / "browser_profile")
    result = notifier._resolve_effective_user_data_dir(
        "https://www.twitch.tv/abc", base, per_channel=False
    )
    assert result == base


def test_resolve_effective_user_data_dir_unknown_url_falls_back(tmp_path) -> None:
    base = str(tmp_path / "browser_profile")
    result = notifier._resolve_effective_user_data_dir(
        "https://example.com/whatever", base, per_channel=True
    )
    assert result == base


def test_resolve_effective_user_data_dir_empty_base_returns_empty(tmp_path) -> None:
    assert (
        notifier._resolve_effective_user_data_dir(
            "https://www.twitch.tv/abc", "", per_channel=True
        )
        == ""
    )


def test_slugify_channel_strips_unsafe_characters() -> None:
    assert notifier._slugify_channel("hello/world\\!@#") == "hello_world"


def test_open_with_browser_settings_uses_per_channel_subdir(
    monkeypatch, tmp_path
) -> None:
    """End-to-end: subprocess.Popen should see the per-channel path."""
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)

    base = tmp_path / "browser_profile"
    captured_args: list[list[str]] = []

    class FakePopen:
        def __init__(self, args, **kwargs):  # noqa: D401
            captured_args.append(list(args))

    monkeypatch.setattr(notifier.subprocess, "Popen", FakePopen)

    assert notifier._open_with_browser_settings(
        "https://www.twitch.tv/Kaicenat",
        {
            "enabled": True,
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": True,
            "apply_geometry": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "minimized": False,
            "user_data_dir": str(base),
            "per_channel_profile": True,
        },
    )

    assert len(captured_args) == 1
    flat = captured_args[0]
    user_data_flag = next(a for a in flat if a.startswith("--user-data-dir="))
    expected_subdir = str(base / "twitch_kaicenat")
    assert user_data_flag == f"--user-data-dir={expected_subdir}"
    assert "--app=https://www.twitch.tv/Kaicenat" in flat
    # Chromium hygiene flags should accompany an isolated profile launch.
    assert "--no-first-run" in flat
    assert "--no-default-browser-check" in flat
    # The per-channel sub-folder must be created so Chrome doesn't have to.
    assert (base / "twitch_kaicenat").is_dir()


def test_open_with_browser_settings_per_channel_off_uses_base_dir(
    monkeypatch, tmp_path
) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)

    base = tmp_path / "browser_profile"
    captured: list[list[str]] = []
    monkeypatch.setattr(
        notifier.subprocess, "Popen", lambda args, **kw: captured.append(list(args))
    )

    notifier._open_with_browser_settings(
        "https://www.twitch.tv/Kaicenat",
        {
            "enabled": True,
            "browser_path": "chrome",
            "new_window": True,
            "user_data_dir": str(base),
            "per_channel_profile": False,
        },
    )
    flat = captured[0]
    assert f"--user-data-dir={base}" in flat
    assert not any("twitch_kaicenat" in a for a in flat)


def test_build_browser_args_chromium_no_first_run_flags(monkeypatch) -> None:
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://www.twitch.tv/x",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": True,
            "apply_geometry": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "user_data_dir": "/tmp/p",
        },
    )
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args


def test_build_browser_args_chromium_no_first_run_only_with_profile(monkeypatch) -> None:
    """Without a profile path, we should NOT add the no-first-run flags
    (they would unnecessarily change the user's main browser behaviour)."""
    _stub_browser_resolution(monkeypatch, "chrome")
    args = notifier._build_browser_args(
        "https://www.twitch.tv/x",
        {
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": False,
            "apply_geometry": True,
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "user_data_dir": "",
        },
    )
    assert "--no-first-run" not in args
    assert "--no-default-browser-check" not in args


def test_configure_user32_signatures_sets_argtypes() -> None:
    """Verify HWND args are declared as wintypes.HWND (avoids 64-bit truncation)."""
    import ctypes
    from ctypes import wintypes

    class FakeFn:
        argtypes: object = None
        restype: object = None

    class FakeUser32:
        SetWindowPos = FakeFn()
        ShowWindow = FakeFn()
        IsWindowVisible = FakeFn()
        GetWindowTextLengthW = FakeFn()
        GetClassNameW = FakeFn()
        GetWindowTextW = FakeFn()
        PostMessageW = FakeFn()
        IsWindow = FakeFn()
        GetWindow = FakeFn()
        GetWindowRect = FakeFn()
        GetWindowLongW = FakeFn()
        SetWindowLongW = FakeFn()

    user32 = FakeUser32()
    notifier._configure_user32_signatures(user32)

    assert user32.SetWindowPos.argtypes[0] is wintypes.HWND
    assert user32.SetWindowPos.argtypes[1] is wintypes.HWND
    assert user32.SetWindowPos.restype is wintypes.BOOL
    assert user32.ShowWindow.argtypes[0] is wintypes.HWND
    assert user32.ShowWindow.argtypes[1] is ctypes.c_int
    assert user32.ShowWindow.restype is wintypes.BOOL

    # Re-running is a no-op (guard flag).
    user32.SetWindowPos.argtypes = "tampered"
    notifier._configure_user32_signatures(user32)
    assert user32.SetWindowPos.argtypes == "tampered"


# ─────────────────────────────────────────────
# Window tracking + close-on-offline
# ─────────────────────────────────────────────
def _reset_tracked_hwnds() -> None:
    with notifier._TRACKED_HWNDS_LOCK:
        notifier._TRACKED_WINDOWS_BY_URL.clear()


def test_register_and_snapshot_tracked_hwnds_roundtrip() -> None:
    _reset_tracked_hwnds()
    try:
        notifier._register_tracked_hwnd("https://x", 1234)
        notifier._register_tracked_hwnd("https://x", 5678)
        notifier._register_tracked_hwnd("https://y", 99)
        assert notifier._snapshot_tracked_hwnds("https://x") == {1234, 5678}
        assert notifier._snapshot_tracked_hwnds("https://y") == {99}
        assert notifier._snapshot_tracked_hwnds("https://missing") == set()
    finally:
        _reset_tracked_hwnds()


def test_register_tracked_hwnd_ignores_empty_url_and_zero_hwnd() -> None:
    _reset_tracked_hwnds()
    try:
        notifier._register_tracked_hwnd("", 1)
        notifier._register_tracked_hwnd("https://x", 0)
        assert not notifier._TRACKED_WINDOWS_BY_URL
    finally:
        _reset_tracked_hwnds()


def test_clear_tracked_hwnds_removes_the_entry() -> None:
    _reset_tracked_hwnds()
    try:
        notifier._register_tracked_hwnd("https://x", 42)
        notifier._clear_tracked_hwnds("https://x")
        assert notifier._snapshot_tracked_hwnds("https://x") == set()
    finally:
        _reset_tracked_hwnds()


def test_close_browser_window_for_url_no_op_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    assert notifier.close_browser_window_for_url("https://x") == 0


def test_close_browser_window_for_url_uses_tracked_hwnds(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed_hwnds: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed_hwnds.append(hwnd), True)[1],
    )
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: {999},  # should NOT be used when tracking hits
    )

    notifier._register_tracked_hwnd("https://stream", 11)
    notifier._register_tracked_hwnd("https://stream", 22)

    result = notifier.close_browser_window_for_url(
        "https://stream", title_keywords=["channel"]
    )

    assert result == 2
    assert set(closed_hwnds) == {11, 22}
    # Registry is cleared after the close call.
    assert notifier._snapshot_tracked_hwnds("https://stream") == set()


def test_close_browser_window_for_url_falls_back_to_title_keyword(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )
    monkeypatch.setattr(
        notifier,
        "_find_hwnds_by_title_keyword",
        lambda kws: {777} if "Kaicenat" in kws else set(),
    )

    result = notifier.close_browser_window_for_url(
        "https://www.twitch.tv/Kaicenat", title_keywords=["Kaicenat"]
    )

    assert result == 1
    assert closed == [777]


def test_close_browser_window_for_url_returns_zero_when_no_match(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_post_close_window", lambda hwnd: False)
    monkeypatch.setattr(notifier, "_find_hwnds_by_title_keyword", lambda kws: set())

    assert (
        notifier.close_browser_window_for_url(
            "https://x", title_keywords=["nope"]
        )
        == 0
    )


def test_close_browser_window_for_url_empty_url_returns_zero(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    assert notifier.close_browser_window_for_url("") == 0


def test_find_hwnds_by_title_keyword_case_insensitive_substring(monkeypatch) -> None:
    monkeypatch.setattr(
        notifier,
        "_enum_visible_hwnds_with_title",
        lambda: [
            (1, "Kaicenat - Twitch"),
            (2, "Chrome - New Tab"),
            (3, "kaicenat live stream"),
        ],
    )
    matches = notifier._find_hwnds_by_title_keyword(["KaIcEnAt"])
    assert matches == {1, 3}


def test_find_hwnds_by_title_keyword_empty_keyword_list_returns_empty(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        notifier,
        "_enum_visible_hwnds_with_title",
        lambda: [(1, "anything")],
    )
    assert notifier._find_hwnds_by_title_keyword(["  ", ""]) == set()


def test_post_close_window_posts_wm_close(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    user32.IsWindow.return_value = 1
    user32.PostMessageW.return_value = 1

    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    assert notifier._post_close_window(0xABCD) is True
    user32.IsWindow.assert_called_once_with(0xABCD)
    user32.PostMessageW.assert_called_once_with(0xABCD, notifier._WM_CLOSE, 0, 0)


def test_post_close_window_returns_false_when_hwnd_invalid(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    user32.IsWindow.return_value = 0
    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    assert notifier._post_close_window(0xDEAD) is False
    user32.PostMessageW.assert_not_called()


def test_hide_window_from_taskbar_flips_ws_ex_toolwindow() -> None:
    """Verify the helper ORs WS_EX_TOOLWINDOW in and ANDs WS_EX_APPWINDOW out,
    then performs a SW_HIDE -> SW_SHOW cycle to refresh the taskbar."""
    user32 = MagicMock()
    # Pretend the window currently has WS_EX_APPWINDOW set and nothing else.
    user32.GetWindowLongW.return_value = notifier._WS_EX_APPWINDOW

    assert notifier._hide_window_from_taskbar(user32, 0x1234) is True

    user32.GetWindowLongW.assert_called_once_with(0x1234, notifier._GWL_EXSTYLE)

    set_call = user32.SetWindowLongW.call_args
    assert set_call.args[0] == 0x1234
    assert set_call.args[1] == notifier._GWL_EXSTYLE
    new_style = set_call.args[2]
    # Tool-window bit set, app-window bit cleared.
    assert new_style & notifier._WS_EX_TOOLWINDOW
    assert not (new_style & notifier._WS_EX_APPWINDOW)

    # SW_HIDE -> SW_SHOW pair (in that order) refreshes the taskbar.
    show_calls = [c.args for c in user32.ShowWindow.call_args_list]
    assert show_calls == [(0x1234, notifier._SW_HIDE), (0x1234, notifier._SW_SHOW)]


def test_hide_window_from_taskbar_noop_when_already_hidden() -> None:
    """If WS_EX_TOOLWINDOW is already set and WS_EX_APPWINDOW already cleared,
    no syscalls beyond GetWindowLongW should fire."""
    user32 = MagicMock()
    user32.GetWindowLongW.return_value = notifier._WS_EX_TOOLWINDOW

    assert notifier._hide_window_from_taskbar(user32, 0x5678) is False
    user32.SetWindowLongW.assert_not_called()
    user32.ShowWindow.assert_not_called()


def test_hide_window_from_taskbar_returns_false_on_getwindowlong_failure() -> None:
    user32 = MagicMock()
    user32.GetWindowLongW.side_effect = OSError("boom")

    assert notifier._hide_window_from_taskbar(user32, 0x1) is False
    user32.SetWindowLongW.assert_not_called()


def test_apply_new_browser_window_settings_async_hides_from_taskbar(
    monkeypatch,
) -> None:
    """The post-launch worker should call _hide_window_from_taskbar when the
    settings dict has hide_from_taskbar=True."""
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    user32.ShowWindow.return_value = 1
    user32.SetWindowPos.return_value = 1
    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    enum_results = iter([
        set(),
        {4321},
        {4321},
    ])
    monkeypatch.setattr(
        notifier, "_enum_browser_hwnds", lambda _cls: next(enum_results)
    )

    hide_calls: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_hide_window_from_taskbar",
        lambda u32, hwnd: hide_calls.append(hwnd) or True,
    )

    thread = notifier._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1",
        baseline=set(),
        settings={
            "x": 0,
            "y": 0,
            "width": 1280,
            "height": 720,
            "minimized": False,
            "hide_from_taskbar": True,
        },
        apply_geometry=True,
        deadline_s=1.0,
    )
    assert thread is not None
    thread.join(timeout=2.0)
    assert hide_calls == [4321]


def test_apply_new_browser_window_settings_async_skips_taskbar_hide_when_off(
    monkeypatch,
) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    enum_results = iter([set(), {1}, {1}])
    monkeypatch.setattr(
        notifier, "_enum_browser_hwnds", lambda _cls: next(enum_results)
    )

    called = []
    monkeypatch.setattr(
        notifier,
        "_hide_window_from_taskbar",
        lambda u32, hwnd: called.append(hwnd) or True,
    )

    thread = notifier._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1",
        baseline=set(),
        settings={"hide_from_taskbar": False},
        apply_geometry=False,
        deadline_s=1.0,
    )
    assert thread is not None
    thread.join(timeout=2.0)
    assert called == []


def test_configure_user32_signatures_includes_getwindowlong() -> None:
    """_configure_user32_signatures should declare argtypes for the
    GetWindowLongW / SetWindowLongW pair used by the taskbar-hide helper."""
    import ctypes
    from ctypes import wintypes

    class FakeFn:
        argtypes: object = None
        restype: object = None

    class FakeUser32:
        SetWindowPos = FakeFn()
        ShowWindow = FakeFn()
        IsWindowVisible = FakeFn()
        GetWindowTextLengthW = FakeFn()
        GetClassNameW = FakeFn()
        GetWindowTextW = FakeFn()
        PostMessageW = FakeFn()
        IsWindow = FakeFn()
        GetWindow = FakeFn()
        GetWindowRect = FakeFn()
        GetWindowLongW = FakeFn()
        SetWindowLongW = FakeFn()

    user32 = FakeUser32()
    notifier._configure_user32_signatures(user32)

    assert user32.GetWindowLongW.argtypes[0] is wintypes.HWND
    assert user32.GetWindowLongW.argtypes[1] is ctypes.c_int
    assert user32.GetWindowLongW.restype is wintypes.LONG

    assert user32.GetWindow.argtypes[0] is wintypes.HWND
    assert user32.GetWindow.argtypes[1] is ctypes.c_uint
    assert user32.GetWindow.restype is wintypes.HWND

    assert user32.GetWindowRect.argtypes[0] is wintypes.HWND
    assert user32.GetWindowRect.argtypes[1] == ctypes.POINTER(wintypes.RECT)
    assert user32.GetWindowRect.restype is wintypes.BOOL

    assert user32.SetWindowLongW.argtypes[0] is wintypes.HWND
    assert user32.SetWindowLongW.argtypes[1] is ctypes.c_int
    assert user32.SetWindowLongW.argtypes[2] is wintypes.LONG
    assert user32.SetWindowLongW.restype is wintypes.LONG


def test_apply_new_browser_window_settings_async_registers_tracked_url(
    monkeypatch,
) -> None:
    """The post-launch worker should call _register_tracked_hwnd as it
    discovers each new browser window so close-on-offline can find them."""
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    user32.ShowWindow.return_value = 1
    user32.SetWindowPos.return_value = 1
    # The noise-window filter (added when we threaded title_hints through to
    # the prune feature) reads GetWindowText* before applying geometry, so
    # stub them to return a legit-looking title.
    user32.GetWindowTextLengthW.return_value = len("Live Stream")

    def _get_text(_hwnd, buf, _size):
        buf.value = "Live Stream"
        return len("Live Stream")

    user32.GetWindowTextW.side_effect = _get_text

    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    enum_results = iter([
        set(),  # first poll: nothing new
        {1234},  # second poll: discovered the new window
        {1234},
    ])
    monkeypatch.setattr(
        notifier, "_enum_browser_hwnds", lambda _cls: next(enum_results)
    )

    thread = notifier._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1",
        baseline=set(),
        settings={"x": 0, "y": 0, "width": 1280, "height": 720, "minimized": False},
        apply_geometry=True,
        deadline_s=1.0,
        track_for_url="https://x",
        track_keywords=("hello", "Live Stream"),
    )
    assert thread is not None
    thread.join(timeout=2.0)
    assert notifier._snapshot_tracked_hwnds("https://x") == {1234}
    # The TrackedWindow entry must also have stored the keywords so the
    # off-topic prune pass can identify the window later.
    tracked = notifier._snapshot_tracked_windows("https://x")
    assert tracked and tracked[0].keywords == ("hello", "live stream")
    _reset_tracked_hwnds()


# ─────────────────────────────────────────────
# B — noise-title filter
# ─────────────────────────────────────────────
def test_browser_popup_or_tool_window_filter_detects_owned_tool_and_small_windows() -> None:
    class FakeUser32:
        owner = 0
        ex_style = 0
        rect = (0, 0, 1280, 720)

        def GetWindow(self, _hwnd, _cmd):
            return self.owner

        def GetWindowLongW(self, _hwnd, _index):
            return self.ex_style

        def GetWindowRect(self, _hwnd, rect_ptr):
            left, top, right, bottom = self.rect
            rect = rect_ptr._obj
            rect.left = left
            rect.top = top
            rect.right = right
            rect.bottom = bottom
            return 1

    user32 = FakeUser32()
    assert notifier._is_browser_popup_or_tool_window(user32, 100) is False

    user32.owner = 99
    assert notifier._is_browser_popup_or_tool_window(user32, 100) is True

    user32.owner = 0
    user32.ex_style = notifier._WS_EX_TOOLWINDOW
    assert notifier._is_browser_popup_or_tool_window(user32, 100) is True

    user32.ex_style = 0
    user32.rect = (0, 0, 260, 180)
    assert notifier._is_browser_popup_or_tool_window(user32, 100) is True


def test_window_manager_ignores_popup_then_manages_real_window(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)

    user32 = MagicMock()
    user32.ShowWindow.return_value = 1
    user32.SetWindowPos.return_value = 1
    user32.GetWindow.side_effect = lambda hwnd, _cmd: 10 if hwnd == 111 else 0
    user32.GetWindowLongW.return_value = 0
    user32.GetWindowRect.return_value = 1
    user32.GetWindowTextLengthW.return_value = len("Live Stream")

    def _get_text(_hwnd, buf, _size):
        buf.value = "Live Stream"
        return len("Live Stream")

    user32.GetWindowTextW.side_effect = _get_text

    fake_ctypes = MagicMock()
    fake_ctypes.windll.user32 = user32
    monkeypatch.setitem(__import__("sys").modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)

    enum_results = iter([
        set(),
        {111},
        {111, 222},
        {111, 222},
    ])
    monkeypatch.setattr(
        notifier, "_enum_browser_hwnds", lambda _cls: next(enum_results)
    )

    thread = notifier._apply_new_browser_window_settings_async(
        "Chrome_WidgetWin_1",
        baseline=set(),
        settings={"x": 0, "y": 0, "width": 1280, "height": 720},
        deadline_s=1.0,
        track_for_url="https://x",
        track_keywords=("Live Stream",),
    )
    assert thread is not None
    thread.join(timeout=2.0)
    assert notifier._snapshot_tracked_hwnds("https://x") == {222}
    assert user32.SetWindowPos.call_args.args[0] == 222
    _reset_tracked_hwnds()


def test_is_noise_window_title_recognises_blank_browser_windows() -> None:
    assert notifier._is_noise_window_title("") is True
    assert notifier._is_noise_window_title("   ") is True
    assert notifier._is_noise_window_title("Google Chrome") is True
    assert notifier._is_noise_window_title("Microsoft Edge") is True
    assert notifier._is_noise_window_title("New Tab") is True
    # Multilingual New-Tab strings used by Chrome / Edge.
    assert notifier._is_noise_window_title("新分頁") is True
    assert notifier._is_noise_window_title("新分页") is True
    assert notifier._is_noise_window_title("新しいタブ") is True
    assert notifier._is_noise_window_title("새 탭") is True


def test_is_noise_window_title_passes_real_stream_titles() -> None:
    # Window titles produced by App Mode on Twitch/YouTube usually look like
    # the stream title verbatim, or "<stream title> - YouTube".
    assert notifier._is_noise_window_title("Kaicenat live now!!! @!@") is False
    assert notifier._is_noise_window_title("Stream Title - YouTube") is False
    assert notifier._is_noise_window_title("📺 LIVE: tonight's show") is False


# ─────────────────────────────────────────────
# D1 — close_all_tracked_windows
# ─────────────────────────────────────────────
def test_close_all_tracked_windows_closes_every_url(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    notifier._register_tracked_hwnd("https://a", 1)
    notifier._register_tracked_hwnd("https://a", 2)
    notifier._register_tracked_hwnd("https://b", 3)

    result = notifier.close_all_tracked_windows()
    assert result == 3
    assert set(closed) == {1, 2, 3}
    # Registry is wiped after a successful sweep so a second invocation is
    # cheap and won't double-close.
    assert notifier.close_all_tracked_windows() == 0
    _reset_tracked_hwnds()


def test_close_all_tracked_windows_no_op_off_windows(monkeypatch) -> None:
    _reset_tracked_hwnds()
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    notifier._register_tracked_hwnd("https://a", 1)
    assert notifier.close_all_tracked_windows() == 0
    # Registry not cleared on non-Windows — caller still owns it.
    assert notifier._snapshot_tracked_hwnds("https://a") == {1}
    _reset_tracked_hwnds()


# ─────────────────────────────────────────────
# D2 — prune_off_topic_tracked_windows
# ─────────────────────────────────────────────
class _FakeUser32:
    """Minimal user32 stub for the prune helper.

    *titles* maps HWND → title string; HWNDs not in the dict are treated as
    "no longer alive" (IsWindow returns False).
    """

    def __init__(self, titles: dict[int, str]) -> None:
        self.titles = titles

    def IsWindow(self, hwnd: int) -> int:
        return 1 if int(hwnd) in self.titles else 0

    def GetWindowTextLengthW(self, hwnd: int) -> int:
        return len(self.titles.get(int(hwnd), ""))

    def GetWindowTextW(self, hwnd: int, buf, _size: int) -> int:
        text = self.titles.get(int(hwnd), "")
        buf.value = text
        return len(text)


def _install_fake_user32(monkeypatch, titles: dict[int, str]) -> _FakeUser32:
    import ctypes

    user32 = _FakeUser32(titles)
    fake_windll = MagicMock()
    fake_windll.user32 = user32
    monkeypatch.setattr(ctypes, "windll", fake_windll)
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    monkeypatch.setattr(notifier, "_configure_user32_signatures", lambda _u: None)
    return user32


def test_prune_off_topic_closes_window_with_blacklisted_title(monkeypatch) -> None:
    _reset_tracked_hwnds()
    _install_fake_user32(monkeypatch, {111: "New Tab"})
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    # Window was registered long enough ago to clear the grace period.
    notifier._register_tracked_hwnd("https://stream", 111, keywords=["channelA"])
    with notifier._TRACKED_HWNDS_LOCK:
        for t in notifier._TRACKED_WINDOWS_BY_URL["https://stream"]:
            t.opened_at = 0.0

    result = notifier.prune_off_topic_tracked_windows(min_age_s=1.0)
    assert result == 1
    assert closed == [111]
    assert notifier._snapshot_tracked_hwnds("https://stream") == set()
    _reset_tracked_hwnds()


def test_prune_off_topic_closes_window_that_lost_all_keywords(monkeypatch) -> None:
    _reset_tracked_hwnds()
    # Window title no longer contains "channelA" — user navigated away.
    _install_fake_user32(monkeypatch, {222: "Some Unrelated Page"})
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    notifier._register_tracked_hwnd("https://stream", 222, keywords=["channelA"])
    with notifier._TRACKED_HWNDS_LOCK:
        for t in notifier._TRACKED_WINDOWS_BY_URL["https://stream"]:
            t.opened_at = 0.0

    assert notifier.prune_off_topic_tracked_windows(min_age_s=1.0) == 1
    assert closed == [222]
    _reset_tracked_hwnds()


def test_prune_off_topic_keeps_window_with_matching_keyword(monkeypatch) -> None:
    _reset_tracked_hwnds()
    _install_fake_user32(monkeypatch, {333: "channelA live right now"})
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    notifier._register_tracked_hwnd("https://stream", 333, keywords=["channelA"])
    with notifier._TRACKED_HWNDS_LOCK:
        for t in notifier._TRACKED_WINDOWS_BY_URL["https://stream"]:
            t.opened_at = 0.0

    assert notifier.prune_off_topic_tracked_windows(min_age_s=1.0) == 0
    assert closed == []
    # Still tracked — we didn't touch it.
    assert notifier._snapshot_tracked_hwnds("https://stream") == {333}
    _reset_tracked_hwnds()


def test_prune_off_topic_respects_grace_period(monkeypatch) -> None:
    """Window with a noise title is still skipped if it's brand new."""
    _reset_tracked_hwnds()
    _install_fake_user32(monkeypatch, {444: "New Tab"})
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    # opened_at is "now" → still in grace period for min_age_s=60.
    notifier._register_tracked_hwnd("https://stream", 444, keywords=["x"])

    assert notifier.prune_off_topic_tracked_windows(min_age_s=60.0) == 0
    assert closed == []
    # Window is still tracked — we deliberately did not close it yet.
    assert notifier._snapshot_tracked_hwnds("https://stream") == {444}
    _reset_tracked_hwnds()


def test_prune_off_topic_drops_dead_hwnds_without_closing(monkeypatch) -> None:
    """HWND that no longer exists (user already closed it) should just be
    cleaned up — never reported as a "close" event."""
    _reset_tracked_hwnds()
    # No HWND in the title map → IsWindow returns 0 → stale.
    _install_fake_user32(monkeypatch, {})
    closed: list[int] = []
    monkeypatch.setattr(
        notifier,
        "_post_close_window",
        lambda hwnd: (closed.append(hwnd), True)[1],
    )

    notifier._register_tracked_hwnd("https://stream", 555, keywords=["x"])
    with notifier._TRACKED_HWNDS_LOCK:
        for t in notifier._TRACKED_WINDOWS_BY_URL["https://stream"]:
            t.opened_at = 0.0

    assert notifier.prune_off_topic_tracked_windows(min_age_s=1.0) == 0
    assert closed == []
    assert notifier._snapshot_tracked_hwnds("https://stream") == set()
    _reset_tracked_hwnds()


def test_prune_off_topic_no_op_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    assert notifier.prune_off_topic_tracked_windows() == 0


# ─────────────────────────────────────────────
# title_hints threaded through to action helpers
# ─────────────────────────────────────────────
def test_title_hints_from_stream_info_includes_display_channel_title() -> None:
    from stream_monitor.fetcher.base import StreamInfo as _SI

    info = _SI(
        channel="kaicenat",
        platform="twitch",
        is_live=True,
        title="LIVE NOW!!!",
        url="https://www.twitch.tv/kaicenat",
        display_name="Kai Cenat",
    )
    hints = notifier._title_hints_from_stream_info(info)
    assert "Kai Cenat" in hints
    assert "kaicenat" in hints
    assert "LIVE NOW!!!" in hints


def test_register_tracked_hwnd_merges_keywords_on_re_register() -> None:
    _reset_tracked_hwnds()
    try:
        notifier._register_tracked_hwnd("https://x", 1, keywords=["a"])
        notifier._register_tracked_hwnd("https://x", 1, keywords=["b", "a"])
        tracked = notifier._snapshot_tracked_windows("https://x")
        assert len(tracked) == 1
        assert tracked[0].keywords == ("a", "b")
    finally:
        _reset_tracked_hwnds()
