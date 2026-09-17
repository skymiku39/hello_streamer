"""Tests for the pure action-plan contract."""

from __future__ import annotations

import pytest

from stream_monitor.action_plan import (
    LifecycleEffect,
    TriggerSettings,
    action_plan_for,
    action_plan_for_status,
    action_plan_for_trigger,
)


@pytest.mark.parametrize(
    ("action", "open_browser", "after_open"),
    [
        ("open_and_stop", True, LifecycleEffect.STOP_MONITOR),
        ("open_and_keep", True, LifecycleEffect.NONE),
        ("notify_only", False, LifecycleEffect.NONE),
        ("open_and_exit", True, LifecycleEffect.EXIT_APP),
    ],
)
def test_action_plan_describes_each_configured_action(
    action: str,
    open_browser: bool,
    after_open: LifecycleEffect,
) -> None:
    plan = action_plan_for(action)

    assert plan is not None
    assert plan.key == action
    assert plan.notify is True
    assert plan.open_browser is open_browser
    assert plan.after_open is after_open
    assert plan.is_terminal is (after_open is not LifecycleEffect.NONE)


def test_action_plan_for_status_applies_upcoming_and_video_rules() -> None:
    assert action_plan_for_status("open_and_exit", "upcoming").key == "notify_only"
    assert action_plan_for_status("open_and_keep", "video") is None
    assert action_plan_for_status("open_and_keep", "live").key == "open_and_keep"


def test_action_plan_rejects_unknown_action() -> None:
    assert action_plan_for("not-a-real-action") is None


def test_trigger_plan_can_open_without_notification() -> None:
    plan = action_plan_for_trigger(
        TriggerSettings(notify_on_live=False, open_on_live=True), "live"
    )

    assert plan is not None
    assert plan.notify is False
    assert plan.open_browser is True
    assert plan.after_open is LifecycleEffect.STOP_MONITOR


def test_trigger_plan_can_notify_without_opening() -> None:
    plan = action_plan_for_trigger(
        TriggerSettings(notify_on_live=True, open_on_live=False), "live"
    )

    assert plan is not None
    assert plan.notify is True
    assert plan.open_browser is False
    assert plan.after_open is LifecycleEffect.NONE


def test_trigger_plan_does_not_apply_live_stop_to_upcoming() -> None:
    plan = action_plan_for_trigger(
        TriggerSettings(open_on_upcoming=True), "upcoming"
    )

    assert plan is not None
    assert plan.open_browser is True
    assert plan.after_open is LifecycleEffect.NONE


def test_trigger_plan_with_both_effects_disabled_is_suppressed() -> None:
    assert action_plan_for_trigger(
        TriggerSettings(notify_on_live=False, open_on_live=False), "live"
    ) is None
