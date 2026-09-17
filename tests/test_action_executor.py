"""Tests for structured action outcomes and platform-port boundaries."""

from __future__ import annotations

from stream_monitor.action_executor import ActionExecutor, ActionStatus
from stream_monitor.action_plan import action_plan_for
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.platform_ports import ActionPorts


def _info() -> StreamInfo:
    return StreamInfo(
        channel="hello",
        platform="twitch",
        is_live=True,
        title="Live",
        url="https://www.twitch.tv/hello",
    )


class _Notifications:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls: list[bool] = []

    def send(self, _info: StreamInfo, *, with_open_button: bool) -> bool:
        self.calls.append(with_open_button)
        return self.result


class _Browser:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = 0

    def open(self, _info: StreamInfo, _settings) -> bool:
        self.calls += 1
        return self.result


def _executor(notifications: _Notifications, browser: _Browser) -> ActionExecutor:
    return ActionExecutor(
        ports=ActionPorts(notification=notifications, browser=browser)
    )


def test_notify_only_reports_notification_failure() -> None:
    notifications = _Notifications(False)
    browser = _Browser()

    result = _executor(notifications, browser).execute(
        action_plan_for("notify_only"), _info()
    )

    assert result.status == ActionStatus.NOTIFICATION_FAILED
    assert result.succeeded is False
    assert result.notification_sent is False
    assert browser.calls == 0


def test_open_action_reports_browser_failure_and_recovery_notification() -> None:
    notifications = _Notifications()
    browser = _Browser(False)

    result = _executor(notifications, browser).execute(
        action_plan_for("open_and_stop"), _info()
    )

    assert result.status == ActionStatus.BROWSER_FAILED
    assert result.browser_opened is False
    assert notifications.calls == [False, True]


def test_successful_terminal_action_reports_each_effect() -> None:
    notifications = _Notifications()
    browser = _Browser()
    lifecycle: list[str] = []

    result = _executor(notifications, browser).execute(
        action_plan_for("open_and_exit"),
        _info(),
        exit_fn=lambda: lifecycle.append("exit"),
    )

    assert result.succeeded is True
    assert result.status == ActionStatus.COMPLETED
    assert result.notification_sent is True
    assert result.browser_opened is True
    assert lifecycle == ["exit"]


def test_terminal_action_without_lifecycle_callback_is_not_reported_complete() -> None:
    result = _executor(_Notifications(), _Browser()).execute(
        action_plan_for("open_and_stop"), _info()
    )

    assert result.status == ActionStatus.LIFECYCLE_FAILED
    assert result.browser_opened is True
    assert result.succeeded is False


def test_cancelled_action_does_not_start_any_effect() -> None:
    notifications = _Notifications()
    browser = _Browser()

    result = _executor(notifications, browser).execute(
        action_plan_for("open_and_stop"),
        _info(),
        is_cancelled=lambda: True,
    )

    assert result.status == ActionStatus.CANCELLED
    assert notifications.calls == []
    assert browser.calls == 0


def test_cancellation_after_notification_prevents_browser_launch() -> None:
    notifications = _Notifications()
    browser = _Browser()
    cancelled = [False]

    def notify(_info, *, with_open_button: bool) -> bool:
        notifications.calls.append(with_open_button)
        cancelled[0] = True
        return True

    notifications.send = notify
    result = _executor(notifications, browser).execute(
        action_plan_for("open_and_stop"),
        _info(),
        is_cancelled=lambda: cancelled[0],
    )

    assert result.status == ActionStatus.CANCELLED
    assert result.notification_sent is True
    assert browser.calls == 0
