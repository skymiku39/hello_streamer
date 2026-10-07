"""Failure delivery from browser workers to the main window, without live I/O."""

from types import SimpleNamespace

import pytest

from stream_monitor.app import App
from stream_monitor.page_assist_status import (
    drain_page_assist_status,
    publish_page_assist_status,
)


@pytest.fixture(autouse=True)
def clear_notices():
    drain_page_assist_status()
    yield
    drain_page_assist_status()


def test_notice_backlog_is_bounded_and_keeps_latest_outcomes():
    for index in range(100):
        publish_page_assist_status(str(index), "timeout")
    notices = drain_page_assist_status()
    assert len(notices) == 32
    assert notices[-1] == ("99", "timeout")


def test_recovery_does_not_clear_another_channels_failure():
    rendered = []
    probe = SimpleNamespace(
        _page_assist_failures={},
        _render_page_assist_notice=lambda: rendered.append(True),
    )
    publish_page_assist_status("a", "timeout")
    publish_page_assist_status("b", "profile_nondebug")
    publish_page_assist_status("a", "")
    App._poll_page_assist_status(probe)
    assert probe._page_assist_failures == {"b": "profile_nondebug"}
    assert rendered == [True]
    publish_page_assist_status("*", "")
    App._poll_page_assist_status(probe)
    assert probe._page_assist_failures == {}


def test_worker_connection_failure_reaches_ui(monkeypatch):
    from stream_monitor import twitch_page_assist as assist
    from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

    def fail(*_args, **_kwargs):
        raise RuntimeError("endpoint unavailable")

    monkeypatch.setattr(
        assist, "CdpClient", lambda: SimpleNamespace(connect=fail, close=lambda: None)
    )
    worker = assist._PageAssistWorker("https://www.twitch.tv/test", 12345, ViewerEngagementSettings())
    worker._run()
    assert drain_page_assist_status() == [(worker.url, "unavailable")]


def test_failure_messages_exist_and_format_in_every_locale():
    from stream_monitor import i18n

    for table in i18n._TRANSLATIONS.values():
        for key in ("profile_busy", "failed"):
            text = table[f"engagement.page_assist.{key}"].format(url="https://www.twitch.tv/test")
            assert "https://www.twitch.tv/test" in text


def test_failure_banner_gives_profile_recovery_action_and_hides_on_recovery():
    from stream_monitor import i18n

    class Label:
        def __init__(self):
            self.text = ""
            self.manager = ""

        def configure(self, **kwargs):
            self.text = kwargs["text"]

        def pack(self, **_kwargs):
            self.manager = "pack"

        def pack_forget(self):
            self.manager = ""

        def winfo_manager(self):
            return self.manager

    label = Label()
    probe = SimpleNamespace(
        _page_assist_failures={"https://www.twitch.tv/test": "profile_nondebug"},
        _page_assist_notice=label,
    )
    App._render_page_assist_notice(probe)
    assert label.text == i18n.tr(
        "engagement.page_assist.profile_busy", url="https://www.twitch.tv/test"
    )
    assert label.manager == "pack"
    probe._page_assist_failures.clear()
    App._render_page_assist_notice(probe)
    assert label.manager == ""
