from __future__ import annotations

from types import SimpleNamespace

import pytest

from stream_monitor import app as app_module
from stream_monitor.app import App


def _probe(channels: list[dict]) -> SimpleNamespace:
    calls: list[str] = []
    return SimpleNamespace(
        config={"channels": channels},
        calls=calls,
        wait_window=lambda dialog: None,
        _add_channel_row=lambda ch: calls.append("row"),
        _save_config=lambda: calls.append("save"),
        _controller=SimpleNamespace(
            update_channels=lambda chs: calls.append("update")
        ),
        refresh_points_priority_allocation=lambda **kw: calls.append("points"),
    )


def _run(monkeypatch, probe: SimpleNamespace, result: dict) -> None:
    monkeypatch.setattr(
        app_module,
        "AddChannelDialog",
        lambda parent: SimpleNamespace(result=result),
    )
    App._on_add_channel(probe)


@pytest.mark.parametrize(
    "dialog_result",
    [
        {"platform": "twitch", "name": "abc", "display_name": "New Name"},
        {"platform": "twitch", "name": "ABC"},
    ],
)
def test_add_existing_channel_is_skipped_by_key(monkeypatch, dialog_result) -> None:
    existing = {
        "platform": "twitch",
        "name": "abc",
        "display_name": "Old Name",
        "enabled": True,
        "channel_mode": "monitor",
        "monitor_only": True,
    }
    probe = _probe([existing])

    _run(monkeypatch, probe, dialog_result)

    assert probe.config["channels"] == [existing]
    assert probe.calls == []


def test_add_new_channel_is_appended(monkeypatch) -> None:
    existing = {"platform": "twitch", "name": "abc", "enabled": True}
    new = {"platform": "twitch", "name": "other"}
    probe = _probe([existing])

    _run(monkeypatch, probe, new)

    assert probe.config["channels"] == [existing, new]
    assert probe.calls == ["row", "save", "update", "points"]
