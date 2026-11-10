from __future__ import annotations

import json
import logging

from stream_monitor import runtime_diagnostics as rd


def test_unfinished_session_is_retained_and_reported(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(rd.faulthandler, "is_enabled", lambda: True)
    first = rd.RuntimeDiagnostics(tmp_path)
    first.start("test")
    old = json.loads(first.path.read_text())

    with caplog.at_level(logging.WARNING):
        restarted = rd.RuntimeDiagnostics(tmp_path)
        restarted.start("test")

    assert "ended without an exit record" in caplog.text
    assert json.loads((tmp_path / "runtime-session.previous.json").read_text()) == old
    restarted.finish("tray_menu")
    saved = json.loads(restarted.path.read_text())
    assert saved["state"] == "exited"
    assert saved["exit_reason"] == "tray_menu"
    assert saved["ended_at"] >= saved["started_at"]


def test_normal_exit_is_not_reported_as_crash(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(rd.faulthandler, "is_enabled", lambda: True)
    first = rd.RuntimeDiagnostics(tmp_path)
    first.start("test")
    first.finish("window_close")
    with caplog.at_level(logging.WARNING):
        second = rd.RuntimeDiagnostics(tmp_path)
        second.start("test")
        second.finish("requested")
    assert "ended without an exit record" not in caplog.text
    assert not (tmp_path / "runtime-session.previous.json").exists()


def test_unwritable_diagnostics_do_not_prevent_start_or_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(rd.faulthandler, "is_enabled", lambda: True)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    session = rd.RuntimeDiagnostics(blocked)
    session.start("test")
    session.finish("requested")


def test_native_crash_file_stays_open_until_handler_is_disabled(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rd.faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(rd.faulthandler, "enable", lambda **kw: calls.append(kw))
    session = rd.RuntimeDiagnostics(tmp_path)
    session.start("test")
    output = calls[0]["file"]
    assert not output.closed
    assert calls[0]["all_threads"] is True

    def disable():
        assert not output.closed
        calls.append("disabled")

    monkeypatch.setattr(rd.faulthandler, "disable", disable)
    session.finish("requested")
    assert calls[-1] == "disabled"
    assert output.closed
