"""Pure decision policy for per-channel side-effects.

Historically the "should we act on this channel?" decision was scattered across
``MonitorEventBridge.tick`` as a chain of ``if trigger and not monitor_only and
action == …`` predicates, mixing three orthogonal concepts:

* **mode** — the global monitor mode (``idle`` / ``trigger`` / ``watch`` and
  their one-cycle variants); trigger modes perform side-effects.
* **monitor_only** — a per-channel flag that says "observe but never act".
* **action** — the configured trigger action, further narrowed by the stream
  status (upcoming → notify only, plain video → nothing).

This module centralises those rules into small, dependency-free pure functions
so the bridge only *applies* decisions and the rules can be unit-tested in
isolation. Keeping them pure (no I/O, no Tk, no Win32) is what lets the tricky
mode × channel-mode × action matrix be verified exhaustively.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from stream_monitor.action_plan import (
    ActionPlan,
    LifecycleEffect,
    TriggerSettings,
    action_plan_for_status,
    action_plan_for_trigger,
    effective_action,
)

TRIGGER_MODE = "trigger"
WATCH_MODE = "watch"
TRIGGER_ONCE_MODE = "trigger_once"
WATCH_ONCE_MODE = "watch_once"
GLOBAL_TRIGGER_MODES = frozenset({TRIGGER_MODE, TRIGGER_ONCE_MODE})
GLOBAL_MONITOR_MODES = frozenset({WATCH_MODE, WATCH_ONCE_MODE})
GLOBAL_ACTIVE_MODES = GLOBAL_TRIGGER_MODES | GLOBAL_MONITOR_MODES


def is_one_shot_monitor_mode(mode: str) -> bool:
    return mode in {TRIGGER_ONCE_MODE, WATCH_ONCE_MODE}


def base_monitor_mode(mode: str) -> str:
    """Return the persistent/reusable mode represented by a one-cycle mode."""
    if mode == TRIGGER_ONCE_MODE:
        return TRIGGER_MODE
    if mode == WATCH_ONCE_MODE:
        return WATCH_MODE
    return mode

# Per-channel side-effect modes. These are intentionally separate from the
# global monitor mode above: a channel can be observed while either allowing
# notifications or suppressing every side-effect.
CHANNEL_MODE_TRIGGER = "trigger"
CHANNEL_MODE_MONITOR = "monitor"
CHANNEL_MODE_NOTIFY = "notify"
CHANNEL_MODES = frozenset(
    {CHANNEL_MODE_TRIGGER, CHANNEL_MODE_MONITOR, CHANNEL_MODE_NOTIFY}
)


def normalize_channel_mode(
    value: object, *, legacy_monitor_only: bool = False
) -> str:
    """Return a valid channel mode, migrating the legacy boolean flag."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in CHANNEL_MODES:
            return normalized
    return CHANNEL_MODE_MONITOR if legacy_monitor_only else CHANNEL_MODE_TRIGGER


def channel_mode_for(channel: Mapping[str, object]) -> str:
    """Read the canonical channel mode while accepting old config files."""
    return normalize_channel_mode(
        channel.get("channel_mode"),
        legacy_monitor_only=bool(channel.get("monitor_only", False)),
    )


def apply_channel_mode(channel: dict[str, object], mode: str) -> str:
    """Persist a mode and keep the old ``monitor_only`` field compatible."""
    normalized = normalize_channel_mode(mode)
    channel["channel_mode"] = normalized
    # Old integrations may still read this field. It remains true only for
    # the silent mode; the notification-capable mode is distinguished by
    # ``channel_mode`` and is handled by the canonical policy below.
    channel["monitor_only"] = normalized == CHANNEL_MODE_MONITOR
    return normalized


def next_channel_mode(mode: str) -> str:
    """Return the next mode used by the compact eye-button control."""
    current = normalize_channel_mode(mode)
    return {
        CHANNEL_MODE_TRIGGER: CHANNEL_MODE_MONITOR,
        CHANNEL_MODE_MONITOR: CHANNEL_MODE_NOTIFY,
        CHANNEL_MODE_NOTIFY: CHANNEL_MODE_TRIGGER,
    }[current]


_MONITOR_ONLY_CHANNEL_MODES = frozenset({CHANNEL_MODE_MONITOR})

@dataclass(frozen=True)
class LiveActionDecision:
    """What to do for a single went-live / upcoming event.

    ``plan`` is ``None`` when nothing should happen; ``suppressed_reason`` then
    explains why (for logging / tests). The ``action`` property is derived from
    the plan, so the action key cannot diverge from its executable effects.
    """

    plan: ActionPlan | None = None
    suppressed_reason: str = ""

    @property
    def action(self) -> str | None:
        return self.plan.key if self.plan is not None else None


def resolve_live_action(
    *,
    mode: str,
    monitor_only: bool,
    configured_action: str,
    stream_status: str,
    trigger_settings: TriggerSettings | dict[str, object] | None = None,
    channel_mode: str | None = None,
) -> LiveActionDecision:
    """Decide the side-effect for one live/upcoming channel event."""
    if mode not in GLOBAL_TRIGGER_MODES:
        return LiveActionDecision(suppressed_reason="mode")
    resolved_channel_mode = normalize_channel_mode(
        channel_mode, legacy_monitor_only=monitor_only
    )
    if resolved_channel_mode in _MONITOR_ONLY_CHANNEL_MODES:
        return LiveActionDecision(suppressed_reason="monitor_only")
    plan = (
        action_plan_for_trigger(trigger_settings, stream_status)
        if trigger_settings is not None
        else action_plan_for_status(configured_action, stream_status)
    )
    if plan is None:
        action = (
            effective_action(configured_action, stream_status)
            if trigger_settings is None
            else None
        )
        if trigger_settings is not None:
            reason = "video" if (stream_status or "live") == "video" else "disabled"
        else:
            reason = "video" if action is None else "invalid_action"
        return LiveActionDecision(suppressed_reason=reason)
    if resolved_channel_mode == CHANNEL_MODE_NOTIFY:
        # A notification-capable monitor must never inherit the configured
        # browser/lifecycle effect. Keep the user's notification switches
        # and status-specific rules, then narrow the resulting plan.
        if not plan.notify:
            return LiveActionDecision(suppressed_reason="disabled")
        plan = replace(
            plan,
            key="notify_only",
            open_browser=False,
            after_open=LifecycleEffect.NONE,
        )
    return LiveActionDecision(plan=plan)


def should_close_on_offline(
    *,
    mode: str,
    monitor_only: bool,
    wake_verify_active: bool,
    close_on_offline: bool,
    tracking_available: bool,
    channel_mode: str | None = None,
) -> bool:
    """Whether a went-offline event should auto-close the tracked window.

    Suppressed during wake verification (post-sleep) because a stale cache can
    momentarily look offline before the confirming poll, and for monitor-only
    channels (observe but never act).
    """
    resolved_channel_mode = normalize_channel_mode(
        channel_mode, legacy_monitor_only=monitor_only
    )
    return (
        mode in GLOBAL_TRIGGER_MODES
        and close_on_offline
        and tracking_available
        and resolved_channel_mode == CHANNEL_MODE_TRIGGER
        and not wake_verify_active
    )


def should_prune_blank_tabs(
    *,
    mode: str,
    close_off_topic: bool,
    tracking_available: bool,
) -> bool:
    """Whether the post-poll blank-tab prune sweep should run."""
    return mode in GLOBAL_TRIGGER_MODES and close_off_topic and tracking_available
