from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from stream_monitor.twitch_acceptance import record_evidence

BASE = datetime(2026, 11, 4, tzinfo=timezone.utc)


def sample(minutes, **values):
    data = {
        "channel": "one", "ts_utc": (BASE + timedelta(minutes=minutes)).isoformat(),
        "points_balance": 100, "streak_count": 10,
        "stream_state": "live", "stream_started_at": (BASE + timedelta(minutes=60)).isoformat(),
        "prior_stream_ended_at": (BASE - timedelta(minutes=30)).isoformat(),
        "playing_channels": ["one", "two"],
        "source": "real_account_twitch_popout_ui", "synthetic_fixture": False,
        "screenshot_version": 4, "streak_screenshot": "/fixture/native-streak.png",
    }
    data.update(values)
    return data


def state():
    result = {"scope": ["one", "two"], "channels": {}}
    record_evidence(result, sample(0, stream_state="offline", playing_channels=[]))
    return result


def test_real_points_need_adjacent_same_broadcast_overlap_samples():
    result = state()
    record_evidence(result, sample(70))
    record_evidence(result, sample(71, points_balance=110))
    proof = result["channels"]["one"]["points_proof"]
    assert (proof["before"], proof["after"]) == (100, 110)
    assert proof["simultaneous_channels"] == ["one", "two"]
    assert result["result"] == "pending"  # Other channel still unverified.


@pytest.mark.parametrize("changes", [
    {"playing_channels": ["one"]}, {"stream_state": "offline"},
    {"points_balance": None}, {"synthetic_fixture": True},
    {"source": "synthetic_fixture"}, {"points_balance": 100},
    {"stream_started_at": (BASE + timedelta(minutes=65)).isoformat()},
])
def test_inconclusive_or_synthetic_points_never_pass(changes):
    result = state()
    record_evidence(result, sample(70))
    record_evidence(result, sample(71, **{"points_balance": 110, **changes}))
    assert result["channels"]["one"]["points_proof"] is None


def test_a_gap_or_lost_overlap_breaks_points_comparison():
    for middle in [sample(71, playing_channels=[]), sample(71, points_balance=None)]:
        result = state()
        record_evidence(result, sample(70))
        record_evidence(result, middle)
        record_evidence(result, sample(72, points_balance=110))
        assert result["channels"]["one"]["points_proof"] is None
    result = state()
    record_evidence(result, sample(70))
    record_evidence(result, sample(74, points_balance=110))
    assert result["channels"]["one"]["points_proof"] is None


def test_streak_needs_new_broadcast_age_gap_and_server_ui_increment():
    result = state()
    record_evidence(result, sample(69))
    record_evidence(result, sample(70, streak_count=11))
    proof = result["channels"]["one"]["streak_proof"]
    assert (proof["before"], proof["after"]) == (10, 11)
    assert result["result"] == "pending"


@pytest.mark.parametrize("changes", [
    {"streak_count": None}, {"streak_count": 10}, {"streak_count": 0},
    {"playing_channels": ["one"]}, {"playing_channels": []},
    {"stream_state": "offline"}, {"stream_started_at": None},
    {"stream_started_at": (BASE - timedelta(minutes=1)).isoformat()},
    {"synthetic_fixture": True},
    {"streak_screenshot": None}, {"screenshot_version": 1},
])
def test_points_or_playing_do_not_substitute_for_cross_session_streak(changes):
    result = state()
    record_evidence(result, sample(69))
    record_evidence(result, sample(70, **{"streak_count": 11, **changes}))
    assert result["channels"]["one"]["streak_proof"] is None


def test_too_young_or_too_close_broadcast_stays_pending():
    result = state()
    record_evidence(result, sample(64))
    record_evidence(result, sample(65, streak_count=11))
    assert result["channels"]["one"]["streak_proof"] is None
    result = state()
    result["channels"]["one"]["baseline"]["prior_stream_ended_at"] = (
        BASE + timedelta(minutes=45)).isoformat()
    record_evidence(result, sample(69))
    record_evidence(result, sample(70, streak_count=11))
    assert result["channels"]["one"]["streak_proof"] is None


def test_completion_requires_both_kinds_of_proof_for_every_channel():
    result = state()
    record_evidence(result, sample(69))
    record_evidence(result, sample(70, streak_count=11))
    record_evidence(result, sample(71, points_balance=110, streak_count=11))
    assert result["channels"]["one"]["result"] == "passed"
    assert result["result"] == "pending"
    result["channels"]["two"] = deepcopy(result["channels"]["one"])
    record_evidence(result, sample(72, points_balance=110, streak_count=11))
    assert result["result"] == "passed"


def test_unbounded_points_are_explicitly_not_applicable_and_never_passed():
    result = state()
    record_evidence(result, sample(70, points_unbounded=True, points_balance=None))
    assert result["channels"]["one"]["result"] == "not_applicable"
    assert result["channels"]["one"]["points_proof"] is None
    assert result["result"] == "pending"


def test_counter_increase_before_multiwatch_does_not_prove_multiwatch_credit():
    result = state()
    record_evidence(result, sample(70, streak_count=11))
    record_evidence(result, sample(71, streak_count=11, points_balance=110))
    assert result["channels"]["one"]["points_proof"] is not None
    assert result["channels"]["one"]["streak_proof"] is None


def test_risk_warning_and_last_native_read_survive_an_unavailable_ui_sample():
    result = state()
    risk = sample(69, streak_status="at_risk")
    record_evidence(result, risk)
    record_evidence(result, sample(70, streak_count=None, streak_screenshot=None))
    assert result["channels"]["one"]["last_streak_risk"] == risk
    assert result["channels"]["one"]["last_native_streak"] == risk


def test_at_risk_counter_does_not_prove_new_broadcast_credit():
    result = state()
    record_evidence(result, sample(69))
    record_evidence(result, sample(70, streak_count=11, streak_status="at_risk"))
    assert result["channels"]["one"]["streak_proof"] is None


def test_next_offline_baseline_tracks_actual_last_end_and_preserves_first_baseline():
    result = state()
    initial = deepcopy(result["channels"]["one"]["baseline"])
    next_baseline = sample(120, stream_state="offline", playing_channels=[],
                           prior_stream_ended_at=(BASE + timedelta(minutes=110)).isoformat())
    record_evidence(result, next_baseline)
    row = result["channels"]["one"]
    assert row["initial_baseline"] == initial
    assert row["baseline"] == next_baseline
    record_evidence(result, sample(131, stream_started_at=(BASE + timedelta(minutes=120)).isoformat()))
    record_evidence(result, sample(132, streak_count=11,
                                  stream_started_at=(BASE + timedelta(minutes=120)).isoformat()))
    assert row["streak_proof"] is None  # Only ten minutes since the actual last end.
