"""Quiet CDP sockets must survive until a command or actual disconnect."""

from unittest.mock import Mock

from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException

from stream_monitor.cdp_client import CdpClient


def test_reader_handles_response_after_repeated_idle_timeouts():
    client = CdpClient()
    client._ws = Mock()
    client._ws.recv.side_effect = [
        WebSocketTimeoutException("quiet"),
        WebSocketTimeoutException("still quiet"),
        '{"id": 7, "result": {"value": true}}',
        "",  # Actual EOF ends the reader.
    ]
    client._read_loop()
    assert client._pending[7]["result"] == {"value": True}
    assert client._ws.recv.call_count == 4
    assert not client.connected


def test_reader_stops_on_real_disconnect():
    client = CdpClient()
    client._ws = Mock()
    client._ws.recv.side_effect = WebSocketConnectionClosedException("disconnected")
    client._read_loop()
    assert not client.connected
    client._ws.recv.assert_called_once()


def test_stop_is_respected_during_idle_timeout():
    client = CdpClient()
    client._ws = Mock()

    def timeout_then_stop():
        client._closed.set()
        raise WebSocketTimeoutException("quiet")

    client._ws.recv.side_effect = timeout_then_stop
    client._read_loop()
    client._ws.recv.assert_called_once()
