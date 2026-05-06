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
