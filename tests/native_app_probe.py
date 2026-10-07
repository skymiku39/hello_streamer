"""Native App wiring checks with external effects isolated at their ports."""

from __future__ import annotations

import sys
import tempfile
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from stream_monitor import app as app_module
from stream_monitor import i18n, notifier
from stream_monitor.app import App
from stream_monitor.app_dialogs import (
    AddChannelDialog,
    AppSettingsDialog,
    BrowserSettingsDialog,
    LanguageDialog,
)
from stream_monitor.app_ui import _FONT_FAMILY, AppButton
from stream_monitor.db import SeenVideoDB
from stream_monitor.fetcher.base import StreamInfo
from stream_monitor.monitor import Monitor
from stream_monitor.monitor import deps as monitor_deps


@contextmanager
def native_app():
    monkeypatch = pytest.MonkeyPatch()
    temporary = tempfile.TemporaryDirectory(prefix="hello-native-")
    monkeypatch.setattr(app_module, "SeenVideoDB", lambda: SeenVideoDB(Path(temporary.name) / "seen.db"))
    config = {
        "channels": [{"platform": "twitch", "name": "testchannel"}],
        "check_interval": 60,
        "monitor_mode": "trigger",
        "language": "zh_TW",
        "minimize_to_tray": False,
        "window_geometry": "920x580+80+80",
        "trigger_settings": {
            "notify_on_live": False,
            "open_on_live": True,
            "after_open": "none",
            "notify_on_open_failure": False,
        },
    }
    monkeypatch.setattr(app_module.config_manager, "load", lambda: deepcopy(config))
    monkeypatch.setattr(app_module.config_manager, "save", lambda value: value)
    monkeypatch.setattr(app_module, "TrayIcon", MagicMock())
    monkeypatch.setattr(app_module, "configure_viewer_engagement", lambda _s: None)
    monkeypatch.setattr(app_module, "_reorder_debug", lambda *_a, **_kw: None)
    monkeypatch.setattr(App, "maybe_restart_dead_monitor", lambda _s: None)
    monkeypatch.setattr(Monitor, "start", lambda _s: None)
    monkeypatch.setattr(Monitor, "restart_thread", lambda _s: None)
    services = MagicMock()
    services.window.tracking_available.return_value = False
    services.window.release_keep_awake_for_closed.return_value = 0
    services.browser.open.return_value = True
    monkeypatch.setattr(app_module, "platform_services", lambda: services)
    monkeypatch.setattr(notifier, "platform_services", lambda: services)
    apps = []

    def create(*, silent=False, scale=1.0):
        ctk.set_widget_scaling(scale)
        ctk.set_window_scaling(scale)
        app = App(silent=silent)
        apps.append(app)
        return app, services

    try:
        yield create
    finally:
        for app in apps:
            app._truly_quitting = True
            app._action_coordinator.shutdown()
            app._controller.shutdown()
            app._unsub_i18n()
            app.destroy()
            app._db.close()
        monkeypatch.undo()
        temporary.cleanup()


def _pump(app, seconds=0.2):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.update()
        time.sleep(0.005)


def test_cold_start_interval_fits_without_manual_resize(native_app, silent, scale):
    app, _services = native_app(silent=silent, scale=scale)
    if silent:
        _pump(app)
        app.deiconify()
    _pump(app)
    flow = app._run_flow
    # These controls fit at the default width. Do not call reflow or change
    # geometry here: those interventions hid the startup bug in the old smoke.
    ys = [widget.winfo_y() for widget in flow._items]
    assert len(set(ys)) == 1, [(w.winfo_x(), w.winfo_y(), w.winfo_width())
                               for w in flow._items]
    last = flow._items[-1]
    assert last.winfo_x() + last.winfo_width() <= flow.winfo_width() + 2
    before = [(w.winfo_x(), w.winfo_y(), w.winfo_width()) for w in flow._items]
    _pump(app)
    assert before == [(w.winfo_x(), w.winfo_y(), w.winfo_width()) for w in flow._items]


