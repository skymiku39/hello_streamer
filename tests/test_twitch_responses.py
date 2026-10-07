"""Malformed Twitch responses are unavailable evidence, not offline readings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stream_monitor.fetcher.twitch import TwitchFetcher
from stream_monitor.monitor import ChannelStatus, Monitor, deps


@pytest.fixture
def response_fetcher(monkeypatch):
    fetcher = TwitchFetcher()

    def configure(payload):
        response = SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: payload,
        )
        monkeypatch.setattr(fetcher._session, "post", lambda *args, **kwargs: response)
        return fetcher

    yield configure
    fetcher._session.close()


@pytest.mark.parametrize("payload", [None, [], False, 42, "invalid"])
def test_non_object_gql_response_is_unavailable(response_fetcher, payload):
    assert response_fetcher(payload).get_stream_info("hello") is None


@pytest.mark.parametrize(
    "user",
    [[], "invalid", {"displayName": "Hello"}, {"stream": []}, {"stream": {}},
     {"stream": {"type": None}}, {"stream": {"type": []}}],
)
def test_incomplete_stream_response_is_unavailable(response_fetcher, user):
    assert response_fetcher({"data": {"user": user}}).get_stream_info("hello") is None


def test_gql_error_does_not_turn_null_stream_into_offline(response_fetcher):
    fetcher = response_fetcher({
        "data": {"user": {"displayName": "Hello", "stream": None}},
        "errors": [{"message": "stream lookup failed"}],
    })
    assert fetcher.get_stream_info("hello") is None


def test_explicit_null_stream_without_errors_is_offline(response_fetcher):
    fetcher = response_fetcher({"data": {"user": {"displayName": "Hello", "stream": None}}})
    info = fetcher.get_stream_info("hello")
    assert info is not None
    assert info.is_live is False
    assert info.display_name == "Hello"


def test_live_response_normalizes_invalid_optional_text(response_fetcher):
    fetcher = response_fetcher({"data": {"user": {
        "displayName": None,
        "stream": {"type": "live", "title": [], "createdAt": 123},
    }}})
    info = fetcher.get_stream_info("hello")
    assert info is not None and info.is_live
    assert info.title == info.started_at == info.display_name == ""


def test_valid_live_response_preserves_metadata(response_fetcher):
    fetcher = response_fetcher({"errors": [], "data": {"user": {
        "displayName": "Hello",
        "stream": {"type": "live", "title": "Playing", "createdAt": "2026-10-07T00:00:00Z"},
    }}})
    info = fetcher.get_stream_info("hello")
    assert info is not None and info.is_live
    assert info.title == "Playing"
    assert info.display_name == "Hello"
    assert info.started_at == "2026-10-07T00:00:00Z"


def test_incomplete_response_cannot_accumulate_offline_strikes(response_fetcher, monkeypatch):
    fetcher = response_fetcher({"data": {"user": {"displayName": "Hello"}}})
    monkeypatch.setattr(deps, "get_fetcher", lambda _: fetcher)
    monitor = Monitor(
        channels=[{"platform": "twitch", "name": "hello"}], db=object(),
        initial_statuses={"twitch:hello": ChannelStatus(status=True)},
    )
    for _ in range(3):
        monitor._probe_live(monitor._entries[0])
        assert monitor._offline_strikes == {}
        assert monitor.snapshot_statuses()["twitch:hello"].is_live


@pytest.mark.parametrize(
    "node",
    [None, [], "invalid", {"id": {}}, {"id": True},
     {"id": "99", "lengthSeconds": float("inf")},
     {"id": "99", "createdAt": "2026-10-07T00:00:00Z", "lengthSeconds": 10**30}],
)
def test_invalid_archive_response_is_unavailable(response_fetcher, node):
    fetcher = response_fetcher({"data": {"user": {"videos": {"edges": [{"node": node}]}}}})
    assert fetcher.get_latest_archive("hello") is None
