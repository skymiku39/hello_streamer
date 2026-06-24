"""Wheel nudge helpers during channel reorder."""

from __future__ import annotations

from stream_monitor.app import App


def test_wheel_nudge_steps_windows_delta() -> None:
    up = type("Ev", (), {"delta": 120, "num": None})()
    down = type("Ev", (), {"delta": -120, "num": None})()
    assert App._wheel_nudge_steps(up) == -1
    assert App._wheel_nudge_steps(down) == 1


def test_wheel_nudge_steps_linux_buttons() -> None:
    up = type("Ev", (), {"delta": 0, "num": 4})()
    down = type("Ev", (), {"delta": 0, "num": 5})()
    assert App._wheel_nudge_steps(up) == -1
    assert App._wheel_nudge_steps(down) == 1
