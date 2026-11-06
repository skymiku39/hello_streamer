"""Keep long-running evidence collection alive during Windows status reads."""

import json
import os
import threading
import time

import pytest

from scripts import verify_twitch_live as collector
from stream_monitor.cdp_client import CdpAttachResult


@pytest.mark.skipif(os.name != "nt", reason="Windows destination-sharing contract")
def test_checkpoint_waits_for_real_reader_then_replaces_complete_json(tmp_path):
    path = tmp_path / "acceptance.json"
    old = {"result": "pending", "channels": {"one": {"streak_count": 1}}}
    new = {"result": "passed", "channels": {"one": {"streak_count": 2}}}
    collector.atomic_json(path, old)
    errors = []

    def write():
        try:
            collector.atomic_json(path, new)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=write)
    try:
        with path.open("rb") as reader:
            worker.start()
            deadline = time.monotonic() + 1
            while not path.with_suffix(".tmp").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert path.with_suffix(".tmp").exists()
            worker.join(timeout=0.15)
            assert worker.is_alive(), errors
            assert json.load(reader) == old
    finally:
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert not errors
    assert json.loads(path.read_text(encoding="utf-8")) == new
    assert not path.with_suffix(".tmp").exists()


def test_permanent_denial_preserves_previous_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "acceptance.json"
    collector.atomic_json(path, {"result": "pending"})

    def denied(*_args):
        raise PermissionError("checkpoint remains locked")

    clock = iter([0.0, 4.0])
    monkeypatch.setattr(collector.os, "replace", denied)
    monkeypatch.setattr(collector.time, "monotonic", lambda: next(clock))
    with pytest.raises(PermissionError, match="remains locked"):
        collector.atomic_json(path, {"result": "passed"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"result": "pending"}
    assert json.loads(path.with_suffix(".tmp").read_text(encoding="utf-8")) == {
        "result": "passed",
    }


def test_non_sharing_write_failure_is_not_retried(tmp_path, monkeypatch):
    path = tmp_path / "acceptance.json"
    calls = []

    def failed(*_args):
        calls.append(1)
        raise OSError("storage unavailable")

    monkeypatch.setattr(collector.os, "replace", failed)
    with pytest.raises(OSError, match="storage unavailable"):
        collector.atomic_json(path, {"result": "pending"})
    assert len(calls) == 1


def test_selected_acceptance_does_not_wait_for_unselected_guest_channel():
    config = {"channels": [
        {"platform": "twitch", "name": "guest"},
        {"platform": "twitch", "name": "priority_one"},
        {"platform": "twitch", "name": "priority_two"},
        {"platform": "twitch", "name": "disabled", "enabled": False},
        {"platform": "youtube", "name": "other"},
    ]}
    assert collector.acceptance_scope(config, ["PRIORITY_TWO", "priority_one", "priority_one"]) == [
        "priority_one", "priority_two",
    ]
    assert collector.acceptance_scope(config, None) == ["guest", "priority_one", "priority_two"]
    for names in [["disabled"], ["other"], ["missing"], []]:
        with pytest.raises(ValueError):
            collector.acceptance_scope(config, names)


def test_reserved_cold_start_port_is_not_a_running_browser():
    assert not collector.existing_browser_ready(CdpAttachResult(
        ok=True, port=12345, reason="allocated", pass_debugging_flag=True))
    assert not collector.existing_browser_ready(CdpAttachResult(
        ok=True, port=0, reason="ephemeral", pass_debugging_flag=True))
    assert collector.existing_browser_ready(CdpAttachResult(ok=True, port=12345, reason="process"))


def test_two_tabs_in_one_window_cannot_pass_independent_playback():
    ids = {"one": 10, "two": 10, "three": 20}
    assert not collector.independent_window("one", ids)
    assert not collector.independent_window("two", ids)
    assert not collector.independent_window("missing", ids)
    assert collector.independent_window("three", ids)


def test_inspector_replaces_only_owned_chat_tab_and_preserves_playback():
    from unittest.mock import Mock

    pages = [{"id": "player", "url": "https://www.twitch.tv/one"},
             {"id": "inspector", "url": "https://www.twitch.tv/popout/one/chat"}]
    client = Mock()
    windows = {"player": 10, "inspector": 10, "new-inspector": 20}
    def call(method, params):
        if method == "Browser.getWindowForTarget":
            return {"windowId": windows[params["targetId"]]}
        if method == "Target.createTarget":
            assert params["newWindow"] is True
            return {"targetId": "new-inspector"}
        assert method == "Target.closeTarget" and params["targetId"] == "inspector"
        return {"success": True}
    client.call.side_effect = call
    assert collector.ensure_inspector_window(client, pages, "inspector", "one")[0] == "new-inspector"
    client.reset_mock()
    assert collector.ensure_inspector_window(client, pages, "player", "one")[0] == "new-inspector"
    assert all(c.args[0] != "Target.closeTarget" for c in client.call.call_args_list)
