"""Keep long-running evidence collection alive during Windows status reads."""

import json
import os
import threading
import time

import pytest

from scripts import verify_twitch_live as collector


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
