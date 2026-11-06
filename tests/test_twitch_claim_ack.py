import json
from unittest.mock import Mock

import pytest

from stream_monitor.twitch_claim_ack import (
    ClaimAcknowledgements,
    ClaimResult,
    parse_claim_response,
)
from stream_monitor.twitch_page_assist import _PageAssistWorker
from stream_monitor.viewer_engagement_model import ViewerEngagementSettings

REJECTED = {"data": {"claimCommunityPoints": None}, "errors": [
    {"message": "failed integrity check", "extensions": {"code": "IntegrityCheckFailed"}},
]}
ACCEPTED = {"data": {"claimCommunityPoints": {"currentPoints": {"balance": 250}}}}
NATIVE_ACCEPTED = {"data": {"claimCommunityPoints": {
    "claim": {"pointsEarnedBaseline": 50, "pointsEarnedTotal": 50},
    "currentPoints": 138262, "error": None,
}}}


def event(tracker, request_id="1", operation="ClaimCommunityPoints"):
    tracker.observe("Network.requestWillBeSent", {"requestId": request_id, "request": {
        "url": "https://gql.twitch.tv/gql", "postData": json.dumps({"operationName": operation}),
        "headers": {"Authorization": "not-retained"},
    }})
    tracker.observe("Network.loadingFinished", {"requestId": request_id})


def test_native_integrity_failure_is_rejection_even_when_data_is_null():
    assert parse_claim_response([REJECTED]) == ClaimResult("rejected", "IntegrityCheckFailed")
    assert parse_claim_response([ACCEPTED, REJECTED]).status == "rejected"
    assert parse_claim_response([ACCEPTED]) == ClaimResult("accepted", balance=250)
    assert parse_claim_response({"data": None, "errors": REJECTED["errors"]}).status == "rejected"


def test_real_twitch_scalar_current_points_response_is_accepted():
    assert parse_claim_response([NATIVE_ACCEPTED]) == ClaimResult("accepted", balance=138262)


@pytest.mark.parametrize("balance", [None, True, "138262", -1, float("inf")])
def test_invalid_scalar_current_points_is_not_acceptance(balance):
    assert parse_claim_response({"data": {"claimCommunityPoints": {"currentPoints": balance}}}) is None


@pytest.mark.parametrize("response", [{}, {"data": {"claimCommunityPoints": None}},
    {"data": {"claimCommunityPoints": {"currentPoints": {"balance": True}}}},
    {"data": {"claimCommunityPoints": {"currentPoints": {"balance": float('nan')}}}}])
def test_unknown_response_is_not_success(response):
    assert parse_claim_response(response) is None


def test_network_reader_only_queues_matching_claims_and_never_retains_credentials():
    tracker = ClaimAcknowledgements()
    client = Mock()
    client.call.return_value = {"body": json.dumps(REJECTED)}
    event(tracker, "unrelated", "ChannelPointsContext")
    assert tracker.poll(client) is None
    client.call.assert_not_called()
    event(tracker)
    assert tracker._pending == {"1": True}
    assert tracker.poll(client) == ClaimResult("rejected", "IntegrityCheckFailed")
    assert tracker.poll(client) is None
    assert client.call.call_count == 1


def test_new_attempt_does_not_accept_previous_response_and_tracking_is_bounded():
    tracker = ClaimAcknowledgements()
    event(tracker, "previous")
    tracker.begin(Mock())
    assert tracker.poll(Mock()) is None
    for i in range(40):
        event(tracker, str(i))
    assert len(tracker._pending) == 16


def test_native_retry_success_is_not_discarded_after_rejected_response():
    tracker = ClaimAcknowledgements()
    client = Mock()
    client.call.side_effect = [{"body": json.dumps(REJECTED)}, {"body": json.dumps(ACCEPTED)}]
    event(tracker, "first")
    event(tracker, "native-retry")
    assert tracker.poll(client) == ClaimResult("accepted", balance=250)
    assert client.call.call_count == 2
    assert tracker.poll(client) is None


def test_late_native_acceptance_clears_rejection_without_repeating_click(monkeypatch):
    from stream_monitor.page_assist_status import drain_page_assist_status

    drain_page_assist_status()
    worker = _PageAssistWorker("https://www.twitch.tv/test", 1234, ViewerEngagementSettings())
    worker._claim_blocked = True
    client = Mock()
    monkeypatch.setattr(worker._claim_ack, "poll", lambda _c: ClaimResult("accepted", balance=250))
    assert worker._read_claim_ack(client).status == "accepted"
    assert not worker._claim_blocked
    assert drain_page_assist_status() == [(worker.url, "")]
    client.click_at.assert_not_called()


def test_rejected_claim_pauses_only_claims_until_explicit_off_on(monkeypatch):
    settings = ViewerEngagementSettings(enabled=True, page_assist_enabled=True, claim_channel_points=True)
    worker = _PageAssistWorker("https://www.twitch.tv/test", 1234, settings)
    client = Mock()
    monkeypatch.setattr(worker, "_ensure_chat_visible", lambda _c: "visible")
    monkeypatch.setattr(worker, "_sleep", lambda _s: True)
    monkeypatch.setattr(worker, "_probe_claim", lambda _c: {
        "found": True, "x": 50, "y": 30, "left": 30, "top": 20, "width": 40, "height": 20,
    })
    monkeypatch.setattr(worker._claim_ack, "poll", lambda _c: ClaimResult("rejected", "IntegrityCheckFailed"))
    assert not worker._try_claim(client)
    assert worker._claim_blocked and not worker._stop.is_set()
    client.click_at.assert_called_once()
    worker.update_settings(settings)
    assert not worker._try_claim(client)
    client.click_at.assert_called_once()
    worker.update_settings(ViewerEngagementSettings(enabled=True, page_assist_enabled=True, claim_channel_points=False))
    worker.update_settings(settings)
    assert not worker._claim_blocked and worker.settings.page_assist_active()


def test_disappearing_button_without_server_ack_is_not_success(monkeypatch):
    worker = _PageAssistWorker("https://www.twitch.tv/test", 1234, ViewerEngagementSettings())
    client = Mock()
    monkeypatch.setattr(worker, "_ensure_chat_visible", lambda _c: "visible")
    monkeypatch.setattr(worker, "_sleep", lambda _s: True)
    monkeypatch.setattr(worker, "_probe_claim", lambda _c: {"found": True, "x": 20, "y": 20})
    monkeypatch.setattr(worker, "_claim_still_present", lambda *_a: {"present": False})
    assert not worker._try_claim(client)
    assert not worker._claim_blocked
    client.click_at.assert_called_once()


def test_playback_or_passive_points_cannot_clear_rejected_claim_notice():
    from stream_monitor.page_assist_status import drain_page_assist_status

    drain_page_assist_status()
    worker = _PageAssistWorker("https://www.twitch.tv/test", 1234, ViewerEngagementSettings())
    worker._claim_blocked = True
    worker._publish_status("")
    assert drain_page_assist_status() == [(worker.url, "claim_rejected")]
