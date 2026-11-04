"""Evidence rules for real-account Twitch acceptance, never synthetic credit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def epoch(value: str | None) -> float | None:
    try:
        stamp = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def record_evidence(state: dict[str, Any], sample: dict[str, Any]) -> None:
    """Accumulate independent numeric points and cross-broadcast UI proofs."""
    channel = sample["channel"]
    if channel not in state["scope"]:
        raise ValueError("Evidence channel is outside the acceptance scope")
    row = state["channels"].setdefault(channel, {
        "baseline": None, "points_proof": None, "streak_proof": None,
    })
    row["latest"] = sample
    now = epoch(sample["ts_utc"])
    balance = sample.get("points_balance")
    count = sample.get("streak_count")
    if sample.get("synthetic_fixture") or sample.get("source") != "real_account_twitch_popout_ui":
        row["result"] = "pending"
        return
    if sample.get("points_unbounded"):
        row["eligibility"] = "unbounded_points_cannot_verify_numeric_increase"
        row["result"] = "not_applicable"
        state["updated_utc"] = sample["ts_utc"]
        return
    native_count = (isinstance(count, int) and not isinstance(count, bool)
                    and sample.get("screenshot_version") == 4
                    and sample.get("streak_screenshot"))
    if native_count:
        row["last_native_streak"] = sample
        if sample.get("streak_status") == "at_risk":
            row["last_streak_risk"] = sample
    replace_baseline = (row["baseline"] is None
                        or row["baseline"].get("screenshot_version") != 4)
    baseline = row.get("baseline")
    if baseline and row.get("streak_proof") is None:
        prior_end = epoch(baseline.get("prior_stream_ended_at"))
        new_end = epoch(sample.get("prior_stream_ended_at"))
        replace_baseline |= new_end is not None and (prior_end is None or new_end > prior_end)
    if (replace_baseline and sample.get("stream_state") == "offline"
            and native_count and sample.get("streak_status", "active") == "active"):
        if row["baseline"]:
            row.setdefault("initial_baseline", row["baseline"])
        row["baseline"] = sample

    previous = row.get("overlap_sample")
    overlap = set(sample.get("playing_channels", []))
    started = sample.get("stream_started_at")
    active = (sample.get("stream_state") == "live" and epoch(started) is not None
              and channel in overlap and len(overlap) >= 2
              and isinstance(balance, int) and not isinstance(balance, bool))
    if active and previous is not None and now is not None:
        prior_time = epoch(previous["ts_utc"])
        shared = overlap.intersection(previous.get("playing_channels", []))
        if (prior_time is not None and 0 < now - prior_time <= 180
                and previous.get("stream_started_at") == started
                and len(shared) >= 2 and channel in shared
                and balance > previous["points_balance"]):
            row["points_proof"] = {
                "before": previous["points_balance"], "after": balance,
                "before_utc": previous["ts_utc"], "after_utc": sample["ts_utc"],
                "stream_started_at": started, "simultaneous_channels": sorted(shared),
                "source": "real_account_points_ui",
                "gain_cause": "unattributed; app bonus claims may also increase balance",
            }
        prior_count = previous.get("streak_count")
        if (prior_time is not None and 0 < now - prior_time <= 180
                and previous.get("stream_started_at") == started
                and len(shared) >= 2 and channel in shared
                and isinstance(count, int) and not isinstance(count, bool)
                and isinstance(prior_count, int) and not isinstance(prior_count, bool)
                and sample.get("streak_status", "active") == "active"
                and previous.get("streak_status", "active") == "active"
                and count > prior_count):
            row["streak_overlap_candidate"] = {
                "before": prior_count, "after": count,
                "observed_before_utc": previous["ts_utc"],
                "observed_increment_utc": sample["ts_utc"],
                "stream_started_at": started, "simultaneous_channels": sorted(shared),
                "after_screenshot": sample.get("streak_screenshot"),
            }
    row["overlap_sample"] = sample if active else None

    baseline = row.get("baseline")
    start_time = epoch(started)
    if baseline is not None and now is not None and start_time is not None:
        base_time = epoch(baseline["ts_utc"])
        last_end = epoch(baseline.get("prior_stream_ended_at"))
        candidate = row.get("streak_overlap_candidate", {})
        if (sample.get("stream_state") == "live" and channel in overlap and len(overlap) >= 2
                and baseline.get("screenshot_version") == 4
                and sample.get("screenshot_version") == 4 and sample.get("streak_screenshot")
                and sample.get("streak_status", "active") == "active"
                and candidate.get("stream_started_at") == started
                and candidate.get("after") == count
                and base_time is not None and start_time > base_time
                and last_end is not None and start_time - last_end >= 1800
                and now - start_time >= 600 and isinstance(count, int)
                and not isinstance(count, bool) and count > baseline["streak_count"]):
            row["streak_proof"] = {
                **candidate,
                "before": baseline["streak_count"], "after": count,
                "baseline_utc": baseline["ts_utc"], "after_utc": sample["ts_utc"],
                "prior_stream_ended_at": baseline["prior_stream_ended_at"],
                "stream_started_at": started,
                "source": "real_account_native_watch_streak_ui",
                "before_screenshot": baseline.get("streak_screenshot"),
                "after_screenshot": sample.get("streak_screenshot"),
            }
    row["result"] = (
        "passed" if row.get("points_proof") and row.get("streak_proof") else "pending"
    )
    state["updated_utc"] = sample["ts_utc"]
    results = [state["channels"].get(name, {}).get("result") for name in state["scope"]]
    state["result"] = "pending"
    if all(result == "passed" for result in results):
        state["result"] = "passed"
    elif results.count("passed") >= 2 and all(
        result in {"passed", "not_applicable"} for result in results
    ):
        state["result"] = "completed_with_not_applicable"
