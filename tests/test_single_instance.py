"""Local IPC should survive incomplete clients and transient UI errors."""

from __future__ import annotations

import socket
import threading

import pytest

from stream_monitor import single_instance


@pytest.fixture
def instance_factory(monkeypatch):
    monkeypatch.setattr(single_instance, "_SOCKET_TIMEOUT", 0.02, raising=False)
    monkeypatch.setattr(single_instance, "_REQUEST_TIMEOUT", 0.3, raising=False)
    instances = []

    def create(callback):
        instance = single_instance.SingleInstance(callback, port=0)
        assert instance.try_lock()
        instances.append(instance)
        return instance

    yield create
    for instance in instances:
        instance.release()
        instance._thread.join(timeout=1)


def _connect(instance):
    return socket.create_connection(instance._server.getsockname(), timeout=1)


def test_silent_client_does_not_block_later_show_request(instance_factory):
    shown = threading.Event()
    instance = instance_factory(shown.set)
    with _connect(instance), _connect(instance) as client:
        client.sendall(b"SHOW")
        assert shown.wait(1)


def test_fragmented_show_request_is_reassembled(instance_factory):
    shown = threading.Event()
    instance = instance_factory(shown.set)
    with _connect(instance) as client:
        client.sendall(b"SH")
        client.settimeout(0.05)
        # The server must retain this incomplete request rather than close it.
        with pytest.raises(TimeoutError):
            client.recv(1)
        client.sendall(b"OW")
        assert shown.wait(1)


def test_callback_failure_does_not_kill_listener(instance_factory):
    failed = threading.Event()
    shown = threading.Event()

    def on_show():
        if not failed.is_set():
            failed.set()
            raise RuntimeError("window not ready yet")
        shown.set()

    instance = instance_factory(on_show)
    with _connect(instance) as client:
        client.sendall(b"SHOW")
    assert failed.wait(1)
    with _connect(instance) as client:
        client.sendall(b"SHOW")
    assert shown.wait(1)


def test_release_stops_listener_with_an_incomplete_client(instance_factory):
    shown = threading.Event()
    instance = instance_factory(shown.set)
    with _connect(instance) as client:
        client.sendall(b"S")
        client.settimeout(0.05)
        with pytest.raises(TimeoutError):
            client.recv(1)
        instance.release()
        instance._thread.join(timeout=0.5)
        assert not instance._thread.is_alive()
    assert not shown.is_set()


def test_existing_instance_is_signalled_and_can_release_port(instance_factory):
    shown = threading.Event()
    owner = instance_factory(shown.set)
    port = owner._server.getsockname()[1]
    second = single_instance.SingleInstance(port=port)
    assert second.try_lock() is False
    assert shown.wait(1)
    assert second._server is None
    owner.release()
    owner._thread.join(timeout=1)
    replacement = single_instance.SingleInstance(port=port)
    try:
        assert replacement.try_lock() is True
    finally:
        replacement.release()
        if replacement._thread is not None:
            replacement._thread.join(timeout=1)
