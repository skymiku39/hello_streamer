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


def test_call_after_disconnect_raises_an_oserror():
    # websocket-client raises WebSocketConnectionClosedException (not an
    # OSError) on send once the page closed; the claim-ack poll only handles
    # OSError-family errors, so the page-assist worker ended instead.
    import pytest

    client = CdpClient()
    client._ws = Mock()
    client._ws.send.side_effect = WebSocketConnectionClosedException("closed")

    with pytest.raises(ConnectionError):
        client.call("Network.getResponseBody", {"requestId": "1"}, timeout=0.5)


def test_claim_ack_poll_survives_a_closed_socket():
    from stream_monitor.twitch_claim_ack import ClaimAcknowledgements

    client = CdpClient()
    client._ws = Mock()
    client._ws.send.side_effect = WebSocketConnectionClosedException("closed")
    acks = ClaimAcknowledgements()
    acks._pending["req-1"] = True

    assert acks.poll(client) is None
