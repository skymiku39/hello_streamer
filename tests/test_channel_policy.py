"""Exhaustive tests for the pure per-channel decision policy."""

from __future__ import annotations

import itertools

from stream_monitor.channel_policy import (
    LiveActionDecision,
    effective_action,
    resolve_live_action,
    should_close_on_offline,
    should_prune_blank_tabs,
)

_ACTIONS = ("open_and_stop", "open_and_keep", "notify_only", "open_and_exit")


def test_effective_action_narrows_by_stream_status() -> None:
    for action in _ACTIONS:
        assert effective_action(action, "live") == action
        assert effective_action(action, "") == action  # empty ⇒ live
        assert effective_action(action, "upcoming") == "notify_only"
        assert effective_action(action, "video") is None


def test_resolve_live_action_suppressed_outside_trigger() -> None:
    for mode in ("idle", "watch"):
        decision = resolve_live_action(
            mode=mode,
            monitor_only=False,
            configured_action="open_and_stop",
            stream_status="live",
        )
        assert decision == LiveActionDecision(action=None, suppressed_reason="mode")


def test_resolve_live_action_suppressed_for_monitor_only() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=True,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action is None
    assert decision.suppressed_reason == "monitor_only"


def test_resolve_live_action_video_does_nothing() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="video",
    )
    assert decision.action is None
    assert decision.suppressed_reason == "video"


def test_resolve_live_action_upcoming_notifies_only() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="upcoming",
    )
    assert decision.action == "notify_only"
    assert decision.opens_window is False
    assert decision.triggers_stop is False
    assert decision.triggers_exit is False


def test_resolve_live_action_open_and_stop_flags() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action == "open_and_stop"
    assert decision.opens_window is True
    assert decision.triggers_stop is True
    assert decision.triggers_exit is False


def test_resolve_live_action_open_and_exit_flags() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_exit",
        stream_status="live",
    )
    assert decision.action == "open_and_exit"
    assert decision.opens_window is True
    assert decision.triggers_stop is False
    assert decision.triggers_exit is True


def test_resolve_live_action_open_and_keep_no_lifecycle_flags() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_keep",
        stream_status="live",
    )
    assert decision.action == "open_and_keep"
    assert decision.opens_window is True
    assert decision.triggers_stop is False
    assert decision.triggers_exit is False


def test_resolve_live_action_matrix_only_trigger_active_channels_act() -> None:
    """Exhaustive: side-effects only when trigger mode AND not monitor_only."""
    for mode, monitor_only, action, status in itertools.product(
        ("idle", "trigger", "watch"),
        (False, True),
        _ACTIONS,
        ("live", "upcoming", "video", ""),
    ):
        decision = resolve_live_action(
            mode=mode,
            monitor_only=monitor_only,
            configured_action=action,
            stream_status=status,
        )
        acts = decision.action is not None
        should_act = (
            mode == "trigger"
            and not monitor_only
            and effective_action(action, status) is not None
        )
        assert acts is should_act


def test_should_close_on_offline_full_matrix() -> None:
    for (
        mode,
        monitor_only,
        wake,
        close_flag,
        tracking,
    ) in itertools.product(
        ("idle", "trigger", "watch"),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
    ):
        result = should_close_on_offline(
            mode=mode,
            monitor_only=monitor_only,
            wake_verify_active=wake,
            close_on_offline=close_flag,
            tracking_available=tracking,
        )
        expected = (
            mode == "trigger"
            and close_flag
            and tracking
            and not monitor_only
            and not wake
        )
        assert result is expected


def test_should_prune_blank_tabs_requires_trigger_and_tracking() -> None:
    assert (
        should_prune_blank_tabs(
            mode="trigger", close_off_topic=True, tracking_available=True
        )
        is True
    )
    assert (
        should_prune_blank_tabs(
            mode="watch", close_off_topic=True, tracking_available=True
        )
        is False
    )
    assert (
        should_prune_blank_tabs(
            mode="trigger", close_off_topic=False, tracking_available=True
        )
        is False
    )
    assert (
        should_prune_blank_tabs(
            mode="trigger", close_off_topic=True, tracking_available=False
        )
        is False
    )
