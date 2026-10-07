"""Fault-injection contracts for App non-UI keep-alive / recovery paths."""

from __future__ import annotations

import logging
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from stream_monitor import app as app_module
from stream_monitor.app import App
from stream_monitor.channel_policy import (
    TRIGGER_MODE,
    TRIGGER_ONCE_MODE,
    WATCH_ONCE_MODE,
    mode_for_silent_start,
)


class _SchedulerProbe:
    def __init__(self) -> None:
        self._truly_quitting = False
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay_ms: int, callback: object) -> str:
        self.after_calls.append((delay_ms, callback))
        return "after-token"


def test_monitor_health_check_reschedules_after_controller_failure() -> None:
    probe = _SchedulerProbe()
    probe._monitor_health_check = types.MethodType(  # type: ignore[method-assign]
        App._monitor_health_check, probe
    )

    def boom() -> None:
        raise RuntimeError("health boom")

    probe.maybe_restart_dead_monitor = boom  # type: ignore[attr-defined]

    probe._monitor_health_check()

    assert len(probe.after_calls) == 1
    assert probe.after_calls[0][0] == 10_000
    assert probe.after_calls[0][1] == probe._monitor_health_check


def test_poll_events_reschedules_after_tick_failure() -> None:
    probe = _SchedulerProbe()
    probe._poll_events = types.MethodType(App._poll_events, probe)  # type: ignore[method-assign]
    probe._controller = SimpleNamespace(  # type: ignore[attr-defined]
        tick=lambda: (_ for _ in ()).throw(RuntimeError("tick boom"))
    )

    probe._poll_events()

    assert len(probe.after_calls) == 1
    assert probe.after_calls[0][0] == 80
    assert probe.after_calls[0][1] == probe._poll_events


def test_save_config_keeps_app_alive_on_oserror(monkeypatch) -> None:
    probe = SimpleNamespace(
        config={},
        _cancel_scheduled_config_save=lambda: None,
        geometry=lambda: "800x600",
    )

    def boom(_config):
        raise OSError("disk locked")

    monkeypatch.setattr(app_module.config_manager, "save", boom)

    App._save_config(probe)  # type: ignore[arg-type]


def test_reset_persist_config_still_propagates_oserror(monkeypatch) -> None:
    """Durable reset path must not swallow save failures (unlike _save_config)."""

    class _Probe:
        config: dict = {"channels": []}
        _db = object()
        _controller = object()
        _action_coordinator = object()
        _channel_rows: list = []

        def _cancel_scheduled_config_save(self) -> None:
            return None

        def geometry(self) -> str:
            return "800x600"

    probe = _Probe()

    def boom(_config):
        raise OSError("config locked")

    def fake_reset(**kwargs):
        kwargs["persist_config"](probe.config)
        return SimpleNamespace(ok=True, error_key=None)

    monkeypatch.setattr(app_module.config_manager, "save", boom)
    monkeypatch.setattr(app_module, "reset_launch_records", fake_reset)
    monkeypatch.setattr(app_module, "portable_paths", lambda: SimpleNamespace())

    with pytest.raises(OSError, match="config locked"):
        App._reset_launch_records_action(probe)  # type: ignore[arg-type]


def test_silent_start_recovers_one_shot_and_rejects_non_str_mode() -> None:
    saved_mode: object = TRIGGER_ONCE_MODE
    if not isinstance(saved_mode, str):
        saved_mode = TRIGGER_MODE
    recovered = mode_for_silent_start(saved_mode)
    assert recovered == TRIGGER_MODE
    assert recovered != TRIGGER_ONCE_MODE

    saved_mode = WATCH_ONCE_MODE
    recovered = mode_for_silent_start(saved_mode)
    assert recovered == "watch"

    saved_mode = 123
    if not isinstance(saved_mode, str):
        saved_mode = TRIGGER_MODE
    assert mode_for_silent_start(saved_mode) == TRIGGER_MODE


def test_report_callback_exception_logs_tk_failure(caplog) -> None:
    probe = object.__new__(App)
    with caplog.at_level(logging.CRITICAL, logger=app_module.logger.name):
        App.report_callback_exception(
            probe, RuntimeError, RuntimeError("tk boom"), None
        )
    assert "Unhandled Tk callback exception" in caplog.text


def test_main_releases_lock_when_app_constructor_fails(
    monkeypatch, tmp_path: Path
) -> None:
    released: list[bool] = []

    class _Lock:
        _on_show = None

        def try_lock(self) -> bool:
            return True

        def release(self) -> None:
            released.append(True)

    class _NoFileHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            return None

    monkeypatch.setattr(app_module, "SingleInstance", _Lock)
    monkeypatch.setattr(app_module, "_check_writable", lambda _path: None)
    monkeypatch.setattr(
        app_module,
        "portable_paths",
        lambda: SimpleNamespace(
            root=tmp_path,
            logs_dir=tmp_path / "logs",
            application_log=tmp_path / "logs" / "app.log",
        ),
    )
    monkeypatch.setattr(
        app_module.config_manager,
        "load",
        lambda: {"language": "en-US"},
    )
    monkeypatch.setattr(app_module.i18n, "set_language", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module.i18n, "normalize", lambda value: value)
    monkeypatch.setattr(app_module.sys, "argv", ["stream-monitor"])
    monkeypatch.setattr(
        app_module.logging.handlers,
        "RotatingFileHandler",
        lambda *_a, **_k: _NoFileHandler(),
    )
    monkeypatch.setattr(
        app_module,
        "App",
        lambda silent=False: (_ for _ in ()).throw(RuntimeError("boot failed")),
    )

    previous_hook = threading.excepthook
    try:
        with pytest.raises(RuntimeError, match="boot failed"):
            app_module.main()
    finally:
        threading.excepthook = previous_hook

    assert released == [True]
