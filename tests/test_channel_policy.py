"""Exhaustive tests for the pure per-channel decision policy."""

from __future__ import annotations

import itertools

from stream_monitor.channel_policy import (
    CHANNEL_MODE_MONITOR,
    CHANNEL_MODE_NOTIFY,
    CHANNEL_MODE_TRIGGER,
    TRIGGER_ONCE_MODE,
    WATCH_ONCE_MODE,
    LiveActionDecision,
    apply_channel_mode,
    channel_mode_for,
    effective_action,
    mode_for_silent_start,
    resolve_live_action,
    should_close_on_offline,
    should_prune_blank_tabs,
)

_ACTIONS = ("open_and_stop", "open_and_keep", "notify_only", "open_and_exit")


def test_mode_for_silent_start_recovers_one_shot_modes() -> None:
    assert mode_for_silent_start(TRIGGER_ONCE_MODE) == "trigger"
    assert mode_for_silent_start(WATCH_ONCE_MODE) == "watch"
    assert mode_for_silent_start("trigger") == "trigger"


def test_effective_action_narrows_by_stream_status() -> None:
    for action in _ACTIONS:
        assert effective_action(action, "live") == action
        assert effective_action(action, "") == action  # empty ⇒ live
        assert effective_action(action, "upcoming") == "notify_only"
        assert effective_action(action, "video") is None


def test_resolve_live_action_suppressed_outside_trigger() -> None:
    for mode in ("idle", "watch", WATCH_ONCE_MODE):
        decision = resolve_live_action(
            mode=mode,
            monitor_only=False,
            configured_action="open_and_stop",
            stream_status="live",
        )
        assert decision == LiveActionDecision(suppressed_reason="mode")


def test_resolve_live_action_suppressed_for_monitor_only() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=True,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action is None
    assert decision.suppressed_reason == "monitor_only"


def test_global_trigger_once_keeps_normal_trigger_policy() -> None:
    decision = resolve_live_action(
        mode=TRIGGER_ONCE_MODE,
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action == "open_and_stop"


def test_global_watch_once_suppresses_side_effects() -> None:
    decision = resolve_live_action(
        mode=WATCH_ONCE_MODE,
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action is None
    assert decision.suppressed_reason == "mode"


def test_notify_mode_keeps_notification_but_removes_browser_lifecycle() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        channel_mode=CHANNEL_MODE_NOTIFY,
        configured_action="open_and_stop",
        stream_status="live",
    )

    assert decision.action == "notify_only"
    assert decision.plan is not None
    assert decision.plan.notify is True
    assert decision.plan.open_browser is False
    assert decision.plan.after_open.value == "none"


def test_notify_mode_respects_notification_switch() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        channel_mode=CHANNEL_MODE_NOTIFY,
        configured_action="open_and_stop",
        stream_status="live",
        trigger_settings={"notify_on_live": False, "open_on_live": True},
    )

    assert decision.action is None
    assert decision.suppressed_reason == "disabled"


def test_channel_mode_migrates_legacy_flag_and_keeps_compatibility_view() -> None:
    channel: dict[str, object] = {"platform": "twitch", "name": "hello"}
    assert channel_mode_for(channel) == CHANNEL_MODE_TRIGGER

    apply_channel_mode(channel, CHANNEL_MODE_NOTIFY)
    assert channel_mode_for(channel) == CHANNEL_MODE_NOTIFY
    assert channel["monitor_only"] is False

    legacy = {"platform": "twitch", "name": "hello", "monitor_only": True}
    assert channel_mode_for(legacy) == CHANNEL_MODE_MONITOR


def test_resolve_live_action_video_does_nothing() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="video",
    )
    assert decision.action is None
    assert decision.suppressed_reason == "video"


def test_independent_settings_report_video_separately_from_disabled() -> None:
    video = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="video",
        trigger_settings={},
    )
    disabled = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="live",
        trigger_settings={"notify_on_live": False, "open_on_live": False},
    )
    assert video.suppressed_reason == "video"
    assert disabled.suppressed_reason == "disabled"


def test_resolve_live_action_upcoming_notifies_only() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="upcoming",
    )
    assert decision.action == "notify_only"
    assert decision.plan is not None
    assert decision.plan.open_browser is False


def test_resolve_live_action_open_and_stop_uses_plan() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_stop",
        stream_status="live",
    )
    assert decision.action == "open_and_stop"
    assert decision.plan is not None
    assert decision.plan.after_open.value == "stop_monitor"


def test_resolve_live_action_open_and_exit_uses_plan() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_exit",
        stream_status="live",
    )
    assert decision.action == "open_and_exit"
    assert decision.plan is not None
    assert decision.plan.after_open.value == "exit_app"


def test_resolve_live_action_open_and_keep_uses_plan() -> None:
    decision = resolve_live_action(
        mode="trigger",
        monitor_only=False,
        configured_action="open_and_keep",
        stream_status="live",
    )
    assert decision.action == "open_and_keep"
    assert decision.plan is not None
    assert decision.plan.open_browser is True
    assert decision.plan.after_open.value == "none"


def test_resolve_live_action_matrix_only_trigger_active_channels_act() -> None:
    """Exhaustive: side-effects only when trigger mode AND not monitor_only."""
    for mode, monitor_only, action, status in itertools.product(
        ("idle", "trigger", "watch", TRIGGER_ONCE_MODE, WATCH_ONCE_MODE),
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
            mode in ("trigger", TRIGGER_ONCE_MODE)
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
        ("idle", "trigger", "watch", TRIGGER_ONCE_MODE, WATCH_ONCE_MODE),
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
            mode in ("trigger", TRIGGER_ONCE_MODE)
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
            mode=TRIGGER_ONCE_MODE, close_off_topic=True, tracking_available=True
        )
        is True
    )
    assert (
        should_prune_blank_tabs(
            mode=WATCH_ONCE_MODE, close_off_topic=True, tracking_available=True
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