def test_live_event_reaches_browser_through_real_app(native_app):
    app, services = native_app()
    app._on_start()
    monitor = app._controller._monitor
    info = StreamInfo(
        channel="testchannel", platform="twitch", is_live=True,
        url="https://www.twitch.tv/testchannel", title="Live", stream_status="live",
    )
    monitor._dispatch_went_live_events([(monitor._entries[0], info)])
    _pump(app)
    services.browser.open.assert_called_once_with(info, None)
    services.notification.send.assert_not_called()


def test_watch_to_trigger_opens_current_live_once(native_app, *, pending_event=False):
    app, services = native_app()
    info = StreamInfo(
        channel="testchannel", platform="twitch", is_live=True,
        url="https://www.twitch.tv/testchannel", title="Live", stream_status="live",
    )
    fetcher = SimpleNamespace(get_stream_info=lambda _name: info)
    with patch.object(monitor_deps, "get_fetcher", return_value=fetcher):
        app._on_watch()
        monitor = app._controller._monitor
        monitor._poll_cycle = 1
        monitor._tier1_probe_entries(monitor._entries)
        if not pending_event:
            _pump(app)
        services.browser.open.assert_not_called()
        # Go through the actual segmented-control callback and controller.
        app._on_mode_segment(app._mode_segment_label("trigger"))
        _pump(app)
        # The watch-cycle event may still be queued when the user switches.
        # It must not open now and again on the requested fresh poll.
        services.browser.open.assert_not_called()
        monitor._poll_cycle = 2
        monitor._tier1_probe_entries(monitor._entries)
        _pump(app)
        services.browser.open.assert_called_once_with(info, None)
        monitor._poll_cycle = 3
        monitor._tier1_probe_entries(monitor._entries)
        _pump(app)
        services.browser.open.assert_called_once()


def test_dialog_buttons_use_application_style(native_app):
    app, _services = native_app()
    _pump(app)
    callbacks = {
        "minimize_to_tray": False, "run_on_startup": False,
        "on_tray_changed": lambda _v: None,
        "on_startup_changed": lambda _v: True,
        "on_reset_launch_records": lambda: (True, "settings.reset.success"),
    }
    factories = [
        (lambda: AppSettingsDialog(app, **callbacks), "_close_btn"),
        (lambda: LanguageDialog(app), "_close_btn"),
        (lambda: AddChannelDialog(app), "_cancel_btn"),
        (lambda: BrowserSettingsDialog(app, {}), "_cancel_btn"),
    ]
    for factory, name in factories:
        dialog = factory()
        try:
            # CTk briefly withdraws new Windows dialogs while applying their
            # title-bar theme. Wait for mapping, not a fixed startup delay.
            deadline = time.monotonic() + 3
            while not dialog.winfo_ismapped() and time.monotonic() < deadline:
                _pump(app, 0.05)
            for language in ("zh_TW", "en", "ja"):
                i18n.set_language(language)
                _pump(app)
                button = getattr(dialog, name)
                assert isinstance(button, AppButton)
                assert button.cget("font").cget("family") == _FONT_FAMILY
                assert button.cget("fg_color") != "transparent"
                assert button.winfo_ismapped(), (type(dialog).__name__, language)
                assert button.winfo_reqwidth() >= button.cget("font").measure(button.cget("text"))
                assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
        finally:
            dialog.destroy()


if __name__ == "__main__":
    with native_app() as create:
        if sys.argv[1] == "layout":
            test_cold_start_interval_fits_without_manual_resize(create, sys.argv[2] == "silent", float(sys.argv[3]))
        elif sys.argv[1] == "action":
            test_live_event_reaches_browser_through_real_app(create)
        elif sys.argv[1] == "watch_to_trigger":
            test_watch_to_trigger_opens_current_live_once(create)
        elif sys.argv[1] == "watch_to_trigger_pending":
            test_watch_to_trigger_opens_current_live_once(create, pending_event=True)
        elif sys.argv[1] == "dialogs":
            test_dialog_buttons_use_application_style(create)
        else:
            raise ValueError(sys.argv[1])
