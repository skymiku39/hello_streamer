"""Tests for shared UI formatting helpers."""

from stream_monitor import i18n
from stream_monitor.app_ui import _format_minutes_delta


def test_format_minutes_delta_under_one_minute() -> None:
    i18n.set_language("en-US", notify=False)
    assert _format_minutes_delta(30) == "<1m"
    assert _format_minutes_delta(90) == "1m"
    assert _format_minutes_delta(0) == "0m"
