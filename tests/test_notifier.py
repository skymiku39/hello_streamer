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
            "browser_path": "chrome",
            "new_window": True,
            "app_mode": False,
            "x": 10,
            "y": 20,
            "width": 800,
            "height": 600,
        },
    )
    assert args == [
        "chrome",
        "--new-window",
        "--window-position=10,20",
        "--window-size=800,600",
        "https://example.com",
    ]


def test_build_browser_args_app_mode_replaces_url() -> None:
    args = notifier._build_browser_args(
        "https://example.com",
        {
            "browser_path": "msedge",
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
    assert args[0] == "msedge"


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
        "browser_path": "chrome",
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
            "chrome",
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

    def fake_open(url, browser_settings=None):
        captured["url"] = url
        captured["settings"] = browser_settings
        return True

    monkeypatch.setattr(notifier, "open_url", fake_open)

    settings = {"enabled": True, "browser_path": "chrome"}
    notifier.execute_action("open_and_keep", _info(), browser_settings=settings)

    assert captured["url"] == "https://www.twitch.tv/hello"
    assert captured["settings"] is settings
