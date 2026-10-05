"""Small contracts for App lifecycle persistence hooks."""

from __future__ import annotations

import pytest

from stream_monitor import notifier
from stream_monitor.app import App


class _CacheSaveProbe:
    def __init__(self) -> None:
        self.saved = 0
        self.scheduled: list[int] = []

    def _save_status_cache(self) -> None:
        self.saved += 1

    def _schedule_config_save(self, *, delay_ms: int = 250) -> None:
        self.scheduled.append(delay_ms)


def test_save_status_cache_schedules_durable_config_write() -> None:
    probe = _CacheSaveProbe()

    App.save_status_cache(probe)  # type: ignore[arg-type]

    assert probe.saved == 1
    assert probe.scheduled == [500]


def test_browser_launch_fails_closed_when_profile_creation_fails(
    monkeypatch, tmp_path
) -> None:
    def fail_mkdir(*_args, **_kwargs):
        raise PermissionError("profile denied")

    monkeypatch.setattr(notifier.Path, "mkdir", fail_mkdir)
    monkeypatch.setattr(notifier, "_resolve_browser_executable", lambda _p: "chrome")
    monkeypatch.setattr(notifier, "detect_browser_family", lambda _p: "chromium")
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    monkeypatch.setattr(
        notifier.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not run"),
    )

    opened = notifier._open_with_browser_settings(
        "https://www.twitch.tv/hello",
        {
            "enabled": True,
            "browser_path": "chrome",
            "user_data_dir": str(tmp_path / "profile"),
            "per_channel_profile": False,
        },
        manage=False,
    )

    assert opened is False
